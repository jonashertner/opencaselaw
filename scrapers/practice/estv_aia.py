"""
ESTV — automatic exchange of information (AIA/CRS), FATCA and CbCR guidance
============================================================================

Federal tax administrative practice for international information exchange,
separate from the domestic-tax Kreisschreiben in estv_kreisschreiben.py
(DBST/VST/STA — a different page family, different subject matter, no
overlap checked and none found).

Pages (admin.ch "sd-web" platform, parsed by sdweb.download_items):

  AIA Wegleitung + technical Wegleitung:
    de /de/gesetzliche-grundlagen-und-publikationen-aia
    fr /fr/bases-legales-et-publications-ear
    it /it/pubblicazioni-e-basi-legali-sai
  AIA-Qualifikationsgremium FAQ (plain accordion Q&A, no PDF; DE/FR only —
    no IT/EN page exists):
    de /de/fragen-antworten-zum-aia-qualifikationsgremium
    fr /fr/questions-reponses-sur-lorgane-de-qualification-ear
    (parsed with stiftungsaufsicht.parse_faq — same accordion markup)
  CbCR Wegleitung + overview sheet (IT: Wegleitung only, no overview sheet):
    de /de/cbcr-publikationen   fr /fr/publications-sur-cbcr   it /it/pubblicazioni-cbcr
  FATCA guidance + info letters:
    de /de/amtshilfe-gemaess-fatca
    fr /fr/assistance-administrative-selon-fatca
    it /it/assistenza-amministrativa-nell-ambito-del-fatca

Every document is matched by its DAM hash (the "sd-web/<hash>/" path
segment, stable across display file names and languages), not by title —
titles vary too much by language to pattern-match reliably, and the hash is
how a fallback copy is detected: the FATCA "FATCA Information Delivery —
Technical Specification and User Guide" is in English only but served
identically under every language's URL (verified by md5, 2026-11-06) and is
tagged language="en" directly rather than whichever page it was found on.

The AIA Qualifikationsgremium FAQ was, until 2026-01-01, periodically folded
into Annex 4 of the AIA Wegleitung itself (the page says so explicitly) —
so the Wegleitung PDF already carries that older Q&A history; only the
individually-published Q&A from 2026-01-01 onward are scraped here as
separate rows.

Excluded, and why:
  * FATCA "Business & Validation Rules" and "FATCA-Update Rules" — raw XML
    field-validation tables for bank IT departments, not interpretive
    guidance (same reasoning as ehra.py skipping the BlueBook XML schema).
  * "FATCA Credential Form" — a form.
  * FATCA-Mitteilungen (fatca-mitteilungen-YYYYMMDD pages) — per-bank
    procedural notice bulletins naming individual institutions under a
    group request, not general guidance.
  * Amtshilfe auf Ersuchen / EOIR (double-tax-treaty administrative
    assistance on request) — a different legal basis from AIA's automatic
    exchange; out of scope for this source.
  * No QI (Qualified Intermediary) guidance was found published by ESTV —
    the QI program runs between banks and the US IRS directly.
  * The AIA Wegleitung page shows only the CURRENT edition of each
    document; ESTV does not keep a public archive of superseded editions
    (unlike FINMA circulars). Each edition's doc_id encodes its own
    publication date, so successor editions are added as new rows going
    forward, but history before this scraper's first run cannot be
    recovered from the live site.

doc_number is shared by all language rows and, since each edition's doc_id
encodes its date, by all future editions too (search_practice collapses to
the newest, include_superseded gives the history from here on).
"""
from __future__ import annotations

import logging
from typing import Iterator

from .base import PracticeScraper, dateline_iso, first_date_iso, month_year_year, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items
from .stiftungsaufsicht import parse_faq

logger = logging.getLogger(__name__)

BASE = "https://www.estv.admin.ch"

PAGES = (
    ("de", f"{BASE}/de/gesetzliche-grundlagen-und-publikationen-aia"),
    ("fr", f"{BASE}/fr/bases-legales-et-publications-ear"),
    ("it", f"{BASE}/it/pubblicazioni-e-basi-legali-sai"),
    ("de", f"{BASE}/de/cbcr-publikationen"),
    ("fr", f"{BASE}/fr/publications-sur-cbcr"),
    ("it", f"{BASE}/it/pubblicazioni-cbcr"),
    ("de", f"{BASE}/de/amtshilfe-gemaess-fatca"),
    ("fr", f"{BASE}/fr/assistance-administrative-selon-fatca"),
    ("it", f"{BASE}/it/assistenza-amministrativa-nell-ambito-del-fatca"),
)

FAQ_PAGES = (
    ("de", f"{BASE}/de/fragen-antworten-zum-aia-qualifikationsgremium"),
    ("fr", f"{BASE}/fr/questions-reponses-sur-lorgane-de-qualification-ear"),
)

