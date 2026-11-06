"""
SIF — State Secretariat for International Finance: NRA reports and AIA guidance
================================================================================

sif.admin.ch mostly publishes news items about double-tax-treaty protocols
(press releases — excluded) and Botschaften/Bundesratsberichte that go
through the Bundesblatt (already in the Materialien corpus — excluded). What
is left, and genuinely SIF's own text, not reproduced elsewhere:

  * The Koordinationsgruppe zur Bekämpfung der Geldwäscherei und
    Terrorismusfinanzierung (KGGT)'s National Risk Assessment (NRA) report
    series, 2017-2024, on sif.admin.ch/{lang}/integrit(e/y/ä)(...)-financial-
    centre-style pages (de /de/integritaet-finanzplatzes, fr
    /fr/integrite-place-financiere, it /it/integrita-piazza-finanziaria,
    en /en/integrity-financial-centre) — parsed by sdweb.download_items.
  * "Anleitung zum Bundesgesetz zur Umsetzung von Empfehlungen des Global
    Forum" (/{lang}/anleitung-bundesgesetz-global-forum or /en/guidance-
    federal-act-global-forum) and "Erläuterungen zur AIAV"
    (/{lang}/gesetzliche-grundlagen-funktionsweise-aia or /en/legal-basis-aeoi).

Two of the KGGT NRA reports on that page are BYTE-IDENTICAL to documents
already scraped by mros.py from fedpol.admin.ch (verified by md5,
2026-11-06: the 2024 crypto-assets NRA and the 2024 proliferation-financing
NRA are mirrored on both sites) — excluded here to avoid ingesting the same
document twice under two source keys. A "Mandat der KGGT" (the group's terms
of reference) is organisational, not practice guidance, and is also
excluded, as is a commissioned "Rechtsgutachten" (expert legal opinion,
closer to secondary literature than official administrative practice) and
two "Regulierungsfolgenabschätzung" impact-assessment studies for a planned
beneficial-ownership register (legislative preparatory material, not
interpretive guidance). The "Bericht über die Schliessfächer..." and
"Bericht des Bundesrates zu virtuellen Währungen..." reports on the same
page are Bundesrat/Postulat reports (same two documents MROS's publications
page also links, from the same sif.admin.ch/efd.admin.ch hosting) and are
excluded for the same Materialien reason as in mros.py.

An SIF page announcing the OECD/Global Forum's annual AIA peer-review
report ("Bericht zum Prüfmechanismus...") carries no attached PDF — it is
prose summarising the report's conclusion, i.e. a press release — and is
not scraped.

Every item is matched by its DAM hash (the "sd-web/<hash>/" path segment),
not by title, both to find the historical reports precisely and to exclude
the two fedpol duplicates precisely regardless of which language page they
are seen on first.

This source is intentionally small: most of what sif.admin.ch publishes is
either a press release about a treaty protocol or a Bundesblatt document
already in Materialien, and Jonas asked to keep it that way rather than pad
it with either.

issuing_authority is "SIF".
"""
from __future__ import annotations

import logging
from typing import Iterator

from .base import PracticeScraper, dateline_iso, first_date_iso, month_year_year, slugify
from .langdedup import TextDedupMixin
from .sdweb import download_items

logger = logging.getLogger(__name__)

BASE = "https://www.sif.admin.ch"

NRA_PAGES = (
    ("de", f"{BASE}/de/integritaet-finanzplatzes"),
    ("fr", f"{BASE}/fr/integrite-place-financiere"),
    ("it", f"{BASE}/it/integrita-piazza-finanziaria"),
    ("en", f"{BASE}/en/integrity-financial-centre"),
)
GUIDANCE_PAGES = (
    # Slugs are NOT shared across languages on this site (unlike the sdweb
    # pattern elsewhere) — each was confirmed against the per-language
    # sitemap.xml, 2026-11-06.
    ("de", f"{BASE}/de/anleitung-bundesgesetz-global-forum"),
    ("fr", f"{BASE}/fr/instructions-loi-federale-forum-mondial"),
    ("it", f"{BASE}/it/guida-legge-federale-forum-globale"),
    ("en", f"{BASE}/en/guidance-federal-act-global-forum"),
    ("de", f"{BASE}/de/gesetzliche-grundlagen-funktionsweise-aia"),
    ("fr", f"{BASE}/fr/bases-legales-fonctionnement-ear"),
    ("it", f"{BASE}/it/basi-legali-funzionamento-aia"),
    ("en", f"{BASE}/en/legal-basis-aeoi"),
)

