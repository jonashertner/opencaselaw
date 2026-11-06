"""
SIX Exchange Regulation — sanction decisions
============================================

SIX Exchange Regulation AG (SER) enforces the listing rules of SIX Swiss
Exchange — ad hoc publicity, financial reporting, management transactions,
regular reporting obligations, participant duties — under the self-regulation
the FinfraG delegates to the exchange. Its decisions are published, mostly
anonymised, on ser-ag.com:

  * Sanktionsbescheide of SER itself (SB-AHP-III/24, SER-MT-I/20 …),
  * decisions of the Sanktionskommission (SaKo-X/2023 …),
  * arbitral awards and settlements of the Schiedsgericht reviewing them,
  * decisions of the Beschwerdeinstanz and of Listing & Admission.

The two Federal Supreme Court judgments and the one Federal Administrative
Court judgment SER also lists are in the corpus under bger/bvger already and
are skipped here.

Listing: the page's React widget reads an AEM endpoint,
``<page>/_jcr_content/root/contentparsys/parsys/assets_taxonomy_filt.results.json``,
which returns every decision with its title (the SER reference), a
description ("KR 53, DAH 5 | <summary> | Fahrlässigkeit"), the decision date
(entryIntoForceDate), the publication date and the PDF URL.

Languages: SER publishes most decisions in ONE language — of the 58 on
2026-11-06, 24 only in English, 23 only in German, 8 only in French — and
three 2016/17 Sanktionskommission decisions in German, French and English.
The listings in the three site languages link "-de"/"-fr"/"-en" files, but a
missing translation is served as another language's file (SaKo-II/2024's
"-de.pdf" is the English original; SB-AHP-III/24's is French). So:

  * the file SER's German page links is the decision's main row (plain
    docket), its language detected from the text — English included, which
    detect_language() in models.py cannot return;
  * every further file from the French and English pages whose text is new
    AND in a language not taken yet is an official translation, stored under
    "<docket> (FR)" / "<docket> (EN)": the build keeps one row per court +
    docket + date, so a translation needs its own docket to survive;
  * a fallback copy is remembered as a gap (re-probed after the gap TTL), so
    the nightly run does not download it again.

six_ser is on the quality gate's list of bodies whose official texts may
carry language 'en' (quality/checks/languages.py).

Licence: SER is a private company and claims copyright over its site; Jonas
decided on 2026-11-06 to ingest the sanction decisions (official-function
decisions of an exchange regulator).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from base_scraper import BaseScraper
from models import Decision, detect_language, extract_citations, make_decision_id
from scrapers.elcom import _extract_pdf_text  # same PDF-text helper as rab / weko

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ser-ag.com"
LISTING_PAGE = f"{BASE_URL}/de/resources/sanctions.html"

# Markers of court judgments SER lists but which the corpus holds as bger/bvger.
_SKIP_MARKERS = re.compile(r"Bundesgericht|Bundesverwaltungsgericht|Federal (Supreme |Administrative )?Court|"
                           r"Tribunal f[ée]d[ée]ral", re.I)
_ARBITRATION = re.compile(r"^(Schiedsentscheid|Schiedsspruch|Vergleich des Schiedsgerichts|Arbitration|Settlement)", re.I)
_REF = re.compile(r"\b((?:SaKo|SB|SER|BI|AB)(?:[- ][A-Za-z0-9]+)*[- ]?(?:[IVXLC]+|\d{3})/\d{2,4}"
                  r"(?:\s*/\s*[A-Z]+-[IVXLC]+/\d{2,4})*)")
_DECISION_TYPE = (
    (re.compile(r"^Vergleich", re.I), "Vergleich"),
    (_ARBITRATION, "Schiedsentscheid"),
    (re.compile(r"^SaKo", re.I), "Sanktionsentscheid"),
    (re.compile(r"^(SB|SER)", re.I), "Sanktionsbescheid"),
)
_LANGS = {"de", "fr", "it", "en"}
LISTINGS = {lang: (f"{BASE_URL}/{lang}/resources/sanctions/_jcr_content/root/contentparsys/parsys/"
                   "assets_taxonomy_filt.results.json") for lang in ("de", "fr", "en")}
_EN_WORDS = re.compile(r"\b(?:the|of|and|to|which|shall|has|have|was|were|is|that|with)\b", re.I)
_OWN_WORDS = {
    "de": re.compile(r"\b(?:der|die|das|und|ist|nicht|wird|eine?)\b", re.I),
    "fr": re.compile(r"\b(?:le|la|les|et|est|des|une?|du)\b", re.I),
    "it": re.compile(r"\b(?:il|la|che|di|del|della|una?|non)\b", re.I),
}
_FILE_LANG = re.compile(r"[-_](de|fr|en|it)\.pdf$", re.I)


def detect_text_language(text: str) -> str:
    """de/fr/it as models.detect_language decides, unless English words
    outnumber that language's own function words (SER writes many decisions
    in English only)."""
    head = text[:6000]
    lang = detect_language(head)
    return "en" if len(_EN_WORDS.findall(head)) > len(_OWN_WORDS[lang].findall(head)) else lang


def file_stem(url: str) -> str:
    """The URL without its language suffix: the key that joins one decision's
    entries across the DE/FR/EN listings."""
    return _FILE_LANG.sub("", url)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _ms_date(ms) -> date | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def docket_for(item: dict) -> str:
    """SER's own reference. Arbitral awards and settlements are prefixed
    "Schiedsgericht" so they do not share a docket with the decision they
    review; items without a reference use the PDF's file stem."""
    title = _clean(item.get("title"))
    m = _REF.search(title)
    if m:
        ref = re.sub(r"\s+", " ", m.group(1)).strip()
        return f"Schiedsgericht {ref}" if _ARBITRATION.match(title) else ref
    stem = item["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[-_]de$", "", stem)
    return stem.upper()


