"""
Bern Directorate Beschwerdeentscheide Scraper (BE Direktionen)
==============================================================
Scrapes the administrative-appeal decisions (Beschwerdeentscheide) of Bernese
cantonal directorates that publish their own decision tables — the executive
administrative-recourse layer entscheidsuche does not aggregate.

Covered directorates (chamber = the directorate named in the docket, else the
listing's directorate):
- GSI — Gesundheits-, Sozial- und Integrationsdirektion (static table)
- BKD — Bildungs- und Kulturdirektion, Rechtsdienst (static table; dockets
  "2024.BKD.4985" and, before 2020, "300.01-19")
- DIJ — Direktion für Inneres und Justiz (formerly JGK): Grundbuch
  Beschwerdeentscheide and Notariatsaufsicht Disziplinar-, Moderations- und
  Administrativentscheide. Both pages render a JAXForms data pool in the
  browser; the pool's JSON is read directly (DATA_POOLS)
- KAIO — Beschaffungswesen (static table). Its PDFs carry no docket in link or
  file name, so the docket filter matches none of them (0 rows to date).

Static tables: Datum | Nummer (docket, links the PDF) | Gegenstand (subject).
Pattern = static index → direct PDF (like scrapers/esbk.py). The bvd has its own
Tribuna portal (be_bvd). robots Crawl-delay:10 → REQUEST_DELAY 10s.

The frozen entscheidsuche shard es_be_weitere (court be_weitere) holds earlier
copies of GSI/GEF, BKD and DIJ/JGK rulings under that court code.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id, parse_date
from scrapers.elcom import _extract_pdf_text

logger = logging.getLogger(__name__)

# (directorate abbreviation, listing URL)
LISTINGS = [
    ("GSI", "https://www.gsi.be.ch/de/start/ueber-uns/generalsekretariat/rechtsabteilung/rechtsprechung.html"),
    ("KAIO", "https://www.kaio.fin.be.ch/de/start/themen/oeffentliches-beschaffungswesen/rechtliches/beschwerdeentscheide.html"),
    ("BKD", "https://www.bkd.be.ch/de/start/ueber-uns/die-organisation/bkd-generalsekretariat/rechtsdienst-bkd/ausgewaehlte-beschwerdeentscheide.html"),
]

# JAXForms data pools: (directorate, pool id, page that embeds it, PDF host).
# The page calls createDataPool(..., 'KAIO', '<pool id>', 'standard', 'de',
# 'paging=false'); its grid then GETs POOL_URL with the pool config as query
# parameters and receives every entry at once (no session needed; checked
# 2026-11-08: 202 Grundbuch and 150 Notariat entries).
POOL_URL = "https://ktbe.jaxforms.com/formservice/services/rest/generic/json"
DATA_POOLS = [
    ("DIJ", "77e7438e-f9c8-4e73-9302-bbfec0ad04b3",
     "https://www.gba.dij.be.ch/de/start/grundbuch/rechtliches/beschwerdeentscheide.html",
     "https://www.gba.dij.be.ch/content"),
    ("DIJ", "3a44138f-ec0b-4afb-bbe0-4daedb0eb2b0",
     "https://www.gba.dij.be.ch/de/start/notariat/notariatsaufsicht/entscheide.html",
     "https://www.gba.dij.be.ch/content"),
]
# The Notariat pool also lists the Verwaltungsgericht's and the Bundesgericht's
# rulings on appeal from the directorate (11 on 2026-11-08). Those courts have
# their own scrapers; only the directorate's own rulings are taken here.
POOL_AUTHORITIES = ("Direktion für Inneres und Justiz", "Justiz-, Gemeinde- und Kirchendirektion")

# The pools' DateiName does not always match the file on gba.dij.be.ch
# (checked 2026-11-09, all 33 rulings that answered 404 the night before):
# - a double space or a line break in the name where the stored file has one
#   space ("Beschwerdeentscheid 2022.DIJ.295  15.11.2022.pdf");
# - a ruling filed in the other language's folder under that folder's prefix:
#   the pool says de/notariat/.../Entscheid 26.11-15.64, the file is
#   fr/notariat/.../Decision 26.11-15.64; the pool says fr/.../Décision sur
#   recours 2024.DIJ.8653, the file is de/.../Beschwerdeentscheid 2024.DIJ.8653.
# Live run 2026-11-09 with these candidates: 24 of the 33 download (15 not
# held before, 9 replacing entscheidsuche copies). 9 answer 404 under every
# name, as the canton's own page links them; those are cached as gaps and
# re-probed weekly.
_FOLDER_PREFIX = {
    ("de", "notariat"): "Entscheid ",
    ("fr", "notariat"): "Decision ",
    ("de", "entscheide"): "Beschwerdeentscheid ",
    ("fr", "entscheide"): "Decision sur recours ",
}
_POOL_PATH_RE = re.compile(
    r"^(?P<head>.*/dokumente/)(?P<lang>de|fr)/(?P<sub>(?:notariat/)?)entscheide/"
    r"(?P<prefix>Decision sur recours |Décision sur recours |Beschwerdeentscheid |"
    r"Entscheid |Decision |Décision )?(?P<rest>.+)$")   # longest prefixes first


def _pool_paths(path: str) -> list[str]:
    """Candidate DAM paths for a pool DateiName, most likely first: the name
    with whitespace runs collapsed, then the other language's folder under its
    prefix, then the same folder under its usual prefix. At most three."""
    first = re.sub(r"\s+", " ", path.strip())
    out = [first]
    m = _POOL_PATH_RE.match(first)
    if m:
        kind = "notariat" if m.group("sub") else "entscheide"
        lang = m.group("lang")
        other = "fr" if lang == "de" else "de"
        for alt in (other, lang):
            out.append(f"{m.group('head')}{alt}/{m.group('sub')}entscheide/"
                       f"{_FOLDER_PREFIX[(alt, kind)]}{m.group('rest')}")
    seen: list[str] = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return seen[:3]

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Directorate docket: 2025.GSI.2700, 2026.KAIO.12, …  (year.DIR.number)
DOCKET_RE = re.compile(r"\b(\d{4}\.[A-Za-zÄÖÜ]{2,6}\.\d+)\b")
# Older numbering, read from the link text (the table's Nummer cell):
# BKD before 2020 "300.01-19" (Abteilung.Nummer-Jahr); GEF (now GSI) before
# 2018 "GEF.2012-0035". 46 GEF rulings on the GSI page were skipped before.
LEGACY_DOCKET_RE = re.compile(r"^(\d{3}\.\d{2}-\d{2}|GEF\.\d{4}-\d{4,5})(?![\d.-])")
_TAG_RE = re.compile(r"<[^>]+>")


def _pool_text(value) -> str | None:
    """Pool HTML snippet ("...<br/>...") as plain text."""
    if not value:
        return None
    text = _TAG_RE.sub("\n", str(value).replace("&nbsp;", " "))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


def _pool_docket(number: str) -> str:
    """'2019.JGK.6290  / 2019.JGK.6370' -> '2019.JGK.6290 / 2019.JGK.6370'.
    Only whitespace runs collapse; '2022.DIJ.5528/5529' stays as published."""
    return re.sub(r"\s+", " ", number.strip())


def _chamber(docket: str, directorate: str) -> str:
    """The directorate named in the docket (2025.DIJ.1 -> DIJ, GEF.2012-0035 ->
    GEF), else the listing's."""
    parts = docket.split(".")
    if len(parts) >= 3 and parts[1].isalpha():
        return parts[1].upper()
    if len(parts) == 2 and parts[0].isalpha():
        return parts[0].upper()
    return directorate


