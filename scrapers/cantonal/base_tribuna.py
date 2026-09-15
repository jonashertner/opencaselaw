"""
Tribuna Platform Base — GWT-RPC Cantonal Court Scraper
=======================================================
Base class for Tribuna VTPlus cantonal court portals.

Architecture (reverse-engineered Feb 2026):
1. GET base URL → set session cookies
2. POST readConfigFile() → get DLAConfig with encrypted credentials
3. POST getBerechtigungen("","") → get permissions
4. POST search(...) → paginated results (20 per page)
   - Old Tribuna (GR, ZG): 46 params (20 search fields)
   - New Tribuna (FR, BE VG): 47 params (21 search fields)
5. GET ServletDownload/{docket}_{enc_path}?path=...&pathIsEncrypted=1 → PDF

To implement a new Tribuna scraper:
    class GRGerichteScraper(TribunaBaseScraper):
        CANTON = "GR"
        COURT_CODE_STR = "gr_gerichte"
        BASE_URL = "https://entscheidsuche.gr.ch"
        LOCALE = "de"

Covered portals (as of Feb 2026):
  - GR: https://entscheidsuche.gr.ch (old Tribuna version)
  - ZG VGR: https://verwaltungsgericht.zg.ch
  - BE ZSG: https://www.zsg-entscheide.apps.be.ch/tribunapublikation
  - BE VGR: https://www.vg-urteile.apps.be.ch/tribunapublikation (new Tribuna version)
  - FR: https://publicationtc.fr.ch (new Tribuna version)

Note: Different portals run different Tribuna compilations. The GWT permutation
is auto-discovered from each portal's tribunavtplus.nocache.js at runtime.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote
from typing import Iterator

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date

logger = logging.getLogger(__name__)


class TribunaProtocolError(RuntimeError):
    """The Tribuna GWT-RPC server rejected our request shape.

    Typically an ``IncompatibleRemoteServiceException`` returned as a ``//EX``
    payload after a server-side VTPlus upgrade changes the deployed
    ``search()`` signature so it no longer matches ``SEARCH_FIELD_COUNT``.
    Raising — rather than silently returning zero results — keeps the failure
    visible to the scraper-health monitor instead of masquerading as an empty
    portal (which is what hid the BE Zivil-/Straf + Anwaltsaufsicht breakage
    for months and defeated the silent-success detector).
    """


# GWT-RPC serialization policy hashes (shared across Tribuna VTPlus versions)
_CONFIG_HASH = "7225438C30B96853F589E2336CAF98F1"
_LOADTABLE_HASH = "CAC80118FB77794F1FDFC1B51371CC63"
# Fallback permutation (for portals where auto-discovery fails)
_GWT_PERMUTATION_FALLBACK = "C91406E3C064F0230BE12F3EF5EDF1D6"

# Regex patterns for parsing search responses
_RE_TOTAL = re.compile(r"^//OK\[(\d+)")
_RE_DOC_ID = re.compile(r"^[0-9a-f]{32}$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_DOCKET = re.compile(r"^[A-Z0-9]{1,4}\s+\d{4}\s+\d+$")
_RE_ENC_PATH = re.compile(r"^[0-9a-f]{60,}$")
# Since ~2026-08 publicationtc.fr.ch (VTPlus 24.x) sends the encrypted document
# path as ~140 chars of base64 (with "=" GWT-escaped as \x3D) instead of hex.
# GR / ZG / BE still send hex, so both forms are recognised.
_RE_B64_PATH = re.compile(r"^[A-Za-z0-9+/]{110,}={0,2}$")
_RE_GWT_ESCAPE = re.compile(r"\\x([0-9A-Fa-f]{2})|\\u([0-9A-Fa-f]{4})|\\(.)")


def _gwt_unescape(s: str) -> str:
    r"""Decode the escapes GWT-RPC uses inside its string table (\x3D, \uNNNN, \\, \")."""
    if "\\" not in s:
        return s

    def _sub(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1), 16))
        if m.group(2):
            return chr(int(m.group(2), 16))
        return m.group(3)

    return _RE_GWT_ESCAPE.sub(_sub, s)
_RE_HEX = re.compile(r"^[0-9a-f]{60,}$")
_RE_B64CRED = re.compile(r"^[A-Za-z0-9+/=]{60,140}$")

