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
    serial number continues a confirmed one in the same text;
  * the own header lies within one page of where the reference page begins;
    two candidates without the reference page's number, or a heading numbered
    one less on the reference page (the own heading, garbled), leave the text
    whole rather than take the next ruling;
  * a day number with OCR noise glued to it gives no date (glued_day), nor
    does a year the volume cannot hold (historical_year_plausible).
Validated on the 14,578 published rows and 48 rows read against the DFR scans
(runbooks/historical_bge_and_sg_twins_2026-10-07.md, "Validation").

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
    r"|Arr[êe]ts?|Jugement|D[ée]cision|Sentenza|Decisione|Giudizio|Decreto"
    # the OCR of volumes 28-39 that opens a text with its own header, without
    # page numbers ("6. Arteil vom 2. Februar 1905 / in Sachen Zuppinger",
    # BGE 31 I 33; 1,248 rows were left whole for want of these spellings)
    r"|Ar[tlkf]ei[lf]|Urheil|Ustheil|Urieil|E(?:ut|n|nd)scheid"
    # the noun with OCR damage measured on 1,121 unplaced rows of volumes
    # 40-64: a stray mark or a misread first letter ("'Urteil", "T1rteilvom
    # 12. März 1914", BGE 40 I 116; never a lower-case one, so "Vorteil" is
    # not a ruling), "Arrit" for "Arrêt" (41 I 384)
    r"|(?-i:[^\sa-zäöü]{0,2})rt(?:h)?eil(?:vom)?|Arr[iä]t)\b"
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
# "i. S." loses its first period to OCR ("16. Entscheid vom as. April 1937 i S.
# Schweiz.", BGE 63 III 57).
# "cause" in the OCR spellings measured on served headers ("dans la causa
# Richoz contra Bavaud", BGE 62 II 193; "cattse", "canse", "eause").
_PARTIES = (r"\bi\s*\.?\s*S\s*\.|\bin\s+Sachen\b"
            r"|\bdans\s+la\s*,?\s*(?:cause|causa|cattse|canse|eause)\b"
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
# A page of the text layer holds about 2,000 characters (median 1,970-2,190 per
# decade of volumes, 99th percentile 2,727; 14,578 rows, 2026-10-07). The own
# header stands on its reference page, so it lies within one page of where that
# page starts; a header further on stands on a later page (BGE 44 III 163 took
# No 45 from 7,267 characters after page 162).
_PAGE_CHARS_MAX = 3000
# On a right-hand page the text layer can read the ruling header before the
# running head's page number: "73. Arret du 21 Septembre 1888 dans la cause
# Godat / contre Hotfmann. / 479" (BGE 14 I 479; the DFR scan has No 73 at the
# top of page 479). 333 of the 14,578 rows show it, all before an odd page
# number, the number within five lines and 400 characters of the header.
_PRE_MARK_LINES = 5
_PRE_MARK_CHARS = 400


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


def _header_block_end(text: str, start: int) -> int:
    """Offset just past the own header's lines, as header_block reads them."""
    pos = start
    for i, line in enumerate(text[start:start + _HEADER_SPAN].split("\n")[:4]):
        pos += len(line) + 1
        if re.search(r"\.\s*$", line):
            break
    return min(pos, len(text))


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


def _header_pages(headers: list[Header], marks: list[tuple[int, int]],
                  page: int, text: str) -> list[int | None]:
    """The page each header stands on: the last page number before it (None
    before the first one). A header whose lines the reference page's own odd
    number follows stands on the reference page (a right-hand page read header
    first). That reading is never extended to other pages: a header can also
    end a page ("16. Arret du 29 Mars 1878 dans la cause Bonvin. / Par exploit
    ..." then 61, BGE 4 I 60)."""
    own = next((pos for pos, n in marks if n == page), None)
    out: list[int | None] = []
    for i, h in enumerate(headers):
        following = headers[i + 1].pos if i + 1 < len(headers) else len(text)
        if (own is not None and page % 2 == 1 and h.pos < own < following
                and own - h.pos <= _PRE_MARK_CHARS
                and text.count("\n", h.pos, own) <= _PRE_MARK_LINES
                and not any(h.pos < pos < own for pos, _ in marks)):
            out.append(page)
            continue
        before = [n for pos, n in marks if pos < h.pos]
        out.append(before[-1] if before else None)
    return out


def _own_header(headers: list[Header], pages: list[int | None],
                marks: list[tuple[int, int]], text: str, page: int) -> int | None:
    """Index of the own header in ``headers``, or None when it cannot be placed."""
    on_page = [pos for pos, n in marks if n == page]
    if on_page:
        top = on_page[0]
        for i, h in enumerate(headers):
            if pages[i] == page and h.pos - top <= _PAGE_CHARS_MAX:
                return i
        return None
    if not marks:
        # No page number at all: only a header that opens the text (a
        # running head may precede it) is safe to call the own one.
        if headers and len(text[:headers[0].pos].strip()) <= 120:
            return 0
        return None
    # The page's own number did not survive OCR: its header stands after the
    # last earlier page number (or the start of the text), before the next
    # later one, and within one page of where the reference page begins.
    before = [(pos, n) for pos, n in marks if n < page]
    after = [pos for pos, n in marks if n > page]
    lo = before[-1][0] if before else 0
    hi = after[0] if after else len(text)
    if before:
        hi = min(hi, lo + (page - before[-1][1] + 1) * _PAGE_CHARS_MAX)
    found = [i for i, h in enumerate(headers) if lo <= h.pos < hi]
    # Two headers in that span: the first can be a short ruling that begins
    # on the page before; without the page number nothing tells them apart,
    # and the wrong pick would carry the neighbour's text and date.
    if len(found) != 1:
        return None
    return found[0]


# A heading whose ruling noun OCR garbled beyond the HEADER_RE spellings: the
# serial, an optional "Auszug aus dem" in any spelling ("Anszng a.us aem",
# "AUSlug aus dem"), then the remains of the noun ("Besohluss", "Orteil",
# "Eztnit da l'arret", "trrteil").
_GARBLED_HEADING_RE = re.compile(
    r"[ \t]*[.,][ \t]*(?:A\S{0,3}[sSzZlL]\S{0,4}[ \t]+\S{1,6}[ \t]+\S{1,5}[ \t]+)?"
    r"\S{0,3}(?:Urt|Ort|rrt|Entsch|Intsch|Ents|Arr|Arn|Ar\S?t|Sent|Bes[co]h|Beschl|Extr|Eztr|Eztn"
    r"|Estr|D[ée]cis|Jug)",
    re.IGNORECASE,
)


def _garbled_previous_header(text: str, lo: int, start: int, serial: int,
                             own_page_known: bool = True) -> bool:
    """True when a heading numbered ``serial - 1`` stands between the reference
    page's top and the chosen header: then OCR garbled the own ruling's header
    and the chosen one is the next ruling's ("35. Auszug aus dem Besohluss vom
    aa. September 1Sa1 / i. S. Bürer." before No 36 on BGE 47 III 116; "7.
    Auszug sous dem Urteil" before No 8 on BGE 45 I 54; DFR scans). Taking No 36
    would replace the ruling by its neighbour and date it by the neighbour."""
    if serial < 2:
        return False
    own_years = {int(y) for y in re.findall(r"\b(1[89]\d\d)\b", header_block(text, start))}
    pat = re.compile(rf"(?m)^[ \t]*{serial - 1}(?=[ \t]*[.,])")
    for m in pat.finditer(text, max(0, lo), start):
        head = text[m.end():m.end() + 60]
        if re.match(r"[ \t]*[.,][ \t]*(?:" + _MONTHS + r")\b", head, re.IGNORECASE):
            continue        # "28. November 1916 aufgehoben": a date, not a heading
        block = text[m.start():min(start, m.start() + 220)]
        if _GARBLED_HEADING_RE.match(head) and _PARTIES_RE.search(block):
            return True
        # Fraktur read as Antiqua keeps only the number and the year: "141.
        # mef~luü Uom 9. ,sufi 1875 in Sa~en ma~V!i." before No 142 on BGE
        # 1 I 520 (No 142 begins on page 521). Only on the reference page
        # itself: before its number the previous page can hold the previous
        # ruling's heading (BGE 44 II 447, 45 II 215).
        if not own_page_known:
            continue
        line = text[m.start():text.find("\n", m.start()) if "\n" in text[m.start():start] else start]
        if len(line) <= 100 and any(abs(int(y) - o) <= 1 for y in re.findall(r"\b(1[89]\d\d)\b", line)
                                    for o in own_years):
            return True
    return False


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
    pages = _header_pages(headers, marks, page, text)
    own = _own_header(headers, pages, marks, text, page)
    if own is None:
        return None
    start, serial = headers[own].pos, headers[own].serial
    on_page_end = next((pos for pos, n in marks if n > page), None)
    own_top = next((pos for pos, n in marks if n == page), None)
    earlier = [pos for pos, n in marks if n < page]
    page_top = min(own_top if own_top is not None else (earlier[-1] if earlier else 0), start)
    if _garbled_previous_header(text, page_top, start, serial, own_top is not None):
        return None
    end = len(text)
    for i in range(own + 1, len(headers)):
        h = headers[i]
        # Past one page from the reference page's top the header stands on a
        # later page even when that page's number did not survive OCR.
        later_page = ((on_page_end is not None and h.pos >= on_page_end)
                      or (own_top is not None and h.pos - own_top > _PAGE_CHARS_MAX))
        if not (serial < h.serial <= serial + 3
                # OCR dropped a digit of the own serial ("2. Urteil der 11.
                # Zivilabteilung" for No 22, BGE 79 II 137): a full ruling
                # header on a later page still ends the ruling
                or (h.strong and h.serial > serial and later_page)):
            continue        # an Erwägung number or a citation, not the next ruling
        if on_page_end is not None and own_top is not None and not later_page:
            continue        # a second ruling on the reference page shares its number
        end = h.pos
        break
    nxt = _next_serial_line(text, start, serial)
    if nxt is not None and nxt < end and (on_page_end is None or nxt >= on_page_end or own_top is None
                                          or nxt - own_top > _PAGE_CHARS_MAX):
        end = nxt
    return Segment(start=start, end=end, serial=serial, header=header_block(text, start))


def _next_serial_line(text: str, start: int, serial: int) -> int | None:
    """Offset of the next ruling's heading when OCR garbled both its ruling noun
    and its month ("13. Ardt du 4 aoftt 1949 dans la cause Hausmann.", after
    No 12 = BGE 75 III 44): the line opens with the serial number that follows
    the own one, names a year and the parties on its first two lines."""
    own_years = {int(y) for y in re.findall(r"\b(1[89]\d\d)\b", header_block(text, start))}
    if not own_years:
        return None
    # "13. -" opens an Erwägung, never a ruling; the year must be the own
    # ruling's or a neighbouring one (a numbered paragraph citing another
    # ruling "vom ... 1938 i. S. X" does not pass). A number followed by a
    # month is a date, and the own header's lines are never the next ruling:
    # "9. Auszug aus dem Urteil des Kassationshofes vom / 10. März 1926 i. S.
    # Bundesanwaltschaft gegen Stettler." (BGE 52 I 54) is one header.
    pat = re.compile(
        rf"(?m)^[ \t]*{serial + 1}[ \t]*[.,][ \t]*(?![-\u2013\u2014])(?!(?:{_MONTHS})\b)"
        rf"\S[^\n]{{0,120}}?\b(1[89]\d\d)\b",
        re.IGNORECASE,
    )
    for m in pat.finditer(text, max(start + 1, _header_block_end(text, start))):
        if not any(abs(int(m.group(1)) - y) <= 1 for y in own_years):
            continue
        if _PARTIES_RE.search(text[m.start():m.start() + 200]):
            return m.start()
    return None


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


_DAY_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2})\.?\s*(?:" + _MONTHS + r")\b", re.IGNORECASE)


