"""
Appenzell Innerrhoden Courts Scraper (AI Gerichte)
===================================================
Scrapes the published rulings of the Kantonsgericht Appenzell I.Rh. from
www.ai.ch.

Architecture (since the ai.ch relaunch on a headless Plone CMS, 2026):
- Every published ruling is a File in ONE Plone media folder
  (/themen/staat-und-recht/gerichte/rechtsprechung/<32-hex folder id>/),
  2015 to date, 104 files on 2026-11-08. The folder is listed through the
  Plone REST API on the CMS backend (ai-api.webcloud7.ch, the host
  www.ai.ch's own download proxy fetches from); each file downloads from
  <file>/@@download/file.
- /themen/staat-und-recht/gerichte/rechtsprechung shows the latest dozen as a
  table (docket | Gegenstand | listing date). It supplies the subject and the
  date ai.ch put a ruling online, and the folder path.

Until 2026-11-08 discovery kept only listing links ending in ".pdf". Since
~2026-08-18 every link carries a cache-buster ("v-21-2025.pdf?_t=…"), some
have no extension ("k-3-2025") and some point into a trash container that
answers 401; the filter matched nothing and the scraper reported +0 new with
exit 0 for 82 nights (V 14-, V 15-, V 21-, V 7-2025 and K 3-2025 missed).
Discovery now raises at the end when the listing or the folder yields
nothing, so a parse break exits 1 and shows as FAILED in scraper_health.

Ids: new rows take the entscheidsuche shape (ai_gerichte_V_21-2025) so a
direct row replaces its entscheidsuche copy under the same id. Rows written
before 2026-11-08 keep their ids ("ai_gerichte_V 18-2025",
"ai_gerichte_v-9-2025"); both shapes count as known.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Iterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ai.ch"
API_BASE = "https://ai-api.webcloud7.ch"
LISTING_PATH = "/themen/staat-und-recht/gerichte/rechtsprechung"
LISTING_URL = f"{BASE_URL}{LISTING_PATH}"
# The media folder as of 2026-11-08; the listing's links name it, and win.
DEFAULT_FOLDER_PATH = f"{LISTING_PATH}/4e6dead68fcb49b5a80fe5a1ea15232d"
_FOLDER_RE = re.compile(re.escape(LISTING_PATH) + r"/([0-9a-f]{32})(?=/)")

RE_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")
# A docket as the court writes it: "V 21-2025", "KE 19-2025", "V 10 2017",
# "V 6-2020.pdf" (file titles), "K 7-2021".
_DOCKET_RE = re.compile(r"^\s*([A-Za-z]{1,4})\s*(\d{1,3})\s*[-\s_]\s*((?:19|20)\d{2})\b")
# The same docket in a file id: "v-21-2025.pdf", "v-6_2020.pdf",
# "aktuelle-rechtssprechung-v-6-2017.pdf", "v-9-2024-1" (a second upload).
_SLUG_RE = re.compile(r"(?:^|-)([a-z]{1,4})-(\d{1,3})[-_]((?:19|20)\d{2})(?:-\d+)?(?:\.pdf)?$", re.I)


def parse_docket(text: str | None) -> tuple[str, int, str] | None:
    """("V", 21, "2025") from "V 21-2025", "V 10 2017", "V 6-2020.pdf"."""
    m = _DOCKET_RE.match(text or "")
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2)), m.group(3)


def parse_slug(file_id: str | None) -> tuple[str, int, str] | None:
    m = _SLUG_RE.search((file_id or "").strip("/").split("/")[-1])
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2)), m.group(3)


def docket_str(parts: tuple[str, int, str]) -> str:
    return f"{parts[0]} {parts[1]}-{parts[2]}"


def decision_id_for(parts: tuple[str, int, str]) -> str:
    """The entscheidsuche id shape: ai_gerichte_V_21-2025."""
    return f"ai_gerichte_{parts[0]}_{parts[1]}-{parts[2]}"


def legacy_ids(parts: tuple[str, int, str]) -> list[str]:
    """Ids rows written before 2026-11-08 carry for the same docket."""
    letters, num, year = parts
    return [f"ai_gerichte_{letters} {num}-{year}",
            f"ai_gerichte_{letters.lower()}-{num}-{year}"]


def _strip_query(href: str) -> str:
    return urlsplit(href).path.rstrip("/")


_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
# The court's own source line, at the end of each published ruling:
# "Kantonsgericht Appenzell I.Rh., Entscheid K 7-2021 vom 16. November 2021",
# "..., Verwaltungsgericht,\nEntscheid V 8-2024 vom 03.12.2024". The docket may
# be wrapped ("KSE 15-\n2025").
_OWN_LINE_RE = re.compile(
    r"\b(?:Entscheid|Zwischenentscheid|Endentscheid|Urteil|Beschluss|Verfügung)\s+"
    r"(?P<docket>[A-Z]{1,4}\s?\d{1,4}\s?[-/]\s?\d{2,4})\s+vom\s+"
    r"(?:(?P<d>\d{1,2})\.\s*(?P<m>" + _MONTH_ALT + r")\s*(?P<y>(?:19|20)\d{2})"
    r"|(?P<nd>\d{1,2})\.(?P<nm>\d{1,2})\.(?P<ny>(?:19|20)\d{2}))(?!\d)",
    re.IGNORECASE)


def _docket_key(docket: str | None) -> str:
    """'K 7-2021', 'K_7-2021', 'kse-2-2025', 'KE 63/06' -> 'K72021' ..."""
    return re.sub(r"[\s_/-]", "", docket or "").upper()


def own_decision_date(text: str | None, docket: str | None) -> date | None:
    """The date the ruling's own source line gives for ``docket``, or None.

    The listing on ai.ch dates an entry by when it was put online, weeks or
    months after the ruling: K 7-2021 of 16 November 2021 was listed on
    19 January 2022, after the Federal Supreme Court had already declined to
    hear the appeal (report 2026-10-24). Only a line naming the row's own
    docket counts; the text also names the decisions it reviews and the
    Federal Supreme Court's ruling on it. The last such line wins (the source
    line closes the ruling)."""
    key = _docket_key(docket)
    if not text or not key:
        return None
    found = None
    for m in _OWN_LINE_RE.finditer(text):
        if _docket_key(m.group("docket")) != key:
            continue
        try:
            if m.group("y"):
                found = date(int(m.group("y")), _MONTHS[m.group("m").lower()], int(m.group("d")))
            else:
                found = date(int(m.group("ny")), int(m.group("nm")), int(m.group("nd")))
        except (ValueError, KeyError):
            continue
    if found is None:
        return None
    year = re.search(r"[-/_ ]((?:19|20)\d{2})$", (docket or "").strip())
    if year and found.year < int(year.group(1)):
        return None
    return found


def _parse_swiss_date(text):
    if not text:
        return None
    m = RE_DATE.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def parse_listing(html: str) -> tuple[dict, set[str]]:
    """Rows of the ai.ch Rechtsprechung table and the media folders it links.

    A row reads "V 21-2025 | Ergänzungsleistungen, … | 26.10.2026". Links
    carry a cache-buster (?_t=…), may lack ".pdf", and may point into a
    cms_trash_container (401) while the file itself sits in the media folder:
    the file id (last path segment) is what counts, not the link."""
    soup = BeautifulSoup(html, "html.parser")
    rows: dict = {}
    folders: set[str] = set()
    for a in soup.find_all("a", href=True):
        path = _strip_query(a["href"])
        if path.startswith("http"):
            path = urlsplit(path).path.rstrip("/")
        fm = _FOLDER_RE.search(path)
        if fm:
            folders.add(f"{LISTING_PATH}/{fm.group(1)}")
        file_id = path.split("/")[-1]
        parts = parse_docket(a.get_text(" ", strip=True))
        if not parts or not parse_slug(file_id):
            continue
        tr = a.find_parent("tr")
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] if tr else []
        listed = _parse_swiss_date(cells[-1]) if cells else None
        subject = None
        if len(cells) >= 3:
            subject = " ".join(c for c in cells[1:-1] if c) or None
        rows.setdefault(parts, {"file_id": file_id, "listed": listed, "subject": subject})
    return rows, folders


_PAGE_MARK_RE = re.compile(r"^\s*\d{1,3}\s*-\s*\d{1,3}\s*\n")
_ERWAEGUNGEN_RE = re.compile(r"\n\s*Erw(?:ä|ae)gungen\s*:?\s*\n")


def head_regeste(text: str | None) -> str | None:
    """The subject line and headnote the court prints above "Erwägungen:".

    A ruling on ai.ch opens with a page mark ("1 - 5"), the subject
    ("IVG-Beschwerde") and a headnote paragraph, then "Erwägungen:". That
    block, verbatim line by line, is the regeste; None when the ruling does
    not have that shape."""
    if not text:
        return None
    head = text[:8000]
    m = _ERWAEGUNGEN_RE.search(head)
    if not m:
        return None
    block = _PAGE_MARK_RE.sub("", head[:m.start()].lstrip(" \n"), count=1)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    regeste = "\n".join(lines)
    if len(regeste) < 10 or len(regeste) > 6000:
        return None
    return regeste


class AIGerichteScraper(BaseScraper):
    """
    Scraper for Appenzell Innerrhoden court decisions.

    Strategy: list the Plone media folder (every ruling) and the public
    listing (subject, listing date), download each new PDF.
    """

    REQUEST_DELAY = 2.0
    TIMEOUT = 30
    MAX_ERRORS = 20

    @property
    def court_code(self):
        return "ai_gerichte"

    # ── discovery ───────────────────────────────────────────────────────

    def _listing_rows(self) -> tuple[dict, set[str]]:
        """{(letters, num, year): row} from the public listing, and the
        media-folder paths its links name. Raises on a failed fetch."""
        r = self.get(LISTING_URL)
        # Served as "text/html" without a charset; requests would decode the
        # UTF-8 page as Latin-1 ("ErgÃ¤nzungsleistungen").
        r.encoding = "utf-8"
        return parse_listing(r.text)

    def _folder_items(self, folder_path: str) -> list[dict]:
        """Every File in the media folder, via the Plone REST API."""
        items: list[dict] = []
        url = f"{API_BASE}{folder_path}?b_size=500"
        for _ in range(20):                       # batches of 500; 104 today
            r = self.get(url, headers={"Accept": "application/json"})
            data = r.json()
            items.extend(i for i in data.get("items", []) if i.get("@type") == "File")
            url = (data.get("batching") or {}).get("next")
            if not url:
                break
        return items

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        failures: list[str] = []
        listing: dict = {}
        folders: set[str] = set()
        try:
            listing, folders = self._listing_rows()
        except Exception as e:
            failures.append(f"listing {LISTING_URL} failed: {e}")
        else:
            if not listing:
                failures.append(f"listing {LISTING_URL} parsed 0 rulings (page restructured?)")

        folder_path = sorted(folders)[0] if folders else DEFAULT_FOLDER_PATH
        if len(folders) > 1:
            logger.warning(f"AI: listing names {len(folders)} media folders: {sorted(folders)}")
        files: list[dict] = []
        try:
            files = self._folder_items(folder_path)
        except Exception as e:
            failures.append(f"media folder {folder_path} search failed: {e}")
        else:
            if not files:
                failures.append(f"media folder {folder_path} listed 0 files (moved?)")

        # One stub per docket: the folder first (it has every file), then a
        # listing row whose file the folder did not list.
        stubs: dict[tuple, str] = {}
        for item in files:
            # The @id path, as the CMS encodes it ("KE%2025-2018.pdf").
            path = urlsplit(item.get("@id") or "").path.rstrip("/")
            parts = parse_docket(item.get("title")) or parse_slug(path)
            if not path or not parts or parts in stubs:
                continue                          # v-9-2024-1: second upload of V 9-2024
            stubs[parts] = path
        for parts, row in listing.items():
            if parts not in stubs:
                stubs[parts] = f"{folder_path}/{row['file_id']}"

        total = 0
        for parts in sorted(stubs, key=lambda p: (p[2], p[1]), reverse=True):
            decision_id = decision_id_for(parts)
            if any(self.state.is_known(i) for i in [decision_id, *legacy_ids(parts)]):
                continue
            row = listing.get(parts, {})
            listed = row.get("listed")
            if since_date and listed and listed < since_date:
                continue
            base = stubs[parts]
            total += 1
            yield {
                "decision_id": decision_id,
                "docket_number": docket_str(parts),
                "decision_date": listed,
                "subject": row.get("subject"),
                "url": f"{BASE_URL}{base}",
                "pdf_url": f"{API_BASE}{base}/@@download/file",
            }

        logger.info(
            f"AI: discovery complete: {total} new stubs "
            f"({len(files)} files in the media folder, {len(listing)} listed)")
        if failures:
            # After yielding: whatever one source found is still fetched and
            # written; the run then exits 1 so scraper_health shows FAILED.
            for f in failures:
                logger.error(f"AI: {f}")
            raise RuntimeError("AI discovery incomplete: " + "; ".join(failures))

    # ── fetch ───────────────────────────────────────────────────────────

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract text."""
        pdf_url = stub.get("pdf_url")
        if not pdf_url:
            return None

        full_text = ""
        fetched = False
        try:
            r = self.get(pdf_url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000 and r.content[:5] == b"%PDF-":
                fetched = True
                full_text = self._extract_pdf_text(r.content)
        except Exception as e:
            logger.warning(f"AI: PDF download failed for {stub['docket_number']}: {e}")

        # No placeholder rows (until 2026-09-14 the title, or "[Text extraction
        # failed …]", was stored as the full text): a failed download is retried
        # next run, a fetched PDF without a text layer is cached as a gap.
        if not fetched:
            return None
        if not full_text or len(full_text) < 50:
            logger.warning(
                f"AI: no usable text for {stub['docket_number']} — cached as gap for "
                f"{self.state.GAP_TTL_DAYS} days"
            )
            self.state.mark_gap(stub["decision_id"])
            return None

        # The ruling's own line first; the listing date is when ai.ch put it
        # online, so it stays only as the publication date.
        listed = stub.get("decision_date")
        decision_date = own_decision_date(full_text, stub["docket_number"]) or listed
        if not decision_date:
            logger.warning(f"[ai_gerichte] No date for {stub['docket_number']}")

        language = detect_language(full_text) if len(full_text) > 100 else "de"
        regeste = head_regeste(full_text)
        subject = stub.get("subject") or (regeste.split("\n", 1)[0] if regeste else None)

        return Decision(
            decision_id=stub["decision_id"],
            court="ai_gerichte",
            canton="AI",
            docket_number=stub["docket_number"],
            decision_date=decision_date,
            publication_date=listed if listed and listed != decision_date else None,
            language=language,
            title=subject or stub["docket_number"],
            regeste=regeste or subject,
            full_text=full_text,
            source_url=stub.get("url"),
            pdf_url=stub.get("pdf_url"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes."""
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text())
            doc.close()
            return "\n\n".join(pages)
        except ImportError:
            pass

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

        return ""
