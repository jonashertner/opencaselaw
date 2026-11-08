"""Decision dates read from the source itself.

A decision date is a legal fact. These helpers read it from what the court
publishes: the docket a court mints from the date (BGer's EGMR collection),
the ruling's own header ("Entscheid vom 23. Februar 2026", "Bern, 5. Februar
2026"), the trailer a yearbook prints under each entry ("SB 99 71 / Urteil vom
14. Dezember 1999", "Entscheid Nr. 61/2003 vom 4.7.2003"), the date in the
official file name or URL slug. Never a third-party index.

Every helper returns an ISO string ('YYYY-MM-DD') or None, and None whenever
the source does not state the date unambiguously: a missing date is better
than a wrong one. The scrapers use these for new rows and
scripts/repair_source_dates.py applies the same rules to rows already stored,
so both write the same date for the same document.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    # de
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "dezember": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
    # it
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "dicembre": 12,
    # rm (Romansh rulings of the GR courts)
    "schaner": 1, "favrer": 2, "avrigl": 4, "matg": 5, "zercladur": 6,
    "fanadur": 7, "avust": 8, "settember": 9, "october": 10, "december": 12,
}
_MONTH_ALT = "|".join(sorted((re.escape(m) for m in MONTHS), key=len, reverse=True))

# "23. Februar 2026", "1er septembre 2026", "19 juin 2026", "1°marzo 2024",
# "5.Februar 2026" (no space), "23.02.2026", "4.7.2003", "14.11.02".
_D = r"(?P<d>\d{1,2})(?:\.|er|°)?"
DATE_WORDS = rf"{_D}\s*(?P<m>{_MONTH_ALT})\s+(?P<y>(?:18|19|20)\d{{2}})(?!\d)"
DATE_NUM = r"(?P<nd>\d{1,2})\.\s?(?P<nm>\d{1,2})\.\s?(?P<ny>(?:18|19|20)\d{2}|\d{2})(?!\d)"
_ANY_DATE = re.compile(rf"(?:{DATE_WORDS}|{DATE_NUM})", re.IGNORECASE)


def iso(y: int, m: int, d: int, today: date | None = None) -> str | None:
    """'YYYY-MM-DD' for a real calendar day between 1800 and today, else None."""
    try:
        v = date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None
    if v.year < 1800 or v > (today or date.today()):
        return None
    return v.isoformat()


def _year(y: str, pivot: int | None = None) -> int:
    """A 2-digit year expanded against `pivot` (the docket year when known):
    the century that puts it closest. Without a pivot: 00-49 -> 20xx."""
    n = int(y)
    if len(y) != 2:
        return n
    if pivot is None:
        return 2000 + n if n < 50 else 1900 + n
    cands = (1900 + n, 2000 + n)
    return min(cands, key=lambda c: abs(c - pivot))


def date_from_match(m: re.Match, pivot: int | None = None, today: date | None = None) -> str | None:
    """ISO date from a match of DATE_WORDS / DATE_NUM (either group set)."""
    g = m.groupdict()
    if g.get("m"):
        month = MONTHS.get(g["m"].lower())
        return iso(int(g["y"]), month, int(g["d"]), today) if month else None
    if g.get("nm"):
        return iso(_year(g["ny"], pivot), int(g["nm"]), int(g["nd"]), today)
    return None


def parse_one(s: str, pivot: int | None = None, today: date | None = None) -> str | None:
    """The single date a short string states ("23. Februar 2026", "4.7.2003")."""
    m = _ANY_DATE.search(s or "")
    return date_from_match(m, pivot, today) if m else None


def flat(text: str | None) -> str:
    """Whitespace (incl. NBSP and CR) collapsed to single spaces."""
    return re.sub(r"[\s ]+", " ", text or "").strip()


def is_placeholder(value: str | None, scraped_at: str | None = None) -> bool:
    """Empty, year-0000, a 1 January (a volume/yearbook year or a portal
    sentinel, never a ruling date in these courts) or the scrape day."""
    v = str(value or "").strip()[:10]
    if not v or v.startswith("0000"):
        return True
    if v.endswith("-01-01"):
        return True
    return bool(scraped_at and v == str(scraped_at)[:10])


# ---------------------------------------------------------------- EGMR (BGer)

_EGMR_DOCKET = re.compile(r"^(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})_\d+_\d{2}$")


def egmr_docket_date(docket: str | None, today: date | None = None) -> str | None:
    """BGer's EGMR collection keys each judgment as YYYYMMDD_<appno>_<yy>,
    the date being the judgment's ("20160823_53377_11" = decision of 23 August
    2016 in application no. 53377/11). The listing prints the same date as
    "23.08.16"."""
    m = _EGMR_DOCKET.match(str(docket or "").strip())
    return iso(m["y"], m["m"], m["d"], today) if m else None


def egmr_listing_date(cell: str | None, docket: str | None = None,
                      today: date | None = None) -> str | None:
    """The listing's date column ("05.11.26"); the 2-digit year is expanded
    against the docket's year when there is one."""
    pivot = None
    dd = egmr_docket_date(docket, today)
    if dd:
        pivot = int(dd[:4])
    return parse_one(cell or "", pivot, today)


