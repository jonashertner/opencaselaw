"""Derive authoritative decision fields from the decision's OWN text — the source
of truth — and emit the standardized case identifier (ECLI) from them.

Root-cause principle (2026-06-28 audit): the corpus asserts scraped metadata as
fact (volume-year dates, empty date fields, source labels) even when the
authoritative answer is in the document text. This module re-derives the real
values from text, reports provenance, and builds the canonical identifier — so a
decision is referenced by what its document actually says, not by a scrape fallback.

Coupling with standardized referencing (ECLI:CH:<court>:<year>:<ordinal>):
  1. ECLI needs the REAL year — a synthetic 2026-01-01 yields a wrong ECLI. So
     this extraction is the prerequisite that makes the identifier correct.
  2. The docket number embeds the filing year (9C_113/2025 -> 2025). A ruling
     cannot predate its docket, so the docket year VALIDATES the extracted date
     (this rejects a cited case's date grabbed from the head).
  3. The ECLI is one identifier per ruling, so the BGE excerpt and its docket
     map to the SAME ECLI — the identifier IS the dedup/join key for the
     logical-decision layer.

Pure functions (text in, values out) — no DB access — trivially testable.
The canonical Urteilskopf/rubrum states both together:
    "... 9C_113/2025 vom 27. September 2025 Regeste ..."
"""
from __future__ import annotations

import re
from datetime import date as _date

_MONTHS: dict[str, int] = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "dicembre": 12,
}
_MONTH_ALT = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))

_DOCKET_RE = r"\d[A-Za-z]{1,3}[_.]\d{1,5}/\d{4}"
# Federal Insurance Court (EVG) dockets until 2006: "P 71/01", "I 189/02",
# "U 49/98". Only taken where the ruling's date follows directly, never as a
# bare match: "A 12/34" is too common a shape in running text.
_EVG_DOCKET_RE = r"[A-Z]{1,2}\s\d{1,4}/\d{2}"
# DE 'vom' / FR 'du', 'des' (two session dates) / IT 'del', "dell'11" (no
# space after the apostrophe).
_MARK = r"\s+(?:vom|du|des|del(?:l['’])?)\s*"
# "1er septembre", "1° settembre"; a second session date ("des 14 juillet et
# 1er septembre 2005", BGE 131 III 601) is skipped: the first is the ruling's.
_DAY = r"(\d{1,2})(?:er|°)?\.?"
_SECOND = r"(?:\s+(?:et|und|e)\s+\d{1,2}(?:er|°)?\.?\s+(?:" + _MONTH_ALT + r"))?"

# A consolidated proceeding lists its dockets before the date ("2C_36/2023 /
# 2C_38/2023 du 5 juin 2024", BGE 151 I 3): group 1 is the FIRST, the lead
# under which the judgment is filed.
_ANY_DK = r"(?:" + _DOCKET_RE + r"|" + _EVG_DOCKET_RE + r")"
_DK_CHAIN = r"(" + _ANY_DK + r")(?:\s*/\s*" + _ANY_DK + r")*"

# Precise: the decision's date immediately after its docket ('<docket> vom <date>').
_DK_DATE_TEXT = re.compile(
    _DK_CHAIN + _MARK + _DAY + r"\s*(" + _MONTH_ALT + r")" + _SECOND + r"\s+(\d{4})\b", re.IGNORECASE)
_DK_DATE_NUM = re.compile(
    _DK_CHAIN + _MARK + r"(\d{1,2})\.(\d{1,2})\.(\d{4})\b", re.IGNORECASE)
# Fallback: any plausible date / docket in the head.
_TEXT_DATE = re.compile(r"\b(\d{1,2})\.?\s+(" + _MONTH_ALT + r")\s+(\d{4})\b", re.IGNORECASE)
_NUM_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DOCKET = re.compile(r"\b(" + _DOCKET_RE + r")\b")

_MIN_YEAR = 1875
# ECLI court tokens for the Swiss federal tier (BGE excerpt shares BGER with its docket).
_ECLI_COURT = {"bge": "BGER", "bger": "BGER", "bvger": "BVGER",
               "bstger": "BSTGER", "bpatger": "BPATGER", "mkg": "MKG"}


def normalize_docket(d: str | None) -> str | None:
    """Canonical docket: '2P.139/2004'/'2C 838/2018' -> '2P_139/2004'/'2C_838/2018'."""
    if not d:
        return d
    d = d.strip()
    m = re.match(r"^(\d[A-Za-z]{1,3})[\s._]+(\d{1,5}/\d{4})$", d)
    return (m.group(1) + "_" + m.group(2)) if m else d.replace(" ", "")


def docket_year(docket: str | None) -> int | None:
    """Filing year embedded in the docket ('9C_113/2025' -> 2025)."""
    m = re.search(r"/(\d{4})\b", docket or "")
    return int(m.group(1)) if m else None


def _valid(y: int, m: int, dy: int, max_year: int | None) -> str | None:
    if y < _MIN_YEAR or (max_year is not None and y > max_year):
        return None
    try:
        return _date(y, m, dy).isoformat()
    except ValueError:
        return None


