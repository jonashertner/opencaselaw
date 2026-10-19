"""Host-local decision suppression for privacy takedowns.

Removing a decision from the corpus takes a full rebuild (a day or more).
For a privacy takedown that is too slow, so the serving layer can hide a
decision at once: every worker reads a host-local list of decision ids and

  - refuses tool calls and REST requests whose arguments name a suppressed
    decision (by id or by docket),
  - drops suppressed decisions from structured (JSON) results,
  - drops Markdown result blocks headed by a suppressed decision,
  - unlinks /entscheid/ links to suppressed decisions inside other texts
    (the cited docket stays: it is the court's own text),
  - answers 410 for their decision pages.

The list is operational data and is never committed: the requests behind it
are confidential (docs/governance-and-removal-policy.md). One decision id per
line, '#' starts a comment, e.g. ``bvger_A-1234_2025``. A docket joined into
another decision can be listed in the same id form to block lookups by it.

Path: $OCL_SUPPRESSED_DECISIONS_FILE, default
/opt/caselaw/suppressed_decisions.txt. A missing or empty file suppresses
nothing; the only cost is a stat every RELOAD_S seconds.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/opt/caselaw/suppressed_decisions.txt"
RELOAD_S = 15.0

NOT_AVAILABLE = {
    "error": "not_available",
    "message": "This decision is not available on OpenCaseLaw.",
}
NOT_AVAILABLE_JSON = json.dumps(NOT_AVAILABLE, ensure_ascii=False)

# JSON keys that carry a decision id, and keys that carry a decision URL.
_ID_KEYS = ("decision_id", "canonical_decision_id", "id", "source_decision_id",
            "target_decision_id", "citing_decision_id", "cited_decision_id")
_URL_KEYS = ("url", "canonical_url", "link", "display_url")

_HEADING = re.compile(r"^(#{1,6}\s|-{3,}\s*$)")
# A result-list item as the tools print it: "**3.** [D-1/2020](…) …",
# "- [D-1/2020](…) conf=0.9", "3. [D-1/2020](…)".
_ITEM = re.compile(r"^\s*(?:\*\*\d+\.\*\*|\d+\.|[-*•])\s+")


def _docket_pattern(decision_id: str) -> str | None:
    """'bvger_D-5522_2023' -> a pattern for 'D-5522/2023', 'D-5522_2023', ...

    The court prefix is everything up to the first '_'; the year is the part
    after the last '_'. Dockets are written with '/' or '_' before the year.
    """
    _, sep, rest = decision_id.partition("_")
    if not sep:
        return None
    number, sep, year = rest.rpartition("_")
    if not sep or not number or not year.isdigit():
        return None
    return (r"(?<![A-Za-z0-9])" + re.escape(number).replace("_", "[_ ]")
            + r"\s*[/_]\s*" + re.escape(year) + r"(?![0-9])")


@dataclass(frozen=True)
class Suppression:
    ids: frozenset = frozenset()
    _ref: re.Pattern | None = field(default=None, compare=False)    # id or docket
    _id_re: re.Pattern | None = field(default=None, compare=False)  # id only
    _link: re.Pattern | None = field(default=None, compare=False)   # Markdown link
    _lead: re.Pattern | None = field(default=None, compare=False)   # link opening an item

    @classmethod
    def from_ids(cls, ids) -> "Suppression":
        ids = frozenset(i for i in ids if i)
        if not ids:
            return cls()
        alt = "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
        id_pat = r"(?<![A-Za-z0-9_])(?:" + alt + r")(?![A-Za-z0-9_])"
        dockets = [p for p in (_docket_pattern(i) for i in ids) if p]
        ref_pat = "|".join([id_pat, *dockets])
        link_pat = (r"\[([^\]\n]*)\]\(\s*[^)\s]*?/entscheid/(?:" + alt
                    + r")(?![A-Za-z0-9_])[^)\s]*\s*\)")
        return cls(ids, re.compile(ref_pat, re.IGNORECASE), re.compile(id_pat),
                   re.compile(link_pat), re.compile(r"^" + link_pat))

    # ── matching ─────────────────────────────────────────────
    def mentions(self, text: str) -> bool:
        """True when text names a suppressed decision by id or by docket."""
        return bool(self.ids) and bool(self._ref.search(text or ""))

    def args_hit(self, arguments) -> bool:
        if not self.ids or not arguments:
            return False
        try:
            blob = json.dumps(arguments, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = str(arguments)
        return self.mentions(blob)

    def _record_hit(self, d: dict) -> bool:
        for k in _ID_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v in self.ids:
                return True
        for k in _URL_KEYS:
            v = d.get(k)
            if isinstance(v, str) and "/entscheid/" in v and self._id_re.search(v):
                return True
        return False

    def _item_hit(self, d: dict) -> bool:
        """A list item is a suppressed result when it, or a record nested one
        level down ({"decision": {...}, "snippet": ...}), names one."""
        return self._record_hit(d) or any(
            isinstance(v, dict) and self._record_hit(v) for v in d.values())

    # ── filtering ────────────────────────────────────────────
    def filter_obj(self, obj):
        """Drop suppressed records from lists, null them in dicts, unlink strings."""
        if not self.ids:
            return obj
        if isinstance(obj, list):
            return [self.filter_obj(x) for x in obj
                    if not (isinstance(x, dict) and self._item_hit(x))]
        if isinstance(obj, tuple):
            return tuple(self.filter_obj(list(obj)))
        if isinstance(obj, dict):
            if self._record_hit(obj):
                return dict(NOT_AVAILABLE)
            return {k: (None if isinstance(v, dict) and self._record_hit(v)
                        else self.filter_obj(v))
                    for k, v in obj.items()}
        if isinstance(obj, str):
            return self.unlink(obj)
        return obj

    def unlink(self, text: str) -> str:
        if not self.ids or "/entscheid/" not in text:
            return text
        return self._link.sub(lambda m: m.group(1), text)

    def scrub_text(self, text: str) -> str:
        """Filter one tool/REST text: JSON is filtered structurally, Markdown
        loses the blocks headed by a suppressed decision and its links."""
        if not self.ids or not text or not self._id_re.search(text):
            return text
        stripped = text.lstrip()
        if stripped[:1] in "{[":
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                if "\n" in text.strip():
                    return json.dumps(self.filter_obj(parsed), ensure_ascii=False, indent=2)
                return json.dumps(self.filter_obj(parsed), ensure_ascii=False,
                                  separators=(",", ":"))
        return self.unlink(self._drop_blocks(text))

    def _drop_blocks(self, text: str) -> str:
        """Drop a '## [link]' section up to the next heading or rule, and a
        list item opened by a link to a suppressed decision up to the next
        item or blank line. A link inside running text only loses its link."""
        out, mode = [], None          # None | "heading" | "item"
        for line in text.split("\n"):
            item = _ITEM.match(line)
            if _HEADING.match(line):
                mode = "heading" if (line.startswith("#") and self._id_re.search(line)) else None
            elif item and mode != "heading":
                mode = "item" if self._lead.match(line[item.end():]) else None
            elif mode == "item" and not line.strip():
                mode = None
                continue              # the item's blank separator goes with it
            if mode is None:
                out.append(line)
        return "\n".join(out)


_EMPTY = Suppression()
_lock = threading.Lock()
_state = {"checked": 0.0, "mtime": None, "path": None, "value": _EMPTY}


def _path() -> str:
    return os.environ.get("OCL_SUPPRESSED_DECISIONS_FILE", DEFAULT_PATH)


def load(path: str) -> Suppression:
    ids = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    return Suppression.from_ids(ids)


def current() -> Suppression:
    """The active list, re-read when the file changes (checked every RELOAD_S)."""
    now = time.monotonic()
    path = _path()
    if now - _state["checked"] < RELOAD_S and path == _state["path"]:
        return _state["value"]
    with _lock:
        _state["checked"] = now
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime == _state["mtime"] and path == _state["path"]:
            return _state["value"]
        value = _EMPTY
        if mtime is not None:
            try:
                value = load(path)
            except OSError as exc:
                logger.error("suppression list unreadable (%s); keeping previous list",
                             type(exc).__name__)
                value = _state["value"]
        if len(value.ids) != len(_state["value"].ids):
            logger.info("suppression list: %d decision(s)", len(value.ids))
        _state.update(mtime=mtime, path=path, value=value)
        return value


def _reset_for_tests() -> None:
    _state.update(checked=0.0, mtime=None, path=None, value=_EMPTY)


def filter_tool_result(s: Suppression, result, text_content_cls):
    """Apply scrub_text to a tool result: a list of contents, or the
    (contents, structuredContent) tuple some tools return."""
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        return (filter_tool_result(s, content, text_content_cls),
                s.filter_obj(structured))
    if isinstance(result, list):
        out = []
        for c in result:
            if getattr(c, "type", None) == "text" and isinstance(getattr(c, "text", None), str):
                new = s.scrub_text(c.text)
                out.append(c if new is c.text else text_content_cls(type="text", text=new))
            else:
                out.append(c)
        return out
    return result


def wrap_tool_handler(inner, payload_var, text_content_cls):
    """Wrap the MCP tool dispatcher: refuse calls naming a suppressed decision,
    filter everything else (including the parked JSON payload)."""
    @functools.wraps(inner)
    async def wrapped(name, arguments):
        s = current()
        if not s.ids:
            return await inner(name, arguments)
        if s.args_hit(arguments):
            logger.info("suppression: refused %s", name)
            return [text_content_cls(type="text", text=NOT_AVAILABLE_JSON)]
        result = await inner(name, arguments)
        if payload_var is not None:
            payload = payload_var.get()
            if payload is not None:
                payload_var.set(s.filter_obj(payload))
        return filter_tool_result(s, result, text_content_cls)
    return wrapped