# ---------------------------------------------------------- ruling headers

# "Entscheid", "Urteil", … and their compounds ("Abschreibungsentscheid",
# "Präsidialverfügung", "Zwischenentscheid"), then at most three plain words
# ("der Einzelrichterin") before "vom". Never "Urteil BGer 2C_405/2021 vom …":
# the appeal note NW prints above its own date line.
_HEAD_KIND = r"[A-Za-zÄÖÜäöü]*(?:entscheid|urteil|beschluss|verfügung|entscheidung)"
_HEAD_FILLER = r"(?:(?!BGer\b|Bundes)[A-Za-zÄÖÜäöü.\-]+\s+){0,3}?"
_HEAD_VOM = re.compile(rf"(?<![A-Za-zÄÖÜäöü]){_HEAD_KIND}\s+{_HEAD_FILLER}vom\s+"
                       rf"(?:{DATE_WORDS}|{DATE_NUM})", re.IGNORECASE)
# Where a ruling's head ends: the bench, the parties, the facts.
_HEAD_END = re.compile(r"\b(?:Besetzung|Verfahrensbeteiligte|Parteien|in\s+Sachen|Sachverhalt|"
                       r"Composition|Participants|Prozessgeschichte)\b", re.IGNORECASE)


def ruling_head(text: str | None, limit: int = 1500) -> str:
    """The part of a ruling before its bench/parties block, flattened."""
    head = flat((text or "")[:limit])
    m = _HEAD_END.search(head, 20)
    return head[:m.start()] if m else head


def head_vom_date(text: str | None, limit: int = 1500, today: date | None = None) -> str | None:
    """The first "<Entscheid|Urteil|Beschluss|…> vom <date>" in the ruling's
    head (UR/NW layout: docket, then "Entscheid vom 23. Februar 2026"), i.e.
    before the bench and parties ("Besetzung", "Verfahrensbeteiligte"). The
    decision a ruling reviews is named after them ("Gegenstand … Entscheid
    Kantonsgericht Nidwalden … vom 12. Januar 2021")."""
    m = _HEAD_VOM.search(ruling_head(text, limit))
    return date_from_match(m, None, today) if m else None


def place_date_head(text: str | None, places: tuple[str, ...], limit: int = 1500,
                    today: date | None = None) -> str | None:
    """"Bern, 5. Februar 2026" / "Berne, le 19 juin 2026" in the head: the
    date line of the BE Obergericht's letterhead. The first such line in the
    first `limit` characters (it precedes "Besetzung" / "Composition")."""
    head = flat((text or "")[:limit])
    alt = "|".join(re.escape(p) for p in places)
    m = re.search(rf"(?<![A-Za-zäöü])(?:{alt}),?\s+(?:le\s+)?(?:{DATE_WORDS}|{DATE_NUM})",
                  head, re.IGNORECASE)
    return date_from_match(m, None, today) if m else None


# ---------------------------------------------------- GR Praxis (PKG/PVG/POG)

