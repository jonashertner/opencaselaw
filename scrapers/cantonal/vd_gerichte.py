"""
Vaud Courts Scraper (VD Gerichte)
==================================
Scrapes court decisions from the Canton de Vaud REST API at
prestations.vd.ch/pub/101623/api/.

Architecture:
- GET /pub/101623/ → session cookies + XSRF-TOKEN
- POST /pub/101623/api/search (JSON) → paginated results (Spring Data Page)
- GET /pub/101623/api/decision/download/{uuid} → PDF

XSRF Protection:
- The XSRF-TOKEN cookie must be sent as X-XSRF-TOKEN header on POST requests.
- Without it, POST returns 403 Forbidden.

Search API:
- Date range is required to get results (empty search returns 0)
- Max pageSize: 100
- sortBy: "DATE_DE_DECISION" or "PERTINENCE"
- queryTarget: "ALL", "DECISION", "RESUME", "CAUSE"
- Date format: {"from": [YYYY, MM, DD], "to": [YYYY, MM, DD]}

Volume: ~3,500-4,000 decisions per year. Total ~10,000+ since 2020.
Platform: Spring Boot REST API with Angular SPA frontend.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://prestations.vd.ch/pub/101623"
API_URL = f"{BASE_URL}/api"

# Monthly iteration to stay under 10,000 result cap
# Earliest year with decisions
START_YEAR = 2007

# The portal's stable identity for a decision is its uuid (decisionHit.id); it
# is also the last path segment of the pdf_url stored on every held record.
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Corpus shard the scraper appends to (run_scraper.py); used once to seed the
# uuid sidecar when it is missing. Override for tests / non-standard layouts.
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARD_ENV = "VD_GERICHTE_SHARD"


# What the listing's decisionHit.numero holds when it is only the ruling's
# per-year number: "344", "111/I" (chamber section), "47bis".
RULING_NO_RE = re.compile(r"^\d+(?:/[IVX]+|bis|ter)?$", re.IGNORECASE)

# ── the case number in the ruling's own header ───────────────────────────
# Since 2026-09-04 the listing carries no case number (affaireHit.numero and
# .cause are null on every hit). The PDF prints it above the chamber line:
#   TRIBUNAL CANTONAL / 344 / DA21.000826-LAS / CHAMBRE DES RECOURS PENALE
# Only the header is read: the body cites the case numbers of other rulings.
# New rulings no longer print it either ("PE25.***-***", the norm since
# 2025-12); those fall back to chamber, year and number (_resolve_docket).
_HEADER_LINES = 8
_HEADER_END_RE = re.compile(
    r"\b(?:arr[êe]t|s[ée]ance|audience|jugement|d[ée]cision|ordonnance|"
    r"composition|pr[ée]sidence|statuant|greffi)|\bjuges?\s*:|_{4}",
    re.IGNORECASE,
)
# "DA21.000826-LAS", "PE.06.006465-AME", "LY 13.008197", "TD19-007793-210984";
# pdfplumber reads the capital I of "JI19.044023" as a lower-case l. Six digits
# exactly: "PE13.0022035" is a misprint. Slash references in the same place
# ("RPE/01/14/0001385/ds") are the prosecutor's, not the court's number.
_DOSSIER_RE = re.compile(
    r"(?<![\w./])([A-Z][A-Z0-9l]|\d[A-Z])[. ]?(\d{2})[.-]? ?(\d{6})(?![\d*])"
)


def dossier_from_header(text: str | None) -> str | None:
    """The case number printed in a ruling's header, or None when the header
    has none (2009-2011 rulings) or masks it."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    header = []
    for i, line in enumerate(lines[:_HEADER_LINES]):
        if i and _HEADER_END_RE.search(line):
            break
        header.append(line)
    for line in header:
        m = _DOSSIER_RE.search(line)
        if m:
            prefix = m.group(1).replace("l", "I")
            return f"{prefix}{m.group(2)}.{m.group(3)}"
    return None


