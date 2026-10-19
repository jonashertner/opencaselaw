"""
ECLI (European Case Law Identifier) minting for Swiss court decisions.

The ECLI specification (Council of the EU, 2011/C 127/01) defines a
country-prefixed identifier:

    ECLI:<CC>:<COURT>:<YEAR>:<ID>

For Swiss decisions, this module produces ECLIs as the European
projection of the Swiss-native cli:ch identifier (see cli_ch.py).
The projection makes Swiss caselaw addressable from European legal
data systems and cross-border resolvers via a single uniform
identifier format.

For BGE leading decisions we use:

    ECLI:CH:BGE:<YEAR>:<vol>.<part>.<page>

which is the form the ECLI spec generates from the BGE citation
`BGE 140 III 86` (vol/part/page).

This is a convention proposed by OpenCaseLaw as part of the
open-law-standards proposal at docs/standards/. The same approach
extends to all Swiss decisions in the corpus using a small set of
deterministic rules:

  - BGE leading decisions:   ECLI:CH:BGE:<year>:<vol>.<part>.<page>
  - Federal Supreme Court:   ECLI:CH:BGER:<year>:<docket-normalized>
  - Federal Admin Court:     ECLI:CH:BVGER:<year>:<docket-normalized>
  - Federal Criminal Court:  ECLI:CH:BSTGER:<year>:<docket-normalized>
  - Federal Patent Court:    ECLI:CH:BPATGER:<year>:<docket-normalized>
  - Cantonal:                ECLI:CH:<COURT>:<year>:<docket-normalized>
    where COURT is the upper-case cantonal court code with the canton
    prefix kept (e.g. ZHOG for ZH Obergericht).

Docket normalisation replaces '/' with '_' and ' ' with '.' so the
result satisfies the ECLI ID character set (alphanumeric, '.', '_').

This is a pure function over the existing schema — no DB migration is
required. The MCP server, REST API, and seo_pages.py per-decision pages
can each call `mint_ecli(...)` to derive the identifier on demand.

Reference: https://e-justice.europa.eu/175/EN/european_case_law_identifier_ecli
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional


# Map our internal court codes to the ECLI court component.
# Federal courts get short canonical names; cantonal courts use
# CANTON+COURT-MNEMONIC concatenation (e.g. ZHOG = ZH Obergericht).
_COURT_TO_ECLI: dict[str, str] = {
    # Federal — top
    "bger": "BGER",
    "bge": "BGE",
    "bge_egmr": "BGE",        # BGE-format EGMR cross-refs share BGE namespace
    "bge_historical": "BGE",
    "bvger": "BVGER",
    "bstger": "BSTGER",
    "bpatger": "BPATGER",
    "ch_bundesrat": "BR",     # Bundesrat (administrative)
    # Federal — regulators
    "finma": "FINMA",
    "finma_versicherungsrecht": "FINMAV",
    "weko": "WEKO",
    "edoeb": "EDOEB",
    "ubi": "UBI",
    "elcom": "ELCOM",
    "postcom": "POSTCOM",
    "comcom": "COMCOM",
    # Federal — military
    "mkg": "MKG",
    # Federal — customs/tax admin
    "bazg": "BAZG",
    # Federal — international
    "ecthr": "ECHR",          # European Court of Human Rights
    "hudoc_ch": "ECHR",
    "emark": "EMARK",         # Eidg. Migrationskommission (legacy)
    "ta_sst": "TASST",        # Schweizer Sportgericht (Swiss Sports Tribunal, sportstribunal.ch)
    # Canton-prefixed cantonal codes are derived dynamically from the
    # court field below — no exhaustive list needed.
}

# ID character set per ECLI spec: a-z A-Z 0-9 . _ (no '/', no spaces).
_ECLI_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]")

# BGE docket pattern: "BGE 140 III 86" → vol=140, div=III, page=86
_BGE_DOCKET = re.compile(r"^BGE\s+(\d+)\s+([IVX]+)\s+(\d+)", re.IGNORECASE)


def _normalize_docket(docket: str) -> str:
    """Make a docket safe for the ECLI ID position.

    Examples:
      6B_1234/2025  → 6B_1234.2025
      A-1234/2024   → A-1234.2024
      O2024_001     → O2024_001
      BGE 140 III 86 → 140.III.86  (handled separately for BGE)
    """
    s = docket.strip()
    # Replace / with .
    s = s.replace("/", ".")
    # Replace whitespace runs with .
    s = re.sub(r"\s+", ".", s)
    # Strip anything outside the safe set
    s = _ECLI_ID_SAFE.sub("", s)
    # Collapse repeated dots
    s = re.sub(r"\.{2,}", ".", s)
    return s.strip(".") or "unknown"


def _derive_year(decision_date, docket_number) -> Optional[int]:
    """Pull a 4-digit year from decision_date or from a trailing /YYYY
    in the docket. Returns None if neither source yields one."""
    if decision_date:
        if isinstance(decision_date, (date, datetime)):
            return decision_date.year
        if isinstance(decision_date, str):
            m = re.match(r"^(\d{4})", decision_date)
            if m:
                return int(m.group(1))
    if docket_number:
        m = re.search(r"/(\d{4})\b", docket_number)
        if m:
            return int(m.group(1))
    return None


def _cantonal_ecli_court(court_code: str) -> str:
    """Derive the ECLI court component for a cantonal court code.

    Convention: take the canton prefix, capitalise it, and append a short
    chamber/instance mnemonic derived from the second segment.

    Examples:
      zh_obergericht       → ZHOG
      zh_verwaltungsgericht → ZHVG
      be_zivilstraf        → BEZS
      vd_gerichte          → VDG
    """
    parts = court_code.split("_", 1)
    if len(parts) == 1:
        return court_code.upper()
    canton, rest = parts[0].upper(), parts[1]
    # Acronymise rest: take the first letter of each underscore-separated
    # word, plus the first letter of CamelCase boundaries. Short mnemonics
    # keep the ECLI compact and readable.
    rest_acronym = "".join(w[0].upper() for w in rest.split("_") if w)
    if not rest_acronym:
        rest_acronym = "G"  # default: "Gerichte" → G
    return canton + rest_acronym


def mint_ecli(
    decision_id: str,
    court: str,
    docket_number: Optional[str] = None,
    decision_date=None,
    language: Optional[str] = None,
) -> Optional[str]:
    """Mint a Swiss ECLI URI for a decision.

    Returns the ECLI string or None if no year can be derived (year is
    a mandatory component of ECLI).

    The language tag is NOT appended to the ECLI ID — per the
    Council-of-EU spec the ECLI uniquely identifies the *decision*, not
    its language variant. Trilingual BGEs share one ECLI; the language
    is conveyed separately via inLanguage in Schema.org metadata.
    """
    if not court or not decision_id:
        return None

    year = _derive_year(decision_date, docket_number)
    if year is None:
        return None

    # Court component
    court_lc = court.lower()
    if court_lc in _COURT_TO_ECLI:
        ecli_court = _COURT_TO_ECLI[court_lc]
    elif "_" in court_lc:
        ecli_court = _cantonal_ecli_court(court_lc)
    else:
        ecli_court = court_lc.upper()

    # ID component
    if ecli_court == "BGE" and docket_number:
        # Try to parse the "BGE 140 III 86" form for a canonical BGE ECLI.
        m = _BGE_DOCKET.match(docket_number)
        if m:
            vol, div, page = m.group(1), m.group(2).upper(), m.group(3)
            return f"ECLI:CH:BGE:{year}:{vol}.{div}.{page}"
        # Fall through to the generic docket normalisation if the docket
        # doesn't match the canonical BGE form.

    if not docket_number:
        # No docket → fall back to a derived ID from the decision_id tail
        # so the ECLI is at least stable. Strip the court prefix.
        tail = decision_id
        if tail.lower().startswith(court_lc + "_"):
            tail = tail[len(court_lc) + 1:]
        ecli_id = _normalize_docket(tail)
    else:
        ecli_id = _normalize_docket(docket_number)

    if not ecli_id:
        return None

    return f"ECLI:CH:{ecli_court}:{year}:{ecli_id}"


def mint_ecli_from_row(row) -> Optional[str]:
    """Convenience: take a sqlite3.Row or dict and mint an ECLI from it."""
    if hasattr(row, "keys"):
        get = lambda k: row[k] if k in row.keys() else None
    else:
        get = lambda k: row.get(k)
    return mint_ecli(
        decision_id=get("decision_id"),
        court=get("court"),
        docket_number=get("docket_number"),
        decision_date=get("decision_date"),
        language=get("language"),
    )