def decision_type_for(item: dict, docket: str) -> str:
    title = _clean(item.get("title"))
    for rx, label in _DECISION_TYPE:
        if rx.search(title) or rx.search(docket):
            return label
    marker = item.get("marker") or ""
    if "Beschwerde" in marker or docket.startswith(("BI-", "AB-")):
        return "Beschwerdeentscheid"
    return "Entscheid"


def parse_results(data: dict) -> list[dict]:
    """The results.json -> one stub per decision (court judgments skipped)."""
    stubs, seen = [], set()
    for item in data.get("results") or []:
        url = item.get("url") or ""
        if not url.lower().endswith(".pdf"):
            continue
        if _SKIP_MARKERS.search(item.get("marker") or ""):
            continue
        docket = docket_for(item)
        if docket in seen:
            docket = f"{docket} {url.rsplit('/', 1)[-1].rsplit('.', 1)[0]}"
        seen.add(docket)
        desc = _clean(item.get("description"))
        parts = [p.strip() for p in desc.split("|")]
        summary = parts[1] if len(parts) >= 2 else desc
        stubs.append({
            "docket_number": docket,
            "stem": file_stem(url if url.startswith("http") else BASE_URL + url),
            "pdf_url": url if url.startswith("http") else BASE_URL + url,
            "decision_date": _ms_date(item.get("entryIntoForceDate")),
            "publication_date": _ms_date(item.get("publicationDate")),
            "chamber": _clean(item.get("marker")) or None,
            "regeste": desc or None,
            "title": f"{docket}: {summary}" if summary else docket,
            "decision_type": decision_type_for(item, docket),
            "topics": item.get("businessTopic") or [],
            "sanction": _clean(item.get("freeText")),
        })
    return stubs