# DAM hash -> (doc_number, doc_type). Verified 2026-11-06.
_KNOWN = {
    "hEtJr9vx6Ej-": ("ESTV-AIA-Wegleitung", "wegleitung"),
    "nawtcd6uyf89": ("ESTV-AIA-Technische-Wegleitung", "wegleitung"),
    "tNGbUp42L2fI": ("ESTV-CbCR-Technische-Wegleitung", "wegleitung"),
    "mukNU0ufuyOV": ("ESTV-CbCR-Ueberblick", "wegleitung"),
    "XoQmVpZrfpf1": ("ESTV-FATCA-Info-Gruppenersuchen", "mitteilung"),
    "YLRmHFVF3zHd": ("ESTV-FATCA-Info-Banken-US-Konten", "mitteilung"),
    "UNFYQV2wypTn": ("ESTV-FATCA-Info-Finanzdienstleister-US-Konten", "mitteilung"),
}
# English-only, served identically under every language path.
_EN_ONLY = {
    "1obWmL9z5Fud": ("ESTV-FATCA-User-Guide", "wegleitung"),
}
# Technical XML specs and a form — not prose guidance.
_SKIP = {
    "L2uOxgEr-d93",   # Business & Validation Rules FTA
    "mE7rJIQOHBKm",   # FATCA-Update Rules FTA
    "SYpGz0JIklio",   # FATCA Credential Form
}


def _hash_from_url(url: str) -> str:
    parts = url.split("/sd-web/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 else ""


class EstvAiaScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "estv_aia"
    ISSUING_AUTHORITY = "ESTV"
    DEFAULT_DOC_TYPE = "wegleitung"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['aia_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            # The explicit "Bern, <date>" dateline beats everything else —
            # the Technical Wegleitung opens "Bern, September 2026" but its
            # very next page is a "Dokumentengeschichte" revision table
            # starting "19.09.2017 Initiale Version", which a plain scan of
            # the first few hundred characters picks up instead. file_date
            # (the page's own stated date — used by the CbCR overview
            # sheet, which cites "Botschaft vom 23.11.2016 zum ALBAG" and
            # has no dateline of its own) beats a bare "Month YYYY"
            # mention, which beats a last-resort wide scan.
            doc["date"] = (dateline_iso(body_text[:500])
                            or stub.get("file_date", "")
                            or first_date_iso(body_text[:500])
                            or month_year_year(body_text[:500])
                            or first_date_iso(body_text[:2000]))
        return doc

    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def discover_documents(self) -> Iterator[dict]:
        seen_en_only: set[str] = set()
        for lang, url in PAGES:
            html = self._page(url)
            if html is None:
                continue
            items = list(download_items(html, BASE))
            logger.info("[%s] %s: %d download items", self.SOURCE_KEY, url, len(items))
            for item in items:
                h = _hash_from_url(item["pdf_url"])
                if h in _SKIP:
                    continue
                if h in _EN_ONLY:
                    if h in seen_en_only:
                        continue
                    seen_en_only.add(h)
                    number, doc_type = _EN_ONLY[h]
                    out_lang = "en"
                elif h in _KNOWN:
                    number, doc_type = _KNOWN[h]
                    out_lang = lang
                else:
                    logger.warning("[%s] unrecognised document, skipped: %s (%s)",
                                   self.SOURCE_KEY, item["title"], item["pdf_url"])
                    continue
                stub = {
                    "pdf_url": item["pdf_url"],
                    "url": url,
                    "title": item["title"],
                    "doc_number": number,
                    "date": "",
                    "language": out_lang,
                    "doc_type": doc_type,
                    "topics": [number],
                    "file_date": item["file_date"],
                    "aia_id": f"{slugify(number)}_{(item['file_date'] or 'nodate').replace('-', '')}_{out_lang}",
                }
                out = self.with_body(stub, number)
                if out:
                    yield out
        yield from self._faq()

    def _faq(self) -> Iterator[dict]:
        for lang, url in FAQ_PAGES:
            html = self._page(url)
            if html is None:
                continue
            qas = parse_faq(html)
            logger.info("[%s] %s: %d FAQ entries", self.SOURCE_KEY, url, len(qas))
            for qa in qas:
                number = f"ESTV-AIA-FAQ {slugify(qa['question'])[:50]}"
                yield {
                    "url": url,
                    "title": qa["question"],
                    "doc_number": number,
                    "date": "",
                    "language": lang,
                    "doc_type": "faq",
                    "topics": [number, "AIA-Qualifikationsgremium"],
                    "aia_id": f"faq_{slugify(qa['question'])[:50]}_{lang}",
                    "body_text": f"Frage: {qa['question']}\n\nAntwort: {qa['answer']}",
                }
