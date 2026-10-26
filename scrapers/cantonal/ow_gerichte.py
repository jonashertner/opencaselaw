"""
Obwalden Courts Scraper (OW Gerichte)
======================================
Scrapes court decisions from the Weblaw Vaadin portal at rechtsprechung.ow.ch.

Platform: Weblaw Vaadin LEv3 (server-side Java UI framework)
Coverage: Obergericht / Verwaltungsgericht Obwalden (OGVE series)
Volume: ~2,205 decisions (1976-present)
Language: de

Architecture:
1. POST init with browser details -> JSON with Vaadin-Security-Key
2. POST UIDL click on search button -> paginated result list
3. Parse hierarchy/state from UIDL response -> docket, abstract, cache URL
4. POST UIDL layoutClick on "weiter" element -> next page
5. Playwright with injected JSESSIONID visits cache URL -> full text

The cache viewer (rechtsprechung.ow.ch/le/cache/) requires an authuser token
that matches the session's JSESSIONID. We use requests for fast UIDL discovery,
then inject the JSESSIONID cookie into a Playwright browser context to render
the Vaadin cache viewer and extract full decision text.

Source: https://rechtsprechung.ow.ch
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import date
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

# ============================================================
# Constants
# ============================================================

HOST = "https://rechtsprechung.ow.ch"
INIT_URL = HOST + "/le/"
UIDL_URL = HOST + "/le/UIDL/?v-uiId=0"
REFERER = HOST + "/le/"

HEADERS_UIDL = {
    "Content-Type": "text/plain;charset=utf-8",
    "Referer": REFERER,
}

# Regex for docket number extraction:
#   "A 01/0001 (A 01/0002)" -> vor="A 01/0001", in="A 01/0002"
#   "A 01/0001"             -> vor="A 01/0001", in=None
RE_DOCKET = re.compile(
    r"^\s*(?P<vor>[^\s(<][^(<]*[^\s(<])\s*(?:\((?P<in>[^)]+)\)\s*)?$"
)

# Regex for result count: "Resultat 1-20 von 2205"
RE_TREFFER = re.compile(r"Resultat\s+(\d+)-(\d+)\s+von\s+(\d+)")

# UIDL response prefix that must be stripped before JSON parsing
UIDL_PREFIX = "for(;;);"

# The line that dates an AbR / VVGE / OGVE entry, right after its Regeste:
#   "Entscheid der Obergerichtskommisson vom 18. Juli 2001"
#   "Urteil des Obergerichts vom 27. März/3. April 1980"
#   "Präsidialverfügung vom 5. Oktober 1982"
#   "Entscheid vom 17.12.1968 i.S. Rekurs A. A. gegen Gemeinderat Engelberg."
_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_RULING_LINE_RE = re.compile(
    r"^\s*(?:Entscheide?|Urteile?|Beschlüsse|Beschluss|Verfügung(?:en)?|Präsidialverfügung"
    r"|Präsidialentscheid|Zwischenentscheid|Teilurteil)\b[^\n]{0,160}?\bvom\s+"
    r"(?:(?P<span>\d{1,2}\.(?:\s*(?:" + _MONTH_ALT + r"))?\s*/\s*)?"
    r"(?P<d>\d{1,2})\.\s*(?P<m>" + _MONTH_ALT + r")\s+(?P<y>\d{4})"
    r"|(?P<nd>\d{1,2})\.(?P<nm>\d{1,2})\.(?P<ny>\d{4}))(?!\d)",
    re.I,
)
# Where the Regeste block ends and the reasons begin.
_BODY_START_RE = re.compile(
    r"^\s*(?:Sachverhalt|Aus dem Sachverhalt|Aus den Erwägungen|(?:In )?Erwägung(?:en)?|de\|)\s*:?\s*$",
    re.I,
)
# Any short line dating the entry closes the Regeste block, a ruling's or not:
# "Stellungnahme des Rechtsdienstes vom 3. Juni 1993." (not "G. beantragt in
# seiner Vernehmlassung vom ...", a sentence of the facts).
_DATING_LINE_RE = re.compile(
    r"^\s*[A-ZÄÖÜ][\w-]+\s+(?:de[rs]|an)\s[^\n(]{0,90}\bvom\s+(?:(?:\d{1,2}\.(?:\s*(?:" + _MONTH_ALT + r"))?\s*/\s*)?"
    r"\d{1,2}\.\s*(?:" + _MONTH_ALT + r")\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4})(?!\d)[^\n]{0,60}$",
    re.I,
)
_REGESTE_MAX = 3000
_VOLUME_RE = re.compile(r"\b((?:18|19|20)\d{2})\s*/\s*(\d{2})\b")


def _volume_years(docket: str | None) -> tuple[int, int] | None:
    """(first, last) year of the yearbook a docket sits in: "AbR 2000/01" -> (2000, 2001)."""
    m = _VOLUME_RE.search(docket or "")
    if not m:
        return None
    first = int(m.group(1))
    last = first // 100 * 100 + int(m.group(2))
    if last < first:
        last += 100
    return first, last


def ruling_date(text: str | None, docket: str | None = None) -> tuple[str, date | None]:
    """(outcome, date) of the ruling an OW yearbook entry reports.

    The portal dates every entry imported in 2015 with 26 November 2015, and
    the first "Beschluss vom <date>" of the text is often a date from the
    Sachverhalt: AbR 2000/01 Nr. 4 of 18 July 2001 was cited as of
    8 February 1999, the day of a Gemeinderat resolution it recounts
    (report 2026-10-26). The entry's own line names the ruling body and its
    date between the Regeste and the reasons; only that line counts.

    Outcomes: "ok"; "no_line"; "split_date" for "vom 25. November/
    22. Dezember 2003" (deliberation and dispatch, left to the reader);
    "implausible_year" when the year falls outside the yearbook (the printed
    "VVGE 1987/88 Nr. 30 ... vom 2. März 1887" is a misprint)."""
    if not text:
        return "no_line", None
    for line in text.split("\n")[:15]:
        if _BODY_START_RE.match(line):
            break
        m = _RULING_LINE_RE.match(line)
        if not m:
            continue
        if m.group("span"):
            return "split_date", None
        try:
            if m.group("y"):
                d = date(int(m.group("y")), _MONTHS[m.group("m").lower()], int(m.group("d")))
            else:
                d = date(int(m.group("ny")), int(m.group("nm")), int(m.group("nd")))
        except (ValueError, KeyError):
            return "no_line", None
        years = _volume_years(docket)
        if d > date.today() or (years and not years[0] - 10 <= d.year <= years[1] + 2):
            return "implausible_year", None
        return "ok", d
    return "no_line", None


def full_regeste(text: str | None, regeste: str | None) -> str | None:
    """The whole Regeste block of the text where ``regeste`` is cut short.

    The portal's abstract, which the scraper stores as regeste, stops at
    ~240 characters (AbR 2000/01 Nr. 4: "... vom 18. Juli 2"). The text
    opens with the same block in full: from the first line up to the line
    dating the entry (included), before the reasons begin. Returns
    None unless the stored regeste is a prefix of that block, the block is
    longer, and it stays within a Regeste's length."""
    if not text or not regeste:
        return None
    block: list[str] = []
    for line in text.split("\n")[:15]:
        if _BODY_START_RE.match(line):
            # Reasons begin with no line dating the entry: the older VVGE
            # run Regeste and facts together, so the block's end is unknown.
            return None
        block.append(line)
        if _RULING_LINE_RE.match(line) or _DATING_LINE_RE.match(line.strip()):
            break
    else:
        return None  # no end of the block in sight: not a yearbook entry
    composed = re.sub(r"\s+", " ", " ".join(block)).strip()
    current = re.sub(r"\s+", " ", regeste).strip()
    if (len(composed) <= len(current) or len(composed) > _REGESTE_MAX
            or not composed.startswith(current)):
        return None
    return composed

