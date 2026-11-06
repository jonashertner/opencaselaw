"""
MROS — Meldestelle für Geldwäscherei / Money Laundering Reporting Office Switzerland
=====================================================================================

fedpol's financial-intelligence unit. Official texts live on
fedpol.admin.ch/{de,fr,it,en}/publikationen(-der-meldestelle-fuer-geldwaescherei)-mros
(admin.ch's Nuxt "sd-web" platform, parsed by sdweb.download_items):

  * Jahresberichte MROS 2015-2025 (annual reports), one PDF per language,
    DE/FR/IT/EN, all genuinely distinct per-language files (verified by byte
    comparison, 2026-11-06). Earlier editions (1998/2001-2014) are NOT
    published here any more: the page itself says publications older than
    10 years are archived at the Swiss National Library (Helveticat) and
    must be consulted there. This scraper does not reach into that archive
    — the pre-2015 Jahresberichte are a known gap, not an oversight.
  * Typology Report, Negative typologies in reporting, the FIAHT (Financial
    Intelligence Against Human Trafficking) guide, two sanctions-context
    Alerts (Hamas/PIJ, October 2023), and three sectoral National Risk
    Assessment (NRA) reports (crypto-assets, proliferation financing,
    legal entities/arrangements).

Several "Weitere Publikationen" items exist in ONE language only but are
served under every language's URL with byte-identical content: the Typology
Report and both Alerts are English-only (confirmed via md5 across all four
/dam/{lang}/sd-web/<hash>/ paths); the FIAHT guide and the crypto-assets NRA
have no Italian translation (the IT page silently serves the German file).
These are identified by the DAM hash — the stable path segment after
"sd-web/", independent of the display file name — rather than by title or
page language, and English-only items are tagged language="en" directly
(not whichever page happened to be fetched first) so the metadata matches
the actual text. TextDedupMixin.with_body then drops the byte-identical
fallback rows.

NOT ingested: "Weitere Berichte zur Integrität des Finanzplatzes" lower on
the same page (Regulierungsfolgenabschätzung zum Register wirtschaftlich
Berechtigter, the Rohstoffhandel- und Schliessfächer-Berichte, "Bericht des
Bundesrates zu virtuellen Währungen") — these are hosted on sif.admin.ch /
efd.admin.ch, read as Bundesrat/Postulat reports, and belong in the
Bundesblatt/Materialien corpus, not MROS practice (per Jonas's standing
instruction not to duplicate Botschaften/Bundesratsberichte into practice.db).
FATCA-Mitteilungen-style per-bank procedural notices do not exist for MROS
and are not a concern here.

issuing_authority is "MROS" (the office itself), matching the pattern used
for UEK/RAB/EHRA — the specific agency that issues the text, not its parent
department (fedpol / EJPD).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from .base import PracticeScraper, dateline_iso, first_date_iso, month_year_year, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items

logger = logging.getLogger(__name__)

BASE = "https://www.fedpol.admin.ch"
PAGES = (
    ("de", f"{BASE}/de/publikationen-der-meldestelle-fuer-geldwaescherei-mros"),
    ("fr", f"{BASE}/fr/publications-du-bureau-de-communication-en-matiere-de-blanchiment-dargent-mros"),
    ("it", f"{BASE}/it/pubblicazioni-del-ufficio-di-comunicazione-in-materia-di-riciclaggio-di-denaro-mros"),
    ("en", f"{BASE}/en/publications-of-the-money-laundering-reporting-office-switzerland-mros"),
)

_JB_TITLE = re.compile(r"Jahresbericht MROS|Rapport annuel MROS|Rapporto d.attività MROS|Annual Report MROS", re.I)
_JB_YEAR = re.compile(r"\b(20\d{2})\b")

# DAM hash (the "sd-web/<hash>/" path segment, stable across file-name
# variants and languages) -> (doc_number, doc_type). Verified 2026-11-06.
_KNOWN = {
    "kKMY3e-DvCNI": ("MROS-Negativtypologien 2025", "bericht"),
    "7V0keqwV8T0U": ("MROS-FIAHT-Guide", "wegleitung"),
    "LhhAeulhWzj6": ("MROS-NRA Krypto-Assets 2024", "bericht"),
    "9Mds5H0o9Q6E": ("MROS-NRA Proliferationsfinanzierung 2024", "bericht"),
    "Vy2bPXVcre3s": ("MROS-NRA Juristische Personen und Rechtsvereinbarungen 2026", "bericht"),
}
# Served identically (byte-for-byte, verified) under every language path —
# the real document is English-only. Tag it language="en" directly instead
# of whichever page it happened to be discovered on first.
_EN_ONLY = {
    "3cGUkqiTWrnf": ("MROS-Typologiebericht 2025", "bericht"),
    "WXatAj-tfELB": ("MROS-Alert Hamas-PIJ 2023-11-03", "mitteilung"),
    "UxRMAW2qMmjm": ("MROS-Alert-Addendum Hamas-PIJ 2023-12-05", "mitteilung"),
}

def _hash_from_url(url: str) -> str:
    parts = url.split("/sd-web/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 else ""


class MrosScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "mros"
    ISSUING_AUTHORITY = "MROS"
    DEFAULT_DOC_TYPE = "bericht"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['mros_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            # The explicit "Bern, <date>" dateline beats everything else:
            # a plain scan of the first few hundred characters can land on
            # an in-document revision-history table instead (an NRA/Alert
            # PDF has none, but see estv_aia.py / sif.py for a case that
            # does). file_date (the page's own stated date) beats a bare
            # "Month YYYY" mention, which beats a last-resort wide scan.
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
            logger.info("[%s] %s: %d items", self.SOURCE_KEY, lang, len(items))
            for item in items:
                if _JB_TITLE.search(item["title"]):
                    yield from self._jahresbericht(item, lang, url)
                    continue
                h = _hash_from_url(item["pdf_url"])
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
                    logger.warning("[%s] unrecognised publication, skipped: %s (%s)",
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
                    "topics": [number, "MROS"],
                    "file_date": item["file_date"],
                    "mros_id": f"{slugify(number)}_{out_lang}",
                }
                out = self.with_body(stub, number)
                if out:
                    yield out

    def _jahresbericht(self, item: dict, lang: str, page_url: str) -> Iterator[dict]:
        m = _JB_YEAR.search(item["title"])
        if not m:
            logger.warning("[%s] annual report without a year, skipped: %s",
                           self.SOURCE_KEY, item["title"])
            return
        year = m.group(1)
        number = f"MROS-Jahresbericht {year}"
        stub = {
            "pdf_url": item["pdf_url"],
            "url": page_url,
            "title": item["title"],
            "doc_number": number,
            "date": year,
            "language": lang,
            "doc_type": "bericht",
            "topics": [number, "MROS", "Jahresbericht"],
            "mros_id": f"jb_{year}_{lang}",
        }
        out = self.with_body(stub, number)
        if out:
            yield out