# Column definitions for the search request (field key, display label)
_COLUMNS = [
    ("decisionDate", "Entscheiddatum"),
    ("dossierNumber", "Dossier"),
    ("classification", "Zusatzeigenschaft"),
    ("indexCode", "Quelle"),
    ("dossierObject", "Betreff"),
    ("law", "Rechtsgebiet"),
    ("shortText", "Vorschautext"),
    ("department", "Abteilung"),
    ("createDate", "Erfasst am"),
    ("creater", "Ersteller"),
    ("judge", "Richter"),
    ("executiontype", "Erledigungsart"),
    ("legalDate", "Rechtskraftdatum"),
    ("objecttype", "Objekttyp"),
    ("typist", "Schreiber"),
    ("description", "Beschreibung"),
    ("reference", "Referenz"),
    ("relevance", None),  # no label for last column
]


class TribunaBaseScraper(BaseScraper):
    """Base scraper for Tribuna VTPlus GWT-RPC court portals."""

    CANTON: str = ""
    COURT_CODE_STR: str = ""
    BASE_URL: str = ""           # e.g., "https://entscheidsuche.gr.ch"
    COURT_FILTER: str = ""       # e.g., "OG" — single court filter
    COURT_FILTERS: list[str] = []  # Multiple courts — iterates over each
    LOCALE: str = "de"           # "de", "fr", "it"
    REQUEST_DELAY: float = 2.5
    MAX_PAGES: int = 1000        # 20 results/page = 20,000 max
    PAGE_SIZE: int = 20          # Fixed by Tribuna server
    # Number of search field strings in the GWT-RPC search method.
    # Old Tribuna (GR, ZG): 20 fields → 46-param search()
    # New Tribuna (FR, BE VG): 21 fields → 47-param search()
    SEARCH_FIELD_COUNT: int = 20

    # Date-windowed discovery (opt-in). When DATE_WINDOW_FIELD is set to the
    # search-field index whose value filters by an ISO date prefix ("YYYY",
    # "YYYY-MM", "YYYY-MM-DD"), discovery partitions the result set into date
    # windows instead of one offset-paginated pass. This defeats the server's
    # deep-offset under-fill: offset pagination over the portal's unstable date
    # sort silently skips rows on large result sets (e.g. BE VG returned only
    # 9,009 of 11,420). Year windows partition the corpus exactly; windows that
    # still under-fill are split finer (year → month → day) up to
    # DATE_WINDOW_MAX_DEPTH. None = original single-pass behaviour (every other
    # Tribuna portal is unaffected).
    DATE_WINDOW_FIELD: int | None = None
    DATE_WINDOW_START_YEAR: int = 2000
    DATE_WINDOW_MAX_DEPTH: int = 1      # 0=year only, 1=+month, 2=+day
    DATE_WINDOW_SPLIT_OVER: int = 150   # only split a window finer when total exceeds this

    # Overridable: the tribunavtplus subpath (usually "tribunavtplus")
    TRIBUNA_PATH: str = "tribunavtplus"

    @property
    def court_code(self) -> str:
        return self.COURT_CODE_STR

    @property
    def _gwt_base(self) -> str:
        return f"{self.BASE_URL}/{self.TRIBUNA_PATH}"

    def _discover_permutation(self) -> str:
        """Auto-discover GWT permutation from the portal's nocache.js."""
        try:
            r = self.get(f"{self._gwt_base}/tribunavtplus.nocache.js")
            # Extract 32-char uppercase hex hashes (GWT permutation candidates)
            hashes = re.findall(r"[A-F0-9]{32}", r.text)
            if hashes:
                perm = hashes[0]
                logger.info(f"[{self.court_code}] Auto-discovered GWT permutation: {perm}")
                return perm
        except Exception as e:
            logger.debug(f"[{self.court_code}] Permutation discovery failed: {e}")
        logger.warning(f"[{self.court_code}] Using fallback GWT permutation")
        return _GWT_PERMUTATION_FALLBACK

    def _gwt_headers_with(self, permutation: str) -> dict:
        return {
            "Content-Type": "text/x-gwt-rpc; charset=utf-8",
            "X-GWT-Permutation": permutation,
            "X-GWT-Module-Base": f"{self._gwt_base}/",
        }

    @property
    def _gwt_headers(self) -> dict:
        # Use cached permutation if available, otherwise fallback
        perm = getattr(self, "_cached_permutation", _GWT_PERMUTATION_FALLBACK)
        return self._gwt_headers_with(perm)

    def _init_session(self) -> str:
        """Initialize session: GET base, discover permutation, readConfig, getBerechtigungen.

        Returns the encrypted credential string from config.
        """
        # Step 1: Cookie init
        try:
            self.get(f"{self.BASE_URL}/")
        except Exception:
            pass

        # Step 1b: Discover the correct GWT permutation for this portal
        self._cached_permutation = self._discover_permutation()

        # Step 2: readConfigFile
        config_body = (
            f"7|0|4|{self._gwt_base}/|{_CONFIG_HASH}|"
            "tribunavtplus.client.zugriff.ConfigService|readConfigFile|"
            "1|2|3|4|0|"
        )
        resp = self.post(
            f"{self._gwt_base}/config",
            data=config_body,
            headers=self._gwt_headers,
        )

        # Extract the ~96-char encrypted credential from config response
        config_strings = re.findall(r'"([^"]*)"', resp.text)
        hex_strings = [s for s in config_strings if _RE_HEX.match(s)]
        # The credential is the last hex string around 96 chars (varies per portal)
        # Sort by length and pick the one closest to 96 chars
        credential = ""
        if hex_strings:
            # Prefer ~96 char string, fallback to last
            candidates = sorted(hex_strings, key=lambda s: abs(len(s) - 96))
            credential = candidates[0]
        else:
            # FR came back from its 2026-08/09 outage issuing base64-style
            # credentials ("AAAAD…", +/= charset) instead of hex; BE portals
            # still use hex, so this path only runs when hex finds nothing.
            # Same closest-to-96 heuristic. Dots exclude GWT class names.
            b64_strings = [s for s in config_strings
                           if _RE_B64CRED.match(s)]
            if b64_strings:
                candidates = sorted(b64_strings,
                                    key=lambda s: abs(len(s) - 96))
                credential = candidates[0]
        logger.info(f"[{self.court_code}] Config loaded, credential len={len(credential)}")

        # Step 3: getBerechtigungen
        berech_body = (
            f"7|0|6|{self._gwt_base}/|{_LOADTABLE_HASH}|"
            "tribunavtplus.client.zugriff.LoadTableService|getBerechtigungen|"
            "java.lang.String/2004016611||"
            "1|2|3|4|2|5|5|6|6|"
        )
        self.post(
            f"{self._gwt_base}/loadTable",
            data=berech_body,
            headers=self._gwt_headers,
        )

        return credential

    def _build_search_body(self, credential: str, page: int, total: int | None,
                           court_filter: str | None = None,
                           search_field_overrides: dict | None = None) -> str:
        """Build the GWT-RPC search request body.

        Args:
            credential: Encrypted credential string from config.
            page: Page number (0-indexed).
            total: Total results from previous response (None for first page).
            court_filter: Court filter code (defaults to self.COURT_FILTER).
        """
        base = self._gwt_base
        court = court_filter if court_filter is not None else self.COURT_FILTER
        locale = self.LOCALE

        # Build string table
        strings = [
            f"{base}/",                                    # 0
            _LOADTABLE_HASH,                               # 1
            "tribunavtplus.client.zugriff.LoadTableService",  # 2
            "search",                                      # 3
            "java.lang.String/2004016611",                 # 4
            "java.util.ArrayList/4159755760",              # 5
            "Z",                                           # 6
            "I",                                           # 7
            "java.lang.Integer/3438268394",                # 8
            "java.util.Map",                               # 9
            "",                                            # 10 (empty)
            "0",                                           # 11 (always "0" — page is a literal in values)
            court,                                         # 12
            "0;false",                                     # 13
            "5;true",                                      # 14
            credential,                                    # 15
            "1",                                           # 16
            "java.util.HashMap/1797211028",                # 17
        ]
        # Add column pairs
        for key, label in _COLUMNS:
            strings.append(key)
            if label:
                strings.append(label)
        strings.append(locale)

        # Optional per-search-field filter values (foundation for date-windowed
        # discovery). Each override injects a value into one search field; the
        # values are appended to the string table last so existing refs stay
        # valid. Default: every search field empty (ref 11 = "").
        _nf = self.SEARCH_FIELD_COUNT
        field_refs = ["11"] * _nf
        if search_field_overrides:
            for _fi, _val in sorted(search_field_overrides.items()):
                if 0 <= _fi < _nf and _val:
                    strings.append(_val)
                    field_refs[_fi] = str(len(strings))

        num_strings = len(strings)

        # Build string table section (pipe-delimited)
        st = "|".join(strings)

        # Type descriptors — dynamic based on SEARCH_FIELD_COUNT
        # Params: String, String, ArrayList, Z, ArrayList,
        #         N×String (search fields), 3×I, 2×String, 3×Integer,
        #         4×String, Z, Map, 7×String
        # N=20 → 46 params (old Tribuna), N=21 → 47 params (new Tribuna)
        nf = self.SEARCH_FIELD_COUNT
        num_params = 5 + nf + 21
        field_types = "|".join(["5"] * nf)
        types = (
            f"5|5|6|7|6|"           # params 1-5
            f"{field_types}|"        # params 6-(5+nf): search field strings
            f"8|8|8|5|5|"           # I×3, String×2
            f"9|9|9|5|5|5|5|"       # Integer×3, String×4
            f"7|10|"                # Z, Map
            f"5|5|5|5|5|5|5"        # String×7
        )

        # Value section — parameterized for page and total
        # String refs are 1-based: ref N → strings[N-1]
        # str[10] = "" → ref 11, str[11] = "0" → ref 12
        # Page number is NOT in the string table — it's a literal in values (20|{page}|-1|)
        empty_ref = 11  # 1-based ref to "" (str[10])
        zero_ref = 12   # 1-based ref to "0" (str[11], always "0")

        # Search field values: empty by default, or per-field overrides.
        empties = "|".join(field_refs)

        # Column definition map refs (HashMap<String,String>: key→label)
        col_refs = []
        idx = 19  # 1-based index starting after "java.util.HashMap/1797211028" (str[17] = ref 18)
        for key, label in _COLUMNS:
            col_refs.append(f"5|{idx}")  # key
            idx += 1
            if label:
                col_refs.append(f"5|{idx}")  # label value
                idx += 1
            else:
                col_refs.append(f"5|{empty_ref}")  # empty string value
        col_section = "|".join(col_refs)

        locale_ref = idx  # Last string = locale

        if total is None:
            # Page 0: no total count
            values = (
                f"{empty_ref}|{zero_ref}|6|0|0|6|1|5|13|"
                f"{empties}|"
                f"20|{page}|-1|"
                f"{empty_ref}|{empty_ref}|"
                f"0|9|0|9|-1|"
                f"14|15|16|17|0|18|18|"
                f"{col_section}|"
                f"{empty_ref}|{locale_ref}|"
                f"{empty_ref}|{empty_ref}|{zero_ref}|{zero_ref}|0|"
            )
        else:
            # Page N>0: include total count
            values = (
                f"{empty_ref}|{zero_ref}|6|0|0|6|1|5|13|"
                f"{empties}|"
                f"20|{page}|-1|"
                f"{empty_ref}|{empty_ref}|"
                f"9|{total}|9|0|9|-1|"
                f"14|15|16|17|0|18|18|"
                f"{col_section}|"
                f"{empty_ref}|{locale_ref}|"
                f"{empty_ref}|{empty_ref}|{zero_ref}|{zero_ref}|0|"
            )

        return f"7|0|{num_strings}|{st}|1|2|3|4|{num_params}|{types}|{values}"

    def _parse_search_response(self, text: str) -> tuple[int, list[dict]]:
        """Parse GWT-RPC search response.

        Returns (total_count, list_of_stubs).
        Each stub has: docket_number, decision_date, enc_path, title.
        (doc_id was dropped 2026-08-27 — optional upstream, never read,
        and gating on it silently discarded rows. See GitHub #68.)
        """
        if text.startswith("//EX"):
            # GWT-RPC server-side exception (typically
            # IncompatibleRemoteServiceException when a VTPlus upgrade changes
            # the deployed search() signature so it no longer matches
            # SEARCH_FIELD_COUNT). MUST NOT be swallowed as (0, []): doing so
            # masked a stale-protocol failure as an empty portal for months and
            # defeated the silent-success health detector. Raise so the scraper
            # fails loudly (the "search failed" ERROR line also feeds the
            # discovery-error count in run_all_scrapers.py).
            logger.error(
                f"[{self.court_code}] Tribuna search failed: server rejected "
                f"request (GWT //EX; SEARCH_FIELD_COUNT={self.SEARCH_FIELD_COUNT} "
                f"may be stale): {text[:200]}"
            )
            raise TribunaProtocolError(
                f"{self.court_code}: GWT-RPC //EX — server search() signature "
                f"no longer matches SEARCH_FIELD_COUNT={self.SEARCH_FIELD_COUNT}"
            )
        if not text.startswith("//OK"):
            logger.warning(f"[{self.court_code}] Bad response: {text[:200]}")
            return 0, []

        m = _RE_TOTAL.match(text)
        total = int(m.group(1)) if m else 0

        # Extract all strings from the response, KEEPING their positions —
        # the row a value belongs to is determined by where it sits, not by
        # how many values of its kind came before it.
        all_strings = [_gwt_unescape(x) for x in re.findall(r'"([^"]*)"', text)]

        # Group fields into decisions.
        #
        # Response layout, confirmed against a live be_verwaltungsgericht
        # page on 2026-08-27: each row is doc_id -> docket -> date(s) ->
        # enc_path, and the doc_id PRECEDES its own docket.
        #
        # Two defects came from zipping parallel lists by ordinal:
        #
        # 1. Rows vanished. The loop ran `min(len(doc_ids), len(dockets))`,
        #    but doc_id is an OPTIONAL per-row field that older records
        #    mostly lack — a 2011 window returns 12 dockets and 2 doc_ids,
        #    so ten real decisions were silently dropped. Per-year recovery
        #    tracked the doc_id adoption curve exactly (2017: 987/988,
        #    2013: 82/674, 2011: 2/12), which is GitHub #68. doc_id was
        #    written into the stub and never read again by any Tribuna
        #    scraper, so it is gone entirely now. (bvger/bstger have their
        #    own doc_id, but they extend BaseScraper, not this class.)
        #
        # 2. Dates shifted. A row can emit more than one date — the
        #    Rechtskraft sentinel "0000-00-00" and a later createDate — so
        #    `dates_list[i]` slid every subsequent row along. Measured on
        #    that same live page: 20 dockets, 22 dates, and 19 of the 20
        #    rows carried another row's date. That is the source of the
        #    "No date for" warnings and the null decision_date rows.
        #
        # Both are fixed by anchoring on the docket: row i owns everything
        # from its own docket up to the next docket. The preceding doc_id
        # falls outside the span, which is exactly why a forward span must
        # not be used to recover it.
        decisions = []
        kinds: list[tuple[int, str, str]] = []
        for idx, s in enumerate(all_strings):
            if _RE_DOC_ID.match(s):
                kinds.append((idx, "doc_id", s))
            elif _RE_DOCKET.match(s):
                kinds.append((idx, "docket", s))
            elif _RE_DATE.match(s):
                kinds.append((idx, "date", s))
            elif _RE_ENC_PATH.match(s) or _RE_B64_PATH.match(s):
                kinds.append((idx, "enc_path", s))

        # Find titles: strings that are >10 chars, not hex, not dates, not types
        skip_prefixes = ("java.", "tribuna", "[B/", "[L", "com.", "viewtype",
                         "reportpath", "reportexport", "reporttitle", "reportname")
        for idx, s in enumerate(all_strings):
            if (len(s) > 10
                and not _RE_DOC_ID.match(s)
                and not _RE_DATE.match(s)
                and not _RE_DOCKET.match(s)
                and not _RE_ENC_PATH.match(s)
                and not _RE_B64_PATH.match(s)
                and not _RE_HEX.match(s)
                # An all-digit run is an internal row id, not a subject line.
                # Measured 2026-08-27: the span for be_verwaltungsgericht
                # "200 2011 322" holds '113347081404410' immediately before
                # the real title 'Einspracheentscheid vom 22. August 2011',
                # so a first-match rule picks the id without this.
                and not s.isdigit()
                and not any(s.startswith(p) for p in skip_prefixes)):
                kinds.append((idx, "title", s))
        kinds.sort(key=lambda t: t[0])

        # One row per docket. Row i spans [its own index, next docket's index).
        docket_positions = [i for i, (_, k, _) in enumerate(kinds) if k == "docket"]
        for n, ki in enumerate(docket_positions):
            end = docket_positions[n + 1] if n + 1 < len(docket_positions) else len(kinds)
            span = kinds[ki:end]

            def _first(kind: str, skip: set[str] = frozenset()) -> str:
                for _, k, v in span:
                    if k == kind and v not in skip:
                        return v
                return ""

            decisions.append({
                "docket_number": kinds[ki][2],
                # "0000-00-00" is the Rechtskraft sentinel, not a date.
                "decision_date": _first("date", {"0000-00-00"}),
                "enc_path": _first("enc_path"),
                "title": _first("title"),
            })

        return total, decisions

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover decisions via Tribuna GWT-RPC search."""
        credential = self._init_session()
        if not credential:
            logger.error(f"[{self.court_code}] Failed to get credential from config")
            return

        # Determine which court filters to iterate over
        filters = self.COURT_FILTERS if self.COURT_FILTERS else [self.COURT_FILTER]

        for court_filter in filters:
            yield from self._search_court(credential, court_filter, since_date)

    def _search_court(self, credential: str, court_filter: str, since_date=None) -> Iterator[dict]:
        """Search a single court filter and yield decision stubs.

        With DATE_WINDOW_FIELD set, discovery is partitioned into date windows
        (year, recursively split to month/day where the server under-fills);
        otherwise the original single offset-paginated pass.
        """
        if self.DATE_WINDOW_FIELD is not None:
            yield from self._search_court_windowed(credential, court_filter, since_date)
            return

        total = None
        for page in range(self.MAX_PAGES):
            try:
                body = self._build_search_body(credential, page, total, court_filter)
                resp = self.post(
                    f"{self._gwt_base}/loadTable",
                    data=body,
                    headers=self._gwt_headers,
                )
            except Exception as e:
                logger.error(f"[{self.court_code}] Search page {page} failed: {e}")
                break

            page_total, decisions = self._parse_search_response(resp.text)

            if total is None:
                total = page_total
                self.portal_count = (self.portal_count or 0) + total
                logger.info(f"[{self.court_code}] Total results for '{court_filter}': {total}")

            if not decisions:
                logger.info(f"[{self.court_code}] No more results at page {page}")
                break

            for stub in decisions:
                if since_date and stub.get("decision_date"):
                    d = parse_date(stub["decision_date"])
                    if d and d < since_date:
                        continue

                did = make_decision_id(self.court_code, stub["docket_number"])
                if self.state.is_known(did):
                    continue

                stub["decision_id"] = did
                yield stub

            # Check if we've exhausted all pages
            if total and (page + 1) * self.PAGE_SIZE >= total:
                logger.info(f"[{self.court_code}] All {total} results covered in {page+1} pages")
                break

    # ------------------------------------------------------------------
    # Date-windowed discovery (opt-in via DATE_WINDOW_FIELD)
    # ------------------------------------------------------------------

    def _search_court_windowed(self, credential, court_filter, since_date=None) -> Iterator[dict]:
        """Partition discovery into descending-year date windows."""
        from datetime import datetime
        end_year = datetime.now().year + 1
        start_year = self.DATE_WINDOW_START_YEAR
        if since_date:
            # Incremental run: only walk windows that can contain new decisions.
            start_year = max(start_year, since_date.year)
        for year in range(end_year, start_year - 1, -1):
            yield from self._window(credential, court_filter, since_date, str(year), depth=0)

    def _window(self, credential, court_filter, since_date, prefix, depth) -> Iterator[dict]:
        """Walk one date window; split finer if the server under-fills it."""
        total, stubs = self._collect_window(credential, court_filter, prefix)
        if depth == 0:
            # Each year is counted once → portal_count tracks the true corpus size.
            self.portal_count = (self.portal_count or 0) + total
            logger.info(f"[{self.court_code}] window '{prefix}': total={total}")
        if total == 0:
            return
        unique = {st["docket_number"]: st for st in stubs}
        # The server under-fills large windows (offset pagination over an
        # unstable date sort skips rows). Split finer until each window is small
        # enough to paginate completely.
        if (len(unique) < total and depth < self.DATE_WINDOW_MAX_DEPTH
                and total > self.DATE_WINDOW_SPLIT_OVER):
            for child in self._sub_windows(prefix, depth):
                yield from self._window(credential, court_filter, since_date, child, depth + 1)
            return
        if len(unique) < total:
            logger.warning(
                f"[{self.court_code}] window '{prefix}': recovered {len(unique)}/{total} "
                f"(residual under-fill)"
            )
        yield from self._yield_new(list(unique.values()), since_date)

    @staticmethod
    def _sub_windows(prefix: str, depth: int) -> list[str]:
        if depth == 0:      # year → months
            return [f"{prefix}-{m:02d}" for m in range(1, 13)]
        if depth == 1:      # month → days
            return [f"{prefix}-{d:02d}" for d in range(1, 32)]
        return []

    def _collect_window(self, credential, court_filter, value) -> tuple[int, list[dict]]:
        """Paginate one date-filtered window fully; return (server_total, stubs)."""
        overrides = {self.DATE_WINDOW_FIELD: value}
        total = None
        stubs: list[dict] = []
        for page in range(self.MAX_PAGES):
            try:
                body = self._build_search_body(
                    credential, page, total, court_filter,
                    search_field_overrides=overrides,
                )
                resp = self.post(
                    f"{self._gwt_base}/loadTable", data=body, headers=self._gwt_headers
                )
            except Exception as e:
                logger.error(f"[{self.court_code}] Search page {page} ['{value}'] failed: {e}")
                break
            page_total, decisions = self._parse_search_response(resp.text)
            if total is None:
                total = page_total
            if not decisions:
                break
            stubs.extend(decisions)
            if total and (page + 1) * self.PAGE_SIZE >= total:
                break
        return (total or 0), stubs

    def _yield_new(self, stubs, since_date) -> Iterator[dict]:
        """Dedup collected stubs + apply since_date; yield the unknown ones."""
        seen = set()
        for stub in stubs:
            docket = stub.get("docket_number")
            if not docket or docket in seen:
                continue
            seen.add(docket)
            if since_date and stub.get("decision_date"):
                d = parse_date(stub["decision_date"])
                if d and d < since_date:
                    continue
            did = make_decision_id(self.court_code, docket)
            if self.state.is_known(did):
                continue
            stub["decision_id"] = did
            yield stub

    def _build_download_url(self, stub: dict) -> str:
        """Build the PDF download URL from stub data."""
        docket_url = stub["docket_number"].replace(" ", "_")
        enc_path = stub.get("enc_path", "")
        if not enc_path:
            return ""
        if not _RE_ENC_PATH.match(enc_path):
            # base64 path (FR since 2026-08): "/" and "+" must not land in the URL
            # path segment (the old shape answers HTTP 400); the servlet accepts the
            # token in the query string alone. Verified live 2026-09-15: 601 2026 45
            # → 200, application/pdf, 9 pages.
            return (
                f"{self._gwt_base}/ServletDownload/{docket_url}"
                f"?path={quote(enc_path, safe='')}&pathIsEncrypted=1&dossiernummer={docket_url}"
            )
        return (
            f"{self._gwt_base}/ServletDownload/{docket_url}_{enc_path}"
            f"?path={enc_path}&pathIsEncrypted=1&dossiernummer={docket_url}"
        )

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch the PDF and extract text for a decision."""
        url = self._build_download_url(stub)
        if not url:
            logger.warning(f"[{self.court_code}] No download URL for {stub['docket_number']}")
            return None

        try:
            resp = self.get(url)
            ct = resp.headers.get("Content-Type", "")
            if "pdf" in ct.lower() and resp.content[:4] == b"%PDF":
                text = self._pdf_text(resp.content)
            else:
                logger.warning(
                    f"[{self.court_code}] Non-PDF response for {stub['docket_number']}: {ct}"
                )
                return None

            text = self.clean_text(text)
            if len(text) < 50:
                logger.warning(f"[{self.court_code}] Too short text for {stub['docket_number']}")
                return None

            dd = parse_date(stub.get("decision_date", ""))
            if not dd:
                logger.warning(f"[{self.court_code}] No date for {stub['docket_number']}")
            return Decision(
                decision_id=stub["decision_id"],
                court=self.court_code,
                canton=self.CANTON,
                docket_number=stub["docket_number"],
                decision_date=dd,
                language=detect_language(text),
                title=stub.get("title"),
                full_text=text,
                source_url=url,
                cited_decisions=extract_citations(text),
            )
        except Exception as e:
            logger.error(f"[{self.court_code}] Fetch error {stub['docket_number']}: {e}")
            return None

    @staticmethod
    def _pdf_text(data: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            return "\n\n".join(p.get_text() for p in doc)
        except ImportError:
            pass
        try:
            from pdfminer.high_level import extract_text
            from io import BytesIO
            return extract_text(BytesIO(data))
        except ImportError:
            pass
        return ""