_MONTH_NUMBERS = (
    ("januar", 1), ("janvier", 1), ("gennaio", 1), ("februar", 2), ("février", 2), ("febbraio", 2),
    ("märz", 3), ("marz", 3), ("mars", 3), ("marzo", 3), ("april", 4), ("avril", 4), ("aprile", 4),
    ("mai", 5), ("maggio", 5), ("juni", 6), ("juin", 6), ("giugno", 6), ("juli", 7),
    ("juillet", 7), ("luglio", 7), ("august", 8), ("août", 8), ("aout", 8), ("agosto", 8),
    ("september", 9), ("septembre", 9), ("settembre", 9), ("oktober", 10), ("octobre", 10),
    ("ottobre", 10), ("november", 11), ("novembre", 11), ("dezember", 12), ("décembre", 12),
    ("decembre", 12), ("dicembre", 12),
)


def header_months(header: str) -> set[int]:
    """The months a header names in a readable spelling."""
    words = {w.lower() for w in re.findall(r"[^\W\d_]+", normalise_header_date(header or ""))}
    return {n for name, n in _MONTH_NUMBERS if name in words}


def glued_day(header: str, day: int) -> bool:
    """True when ``header`` writes ``day`` as a single digit glued to OCR noise.

    Such a digit lost its neighbour: "22. Sentenza !3 aprile 1914" is of 2
    April (BGE 40 II 109), "64. Sentenza. a2 ottobre 1914" of 22 October (BGE
    40 III 355), "Arret du 1.7 décembre 1875" of the 17th (DFR scans; 127 of
    the served headers glue a character to their day). A hearing range ("vom
    1./2. Dezember 1882") and the Italian elision ("dell'8 luglio") are not
    noise, and a two-digit day has no digit left to lose."""
    for m in _DAY_TOKEN_RE.finditer(header or ""):
        if len(m.group(1)) != 1 or int(m.group(1)) != day:
            continue
        before = header[:m.start(1)]
        if not before or before[-1].isspace() or before[-1] == "/" \
                or re.search(r"dell['’]$", before, re.IGNORECASE):
            continue
        return True
    return False