class BEDirektionenScraper(BaseScraper):
    """Scraper for Bernese cantonal directorate rulings (GSI, BKD, DIJ, KAIO)."""

    REQUEST_DELAY = 10.0  # robots Crawl-delay:10
    TIMEOUT = 120

    @property
    def court_code(self) -> str:
        return "be_direktionen"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        listed: set[str] = set()
        found = 0
        for stub in self._listing_stubs():
            if stub["docket_number"] in listed:
                continue
            listed.add(stub["docket_number"])
            decision_id = make_decision_id("be_direktionen", stub["docket_number"])
            if self.state.is_known(decision_id):
                continue
            if since_date and stub.get("decision_date"):
                parsed = parse_date(stub["decision_date"])
                if parsed and parsed < since_date:
                    continue
            found += 1
            yield stub
        # Every ruling the listings name, so the nightly health gap compares
        # the shard with what the directorates publish.
        self.portal_count = len(listed)
        logger.info(f"[be_direktionen] Found {found} new decisions ({len(listed)} listed)")

    def _listing_stubs(self) -> Iterator[dict]:
        yield from self._table_stubs()
        yield from self._pool_stubs()

    def _table_stubs(self) -> Iterator[dict]:
        """Static tables: Datum | Nummer (links the PDF) | Gegenstand."""
        seen_urls: set[str] = set()
        for directorate, listing_url in LISTINGS:
            try:
                resp = self.get(listing_url)
            except Exception as e:
                logger.error(f"[be_direktionen] {directorate} listing failed: {e}")
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ".pdf" not in href.lower():
                    continue
                link_text = a.get_text(" ", strip=True)
                m = (DOCKET_RE.search(link_text) or DOCKET_RE.search(href)
                     or LEGACY_DOCKET_RE.match(link_text))
                if not m:
                    continue  # only docket-numbered decision PDFs (skips forms etc.)
                docket = m.group(1)

                pdf_url = urljoin(listing_url, href)
                if pdf_url in seen_urls:
                    continue
                seen_urls.add(pdf_url)

                decision_date_str, title = None, None
                row = a.find_parent("tr")
                if row:
                    cells = row.find_all(["th", "td"])
                    for c in cells:
                        dm = DATE_RE.search(c.get_text(strip=True))
                        if dm:
                            decision_date_str = f"{dm.group(1)}.{dm.group(2)}.{dm.group(3)}"
                            break
                    if len(cells) >= 3:
                        gegenstand = cells[-1].get_text(" ", strip=True)
                        if gegenstand and not DATE_RE.search(gegenstand) and docket not in gegenstand:
                            title = gegenstand

                # The true issuing directorate is the docket prefix (YYYY.DIR.N), which can
                # differ from the listing page (e.g. a BKD decision listed on the KAIO portal).
                yield {
                    "docket_number": docket,
                    "decision_date": decision_date_str or "",
                    "pdf_url": pdf_url,
                    "title": title,
                    "chamber": _chamber(docket, directorate),
                }

    def _pool_stubs(self) -> Iterator[dict]:
        """JAXForms data pools (DIJ Grundbuch and Notariatsaufsicht)."""
        for directorate, pool_id, _page_url, pdf_host in DATA_POOLS:
            try:
                resp = self.get(POOL_URL, params={
                    "handler": "jaxGenericDataPoolHandler", "language": "de",
                    "paging": "false", "id": pool_id, "displayID": "standard",
                })
                entries = resp.json()["results"]
            except Exception as e:
                logger.error(f"[be_direktionen] {directorate} pool {pool_id} failed: {e}")
                continue
            if not entries:
                logger.error(f"[be_direktionen] {directorate} pool {pool_id} returned no entries")
                continue
            # One ruling can be listed once per holding (Notariat 26.11-14.46:
            # one PDF, a disciplinary and an administrative holding). Merge them.
            merged: dict[str, dict] = {}
            for e in entries:
                authority = e.get("Behörde_de")
                if authority and not authority.startswith(POOL_AUTHORITIES):
                    continue
                number, path = (e.get("EntscheidNr") or "").strip(), e.get("DateiName")
                if not number or not path:
                    continue
                docket = _pool_docket(number)
                regeste = _pool_text(e.get("Kurzbeschrieb_de") or e.get("Beschreibung_de"))
                regeste_fr = _pool_text(e.get("Kurzbeschrieb_fr") or e.get("Beschreibung_fr"))
                topic = _pool_text(e.get("Thema_de"))
                if docket in merged:
                    stub = merged[docket]
                    for key, val in (("regeste", regeste), ("abstract_fr", regeste_fr), ("title", topic)):
                        if val and val not in (stub.get(key) or ""):
                            sep = " / " if key == "title" else "\n\n"
                            stub[key] = f"{stub[key]}{sep}{val}" if stub.get(key) else val
                    continue
                tech = str(e.get("DatumTechnisch") or "")
                pdf_urls = [pdf_host + quote(p) for p in _pool_paths(path)]
                merged[docket] = {
                    "docket_number": docket,
                    "decision_date": (f"{tech[:4]}-{tech[4:6]}-{tech[6:8]}" if len(tech) == 8
                                      else e.get("Datum") or ""),
                    "pdf_url": pdf_urls[0],
                    "pdf_urls": pdf_urls,
                    "title": topic,
                    "regeste": regeste,
                    "abstract_fr": regeste_fr,
                    "decision_type": _pool_text(e.get("Entscheidart_de")) or "Beschwerdeentscheid",
                    "legal_area": _pool_text(e.get("Rubrik_de")),
                    "chamber": _chamber(docket, directorate),
                }
            yield from merged.values()

    def fetch_decision(self, stub: dict) -> Decision | None:
        docket = stub["docket_number"]
        candidates = stub.get("pdf_urls") or [stub["pdf_url"]]
        resp, pdf_url = None, candidates[0]
        for url in candidates:
            try:
                resp, pdf_url = self.get(url), url
                break
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 404:
                    continue                      # try the next file name
                logger.error(f"[be_direktionen] Failed to download PDF for {docket}: {e}")
                return None                       # transient: retry tomorrow, no gap
        if resp is None:
            # 404 under every name: the published link is dead. Cache it as a
            # gap (re-probed after GAP_TTL_DAYS) instead of 10 s per name nightly.
            logger.error(f"[be_direktionen] PDF not found under {len(candidates)} "
                         f"name(s) for {docket}: {candidates[0]}")
            self.state.mark_gap(make_decision_id("be_direktionen", docket))
            return None
        full_text = _extract_pdf_text(resp.content)
        if not full_text or len(full_text.strip()) < 50:
            logger.warning(f"[be_direktionen] No text extracted from {docket}")
            return None
        full_text = self.clean_text(full_text)
        language = detect_language(full_text)
        regeste = stub.get("regeste")
        if language == "fr" and stub.get("abstract_fr"):
            regeste = stub["abstract_fr"]
        return Decision(
            decision_id=make_decision_id("be_direktionen", docket),
            court="be_direktionen",
            canton="BE",
            chamber=stub.get("chamber"),
            docket_number=docket,
            decision_date=parse_date(stub.get("decision_date", "")),
            language=language,
            title=stub.get("title"),
            regeste=regeste,
            abstract_de=stub.get("regeste"),
            abstract_fr=stub.get("abstract_fr"),
            legal_area=stub.get("legal_area"),
            decision_type=stub.get("decision_type") or "Beschwerdeentscheid",
            full_text=full_text,
            source_url=pdf_url,
            pdf_url=pdf_url,
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape BE directorate Beschwerdeentscheide")
    parser.add_argument("--since", type=str)
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("pdfminer", "pdfplumber", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    since = date.fromisoformat(args.since) if args.since else None
    scraper = BEDirektionenScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  [{d.chamber}]  {len(d.full_text)} chars  {(d.title or '')[:45]}")
    print(f"\nScraped {len(decisions)} BE directorate decisions")
