"""Appeal information a court publishes with a decision ("Weiterzug ans
Bundesgericht, 6B_122/2024"; "Berufung am Obergericht anhängig").

Scrapers store it in the shard row's ``appeal_info``. decisions.db has no
column for it, so it travels in ``json_data``; this module reads it from there
and resolves the dockets it names to decisions in the corpus. The text is the
court's own wording and is passed on verbatim; nothing is derived from it but
the links.
"""
from __future__ import annotations

import json
import re
import sqlite3

# Federal Supreme Court docket ("6B_122/2024", "4A_480/2013").
_BGER_DOCKET_RE = re.compile(r"\b(\d[A-Z]_\d{1,4}/(?:19|20)\d{2})\b")
# Zürich courts ("LA240029", "LA240029-O"): two letters, year, serial.
_ZH_DOCKET_RE = re.compile(r"\b([A-Z]{2}\d{6})(?:-[A-Z]\d?)?\b")
_BGER_COURTS = ("bger", "bge")
MAX_REFS = 12


def appeal_info_of(row) -> str | None:
    """The stored appeal information of a decisions row (dict or sqlite3.Row)."""
    try:
        blob = row["json_data"]
    except (KeyError, IndexError, TypeError):
        return None
    if not blob:
        return None
    try:
        text = (json.loads(blob).get("appeal_info") or "").strip()
    except (ValueError, AttributeError):
        return None
    return text or None


def dockets_in(text: str | None) -> list[tuple[str, str]]:
    """[(docket as written, kind)] in order of appearance, kind 'bger' | 'zh'."""
    found: list[tuple[int, str, str]] = []
    for m in _BGER_DOCKET_RE.finditer(text or ""):
        found.append((m.start(), m.group(1), "bger"))
    for m in _ZH_DOCKET_RE.finditer(text or ""):
        found.append((m.start(), m.group(1), "zh"))
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for _, docket, kind in sorted(found):
        if docket not in seen:
            seen.add(docket)
            out.append((docket, kind))
    return out[:MAX_REFS]


def resolve(conn: sqlite3.Connection, text: str | None, court: str | None,
            own_id: str | None = None) -> list[dict]:
    """Dockets named in the appeal information that are decisions in the
    corpus: [{"docket", "decision_id"}]. A Zürich docket is only looked up
    for a Zürich decision; a docket held more than once in the target court
    (several rulings of one case) is left unlinked rather than guessed."""
    refs: list[dict] = []
    for docket, kind in dockets_in(text):
        if kind == "zh" and not (court or "").startswith("zh_"):
            continue
        try:
            rows = conn.execute(
                "SELECT decision_id, court FROM decisions WHERE docket_number = ? LIMIT 6",
                (docket,),
            ).fetchall()
        except sqlite3.Error:
            return refs
        if kind == "bger":
            hits = [r[0] for r in rows if r[1] in _BGER_COURTS]
        else:
            hits = [r[0] for r in rows if (r[1] or "").startswith("zh_")]
        hits = [h for h in hits if h != own_id]
        if len(hits) == 1:
            refs.append({"docket": docket, "decision_id": hits[0]})
    return refs
