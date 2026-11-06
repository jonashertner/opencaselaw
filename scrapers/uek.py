"""
UEK/COPA/TOB Scraper (Übernahmekommission / Commission des OPA / Swiss Takeover Board)
========================================================================================

Scrapes the Board's own rulings on public-tender-offer transactions from
takeover.ch.

Architecture:
- https://www.takeover.ch/transactions/all is ONE static page listing every
  transaction (nr 0001.. , 1997 to present) as
  ``<article class="transaction list-item">`` with a link to
  ``/transactions/detail/nr/<NNNN>``. No pagination (verified 2026-11-06:
  ~930 rows, all on one page).
- A transaction's detail page has two sections:
    * "Entscheide der UEK"    — the Board's own rulings, each an
      ``<article class="decision list-item">`` with the docket
      ("Eintscheid-Nr.: 0956 / 01"), a link to a document detail page
      (``/transactions/document/id/<id>``) and a PDF link
      (``/download/contentelements/nr/<id>/lang/<lang>``).
    * "Angebotsdokumente"     — offer-side paperwork (Angebotsprospekt,
      Voranmeldung, Fairness Opinion, Inserat, Medienmitteilung,
      (Zwischen-)Ergebnis, Verwaltungsratsbericht...), each an
      ``<article class="document list-item">``.
  Only the first class ("decision") is a Board ruling; this scraper never
  selects "document" articles — see "What counts as a ruling" below.
- The document detail page (``/transactions/document/id/<id>``) renders the
  ruling's full text as clean HTML (``<section class="content_text">``,
  ``Sachverhalt``/lettered paragraphs/numbered Erwägungen/Dispositiv table)
  — this is the primary text source, not the PDF, and is the one place the
  older (pre-2000) rulings carry a full title (the per-transaction listing's
  inline tooltip is sometimes empty, e.g. transaction 0001, but the document
  page's own ``<h3>`` always has it).
- Before 2009 (BEHG revision) the Board issued non-binding "Empfehlungen"
  (Recommandations/Raccomandazioni); only since then does it issue binding
  "Verfügungen" (Décisions/Decisioni). Both are the Board's own rulings and
  both are ingested; the only thing that differs is the label.

What counts as a ruling (ingested) vs. not (excluded):
- INGESTED: every ``article.decision.list-item`` — these are always the
  Board's own Verfügung/Empfehlung/Zwischenverfügung/Stellungnahme, never
  party material.
- EXCLUDED: every ``article.document.list-item`` under "Angebotsdokumente" —
  Angebotsprospekt/Voranmeldung/Inserat (offer prospectus and its drafts,
  prepared and published by the offeror, not the Board), Fairness Opinion
  (prepared by the target's bank), Verwaltungsratsbericht (the target
  board's own report), (Provisorisches/definitives) Ergebnis and
  Medienmitteilung (result notices and press releases by the parties). None
  of these is a ruling of the Takeover Board; they are the underlying
  transaction's paper trail, analogous to what weko.py's BGBM hub
  deliberately leaves out.

Language handling (verified 2026-11-06, see runbook note below): every
sampled ruling — 1997 through 2026, Empfehlung and Verfügung, German- and
French-drafted — links exactly one PDF for one language. Brute-forcing the
other three ``/lang/<x>`` slugs on a single-language ruling does NOT surface
a missing translation: it either comes back as an empty HTTP 200
(``Content-Length: 0``, ``Content-Type: text/html``) *or*, for an older
decision, silently mirrors the one real file's bytes under the "wrong"
language slug (confirmed on transaction 0001: ``/lang/de`` and ``/lang/fr``
are byte-identical SHA-256, both the French original; ``/lang/it``/``/lang/en``
are the empty stub). So the markup is trusted, not probed: this scraper reads
only the ``<a>`` actually present per decision row. UEK publishes each ruling
in one working language; an article that nonetheless links more than one
PDF (the "document list-item" rows do this routinely for offer paperwork,
repeating ``div.inner`` once per language) is, if it ever happens for a
"decision list-item" row, handled defensively in ``discover_new``: every
linked PDF is downloaded, hashed, and a byte-identical duplicate is dropped
before a decision_id is minted, exactly to guard against the mirroring
behaviour above. The genuinely distinct variants (not yet observed in
practice) each become their own Decision, decision_id suffixed by language
— the same "one id per (ruling, language)" shape used for FINMA Rundschreiben
in the practice/ pipeline.

Coverage: ~930 transactions as of 2026-11-06; only a fraction (roughly a
third, by sampling) carry a Board ruling — the rest are Meldeverfahren
notification-procedure exemptions with no Verfügung at all (e.g. transaction
0100, 0700, 0850: "kein Empfehlungserlass").
Rate limiting: 1.5 seconds (one listing fetch + two fetches — document page,
then PDF — per new ruling; a full crawl also fetches one detail page per
transaction, ~930 requests).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)
from scrapers.weko import _extract_pdf_text  # same PyMuPDF/pdfplumber helper, used only as a fallback
from scrapers import pdf_ocr

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.takeover.ch/transactions/all"
BASE_URL = "https://www.takeover.ch"

TRANSACTION_HREF_RE = re.compile(r"/transactions/detail/nr/(\d{4,})")
DOC_HREF_RE = re.compile(r"/transactions/document/id/(\d+)")
ENTSCHEID_NR_RE = re.compile(r"Eintscheid-Nr\.:\s*(\d+)\s*/\s*(\d+)")
LISTING_DATE_RE = re.compile(r"Datum:\s*(\d{2}\.\d{2}\.\d{4})")
LANG_SLUG_RE = re.compile(r"/lang/([a-z]{2})")

# Decision-type lead word -> canonical German bucket. Same convention as
# weko.py's LEAD_TYPE_NORMAL: normalise French/Italian lead words so
# decision_type is comparable across rulings regardless of drafting language.
TYPE_NORMALIZE = {
    "verfügung": "Verfügung", "décision": "Verfügung", "decisione": "Verfügung",
    "zwischenverfügung": "Zwischenverfügung", "décision intermédiaire": "Zwischenverfügung",
    "decisione intermedia": "Zwischenverfügung",
    "empfehlung": "Empfehlung", "recommandation": "Empfehlung", "raccomandazione": "Empfehlung",
    "stellungnahme": "Stellungnahme", "prise de position": "Stellungnahme",
    "presa di posizione": "Stellungnahme",
}
TYPE_LEAD_RE = re.compile(
    r"^(Verfügung|Zwischenverfügung|Empfehlung|Stellungnahme|"
    r"Décision intermédiaire|Décision|Recommandation|"
    r"Decisione intermedia|Decisione|Raccomandazione|Presa di posizione)\b",
    re.IGNORECASE,
)

# "vom DD. Monat YYYY" / "du DD mois YYYY" / "del DD mese YYYY" — same family
# as weko.py's VOM_DATE_PATTERN / DU_DATE_PATTERN, with an Italian leg added
# (UEK occasionally drafts in Italian).
_MONTHS_DE = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
_MONTHS_FR = "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
_MONTHS_IT = "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
VOM_DATE_RE = re.compile(rf"vom\s+(\d{{1,2}})\.?\s*({_MONTHS_DE})\s+(\d{{4}})", re.IGNORECASE)
DU_DATE_RE = re.compile(rf"du\s+(\d{{1,2}})\.?\s*(?:er)?\s*({_MONTHS_FR})\s+(\d{{4}})", re.IGNORECASE)
DEL_DATE_RE = re.compile(rf"del\s+(\d{{1,2}})\.?\s*({_MONTHS_IT})\s+(\d{{4}})", re.IGNORECASE)

# The 2009 BEHG revision gave the Board power to issue binding Verfügungen;
# before that its own rulings were non-binding Empfehlungen in every
# language. Used only when a ruling's own title carries no recognisable
# lead word at all (not observed since the document page always titles
# itself, but kept as a last-resort default rather than leaving the type
# unset).
_VERFUEGUNG_ERA = date(2009, 1, 1)


def _parse_title_date(title: str) -> str | None:
    m = VOM_DATE_RE.search(title)
    if m:
        return f"{m.group(1)}. {m.group(2)} {m.group(3)}"  # German: "3. November 2026"
    m = DU_DATE_RE.search(title)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"   # French: "17 décembre 1997" (no period)
    m = DEL_DATE_RE.search(title)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"   # Italian: "5 marzo 2010" (no period)
    return None


def _parse_title_type(title: str) -> str | None:
    m = TYPE_LEAD_RE.match(title.strip())
    if not m:
        return None
    return TYPE_NORMALIZE.get(" ".join(m.group(1).lower().split()))


def _full_title(title: str, stub: dict) -> str:
    """'Verfügung 956/01 vom 3. November 2026 — Meier Tobler Group AG
    (Anbieter: Ahlsell AB (publ), Stockholm, Schweden)'."""
    target, offeror = stub.get("target") or "", stub.get("offeror") or ""
    if not target or target in title:
        return title
    return f"{title} — {target}" + (f" (Anbieter: {offeror})" if offeror else "")


