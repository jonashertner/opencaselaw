"""
FINMA Rundschreiben — supervisory circulars, in force and superseded
====================================================================

FINMA circulars state how the Financial Market Supervisory Authority
applies financial market law. They bind FINMA's own supervisory
practice, and every bank, insurer and asset manager is advised against
them, so a practitioner cites them as readily as an ordinance.

They are administrative practice, not court decisions: same class as the
ESTV Kreisschreiben and SEM Weisungen already in this corpus, and they
belong in practice.db / search_practice for the same reason.

Two sources, because FINMA publishes them in two places
-------------------------------------------------------

1. **In force** — a Sitecore search filter behind
   /{lang}/dokumentation/rundschreiben/ (``data-filter-id`` =
   "finmarundschreiben"). One POST returns all of them, no pagination.

2. **Superseded** — a per-year archive under
   /{lang}/dokumentation/archiv/rundschreiben/archiv-{YYYY}/, where the
   year is the year of the circular NUMBER, not of the amendment.

The archive is the more valuable half and the reason this scraper keeps
every version rather than only the newest. It holds two different
things at once:

  * circulars repealed outright, and
  * every superseded VERSION of a circular that is still in force.

FINMA-RS 2008/01 carries five: Erlass 20.11.2008, then amendments of
01.01.2009, 01.09.2011, 01.06.2012 and 01.01.2013. Advising on conduct
from 2010 means reading the version in force in 2010, so collapsing
those to "the latest" would quietly destroy the answer. Each version is
its own document, sharing the circular's ``doc_number`` so a search for
the number returns the whole history, ordered by ``date``.

Languages
---------

DE / FR / IT / EN, taken from each language's own page rather than by
rewriting URLs: the PDFs are genuinely translated (Rundschreiben /
Circulaire / Circolare) and so are the titles, while the URL hash
differs per language. Not every circular exists in every language — EN
in particular 404s often — and a missing translation is skipped, not
faked.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator

from bs4 import BeautifulSoup

from .base import _MONTHS_ALL, PracticeScraper, sha256_hex, slugify

logger = logging.getLogger(__name__)

_HOST = "https://www.finma.ch"
_LANGS = ("de", "fr", "it", "en")

# In-force circulars. The dataset GUID is the one the page's own filter
# widget posts (data-source on the mod-filter div); Order=2 is its
# data-order. Both are stable identifiers in FINMA's CMS, not guesses.
_CURRENT_DATASET = "{3009DAA1-E9A3-4CF1-B0F0-8059B9A37AFA}"
_CURRENT_API = "/{lang}/api/search/getresult"
_CURRENT_PAGE = "/{lang}/dokumentation/rundschreiben/"

_ARCHIVE_INDEX = "/de/dokumentation/archiv/rundschreiben/"

# Status goes into topics, which practice_fts indexes — so it has to be
# searchable in the language the reader is working in, not only German.
_STATUS_IN_FORCE = ["in Kraft", "en vigueur", "in vigore", "in force"]
_STATUS_SUPERSEDED = ["aufgehoben oder ersetzt", "abrogé ou remplacé",
                      "abrogata o sostituita", "repealed or replaced"]

# "2008/01 FINMA-Rundschreiben "Bewilligungs- und Meldepflichten – Banken"
# (31.10.2019)" — the number is the citable identity, the trailing date is
# the circular's own date and NOT the version date.
_NUMBER = re.compile(r"\b(\d{4})/(\d{1,2})\b")
_DMY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

# FINMA names annexes in the file name in every language it publishes.
_ANNEX = re.compile(r"anhang|annexe|allegato|annex", re.I)
_HREFLANG = re.compile(r'<link[^>]+hreflang="([a-z]{2})"[^>]+href="([^"]+)"')
_YEAR_SLUG = re.compile(
    r'href="(/de/dokumentation/archiv/rundschreiben/archiv-\d{4}/)"')
_TAGS = re.compile(r"<[^>]+>")

# The front page of every amended circular prints its own last-amendment
# date: "Letzte Änderung: 4. November 2020 [Änderungen sind mit *
# gekennzeichnet …]", likewise in FR / IT / EN. Some PDFs lay the label
# column out before the value column ("Entrata in vigore: Ultima modifica:
# 1° gennaio 2015 8 marzo 2023 [le modifiche …]"), so the date is taken
# from in front of the bracket first, and from straight after the label
# only when nothing else sits between them. Anything else is no date.
_FRONT_LABEL = (r"(?:Letzte\s+Änderung|Dernière\s+modification"
                r"|Ultima\s+modifica|Last\s+amendment)\s*:")
_WORD_DATE = r"(\d{1,2})(?:\.|er|°|º)?\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})"
_FRONT_HAS_LABEL = re.compile(_FRONT_LABEL, re.I)
_FRONT_BRACKET = re.compile(
    _WORD_DATE + r"\s*\[\s*(?:Änderungen|les\s+modifications"
    r"|le\s+modifiche|Modifications)", re.I)
_FRONT_ADJACENT = re.compile(_FRONT_LABEL + r"\s*" + _WORD_DATE, re.I)
_FRONT_MATTER_CHARS = 4000
_MONTHS = {**_MONTHS_ALL, "january": 1, "february": 2, "march": 3,
           "may": 5, "june": 6, "july": 7, "october": 10, "december": 12}


def _clean(s: str) -> str:
    """Tag-strip, unescape and collapse whitespace in a title."""
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(_TAGS.sub("", s or ""))).strip()


def _iso(dmy: str | None) -> str:
    m = _DMY.search(dmy or "")
    if not m:
        return ""
    d, mth, y = m.groups()
    return f"{y}-{int(mth):02d}-{int(d):02d}"


def _abs(href: str) -> str:
    import html as _html
    href = _html.unescape(href or "").strip()
    return href if href.startswith("http") else _HOST + href


def _doc_number(title: str) -> str:
    """'FINMA-RS 2008/01' — FINMA's own numbering, zero-padded so that
    string ordering matches numeric ordering."""
    m = _NUMBER.search(title or "")
    if not m:
        return ""
    return f"FINMA-RS {m.group(1)}/{int(m.group(2)):02d}"


def _short_number(doc_number: str) -> str:
    """'FINMA-RS 08/1' — the two-digit form practitioners also write.
    Carried in topics so both spellings find the same circular."""
    m = _NUMBER.search(doc_number or "")
    if not m:
        return ""
    return f"FINMA-RS {m.group(1)[2:]}/{int(m.group(2))}"


class FinmaRundschreibenScraper(PracticeScraper):
    SOURCE_KEY = "finma_rs"
    ISSUING_AUTHORITY = "FINMA"
    DEFAULT_DOC_TYPE = "rundschreiben"
    # In-force circulars are re-published in place when amended, at a new
    # URL hash. Without this the corpus would freeze at whichever version
    # we happened to fetch first.
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.0

    def __init__(self):
        super().__init__()
        # Reported at the end of the run: a skipped document must be
        # visible, never silently absent.
        self.skipped_non_pdf = 0
        self.date_disagreements = 0
        self.front_page_dated = 0
        self.front_page_disagreements = 0
        # (doc_number, lang) -> the in-force listing's date, the upper
        # bound for any archived version of that circular.
        self._in_force_dates: dict[tuple[str, str], str] = {}
        self._in_force_langs: set[str] = set()

    def _normalize(self, stub: dict, body_text: str) -> dict:
        """Date documents by the text actually served; see _in_force_date
        and _archive_date. The doc_id is unaffected: it is "current" for
        the in-force version and was fixed from the stub for archived
        ones, so a corrected date never moves or collides a row."""
        doc = super()._normalize(stub, body_text)
        if stub.get("version_key") == "current":
            dated = _in_force_date(doc["date"], body_text)
        elif stub.get("language") in self._in_force_langs:
            # Without that language's in-force listing there is no upper
            # bound, and an archived version could overtake the in-force
            # one, so the listed date stands.
            dated = _archive_date(
                doc["date"], doc["pdf_url"], body_text,
                stub.get("panel_dates") or [],
                self._in_force_dates.get(
                    (stub.get("doc_number", ""), stub.get("language", ""))))
        else:
            dated = doc["date"]
        if dated != doc["date"]:
            doc["date"] = dated
            self.front_page_dated += 1
            return doc
        printed = _front_page_amendment(body_text)
        if printed and printed != doc["date"]:
            self.front_page_disagreements += 1
        return doc

    # ── identity ────────────────────────────────────────────────────

    def _make_doc_id(self, stub: dict) -> str:
        """One id per (circular, version, language).

        The base implementation keys on doc_number alone, which would
        collapse five versions of RS 2008/01 into one row and lose the
        history this scraper exists to keep.
        """
        # Strip the "FINMA-RS " prefix before slugifying: SOURCE_KEY
        # already carries it, and finma_rs_finma_rs_2026_01 helps nobody.
        number = (stub.get("doc_number") or "").replace("FINMA-RS", "").strip()
        base = slugify(number or stub.get("title", ""))
        version = stub.get("version_key") or "current"
        return f"{self.SOURCE_KEY}_{base}_{version}_{stub.get('language', 'de')}"

    # ── discovery ───────────────────────────────────────────────────

    def discover_documents(self) -> Iterator[dict]:
        yielded = 0
        for stub in self._discover_current():
            yielded += 1
            yield stub
        logger.info("[%s] %d in-force circular records", self.SOURCE_KEY, yielded)
        for stub in self._discover_archive():
            yield stub
        logger.info("[%s] archive: %d non-PDF attachments skipped "
                    "(spreadsheets, no extractable text); %d documents where "
                    "FINMA's stated date and its file name disagree — the "
                    "stated date was kept",
                    self.SOURCE_KEY, self.skipped_non_pdf,
                    self.date_disagreements)
        logger.info("[%s] front pages: %d documents dated by their "
                    "last-amendment line; %d other disagreements with "
                    "the listed date — the listed date was kept",
                    self.SOURCE_KEY, self.front_page_dated,
                    self.front_page_disagreements)

    def _discover_current(self) -> Iterator[dict]:
        """The in-force set, from each language's own API endpoint so the
        titles come back translated rather than rewritten."""
        for lang in _LANGS:
            try:
                resp = self.session.post(
                    _HOST + _CURRENT_API.format(lang=lang),
                    data=f"ds={_CURRENT_DATASET}&Order=2",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=60,
                )
                items = (resp.json() or {}).get("Items") or []
            except (ValueError, json.JSONDecodeError, OSError) as e:
                logger.warning("[%s] in-force listing failed for %s: %s",
                               self.SOURCE_KEY, lang, e)
                continue
            logger.info("[%s] %s: %d circulars in force",
                        self.SOURCE_KEY, lang, len(items))
            self._in_force_langs.add(lang)
            for it in items:
                title = _clean(it.get("Title", ""))
                number = _doc_number(title)
                if not number or not it.get("Link"):
                    continue
                # The listing's Date is the circular's own date; the date
                # in brackets at the end of the title says the same thing
                # and covers the rare empty Date field. It can lag behind
                # the text, which _normalize corrects (_in_force_date).
                listed = _iso(it.get("Date")) or _iso(title)
                self._in_force_dates[(number, lang)] = listed
                yield {
                    "pdf_url": _abs(it["Link"]),
                    "title": title,
                    "doc_number": number,
                    "date": listed,
                    "language": lang,
                    "url": _HOST + _CURRENT_PAGE.format(lang=lang),
                    "version_key": "current",
                    "topics": [number, _short_number(number)] + _STATUS_IN_FORCE,
                }

    def _discover_archive(self) -> Iterator[dict]:
        """Superseded circulars and superseded versions, per year page."""
        index = self.get(_HOST + _ARCHIVE_INDEX).text
        year_urls = sorted({_HOST + s for s in _YEAR_SLUG.findall(index)})
        logger.info("[%s] %d archive year pages", self.SOURCE_KEY, len(year_urls))
        for de_url in year_urls:
            for lang, page in self._language_pages(de_url).items():
                n = 0
                for stub in self._parse_archive_page(page, lang, de_url):
                    n += 1
                    yield stub
                logger.info("[%s] %s [%s]: %d archived versions",
                            self.SOURCE_KEY, de_url.rstrip("/").split("/")[-1],
                            lang, n)

    def _language_pages(self, de_url: str) -> dict:
        """{lang: html} for one archive year, from the page's own
        hreflang links. A language that is absent is simply absent."""
        try:
            de_html = self.get(de_url).text
        except OSError as e:
            logger.warning("[%s] archive page failed %s: %s",
                           self.SOURCE_KEY, de_url, e)
            return {}
        pages = {"de": de_html}
        for lang, href in _HREFLANG.findall(de_html):
            if lang == "de" or lang not in _LANGS:
                continue
            try:
                pages[lang] = self.get(_abs(href)).text
            except OSError as e:
                logger.debug("[%s] %s page missing for %s: %s",
                             self.SOURCE_KEY, lang, de_url, e)
        return pages

    def _parse_archive_page(self, page: str, lang: str,
                            de_url: str) -> Iterator[dict]:
        """Each accordion entry is one circular; each teaser box inside it
        is one published document of that circular.

        Parsed structurally rather than by splitting the markup: not every
        teaser carries a "last changed" line, and a regex that scans
        forward from the link happily picks up the NEXT document's date.
        That silently mis-dated versions (RS 2008/11's 25.01.2017 file was
        stamped 13.09.2013), which is the one error a version history must
        not contain.
        """
        soup = BeautifulSoup(page, "html.parser")
        for head in soup.find_all("h2"):
            anchor = head.find("a")
            if anchor is None:
                continue
            title = _clean(anchor.get_text())
            number = _doc_number(title)
            if not number:
                continue
            # The teasers live in the accordion panel that follows the
            # heading; fall back to the heading's own container when the
            # page is not marked up as a definition list.
            dt = head.find_parent("dt")
            panel = dt.find_next_sibling("dd") if dt else None
            if panel is None:
                panel = head.parent
            stubs = []
            for box in panel.select("div.document-teaser-box"):
                link = box.select_one("a.document-teaser-box-title")
                if link is None or not link.get("href"):
                    continue
                pdf_url = _abs(link["href"])
                name = pdf_url.split("/")[-1].split("?")[0]
                # Some annexes are spreadsheets. We cannot extract text
                # from them, and a document with an empty body is noise in
                # a full-text index, so they are skipped and counted.
                if not name.lower().endswith(".pdf"):
                    self.skipped_non_pdf += 1
                    continue
                # FINMA states the version date twice, and the two do not
                # always agree: the file name sometimes carries the date
                # the version took effect (finma-rs-2008-21-20200101.pdf,
                # last changed 31.10.2019) or the original enactment, and
                # a few update lines are simply stale copies of the
                # sibling entry's. The displayed "last changed" line is
                # the field FINMA maintains consistently, so it wins; the
                # file name only fills the 27 entries that have no line at
                # all. Disagreements are counted and reported rather than
                # resolved by guesswork — we publish FINMA's metadata, we
                # do not invent better metadata than the source has.
                upd = box.select_one(".document-teaser-box-update")
                stated = _iso(upd.get_text() if upd else "")
                from_name = _version_from_filename(pdf_url)
                if stated and from_name and stated != from_name:
                    self.date_disagreements += 1
                version_date = stated or from_name
                is_annex = bool(_ANNEX.search(name))
                stubs.append({
                    "pdf_url": pdf_url,
                    "title": title,
                    "doc_number": number,
                    # The version's own date. This is what makes the
                    # history usable: it is the date the reader matches
                    # against the conduct being advised on.
                    "date": version_date,
                    "language": lang,
                    "url": de_url,
                    # A circular and its annexes share a number and a
                    # date, so the file name has to take part in the
                    # identity or the annexes would overwrite each other
                    # on the DB's ON CONFLICT(doc_id).
                    "version_key": (version_date.replace("-", "") or "undated")
                                   + "_" + sha256_hex(name)[:8],
                    "doc_type": ("rundschreiben_anhang" if is_annex
                                 else "rundschreiben"),
                    "topics": ([number, _short_number(number)]
                               + _STATUS_SUPERSEDED
                               + (["Anhang", "annexe", "allegato", "annex"]
                                  if is_annex else [])),
                })
            # Every version date of this circular on this page, so that
            # _archive_date can refuse a correction that would reorder
            # the history or tie it with another version.
            panel_dates = sorted({s["date"] for s in stubs if s["date"]})
            for stub in stubs:
                yield {**stub, "panel_dates": panel_dates}


_FILENAME_DATE = re.compile(r"(20\d{2}|19\d{2})(\d{2})(\d{2})")


def _version_from_filename(pdf_url: str) -> str:
    """Last resort for the version date: FINMA encodes it in the file
    name (rs-08-01-letzte-aenderung-20130101.pdf,
    rs-08_01_erlass_20081120.pdf, rs-08-01-aendper20110901.pdf)."""
    name = pdf_url.split("/")[-1].split("?")[0]
    m = _FILENAME_DATE.search(name)
    if not m:
        return ""
    y, mth, d = m.groups()
    if not (1 <= int(mth) <= 12 and 1 <= int(d) <= 31):
        return ""
    return f"{y}-{mth}-{d}"


def _front_page_amendment(body_text: str) -> str:
    """The last-amendment date the PDF's own front page prints, ISO, or
    "" when the front page has none or it cannot be read unambiguously."""
    head = (body_text or "")[:_FRONT_MATTER_CHARS]
    if not _FRONT_HAS_LABEL.search(head):
        return ""
    for rx in (_FRONT_BRACKET, _FRONT_ADJACENT):
        m = rx.search(head)
        month = _MONTHS.get(m.group(2).lower()) if m else None
        if month:
            return f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}"
    return ""


def _in_force_date(listed: str, body_text: str) -> str:
    """The date of an in-force circular: the listing's, unless the text
    we serve proves a later amendment.

    The in-force listing's Date lags: for RS 2010/01, 2011/01, 2011/02,
    2013/07, 2017/01, 2019/01 and 2019/02 it still carries the date of
    the version before the 4 November 2020 amendment, so the in-force
    text and its archived predecessor showed the same date. The front
    page is FINMA's own statement too, and a later one is proof: a text
    cannot print an amendment that had not happened yet. An earlier one
    proves nothing, since FINMA leaves stale front pages in place (RS
    2008/04's 2013 file still prints 20 November 2008), so the listing
    keeps it. Archived versions follow a stricter rule, _archive_date.
    """
    printed = _front_page_amendment(body_text)
    return printed if printed > (listed or "") else listed


def _archive_date(listed: str, pdf_url: str, body_text: str,
                  panel_dates: list[str], in_force: str | None) -> str:
    """The date of an archived version: the listed "last changed" line,
    unless the front page AND the file name name another, same date, and
    moving there keeps the version history in order.

    Two of FINMA's three statements agreeing against the third is what
    RS 2008/02 shows: listed 03.04.2011, while the front page and
    rs-08-02-letzte-aenderung-20110304.pdf both say 4 March 2011. The front
    page alone is not enough here: Erlass files print a later amendment
    (RS 2008/01's rs-08_01_erlass_20081120.pdf says 20 November 2009) and
    a FR slot holds the 2021 file of RS 2010/03, so trusting it would
    reorder the history. The order guard refuses any date that would
    reach another version of the circular on the page, or the in-force
    version: an archived version that ties with the one in force is the
    two-identical-dates error this exists to remove.
    """
    printed = _front_page_amendment(body_text)
    if not printed or printed == listed:
        return listed
    if printed != _version_from_filename(pdf_url):
        return listed
    lo, hi = sorted((listed or printed, printed))
    # Equal to `listed` is this version itself or one of its annexes.
    if any(lo <= d <= hi for d in panel_dates if d != listed):
        return listed
    if in_force and printed >= in_force:
        return listed
    return printed
