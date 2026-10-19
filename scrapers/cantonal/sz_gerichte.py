"""
Schwyz Courts Scraper (SZ Gerichte)
====================================
Scrapes court decisions from the Tribuna VTPlus platform at gerichte.sz.ch.

Platform: Tribuna GWT-RPC
Coverage: Kantonsgericht Schwyz (KG)
Volume: ~3,200 decisions (2017-present)
Language: de

Architecture (reverse-engineered from entscheidsuche NeueScraper):
- Hardcoded GWT-RPC search templates (1 result per page)
- Credential embedded in the template (hex string from server config)
- Response parsed by extracting quoted strings from GWT-RPC format
- HTML content retrieved via getDocumentDetails RPC call
- Fallback: PDF via encrypted path + urlEncodingTribuna + ServletDownload

Key differences from base_tribuna.py:
- Different GWT permutation hash per court (KG vs VG)
- Column labels: Urteilsdatum (not Entscheiddatum), Klassierung (not Zusatzeigenschaft)
- Court filter: TRI
- 1 result per page (not 20) — required by this Tribuna variant
- Encrypted PDF paths (128-192 char hex) need urlEncodingTribuna RPC
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Iterator

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date

logger = logging.getLogger(__name__)

# Regex patterns for parsing GWT-RPC responses
_RE_STRIP_PREFIX = re.compile(r"//OK\[[0-9,\.]+\[")
_RE_EXTRACT_STRINGS = re.compile(r'(?<=,")[^\\"]*(?:\\\\"[^\\"]*)*(?=",)')
_RE_TOTAL = re.compile(r"(?<=^//OK\[)[0-9]+")
_RE_DOC_ID = re.compile(r"^[0-9a-f]{32}$")
_RE_DOCKET = re.compile(r"^[A-Z0-9]{1,3}\s(?:19|20)\d\d\s\d+$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_ENC_PATH = re.compile(r"^[0-9a-f]{60,}$")
_RE_DECRYPT_V1 = re.compile(r'(?<=//OK\[1,\[")[0-9a-f]+')
_RE_DECRYPT_V2 = re.compile(
    r'(?<=//OK)([0-9,"a-z.A-Z/\[\]]+partURL",")'
    r'(?P<p1>[^"]+_)(?P<p2>[^"_]+)","(?P<p3>dossiernummer)","(?P<p4>[^"]+)'
)
_RE_HEX_DECODE = re.compile(r"\\x([0-9A-Fa-f]{2})")

# Date of a document from its own head (2026-09-26). The Tribuna listing stamps
# the DOSSIER's date on every document in it (JU ADM 2024 211: listed 27.5.2025,
# "DÉCISION DU 5 FEVRIER 2025" on the page) or no date at all (0000-00-00), so a
# second document under a held docket needs the date it states itself. The
# EARLIEST heading or place-and-date line wins; dates cited later in the text
# (earlier rulings, filings) are never reached first.
_FR_MONTHS = {m: i for i, m in enumerate(
    "janvier fevrier mars avril mai juin juillet aout septembre octobre "
    "novembre decembre".split(), 1)}
_RE_HEAD_DECISION = re.compile(
    r"\b(?:arret|decision|ordonnance|jugement|prononce)(?:\s*/\s*[a-z]+)?"
    r"(?:\s+[a-z]+)?\s+du\s+(\d{1,2})(?:er)?\s+([a-z]+)\s+((?:19|20)\d{2})\b")
_RE_HEAD_PLACE = re.compile(
    r"\b(?:porrentruy|delemont|saignelegier),?\s+le\s+(\d{1,2})(?:er)?\s+"
    r"([a-z]+)\s+((?:19|20)\d{2})\b")


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower())


def header_date(text: str, head_chars: int = 3000) -> date | None:
    """Decision date stated in a document's head, or None."""
    head = _fold((text or "")[:head_chars])
    best = None
    for rx in (_RE_HEAD_DECISION, _RE_HEAD_PLACE):
        for m in rx.finditer(head):
            month = _FR_MONTHS.get(m.group(2))
            if not month:
                continue
            try:
                d = date(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), d)
            break
    return best[1] if best else None