# Vaadin UIDL binary message fragments
_RECORD_SEP = b"\x1d"

_SEARCH_BODY_TPL = (
    b'[["0","com.vaadin.shared.ui.ui.UIServerRpc","resize",'
    b'["793","1429","1429","793"]],["'
    b"{searchbutton}"
    b'","com.vaadin.shared.ui.button.ButtonServerRpc","click",'
    b'[{"metaKey":false,"altKey":false,"shiftKey":false,"ctrlKey":false,'
    b'"relativeX":"10","clientX":"728","relativeY":"17","clientY":"47",'
    b'"button":"LEFT","type":"1"}]]]'
)

_NEXT_BODY_TPL = (
    b'[["0","com.vaadin.shared.ui.ui.UIServerRpc","scroll",'
    b'["535","0"]],["'
    b"{weiterkey}"
    b'","com.vaadin.shared.ui.orderedlayout.'
    b'AbstractOrderedLayoutServerRpc","layoutClick",'
    b'[{"metaKey":false,"altKey":false,"shiftKey":false,"ctrlKey":false,'
    b'"relativeX":"66","clientX":"284","relativeY":"13","clientY":"716",'
    b'"button":"LEFT","type":"8"},null]]]'
)


def _strip_uidl_prefix(text: str) -> str:
    """Strip the Vaadin 'for(;;);' prefix and surrounding brackets from UIDL response."""
    if text.startswith("for(;;);"):
        text = text[len("for(;;);"):]
    return text


