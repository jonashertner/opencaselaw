"""German/Italian federal translations of UN texts on Switzerland
(scrapers/practice/un_ch_translations.py). Synthetic sd-web pages, no network."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice import un_ch_translations as t  # noqa: E402


def _page(*files):
    items = "".join(
        f'<a class="download-item" href="/dam/de/sd-web/{h}/{name}"><h4 class="download-item__title">{title}</h4>'
        f'<p class="download-item__meta-info">PDF 200 kB 1. Juli 2025</p></a>' for h, name, title in files)
    return f"<html><body><h2>Berichte</h2>{items}</body></html>"


PAGES = {
    t.BJ_CAT: _page(("aa", "cat-ber-6-schlussfolgerungen.pdf", "Schlussbemerkungen zum sechsten Bericht")),
    t.BJ_PACTS: _page(("bb", "schlussbem-staatenbericht-3.pdf", "Schlussfolgerungen 3"),
                      ("cc", "schlussbem-staatenbericht-4.pdf", "Schlussfolgerungen 4")),
    t.BJ_SPT: _page(("dd", "ber-spt-besuch-1-2019.pdf", "Bericht des SPT")),
    t.SECO_UN: _page(("ee", "Obs-DESC-finale-DE.pdf", "Abschliessende Bemerkungen 4"),
                     ("ff", "Synth%C3%A8seRecommandations-DE.pdf", "Empfehlungen des Ausschusses (Stand der Umsetzung)")),
    t.EDA_CERD: _page(("gg", "2021-CERD-Schlussbemerkungen-CH_DE.pdf", "Schlussbemerkungen CERD 2021")),
    t.BSV_CRC: _page(("hh", "ConcludingObservationsSwitzerlandDe.pdf", "Empfehlungen 2015"),
                     ("j8ZTFkc2sl5Q", "ConcludingObservationsSwitzerlandEng.pdf", "Empfehlungen 2021 ENG"),
                     ("ii", "schlussbemerkung_kr-ausschuss.pdf", "Empfehlungen OPSC"),
                     ("zz", "schlussbemerkungen-2027.pdf", "Schlussbemerkungen zum siebten Bericht")),
}


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    monkeypatch.setattr(t.UnChTranslationsScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(t.UnChTranslationsScraper, "REQUEST_DELAY", 0)
    s = t.UnChTranslationsScraper()

    def fake_get(url, **_):
        if url not in PAGES:
            raise AssertionError(f"unexpected page fetch {url}")
        return SimpleNamespace(text=PAGES[url], raise_for_status=lambda: None)

    s.get = fake_get
    s.fetch_pdf_text = lambda url: f"Schlussbemerkungen des Ausschusses. Text aus {url}. " * 30
    return s


def test_every_entry_resolves_and_rows_carry_the_un_symbol(scraper, caplog):
    with caplog.at_level(logging.WARNING):
        summary = scraper.run()
    assert summary["new"] == len(t.ENTRIES) and summary["failed"] == 0
    rows = {r["doc_number"]: r for r in map(json.loads, (scraper.OUTPUT_DIR / "un_ch_translations.jsonl").read_text().splitlines())}
    cat = rows["CAT/C/CHE/CO/6"]
    assert cat["issuing_authority"] == "UN-CAT" and cat["language"] == "de" and cat["date"] == "2010-05-25"
    assert cat["pdf_url"] == "https://www.bj.admin.ch/dam/de/sd-web/aa/cat-ber-6-schlussfolgerungen.pdf"
    assert cat["doc_id"] == "un_ch_translations_cat_c_che_co_6_de"
    assert any("nicht massgebend" in tp for tp in cat["topics"])
    assert rows["CRPD/C/CHE/CO/1"]["language"] == "it"
    assert rows["CAT/OP/CHE/ROSP/1"]["doc_type"] == "visit_report"
    assert rows["CRC/C/CHE/CO/5-6"]["pdf_url"].endswith("/j8ZTFkc2sl5Q/ConcludingObservationsSwitzerlandEng.pdf")
    # a new translation-looking file is reported, the SECO implementation table is not
    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "schlussbemerkungen-2027.pdf" in warnings
    assert "Synth" not in warnings


def test_missing_link_is_a_warning_not_a_row(scraper, caplog, monkeypatch):
    monkeypatch.setitem(PAGES, t.BJ_CAT, _page())
    with caplog.at_level(logging.WARNING):
        summary = scraper.run()
    assert summary["new"] == len(t.ENTRIES) - 1
    assert "CAT/C/CHE/CO/6: no link matching" in " ".join(r.getMessage() for r in caplog.records)


def test_all_pages_restructured_is_an_error(scraper, monkeypatch):
    for page in list(PAGES):
        monkeypatch.setitem(PAGES, page, _page())
    with pytest.raises(RuntimeError, match="none of the listed files"):
        scraper.run()


def test_replaced_file_is_refetched(scraper):
    scraper.run()
    PAGES[t.BJ_CAT] = _page(("NEWHASH", "cat-ber-6-schlussfolgerungen.pdf", "Schlussbemerkungen"))
    try:
        again = t.UnChTranslationsScraper()
        again.get, again.fetch_pdf_text = scraper.get, scraper.fetch_pdf_text
        assert again.run()["new"] == 1                       # only the re-uploaded file
    finally:
        PAGES[t.BJ_CAT] = _page(("aa", "cat-ber-6-schlussfolgerungen.pdf", "Schlussbemerkungen zum sechsten Bericht"))