class SZGerichteScraper(BaseScraper):
    """Scraper for Schwyz Kantonsgericht (KG) via Tribuna GWT-RPC."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 50

    # --- Configuration (overridden by SZVerwaltungsgerichtScraper) ---
    CANTON = "SZ"
    COURT_CODE_STR = "sz_gerichte"
    TRIBUNA_URL = "https://gerichte.sz.ch/tribunavtplus/loadTable"
    GWT_PERMUTATION = "8AF5705066F952B29FA749FC5DB6C65D"
    GWT_MODULE_BASE = "https://gerichte.sz.ch/tribunavtplus/"
    DOWNLOAD_URL = "https://gerichte.sz.ch/tribunavtplus/ServletDownload/"

    # Hardcoded search template from entscheidsuche (credential embedded)
    SEARCH_TPL = (
        r"7|0|55|https://gerichte.sz.ch/tribunavtplus/|"
        r"CAC80118FB77794F1FDFC1B51371CC63|"
        r"tribunavtplus.client.zugriff.LoadTableService|search|"
        r"java.lang.String/2004016611|java.util.ArrayList/4159755760|Z|I|"
        r"java.lang.Integer/3438268394|java.util.Map||0|TRI|0;false|5;true|"
        r"57ff49a267d6d777cb0fb9e30ad4179b362c119ebf8551cb23fde1099f740850"
        r"c0dae6bf1acc2eb965090792178ddbf8|"
        r"1|java.util.HashMap/1797211028|"
        r"decisionDate|Urteilsdatum|dossierNumber|Dossier|classification|Klassierung|"
        r"indexCode|Quelle|dossierObject|Betreff|law|Rechtsgebiet|shortText|Vorschautext|"
        r"department|Abteilung|createDate|Erstelldatum|creater|Ersteller|judge|Richter|"
        r"executiontype|Erledigungsart|legalDate|Rechtskraftdatum|objecttype|Objekttyp|"
        r"typist|Schreiber|description|Beschreibung|reference|Referenz|relevance|Relevanz|de|"
        r"1|2|3|4|46|5|5|6|7|6|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|5|"
        r"8|8|8|5|5|9|9|9|5|5|5|5|7|10|5|5|5|5|5|5|5|"
        r"11|12|6|0|0|6|1|5|13|"
        r"11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|11|"
        r"1|{page_nr}|-1|11|11|0|9|0|9|-1|"
        r"14|15|16|17|0|18|18|"
        r"5|19|5|20|5|21|5|22|5|23|5|24|5|25|5|26|5|27|5|28|5|29|5|30|"
        r"5|31|5|32|5|33|5|34|5|35|5|36|5|37|5|38|5|39|5|40|5|41|5|42|"
        r"5|43|5|44|5|45|5|46|5|47|5|48|5|49|5|50|5|51|5|52|5|53|5|54|"
        r"11|55|11|11|12|12|0|"
    )

    # HTML retrieval template (credential as ASCII codes)
    HTML_TPL = (
        "7|0|15|https://gerichte.sz.ch/tribunavtplus/|"
        "CAC80118FB77794F1FDFC1B51371CC63|"
        "tribunavtplus.client.zugriff.LoadTableService|getDocumentDetails|"
        "java.lang.String/2004016611|java.util.List|[B/3308590456|"
        "java.lang.Boolean/476441737|Z|TRI|{}||"
        "java.util.ArrayList/4159755760|0|de|"
        "1|2|3|4|10|5|5|5|6|5|7|8|9|5|5|10|11|12|13|0|14|"
        "7|96|53|55|102|102|52|57|97|50|54|55|100|54|100|55|55|55|99|98|"
        "48|102|98|57|101|51|48|97|100|52|49|55|57|98|51|54|50|99|49|49|"
        "57|101|98|102|56|53|53|49|99|98|50|51|102|100|101|49|48|57|57|"
        "102|55|52|48|56|53|48|99|48|100|97|101|54|98|102|49|97|99|99|"
        "50|101|98|57|54|53|48|57|48|55|57|50|49|55|56|100|100|98|102|56|"
        "8|0|0|12|15|"
    )

    # Path decrypt template
    DECRYPT_START = (
        "7|0|11|https://gerichte.sz.ch/tribunavtplus/|"
        "CAC80118FB77794F1FDFC1B51371CC63|"
        "tribunavtplus.client.zugriff.LoadTableService|urlEncodingTribuna|"
        "java.util.Map|java.util.HashMap/1797211028|java.lang.String/2004016611|partURL|"
    )
    DECRYPT_END = "|1|2|3|4|1|5|6|2|7|8|7|9|7|10|7|11|"

    @property
    def court_code(self) -> str:
        return self.COURT_CODE_STR

    @property
    def _gwt_headers(self) -> dict:
        return {
            "Content-Type": "text/x-gwt-rpc; charset=utf-8",
            "X-GWT-Permutation": self.GWT_PERMUTATION,
            "X-GWT-Module-Base": self.GWT_MODULE_BASE,
        }

    # Stop scanning after this many consecutive already-known decisions.
    # Set OCL_SCRAPER_RESCAN_ALL=1 to disable the early-stop and force a
    # full portal scan — useful for one-off catch-ups after extended
    # downtime (e.g., tunnel outages on JU/NE that left historical gaps).
    CONSECUTIVE_KNOWN_LIMIT = (
        999_999 if os.environ.get("OCL_SCRAPER_RESCAN_ALL") else 200
    )

    # ───────────────────────────────────────────────────────────────────────
    # Document-aware identity (2026-09-26)
    # ───────────────────────────────────────────────────────────────────────
    # A Tribuna portal lists every document of a dossier as its own row with
    # its own doc_id: interim rulings (effet suspensif, recusal), the final
    # judgment, written reasons. decision_id is docket-keyed, so once a docket
    # was held every further document under it was skipped for good. JU,
    # classified 2026-09-26 (all 1,192 rows, PDF texts compared): 12 real
    # rulings lost that way, three of them the main judgment of the dossier.
    # Mirrors the NE per-fiche fix (f4b146c9) and BGer (e555b27f).
    #
    # Sidecar state/<court>.docids.txt holds "doc_id<TAB>decision_id" pairs;
    # a pair counts only if its decision_id is in state (state is marked after
    # the durable corpus write), so a crash between fetch and write self-heals.
    # A further document of a held docket gets "<docket>-T<doc_id[:8]>";
    # docket_number stays REAL, and its date comes from its own head (see
    # header_date) so build_fts5's exact (court, docket, date) dedup cannot
    # fold it into the held record. LEGACY MODE (no sidecar and no seed) is
    # byte-for-byte the old behaviour and never writes.
    #
    # DOCID_SEED: a verified doc_id -> decision_id map shipped with the code.
    # When the sidecar is absent it is created from the seed together with a
    # "<court>.docids.fullwalk" marker; while the marker exists discovery walks
    # the whole listing (no early stop), and the marker is removed only when a
    # walk has covered every row — so an interrupted walk resumes next run.
    # Only set it for a court whose portal was classified document by
    # document; an unverified seed would refetch the corpus.
    DOCID_SEED: Path | None = None
    # A doc_id scheme change on the portal would make every held document look
    # new. Cap what one run may treat as hidden documents.
    MAX_NEW_COLLISIONS = 60

    _docids: set | None = None
    _full_walk = False

    @property
    def CACHE_NONE_AS_GAP(self) -> bool:  # noqa: N802 — base_scraper reads it
        # Zero-byte portal files (JU: 16) come back as None on every walk; in
        # doc-id mode cache them as gaps (re-probed after GAP_TTL_DAYS).
        return self._docids is not None

    def _docid_sidecar(self) -> Path:
        return Path(self.state.state_file).with_name(f"{self.court_code}.docids.txt")

    def _fullwalk_marker(self) -> Path:
        return Path(self.state.state_file).with_name(f"{self.court_code}.docids.fullwalk")

    def _load_docids(self) -> None:
        self._docids = None
        self._full_walk = False
        self._claimed_ids: set = set()
        self._collisions = 0
        if getattr(self, "state", None) is None:
            return
        side = self._docid_sidecar()
        if (not side.exists() or side.stat().st_size == 0) and self.DOCID_SEED:
            seed = Path(self.DOCID_SEED)
            if seed.exists():
                tmp = side.with_name(side.name + ".tmp")
                tmp.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
                self._fullwalk_marker().write_text("seeded\n", encoding="utf-8")
                os.replace(tmp, side)
                logger.info(f"[{self.court_code}] doc-id sidecar seeded from {seed.name}; "
                            "this run walks the whole listing")
        if not side.exists() or side.stat().st_size == 0:
            return                                   # legacy
        try:
            text = side.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.error(f"[{self.court_code}] unreadable doc-id sidecar ({e}) — legacy")
            return
        known, dropped = set(), 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            doc, _, did = line.partition("\t")
            if not did or not self.state.is_known(did):
                dropped += 1                         # crash-window pair: retried
                continue
            known.add(doc)
        state_n = len(getattr(self.state, "_seen", []) or [])
        if state_n > 100 and len(known) < state_n // 2:
            logger.error(f"[{self.court_code}] doc-id sidecar holds {len(known)} documents vs "
                         f"{state_n} known ids — refusing half-seeded mode, legacy")
            return
        if dropped:
            logger.info(f"[{self.court_code}] {dropped} doc-id pairs not in state — retried")
        self._docids = known
        self._full_walk = self._fullwalk_marker().exists()

    def _mark_docid(self, doc_id: str, decision_id: str) -> None:
        if self._docids is None or not doc_id or doc_id in self._docids:
            return                                   # legacy never writes
        self._docids.add(doc_id)
        with open(self._docid_sidecar(), "a", encoding="utf-8") as f:
            f.write(f"{doc_id}\t{decision_id}\n")

    def _identity(self, stub: dict) -> str | None:
        """decision_id to fetch this row under, or None when it is held."""
        base = make_decision_id(self.court_code, stub["docket_number"])
        doc = stub.get("doc_id")
        if self._docids is None or not doc:
            return None if self.state.is_known(base) else base
        claimed = self._claimed_ids
        # Plain id unknown first: a crash-window pair must never suppress a
        # new docket's only document.
        if not self.state.is_known(base) and base not in claimed:
            claimed.add(base)
            return base
        if doc in self._docids:
            return None
        alt = make_decision_id(self.court_code, f"{stub['docket_number']}-T{doc[:8]}")
        if self.state.is_known(alt) or alt in claimed:
            return None
        if self._collisions >= self.MAX_NEW_COLLISIONS:
            if self._collisions == self.MAX_NEW_COLLISIONS:
                logger.error(f"[{self.court_code}] more than {self.MAX_NEW_COLLISIONS} "
                             "unknown documents under held dockets in one run — did the "
                             "portal renumber its doc_ids? Holding the rest back.")
                self._collisions += 1
            return None
        self._collisions += 1
        claimed.add(alt)
        return alt

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        self._load_docids()
        known_limit = 999_999 if self._full_walk else self.CONSECUTIVE_KNOWN_LIMIT
        total = None
        page_nr = 0
        total_yielded = 0
        consecutive_known = 0
        errors = 0

        while True:
            body = self.SEARCH_TPL.format(page_nr=page_nr)

            try:
                resp = self.post(self.TRIBUNA_URL, data=body, headers=self._gwt_headers)
            except Exception as e:
                logger.error(f"[{self.court_code}] Search page {page_nr} failed: {e}")
                errors += 1
                if errors > 10:
                    break
                page_nr += 1
                continue

            if page_nr == 0:
                m = _RE_TOTAL.search(resp.text)
                if m:
                    total = int(m.group())
                    known = self.state.count()
                    self.portal_count = total
                    logger.info(f"[{self.court_code}] Portal: {total}, Known: {known}")
                    if total <= known and not self._full_walk:
                        logger.info(
                            f"[{self.court_code}] No new decisions on portal "
                            f"(portal={total}, known={known}), skipping full scan"
                        )
                        return
                else:
                    logger.error(f"[{self.court_code}] Could not parse total from response")
                    break

            stub = self._parse_single_result(resp.text)
            if not stub:
                errors += 1
                if errors > self.MAX_ERRORS:
                    logger.error(f"[{self.court_code}] Too many errors ({errors})")
                    break
                page_nr += 1
                continue

            errors = 0  # reset on success

            if since_date and stub.get("decision_date"):
                d = parse_date(stub["decision_date"])
                if d and d < since_date:
                    page_nr += 1
                    continue

            did = self._identity(stub)
            if did is not None:
                stub["decision_id"] = did
                total_yielded += 1
                consecutive_known = 0
                yield stub
            else:
                consecutive_known += 1
                if consecutive_known >= known_limit:
                    logger.info(
                        f"[{self.court_code}] {known_limit} consecutive "
                        f"known decisions, stopping early (yielded {total_yielded} new)"
                    )
                    break

            page_nr += 1

            if total and page_nr >= total:
                logger.info(f"[{self.court_code}] All {total} results covered")
                if self._full_walk:
                    self._fullwalk_marker().unlink(missing_ok=True)
                    logger.info(f"[{self.court_code}] doc-id full walk complete")
                break

            if page_nr % 200 == 0:
                logger.info(
                    f"[{self.court_code}] Discovery: page {page_nr}/{total}, "
                    f"yielded {total_yielded} new stubs"
                )

        logger.info(f"[{self.court_code}] Discovery complete: {total_yielded} new stubs")

    def _parse_single_result(self, text: str) -> dict | None:
        """Parse a single decision from a GWT-RPC search response."""
        if not text.startswith("//OK"):
            return None

        content = _RE_STRIP_PREFIX.sub("", text)
        werte = _RE_EXTRACT_STRINGS.findall(content)

        if len(werte) < 10:
            logger.warning(f"[{self.court_code}] Too few strings in response: {len(werte)}")
            return None

        # Find doc_id (32-char hex) — usually at index 3, 4, or 5
        korrektur = 0
        doc_id = None
        for offset in (3, 4, 5):
            if offset < len(werte) and _RE_DOC_ID.fullmatch(werte[offset]):
                doc_id = werte[offset]
                korrektur = offset - 3
                break

        if not doc_id:
            logger.warning(f"[{self.court_code}] No doc_id in response")
            return None

        # Extract title (index 4+k)
        title = ""
        idx = 4 + korrektur
        if idx < len(werte):
            title = werte[idx].replace("\\x27", "'")
            if len(title) < 8:
                title = ""

        # Extract docket number (index 5+k, with ±1 tolerance)
        docket = None
        for try_idx in (5 + korrektur, 6 + korrektur, 4 + korrektur):
            if 0 <= try_idx < len(werte) and _RE_DOCKET.fullmatch(werte[try_idx]):
                docket = werte[try_idx]
                if try_idx != 5 + korrektur:
                    korrektur += try_idx - (5 + korrektur)
                break

        if not docket:
            logger.warning(f"[{self.court_code}] No docket number in response")
            return None

        # Extract decision date (index 6+k, with +1 tolerance)
        decision_date = None
        for try_idx in (6 + korrektur, 7 + korrektur):
            if 0 <= try_idx < len(werte) and _RE_DATE.fullmatch(werte[try_idx]):
                decision_date = werte[try_idx]
                if try_idx != 6 + korrektur:
                    korrektur += 1
                break

        # Extract encrypted path (index 8+k, with up to +3 tolerance)
        enc_path = ""
        for try_idx in range(8 + korrektur, min(12 + korrektur, len(werte))):
            if _RE_ENC_PATH.fullmatch(werte[try_idx]):
                enc_path = werte[try_idx]
                break

        return {
            "doc_id": doc_id,
            "docket_number": docket,
            "decision_date": decision_date or "",
            "title": title,
            "enc_path": enc_path,
        }

    # Below this HTML-length threshold the HTML rendering is almost
    # certainly the cover sheet only (docket, parties, Stichwort) and the
    # full reasoning is PDF-only. Trigger a PDF fetch and pick whichever
    # source is richer. STK (Strafkammer) / GPR / BEK decisions are the
    # canonical cover-only case — 925-char HTML vs 3.5k-char PDF for
    # STK 2021 52 (KGer SZ, investigated 2026-04-24).
    HTML_TRUST_MIN = 2000

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch HTML via getDocumentDetails; also try PDF when the HTML
        looks cover-only. Pick whichever source has more content."""
        doc_id = stub.get("doc_id", "")
        docket = stub["docket_number"]

        html_text = self._fetch_html(doc_id)

        pdf_text = ""
        if (not html_text or len(html_text) < self.HTML_TRUST_MIN) and stub.get("enc_path"):
            pdf_text = self._fetch_pdf(stub)

        # Pick the richer source. PDF wins on ties when available because
        # it's the authoritative form of the decision.
        if len(pdf_text) > len(html_text):
            full_text = pdf_text
        else:
            full_text = html_text

        if not full_text or len(full_text) < 50:
            logger.warning(f"[{self.court_code}] No text for {docket}")
            return None

        dd = parse_date(stub.get("decision_date", ""))
        if self._docids is not None:
            own = header_date(full_text)
            base = make_decision_id(self.court_code, docket)
            if stub["decision_id"] != base:
                # A further document of a held docket: the listing date is the
                # dossier's, so only the date it states itself is usable.
                if not own:
                    logger.warning(f"[{self.court_code}] document under held docket "
                                   f"{docket} states no date — skipped (dedup would fold it)")
                    return None
                dd = own
            elif not dd and own:
                dd = own
        if not dd:
            logger.warning(f"[{self.court_code}] No date for {stub['docket_number']}")

        self._mark_docid(doc_id, stub["decision_id"])
        return Decision(
            decision_id=stub["decision_id"],
            court=self.court_code,
            canton=self.CANTON,
            docket_number=docket,
            decision_date=dd,
            language=detect_language(full_text),
            title=stub.get("title"),
            full_text=full_text,
            source_url=f"{self.GWT_MODULE_BASE}",
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    def _fetch_html(self, doc_id: str) -> str:
        """Fetch full text via getDocumentDetails GWT-RPC call."""
        if not doc_id:
            return ""

        body = self.HTML_TPL.format(doc_id)

        try:
            resp = self.post(self.TRIBUNA_URL, data=body, headers=self._gwt_headers)
        except Exception as e:
            logger.warning(f"[{self.court_code}] HTML fetch failed: {e}")
            return ""

        if not resp.text.startswith("//OK"):
            return ""

        # Find "xhtml" marker and extract the HTML string after it
        m = re.search(r'"xhtml","(.*?)"(?:,|\])', resp.text, re.DOTALL)
        if not m:
            return ""

        html_raw = m.group(1)
        # Decode \xNN hex escapes
        html = _RE_HEX_DECODE.sub(lambda match: chr(int(match.group(1), 16)), html_raw)
        html = html.replace("\\n", "\n").replace('\\"', '"')

        # Strip HTML tags to get plain text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove headers/footers
        for tag in soup.find_all(style=re.compile("headerfooter")):
            tag.decompose()

        paragraphs = []
        for el in soup.find_all(["p", "div", "td", "li"]):
            text = el.get_text(strip=True)
            if text and len(text) > 1:
                paragraphs.append(text)

        if paragraphs:
            return "\n\n".join(paragraphs)

        return soup.get_text(separator="\n", strip=True)

    def _fetch_pdf(self, stub: dict) -> str:
        """Fetch PDF via encrypted path + decrypt + download."""
        enc_path = stub.get("enc_path", "")
        docket = stub["docket_number"]
        docket_url = docket.replace(" ", "_")

        if not enc_path:
            return ""

        # Build decrypt request body
        pfad = f"{docket_url}_{enc_path}|dossiernummer|{docket_url}"
        body = self.DECRYPT_START + pfad + self.DECRYPT_END

        try:
            resp = self.post(self.TRIBUNA_URL, data=body, headers=self._gwt_headers)
        except Exception as e:
            logger.warning(f"[{self.court_code}] Decrypt failed for {docket}: {e}")
            return ""

        # Parse decrypted path
        pdf_url = None
        m1 = _RE_DECRYPT_V1.search(resp.text)
        if m1:
            code = m1.group()
            pdf_url = f"{self.DOWNLOAD_URL}{docket_url}?path={code}&pathIsEncrypted=1&dossiernummer={docket_url}"
        else:
            m2 = _RE_DECRYPT_V2.search(resp.text)
            if m2:
                pdf_url = (
                    f"{self.DOWNLOAD_URL}{m2.group('p1')}{m2.group('p2')}"
                    f"?path={m2.group('p2')}&pathIsEncrypted=1"
                    f"&dossiernummer={m2.group('p4')}"
                )

        if not pdf_url:
            logger.warning(f"[{self.court_code}] Could not decrypt path for {docket}")
            return ""

        # Download and extract PDF text
        try:
            r = self.get(pdf_url)
            if r.content[:4] != b"%PDF":
                return ""
            return self._pdf_text(r.content)
        except Exception as e:
            logger.warning(f"[{self.court_code}] PDF download failed for {docket}: {e}")
            return ""

    @staticmethod
    def _pdf_text(data: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                return "\n\n".join(
                    p.extract_text() or "" for p in pdf.pages
                )
        except ImportError:
            pass
        try:
            from pdfminer.high_level import extract_text
            from io import BytesIO
            return extract_text(BytesIO(data))
        except ImportError:
            pass
        return ""