def extract_urteilskopf(text: str | None, head: int = 2500,
                        max_year: int | None = None) -> dict:
    """Docket + decision date from the header, the date VALIDATED by the docket
    year (a ruling cannot predate its own docket). Returns {docket?, date?, date_raw?}.
    """
    out: dict = {}
    if not text:
        return out
    h = text[:head]
    iso = raw = None
    m = _DK_DATE_TEXT.search(h)
    if m:
        out["docket"] = normalize_docket(m.group(1))
        iso = _valid(int(m.group(4)), _MONTHS[m.group(3).lower()], int(m.group(2)), max_year)
        raw = m.group(0)
    else:
        m = _DK_DATE_NUM.search(h)
        if m:
            out["docket"] = normalize_docket(m.group(1))
            iso = _valid(int(m.group(4)), int(m.group(3)), int(m.group(2)), max_year)
            raw = m.group(0)
    # Validate: decision year must be >= docket (filing) year.
    if iso:
        dy = docket_year(out.get("docket"))
        if dy and int(iso[:4]) < dy:
            iso = raw = None
    if iso:
        out["date"] = iso
        out["date_raw"] = raw
    if "docket" not in out:  # no docket-adjacent date; still capture a docket
        dk = _DOCKET.search(h)
        if dk:
            out["docket"] = normalize_docket(dk.group(1))
    return out


def extract_text_date(text: str | None, head: int = 2500,
                      max_year: int | None = None) -> tuple[str | None, str | None]:
    """Best decision date as (iso, raw): the docket-validated Urteilskopf date if
    present, else the first plausible bare date in the head."""
    uk = extract_urteilskopf(text, head=head, max_year=max_year)
    if uk.get("date"):
        return uk["date"], uk.get("date_raw")
    if not text:
        return None, None
    h = text[:head]
    for mo in _TEXT_DATE.finditer(h):
        iso = _valid(int(mo.group(3)), _MONTHS[mo.group(2).lower()], int(mo.group(1)), max_year)
        if iso:
            return iso, mo.group(0)
    for mo in _NUM_DATE.finditer(h):
        iso = _valid(int(mo.group(3)), int(mo.group(2)), int(mo.group(1)), max_year)
        if iso:
            return iso, mo.group(0)
    return None, None


def extract_docket(text: str | None, head: int = 2500) -> str | None:
    if not text:
        return None
    m = _DOCKET.search(text[:head])
    return normalize_docket(m.group(1)) if m else None


def is_synthetic_date(stored: str | None) -> bool:
    """YYYY-01-01 (volume placeholder) or empty — courts do not rule on Jan 1."""
    return not stored or stored.endswith("-01-01")


def derive_date(stored_date: str | None, text: str | None,
                max_year: int | None = None, max_date: str | None = None
                ) -> tuple[str | None, str | None]:
    """(best_date, provenance): a real stored date is trusted; a synthetic/empty
    one is overridden by the docket-validated text date; else kept-but-flagged.

    A text date is accepted only if it is itself non-synthetic (a YYYY-01-01
    extraction is an artifact — no court rules on Jan 1) and not after max_date
    (today), so future-dated artifacts can't replace a synthetic date.

    provenance in {source_metadata, extracted_from_text, volume_synthetic, null}.
    """
    if stored_date and not is_synthetic_date(stored_date):
        return stored_date, "source_metadata"
    iso, _ = extract_text_date(text, max_year=max_year)
    if iso and not is_synthetic_date(iso) and (max_date is None or iso <= max_date):
        return iso, "extracted_from_text"
    if stored_date:
        return stored_date, "volume_synthetic"
    return None, "null"


def derive_dates(stored_decision: str | None, stored_pub: str | None,
                 text: str | None, max_year: int | None = None,
                 max_date: str | None = None
                 ) -> tuple[str | None, str, str | None, str]:
    """Demux a conflated date into DISTINCT (decision_date, dprov, publication_date, pprov).

    decision_date and publication_date are different things. A BGE's synthetic
    'YYYY-01-01' is the Amtliche-Sammlung VOLUME (publication) year mis-filed into
    decision_date (verified: YYYY == 1874+volume for 97% of them). So:
      - decision_date  <- the real Urteilsdatum recovered from the text, else None
                          (never leave the volume year masquerading as a decision date)
      - publication_date <- the volume year (year-precision, provenance 'volume_year')
                          when no real publication date is stored
    A real stored decision date is trusted; a real stored publication date is kept.

    pprov in {source_metadata, volume_year, null}; dprov as in derive_date().
    """
    dd, dprov = derive_date(stored_decision, text, max_year=max_year, max_date=max_date)
    if stored_pub:
        pd, pprov = stored_pub, "source_metadata"
    elif is_synthetic_date(stored_decision) and stored_decision:
        # the synthetic value is the volume/publication year, not a decision date
        pd, pprov = stored_decision, "volume_year"
        if dprov == "volume_synthetic":        # decision date not recoverable
            dd, dprov = None, "null"            # -> unknown, not the volume year
    else:
        pd, pprov = None, "null"
    return dd, dprov, pd, pprov


def build_ecli(court: str | None, decision_date: str | None,
               docket: str | None) -> str | None:
    """ECLI:CH:<court>:<year>:<ordinal> from the VERIFIED year + normalized docket.

    The year comes from the real decision date, so a synthetic date can't produce a
    wrong-year ECLI. The BGE excerpt and its docket yield the SAME ECLI (same court,
    year, docket) — the identifier is the logical-decision join key.
    """
    yr = (decision_date or "")[:4]
    token = _ECLI_COURT.get((court or "").lower())
    nd = normalize_docket(docket)
    if not (yr.isdigit() and token and nd):
        return None
    ordinal = nd.replace("/", ".")          # 9C_113/2025 -> 9C_113.2025
    return f"ECLI:CH:{token}:{yr}:{ordinal}"
