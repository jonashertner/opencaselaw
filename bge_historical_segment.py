"""Cut a historical BGE (volumes 1-79) down to its own ruling.

DFR serves every ruling of volumes 1-79 as the scanned page range it is printed
on, in double-page spreads. The text of BGE 78 IV 83 therefore opens with the
last page of No 21 ("82 / Strafgesetzbuch. No 21. / Einen Mann in diesem Alter
...") and closes with the first page of No 23 ("23. Auszug aus dem Urteil des
Kassationshofes vom 30. Mai 1952 i. S. Staatsanwaltschaft ... gegen Friedlin").
Search found the neighbours' sentences in the wrong ruling, the canonical-date
pass dated it by a letter of 17 July 1951 quoted in No 21, and the structure
extractor read the serial number "23." as Erwägung 23 (user report 2026-10-07:
1,336 of 14,578 rows carry two or more ruling headers).

A ruling's own header is the serial-numbered heading on the page its BGE
reference names: "22. Auszug aus dem Urteil des Kassationshofes vom 3. Juni 1952
/ i. S. Fyg gegen Born.", "30. Arret du 24 Juin 1887 dans la cause Aebi",
"95. Urtheil vom 16. November 1888 / in Sachen Staub gegen Rust.", "15. Sentenza
del30 aprile 1875 nella Cattsa ...". The text layer prints the page numbers on
lines of their own, so the page a header stands on is the last page number
before it. The ruling runs from its own header to the next ruling's header.

Conservative by construction:
  * no own header on the reference page -> segment() returns None and the
    caller keeps the text unchanged (the Fraktur volumes of 1875-1880 read as
    "Urt~cil bom 19. 3unt 1875 in ~ad)en" and are never cut);
  * a header OCR garbled beyond recognition only means the cut keeps too much;
  * a ruling header is confirmed by its parties or date ("i. S.", "dans la
    cause", "vom 3. Juni"), and a heading whose ruling noun OCR destroyed
    ("47. A.rtet du 3 Juin 1882 dans la cause Curiel.") counts only when its
    serial number continues a confirmed one in the same text.

Pure functions, no I/O: shared by scrapers/bge_historical.py,
scripts/segment_bge_historical.py and backfill_canonical_identity.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# The ruling noun of a header, optionally introduced as an excerpt. Spelling
# covers the 19th-century "Urtheil" and the accent-less "Arret".
_RULING = (
    r"(?:Auszug\s+aus\s+(?:dem|der|den)\s+|Ausz[üu]ge\s+aus\s+(?:dem|den)\s+"
    r"|Extraits?\s+(?:de\s+l['’]\s*|de\s+la\s+|du\s+|des\s+)"
    r"|Estratt[oi]\s+(?:della\s+|dalla\s+|del\s+|dello\s+|dell['’]\s*))?"
    r"(?:Urt(?:h)?eils?|Entscheid(?:ung)?|Beschlu(?:ss|ß)|Bescheid|Verf[üu]gung"
    r"|Arr[êe]ts?|Jugement|D[ée]cision|Sentenza|Decisione|Giudizio|Decreto)\b"
)
_SERIAL = r"(?m)^[ \t]*(?P<serial>\d{1,3})[ \t]*[.,][ \t]*"

# Serial number, then the ruling noun.
HEADER_RE = re.compile(_SERIAL + _RULING, re.IGNORECASE)

# Serial number, at most four words, then the ruling date on the same line:
# the shape of a header whose ruling noun OCR garbled ("47. A.rtet du 3 Juin
# 1882", "32. Sll1teDl& 24 ottobre 1919"). The month must be a real one.
_MONTHS = (
    "Januar|Februar|M[äa]rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
    "|janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre"
    "|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|dicembre"
)
_DATE_TOKEN = (r"(?:(?:vom|du|del|dell['’])\s*)?\S{1,3}?\s*(?:" + _MONTHS
               + r")\.?\s+1[89]\d\d")
WEAK_HEADER_RE = re.compile(
    _SERIAL + r"(?:[^\s\d][^\s]{0,14}[ \t]+){1,4}" + _DATE_TOKEN, re.IGNORECASE
)

# A header names the parties or the date within its first lines. Requiring one
# of them keeps an Erwägung that happens to open "1. Urteil ..." out.
_PARTIES = (r"\bi\s*\.\s*S\s*\.|\bin\s+Sachen\b|\bdans\s+la\s*,?\s*cause\b"
            r"|\ben\s+la\s+cause\b|\bnella\s+ca\w{2,4}\b|\bin\s+causa\b")
_HEADER_CONFIRM_RE = re.compile(
    _PARTIES + r"|\bgegen\b|\bcontre\b|\bcontro\b|\b(?:vom|du|del|dell['’])\s*\d{1,2}",
    re.IGNORECASE,
)
_PARTIES_RE = re.compile(_PARTIES, re.IGNORECASE)
_HEADER_SPAN = 300

# A page number stands alone on its line; OCR sometimes adds a quote ('81"').
_PAGE_LINE_RE = re.compile(r"(?m)^[ \t]*(\d{1,4})[ \t]*[\"'’`]?[ \t]*$")
# Pages run consecutively; a larger jump is a figure in the text or OCR noise.
_MAX_PAGE_STEP = 4


@dataclass(frozen=True)
class Header:
    pos: int
    serial: int
    strong: bool          # the ruling noun survived OCR


@dataclass(frozen=True)
class Segment:
    start: int            # offset of the own header in the source text
    end: int              # offset of the next ruling's header, or len(text)
    serial: int           # the own header's serial number in the volume
    header: str           # the own header's first lines (date and parties)


def ruling_headers(text: str) -> list[Header]:
    """Every confirmed ruling header, in text order."""
    text = text or ""
    strong: dict[int, int] = {}
    for m in HEADER_RE.finditer(text):
        if _HEADER_CONFIRM_RE.search(text[m.end():m.end() + _HEADER_SPAN]):
            strong[m.start()] = int(m.group("serial"))
    # A heading without its ruling noun must name the parties, and its serial
    # must continue another header's in the same text: a numbered Erwägung
    # opening "2. Par arrêt du 12 mars 1880 dans la cause X" has neither a
    # neighbour numbered 1 or 3 among the headers nor, usually, the parties.
    weak: dict[int, int] = {}
    for m in WEAK_HEADER_RE.finditer(text):
        if m.start() in strong:
            continue
        if _PARTIES_RE.search(text[m.start():m.start() + _HEADER_SPAN]):
            weak[m.start()] = int(m.group("serial"))
    serials = set(strong.values()) | set(weak.values())
    out = [Header(p, n, True) for p, n in strong.items()]
    out += [Header(p, n, False) for p, n in weak.items()
            if n - 1 in serials or n + 1 in serials]
    return sorted(out, key=lambda h: h.pos)


def page_marks(text: str, first_page: int) -> list[tuple[int, int]]:
    """(offset, page) of the page-number lines, as one increasing run.

    A spread starts at most one page before the ruling; numbers outside the
    run are figures in the text, not page numbers."""
    marks: list[tuple[int, int]] = []
    last = first_page - 2
    for m in _PAGE_LINE_RE.finditer(text or ""):
        n = int(m.group(1))
        if last < n <= last + _MAX_PAGE_STEP:
            marks.append((m.start(), n))
            last = n
    return marks


def header_block(text: str, start: int) -> str:
    """The own header's first lines (serial line up to the end of the parties
    sentence), joined with spaces; a date glued to its preposition is split."""
    lines = text[start:start + _HEADER_SPAN].split("\n")
    out: list[str] = []
    for line in lines[:4]:
        out.append(line.strip())
        # The header ends with its parties sentence; the Regeste after it
        # cites statutes by date, which must not be read as the ruling date.
        if re.search(r"\.\s*$", line):
            break
    block = re.sub(r"\s+", " ", " ".join(out)).strip()
    return re.sub(r"\b(vom|du|del)(?=\d)", r"\1 ", block, flags=re.IGNORECASE)


def _own_header(headers: list[Header], marks: list[tuple[int, int]], text: str,
                page: int) -> int | None:
    """Index of the own header in ``headers``, or None when it cannot be placed."""
    after = [pos for pos, n in marks if n > page]
    page_end = after[0] if after else len(text)
    on_page = [pos for pos, n in marks if n == page]
    if on_page:
        lo = on_page[0]
    else:
        # The page's own number did not survive OCR: its header stands after
        # the last earlier page number (or the start of the text) and before
        # the next later one. The earliest such header is taken: a wrong pick
        # can then only keep a neighbour's tail, never drop the ruling's head.
        before = [pos for pos, n in marks if n < page]
        lo = before[-1] if before else 0
        if not marks:
            # No page number at all: only a header that opens the text (a
            # running head may precede it) is safe to call the own one.
            if headers and len(text[:headers[0].pos].strip()) <= 120:
                return 0
            return None
    for i, h in enumerate(headers):
        if lo <= h.pos < page_end:
            return i
    return None


def segment(text: str, page: int) -> Segment | None:
    """The own ruling of a page-range text whose BGE reference names ``page``.

    None when no confirmed header can be placed on that page; the caller then
    keeps the text unchanged."""
    if not text:
        return None
    headers = ruling_headers(text)
    if not headers:
        return None
    marks = page_marks(text, page)
    own = _own_header(headers, marks, text, page)
    if own is None:
        return None
    start, serial = headers[own].pos, headers[own].serial
    on_page_end = next((pos for pos, n in marks if n > page), None)
    end = len(text)
    for h in headers[own + 1:]:
        if not serial < h.serial <= serial + 3:
            continue        # an Erwägung number or a citation, not the next ruling
        if on_page_end is not None and h.pos < on_page_end and any(n == page for _, n in marks):
            continue        # a second ruling on the reference page shares its number
        end = h.pos
        break
    return Segment(start=start, end=end, serial=serial, header=header_block(text, start))


def own_text(text: str, page: int) -> tuple[str, Segment | None]:
    """(the ruling's own text, its Segment) — the text unchanged and None when
    the own header cannot be placed."""
    seg = segment(text, page)
    if seg is None:
        return text, None
    return text[seg.start:seg.end].rstrip(), seg


# OCR reads the digit 1 as "l" or "I" and 0 as "O" in a day number ("Arret du 1l
# juillet 1927" = 11 July, BGE 53 III 104). Only a day token that keeps at least
# one real digit and stands before a month name is repaired.
_DAY_OCR_RE = re.compile(
    r"\b((?=[0-9lIO]{0,1}\d)[0-9lIO]{1,2})(\.?\s*(?:" + _MONTHS + r")\b)", re.IGNORECASE
)
_DAY_OCR_MAP = str.maketrans("lIO", "110")
# Other OCR shapes of a header date measured on served rows: accents dropped
# ("du 28 fevrier 1947", BGE 73 IV 132), stray marks between day and month
# ("vom 11 • .Juni 1943", BGE 69 IV 54), "Mai" read as "Mal" after a day.
_ACCENTS = ((r"\bfevrier\b", "février"), (r"\baout\b", "août"),
            (r"\bdecembre\b", "décembre"), (r"\bMarz\b", "März"))
_DAY_NOISE_RE = re.compile(r"\b(\d{1,2})\s*[•·.,:;'\"]+\s*\.?\s*(?=(?:" + _MONTHS + r")\b)",
                           re.IGNORECASE)
_MAL_RE = re.compile(r"\b(\d{1,2}\.?\s*)Mal(?=\s+1[89]\d\d\b)")


def normalise_header_date(header: str) -> str:
    """The header with the measured OCR damage to its date undone."""
    header = _DAY_OCR_RE.sub(lambda m: m.group(1).translate(_DAY_OCR_MAP) + m.group(2), header)
    for pat, rep in _ACCENTS:
        header = re.sub(pat, rep, header, flags=re.IGNORECASE)
    header = _DAY_NOISE_RE.sub(r"\1. ", header)
    return _MAL_RE.sub(r"\1Mai", header)


def header_date(header: str, volume: int) -> date | None:
    """The ruling date written in the own header, gated by the volume year
    (scrapers.bge.parse_urteilskopf: volume year - 3 .. volume year + 1)."""
    from models import parse_date
    from scrapers.bge import BGE_VOLUME_EPOCH, header_date_plausible, parse_urteilskopf

    volume_year = volume + BGE_VOLUME_EPOCH
    header = normalise_header_date(header)
    d = parse_urteilskopf(header, volume_year=volume_year).get("decision_date")
    if d is None:
        # OCR took the preposition ("32. Sll1teDl& 24 ottobre 1919 nella causa"):
        # the header block holds no other date, so its only one is the ruling's.
        d = parse_date(header)
        if not header_date_plausible(d, volume_year):
            d = None
    return d


_REF_RE = re.compile(r"(?:^|_)(\d{1,2})_([IVX]+[ab]?)_(\d{1,4})$", re.IGNORECASE)


def volume_and_page(docket_or_id: str | None) -> tuple[int, int] | None:
    """(volume, page) of a historical BGE id or docket ('78_IV_83',
    'bge_historical_78_IV_83', 'bge_78_IV_83'); None otherwise or for a
    volume after 79."""
    m = _REF_RE.search((docket_or_id or "").strip())
    if not m:
        return None
    vol, page = int(m.group(1)), int(m.group(3))
    if not 1 <= vol <= 79:
        return None
    return vol, page
