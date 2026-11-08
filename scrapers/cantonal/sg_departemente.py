"""
St. Gallen Departments Scraper (SG Departemente)
=================================================
Rekurs- and Aufsichtsentscheide of the St. Gallen departments (Bau- und
Umweltdepartement, Sicherheits- und Justizdepartement, Volkswirtschafts-
departement, Departement des Innern, Bildungsdepartement, Finanzdepartement)
from publikationen.sg.ch/rechtsprechung-departemente.

Platform: the same TYPO3 ``tx_diamjudicalsg`` plugin as the courts portal
(scrapers/cantonal/sg_publikationen.py), on its own page:
- GET /rechtsprechung-departemente/?filter[timerangeType]=-1 → full page whose
  result list carries data-total (779 on 2026-11-08, Entscheide 2012-2026).
- GET the same URL with the resultAjax action and page=N → 10 items per page,
  newest first. Each item: "Publikationsdatum"/"Entscheiddatum", title
  ("BUDE 2026 Nr. 062", "Rekursentscheid VD; Gastwirtschaftsrecht"), Fall-Nr.,
  Instanz (the department), Publizierende Stelle (its Generalsekretariat), and
  the published summary.
- GET /rechtsprechung-departemente-detail/{uid}/ → the ruling is the PDF
  attachment in publication-detail__footer
  (/fileadmin/ekab/judical_sg/YYYY/MM/<docket>/attachments/*.pdf); the
  content block only says "… finden Sie im angehängten PDF-Dokument".

Until 2026-11 the only intake was the retired entscheidsuche SG_Gerichte /
SG_Publikationen spiders, stale since early 2024: 250 rulings were missing.

Identity: rows are filed under court ``sg_publikationen`` with the id shape
the entscheidsuche ingest gave the 517 department rows it holds
(sg_publikationen_<docket, non [A-Za-z0-9_.-] → "_">), so the direct row
replaces its entscheidsuche copy under the same id. Thirteen dockets carry two
rulings (Zwischen- and Rekursentscheid, or a second Rekurs): the earliest
keeps the bare id, a later one takes ``_dYYYYMMDD`` — the suffix the build
gives a same-id ruling with another date.
"""
from __future__ import annotations

import html as html_module
import io
import logging
import re
from collections import defaultdict
from datetime import date
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations

logger = logging.getLogger(__name__)

BASE_URL = "https://publikationen.sg.ch"
LIST_URL = f"{BASE_URL}/rechtsprechung-departemente/"
COURT = "sg_publikationen"
PAGE_SIZE = 10
AJAX_PARAMS = {
    "filter[timerangeType]": "-1",
    "sortorder": "1",
    "tx_diamjudicalsg_judicalpublicationpublicuserportalrenderlist[action]": "resultAjax",
    "tx_diamjudicalsg_judicalpublicationpublicuserportalrenderlist[controller]": "JudicalPublicationPublicUserPortal",
}

RE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
RE_UID = re.compile(r"/rechtsprechung-departemente-detail/(\d+)/?$")
# Content blocks that only point at the attachment.
RE_SEE_PDF = re.compile(r"angehängten PDF|vgl\. PDF|siehe PDF|im PDF", re.I)


