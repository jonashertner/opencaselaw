"""
EMARK Scraper (Asylrekurskommission / ARK)
==========================================

Scrapes published EMARK decisions from the Swiss Asylum Appeals Commission
archive at ark-cra.rekurskommissionen.ch.

The ARK (Schweizerische Asylrekurskommission) was replaced by the
Bundesverwaltungsgericht (BVGer) on January 1, 2007.

Architecture — two archive layouts:

* 1999-2006 ("one file per decision"):
  /assets/resources/ark/emark/{year}/{nr:02d}.htm, decision numbers
  sequential per year (1-42 max). Enumerated from YEAR_RANGES.

* 1993-1998 ("one file per printed page"):
  /assets/resources/ark/emark/{year}/{YY}{NN}{PPP}PUB.htm where YY is the
  two-digit volume year, NN the decision number (Nr.) and PPP the page in
  the yearly volume — e.g. 1997/9708056PUB.htm is EMARK 1997 Nr. 8, S. 56.
  Nothing enumerable: the pages are only reachable through the keyword and
  statute indices d/stichw-98.htm and d/gesetz-98.htm, which link the cited
  pages of each decision (start page under the bare "1997 Nr. 8" entries,
  pinpoints as "1997 Nr. 8, S. 56"). Discovery harvests both indices, dedupes
  to (year, nr), and fetch walks the page chain of a decision from the lowest
  linked page backwards and forwards until the archive returns 404 (the page
  number is part of the file name, so a 404 is the exact boundary of the
  decision). Verified live 2026-09-09: 196 decisions across 1993-1998 (39 /
  29 / 25 / 42 / 27 / 34), matching the counts YEAR_RANGES had assumed but
  could never fetch (the nightly reported them as 196 NoneReturns).

Text is in German, French, or Italian with trilingual summaries.

Rate limiting: 1.0 seconds (static archive, no server load concern)
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://ark-cra.rekurskommissionen.ch/assets/resources/ark/emark"

# Year range and max decision numbers per year (empirically determined)
YEAR_RANGES = {
    1993: 39, 1994: 29, 1995: 25, 1996: 42, 1997: 27, 1998: 34,
    1999: 29, 2000: 30, 2001: 27, 2002: 23, 2003: 30, 2004: 40,
    2005: 25, 2006: 33,
}

# Volumes before this year use the paginated YYNNPPPPUB.htm layout and are
# discovered through the 1993-98 indices instead of YEAR_RANGES enumeration.
FIRST_ENUMERABLE_YEAR = 1999

# Keyword + statute indices covering the 1993-1998 volumes. Both are
# harvested; a decision only cited under a statute head-word still gets found.
PRE1999_INDEX_URLS = (
    f"{BASE_URL}/d/stichw-98.htm",
    f"{BASE_URL}/d/gesetz-98.htm",
)

# ../1997/9708056PUB.htm  → (1997, "97", "08", "056")
PRE1999_HREF_RE = re.compile(
    r"""href\s*=\s*["']?(?:\.\./)?(199[3-8])/(\d{2})(\d{2})(\d{3})PUB\.htm""",
    re.IGNORECASE,
)

# Safety cap on the page walk of a single pre-1999 decision (the longest
# decisions in the 1993-98 volumes run to ~30 printed pages).
MAX_PAGES_PER_DECISION = 80

# Date pattern in EMARK decisions: "24. Januar 2006" or "24 janvier 2006"
DATE_PATTERN_DE = re.compile(
    r"(\d{1,2})\.?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_PATTERN_FR = re.compile(
    r"(\d{1,2})\.?\s*(?:er)?\s*(janvier|février|mars|avril|mai|juin|juillet|"
    r"août|septembre|octobre|novembre|décembre)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_PATTERN_IT = re.compile(
    r"(\d{1,2})\.?\s*(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|"
    r"agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})",
    re.IGNORECASE,
)
DATE_PATTERNS = (DATE_PATTERN_DE, DATE_PATTERN_FR, DATE_PATTERN_IT)

# A decision printed in volume N was handed down in N or shortly before
# (EMARK 1993 Nr. 1 is a judgment of 1 September 1992). Anything older is a
# cited instrument — "Genfer Abkommen vom 12. August 1949", "Accord ... du
# 16 octobre 1980" — not the decision date.
DECISION_DATE_LOOKBACK_YEARS = 2

# Lines that are footnote pointers rather than summary text on a pre-1999
# first page ("Grundsatzentscheid: [1]", "Décision de principe : [2]"), and
# the footnote bodies at the foot of the page ("[1] Entscheid der
# Präsidentenkonferenz ..."), which end the summary block.
_FOOTNOTE_POINTER_RE = re.compile(r"\[\d+\]\s*$")
_FOOTNOTE_BODY_RE = re.compile(r"^\[\d+\]")


