"""
PostCom Scraper (Eidgenössische Postkommission)
=================================================

Scrapes published Verfügungen from the Swiss Federal Postal Commission
(PostCom) at postcom.admin.ch.

Architecture (since the 2026 admin.ch relaunch):
- /de/dokumentation/verfuegungen is now a hub page without decisions ("Mehr über
  «Verfügungen»"). The old <p>-paragraph parser found 0 entries there and the nightly
  run reported "+0 new" with exit 0 until 2026-09-15.
- /de/verfuegungen lists every Verfügung from 2013 on as admin.ch DAM download tiles
  (a.download-item, title "13.05.2026 - Verfügung 8/2026 betreffend ... - rechtskräftig")
  under <h2> year headings.
- /de/strafbescheide lists the Strafbescheide wegen Verletzung der Meldepflicht (2020 on),
  one tile per decision in the addressee's language. The tile's meta date is the upload
  date; the decision date is in the title, most titles carry a case number
  "PostCom-413-6/5".
- Every PDF moved from /inhalte/PDF/Verfuegungen/ to /dam/.../sd-web/<hash>/, so the URL is
  no identity. Verfügungen are keyed by number, year and kind (VFG-8-2026, -beilage,
  -liste) and resolved against the corpus shard first: held rows carry ids from older
  parser rules (VFG-09-2023, slug ids, -liste suffixes on main decisions) that the new
  titles no longer reproduce.

Entry formats in tile titles:
  "DD.MM.YYYY - Verfügung NN/YYYY betreffend Subject - status", "D.M.YYYY - ...",
  "Verfügung 1 / 2013 ...", "Décision 10-2014 concernant ...",
  "Liste ... Beilage zur Verfügung 2/2026", "Strafbescheid wegen Verletzung der
  Meldepflicht, 9. Mai 2022 (PostCom-413-6/5, in französischer Sprache)"

Coverage (2026-09-15): 284 Verfügungen PDFs (2013-2026), 47 Strafbescheide (2020-2025)
Rate limiting: 2.0 seconds (PDF downloads)
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)
from scrapers.elcom import PUB_DATE_PATTERN

logger = logging.getLogger(__name__)

BASE_URL = "https://www.postcom.admin.ch"
LISTING_URL = f"{BASE_URL}/de/dokumentation/verfuegungen"  # hub since the 2026 relaunch, kept for reference
VERFUEGUNGEN_URL = f"{BASE_URL}/de/verfuegungen"
STRAFBESCHEIDE_URL = f"{BASE_URL}/de/strafbescheide"
LISTING_PAGES = ((VERFUEGUNGEN_URL, "Verfügung"), (STRAFBESCHEIDE_URL, "Strafbescheid"))

REPO_ROOT = Path(__file__).resolve().parents[1]
# Corpus shard read to resolve tiles to held rows. Override for tests / other layouts.
SHARD_ENV = "POSTCOM_SHARD"

# Extract Verfügung/Décision/Decisione number and year
VFG_NUMBER_PATTERN = re.compile(
    r"(?:Verfügung|Décision|Decisione)\s*[_\s]*"
    r"(?:Nr?\.?\s*|n[°o]?\s*)?"
    r"(\d+)\s*[/_-]\s*(\d{4})",
    re.IGNORECASE,
)

# Leading date: "DD.MM.YYYY" or "D.M.YYYY" ("13.5.2026 - Verfügung 6/2026") at start of a title
LEADING_DATE = re.compile(r"^(\d{1,2}\.\d{1,2}\.\d{4})")

# Status patterns
STATUS_PATTERN = re.compile(
    r"(rechtskräftig|nicht\s+rechtskräftig|noch\s+nicht\s+rechtskräftig"
    r"|nicht\s+rechtkräftig)",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    """Create a filesystem-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[äÄ]", "ae", text)
    text = re.sub(r"[öÖ]", "oe", text)
    text = re.sub(r"[üÜ]", "ue", text)
    text = re.sub(r"[éèê]", "e", text)
    text = re.sub(r"[àâ]", "a", text)
    text = re.sub(r"[ùû]", "u", text)
    text = re.sub(r"[ôò]", "o", text)
    text = re.sub(r"[îì]", "i", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80]


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using fitz (PyMuPDF) with pdfplumber fallback."""
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        return "\n\n".join(p.get_text() for p in doc)
    except ImportError:
        pass
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)
    except ImportError:
        pass
    return ""


def _clean_title(raw: str) -> str:
    """Clean a raw PostCom title string.

    - Replace underscores with spaces
    - Remove leading date
    - Remove trailing status info
    - Normalize whitespace
    """
    # Replace underscores used as separators
    text = raw.replace("_", " ")
    # Remove leading date
    text = re.sub(r"^\d{1,2}\.\d{2}\.\d{4}\s*[-–]\s*", "", text)
    text = re.sub(r"^\d{1,2}\.\d{2}\.\d{4}\s*", "", text)
    # Remove trailing status
    text = re.sub(
        r"\s*[-–]\s*(rechtskräftig|nicht\s+rechtskräftig|noch\s+nicht\s+rechtskräftig)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove trailing parenthesized status
    text = re.sub(
        r"\s*\(\s*(rechtskräftig|nicht\s+rechtskräftig|noch\s+nicht\s+rechtskräftig)\s*\)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove language notes like "(en langue française)"
    text = re.sub(r"\s*\((?:en|in)\s+(?:langue\s+)?(?:français|französisch|italienisch|italiana)e?\)\s*", "", text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Identity across the 2026 relaunch ────────────────────────────────────────
# Verfügung number and year as tile titles, stored titles and PDF names write them:
# "Verfügung 8/2026", "Verfügung 1 / 2013", "13.5.2026 Verfügung 6 2026", "Verfügung Nr. 7/2014",
# "Décision no.10/2014", "Décision 10-2014".
NUMBER_IN_TITLE = re.compile(
    r"(?:Verfügung|Verfuegung|Décision|Decisione)\s*(?:Nr\.?|n[°o]\.?|no\.?)?\s*"
    r"(\d{1,3})\s*(?:[/_-]|\s)\s*((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
NUMBER_IN_PDF = re.compile(r"VFG[\s_-]*(\d{1,3})[\s_-]+((?:19|20)\d{2})", re.IGNORECASE)
NUMBER_WITHOUT_YEAR = re.compile(
    r"(?:Verfügung|Décision)\s+(\d{1,3})\s+(?:betreffend|betr\.|concernant)", re.IGNORECASE
)
NUMBER_IN_LETTERHEAD = re.compile(
    r"(?:Verfügung|Décision|Decisione)\s+(?:Nr\.|n°\.?)\s*(\d{1,3})\s*/\s*((?:19|20)\d{2})",
    re.IGNORECASE,
)
POSTCOM_CASE = re.compile(r"PostCom-\d+-\d+/\d+")
_KIND_SUFFIX = {"main": "", "beilage": "-beilage", "liste": "-liste"}
_TITLE_STOPWORDS = {
    "betreffend", "concernant", "rechtskräftig", "rechtkräftig", "nicht", "verfügung",
    "décision", "anonymisiert", "sprache", "französischer", "französicher", "italienischer",
}


def _kind(title: str, text_head: str = "") -> str:
    """"liste", "beilage" or "main" for a title (and, for stored rows, the text head)."""
    t = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s*[-–_]?\s*", "", title or "").strip().lower()
    head = re.sub(r"\s+", " ", text_head or "").strip().lower()   # PDF text breaks lines anywhere
    if t.startswith("liste") or head.startswith(("dienstleistungen der grundversorgung", "anbietende gesellschaft")):
        return "liste"
    if t.startswith("beilage"):
        return "beilage"
    return "main"


def _match_kind(kind: str) -> str:
    """Identity compares main decision vs attachment only.

    Liste and Beilage name the same attachment in different years: the tile "Beilage zur
    Verfügung 05/2025" is the document stored as the Liste of 5/2025 (live check 2026-09-15).
    """
    return "main" if kind == "main" else "annex"


def _held_key(title: str, text_head: str, pdf_url: str, decision_date) -> tuple[int, int, str] | None:
    """(number, year, kind) of a stored row, from its title, PDF name, date or letterhead."""
    kind = _match_kind(_kind(title, text_head))
    m = NUMBER_IN_TITLE.search(title) or NUMBER_IN_PDF.search(unquote(pdf_url.rsplit("/", 1)[-1]))
    if m:
        return int(m.group(1)), int(m.group(2)), kind
    m = NUMBER_WITHOUT_YEAR.search(title)
    if m and decision_date:
        return int(m.group(1)), int(str(decision_date)[:4]), kind
    if "PostCom" in text_head[:300]:
        m = NUMBER_IN_LETTERHEAD.search(text_head)
        if m:
            return int(m.group(1)), int(m.group(2)), kind
    return None


def _title_tokens(title: str) -> set[str]:
    t = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s*[-–_]?\s*", "", (title or "").replace("_", " ")).lower()
    return {w for w in re.findall(r"\w+", t) if len(w) >= 4 and w not in _TITLE_STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class PostComScraper(BaseScraper):
    """Scraper for PostCom (Swiss Federal Postal Commission) Verfügungen."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "postcom"

    def _shard_path(self) -> Path:
        return Path(os.environ.get(SHARD_ENV, REPO_ROOT / "output" / "decisions" / "postcom.jsonl"))

    def _held_index(self) -> tuple[dict, dict] | None:
        """Corpus rows as (by_key, by_date), or None when they cannot be known.

        by_key maps (number, year, kind) to a decision_id for every row whose title, PDF
        name, date or letterhead names its Verfügung number; by_date maps an ISO date to
        [(title tokens, decision_id)]. None: the shard is unreadable or empty although state
        holds ids, so a listed tile may be a held decision under an older id.
        """
        by_key: dict[tuple[int, int, str], str] = {}
        by_date: dict[str, list[tuple[set[str], str]]] = {}
        path = self._shard_path()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    did = rec.get("decision_id")
                    if not did:
                        continue
                    title = str(rec.get("title") or "")
                    key = _held_key(
                        title,
                        str(rec.get("full_text") or "")[:800],
                        str(rec.get("pdf_url") or ""),
                        rec.get("decision_date"),
                    )
                    if key:
                        by_key.setdefault(key, did)
                    day = str(rec.get("decision_date") or "")[:10]
                    if day:
                        by_date.setdefault(day, []).append((_title_tokens(title), did))
        except OSError as e:
            logger.warning(f"[postcom] corpus shard unreadable ({path}): {e}")
        if not by_key and not by_date and self.state.count() > 0:
            return None
        return by_key, by_date

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Discover Verfügungen and Strafbescheide from the two listing pages.

        Tiles whose decision the corpus already holds, under any earlier id, are skipped.
        A page without tiles is logged; no tiles on either page is an error rather than a
        quiet "+0 new" (the 2026 relaunch hid every decision behind a hub page that way).
        """
        index = self._held_index()
        if index is None:
            raise RuntimeError(
                f"[postcom] corpus shard {self._shard_path()} unreadable although state holds "
                f"{self.state.count()} ids; refusing to mint ids that may duplicate held decisions"
            )
        by_key, by_date = index
        seen_pdfs: set[str] = set()
        tiles_total = 0
        held = 0
        found = 0
        for listing_url, page_type in LISTING_PAGES:
            response = self.get(listing_url)
            soup = BeautifulSoup(response.text, "html.parser")
            tiles = soup.find_all("a", class_="download-item", href=True)
            tiles_total += len(tiles)
            if not tiles:
                logger.warning(f"[postcom] no download-item tiles on {listing_url} (page restructured?)")
            for tile in tiles:
                stub = self._stub_from_tile(tile, page_type, by_key, by_date, seen_pdfs)
                if stub is None:
                    continue
                if stub.pop("held") or self.state.is_known(stub["decision_id"]):
                    held += 1
                    continue
                if since_date and stub.get("decision_date"):
                    parsed = parse_date(stub["decision_date"])
                    if parsed and parsed < since_date:
                        continue
                found += 1
                yield stub
        if tiles_total == 0:
            raise RuntimeError(
                "[postcom] no download-item tiles on any listing page: postcom.admin.ch "
                "restructured again? (2026: /de/dokumentation/verfuegungen became a hub)"
            )
        logger.info(
            f"[postcom] Found {found} new decisions ({len(seen_pdfs)} PDFs on "
            f"{len(LISTING_PAGES)} pages, {held} already held)"
        )

    def _stub_from_tile(self, tile, page_type: str, by_key: dict, by_date: dict, seen_pdfs: set[str]) -> dict | None:
        href = tile["href"]
        if ".pdf" not in href.lower():
            return None
        pdf_url = href if href.startswith("http") else urljoin(BASE_URL, href)
        if pdf_url in seen_pdfs:
            return None
        seen_pdfs.add(pdf_url)
        h4 = tile.find(class_="download-item__title")
        raw_title = (h4.get_text(" ", strip=True) if h4 else tile.get_text(" ", strip=True)).strip()
        if not raw_title:
            return None
        status_m = STATUS_PATTERN.search(raw_title)
        stub = {
            "pdf_url": pdf_url,
            "title": _clean_title(raw_title),
            "status": status_m.group(0).strip() if status_m else None,
            "decision_type": page_type,
            "held": False,
        }
        if page_type == "Strafbescheid":
            dm = PUB_DATE_PATTERN.search(raw_title)
            decided = parse_date(f"{dm.group(1)}. {dm.group(2)} {dm.group(3)}") if dm else None
            case = POSTCOM_CASE.search(raw_title)
            if case:
                docket = case.group(0)
            elif decided:
                low = raw_title.lower()
                lang = "fr" if "französisch" in low else "it" if "italienisch" in low else "de"
                seq = re.match(r"Strafbescheid\s+(\d+)\s", raw_title)
                docket = f"Strafbescheid {decided.isoformat()} {lang}" + (f" {seq.group(1)}" if seq else "")
            else:
                logger.warning(f"[postcom] Strafbescheid tile without case number or date: {raw_title!r}")
                return None
            stub.update(docket_number=docket, decision_date=decided.isoformat() if decided else "")
        else:
            date_m = LEADING_DATE.match(raw_title)
            decision_date_str = date_m.group(1) if date_m else ""
            num = NUMBER_IN_TITLE.search(raw_title)
            if num:
                number, year, kind = int(num.group(1)), int(num.group(2)), _kind(raw_title)
                docket = f"VFG-{number}-{year}{_KIND_SUFFIX[kind]}"
                stub["held"] = (number, year, _match_kind(kind)) in by_key
            else:
                decided = parse_date(decision_date_str) if decision_date_str else None
                if decided:
                    tokens = _title_tokens(raw_title)
                    stub["held"] = any(
                        _jaccard(tokens, held_tokens) >= 0.6
                        for held_tokens, _ in by_date.get(decided.isoformat(), [])
                    )
                slug = _slugify(_clean_title(raw_title))
                docket = (slug or "unknown") + (f"-{decided.isoformat()}" if decided else "")
            stub.update(docket_number=docket, decision_date=decision_date_str)
        stub["decision_id"] = make_decision_id("postcom", docket)
        return stub

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Download PDF and extract decision text."""
        pdf_url = stub["pdf_url"]
        docket = stub["docket_number"]

        try:
            response = self.get(pdf_url)
        except Exception as e:
            logger.error(f"[postcom] Failed to download PDF for {docket}: {e}")
            return None

        full_text = _extract_pdf_text(response.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(
                f"[postcom] No text extracted from {docket} "
                f"({len(response.content)} bytes PDF)"
            )
            return None

        full_text = self.clean_text(full_text)
        lang = detect_language(full_text)
        decision_date = parse_date(stub.get("decision_date", ""))

        return Decision(
            decision_id=make_decision_id("postcom", docket),
            court="postcom",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=lang,
            title=stub.get("title"),
            legal_area="Postrecht",
            decision_type=stub.get("decision_type") or "Verfügung",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape PostCom Verfügungen")
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    since = date.fromisoformat(args.since) if args.since else None
    scraper = PostComScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {d.title[:60]}")
    print(f"\nScraped {len(decisions)} PostCom Verfügungen")