def _parse_swiss_date(text: str | None) -> date | None:
    m = RE_DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def es_id_part(value: str) -> str:
    """entscheidsuche_ingest._normalize_id_part: the id shape of the rows it holds."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", value or "").strip("_")


def parse_total(html: str) -> int | None:
    m = re.search(r'data-total="(\d+)"', html or "")
    return int(m.group(1)) if m else None


def parse_listing_page(html: str) -> list[dict]:
    """The publication items of one result page."""
    soup = BeautifulSoup(html or "", "html.parser")
    items = []
    for div in soup.find_all("div", class_="publication-list__item"):
        m = RE_UID.search(div.get("data-detailurl", ""))
        if not m:
            continue
        meta = {}
        for dl in div.find_all("dl"):
            dt, dd = dl.find("dt"), dl.find("dd")
            if dt and dd:
                meta[dt.get_text(strip=True).rstrip(":").strip()] = dd.get_text(" ", strip=True)
        dates = {}
        for li in div.find_all("li"):
            t = li.get_text(" ", strip=True)
            if "Entscheiddatum" in t:
                dates["decision"] = _parse_swiss_date(t)
            elif "Publikationsdatum" in t:
                dates["publication"] = _parse_swiss_date(t)
        h2 = div.find("h2")
        p = div.find("p")
        pdf = div.find("a", class_="pdf-btn")
        items.append({
            "uid": m.group(1),
            "docket_number": meta.get("Fall-Nr.", "").strip(),
            "department": meta.get("Instanz") or None,
            "publisher": meta.get("Publizierende Stelle") or None,
            "title": h2.get_text(" ", strip=True) if h2 else None,
            "summary": p.get_text(" ", strip=True) if p else None,
            "decision_date": dates.get("decision"),
            "publication_date": dates.get("publication"),
            "detail_url": urljoin(BASE_URL, div["data-detailurl"]),
            "export_pdf_url": urljoin(BASE_URL, html_module.unescape(pdf["href"])) if pdf and pdf.get("href") else None,
        })
    return items


def assign_ids(items: list[dict]) -> None:
    """Set decision_id on every item (see module docstring, Identity)."""
    by_docket: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        key = es_id_part(it["docket_number"]) or f"pub{it['uid']}"
        by_docket[key].append(it)
    for key, group in by_docket.items():
        group.sort(key=lambda it: (it["decision_date"] or date.max, int(it["uid"])))
        used: set[str] = set()
        for i, it in enumerate(group):
            did = f"{COURT}_{key}"
            if i:
                d = it["decision_date"]
                did += f"_d{d:%Y%m%d}" if d else f"_p{it['uid']}"
                if did in used:
                    did += f"_p{it['uid']}"
            used.add(did)
            it["decision_id"] = did


def attachment_urls(detail_html: str) -> list[str]:
    soup = BeautifulSoup(detail_html or "", "html.parser")
    footer = soup.find("div", class_="publication-detail__footer")
    if not footer:
        return []
    urls = []
    for a in footer.find_all("a", href=True):
        href = a["href"]
        if "fileadmin" in href and href.lower().split("?")[0].endswith(".pdf"):
            u = urljoin(BASE_URL, href)
            if u not in urls:
                urls.append(u)
    return urls


def inline_text(detail_html: str) -> str:
    soup = BeautifulSoup(detail_html or "", "html.parser")
    div = soup.find("div", class_="publication-detail__content")
    if not div:
        return ""
    text = html_module.unescape(div.get_text("\n", strip=True))
    if len(text) < 300 and RE_SEE_PDF.search(text):
        return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class SGDepartementeScraper(BaseScraper):
    """St. Gallen departmental rulings from publikationen.sg.ch."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 45
    MAX_ERRORS = 30

    @property
    def court_code(self):
        # State and shard name. The rows themselves are court=sg_publikationen.
        return "sg_departemente"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        if since_date and isinstance(since_date, str):
            since_date = date.fromisoformat(since_date)

        failures: list[str] = []
        try:
            r = self.get(LIST_URL, params={"filter[timerangeType]": "-1", "sortorder": "1"})
            self.portal_count = parse_total(r.text)
        except Exception as e:
            logger.error(f"SG dep: total page search failed: {e}")
        if not self.portal_count:
            logger.warning("SG dep: the result page shows no data-total; paging blind")

        # Page until an empty page; the total bounds it when known.
        max_pages = (self.portal_count // PAGE_SIZE + 3) if self.portal_count else 300
        items: list[dict] = []
        bad_pages = 0
        for page in range(1, max_pages + 1):
            try:
                r = self.get(LIST_URL, params={**AJAX_PARAMS, "page": str(page)})
            except Exception as e:
                bad_pages += 1
                logger.error(f"SG dep: listing page {page} search failed: {e}")
                if bad_pages >= 3:
                    failures.append(f"listing pages failing from page {page - 2}")
                    break
                continue
            page_items = parse_listing_page(r.text)
            if not page_items:
                break
            items.extend(page_items)

        if not items:
            failures.append("0 publications parsed (page restructured?)")
        elif self.portal_count and len({i["uid"] for i in items}) < self.portal_count:
            logger.warning(
                f"SG dep: listed {len(items)} of {self.portal_count} publications")

        assign_ids(items)
        total = 0
        for it in items:
            if self.state.is_known(it["decision_id"]):
                continue
            if since_date and it["decision_date"] and it["decision_date"] < since_date:
                continue
            total += 1
            yield {**it, "url": it["detail_url"]}

        logger.info(f"SG dep: discovery complete: {total} new stubs from "
                    f"{len(items)} listed (portal total {self.portal_count})")
        if failures:
            for f in failures:
                logger.error(f"SG dep: {f}")
            raise RuntimeError("SG dep discovery incomplete: " + "; ".join(failures))

    def fetch_decision(self, stub: dict) -> Decision | None:
        try:
            r = self.get(stub["detail_url"])
        except Exception as e:
            logger.warning(f"SG dep: detail fetch failed for {stub['docket_number']}: {e}")
            return None

        pdf_urls = attachment_urls(r.text)
        texts, fetched_url = [], None
        for url in pdf_urls:
            t = self._pdf_text(url)
            if t:
                texts.append(t)
                fetched_url = fetched_url or url
        full_text = "\n\n".join(texts).strip()
        if len(full_text) < 200:
            full_text = inline_text(r.text) or full_text
        if len(full_text) < 200 and stub.get("export_pdf_url"):
            # The portal's own PDF rendering of the publication.
            t = self._pdf_text(stub["export_pdf_url"])
            if len(t) > len(full_text):
                full_text, fetched_url = t, stub["export_pdf_url"]

        if len(full_text) < 200:
            logger.warning(f"SG dep: no usable text for {stub['docket_number']} "
                           f"(uid {stub['uid']}, {len(pdf_urls)} attachments)")
            if pdf_urls:
                # The attachment exists but has no text layer: cache as a gap.
                self.state.mark_gap(stub["decision_id"])
            return None

        return Decision(
            decision_id=stub["decision_id"],
            court=COURT,
            canton="SG",
            chamber=stub.get("department"),
            docket_number=stub["docket_number"] or f"SG-DEP-{stub['uid']}",
            decision_date=stub.get("decision_date"),
            publication_date=stub.get("publication_date"),
            language=detect_language(full_text),
            title=stub.get("title"),
            regeste=stub.get("summary"),
            full_text=full_text,
            source_url=stub["detail_url"],
            pdf_url=fetched_url,
            external_id=f"sg_dep:{stub['uid']}",
            cited_decisions=extract_citations(full_text),
        )

    def _pdf_text(self, url: str) -> str:
        try:
            r = self.get(url, timeout=90)
        except Exception as e:
            logger.warning(f"SG dep: PDF download failed {url}: {e}")
            return ""
        if not r.content.startswith(b"%PDF-"):
            logger.warning(f"SG dep: not a PDF: {url}")
            return ""
        return self._extract_pdf_text(r.content)

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                return "\n\n".join(p.extract_text() or "" for p in pdf.pages).strip()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"SG dep: pdfplumber failed ({e}); trying PyMuPDF")
        try:
            import fitz
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                return "\n\n".join(page.get_text() for page in doc).strip()
        except Exception:
            return ""