def parse_listing(html: str) -> list[dict]:
    """Parse the single /transactions/all page into transaction stubs (no
    network, no state). Shared by discover_new and the tests."""
    soup = BeautifulSoup(html, "html.parser")
    stubs: list[dict] = []
    seen: set[str] = set()
    for art in soup.select("article.transaction.list-item"):
        a = art.select_one("a.item-anchor[href]")
        if not a:
            continue
        m = TRANSACTION_HREF_RE.search(a["href"])
        if not m:
            continue
        nr = m.group(1)
        if nr in seen:
            continue
        seen.add(nr)
        # The row names the target company (col2 tooltip / <h3>) and, under
        # it, the offeror ("Anbieter: Ahlsell AB (publ), Stockholm, Schweden").
        # A ruling's own title is only "Verfügung 956/01 vom …", so these go
        # into the decision title to make it findable by company name.
        col2 = art.select_one(".col2")
        target = " ".join((col2.get("data-tooltip") or "").split()) if col2 else ""
        if not target:
            h3 = a.find("h3")
            target = re.sub(r"\s*\(\d{4}\)\s*$", "", " ".join(h3.get_text(" ", strip=True).split())) if h3 else ""
        offeror = ""
        p = art.select_one(".col2 p")
        if p:
            offeror = re.sub(r"^(Anbieter|Offrant|Offerente|Offeror)\s*:\s*", "",
                             " ".join(p.get_text(" ", strip=True).split()), flags=re.I)
        stubs.append({
            "transaction_nr": nr,
            "detail_url": urljoin(BASE_URL, a["href"].rstrip("\\")),
            "target": target,
            "offeror": offeror,
        })
    return stubs