def extract_decision_date(text: str, volume_year: int) -> date | None:
    """Pick the decision date out of the head of an EMARK page.

    Takes the EARLIEST month-name date in the first 2000 characters across the
    three languages (the header "Auszug aus dem Urteil der ARK vom 26. Mai
    2004" / "Extraits de la décision de la CRA du 6 octobre 2005" always comes
    first) and rejects dates outside [volume_year - 2, volume_year].

    The old implementation tried the German pattern first over the whole
    window: a French decision whose Regeste mentioned a German-dated
    instrument got that instrument's date (EMARK 2001 Nr. 12 was served as
    1949-08-12, the Geneva Conventions; 17 of the 237 served rows carried an
    instrument date instead of the header date).
    """
    head = text[:2000]
    candidates: list[tuple[int, date]] = []
    for pattern in DATE_PATTERNS:
        for m in pattern.finditer(head):
            parsed = parse_date(f"{m.group(1)} {m.group(2)} {m.group(3)}")
            if parsed is None:
                continue
            if volume_year - DECISION_DATE_LOOKBACK_YEARS <= parsed.year <= volume_year:
                candidates.append((m.start(), parsed))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def pre1999_page_url(year: int, nr: int, page: int) -> str:
    """URL of one printed page of a 1993-1998 decision."""
    return f"{BASE_URL}/{year}/{year % 100:02d}{nr:02d}{page:03d}PUB.htm"


def parse_pre1999_index(html: str) -> dict[tuple[int, int], set[int]]:
    """Harvest (year, nr) -> {linked page numbers} from a 1993-98 index page."""
    found: dict[tuple[int, int], set[int]] = {}
    for m in PRE1999_HREF_RE.finditer(html):
        year, yy, nn, ppp = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
        if year % 100 != int(yy):
            # Directory year and file-name year disagree — never seen live;
            # trust the file name, which is what the server resolves.
            year = 1900 + int(yy)
        found.setdefault((year, nn), set()).add(ppp)
    return found


_BREAK = "\ue000"  # private-use sentinel for line breaks during HTML → text