# Volume N of volumes 1-79 holds the rulings of year N + 1874 and a few late
# ones of the year before. A header year outside that is OCR: of the published
# own-header dates, 8,246 fall in the volume year and 23 in the year before
# (4 of 4 checked on the scans genuine, e.g. 22 February 1877 in BGE 4 I 147);
# the 71 a year after and the 19 three years before were misread ("21.
# Dezember 1915" printed, "1916" read, BGE 41 II 739; 1928 read as 1925, BGE
# 54 III 268; 12 of 12 checked on the scans). The window of scrapers.bge
# (volume year - 3 .. + 1) is kept for the later volumes.
HISTORICAL_LAG_YEARS = 1


def historical_year_plausible(year: int, volume: int) -> bool:
    """A ruling year that volume ``volume`` (1-79) can hold."""
    volume_year = volume + 1874
    return volume_year - HISTORICAL_LAG_YEARS <= year <= volume_year


def header_date(header: str, volume: int) -> date | None:
    """The ruling date written in the own header, gated by the volume year
    (volume year - 1 .. volume year, historical_year_plausible).
    None when the day number is OCR-damaged (glued_day): no date rather than a
    wrong one."""
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
    if d is not None and glued_day(header, d.day):
        return None
    if d is not None and not historical_year_plausible(d.year, volume):
        return None
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