def parse_decision_articles(html: str, transaction_nr: str) -> list[dict]:
    """Parse the 'Entscheide der UEK' rulings of one transaction detail page.

    Returns one stub per PDF link actually present. A ruling publishes in
    exactly one language in every case sampled (see module docstring), so
    this normally yields one stub per ``article.decision.list-item``; it
    iterates every ``div.inner`` so a ruling that one day repeats the row
    per language (as "Angebotsdokumente" rows already do) is picked up too.

    Deliberately never selects ``article.document.list-item`` — offer
    prospectuses, fairness opinions, board reports, result notices and press
    releases are not Board rulings (see module docstring)."""
    soup = BeautifulSoup(html, "html.parser")
    stubs: list[dict] = []
    for art in soup.select("article.decision.list-item"):
        nr_span = art.select_one(".col1")
        nr_m = ENTSCHEID_NR_RE.search(nr_span.get("data-tooltip", "")) if nr_span else None
        if nr_m:
            docket = f"{int(nr_m.group(1))}/{nr_m.group(2)}"
        else:
            # No "Eintscheid-Nr." tooltip at all — fall back to the
            # transaction number with a '/01' suffix (only correct for a
            # transaction with a single ruling; not observed to be needed).
            docket = f"{int(transaction_nr)}/01"

        date_span = art.select_one(".col0")
        dm = LISTING_DATE_RE.search(date_span.get("data-tooltip", "")) if date_span else None
        listing_date = dm.group(1) if dm else None

        doc_id = None
        title_a = art.select_one("a.item-anchor[href]")
        if title_a:
            dm2 = DOC_HREF_RE.search(title_a["href"])
            doc_id = dm2.group(1) if dm2 else None
        if not doc_id:
            continue  # no document page to fetch — nothing we can ingest

        for inner in art.select("div.inner"):
            dl = inner.select_one('a[href*="/download/contentelements/"]')
            if not dl:
                continue
            pdf_url = urljoin(BASE_URL, dl["href"].rstrip("\\"))
            lm = LANG_SLUG_RE.search(pdf_url)
            stubs.append({
                "transaction_nr": transaction_nr,
                "doc_id": doc_id,
                "docket_number": docket,
                "listing_date": listing_date,
                "pdf_url": pdf_url,
                "url_lang": lm.group(1) if lm else None,
            })
    return stubs


