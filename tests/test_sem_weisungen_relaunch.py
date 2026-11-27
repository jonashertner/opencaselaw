"""SEM Weisungen after the 2026 relaunch of sem.admin.ch.

The old index 404s and the PDFs moved to /dam/de/sd-web/<id>/<file>; the
scraper's link filter only knew /dam/sem/, so every weekly run reported
"+0 new" while 113 documents (all Asylgesetz Weisungen, the AIG chapter
pages) were never collected. Offline: the pages are small fixtures with the
new markup."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice import sem_weisungen as sem  # noqa: E402
from scrapers.practice.base import PracticeScraper  # noqa: E402

DAM = "https://www.sem.admin.ch/dam/de/sd-web"


def _item(file_id, fname, title, description="", file_date="1. Januar 2025"):
    desc = (f'<p class="download-item__description">{description}</p>'
            if description else "")
    return (
        f'<li><a class="download-item" href="{DAM}/{file_id}/{fname}" download="{fname}">'
        f'<svg></svg><div><h4 class="download-item__title">{title}</h4>{desc}'
        f'<p class="download-item__meta-info"><span class="meta-info__item">PDF</span>'
        f'<span class="meta-info__item">1.2 MB</span>'
        f'<span class="meta-info__item">{file_date}</span></p></div></a></li>')


def _card(path, label):
    return (f'<div role="link" class="card card--clickable" aria-label="Mehr über «{label}»">'
            f'<h3>{label}</h3><a class="btn" href="{path}" aria-label="Mehr über «{label}»">'
            f'</a></div>')


def _page(*parts):
    return ('<html><header><a href="/de/kontakt">Kontakt</a>'
            f'<a href="{DAM}/x/header-logo.pdf">x</a></header>'
            f'<main><ul class="list">{"".join(parts)}</ul></main></html>')


AIG_SECTION = _page(
    _item("ttbe", "weisungen-aig-d.pdf",
          "Weisungen und Erläuterungen I. Ausländerbereich (Weisungen AIG). Ohne Kapitel 4.",
          "(Stand 1. November 2026)", "1. November 2026"),
    _card("/de/4-aufenthalt-mit-erwerbstaetigkeit", "04 - Aufenthalt mit Erwerbstätigkeit"),
)
AIG_KAP4 = _page(
    # Listed again on the chapter page: the section page's entry wins.
    _item("ttbe", "weisungen-aig-d.pdf", "Weisungen AIG (chapter copy)"),
    _item("eL", "transportdienstleistungen.pdf",
          "Gemeinsames Rundschreiben SEM-SECO: Grenzüberschreitende Transportdienstleistungen"),
    '<li><a href="https://www.sem.admin.ch/dam/de/sd-web/f/formular.docx">Formular</a></li>',
)
ASYL_SECTION = _page(_card("/de/7-sozial-und-nothilfe", "7  Sozial- und Nothilfe"))
ASYL_7 = _page(
    _item("s7", "7_sozial_nothilfe.pdf",
          "7. Abgeltungen des Bundes für die Sozial- und für die Nothilfe",
          "(Stand 1. Januar 2026)"),
    _item("a3", "anh3-ansaetze-2026.pdf",
          "Anhang 3 zu Weisung III / 7: Anpassung der Pauschalansätze ab 1. Januar 2026"),
)
FZA_SECTION = _page(
    _item("9U", "20170228-rs-transportdienstleistungen.pdf",
          "Rundschreiben: Transportdienstleistungen (28. Februar 2017)", file_date="5. Mai 2024"),
)
WEITERE = _page(
    _item("u1", "ukr-vollzugsweisung-schutzstatus-s-d.pdf",
          "Vollzugsweisung Schutzstatus S Ukraine", file_date="26. November 2026"),
    _item("u3", "ukr-vollzugsweisung-schutzstatus-s-anhang-3-rus.pdf",
          "Anhang III - Verzichtserklärung Schutzstatus S (RUS)"),
)
CHRONO = _page(_item("c1", "20261101-weisungsaenderung-aig.pdf",
                     "1. November 2026: Weisungsänderung AIG"))

PAGES = {
    "/de/i-auslaenderbereich": AIG_SECTION,
    "/de/4-aufenthalt-mit-erwerbstaetigkeit": AIG_KAP4,
    "/de/ii-freizuegigkeitsabkommen": FZA_SECTION,
    "/de/iii-asylgesetz": ASYL_SECTION,
    "/de/7-sozial-und-nothilfe": ASYL_7,
    "/de/iv-integration": _page(),
    "/de/v-buergerrecht": _page(),
    "/de/vi-datenschutz-und-offentlichkeitsgesetz": _page(),
    "/de/vii-visa": _page('<a href="/de/aufenthalt-bis-90-tage" class="link">Aufenthalt</a>'),
    "/de/weitere-weisungen-und-rundschreiben-des-sem": WEITERE,
    "/de/chronologie": CHRONO,
}


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    monkeypatch.setattr(sem.SemWeisungenScraper, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sem, "MIN_DOCUMENTS", 1)

    def make(pages=PAGES, held=()):
        out = tmp_path / "sem_weisungen.jsonl"
        out.write_text("".join(json.dumps(r) + "\n" for r in held))
        s = sem.SemWeisungenScraper()

        def page(path):
            if path not in pages:
                raise RuntimeError(f"404 {path}")
            return pages[path]
        s._page = page
        return s
    return make


# ── page parsing ────────────────────────────────────────────────────────

def test_parse_download_items_new_markup():
    items = sem.parse_download_items(AIG_SECTION, "https://www.sem.admin.ch/de/i-auslaenderbereich")
    assert items == [{
        "pdf_url": f"{DAM}/ttbe/weisungen-aig-d.pdf",
        "title": "Weisungen und Erläuterungen I. Ausländerbereich (Weisungen AIG). Ohne Kapitel 4.",
        "description": "(Stand 1. November 2026)",
        "file_date": "2026-11-01",
    }]


def test_parse_ignores_header_forms_and_other_hosts():
    html = _page('<a href="https://example.org/dam/x.pdf">x</a>',
                 '<a href="/dam/de/sd-web/a/rel.pdf">relative</a>')
    items = sem.parse_download_items(html, "https://www.sem.admin.ch/de/x")
    assert [i["pdf_url"] for i in items] == ["https://www.sem.admin.ch/dam/de/sd-web/a/rel.pdf"]
    # .docx forms on the chapter page are not PDFs
    kap4 = sem.parse_download_items(AIG_KAP4, "https://www.sem.admin.ch/de/4")
    assert all(i["pdf_url"].endswith(".pdf") for i in kap4)


def test_old_dam_path_still_accepted():
    old = ("https://www.sem.admin.ch/dam/sem/de/data/rechtsgrundlagen/weisungen/fza/"
           "weisungen-fza-d.pdf.download.pdf/weisungen-fza-d.pdf")
    items = sem.parse_download_items(_page(f'<a href="{old}">VFP</a>'), "https://www.sem.admin.ch/")
    assert [i["pdf_url"] for i in items] == [old]


def test_chapter_cards():
    assert sem.parse_chapter_cards(ASYL_SECTION) == [("/de/7-sozial-und-nothilfe",
                                                     "7 Sozial- und Nothilfe")]
    assert sem.parse_chapter_cards(PAGES["/de/vii-visa"]) == []   # plain links are not chapters


def test_item_date_precedence():
    assert sem.item_date({"title": "Rundschreiben: X (23. Dezember 2010)",
                          "description": "", "file_date": "2020-10-28"}) == "2010-12-23"
    assert sem.item_date({"title": "Weisungen AIG", "description": "(Stand 30. Juni 2026)",
                          "file_date": "2026-07-01"}) == "2026-06-30"
    assert sem.item_date({"title": "Hotelmeldeschein", "description": "",
                          "file_date": "2013-10-03"}) == "2013-10-03"


def test_classify():
    assert sem.classify("Rundschreiben 28: Rückkehrhilfeprogramm Syrien", "weisung") == "rundschreiben"
    assert sem.classify("Gemeinsames Rundschreiben SEM-SECO: X", "weisung") == "rundschreiben"
    assert sem.classify("Anhang 3 zu Weisung III / 7", "weisung") == "weisung_anhang"
    assert sem.classify("Merkblatt zum Nachweis von Sprachkompetenzen", "weisung") == "merkblatt"
    assert sem.classify("A7/M8/K9 Gesuchsformular für Musiker", "weisung") == "formular"
    assert sem.classify("1. Das Asylverfahren", "weisung") == "weisung"
    assert sem.classify("Weisungen VFP", "rundschreiben") == "rundschreiben"


# ── discovery ───────────────────────────────────────────────────────────

def test_discovery_follows_chapters_and_labels(scraper):
    stubs = list(scraper().discover_documents())
    by_id = {s["doc_number"]: s for s in stubs}
    # chapter pages and the chronology are crawled
    assert {"7_sozial_nothilfe", "anh3-ansaetze-2026", "transportdienstleistungen",
            "20261101-weisungsaenderung-aig"} <= set(by_id)
    s7 = by_id["7_sozial_nothilfe"]
    assert s7["title"] == ("Asylgesetz — 7. Abgeltungen des Bundes für die Sozial- und für "
                           "die Nothilfe (Stand 1. Januar 2026)")
    assert s7["topics"] == ["Asylgesetz", "7 Sozial- und Nothilfe"]
    assert s7["date"] == "2026-01-01"
    assert s7["url"] == "https://www.sem.admin.ch/de/7-sozial-und-nothilfe"
    assert by_id["anh3-ansaetze-2026"]["doc_type"] == "weisung_anhang"
    # the section page's listing wins over the chapter page's copy
    aig = by_id["weisungen-aig"]
    assert aig["title"].startswith("AIG / Ausländerbereich — Weisungen und Erläuterungen")
    assert aig["date"] == "2026-11-01"
    assert aig["revision"] == f"{DAM}/ttbe/weisungen-aig-d.pdf|2026-11-01"
    assert [s["doc_number"] for s in stubs].count("weisungen-aig") == 1
    # RUS/UKR copies of the form are skipped, the German Weisung kept
    assert "ukr-vollzugsweisung-schutzstatus-s" in by_id
    assert not any(n.endswith("-rus") for n in by_id)


def test_held_documents_come_first(scraper):
    held = [{"doc_id": "sem_weisungen_20170228_rs_transportdienstleistungen",
             "content_hash": "h", "body_text": "x"}]
    stubs = list(scraper(held=held).discover_documents())
    assert stubs[0]["doc_number"] == "20170228-rs-transportdienstleistungen"


def test_page_error_fails_the_run(scraper):
    pages = {k: v for k, v in PAGES.items() if k != "/de/7-sozial-und-nothilfe"}
    with pytest.raises(RuntimeError, match="7-sozial-und-nothilfe"):
        list(scraper(pages=pages).discover_documents())


def test_too_few_documents_fails_before_fetching(scraper, monkeypatch):
    monkeypatch.setattr(sem, "MIN_DOCUMENTS", 120)
    s = scraper()
    fetched = []
    s.fetch_pdf_text = lambda url: fetched.append(url) or "text"
    with pytest.raises(RuntimeError, match="expected >= 120"):
        s.run()
    assert fetched == []


def test_production_floor_is_set():
    assert sem.MIN_DOCUMENTS >= 100


# ── run(): refresh, re-issue, twin text ────────────────────────────────

def test_run_refreshes_held_rows_and_skips_twin_text(scraper):
    held = [{"doc_id": "sem_weisungen_20170228_rs_transportdienstleistungen",
             "doc_number": "20170228-rs-transportdienstleistungen",
             "pdf_url": "https://www.sem.admin.ch/dam/sem/de/old.pdf",
             "content_hash": "old", "body_text": "Die Weisung ist alt."}]
    s = scraper(held=held)
    same = "Die Weisung über Transportdienstleistungen gilt für die Kantone. " * 5
    s.fetch_pdf_text = lambda url: same if "transport" in url else f"Die Weisung {url} gilt. " * 5
    summary = s.run()
    rows = [json.loads(x) for x in (s.output_path).read_text().splitlines()]
    ids = [r["doc_id"] for r in rows]
    # the held row was re-fetched (no stored revision) with its new URL
    refreshed = [r for r in rows[1:] if r["doc_id"] == held[0]["doc_id"]]
    assert refreshed and refreshed[0]["pdf_url"].endswith("/9U/20170228-rs-transportdienstleistungen.pdf")
    assert refreshed[0]["revision"].endswith("|2024-05-05")
    # the chapter-4 file with identical text was not stored as a twin
    assert "sem_weisungen_transportdienstleistungen" not in ids
    assert summary["skipped"] == 1 and summary["failed"] == 0

    # A second run with nothing changed fetches only the twin (unknown by
    # id, so it is fetched again and skipped again) and stores nothing.
    again = scraper(held=rows)
    fetched = []
    again.fetch_pdf_text = lambda url: (fetched.append(url) or same) if "transport" in url \
        else pytest.fail(f"re-fetched {url}")
    s2 = again.run()
    assert s2["new"] == 0
    assert fetched == [f"{DAM}/eL/transportdienstleistungen.pdf"]


def test_run_detects_a_reissue(scraper):
    first = scraper()
    first.fetch_pdf_text = lambda url: f"Die Weisung {url} gilt für die Kantone. " * 5
    first.run()
    rows = [json.loads(x) for x in first.output_path.read_text().splitlines()]
    pages = dict(PAGES)
    pages["/de/i-auslaenderbereich"] = AIG_SECTION.replace("1. November 2026</span>",
                                                           "1. Dezember 2026</span>")
    second = scraper(pages=pages, held=rows)
    fetched = []
    second.fetch_pdf_text = lambda url: (fetched.append(url)
                                         or f"Die Weisung {url} gilt ab Dezember. " * 5)
    second.run()
    assert fetched == [f"{DAM}/ttbe/weisungen-aig-d.pdf"]


def test_dedup_content_is_off_by_default():
    assert PracticeScraper.DEDUP_CONTENT is False
