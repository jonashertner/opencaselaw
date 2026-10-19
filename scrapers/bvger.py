"""
Bundesverwaltungsgericht (BVGer) — Federal Administrative Court Scraper
========================================================================

DUAL-MODE ARCHITECTURE (Feb 2026):

  Mode A (PRIMARY):  Weblaw LEv4 at bvger.weblaw.ch
    - Netlify-hosted SPA with serverless API functions
    - Search: POST /api/.netlify/functions/searchQueryService
      Content-Type: text/plain;charset=UTF-8 (NOT application/json)
      aggs requires valid fields: panel, language, year (minimum)
    - Full text: GET /api/.netlify/functions/singleDocQueryService/{leid}
      Returns full HTML decision content (~8-30KB)
    - PDF: via metadataKeywordTextMap.originalUrl
    - 91,000+ decisions indexed

  Mode B (FALLBACK): jurispub.admin.ch ICEfaces
    - Legacy ICEfaces (JSF) — still alive as of Feb 2026
    - Stateful: requires JSESSIONID + ICE session
    - Metadata only from listing (full text via PDF)
    - PDF downloads: /publiws/download?decisionId={uuid}

Note: bvger.weblaw.ch uses DIFFERENT URLs than bstger.weblaw.ch.
BStGer uses /api/getDocuments, BVGer uses /api/.netlify/functions/*.
The /api/getDocuments path does NOT work on bvger.weblaw.ch.

Coverage: 2007–present (~91,000+ decisions)
Rate limiting: 3 seconds
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
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

# ── Mode A: Weblaw LEv4 (Netlify Functions) ───────────────────
WEBLAW_HOST = "https://bvger.weblaw.ch"
WEBLAW_SEARCH_URL = (
    f"{WEBLAW_HOST}/api/.netlify/functions/searchQueryService"
)
WEBLAW_CONTENT_URL = (
    f"{WEBLAW_HOST}/api/.netlify/functions/singleDocQueryService"
)
WEBLAW_HEADERS = {
    "Content-Type": "text/plain;charset=UTF-8",
    "Accept": "*/*",
    "Origin": WEBLAW_HOST,
    "Referer": f"{WEBLAW_HOST}/dashboard?guiLanguage=de",
}
# Minimum working aggs fields for BVGer (validated Feb 2026)
WEBLAW_AGGS_FIELDS = ["panel", "language", "year"]

# ── Mode B: jurispub.admin.ch ICEfaces ────────────────────────
JP_HOST = "https://jurispub.admin.ch"
JP_SESSION_URL = f"{JP_HOST}/publiws/?lang=de"
JP_SUCH_URL = f"{JP_HOST}/publiws/block/send-receive-updates;jsessionid="
JP_PDF_URL = f"{JP_HOST}/publiws/download?decisionId="
JP_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "*/*",
    "Origin": JP_HOST,
    "Referer": f"{JP_HOST}/publiws/publiws/?lang=de",
}
RE_JSESSION = re.compile(r"(?<=JSESSIONID=)[0-9A-F]+(?=;)")
RE_ICE_SESSION = re.compile(r'(?<=script id=")[^:]+(?=:1:configuration-script)')
RE_TREFFERZAHL = re.compile(
    r'<span class="iceOutFrmt standard">([0-9]+(?:,[0-9]+)?) Entscheide gefunden, '
    r"zeige ([0-9]+(?:,[0-9]+)?) bis ([0-9]+(?:,[0-9]+)?)\. "
    r"Seite ([0-9]+(?:,[0-9]+)?) von ([0-9]+(?:,[0-9]+)?)\. Resultat sortiert"
)
RE_DECISION_ID = re.compile(r"decisionId=([0-9a-f-]+)")

# ── Common ────────────────────────────────────────────────────
START_YEAR = 2007
WINDOW_DAYS = 64
MAX_PER_WINDOW = 100

BVGER_ABTEILUNGEN = {
    "I": "Abteilung I (Infrastruktur, Umwelt, Abgaben, Personal)",
    "II": "Abteilung II (Wirtschaft, Wettbewerb, Bildung)",
    "III": "Abteilung III (Sozialversicherungen, Gesundheit)",
    "IV": "Abteilung IV (Asylrecht)",
    "V": "Abteilung V (Asylrecht)",
    "VI": "Abteilung VI (Ausländer- und Bürgerrecht)",
}
_PREFIX_MAP = {"A": "I", "B": "II", "C": "III", "D": "IV", "E": "V", "F": "VI"}

# Panel numeral in the Weblaw `panel` field ("Abt. II (...);;Cour II (...);;
# Corte II (...)") or a jurispub-style "Abteilung II". The trailing \b is what
# makes this correct: a plain substring test ("Abt. I" in raw) matched
# Abt. II/III/IV as Abt. I and Abt. VI as Abt. V, so every Weblaw-sourced
# decision of Abteilung II/III/IV/VI was labelled I or V (bug found
# 2026-09-17; Weblaw has been the primary mode since Feb 2026).
_RE_ABTEILUNG = re.compile(
    r"\b(?:Abt\.|Abteilung|Cour|Corte)\s*(I{1,3}|IV|VI?)\b"
)
# Only the ordinary "A-1234/2025" series carries the Abteilung in its letter.
# The published-collection series ("BVGE 2007/10", "BVGE 2014 IV/3") starts
# with "B" too and must never fall through to Abteilung II.
_RE_DOCKET_PREFIX = re.compile(r"^([A-Fa-f])-\d")


def _detect_abteilung(docket: str, raw: str | None = None) -> str | None:
    """Detect BVGer Abteilung from the raw panel string, else the docket prefix.

    The raw panel string wins when it names exactly one Abteilung. When it
    names none, or more than one (ambiguous), the docket prefix
    (A→I … F→VI) is the tie-breaker. Neither → None.
    """
    if raw:
        found: set[str] = set()
        for k, v in BVGER_ABTEILUNGEN.items():
            if v in raw:  # full canonical label, unique via its parenthetical
                found.add(k)
        found.update(_RE_ABTEILUNG.findall(raw))
        if len(found) == 1:
            return BVGER_ABTEILUNGEN[found.pop()]
    m = _RE_DOCKET_PREFIX.match(docket or "")
    if m:
        return BVGER_ABTEILUNGEN[_PREFIX_MAP[m.group(1).upper()]]
    return None


def _rand_uid() -> str:
    return "_" + "".join(
        random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(8)
    )


def _extract_docket(title_field: str) -> str:
    """
    Extract German docket from multilingual title.
    Format: 'BVGer F-3451/2025;;TAF F-3451/2025;;...'
    """
    first = title_field.split(";;")[0].strip()
    # Remove court prefix: "BVGer " -> ""
    if first.startswith("BVGer "):
        return first[6:]
    return first


def _strip_html(html_str: str) -> str:
    """Strip HTML tags and return clean text."""
    soup = BeautifulSoup(html_str, "lxml")
    return soup.get_text(separator="\n", strip=True)


class BVGerScraper(BaseScraper):
    REQUEST_DELAY = 3.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "bvger"

    # ── Main entry ────────────────────────────────────────────
    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            yield from self._discover_weblaw(since_date)
            return
        except Exception as e:
            logger.warning(f"BVGer Weblaw failed ({e}), falling back to jurispub")
        yield from self._discover_icefaces(since_date)

    def fetch_decision(self, stub: dict) -> Decision | None:
        try:
            docket = stub["docket_number"]
            dd = parse_date(stub.get("decision_date", ""))
            if not dd and stub.get("year"):
                dd = date(stub["year"], 1, 1)
            if not dd:
                logger.warning(f"[bvger] No date for {docket}")

            src = stub.get("_source", "unknown")
            leid = stub.get("leid", "")
            chamber_raw = stub.get("chamber", "")
            chamber = _detect_abteilung(docket, chamber_raw)

            # Fetch full text from Weblaw if available
            full_text = ""
            if src == "weblaw" and leid:
                full_text = self._fetch_weblaw_content(leid)

            # Fall back to stub content
            if not full_text:
                content = stub.get("content_text", "")
                title = stub.get("title", "")
                headnote = stub.get("headnote", "")
                if content:
                    full_text = content
                elif headnote:
                    full_text = f"{title}\n\n{headnote}"
                else:
                    full_text = title

            lang = stub.get("language") or (
                detect_language(full_text) if full_text.strip() else "de"
            )

            # URLs
            title = stub.get("title", "")
            pdf_url = stub.get("pdf_url")
            doc_id = stub.get("doc_id", "")

            if src == "weblaw" and leid:
                source_url = f"{WEBLAW_HOST}/cache?id={leid}&guiLanguage={lang}"
            else:
                source_url = stub.get("source_url", JP_SESSION_URL)

            if not pdf_url:
                if leid and stub.get("original_url"):
                    pdf_url = f"{WEBLAW_HOST}{stub['original_url']}"
                elif doc_id:
                    pdf_url = f"{JP_PDF_URL}{doc_id}"

            return Decision(
                decision_id=make_decision_id("bvger", docket),
                court="bvger",
                canton="CH",
                chamber=chamber,
                docket_number=docket,
                decision_date=dd,
                publication_date=parse_date(stub.get("publication_date", "")),
                language=lang,
                title=title or None,
                regeste=stub.get("subject") or stub.get("headnote") or None,
                full_text=(
                    self.clean_text(full_text)
                    if full_text.strip()
                    else "(metadata only — PDF available)"
                ),
                decision_type=stub.get("ruling_type"),
                source_url=source_url,
                pdf_url=pdf_url,
                cited_decisions=extract_citations(full_text) if full_text else [],
                scraped_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(
                f"BVGer fetch error {stub.get('docket_number', '?')}: {e}",
                exc_info=True,
            )
            return None

    # ══════════════════════════════════════════════════════════
    # Mode A: Weblaw LEv4 (searchQueryService)
    # ══════════════════════════════════════════════════════════

    def _discover_weblaw(self, since_date=None) -> Iterator[dict]:
        floor = date(START_YEAR, 1, 1)
        if since_date:
            if isinstance(since_date, str):
                since_date = parse_date(since_date) or floor
            floor = max(since_date, floor)

        wd = WINDOW_DAYS
        today = date.today()
        total_found = 0
        consecutive_empty_days = 0

        # Scan backwards from today — finds new decisions quickly for daily
        # runs, and stops early when all historical decisions are known.
        cur_end = today
        while cur_end >= floor:
            cur_start = max(cur_end - timedelta(days=wd - 1), floor)
            docs, total, more = self._wl_search(cur_start, cur_end)

            if total > MAX_PER_WINDOW and wd > 1:
                wd = max(1, wd // 2)
                logger.info(
                    f"BVGer Weblaw {cur_start}–{cur_end}: {total} results, "
                    f"narrowing to {wd}d window"
                )
                continue

            # API caps at ~101 results per query. If a single day exceeds
            # that, split by language to get all decisions.
            if total > MAX_PER_WINDOW and wd == 1:
                logger.warning(
                    f"BVGer Weblaw {cur_start}: {total} results on one day, "
                    f"splitting by language"
                )
                new_in_window = 0
                for lang in ("de", "fr", "it"):
                    yield from self._yield_window(
                        cur_start, cur_end, total_found, new_in_window,
                        language=lang,
                    )
                cur_end = cur_start - timedelta(days=1)
                continue

            logger.info(f"BVGer Weblaw {cur_start}–{cur_end}: {total} decisions")

            new_in_window = 0
            for doc in docs:
                stub = self._wl_parse(doc)
                if stub and not self.state.is_known(
                    make_decision_id("bvger", stub["docket_number"])
                ):
                    yield stub
                    total_found += 1
                    new_in_window += 1

            # Pagination: fetch remaining pages
            offset = len(docs)
            while more and offset < total:
                docs, _, more = self._wl_search(cur_start, cur_end, offset)
                for doc in docs:
                    stub = self._wl_parse(doc)
                    if stub and not self.state.is_known(
                        make_decision_id("bvger", stub["docket_number"])
                    ):
                        yield stub
                        total_found += 1
                        new_in_window += 1
                offset += len(docs)

            # Early exit: stop scanning history once we've passed 2 years
            # of consecutive windows with no new (unknown) decisions.
            window_span = (cur_end - cur_start).days + 1
            if new_in_window == 0:
                consecutive_empty_days += window_span
                if consecutive_empty_days >= 730:
                    logger.info(
                        f"BVGer Weblaw: {consecutive_empty_days} consecutive "
                        f"empty days (back to {cur_start}), stopping early"
                    )
                    break
            else:
                consecutive_empty_days = 0

            cur_end = cur_start - timedelta(days=1)

        logger.info(f"BVGer Weblaw discovery complete: {total_found} new stubs")

    def _yield_window(
        self, ab: date, bis: date,
        total_found: int, new_in_window: int,
        language: str | None = None,
    ) -> Iterator[dict]:
        """Yield all stubs from a search window, handling pagination."""
        docs, total, more = self._wl_search(ab, bis, language=language)
        if total == 0:
            return

        label = f"{ab}–{bis}" if ab != bis else str(ab)
        if language:
            label += f" [{language}]"
        logger.info(f"BVGer Weblaw {label}: {total} decisions")

        for doc in docs:
            stub = self._wl_parse(doc)
            if stub and not self.state.is_known(
                make_decision_id("bvger", stub["docket_number"])
            ):
                yield stub

        offset = len(docs)
        while more and offset < total:
            docs, _, more = self._wl_search(ab, bis, offset, language=language)
            for doc in docs:
                stub = self._wl_parse(doc)
                if stub and not self.state.is_known(
                    make_decision_id("bvger", stub["docket_number"])
                ):
                    yield stub
            offset += len(docs)

    def _wl_search(
        self, ab: date, bis: date, offset: int = 0,
        language: str | None = None,
    ) -> tuple[list[dict], int, bool]:
        """Execute a Weblaw search query. Returns (documents, total, has_more)."""
        body = {
            "guiLanguage": "de",
            "userID": _rand_uid(),
            "sessionDuration": int(time.time()) % 10000,
            "size": 100,
            "aggs": {"fields": WEBLAW_AGGS_FIELDS, "size": "10"},
            "metadataDateMap": {
                "rulingDate": {
                    "from": ab.strftime("%Y-%m-%dT00:00:00.000Z"),
                    "to": bis.strftime("%Y-%m-%dT23:59:59.999Z"),
                }
            },
        }
        if offset > 0:
            body["from"] = offset
        if language:
            body["metadataKeywordMap"] = {"language": [language]}

        import json

        resp = self.post(
            WEBLAW_SEARCH_URL,
            headers=WEBLAW_HEADERS,
            data=json.dumps(body),
        )

        data = resp.json()
        if "totalNumberOfDocuments" not in data:
            logger.error(f"BVGer Weblaw unexpected response: {str(data)[:500]}")
            return [], 0, False

        return (
            data.get("documents", []),
            data.get("totalNumberOfDocuments", 0),
            data.get("hasMoreResults", False),
        )

    def _wl_parse(self, doc: dict) -> dict | None:
        """Parse a Weblaw search result document into a stub dict."""
        try:
            kw = doc.get("metadataKeywordTextMap", {})
            dt = doc.get("metadataDateMap", {})
            leid = doc.get("leid", "")

            titles = kw.get("title", [])
            if not titles:
                return None

            docket = _extract_docket(titles[0])
            if not docket:
                return None

            # Language: first value from keyword list
            lang_list = kw.get("language", [])
            lang = lang_list[0] if lang_list else None

            # Panel / Abteilung
            panel_list = kw.get("panel", [])
            panel = panel_list[0] if panel_list else ""

            # Ruling type
            rt_list = kw.get("rulingType", [])
            ruling_type = rt_list[0].split(";;")[0] if rt_list else None

            # Original PDF URL
            orig_list = kw.get("originalUrl", [])
            original_url = orig_list[0] if orig_list else None

            # Content snippet (HTML)
            content_html = doc.get("content", "")
            content_text = _strip_html(content_html) if content_html else ""

            # Extract subject from content
            subject = ""
            if content_html:
                soup = BeautifulSoup(content_html, "lxml")
                for b in soup.find_all("b"):
                    if "Sachgebiet" in b.get_text() or "Domaine" in b.get_text():
                        nxt = b.next_sibling
                        if nxt:
                            subject = str(nxt).strip().lstrip(":").strip()
                            break

            return {
                "docket_number": docket,
                "decision_date": (
                    dt.get("rulingDate", "")[:10] if "rulingDate" in dt else None
                ),
                "publication_date": (
                    dt.get("publicationDate", "")[:10]
                    if "publicationDate" in dt
                    else None
                ),
                "title": docket,
                "chamber": panel,
                "language": lang,
                "ruling_type": ruling_type,
                "subject": subject,
                "content_text": content_text,
                "leid": leid,
                "original_url": original_url,
                "pdf_url": (
                    f"{WEBLAW_HOST}{original_url}" if original_url else None
                ),
                "_source": "weblaw",
            }
        except Exception as e:
            logger.error(f"Weblaw parse error: {e}")
            return None

    def _fetch_weblaw_content(self, leid: str) -> str:
        """Fetch full decision text from singleDocQueryService."""
        try:
            url = (
                f"{WEBLAW_CONTENT_URL}/{leid}"
                f"?guiLanguage=de&userID={_rand_uid()}"
                f"&sessionDuration={int(time.time()) % 10000}"
            )
            resp = self.get(url)
            data = resp.json()
            content_html = data.get("content", "")
            if content_html and len(content_html) > 100:
                return _strip_html(content_html)
        except Exception as e:
            logger.debug(f"Weblaw content fetch failed for {leid}: {e}")
        return ""

    # ══════════════════════════════════════════════════════════
    # Mode B: jurispub.admin.ch ICEfaces (fallback)
    # ══════════════════════════════════════════════════════════

    def _discover_icefaces(self, since_date=None) -> Iterator[dict]:
        cy = date.today().year
        sy = START_YEAR
        if since_date:
            if isinstance(since_date, str):
                since_date = parse_date(since_date) or date(sy, 1, 1)
            sy = max(since_date.year, sy)

        for year in range(cy, sy - 1, -1):
            sess = self._ice_session(year)
            if not sess:
                continue
            js, ice = sess
            html = self._ice_search(js, ice, f"01.01.{year}", f"31.12.{year}")
            if not html:
                continue

            tz = RE_TREFFERZAHL.search(html)
            if not tz:
                if "Kein Suchtreffer" in html:
                    logger.info(f"No results {year}")
                else:
                    logger.warning(f"Cannot parse hit count {year}")
                continue

            total = int(tz.group(1).replace(",", ""))
            pages = int(tz.group(5).replace(",", ""))
            logger.info(f"BVGer ICEfaces {year}: {total} decisions, {pages} pages")

            for s in self._ice_parse(html):
                s["year"] = year
                s["_source"] = "icefaces"
                did = make_decision_id("bvger", s["docket_number"])
                if not self.state.is_known(did):
                    yield s

            if pages > 1:
                logger.warning(
                    f"BVGer {year}: {pages} pages, only page 1 scraped"
                )

    def _ice_session(self, year: int) -> tuple[str, str] | None:
        try:
            r = self.get(f"{JP_SESSION_URL}&{year}")
            if r.status_code != 200 or len(r.content) < 148:
                return None
            m = RE_ICE_SESSION.search(r.text)
            if not m:
                return None
            ice = m.group(0)
            js = None
            for c in r.cookies:
                if c.name == "JSESSIONID":
                    js = c.value
                    break
            if not js:
                for h in r.headers.get("Set-Cookie", "").split(","):
                    m2 = RE_JSESSION.search(h)
                    if m2:
                        js = m2.group(0)
                        break
            if not js:
                return None
            return (js, ice)
        except Exception as e:
            logger.error(f"ICEfaces session error {year}: {e}")
            return None

    def _ice_search(self, js: str, ice: str, ab: str, bis: str) -> str | None:
        rand = f"0.{random.randint(10**15, 10**16 - 1)}"
        url = f"{JP_SUCH_URL}{js}"
        b1 = (
            f"ice.submit.partial=true&ice.event.target=form%3AcalFrom&"
            f"ice.event.captured=form%3AcalFrom&ice.event.type=onblur&"
            f"form%3A_idform%3AcalTosp=&form%3A_idform%3AcalFromsp=&"
            f"form%3A_idcl=&form%3Aform%3Atree_idtn=&form%3Aform%3Atree_idta=&"
            f"form%3AcalTo={bis}&form%3AcalFrom={ab}&form%3AsearchQuery=&"
            f"javax.faces.RenderKitId=&javax.faces.ViewState=1&"
            f"icefacesCssUpdates=&form=&"
            f"ice.session={ice}&ice.view=1&ice.focus=&rand={rand}"
        )
        try:
            self.post(url, data=b1, headers=JP_HEADERS)
        except Exception:
            return None

        rand2 = f"0.{random.randint(10**15, 10**16 - 1)}"
        b2 = (
            f"ice.submit.partial=true&ice.event.target=form%3AsearchSubmitButton&"
            f"ice.event.captured=form%3AsearchSubmitButton&ice.event.type=onclick&"
            f"ice.event.alt=false&ice.event.ctrl=false&ice.event.shift=false&"
            f"ice.event.meta=false&ice.event.x=72&ice.event.y=252&"
            f"ice.event.left=false&ice.event.right=false&"
            f"form%3A_idform%3AcalTosp=&form%3A_idform%3AcalFromsp=&"
            f"form%3A_idcl=&form%3Aform%3Atree_idtn=&form%3Aform%3Atree_idta=&"
            f"form%3AcalTo={bis}&form%3AcalFrom={ab}&form%3AsearchQuery=&"
            f"javax.faces.RenderKitId=&javax.faces.ViewState=1&"
            f"icefacesCssUpdates=&form=&form%3AsearchSubmitButton=suchen&"
            f"ice.session={ice}&ice.view=1&"
            f"ice.focus=form%3AsearchSubmitButton&rand={rand2}"
        )
        try:
            r = self.post(url, data=b2, headers=JP_HEADERS)
            return r.text
        except Exception:
            return None

    def _ice_parse(self, html: str) -> list[dict]:
        """
        Parse ICEfaces result table using BeautifulSoup.

        Table columns:
          0: Docket number (link)
          1: PDF icon (link with decisionId)
          2: Decision date (span)
          3+: Chamber, title, headnotes (spans)
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = []

        rows = soup.select("tr.iceDatTblRow1, tr.iceDatTblRow2")
        if not rows:
            table = soup.find(
                lambda tag: tag.name == "table"
                and "resultTable" in tag.get("id", "")
            )
            if table:
                rows = table.find_all("tr")

        for tr in rows:
            cells = tr.find_all("td")
            if len(cells) < 3:
                continue

            # Col 0: docket number
            link = cells[0].find("a")
            if not link:
                continue
            docket = link.get_text(strip=True)
            if not docket or not re.match(r"[A-Z]-\d+/\d+", docket):
                continue

            # Col 1: PDF link
            pdf_link = cells[1].find("a")
            pdf_url = ""
            doc_id = ""
            if pdf_link and pdf_link.get("href"):
                href = pdf_link["href"]
                pdf_url = f"{JP_HOST}{href}" if href.startswith("/") else href
                m = RE_DECISION_ID.search(href)
                if m:
                    doc_id = m.group(1)

            # Col 2: decision date
            decision_date = ""
            if len(cells) > 2:
                span = cells[2].find("span")
                if span:
                    decision_date = span.get_text(strip=True)

            # Col 3: chamber
            chamber = ""
            if len(cells) > 3:
                span = cells[3].find("span")
                if span:
                    chamber = span.get_text(strip=True)

            # Col 4: title
            title = ""
            if len(cells) > 4:
                span = cells[4].find("span")
                if span:
                    title = span.get_text(strip=True)

            # Col 5-6: headnotes
            headnote_short = ""
            headnote = ""
            if len(cells) > 5:
                span = cells[5].find("span")
                if span:
                    headnote_short = span.get_text(strip=True)
            if len(cells) > 6:
                span = cells[6].find("span")
                if span:
                    headnote = span.get_text(strip=True)

            stubs.append({
                "docket_number": docket,
                "decision_date": decision_date,
                "chamber": chamber,
                "title": title,
                "headnote_short": headnote_short,
                "headnote": headnote,
                "pdf_url": pdf_url,
                "doc_id": doc_id,
                "source_url": JP_SESSION_URL,
            })

        logger.info(f"ICEfaces parsed {len(stubs)} decisions from HTML")
        return stubs


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape BVGer decisions")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD or year")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    since = None
    if args.since:
        since = (
            date(int(args.since), 1, 1)
            if len(args.since) == 4
            else date.fromisoformat(args.since)
        )
    scraper = BVGerScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    print(f"Scraped {len(decisions)} BVGer decisions")
    for d in decisions[:5]:
        print(
            f"  {d.decision_id} ({d.decision_date}) [{d.language}] "
            f"{len(d.full_text)} chars"
        )