class SIXSERScraper(BaseScraper):
    """SIX Exchange Regulation sanction decisions."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 120

    @property
    def court_code(self) -> str:
        return "six_ser"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._texts: dict[str, tuple[str, str] | None] = {}   # url -> (text, language)

    def _listing(self, lang: str) -> dict:
        try:
            return self.get(LISTINGS[lang]).json()
        except Exception as e:
            logger.error(f"[six_ser] {lang} listing failed: {e}")
            return {}

    def discover_new(self, since_date=None) -> Iterator[dict]:
        stubs = parse_results(self._listing("de"))
        logger.info(f"[six_ser] {len(stubs)} decisions listed")
        # Further files per decision from the French and English pages.
        extra: dict[str, list[tuple[str, str, str, str]]] = {}
        for lang in ("en", "fr"):
            for item in self._listing(lang).get("results") or []:
                url = item.get("url") or ""
                if not url.lower().endswith(".pdf") or _SKIP_MARKERS.search(item.get("marker") or ""):
                    continue
                url = url if url.startswith("http") else BASE_URL + url
                extra.setdefault(file_stem(url), []).append(
                    (lang, url, _clean(item.get("title")), _clean(item.get("description"))))
        found = 0
        for stub in stubs:
            if since_date and stub["decision_date"] and stub["decision_date"] < since_date:
                continue
            if not self.state.is_known(make_decision_id("six_ser", stub["docket_number"])):
                found += 1
                yield stub
            urls = {stub["pdf_url"]}
            for lang, url, title, desc in extra.get(stub["stem"], []):
                if url in urls:
                    continue
                urls.add(url)
                probe = self._probe_id(stub["docket_number"], url)
                if self.state.is_known(probe):
                    continue
                found += 1
                yield {**stub, "pdf_url": url, "variant_of": stub["pdf_url"], "probe_id": probe,
                       "listing_title": title, "listing_regeste": desc or stub.get("regeste")}
        logger.info(f"[six_ser] {found} new files to fetch")

    @staticmethod
    def _probe_id(docket: str, url: str) -> str:
        """State id of one translation file, known before its language is."""
        import hashlib
        return make_decision_id("six_ser", f"{docket} file {hashlib.sha1(url.encode()).hexdigest()[:10]}")

    def _text(self, url: str, docket: str) -> tuple[str, str] | None:
        if url not in self._texts:
            self._texts[url] = None
            try:
                resp = self.get(url)
            except Exception as e:
                logger.error(f"[six_ser] PDF download failed for {docket}: {e}")
                return None
            if not resp.content.lstrip()[:5].startswith(b"%PDF"):
                logger.warning(f"[six_ser] {docket}: not a PDF ({url})")
                return None
            text = _extract_pdf_text(resp.content)
            if not text or len(text.strip()) < 200:
                logger.warning(f"[six_ser] no text extracted for {docket} ({url})")
                return None
            text = self.clean_text(text)
            self._texts[url] = (text, detect_text_language(text))
        return self._texts[url]

    def fetch_decision(self, stub: dict) -> Decision | None:
        docket = stub["docket_number"]
        got = self._text(stub["pdf_url"], docket)
        if got is None:
            return None
        full_text, language = got
        title, regeste = stub.get("title"), stub.get("regeste")
        if stub.get("variant_of"):
            main = self._text(stub["variant_of"], docket)
            if main is None:
                return None
            if full_text == main[0] or language == main[1]:
                # the site's fallback copy of a language the decision has already
                self.state.mark_gap(stub["probe_id"])
                logger.info(f"[six_ser] {docket}: {stub['pdf_url']} repeats the {main[1]} text, skipped")
                return None
            self.state.mark_scraped(stub["probe_id"])
            docket = f"{docket} ({language.upper()})"
            lt = stub.get("listing_title") or ""
            parts = [x.strip() for x in (stub.get("listing_regeste") or "").split("|")]
            summary = parts[1] if len(parts) >= 2 else (title.split(": ", 1)[1] if title and ": " in title else "")
            title = f"{docket}: {summary}" if summary else (f"{docket}: {lt}" if lt and lt not in docket else docket)
            regeste = stub.get("listing_regeste") or regeste
        if language not in _LANGS:
            logger.warning(f"[six_ser] {docket}: language {language!r} not de/fr/it/en, skipped")
            return None
        return Decision(
            decision_id=make_decision_id("six_ser", docket),
            court="six_ser",
            canton="CH",
            chamber=stub.get("chamber"),
            docket_number=docket,
            decision_date=stub.get("decision_date"),
            publication_date=stub.get("publication_date"),
            language=language,
            title=title,
            legal_area="Börsenrecht (Kotierungsrecht)",
            regeste=regeste,
            decision_type=stub.get("decision_type"),
            full_text=full_text,
            source_url=LISTING_PAGE,
            pdf_url=stub["pdf_url"],
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape SIX Exchange Regulation sanction decisions")
    parser.add_argument("--max", type=int, default=5)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    scraper = SIXSERScraper()
    decisions = scraper.run(max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {d.language}  {len(d.full_text)} chars  {(d.title or '')[:60]}")