def html_to_text(html: str) -> str:
    """HTML → text keeping inline runs intact (get_text('\\n') splits
    '<u>Decisione di principi</u>o' into two lines); block tags and <br>
    become line breaks, whitespace is collapsed per line."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.find_all(["title", "script", "style", "img", "spacer"]):
        t.decompose()
    # Only <br> and block boundaries break lines; the archive hard-wraps its
    # paragraphs with source newlines, which are plain whitespace.
    for br in soup.find_all("br"):
        br.replace_with(_BREAK)
    for tag in soup.find_all(["p", "div", "tr", "td", "th", "h1", "h2", "h3", "h4", "hr", "li", "table"]):
        tag.insert_before(_BREAK)
        tag.insert_after(_BREAK)
    text = soup.get_text("")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split(_BREAK)]
    return "\n".join(line for line in lines if line)


def _response_text(response) -> str:
    """Decode an archive page. The server sends text/html without a charset,
    so requests falls back to ISO-8859-1; the files are Windows-1252 (curly
    apostrophes, en dashes at 0x92/0x96), which latin-1 turns into C1 control
    characters. cp1252 is a superset for every printable byte."""
    enc = (getattr(response, "encoding", None) or "").lower().replace("_", "-")
    if enc in ("", "iso-8859-1", "latin-1", "latin1"):
        try:
            return response.content.decode("cp1252", errors="replace")
        except AttributeError:
            pass
    return response.text


class EMARKScraper(BaseScraper):
    """Scraper for EMARK (Swiss Asylum Appeals Commission) published decisions."""

    REQUEST_DELAY = 1.0
    TIMEOUT = 30
    MAX_ERRORS = 50
    # 1999+ enumerates year×number ranges where a 404 is a gap in the
    # numbering sequence. Cache them as known gaps so we don't re-hammer the
    # portal with the same 404s every night; ScraperState.GAP_TTL_DAYS will
    # re-probe after a week. (1993-98 is index-driven and ignores the cache.)
    CACHE_NONE_AS_GAP = True

    @property
    def court_code(self) -> str:
        return "emark"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """1999+: enumerate year/number combinations, newest first.
        1993-98: harvest the two archive indices, newest first."""
        found = 0
        for year in sorted(YEAR_RANGES.keys(), reverse=True):
            if since_date and year < since_date.year:
                continue
            if year < FIRST_ENUMERABLE_YEAR:
                continue

            max_nr = YEAR_RANGES[year]
            for nr in range(max_nr, 0, -1):
                docket = f"EMARK-{year}-{nr}"
                decision_id = make_decision_id("emark", docket)
                if self.state.is_known(decision_id):
                    continue

                url = f"{BASE_URL}/{year}/{nr:02d}.htm"
                found += 1
                yield {
                    "docket_number": docket,
                    "year": year,
                    "nr": nr,
                    "url": url,
                }

        for stub in self._discover_pre1999(since_date):
            found += 1
            yield stub

        logger.info(f"[emark] Found {found} new decisions to fetch")

    def _already_scraped(self, decision_id: str) -> bool:
        """Scraped-set membership only — the index tells us the decision
        exists, so a stale gap-cache entry (from the years the enumeration
        404'd on these ids) must not hide it."""
        seen = getattr(self.state, "_seen", None)
        if isinstance(seen, set):
            return decision_id in seen
        return self.state.is_known(decision_id)

    def _fetch_pre1999_index(self) -> dict[tuple[int, int], set[int]]:
        merged: dict[tuple[int, int], set[int]] = {}
        loaded = 0
        for url in PRE1999_INDEX_URLS:
            try:
                response = self.get(url)
            except Exception as e:
                logger.warning(f"[emark] Failed to fetch index {url}: {e}")
                continue
            loaded += 1
            for key, pages in parse_pre1999_index(_response_text(response)).items():
                merged.setdefault(key, set()).update(pages)
        if not loaded:
            logger.error("[emark] No 1993-98 index reachable; skipping pre-1999 discovery")
        else:
            logger.info(
                f"[emark] 1993-98 indices: {loaded}/{len(PRE1999_INDEX_URLS)} loaded, "
                f"{len(merged)} decisions linked"
            )
        return merged

    def _discover_pre1999(self, since_date=None) -> Iterator[dict]:
        if since_date and since_date.year >= FIRST_ENUMERABLE_YEAR:
            return
        index = self._fetch_pre1999_index()
        if not index:
            return
        for (year, nr) in sorted(index.keys(), reverse=True):
            if since_date and year < since_date.year:
                continue
            docket = f"EMARK-{year}-{nr}"
            decision_id = make_decision_id("emark", docket)
            if self._already_scraped(decision_id):
                continue
            pages = sorted(index[(year, nr)])
            yield {
                "docket_number": docket,
                "decision_id": decision_id,
                "year": year,
                "nr": nr,
                "index_pages": pages,
                "start_page": pages[0],
                "url": pre1999_page_url(year, nr, pages[0]),
            }

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _get_html(self, url: str) -> str | None:
        """GET a page; None on 404 (the archive's 'no such page')."""
        try:
            response = self.get(url)
        except Exception as e:
            if hasattr(e, "response") and getattr(e.response, "status_code", 0) == 404:
                logger.debug(f"[emark] 404 {url}")
                return None
            raise
        return _response_text(response)

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download and parse an EMARK decision (one file, or a page chain)."""
        if "index_pages" in stub:
            return self._fetch_pre1999(stub)

        url = stub["url"]
        docket = stub["docket_number"]

        try:
            html = self._get_html(url)
        except Exception as e:
            logger.warning(f"[emark] Failed to fetch {docket}: {e}")
            return None
        if html is None:
            logger.debug(f"[emark] {docket}: 404 (not published)")
            return None

        soup = BeautifulSoup(html, "html.parser")
        full_text = soup.get_text(separator="\n", strip=True)
        if not full_text or len(full_text) < 100:
            logger.debug(f"[emark] {docket}: too short ({len(full_text)} chars)")
            return None
        full_text = self.clean_text(full_text)

        lines = [l.strip() for l in full_text.split("\n") if l.strip()]
        title = self._title_from_lines(lines)

        # Build regeste from summary paragraphs (typically first few substantive lines)
        regeste_lines: list[str] = []
        capture = False
        for line in lines:
            if re.match(r"^\d+\.\s", line) and not capture:
                capture = True
            if capture:
                regeste_lines.append(line)
                if len(regeste_lines) >= 5:
                    break
        regeste = "\n".join(regeste_lines) if regeste_lines else None

        return self._build_decision(stub, url, full_text, title, regeste)

    def _fetch_pre1999(self, stub: dict) -> Decision | None:
        """Walk the printed pages of a 1993-98 decision.

        The index links only cited pages, and its bare "YYYY Nr. N" entry is
        the start page in 191 of 196 decisions but not all — so walk backwards
        from the lowest linked page until the archive 404s, then forwards.
        Pages the index lists are walked through even if one 404s (a hole in
        the archive must not truncate the decision); past the last listed page
        the first 404 ends the decision (the next decision lives under its own
        NN, so its pages are never reached).

        Only a 404 is a boundary. Any other failure (5xx, timeout, connection
        reset) aborts the decision by raising: the run loop records a fetch
        error and does NOT mark the id scraped, so the next nightly retries it.
        Returning the pages fetched so far would persist a truncated full_text
        that _already_scraped() then hides forever.
        """
        docket = stub["docket_number"]
        year, nr = stub["year"], stub["nr"]
        index_pages = sorted(stub.get("index_pages") or [stub["start_page"]])
        first_listed, last_listed = index_pages[0], index_pages[-1]

        pages: dict[int, str] = {}
        try:
            # backwards
            p = first_listed
            while p >= 1 and len(pages) < MAX_PAGES_PER_DECISION:
                html = self._get_html(pre1999_page_url(year, nr, p))
                if html is None:
                    if p == first_listed:
                        logger.warning(f"[emark] {docket}: indexed start page {p} is 404")
                    break
                pages[p] = html
                p -= 1
            # forwards
            p = first_listed + 1
            while len(pages) < MAX_PAGES_PER_DECISION:
                html = self._get_html(pre1999_page_url(year, nr, p))
                if html is None:
                    if p < last_listed:
                        logger.warning(f"[emark] {docket}: page {p} missing inside the indexed range")
                        p += 1
                        continue
                    break
                pages[p] = html
                p += 1
        except Exception as e:
            logger.warning(
                f"[emark] {docket}: aborting after {len(pages)} page(s), "
                f"page {p} failed with a non-404 error, will retry next run: {e}"
            )
            raise

        if not pages:
            logger.warning(f"[emark] {docket}: no pages retrievable")
            return None

        ordered = sorted(pages)
        page_texts = [self.clean_text(html_to_text(pages[p])) for p in ordered]
        full_text = self.clean_text("\n".join(t for t in page_texts if t))
        if len(full_text) < 100:
            logger.debug(f"[emark] {docket}: too short ({len(full_text)} chars)")
            return None

        first_lines = [l for l in page_texts[0].split("\n") if l.strip()]
        title = self._title_from_lines(first_lines)
        regeste = self._pre1999_regeste(first_lines)

        source_url = pre1999_page_url(year, nr, ordered[0])
        logger.info(f"[emark] {docket}: {len(ordered)} pages (S. {ordered[0]}-{ordered[-1]})")
        return self._build_decision(stub, source_url, full_text, title, regeste)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _title_from_lines(lines: list[str]) -> str | None:
        for line in lines[:20]:
            if re.match(r"^(Art\.|Asyl|Flüchtling|Wegweisung|Vollzug|Nichteintret)", line):
                return line[:200]
        return None

    @staticmethod
    def _pre1999_regeste(lines: list[str]) -> str | None:
        """Summary block of a pre-1999 first page: from the first "Art. …"
        head-note onwards, skipping footnote pointers and stopping at the
        first footnote body ("[1] Entscheid der Präsidentenkonferenz …"),
        which on a short first page would otherwise fill the fifth line.
        Falls back to the generic "first numbered paragraph" rule."""
        start = None
        for i, line in enumerate(lines[:20]):
            if re.match(r"^Art\.", line):
                start = i
                break
        if start is None:
            for i, line in enumerate(lines):
                if re.match(r"^\d+\.\s", line):
                    start = i
                    break
        if start is None:
            return None
        out: list[str] = []
        for line in lines[start:]:
            if _FOOTNOTE_BODY_RE.match(line):
                break
            if _FOOTNOTE_POINTER_RE.search(line) and len(line) < 60:
                continue
            out.append(line)
            if len(out) >= 5:
                break
        return "\n".join(out) if out else None

    def _build_decision(
        self, stub: dict, url: str, full_text: str, title: str | None, regeste: str | None
    ) -> Decision:
        docket = stub["docket_number"]
        decision_date = extract_decision_date(full_text, stub["year"])
        if not decision_date:
            # Fallback: use year from docket
            decision_date = date(stub["year"], 1, 1)

        return Decision(
            decision_id=make_decision_id("emark", docket),
            court="emark",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=detect_language(full_text),
            title=title,
            legal_area="Asylrecht",
            regeste=regeste,
            full_text=full_text,
            source_url=url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape EMARK decisions")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    since = date.fromisoformat(args.since) if args.since else None
    scraper = EMARKScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {d.language}")
    print(f"\nScraped {len(decisions)} EMARK decisions")
