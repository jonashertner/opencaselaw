"""
BWO — Bundesamt für Wohnungswesen: Mitteilungen zum Mietrecht
================================================================

"Mitteilungen des Bundesamtes für Wohnungswesen zum Mietrecht — Auszüge aus
Gerichtsentscheiden" (https://www.bwo.admin.ch/de/mitteilungen-zum-mietrecht):
the BWO's periodical digest of cantonal tenancy-law decisions, published
under Art. 23 VMWG. 64 issues ("Bände"), roughly one a year since the 1960s
(Band 21 = 1988 is the oldest checked here; Band 64 = 2025/2026 the newest).

One PDF per Band, genuinely trilingual
---------------------------------------
The page is a Nuxt SSR app; its file list lives in the `__NUXT_DATA__`
payload (`file-teaser-item` nodes), not in plain `<a>` tags, so sdweb.py
cannot be reused here — `_file_teaser_items()` walks that payload directly.
Each Band is listed separately on the DE, FR and IT pages
(/de/mitteilungen-zum-mietrecht, /fr/communications-concernant-le-droit-du-
bail, /it/comunicazioni-concernenti-il-diritto-di-locazione), each under its
own upload (different DAM UUID) — but a live check of Band 64 confirmed the
three uploads are BYTE-IDENTICAL (same SHA-256): one PDF, itself trilingual
inside, uploaded three times for the three listing pages. Only the DE
listing is scraped; fetching the FR/IT pages too would just re-download the
same bytes under a different URL.

Row unit: one row per decision extract
----------------------------------------
Inspection of Band 64 (2025, modern digital-born PDF) and Band 21 (1988,
scanned) shows a consistent per-extract structure: a short Regeste in all
three official languages (whichever two the extract's own court language
is NOT), the extract's own OR/CO/CC article citation on its own line(s),
the full Sachverhalt/Erwägungen or Exposé des faits/Extrait des
considérants or (unheaded, older issues) Tatbestand/Erwägungen text in the
ORIGINAL language of the deciding court, and a closing citation line
naming the court, the decision date and (consistently since ~2000, not
before) a docket — e.g. "Urteil des Obergerichts des Kantons Zürich vom
13. November 2024 (NG240007-O/U)" or the 1988-era bracketed
"(Urteil des Bezirksgerichtes Frauenfeld vom 4. September 1987 in Sachen
M. gegen 14.)".

`split_extracts()` uses that closing citation line as the hard boundary
between extracts (`find_citations`, covering the modern trailing-"(docket)"
form and the older fully-parenthesised form, in German/French/Italian) —
reliable because it is a distinctive, final sentence, unlike the citation's
own verb ("Urteil"/"Arrêt"/...) which also occurs inside the legal
reasoning. Against Band 64 it recovers all 11 TOC-listed extracts; against
Band 21 (1988 scan) 8 of 10 — short of perfect, but within the "ideally one
row per extract" aim the build brief sets, and comfortably better than one
row per issue. `language` and `court`/`date`/`docket` come directly off
that citation; `topics` (the OR/CO/CC articles) are read off the standalone
citation line found near the top of the extract (right after its Regeste,
or — lacking a recognisable Sachverhalt/Fatti/Tatbestand heading to anchor
the search on, as in older or Italian-original extracts — within the
extract's first ~2500 characters); this secondary extraction is best-effort
and occasionally empty or incomplete (see the splitter's own tests).
`title` is deliberately built from the reliable citation fields alone
(court + date + docket) rather than from a heading lifted out of the
trilingual Regeste block, which proved too unreliable to parse generically
across six decades of layout changes.

Fallback: a Band whose text has NO recognisable citation line at all (not
observed in the two Bände checked, but plausible for an early scan with a
badly broken text layer) yields a single whole-issue row instead
(doc_type "mitteilung"), per the build brief's own fallback order.

doc_number: "BWO Mitteilungen Mietrecht Bd. <N> Auszug <i>" (whole-issue
fallback: without the " Auszug <i>" suffix) — stable and unique per extract
since both the volume number and the extract's position within it are
fixed once a Band is published. <i> is the extract's position as found in
the text, NOT the number the Band prints ("No. 3", "Nr. 13"): where a
closing citation is not recognised, two printed extracts share one row, so
the two counts drift apart. "Auszug" keeps it from being read as the printed
number; the court, date and docket in the title are what a reader cites.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Iterator

from .langdedup import body_lang
from .base import (PracticeScraper, _NO_TEXT_LAYER_SENTINEL, extract_pdf_text, first_date_iso,
                   ocr_pdf_text)

logger = logging.getLogger(__name__)

BASE = "https://www.bwo.admin.ch"
DE_PAGE = "/de/mitteilungen-zum-mietrecht"

# ── discovery: the Nuxt __NUXT_DATA__ payload ──────────────────────────
# A "devalue"-style flattened array: most entries are plain values, and a
# [seq] is either a tagged wrapper (["ShallowReactive"|"Reactive", idx]) or
# a plain list/array of further indices. dicts hold {key: index}. Lifted
# from a from-scratch inspection of the page (no Nuxt payload parser
# already exists in this codebase).
_NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _nuxt_payload(html: str) -> list:
    import json
    m = _NUXT_RE.search(html)
    if not m:
        raise ValueError("no __NUXT_DATA__ payload found")
    return json.loads(m.group(1))


def _nuxt_unwrap(data: list, i: int):
    v = data[i]
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in ("ShallowReactive", "Reactive"):
        return _nuxt_unwrap(data, v[1])
    return v


def _nuxt_deref(data: list, i: int, depth: int = 0, max_depth: int = 30):
    if depth > max_depth:
        return "...TRUNCATED"
    v = _nuxt_unwrap(data, i)
    if isinstance(v, list):
        return [_nuxt_deref(data, x, depth + 1, max_depth) if isinstance(x, int) else x for x in v]
    if isinstance(v, dict):
        return {k: (_nuxt_deref(data, x, depth + 1, max_depth) if isinstance(x, int) else x)
                for k, x in v.items()}
    return v


def file_teaser_items(html: str) -> list[dict]:
    """Every `file-teaser-item` node's `properties` dict from the page's
    Nuxt payload, in listing order (newest Band first)."""
    data = _nuxt_payload(html)
    keys = _nuxt_unwrap(data, data[1]["data"])
    page_key = next((k for k in keys if k.endswith(".json")
                      and "/" in k
                      and not any(k.startswith(p) for p in ("index", "main-menus", "footer", "meta-navigation"))),
                     None)
    if page_key is None:
        return []
    full = _nuxt_deref(data, keys[page_key])
    items: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("identifier") == "file-teaser-item":
                items.append(o["properties"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(full)
    return items


_VOLUME_RE = re.compile(r"(\d+)\s*$")
_YEAR_RE = re.compile(r"Band\s+\d+\s*-\s*(\d{4})", re.I)


def band_number(item: dict) -> str:
    m = _VOLUME_RE.search(item.get("title", ""))
    return m.group(1) if m else ""


def band_year(item: dict) -> str:
    m = _YEAR_RE.search(item.get("description", ""))
    return m.group(1) if m else ""


# ── splitting one Band's PDF text into decision extracts ──────────────

_VERB_DE = r"(?:Urteil|Entscheid(?:ung)?|Beschluss|Verf[uü]gung)"
_VERB_FR = r"(?:Jugement|Arr[êe]t|D[ée]cision|Ordonnance)"
_VERB_IT = r"(?:Decisione|Sentenza|Giudizio|Ordinanza)"
_VERB = f"(?:{_VERB_DE}|{_VERB_FR}|{_VERB_IT})"
_COURT_PREP = r"(?:des|der|de la|du|del|della|dei|d[’'])"
_MONTHS = (r"(?:[Jj]anuar|[Ff]ebruar|M[äa]rz|[Aa]pril|[Mm]ai|[Jj]uni|[Jj]uli|[Aa]ugust|"
           r"[Ss]eptember|[Oo]ktober|[Nn]ovember|[Dd]ezember|[Jj]anvier|f[ée]vrier|mars|"
           r"avril|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre|gennaio|"
           r"febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|dicembre)")
_DATE = r"\d{1,2}\.?\s*" + _MONTHS + r"\s+\d{4}"
_VOM = r"(?:vom|du|del|dell[’']|des)"
# The court name may wrap once ("Chambre des baux\net loyers du 11 septembre
# 2017", "Bezirksgerichtspräsidiums Oberlandquart\nvom ..."); a blank line or
# 160 characters ends it, so a match never runs across paragraphs.
_COURT = r"(?:\([A-Z]{1,4}\)\s*|[^\n(]|\n(?!\s*\n)){1,160}?"

_PAT_MODERN = re.compile(
    r"(?P<verb>" + _VERB + r")\s+" + _COURT_PREP + r"\s+(?P<court>" + _COURT + r")\s*"
    + _VOM + r"\s*(?P<date>" + _DATE + r")\s*\n?\s*"
    r"\((?P<docket>[^)]{1,100})\)",
)
_PAT_OLD = re.compile(
    r"\(\s*(?P<verb>" + _VERB + r")\s+" + _COURT_PREP + r"\s+(?P<court>" + _COURT + r")\s*"
    + _VOM + r"\s*(?P<date>" + _DATE + r")(?P<rest>[^)]{0,120})\)",
)

# Exact (accent-folded) verb -> language. Built by hand, not derived from
# the regex patterns above: those contain character classes ("Arr[êe]t")
# whose letters a naive extraction would chop into meaningless fragments.
_VERB_LANG_EXACT: dict[str, str] = {
    "urteil": "de", "entscheid": "de", "entscheidung": "de", "beschluss": "de",
    "verfugung": "de",
    "jugement": "fr", "arret": "fr", "decision": "fr", "ordonnance": "fr",
    "decisione": "it", "sentenza": "it", "giudizio": "it", "ordinanza": "it",
}


def _fold(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _verb_lang(verb: str) -> str:
    key = _fold(re.sub(r"[^A-Za-zÀ-ÿ]", "", verb)).lower()
    return _VERB_LANG_EXACT.get(key, "de")


def is_scan(pdf_bytes: bytes, sample: int = 4) -> bool:
    """True when most sampled pages are covered by one image (a scanned page
    with an OCR text layer). Measured 2026-11-08: Bände 1-52 4/4, 53-64 0/4."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return False
    k = min(sample, len(doc))
    if not k:
        return False
    first = max(0, len(doc) // 2 - k // 2)
    covered = 0
    for i in range(first, first + k):
        page = doc[i]
        area = page.rect.width * page.rect.height or 1
        if any(fitz.Rect(im["bbox"]).width * fitz.Rect(im["bbox"]).height / area > 0.6
               for im in page.get_image_info()):
            covered += 1
    return covered * 2 > k


def _closes_extract(text: str, start: int, end: int) -> bool:
    """A closing citation opens its line or follows a finished sentence, and
    nothing but punctuation follows it on its line. A decision cited in the
    middle of the reasoning ("... während zehn Monaten (Urteil des Bundes-
    gerichts 4A_664/2018 vom 12. März 2019) ...", "bestätigt mit Entscheid
    des Obergerichts Zug ... (Z1 2018 18)") fails one of the two."""
    before = text[max(0, start - 3):start].rstrip(" \t")
    if before and before[-1] not in "\n.;:)»":
        return False
    after = text[end:end + 40].split("\n", 1)[0]
    return not re.search(r"[A-Za-zÀ-ÿ0-9]", after)


def find_citations(text: str) -> list[dict]:
    """Every closing court-citation line in reading order: the hard
    boundary between one decision extract and the next. Covers the
    modern "<Urteil> ... vom <date> (<docket>)" form and the older,
    fully-parenthesised "(<Urteil> ... vom <date> in Sachen ...)" form
    with no docket, in German/French/Italian."""
    matches = []
    for pat in (_PAT_MODERN, _PAT_OLD):
        matches.extend(pat.finditer(text))
    matches.sort(key=lambda m: m.start())
    out: list[dict] = []
    last_end = -1
    for m in matches:
        if m.start() < last_end:
            continue  # the old pattern re-matching inside an already-accepted span
        if not _closes_extract(text, m.start(), m.end()):
            continue  # a decision cited inside the reasoning, not the closing line
        gd = m.groupdict()
        docket = (gd.get("docket") or gd.get("rest") or "").strip(" ,.")
        out.append({
            "start": m.start(), "end": m.end(),
            "language": _verb_lang(gd["verb"]),
            "court": re.sub(r"\s+", " ", gd["court"]).strip(" ,"),
            "date_raw": re.sub(r"\s+", " ", gd["date"]).strip(),
            "docket": re.sub(r"\s+", " ", docket),
        })
        last_end = m.end()
    return out


_NOISE_LINE = re.compile(r"^\s*(?:\d{1,4}|(?:BWO|OFL|UFAB)\s*\|.*)\s*$")


def clean_body(seg: str) -> str:
    """Drop running page headers ("BWO | Mitteilungen zum Mietrecht ,
    Band 64") and bare page-number / margin-digit lines."""
    lines = [ln for ln in seg.split("\n") if not _NOISE_LINE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_BODY_HEAD = re.compile(
    r"^\s*(Sachverhalt|Tatbestand|I\.\s*Tatbestand|Exposé des faits|"
    r"Etat de fait|État de fait|En fait|Fatti|In fatto)\s*$", re.M)
_ART_START = re.compile(r"^\s*Art(?:ikel|icolo)?\.\s*\S")
_STRIP_PARA = re.compile(
    r"\b(?:Abs|al|cpv|lit|let|ch)\.?\s*[\da-z]+(?:\s*(?:und|et|e|and)\s*[\da-z]+)*", re.I)
_CODE_RE = re.compile(r"\b(OR|CO|CC|ZGB|CPC|CPP|OBLF|OLAL|LFus|BMM|AMSL|VMM|OMSL|DAL)\b")
_NUM_RE = re.compile(r"\b(\d{1,4}[a-z]?)\b")


def _parse_article_line(line: str) -> list[str]:
    stripped = _STRIP_PARA.sub(" ", line)
    codes = _CODE_RE.findall(stripped)
    if not codes:
        return []
    code = codes[-1]
    seen: list[str] = []
    for n in _NUM_RE.findall(stripped):
        tag = f"Art. {n} {code}"
        if tag not in seen:
            seen.append(tag)
    return seen


def leading_articles(text: str, seg_start: int, citation_start: int) -> list[str]:
    """The OR/CO/CC article citation(s) near the top of the extract
    (right after its Regeste, just before the Sachverhalt/Fatti heading
    where one is found; otherwise the extract's first ~2500 characters —
    best-effort, see module docstring)."""
    window = text[seg_start:citation_start]
    m = _BODY_HEAD.search(window)
    head = window[max(0, m.start() - 900):m.start()] if m else window[:2500]
    head = clean_body(head)
    lines = head.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _ART_START.match(lines[i]):
            merged = lines[i]
            if i + 1 < len(lines) and not _ART_START.match(lines[i + 1]) and lines[i + 1].strip():
                merged += " " + lines[i + 1]
                i += 1
            for a in _parse_article_line(merged):
                if a not in out:
                    out.append(a)
        i += 1
    return out


def split_extracts(text: str) -> list[dict]:
    """[{index, language, court, date, docket, articles, body_text}] in
    reading order. An empty list means no citation line was found at all
    — the caller then falls back to one row for the whole issue."""
    citations = find_citations(text)
    if not citations:
        return []
    out = []
    seg_start = 0
    for i, c in enumerate(citations):
        articles = leading_articles(text, seg_start, c["start"])
        body = clean_body(text[seg_start:c["end"]])
        seg_start = c["end"]
        out.append({
            "index": i + 1,
            "language": c["language"],
            "court": c["court"],
            "date": first_date_iso(c["date_raw"]) or "",
            "date_display": c["date_raw"],
            "docket": c["docket"],
            "articles": articles,
            "body_text": body,
        })
    return out


# ── scraper ─────────────────────────────────────────────────────────

class BwoMietrechtScraper(PracticeScraper):
    SOURCE_KEY = "bwo_mietrecht"
    ISSUING_AUTHORITY = "BWO"
    DEFAULT_DOC_TYPE = "rechtsprechung"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _extract(self, pdf_bytes: bytes) -> str:
        """pdfplumber for the scanned Bände, PyMuPDF for the born-digital ones.

        Neither reads every Band in order. Bände 1-52 are scans with an OCR
        text layer whose content stream is out of reading order: PyMuPDF
        (base.py's first choice, also on the VPS) emits a page's closing
        citation line before the middle of that page, so the boundary cuts an
        extract in two and glues its tail onto the next one. pdfplumber sorts
        by position, which fixes those, but it reads the two-column layout of
        the born-digital Bände 53- line by line across both columns, where
        PyMuPDF keeps the columns apart."""
        self._last_ocr = False
        text = ""
        if is_scan(pdf_bytes):
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    text = "\n".join((pg.extract_text() or "") for pg in pdf.pages).strip()
            except Exception as e:
                logger.debug("[%s] pdfplumber failed: %s", self.SOURCE_KEY, e)
        if not text:
            text = extract_pdf_text(pdf_bytes)
        if self.OCR_SCANS and len(text.strip()) < self.OCR_MIN_CHARS:
            ocr = ocr_pdf_text(pdf_bytes, self.OCR_LANGS)
            if len(ocr) > len(text.strip()):
                self._last_ocr = True
                return ocr
        return text

    def _page(self, path: str) -> str | None:
        try:
            r = self.get(BASE + path)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, path, e)
            return None

    def _volume_done(self, volume: str) -> bool:
        """True when Auszug 1 of this volume is already on file —
        a cheap stand-in for "is this whole (immutable, once-published)
        Band already indexed" that avoids re-downloading and re-OCRing
        all 64 PDFs on every run."""
        probe = self._make_doc_id({"doc_number": f"BWO Mitteilungen Mietrecht Bd. {volume} Auszug 1"})
        whole = self._make_doc_id({"doc_number": f"BWO Mitteilungen Mietrecht Bd. {volume}"})
        return probe in self._seen_ids or whole in self._seen_ids

    def discover_documents(self) -> Iterator[dict]:
        html = self._page(DE_PAGE)
        if html is None:
            return
        try:
            items = file_teaser_items(html)
        except Exception as e:
            logger.warning("[%s] failed to parse Nuxt payload: %s", self.SOURCE_KEY, e)
            return
        logger.info("[%s] %d Bände listed", self.SOURCE_KEY, len(items))

        for item in items:
            volume = band_number(item)
            year = band_year(item)
            pdf_url = item.get("filePreviewUrl", "")
            if not volume or not pdf_url:
                logger.warning("[%s] unparsable listing: %s", self.SOURCE_KEY, item.get("title"))
                continue
            if self._volume_done(volume):
                continue

            text = self.fetch_pdf_text(pdf_url)
            scanned = self._last_ocr
            if text == _NO_TEXT_LAYER_SENTINEL:
                text = ""
            if not text:
                logger.warning("[%s] Bd. %s: no text extracted", self.SOURCE_KEY, volume)
                yield {
                    "pdf_url": pdf_url, "url": BASE + DE_PAGE,
                    "title": f"Mitteilungen zum Mietrecht, Band {volume}"
                             + (f" ({year})" if year else ""),
                    "doc_number": f"BWO Mitteilungen Mietrecht Bd. {volume}",
                    "date": year, "language": "de", "doc_type": "mitteilung",
                    "topics": ["Mietrecht"],
                    "body_text": f"Mitteilungen zum Mietrecht, Band {volume}. "
                                  f"{self.NO_TEXT_LAYER_BODY}",
                }
                continue

            extracts = split_extracts(text)
            if not extracts:
                logger.warning("[%s] Bd. %s: no extract boundaries found, "
                                "falling back to one row for the whole issue",
                                self.SOURCE_KEY, volume)
                topics = ["Mietrecht"] + (["OCR"] if scanned else [])
                yield {
                    "pdf_url": pdf_url, "url": BASE + DE_PAGE,
                    "title": f"Mitteilungen zum Mietrecht, Band {volume}"
                             + (f" ({year})" if year else ""),
                    "doc_number": f"BWO Mitteilungen Mietrecht Bd. {volume}",
                    "date": year, "language": "de", "doc_type": "mitteilung",
                    "topics": topics, "body_text": text,
                }
                continue

            logger.info("[%s] Bd. %s: %d extracts", self.SOURCE_KEY, volume, len(extracts))
            for ex in extracts:
                topics = [*ex["articles"]] + (["OCR"] if scanned else [])
                title = f"BWO Mitteilungen Mietrecht Bd. {volume}, Auszug {ex['index']}: " \
                        f"{ex['court']}, {ex['date_display']}" \
                        + (f" ({ex['docket']})" if ex["docket"] else "")
                yield {
                    "pdf_url": pdf_url, "url": BASE + DE_PAGE,
                    "title": title,
                    "doc_number": f"BWO Mitteilungen Mietrecht Bd. {volume} Auszug {ex['index']}",
                    "date": ex["date"] or year,
                    # the citation line's language is a fallback: a French
                    # judgment can close with a German citation line
                    "language": body_lang(ex["body_text"]) or ex["language"],
                    "doc_type": "rechtsprechung", "topics": topics,
                    "body_text": ex["body_text"],
                }
