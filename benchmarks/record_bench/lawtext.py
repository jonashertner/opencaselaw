"""Parse get_law markdown into fields that can be copied into a record.

Used by build.py (to copy) and by fixture.py --check (to confirm that what
was copied is in the snapshot). Only reads; never composes citation text.
"""
from __future__ import annotations

import re

_HEAD = re.compile(r"^# (?P<law>.+?)(?: — SR (?P<sr>[\d.]+))?$", re.M)
_ART = re.compile(r"^### (?P<label>(?:Art\.|§) (?P<num>[0-9]+[a-z]*(?:bis|ter|quater|quinquies|sexies)?)(?: — .*)?)$", re.M)
_PARA = re.compile(r"^(?P<num>\d+(?:bis|ter|quater|quinquies|sexies|[a-z])?) (?P<text>\S.*)$")


def _field(md: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}: (.+)$", md, re.M)
    return m.group(1).strip() if m else None


def parse(md: str) -> dict:
    head = _HEAD.search(md)
    title = re.search(r"^\*\*(.+)\*\*$", md, re.M)
    quelle = re.search(r"^Quelle: \[[^\]]*\]\((\S+)\)", md, re.M)
    version = _field(md, "Version")
    text_source = _field(md, "Text source") or ""
    out = {
        "law": head.group("law").strip() if head else None,
        "sr_number": head.group("sr") if head else None,
        "act_title": title.group(1) if title else None,
        "jurisdiction": _field(md, "Jurisdiction"),
        "consolidation_date": _field(md, "Consolidation date"),
        "version": version,
        "edition": None,
        "act_in_force": _field(md, "Act in force"),
        "repealed": "(repealed)" in (_field(md, "Act in force") or ""),
        "pending": _field(md, "Pending changes"),
        "source_url": quelle.group(1) if quelle else None,
        "structured": True,
        "article_label": None,
        "article": None,
        "body": None,
        "paragraphs": {},
        "footnotes": re.findall(r"^> Footnote: (.+)$", md, re.M),
    }
    if version:
        m = re.search(r"edition of (\d{4}-\d{2}-\d{2})", version)
        out["edition"] = m.group(1) if m else None
    else:
        out["edition"] = out["consolidation_date"]
    if "PDF" in text_source or "### Excerpt around" in md:
        out["structured"] = False
    if out["jurisdiction"] and out["jurisdiction"].startswith("Canton"):
        out["structured"] = False  # LexFind text interleaves marginal notes and page headers
    art = _ART.search(md)
    if art:
        out["article_label"] = art.group("label")
        out["article"] = art.group("num")
        rest = md[art.end():]
        stop = min([i for i in (rest.find("\n> Footnote"), rest.find("\n---")) if i >= 0] or [len(rest)])
        body = rest[:stop].strip()
        out["body"] = body
        if out["structured"]:
            for line in body.splitlines():
                m = _PARA.match(line)
                if m:
                    out["paragraphs"][m.group("num")] = line
    elif not out["structured"]:
        m = re.search(r'^### Excerpt around "(?P<label>(?:Art\.|§) (?P<num>[^"]+))".*$', md, re.M)
        if m:
            out["article_label"] = m.group("label")
            out["article"] = m.group("num")
            rest = md[m.end():]
            out["body"] = rest[: rest.find("\n---") if "\n---" in rest else len(rest)].strip()
    return out


def paragraph_in_excerpt(body: str, paragraph: str) -> bool:
    """For unstructured editions: does a line start with the paragraph number?"""
    return any(re.match(rf"^{re.escape(paragraph)} \S", ln.strip()) for ln in (body or "").splitlines())