# DAM hash -> (doc_number, doc_type). Verified 2026-11-06.
_KNOWN = {
    "1JNNgFmywPl8": ("SIF-NRA Betrug und Phishing 2020", "bericht"),
    "RJ7tQm6O57xG": ("SIF-NRA Korruption als Geldwaeschereivortat 2019", "bericht"),
    "U-KOXMYn7poc": ("SIF-NRA Bargeldverwendung 2018", "bericht"),
    "oXadABkyhq2b": ("SIF-NRA Krypto-Assets und Crowdfunding 2018", "bericht"),
    "EvKJw72zi2-M": ("SIF-NRA Geldwaschereirisiken juristische Personen 2018", "bericht"),
    "h26qyxu6uaOR": ("SIF-NRA Non-Profit-Organisationen 2018", "bericht"),
    "8fENZKEykBLK": ("SIF-Bericht Zugang Grundbuchdaten 2017", "bericht"),
    "8aC7Tf4omnsG": ("SIF-Bericht Zugang Grundbuchdaten 2017 Tabelle 1", "bericht"),
    "fD7kwqkJDehh": ("SIF-Bericht Zugang Grundbuchdaten 2017 Tabelle 2", "bericht"),
    "yN5gXa0ZQoJH": ("SIF-Anleitung Global Forum", "wegleitung"),
    "bGkg5EhlWbIL": ("SIF-Erlaeuterungen AIAV", "erlaeuterung"),
}
# Organisational (not practice), a commissioned legal opinion, legislative
# impact-assessment studies, and the two Bundesrat/Postulat reports also
# linked from mros.py's page — all excluded, see module docstring.
_EXCLUDE = {
    "NAf8ZMw6s3Oe",   # Mandat der KGGT
    "U1LDw-MyVHrC",   # Rechtsgutachten Chappuis
    "Ec4Bt1DD3V2g",   # RFA Register wirtschaftlich Berechtigte
    "clN0NzMzws3V",   # RFA Register wirtschaftlich Berechtigte (BASS-Studie)
    "NDwiXE7fpQiL",   # Bericht Schliessfaecher (Bundesrat/Postulat)
    "5oG0fznjschF",   # Bericht des Bundesrates zu virtuellen Waehrungen
}
# Byte-identical to documents already in mros.py (verified by md5,
# 2026-11-06) — same KGGT reports mirrored on fedpol.admin.ch.
_DUPLICATE_OF_MROS = {
    "JLHZtwnau73c",   # = fedpol hash 9Mds5H0o9Q6E, NRA Proliferationsfinanzierung 2024
    "0IXfYWmDsC0v",   # = fedpol hash LhhAeulhWzj6, NRA Krypto-Assets Jan 2024
}


def _hash_from_url(url: str) -> str:
    parts = url.split("/sd-web/", 1)
    return parts[1].split("/", 1)[0] if len(parts) == 2 else ""


class SifScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "sif"
    ISSUING_AUTHORITY = "SIF"
    DEFAULT_DOC_TYPE = "bericht"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['sif_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            # See mros.py / estv_aia.py for why the order matters: the
            # explicit "Bern, <date>" dateline beats a plain scan (which
            # can land on an in-document revision-history table), and
            # file_date beats a bare "Month YYYY" mention, which beats a
            # last-resort wide scan.
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
        for lang, url in (*NRA_PAGES, *GUIDANCE_PAGES):
            html = self._page(url)
            if html is None:
                continue
            items = list(download_items(html, BASE))
            logger.info("[%s] %s: %d download items", self.SOURCE_KEY, url, len(items))
            for item in items:
                h = _hash_from_url(item["pdf_url"])
                if h in _EXCLUDE or h in _DUPLICATE_OF_MROS:
                    continue
                if h not in _KNOWN:
                    logger.warning("[%s] unrecognised document, skipped: %s (%s)",
                                   self.SOURCE_KEY, item["title"], item["pdf_url"])
                    continue
                number, doc_type = _KNOWN[h]
                stub = {
                    "pdf_url": item["pdf_url"],
                    "url": url,
                    "title": item["title"],
                    "doc_number": number,
                    "date": "",
                    "language": lang,
                    "doc_type": doc_type,
                    "topics": [number],
                    "file_date": item["file_date"],
                    "sif_id": f"{slugify(number)}_{(item['file_date'] or 'nodate').replace('-', '')}_{lang}",
                }
                out = self.with_body(stub, number)
                if out:
                    yield out