GR_YEARBOOK = re.compile(r"^(?P<series>P[KVO]G)\s*[ _]\s*(?P<y>\d{4})\s*[ _]\s*(?P<n>\d+)$")
# The trailer each PKG/PVG entry ends with: the docket of the ruling, then its
# kind and date on the next line ("SB 99 71 \n Urteil vom 14. Dezember 1999",
# "S 23 137 Urteil vom 27. Februar 2024", "U 12 120 Urteil vom 5. Februar 2013").
_GR_DOCKET = r"[A-Z]{1,4}\d?\s+\d{2}\s+\d{1,4}(?:\s*[+/]\s*\d{1,4})*"
_GR_KIND = (r"(?:Urteil|Entscheid|Beschluss|Verfügung|Sentenza|Decisione|Decreto|"
            r"Sentenzia|Decisiun|Disposiziun)")
_GR_TRAILER = re.compile(
    rf"(?:^|\n)[ \t]*(?P<docket>{_GR_DOCKET})[ \t]*\n?[ \t]*{_GR_KIND}\s+(?:vom|del|dal|dals|da)\s+"
    rf"(?:{DATE_WORDS}|{DATE_NUM})", re.IGNORECASE)


def gr_yearbook_trailer(text: str | None, yearbook_docket: str | None = None,
                        today: date | None = None) -> str | None:
    """Date of a GR Praxis entry (PKG/PVG/POG), from the trailer that closes it.

    The entry's PDF is a page range: it can open with the end of the previous
    entry, trailer included ("SB 99 71 / Urteil vom 14. Dezember 1999" heads
    the PDF of PKG 1999 25, whose own trailer, "SB 98 74 / Urteil vom 20.
    Januar 1999", closes it). The last trailer is therefore the entry's own;
    it must also stand in the closing part of the text (last 25 %, at most
    2,500 characters from the end), else the PDF lost it and None is
    returned rather than the previous entry's date. Its year may not be after
    the volume year + 1 (an entry cannot postdate its volume)."""
    t = (text or "").replace("\r", "")
    hits = list(_GR_TRAILER.finditer(t))
    if not hits:
        return None
    last = hits[-1]
    tail_from = max(len(t) - max(2500, len(t) // 4), 0)
    if last.start() < tail_from and len(t) - last.end() > 2500:
        return None
    d = date_from_match(last, None, today)
    if not d:
        return None
    m = GR_YEARBOOK.match(str(yearbook_docket or "").strip())
    if m and int(d[:4]) > int(m["y"]) + 1:
        return None
    return d


def gr_yearbook_pdf_key(url: str | None) -> str | None:
    """The document key of a GR Tribuna download ('PVG_2002_7' from
    …/ServletDownload/PVG_2002_7_4d6e0efb…): the same PDF whichever copy
    (direct scrape or the frozen federation copy) points at it."""
    m = re.search(r"/ServletDownload/(P[KVO]G_\d{4}_\d+)_", str(url or ""))
    return m.group(1) if m else None


# --------------------------------------------------------------- AR GVP

_AR_FILE = re.compile(r"/(?:OG|KG|Verwaltung|VG|RR)-(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})-[^/]*\.pdf$",
                      re.IGNORECASE)


def ar_filename_date(url: str | None, today: date | None = None) -> str | None:
    """The decision date the AR portal writes into the file name of a ruling
    PDF: OG-20200824-ERZ-20-34-20210901-ARGVP-2020-3792.pdf (24 August 2020,
    "Verfügung des Einzelrichters des Obergerichts, 24.08.2020"; 20210901 is
    the publication). For the AR GVP yearbook entries it equals the date the
    entry's trailer prints ("JuAK 14.11.02" for ARGVP 2002 3410), while the
    portal's decisionDate field sometimes holds a date the entry merely cites.
    """
    u = re.sub(r"%20", " ", str(url or ""))
    m = _AR_FILE.search(u)
    return iso(m["y"], m["m"], m["d"], today) if m else None


def is_ar_gvp(url: str | None) -> bool:
    return "GVP/" in re.sub(r"%20", " ", str(url or "")).replace("AR GVP", "GVP")


# ------------------------------------------------------------------ UR

def ur_citation_line(text: str | None, docket: str | None, today: date | None = None) -> str | None:
    """The Rechenschaftsbericht excerpts of the UR Obergericht cite the ruling
    as "Obergericht, 11. Februar 2020, OG V 19 63". Only a line naming the
    row's own docket counts."""
    dk = re.sub(r"^\d{4}_", "", str(docket or "")).strip()
    dk = re.sub(r"[_\s]+", " ", dk).rstrip(". ")
    m = re.match(r"(OG\s*[A-Z]{1,3})\s+(\d{2})\s+(\d{1,4})", dk)
    if not m:
        return None
    pat = re.escape(m.group(1)).replace(r"\ ", r"\s*") + rf"\s+{m.group(2)}\s+{m.group(3)}(?!\d)"
    t = flat(text)
    for hit in re.finditer(rf"Obergericht(?:spräsidium)?,\s+(?:{DATE_WORDS}|{DATE_NUM}),\s+{pat}",
                           t, re.IGNORECASE):
        d = date_from_match(hit, None, today)
        if d:
            return d
    return None


# ------------------------------------------------------------------ BL

_BL_SLUG = re.compile(r"^(?P<y>(?:19|20)\d{2})-(?P<m>\d{2})-(?P<d>\d{2})[-_][a-z]", re.IGNORECASE)


def bl_slug_date(docket: str | None, today: date | None = None) -> str | None:
    """baselland.ch files each Kantonsgericht/Steuergericht ruling under a
    slug that opens with its decision date ('2018-12-06-sv-5' = the SV
    chamber's ruling of 6 December 2018)."""
    m = _BL_SLUG.match(str(docket or "").strip())
    return iso(m["y"], m["m"], m["d"], today) if m else None


def bl_steuergericht_trailer(text: str | None, docket: str | None,
                             today: date | None = None) -> str | None:
    """The Steuergericht's published entries close with "Entscheid Nr.
    61/2003 vom 4.7.2003". Only the line with the row's own number/year."""
    m = re.match(r"^\s*(\d{4})\s*/\s*0*(\d+)\s*$", str(docket or ""))
    if not m:
        return None
    year, num = m.group(1), m.group(2)
    pat = re.compile(rf"Entscheid(?:e)?\s+Nr\.?\s*0*{num}\s*/\s*{year}\s+vom\s+(?:{DATE_WORDS}|{DATE_NUM})",
                     re.IGNORECASE)
    hit = pat.search(flat(text))
    return date_from_match(hit, int(year), today) if hit else None


def bl_kg_head(text: str | None, today: date | None = None) -> str | None:
    """"Urteil des Kantonsgerichts Basel-Landschaft, Abteilung
    Sozialversicherungsrecht vom 29. September 2016" (head of the KG rulings)."""
    head = flat((text or "")[:1500])
    m = re.search(rf"(?:Urteil|Entscheid|Beschluss)\s+des\s+Kantonsgerichts\s+Basel-Landschaft"
                  rf"(?:,\s*[^,]{{0,80}}?)?,?\s+vom\s+(?:{DATE_WORDS}|{DATE_NUM})", head, re.IGNORECASE)
    return date_from_match(m, None, today) if m else None


# ------------------------------------------------------------- plausibility

def docket_year(docket: str | None) -> int | None:
    """The registration year a cantonal docket carries: a 4-digit year token
    ("725 2023 77 / 170", "BK 2025 533") or a 2-digit one after the
    chamber/register prefix ("720 13 319", "VA 21 18", "OG V 22 38",
    "ZK2 17 45"). None when the docket names none."""
    toks = re.findall(r"[A-Za-z]+\d?|\d+", str(docket or ""))
    for t in toks:
        if re.fullmatch(r"(?:19|20)\d{2}", t):
            return int(t)
    for i, t in enumerate(toks[1:], 1):
        if re.fullmatch(r"\d{2}", t) and not re.fullmatch(r"\d{1,2}", toks[i - 1]):
            yy = int(t)
            cur = date.today().year % 100
            return 2000 + yy if yy <= cur else 1900 + yy
    return None


def plausible(new: str, docket: str | None, publication_date: str | None) -> bool:
    """A ruling is not decided more than a year before its docket was
    registered, nor after it was published."""
    y = docket_year(docket)
    if y is not None and int(new[:4]) < y:
        return False
    pub = str(publication_date or "")[:10]
    dated = re.fullmatch(r"(?:19[89]\d|20\d{2})-\d{2}-\d{2}", pub) and not pub.endswith("-01-01")
    return not (dated and new > pub)
