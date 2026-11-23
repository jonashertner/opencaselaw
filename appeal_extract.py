"""appeal_extract — the decision under review, from the rubrum (P0.2).

The LegalStats wishlist's #1-ranked ask: ``appealed_docket/date/court``
unlock appeal rates, reversal rates and instance-chain durations. The BGer
rubrum states the attacked decision formulaically in all three languages:

  de: "gegen das Urteil des Obergerichts des Kantons Zürich, II.
       Zivilkammer, vom 30. September 2021 (UV.2021.00016)"
  fr: "contre l'arrêt de la Cour de justice ..., Chambre des assurances
       sociales, du 27 mai 2021 (A/103/2021 - ATAS/506/2021)"
  fr': "contre l'arrêt rendu le 6 octobre 2020 par le Tribunal cantonal..."
  it: "contro la sentenza emanata il ... dalla Camera ..."

Extraction is clamped to the head BEFORE the first section marker
(Sachverhalt/Erwägungen/faits/fatto) so Rechtsmittelbelehrung phrases in
the body can never poison the match (the wishlist's own warning). Old-
format EVG headers carry no anchor and stay None — NULL over guess.

Used (post-wiring) by build_fts5 to populate appealed_* columns; kept
self-contained so export/tests can reuse it.
"""
from __future__ import annotations

import re
from datetime import date as _date

_MONTHS = {
    "januar": 1, "février": 2, "februar": 2, "fevrier": 2, "märz": 3,
    "maerz": 3, "mars": 3, "april": 4, "avril": 4, "mai": 5, "juni": 6,
    "juin": 6, "juli": 7, "juillet": 7, "august": 8, "août": 8, "aout": 8,
    "september": 9, "septembre": 9, "oktober": 10, "octobre": 10,
    "november": 11, "novembre": 11, "dezember": 12, "décembre": 12,
    "decembre": 12, "janvier": 1,
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "dicembre": 12,
}

_DATE_WORDS = r"(\d{1,2})(?:\.|er)?\s+([A-Za-zäéûôüè]+)\s+(\d{4})"
_SECTION_CLAMP = re.compile(
    r"(Sachverhalt|Erwägung|Aus den Erwägungen|Faits|consid[ée]rant|"
    r"In Erwägung|Fatti|in fatto|Considerando|Vu\s*:)", re.I)
_DOCKET_IN_PARENS = re.compile(
    r"([A-ZÀ-Ü][A-Za-z0-9ÄÖÜäöü]{0,11}[./ _-]?\d[\w./ -]{0,28}|\d[\w./ -]{2,28})")

_DE = re.compile(
    r"gegen\s+(?:das|den|die)\s+"
    r"(Urteil|Entscheid|Beschluss|Verfügung|Zwischenentscheid|Nichtanhandnahmeverfügung)e?s?\s+"
    r"(?:des|der|de[sr]\s+la)\s+(.{5,160}?),?\s+vom\s+" + _DATE_WORDS +
    r"(?:\s*\(([^)]{2,70})\))?", re.S)
_FR = re.compile(
    r"contre\s+l[ae'’]\s*(arrêt|jugement|décision|ordonnance)\s+"
    r"(?:de\s+la|de\s+l['’]|du|des|rendu[e]?\s+par\s+l[ae'’]?\s*)\s*(.{5,160}?)[,\s]+du\s+"
    + _DATE_WORDS + r"(?:\s*\(([^)]{2,70})\))?", re.S)
_FR_INV = re.compile(
    r"contre\s+l[ae'’]\s*(arrêt|jugement|décision|ordonnance)\s+rendu[e]?\s+le\s+"
    + _DATE_WORDS + r"\s+par\s+(?:le|la|l['’])\s*(.{5,160}?)[.,;(]", re.S)
_IT = re.compile(
    r"contro\s+la\s+(sentenza|decisione|decreto)\s+"
    r"(?:emanata?\s+(?:il\s+" + _DATE_WORDS + r"\s+)?)?"
    r"(?:dalla?|dal|dell['’])\s*(.{5,160}?)"
    r"(?:\s+(?:il|del|in\s+data)\s+" + _DATE_WORDS + r")?[.,(]", re.S)


def _iso(day: str, month_word: str, year: str):
    m = _MONTHS.get(month_word.lower().strip("."))
    if not m:
        return None
    try:
        d, y = int(day), int(year)
        if not 1875 <= y <= 2035:
            return None
        return _date(y, m, d).isoformat()  # refuses 31 April, 29 February 2021
    except ValueError:
        return None


def _clean_court(raw: str):
    court = re.sub(r"\s+", " ", raw).strip(" ,;")
    # drop a trailing chamber fragment ("..., II. Zivilkammer")
    return court if 5 <= len(court) <= 160 else None


def _docket_from_parens(parens: str | None):
    if not parens:
        return None
    m = _DOCKET_IN_PARENS.search(parens.strip())
    return m.group(1).strip() if m else None


def extract_appealed(full_text: str):
    """Return {'appealed_court_raw','appealed_date','appealed_docket',
    'form'} or None. First anchored match in the pre-section head only."""
    if not full_text:
        return None
    head = full_text[:3000]
    clamp = _SECTION_CLAMP.search(head)
    if clamp:
        head = head[:clamp.start()]

    m = _DE.search(head)
    if m:
        return {"form": m.group(1), "appealed_court_raw": _clean_court(m.group(2)),
                "appealed_date": _iso(m.group(3), m.group(4), m.group(5)),
                "appealed_docket": _docket_from_parens(m.group(6))}
    m = _FR.search(head)
    if m:
        return {"form": m.group(1), "appealed_court_raw": _clean_court(m.group(2)),
                "appealed_date": _iso(m.group(3), m.group(4), m.group(5)),
                "appealed_docket": _docket_from_parens(m.group(6))}
    m = _FR_INV.search(head)
    if m:
        return {"form": m.group(1), "appealed_court_raw": _clean_court(m.group(5)),
                "appealed_date": _iso(m.group(2), m.group(3), m.group(4)),
                "appealed_docket": None}
    m = _IT.search(head)
    if m:
        date = None
        if m.group(2) and m.group(3) and m.group(4):
            date = _iso(m.group(2), m.group(3), m.group(4))
        elif m.group(6) and m.group(7) and m.group(8):
            date = _iso(m.group(6), m.group(7), m.group(8))
        return {"form": m.group(1), "appealed_court_raw": _clean_court(m.group(5)),
                "appealed_date": date, "appealed_docket": None}
    return None