def _extract_decision_html_text(html: str) -> tuple[str | None, str | None]:
    """Parse one /transactions/document/id/<id> page into (full_text, title).

    The page's own ``<h3>`` is the authoritative title (it is never empty,
    unlike the per-transaction listing's inline tooltip — see transaction
    0001). ``content_text`` holds the ruling's Sachverhalt/Erwägungen/
    Dispositiv as plain HTML; <br> become newlines and each block element
    gets a line boundary, same approach as finma.py's vertical-table
    extractor."""
    soup = BeautifulSoup(html, "html.parser")
    h3 = soup.select_one("section.transactiondetail.title h3")
    title = h3.get_text(strip=True) if h3 else None

    section = soup.select_one("section.content_text")
    if not section:
        return None, title

    for br in section.find_all("br"):
        br.replace_with("\n")
    for tag in section.find_all(["p", "tr", "li", "h1", "h2", "h3", "h4"]):
        tag.insert_before("\n")
        tag.insert_after("\n")

    text = section.get_text(separator=" ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    text = text.strip()
    return (text or None), title


class UEKScraper(BaseScraper):
    """Scraper for UEK/COPA/TOB (Swiss Takeover Board) rulings."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60

    @property
    def court_code(self) -> str:
        return "uek"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            resp = self.get(LISTING_URL)
        except Exception as e:
            logger.error(f"[uek] failed to fetch listing: {e}")
            return

        transactions = parse_listing(resp.text)
        logger.info(f"[uek] {len(transactions)} transactions on {LISTING_URL}")

        found = 0
        for tx in transactions:
            nr = tx["transaction_nr"]
            try:
                dresp = self.get(tx["detail_url"])
            except Exception as e:
                logger.warning(f"[uek] detail fetch failed for transaction {nr}: {e}")
                continue

            variants = parse_decision_articles(dresp.text, nr)
            for v in variants:
                v["target"], v["offeror"] = tx.get("target", ""), tx.get("offeror", "")
            if not variants:
                continue  # Meldeverfahren exemption etc. — no Board ruling here

            by_doc: dict[str, list[dict]] = {}
            for v in variants:
                by_doc.setdefault(v["doc_id"], []).append(v)

            for doc_id, vs in by_doc.items():
                docket = vs[0]["docket_number"]

                if len(vs) == 1:
                    v = vs[0]
                    decision_id = make_decision_id("uek", docket)
                    if self.state.is_known(decision_id):
                        continue
                    if since_date and v.get("listing_date"):
                        parsed = parse_date(v["listing_date"])
                        if parsed and parsed < since_date:
                            continue
                    found += 1
                    yield {**v, "use_pdf_text": False, "lang_suffix": None}
                    continue

                # More than one PDF linked for the same ruling (routine for
                # "Angebotsdokumente" rows, not yet observed for a
                # "decision list-item" — handled anyway). Download, hash,
                # drop byte-identical duplicates before minting an id: see
                # module docstring (takeover.ch mirrors the one real file
                # under an absent language's slug instead of 404ing).
                seen_hashes: dict[str, str] = {}
                for v in vs:
                    try:
                        presp = self.get(v["pdf_url"])
                    except Exception as e:
                        logger.warning(f"[uek] variant fetch failed {v['pdf_url']}: {e}")
                        continue
                    ctype = presp.headers.get("Content-Type", "") if hasattr(presp, "headers") else ""
                    content = getattr(presp, "content", b"") or b""
                    if not content or "text/html" in ctype:
                        continue  # placeholder for a language that doesn't exist
                    digest = hashlib.sha256(content).hexdigest()
                    if digest in seen_hashes:
                        logger.info(
                            f"[uek] {docket}: /lang/{v['url_lang']} duplicates "
                            f"/lang/{seen_hashes[digest]} — dropped"
                        )
                        continue
                    seen_hashes[digest] = v["url_lang"] or "?"
                    lang_suffix = v["url_lang"] or "x"
                    decision_id = make_decision_id("uek", f"{docket} ({lang_suffix.upper()})")
                    if self.state.is_known(decision_id):
                        continue
                    if since_date and v.get("listing_date"):
                        parsed = parse_date(v["listing_date"])
                        if parsed and parsed < since_date:
                            continue
                    found += 1
                    yield {**v, "use_pdf_text": True, "lang_suffix": lang_suffix}

        logger.info(f"[uek] Found {found} new rulings")

    def fetch_decision(self, stub: dict) -> Decision | None:
        doc_id = stub["doc_id"]
        docket = stub["docket_number"]
        lang_suffix = stub.get("lang_suffix")

        doc_url = f"{BASE_URL}/transactions/document/id/{doc_id}"
        try:
            resp = self.get(doc_url)
        except Exception as e:
            logger.error(f"[uek] failed to fetch document page for {docket}: {e}")
            return None

        full_text, title = _extract_decision_html_text(resp.text)

        if stub.get("use_pdf_text"):
            # Secondary language of a multi-variant ruling: the HTML document
            # page is shared by every variant and cannot be trusted to be in
            # this variant's language (see module docstring) — extract this
            # variant's own PDF instead.
            try:
                pdf_resp = self.get(stub["pdf_url"])
            except Exception as e:
                logger.error(f"[uek] failed to download PDF for {docket} ({lang_suffix}): {e}")
                return None
            full_text = _extract_pdf_text(pdf_resp.content)
        elif (not full_text or len(full_text.strip()) < 300) and stub.get("pdf_url"):
            # A few document pages carry only the ruling's heading (331/01:
            # "Beendigung des Aktienrückkaufprogramms"); the text is in the PDF.
            # 331/01's PDF is itself a scan, hence the OCR step (Tesseract on the
            # production host, same helper as elcom/postcom).
            try:
                pdf_bytes = self.get(stub["pdf_url"]).content
                pdf_text = _extract_pdf_text(pdf_bytes)
                if len((pdf_text or "").strip()) < pdf_ocr.MIN_TEXT_CHARS:
                    pdf_text = pdf_ocr.ocr_pdf_bytes(pdf_bytes)
            except Exception as e:
                logger.warning(f"[uek] PDF fallback failed for {docket}: {e}")
                pdf_text = ""
            if pdf_text and len(pdf_text.strip()) > len((full_text or "").strip()):
                full_text = pdf_text

        if not full_text or len(full_text.strip()) < 50:
            logger.warning(f"[uek] No usable text for {docket} (doc {doc_id})")
            return None

        full_text = self.clean_text(full_text)

        date_str = (title and _parse_title_date(title)) or stub.get("listing_date") or ""
        decision_date = parse_date(date_str)

        decision_type = (title and _parse_title_type(title)) or None
        if decision_type is None:
            decision_type = "Empfehlung" if (decision_date and decision_date < _VERFUEGUNG_ERA) else "Verfügung"

        # A further language version needs its own docket: the build keeps one
        # row per court + docket + date (build_fts5 dedup pass 1), so "956/01"
        # twice would collapse. Same convention as six_ser: "956/01 (FR)".
        if lang_suffix:
            docket = f"{docket} ({lang_suffix.upper()})"
        decision_id_docket = docket

        return Decision(
            decision_id=make_decision_id("uek", decision_id_docket),
            court="uek",
            canton="CH",
            docket_number=docket,
            decision_date=decision_date,
            language=detect_language(full_text),
            title=_full_title(title or f"{decision_type} {docket}", stub),
            legal_area="Übernahmerecht",
            decision_type=decision_type,
            full_text=full_text,
            source_url=doc_url,
            pdf_url=stub.get("pdf_url"),
            cited_decisions=extract_citations(full_text),
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape UEK/COPA/TOB rulings")
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
    scraper = UEKScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(f"  {d.decision_id}  {d.decision_date}  {len(d.full_text)} chars  {(d.title or '')[:60]}")
    print(f"\nScraped {len(decisions)} UEK rulings")