def _parse_uidl_json(text: str) -> dict:
    """Parse a UIDL response into a dict, handling the for(;;); prefix and list wrapper."""
    raw = _strip_uidl_prefix(text)
    data = json.loads(raw)
    if isinstance(data, list):
        return data[0] if data else {}
    return data


def _build_init_url() -> str:
    """Build the Vaadin session init URL with browser detail parameters."""
    millis = int(time.time() * 1000)
    rand16 = str(random.randint(10**15, 10**16 - 1))
    params = (
        f"v-browserDetails=1"
        f"&theme=le3themeAR"
        f"&v-sh=900&v-sw=1440&v-cw=1439&v-ch=793"
        f"&v-curdate={millis}"
        f"&v-tzo=-60&v-dstd=60&v-rtzo=-60&v-dston=false"
        f"&v-vw=1439&v-vh=0"
        f"&v-loc={INIT_URL}"
        f"&v-wn=le-{millis}-0.{rand16}"
        f"&v-{millis}="
    )
    return f"{INIT_URL}?{params}"


# ============================================================
# Scraper
# ============================================================


class OWGerichteScraper(BaseScraper):
    """
    Scraper for Obwalden court decisions via Weblaw Vaadin UIDL protocol.

    Implements the full Vaadin server-side RPC flow:
    1. Initialize session with browser details POST
    2. Click search button via UIDL RPC
    3. Parse results from UIDL hierarchy/state structure
    4. Paginate via layoutClick on "weiter" element
    5. Fetch full HTML text from .htm URLs

    Total: ~2,205 decisions.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 45
    MAX_ERRORS = 30

    @property
    def court_code(self):
        return "ow_gerichte"

    # ----------------------------------------------------------
    # Vaadin session management
    # ----------------------------------------------------------

    def _init_vaadin_session(self) -> dict | None:
        """
        Initialize a Vaadin session.

        POSTs to the init URL with browser detail parameters.
        Returns dict with:
          - vk: Vaadin-Security-Key
          - searchbutton: key ID of the search button component
          - contentid: key ID of the content container component
        Or None on failure.
        """
        url = _build_init_url()
        try:
            self._rate_limit()
            resp = self.session.post(
                url,
                headers={"Content-Type": "text/plain;charset=utf-8", "Referer": REFERER},
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"[ow_gerichte] Vaadin init request failed: {e}")
            return None

        try:
            outer = json.loads(resp.text)
            uidl = json.loads(outer["uidl"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"[ow_gerichte] Vaadin init parse failed: {e}")
            return None

        vk = uidl.get("Vaadin-Security-Key")
        if not vk:
            logger.error("[ow_gerichte] No Vaadin-Security-Key in init response")
            return None

        state = uidl.get("state", {})

        # Find search button: the key whose state contains clickShortcutKeyCode
        searchbutton = None
        for k, v in state.items():
            if isinstance(v, dict) and "clickShortcutKeyCode" in v:
                searchbutton = k
                break

        # Find content container: key with spacing:true and width:"100.0%"
        contentid = None
        for k, v in state.items():
            if isinstance(v, dict) and v.get("spacing") is True and v.get("width") == "100.0%":
                contentid = k
                break

        if not searchbutton:
            logger.error("[ow_gerichte] Could not find search button in Vaadin state")
            return None

        logger.info(
            f"[ow_gerichte] Vaadin session initialized: "
            f"searchbutton={searchbutton}, contentid={contentid}"
        )

        return {"vk": vk, "searchbutton": searchbutton, "contentid": contentid}

    # ----------------------------------------------------------
    # UIDL request helpers
    # ----------------------------------------------------------

    def _uidl_post(self, body: bytes) -> dict | None:
        """
        POST a UIDL request and parse the response.

        Returns parsed JSON dict or None on failure.
        """
        try:
            self._rate_limit()
            resp = self.session.post(
                UIDL_URL,
                data=body,
                headers=HEADERS_UIDL,
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            return _parse_uidl_json(resp.text)
        except Exception as e:
            logger.error(f"[ow_gerichte] UIDL POST failed: {e}")
            return None

    def _build_search_body(self, sess: dict) -> bytes:
        """Build the UIDL body for clicking the search button."""
        payload = _SEARCH_BODY_TPL.replace(b"{searchbutton}", str(sess["searchbutton"]).encode())
        return sess["vk"].encode() + _RECORD_SEP + payload

    def _build_next_body(self, sess: dict, weiterkey: str) -> bytes:
        """Build the UIDL body for clicking the 'weiter' (next page) element."""
        payload = _NEXT_BODY_TPL.replace(b"{weiterkey}", str(weiterkey).encode())
        return sess["vk"].encode() + _RECORD_SEP + payload

    # ----------------------------------------------------------
    # Result parsing
    # ----------------------------------------------------------

    def _parse_results(self, data: dict, contentid: str | None) -> tuple[int, list[dict]]:
        """
        Parse the UIDL response to extract decision stubs.

        Returns (total_count, list_of_stubs).

        The UIDL response contains:
        - state: dict mapping component IDs to their state (text, resources, etc.)
        - hierarchy: dict mapping parent IDs to lists of child IDs

        For the results:
        - hierarchy[contentid] gives the list of member IDs
        - Each member (except first and last which are header/footer) is a decision
        - For each decision member, its children are accessed via hierarchy[member_id]:
            - child[1] state.text = docket number
            - child[0] -> hierarchy[child0][0] state.text = abstract/regeste
            - child[3] -> hierarchy[child3][1] -> hierarchy[child31][0] state.resources = HTML URL
        """
        state = data.get("state", {})
        hierarchy = data.get("hierarchy", {})

        # Extract total count from "Resultat X-Y von Z" text
        total = 0
        for v in state.values():
            if isinstance(v, dict) and "text" in v:
                m = RE_TREFFER.search(str(v["text"]))
                if m:
                    total = int(m.group(3))
                    break

        stubs = []

        if not contentid or str(contentid) not in hierarchy:
            # Try to find contentid in the current response hierarchy
            # Look for a key whose children list is large (the result container)
            for k, children in hierarchy.items():
                if isinstance(children, list) and len(children) > 3:
                    # Candidate container — check if first child has "Resultat" text
                    first_child = str(children[0]) if children else None
                    if first_child and first_child in state:
                        child_state = state[first_child]
                        if isinstance(child_state, dict) and "text" in child_state:
                            if RE_TREFFER.search(str(child_state["text"])):
                                contentid = k
                                break

        if not contentid or str(contentid) not in hierarchy:
            logger.warning("[ow_gerichte] Could not find content container in hierarchy")
            return total, stubs

        members = hierarchy[str(contentid)]
        if len(members) <= 2:
            return total, stubs

        # Skip first member (header with result count) and last member (pagination)
        decision_members = members[1:-1]

        for mid in decision_members:
            mid_str = str(mid)
            stub = self._parse_decision_member(mid_str, state, hierarchy)
            if stub:
                stubs.append(stub)

        return total, stubs

    def _parse_decision_member(
        self, mid: str, state: dict, hierarchy: dict
    ) -> dict | None:
        """
        Parse a single decision member from the UIDL hierarchy.

        Member children layout (verified from server response):
        - child[0]: container with abstract text
            - child[0][0]: text node with abstract/regeste
        - child[1]: docket number as HTML anchor link
        - child[2]: result number ("1.", "2.", etc.)
        - child[3]: container with original document link
            - child[3][1]: container
                - child[3][1][0]: link with resources.href.uRL = download path
        - child[4]: metadata fields container
            - child[4][N]: field containers with description/text
              (date, type, language, etc.)
        """
        children = hierarchy.get(mid, [])
        if len(children) < 2:
            return None

        # Extract docket number and cache URL from child[1]
        # Contains: <a href="http://...ow.ch:80/le/cache/?leid=...&authuser=..."> docket </a>
        docket_raw = ""
        cache_url = ""
        if len(children) > 1:
            child1_state = state.get(str(children[1]), {})
            if isinstance(child1_state, dict):
                raw_html = child1_state.get("text", "")
                if "<" in raw_html:
                    soup = BeautifulSoup(raw_html, "html.parser")
                    link = soup.find("a")
                    if link:
                        cache_url = link.get("href", "")
                        # Fix HTTP to HTTPS
                        cache_url = cache_url.replace(
                            "http://rechtsprechung.ow.ch:80", HOST
                        )
                    docket_raw = soup.get_text(strip=True)
                else:
                    docket_raw = raw_html

        if not docket_raw:
            return None

        # Parse docket: extract main number before parentheses
        docket_match = RE_DOCKET.match(docket_raw)
        if docket_match:
            docket = docket_match.group("vor").strip()
            docket_2 = docket_match.group("in")
            if docket_2:
                docket_2 = docket_2.strip()
        else:
            docket = docket_raw.strip()
            docket_2 = None

        # Extract abstract from child[0] -> child[0][0]
        abstract = ""
        if children:
            child0_children = hierarchy.get(str(children[0]), [])
            if child0_children:
                child00_state = state.get(str(child0_children[0]), {})
                if isinstance(child00_state, dict):
                    abstract = child00_state.get("text", "")
                    if "<" in abstract:
                        abstract = BeautifulSoup(abstract, "html.parser").get_text(
                            separator="\n", strip=True
                        )

        # Extract HTML download path from child[3][1][0] resources
        html_path = ""
        if len(children) > 3:
            child3_children = hierarchy.get(str(children[3]), [])
            if len(child3_children) > 1:
                child31_children = hierarchy.get(str(child3_children[1]), [])
                if child31_children:
                    link_state = state.get(str(child31_children[0]), {})
                    if isinstance(link_state, dict):
                        resources = link_state.get("resources", {})
                        href_res = resources.get("href", {})
                        if isinstance(href_res, dict):
                            html_path = href_res.get("uRL", "")
                        elif isinstance(href_res, str):
                            html_path = href_res

        # Extract metadata from child[4] descendants (date, language, etc.)
        decision_date = None
        language = None
        if len(children) > 4:
            meta_nodes = self._collect_descendants(str(children[4]), hierarchy)
            for node_id in meta_nodes:
                ns = state.get(node_id, {})
                if not isinstance(ns, dict):
                    continue
                desc = ns.get("description", "")
                text = ns.get("text", "")
                if "Datum" in desc and text:
                    dm = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
                    if dm:
                        decision_date = parse_date(dm.group(0))
                elif "Dokumentsprache" in desc and text:
                    language = text.strip()

        # Fallback: try to extract date from abstract
        if not decision_date and abstract:
            dm = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", abstract)
            if dm:
                decision_date = parse_date(dm.group(0))

        decision_id = make_decision_id("ow_gerichte", docket)

        return {
            "decision_id": decision_id,
            "docket_number": docket,
            "docket_number_2": docket_2,
            "decision_date": decision_date,
            "abstract": abstract.strip() if abstract else None,
            "html_path": html_path,
            "cache_url": cache_url,
            "language": language or "de",
            "url": (HOST + html_path) if html_path else HOST,
        }

    @staticmethod
    def _collect_descendants(node_id: str, hierarchy: dict) -> list[str]:
        """Collect all descendant node IDs (BFS)."""
        result = [node_id]
        queue = [node_id]
        while queue:
            nid = queue.pop(0)
            for child in hierarchy.get(nid, []):
                cs = str(child)
                result.append(cs)
                queue.append(cs)
        return result

    def _find_weiter_key(self, data: dict, contentid: str | None) -> str | None:
        """
        Find the 'weiter' (next page) key in the UIDL response.

        The weiter key is the highest-numbered key in the last member's
        children that has 'registeredEventListeners' in its state.
        """
        state = data.get("state", {})
        hierarchy = data.get("hierarchy", {})

        if not contentid or str(contentid) not in hierarchy:
            # Fallback: find any key with registeredEventListeners
            candidates = [
                k for k, v in state.items()
                if isinstance(v, dict) and "registeredEventListeners" in v
            ]
            if candidates:
                return max(candidates, key=lambda x: int(x) if x.isdigit() else 0)
            return None

        members = hierarchy.get(str(contentid), [])
        if not members:
            return None

        # Last member contains pagination controls
        last_member = str(members[-1])

        # Recursively find all descendant IDs of the last member
        def _descendants(node_id: str) -> list[str]:
            result = [node_id]
            for child in hierarchy.get(node_id, []):
                result.extend(_descendants(str(child)))
            return result

        desc_ids = _descendants(last_member)

        # Find the highest-numbered key with registeredEventListeners
        candidates = [
            d for d in desc_ids
            if d in state
            and isinstance(state[d], dict)
            and "registeredEventListeners" in state[d]
        ]

        if candidates:
            return max(candidates, key=lambda x: int(x) if x.isdigit() else 0)

        # Broader fallback: any key with registeredEventListeners
        all_candidates = [
            k for k, v in state.items()
            if isinstance(v, dict) and "registeredEventListeners" in v
        ]
        if all_candidates:
            return max(all_candidates, key=lambda x: int(x) if x.isdigit() else 0)

        return None

    # ----------------------------------------------------------
    # Discovery
    # ----------------------------------------------------------

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """
        Discover all OW court decisions via Vaadin UIDL protocol.

        Two-phase approach to avoid session timeout:
        1. Fast UIDL-only discovery: collect ALL stubs via rapid pagination
           (~0.3s per page, ~70s total for 221 pages)
        2. Yield stubs for fetching (Playwright-based, slow but session-independent)

        The Vaadin session times out if fetch_decision() (5-15s each via Playwright)
        runs between UIDL pagination requests. Collecting all stubs first avoids this.
        """
        if since_date and isinstance(since_date, str):
            since_date = parse_date(since_date)

        # Phase 1: Fast UIDL discovery — collect all stubs before any fetching
        all_stubs = self._discover_all_stubs()
        logger.info(f"[ow_gerichte] Discovered {len(all_stubs)} total stubs")

        # Phase 2: Filter and yield
        total_yielded = 0
        for stub in all_stubs:
            if self.state.is_known(stub["decision_id"]):
                continue
            if since_date and stub.get("decision_date"):
                if isinstance(stub["decision_date"], date) and stub["decision_date"] < since_date:
                    continue
            total_yielded += 1
            yield stub

        logger.info(f"[ow_gerichte] Discovery complete: {total_yielded} new stubs")

    def _discover_all_stubs(self) -> list[dict]:
        """
        Collect all decision stubs via fast UIDL pagination.

        No Playwright or slow operations — pure UIDL requests at ~0.3s each.
        Returns the complete list of stubs (typically ~2,205).
        """
        sess = self._init_vaadin_session()
        if not sess:
            logger.error("[ow_gerichte] Failed to initialize Vaadin session")
            return []

        contentid = sess.get("contentid")

        # Click search
        body = self._build_search_body(sess)
        data = self._uidl_post(body)
        if not data:
            logger.error("[ow_gerichte] Search click failed")
            return []

        # Update contentid from response if not found during init
        if not contentid:
            hierarchy = data.get("hierarchy", {})
            state = data.get("state", {})
            for k, children in hierarchy.items():
                if isinstance(children, list) and len(children) > 3:
                    first_child_state = state.get(str(children[0]), {})
                    if isinstance(first_child_state, dict) and "text" in first_child_state:
                        if RE_TREFFER.search(str(first_child_state["text"])):
                            contentid = k
                            break

        # Parse first page
        total, stubs = self._parse_results(data, contentid)
        logger.info(f"[ow_gerichte] Total decisions reported: {total}")

        all_stubs = list(stubs)
        page = 1

        # Paginate rapidly through all pages
        weiterkey = self._find_weiter_key(data, contentid)

        while weiterkey:
            page += 1
            body = self._build_next_body(sess, weiterkey)
            data = self._uidl_post(body)
            if not data:
                logger.warning(f"[ow_gerichte] Page {page} UIDL request failed, stopping")
                break

            _, stubs = self._parse_results(data, contentid)
            if not stubs:
                logger.info(f"[ow_gerichte] Page {page}: no more results")
                break

            all_stubs.extend(stubs)

            if page % 50 == 0:
                logger.info(f"[ow_gerichte] Page {page}: {len(all_stubs)} stubs collected")

            weiterkey = self._find_weiter_key(data, contentid)

        logger.info(f"[ow_gerichte] Pagination complete: {len(all_stubs)} stubs across {page} pages")
        return all_stubs

    # ----------------------------------------------------------
    # Playwright lifecycle for cache viewer
    # ----------------------------------------------------------

    _pw = None          # playwright context manager
    _browser = None     # browser instance
    _pw_context = None  # browser context with JSESSIONID cookie

    def _ensure_playwright(self) -> bool:
        """
        Lazily init Playwright browser with JSESSIONID cookie injected.

        Returns True if Playwright is ready, False otherwise.
        """
        if self._pw_context is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning(
                "[ow_gerichte] playwright not installed. "
                "pip install playwright && playwright install chromium --with-deps"
            )
            return False

        jsid = self.session.cookies.get("JSESSIONID")
        if not jsid:
            logger.warning("[ow_gerichte] No JSESSIONID in requests session")
            return False

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._pw_context = self._browser.new_context()
            self._pw_context.add_cookies([{
                "name": "JSESSIONID",
                "value": jsid,
                "domain": "rechtsprechung.ow.ch",
                "path": "/le",
            }])
            logger.info(f"[ow_gerichte] Playwright initialized with JSESSIONID={jsid[:8]}...")
            return True
        except Exception as e:
            logger.error(f"[ow_gerichte] Playwright init failed: {e}")
            self._cleanup_playwright()
            return False

    def _cleanup_playwright(self):
        """Close Playwright browser and context."""
        try:
            if self._pw_context:
                self._pw_context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw_context = None
        self._browser = None
        self._pw = None

    def _fetch_text_via_playwright(self, cache_url: str, docket: str) -> str:
        """
        Navigate Playwright to a cache URL and extract the rendered text.

        The cache viewer is a Vaadin app that renders decision text client-side.
        We wait for the content to load, then extract body text and clean it.
        """
        if not self._ensure_playwright():
            return ""

        page = None
        try:
            page = self._pw_context.new_page()
            page.goto(cache_url, wait_until="networkidle", timeout=30000)
            # Wait for Vaadin rendering
            page.wait_for_timeout(5000)

            body_text = page.inner_text("body")

            # Clean UI chrome from the extracted text
            # Remove common Vaadin UI elements that appear in the body
            lines = body_text.split("\n")
            cleaned = []
            skip_patterns = {
                "Neue Suche", "Original", "Treffer", "Suchbegriffe",
                "vorige Seite", "nächste Seite", "rechtsprechung.ow.ch",
                "zurück zur Trefferliste",
            }
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if any(pat in stripped for pat in skip_patterns):
                    continue
                cleaned.append(stripped)

            return "\n".join(cleaned).strip()

        except Exception as e:
            logger.warning(f"[ow_gerichte] Playwright fetch failed for {docket}: {e}")
            return ""
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    # ----------------------------------------------------------
    # Fetch full decision
    # ----------------------------------------------------------

    def fetch_decision(self, stub: dict) -> Decision | None:
        """
        Fetch the full decision text via Playwright cache viewer.

        Uses the cache URL from discovery (Vaadin cache viewer with authuser token).
        Falls back to abstract if Playwright fails.
        """
        docket = stub["docket_number"]
        cache_url = stub.get("cache_url", "")
        full_text = ""

        if cache_url:
            full_text = self._fetch_text_via_playwright(cache_url, docket)
            if full_text and len(full_text) > 100:
                logger.debug(f"[ow_gerichte] Got {len(full_text)} chars via Playwright for {docket}")

        # Fallback to abstract
        if not full_text or len(full_text) < 50:
            abstract = stub.get("abstract", "")
            if abstract and len(abstract) > 20:
                full_text = abstract
                logger.debug(f"[ow_gerichte] Using abstract fallback for {docket}")
            elif full_text:
                pass  # Keep short text
            else:
                full_text = f"[Text extraction failed for {docket}]"

        # Decision date: the entry's own ruling line beats the portal's date,
        # which is the 2015 import day for every older entry.
        decision_date = stub.get("decision_date")
        publication_date = None
        _, own = ruling_date(full_text, docket)
        if own:
            if decision_date and decision_date != own:
                publication_date = decision_date
            decision_date = own
        if not decision_date:
            dm = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", full_text)
            if dm:
                decision_date = parse_date(dm.group(0))
        if not decision_date:
            logger.warning(f"[ow_gerichte] No date for {stub.get('docket_number', '?')}")

        # Language from metadata or detection
        language = stub.get("language", "de")
        if language not in ("de", "fr", "it", "rm") and len(full_text) > 200:
            language = detect_language(full_text)

        # Source URL: prefer cache URL, fall back to host
        source_url = cache_url or stub.get("url", HOST)

        return Decision(
            decision_id=stub["decision_id"],
            court="ow_gerichte",
            canton="OW",
            docket_number=docket,
            docket_number_2=stub.get("docket_number_2"),
            decision_date=decision_date,
            publication_date=publication_date,
            language=language,
            regeste=full_regeste(full_text, stub.get("abstract")) or stub.get("abstract") or None,
            full_text=full_text,
            source_url=source_url,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    def __del__(self):
        """Ensure Playwright resources are cleaned up."""
        self._cleanup_playwright()
