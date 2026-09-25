"""Schaffhausen Obergericht citations: "OGE 60/2017/43 vom 10. Januar 2020".

One docket can carry several rulings (an interim and a final decision, a
revision), and the corpus holds each Schaffhausen ruling twice (sh_gerichte
"Nr. 60/2017/43" and sh_obergericht "60/2017/43") with dates that do not
always agree. The docket alone therefore does not identify the ruling a
citation means: when the citation writes a date, only a ruling of that date
is the one cited. Shared by the scholarship citation extractor and the MCP
server's cite() so both read a citation the same way.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

# Abteilung/Jahr/Nummer, optional letter suffix (60/2008/20A).
OGE_RE = re.compile(r"\bOGE\s+(\d{1,3}/\d{4}/\d{1,4}[A-Z]?)(?![\d/])")
# A stored docket: "Nr. 60/2017/43" (sh_gerichte) or "60/2017/43".
SH_DOCKET_RE = re.compile(r"(?:Nr\.\s*)?(\d{1,3}/\d{4}/\d{1,4}[A-Z]?)")

_MONTHS = {
    # de
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # it
    "gennaio": 1, "febbraio": 2, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
}
# What may stand between the docket and its date.
_LEAD = re.compile(r"^\s*,?\s*(?:vom|du|del|dal)\s+", re.IGNORECASE)
_NUMERIC = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b")
_WORDED = re.compile(r"(\d{1,2})(?:\.|er|°)?\s+([^\W\d_]+)\s+(\d{4})\b")

# Returned by cited_date() when a date is announced ("vom …") but cannot be read.
UNREADABLE = "?"


def cited_date(after: str) -> str | None:
    """ISO date written right after a docket, None when none is written,
    UNREADABLE when "vom"/"du" announces one this parser cannot read."""
    lead = _LEAD.match(after or "")
    if not lead:
        return None
    rest = after[lead.end():lead.end() + 40]
    m = _NUMERIC.match(rest)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _WORDED.match(rest)
        mo = _MONTHS.get(m.group(2).lower()) if m else None
        if not m or not mo:
            return UNREADABLE
        d, y = int(m.group(1)), int(m.group(3))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return UNREADABLE
    return f"{y:04d}-{mo:02d}-{d:02d}"


def iter_oge(text: str) -> Iterator[tuple[str, str, str | None]]:
    """(raw match, docket, cited date or None or UNREADABLE) per citation."""
    for m in OGE_RE.finditer(text or ""):
        yield m.group(0), m.group(1), cited_date(text[m.end():m.end() + 60])


def pick(candidates: Iterable[tuple[str, str | None, str]],
         date: str | None) -> str | None:
    """The decision_id a citation means, from (decision_id, decision_date,
    court) rows carrying its docket; None when it cannot be told.

    A written date must equal the stored date (either twin may carry it);
    no written date takes the sh_gerichte row first, as before. An
    unreadable date links nothing."""
    rows = sorted(candidates, key=lambda r: (r[2] != "sh_gerichte", r[1] or "", r[0]))
    if date == UNREADABLE:
        return None
    if date:
        rows = [r for r in rows if (r[1] or "")[:10] == date]
    return rows[0][0] if rows else None