def uuid_from_pdf_url(url: str | None) -> str | None:
    """Return the decision uuid encoded in a pdf_url, or None."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1].strip().lower()
    return tail if UUID_RE.match(tail) else None


def iter_uuid_ids(shard: Path) -> Iterator[tuple[str, str]]:
    """Stream (uuid, decision_id) pairs from a corpus shard, in file order.
    Torn or unparseable lines and records without a pdf uuid are skipped."""
    with open(shard, "rb") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            u = uuid_from_pdf_url(rec.get("pdf_url"))
            if u:
                yield u, rec.get("decision_id") or ""


class VDGerichteScraper(BaseScraper):
    """
    Scraper for Canton de Vaud court decisions via REST API.

    Uses monthly date-range windows, paginating within each month.
    Downloads PDF for each decision.
    """

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 50

    @property
    def court_code(self):
        return "vd_gerichte"

    def _init_session(self) -> bool:
        """Initialize session: get cookies and XSRF token."""
        try:
            r = self.session.get(f"{BASE_URL}/", timeout=self.TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"VD: failed to init session: {e}")
            return False

        xsrf = self.session.cookies.get("XSRF-TOKEN")
        if not xsrf:
            logger.error("VD: no XSRF-TOKEN cookie received")
            return False

        self.session.headers["X-XSRF-TOKEN"] = xsrf
        self.session.headers["Accept"] = "application/json, text/plain, */*"
        self.session.headers["Origin"] = "https://prestations.vd.ch"
        self.session.headers["Referer"] = f"{BASE_URL}/"
        logger.info("VD: session initialized, XSRF token acquired")
        return True

    def _search(self, date_from: list[int], date_to: list[int], page: int = 0) -> dict | None:
        """Execute a search API call. Returns parsed JSON or None."""
        body = {
            "page": page,
            "pageSize": 100,
            "sortBy": "DATE_DE_DECISION",
            "queryTarget": "ALL",
            "query": "",
            "modelesDecision": [],
            "resultatsDecision": [],
            "naturesAffaire": [],
            "compositionsCour": [],
            "autoritesDirectrice": [],
            "juges": [],
            "greffiers": [],
            "resultatsRecours": [],
            "jurivoc": {"inclusions": [], "exclusions": []},
            "articlesDeLoi": {"inclusions": [], "exclusions": []},
            "datePublication": {"from": None, "to": None},
            "dateDecision": {"from": date_from, "to": date_to},
        }

        self._rate_limit()
        try:
            r = self.session.post(
                f"{API_URL}/search",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=self.TIMEOUT,
            )
            if r.status_code == 403:
                # XSRF token may have expired, refresh
                logger.warning("VD: 403 on search, refreshing session")
                if self._init_session():
                    r = self.session.post(
                        f"{API_URL}/search",
                        json=body,
                        headers={"Content-Type": "application/json"},
                        timeout=self.TIMEOUT,
                    )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"VD: search failed: {e}")
            return None

    # ── uuid-keyed identity ──────────────────────────────────────────────
    # 2026-09-04: prestations.vd.ch stopped returning affaireHit.numero (null
    # on every hit, all years). Ids were docket-keyed from that number, so
    # the fallback (decisionHit.numero) minted NEW ids for decisions the
    # corpus already served under their ZD number — 8,133 duplicates in one
    # night — and its per-year sequence numbers ("641") collide across years,
    # which makes genuinely new rulings look known and never fetched.
    #
    # Sidecar state/vd_gerichte.uuids.txt, one "<uuid>\t<decision_id>" line
    # per held decision. Seeded once from the corpus shard (first id per
    # uuid = the id the corpus has served longest) when the sidecar is
    # missing — automatically at startup if the shard is readable, or with
    # scripts/vd_uuid_sidecar.py --seed — and appended after every durable
    # write (mark_run_complete). A listing whose uuid is held is skipped
    # whatever id it would mint. No sidecar and no shard = the old
    # docket-keyed is_known() check alone, never an error.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._load_known_uuids()

    def _uuid_sidecar(self) -> Path:
        state_dir = getattr(self, "state_dir", None) or Path("state")
        return Path(state_dir) / f"{self.court_code}.uuids.txt"

    def _shard_path(self) -> Path:
        return Path(os.environ.get(
            SHARD_ENV, REPO_ROOT / "output" / "decisions" / "vd_gerichte.jsonl"))

    def _seed_from_shard(self) -> int:
        """Write the sidecar from the corpus shard (first id per uuid).
        Returns the number of uuids written; 0 when no shard is readable."""
        shard = self._shard_path()
        try:
            if not shard.is_file() or shard.stat().st_size == 0:
                return 0
            first: dict[str, str] = {}
            for u, did in iter_uuid_ids(shard):
                first.setdefault(u, did)
        except OSError as e:
            logger.warning(f"[vd_gerichte] cannot read {shard} to seed the uuid sidecar: {e}")
            return 0
        p = self._uuid_sidecar()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"# seeded from {shard} — <uuid>\\t<decision_id>, first id per uuid\n")
            for u, did in first.items():
                f.write(f"{u}\t{did}\n")
        os.replace(tmp, p)
        logger.info(f"[vd_gerichte] uuid sidecar seeded from {shard}: {len(first)} uuids")
        return len(first)

    def _load_known_uuids(self) -> None:
        self._known_uuids: dict[str, str] = {}
        p = self._uuid_sidecar()
        if not p.exists() and self._seed_from_shard() == 0:
            logger.info(
                "[vd_gerichte] uuid sidecar missing and no shard to seed from — "
                "docket-keyed identity only (scripts/vd_uuid_sidecar.py --seed)")
            return
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t", 1)
                    u = parts[0].strip().lower()
                    if not UUID_RE.match(u):
                        continue
                    self._known_uuids.setdefault(
                        u, parts[1].strip() if len(parts) > 1 else "")
        except OSError as e:
            logger.warning(
                f"[vd_gerichte] unreadable uuid sidecar ({e}) — docket-keyed "
                "identity only; reseed to recover")
            self._known_uuids = {}
            return
        logger.info(f"[vd_gerichte] uuid sidecar: {len(self._known_uuids)} held uuids")

    def _mark_uuid(self, uuid: str | None, decision_id: str) -> None:
        """Record a durably written decision's uuid (idempotent, append-only)."""
        known = getattr(self, "_known_uuids", None)
        if known is None:
            known = self._known_uuids = {}
        u = (uuid or "").strip().lower()
        if not UUID_RE.match(u) or u in known:
            return
        known[u] = decision_id
        p = self._uuid_sidecar()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{u}\t{decision_id}\n")

    def _is_new(self, stub: dict) -> bool:
        """Unknown by id AND by uuid. The uuid check is what survives a
        portal that changes how it numbers cases."""
        if self.state.is_known(stub["decision_id"]):
            return False
        known = getattr(self, "_known_uuids", None) or {}
        return (stub.get("uuid") or "").strip().lower() not in known

    def on_decision_persisted(self, decision) -> None:
        """Per-decision hook run_scraper.py calls right after it marks state
        (it never calls mark_run_complete): append the decision's uuid so the
        sidecar keeps growing between seeds. Night 1 (2026-09-05) showed the
        gap — seeded once at 01:37, then flat through 5,589 new decisions."""
        self._mark_uuid(uuid_from_pdf_url(getattr(decision, "pdf_url", None)),
                        decision.decision_id)

    def mark_run_complete(self, decisions: list) -> None:
        """State first (durable), then the uuid sidecar (BaseScraper.run callers)."""
        super().mark_run_complete(decisions)
        for d in decisions:
            self.on_decision_persisted(d)

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        if not self._init_session():
            return

        today = date.today()
        start_year = since_date.year if since_date else START_YEAR
        start_month = since_date.month if since_date else 1

        total_yielded = 0

        # Iterate month by month, newest first
        for year in range(today.year, start_year - 1, -1):
            end_month = today.month if year == today.year else 12
            begin_month = start_month if year == start_year else 1

            for month in range(end_month, begin_month - 1, -1):
                # Last day of month
                if month == 12:
                    last_day = 31
                else:
                    next_month = date(year, month + 1, 1)
                    last_day = (next_month - timedelta(days=1)).day

                date_from = [year, month, 1]
                date_to = [year, month, last_day]

                logger.info(f"VD: searching {year}-{month:02d}")

                data = self._search(date_from, date_to, page=0)
                if not data:
                    continue

                response = data.get("response", {})
                total_elements = response.get("totalElements", 0)
                total_pages = response.get("totalPages", 0)

                if total_elements == 0:
                    logger.debug(f"VD: {year}-{month:02d}: no results")
                    continue

                logger.info(f"VD: {year}-{month:02d}: {total_elements} decisions, {total_pages} pages")

                # Process page 0
                for stub in self._parse_search_page(response):
                    if self._is_new(stub):
                        total_yielded += 1
                        yield stub

                # Process remaining pages
                for page in range(1, total_pages):
                    data = self._search(date_from, date_to, page=page)
                    if not data:
                        break
                    response = data.get("response", {})
                    for stub in self._parse_search_page(response):
                        if self._is_new(stub):
                            total_yielded += 1
                            yield stub

        logger.info(f"VD: discovery complete: {total_yielded} new stubs")

    def _parse_search_page(self, response: dict) -> Iterator[dict]:
        """Parse decisions from a search response page."""
        content = response.get("content", [])
        for item in content:
            try:
                stub = self._parse_search_item(item)
                if stub:
                    yield stub
            except Exception as e:
                logger.debug(f"VD: parse error: {e}")

    def _parse_search_item(self, item: dict) -> dict | None:
        """Parse a single search result item into a stub dict."""
        hit = item.get("decisionHit", {})
        if not hit:
            return None

        uuid = hit.get("id")
        if not uuid:
            return None

        affaire = hit.get("affaireHit") or {}
        affaire_no = str(affaire.get("numero") or "").strip()
        hit_no = str(hit.get("numero") or "").strip()
        ruling_no = None
        if affaire_no:
            # Case number ("ZD17.028583"): the historical id scheme.
            docket, id_key = affaire_no, affaire_no
        elif hit_no and not RULING_NO_RE.match(hit_no):
            # A real docket ("AI 210/17 - 249/2017") is unique on its own.
            docket, id_key = hit_no, hit_no
        else:
            # Bare per-year sequence number, or nothing: collides across
            # years, so the id is keyed on the portal's stable uuid. The
            # docket is settled in fetch_decision, with the PDF in hand.
            docket, id_key = (hit_no or uuid), uuid
            ruling_no = docket

        # Parse decision date
        date_str = hit.get("dateDecision", "")
        decision_date = parse_date(date_str)

        # Parse publication date
        pub_str = hit.get("datePublication", "")
        publication_date = parse_date(pub_str)

        # Authority info
        autorite = affaire.get("autoriteDirectrice", "")
        chamber = affaire.get("autoritePremiereInstance", "")

        # Judges
        judges_list = affaire.get("jugesAbreviation", [])
        judges = ", ".join(judges_list) if judges_list else None

        # Clerks
        clerks_list = affaire.get("greffiersAbreviation", [])
        clerks = ", ".join(clerks_list) if clerks_list else None

        # Legal area / nature
        nature = hit.get("natureAffaire", "")

        # Résumé
        resume = hit.get("resume", "")

        # Articles de loi
        articles = hit.get("articlesDeLoi", {})
        articles_str = ""
        if articles:
            parts = []
            for law, arts in articles.items():
                for art in arts:
                    parts.append(f"{art} {law}")
            articles_str = "; ".join(parts)

        # Outcome
        resultats = hit.get("resultats", [])
        outcome = "; ".join(resultats) if resultats else None

        # Jurivoc concepts
        concepts = hit.get("conceptsJurivoc", [])

        decision_id = make_decision_id("vd_gerichte", id_key)

        return {
            "decision_id": decision_id,
            "docket_number": docket,
            "ruling_no": ruling_no,
            "uuid": uuid,
            "decision_date": decision_date,
            "publication_date": publication_date,
            "autorite": autorite,
            "chamber": chamber,
            "judges": judges,
            "clerks": clerks,
            "nature": nature,
            "resume": resume,
            "articles": articles_str,
            "outcome": outcome,
            "concepts": concepts,
            "url": f"{API_URL}/decision/download/{uuid}",
        }

    @staticmethod
    def _resolve_docket(stub: dict, full_text: str, decision_date) -> tuple[str, str | None]:
        """(docket_number, docket_number_2) for a decision.

        A docket from the listing stands. Otherwise the case number in the
        PDF header, else "CREP 2021/344": rulings are cited by chamber, date
        and number ("CREP 19 avril 2021/344"), and the bare number repeats
        every year. docket_number_2 keeps the listing's value either way: one
        case number carries several rulings, up to four on one day, so the
        build keys vd_gerichte on it (build_fts5.DECISION_NUMBER_COURTS).
        """
        ruling_no = stub.get("ruling_no")
        if not ruling_no:
            return stub["docket_number"], None
        dossier = dossier_from_header(full_text)
        if dossier:
            return dossier, ruling_no
        chamber = (stub.get("autorite") or "").strip()
        if chamber and decision_date and ruling_no != stub.get("uuid"):
            return f"{chamber} {str(decision_date)[:4]}/{ruling_no}", ruling_no
        return ruling_no, ruling_no

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch PDF and extract text for a single decision."""
        uuid = stub.get("uuid")
        if not uuid:
            logger.warning(f"VD: no UUID for {stub['docket_number']}")
            return None

        # Download PDF
        pdf_url = f"{API_URL}/decision/download/{uuid}"
        try:
            self._rate_limit()
            r = self.session.get(pdf_url, timeout=self.TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"VD: PDF download failed for {stub['docket_number']}: {e}")
            return None

        content_type = r.headers.get("Content-Type", "")
        if "pdf" not in content_type and len(r.content) < 100:
            logger.warning(f"VD: unexpected content type for {stub['docket_number']}: {content_type}")
            return None

        # Extract text from PDF
        full_text = self._extract_pdf_text(r.content)
        if not full_text:
            # Use résumé as fallback
            full_text = stub.get("resume", "")
            if not full_text:
                full_text = f"[PDF text extraction failed for {stub['docket_number']}]"

        decision_date = stub.get("decision_date")
        if not decision_date:
            decision_date = stub.get("publication_date")
        if not decision_date:
            logger.warning(f"VD: no date for {stub['docket_number']}")

        language = detect_language(full_text) if len(full_text) > 100 else "fr"
        docket_number, docket_number_2 = self._resolve_docket(stub, full_text, decision_date)

        return Decision(
            decision_id=stub["decision_id"],
            court="vd_gerichte",
            canton="VD",
            chamber=stub.get("autorite"),
            docket_number=docket_number,
            docket_number_2=docket_number_2,
            decision_date=decision_date,
            publication_date=stub.get("publication_date"),
            language=language,
            title=stub.get("nature"),
            legal_area=stub.get("nature"),
            regeste=stub.get("resume") or None,
            full_text=full_text,
            outcome=stub.get("outcome"),
            judges=stub.get("judges"),
            clerks=stub.get("clerks"),
            source_url=f"{BASE_URL}/",
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes using pdfplumber or PyPDF2."""
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except ImportError:
            pass

        try:
            from PyPDF2 import PdfReader
            import io
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
        except ImportError:
            pass

        logger.warning("VD: no PDF extraction library available (install pdfplumber or PyPDF2)")
        return ""
