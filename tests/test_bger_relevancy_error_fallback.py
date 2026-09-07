"""relevancy.bger.ch lags the live site: on publication day it answers every
Neuheiten decision with BGer's "Document Dienstes ist fehlgeschlagen" page
(2026-09-07: 22 of 22) while search.bger.ch already serves the texts. The
page is a normal 200 with the site chrome (>500 chars), so fetch_decision
used to accept it as the document and never tried the Eurospider URL; the
decision then waited a day for the mirror. Offline."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import Decision, make_decision_id  # noqa: E402
from scrapers.bger import (  # noqa: E402
    DOCUMENT_SERVICE_ERROR, BgerScraper, _is_document_service_error,
)

ERROR_PAGE = (
    "<html><body><div class='chrome'>" + "menu " * 120
    + f"<p>Die Anfrage des {DOCUMENT_SERVICE_ERROR}.</p></body></html>"
)
JUDGMENT_PAGE = (
    "<html><body><div class='content'>4A_45/2026 Urteil vom 10. Juni 2026 "
    + "Die Beschwerde wird abgewiesen. " * 40 + "</div></body></html>"
)
SEARCH_URL = ("https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php"
              "?lang=de&type=show_document&highlight_docid=aza://10-06-2026-4A_45-2026")


class _Resp:
    ok = True

    def __init__(self, text, url="http://relevancy.bger.ch/php/aza/http/index.php"):
        self.text, self.url = text, url


class _NoIncapsula:
    @staticmethod
    def is_incapsula_blocked(text):
        return False


def scraper(monkeypatch, relevancy_text, search_text):
    sc = BgerScraper.__new__(BgerScraper)   # no network init
    sc.evg_only = False
    sc.until_date = None
    sc.neuheiten_only = False
    sc._incapsula = _NoIncapsula()
    calls = {"relevancy": 0, "search": 0}

    def fake_get(url, headers=None, **kw):
        calls["relevancy"] += 1
        return _Resp(relevancy_text)

    def fake_pow(url):
        calls["search"] += 1
        return _Resp(search_text, url=url)

    monkeypatch.setattr(sc, "get", fake_get)
    monkeypatch.setattr(sc, "_get_with_pow", fake_pow)
    monkeypatch.setattr(sc, "_mark_date", lambda *a, **k: None)

    def fake_parse(html, stub, source_url):
        if _is_document_service_error(html):
            return None            # mirrors the real parser's own check
        return Decision(decision_id=stub["decision_id"], court="bger", canton="CH",
                        docket_number=stub["docket_number"],
                        decision_date=stub["decision_date"], language="de",
                        full_text="x" * 100, source_url=source_url)
    monkeypatch.setattr(sc, "_parse_decision_html", fake_parse)
    return sc, calls


def stub():
    return {"decision_id": make_decision_id("bger", "4A_45/2026"),
            "docket_number": "4A_45/2026", "decision_date": date(2026, 6, 10),
            "url": SEARCH_URL, "language": "de"}


def test_error_page_is_recognised():
    assert _is_document_service_error(ERROR_PAGE)
    assert not _is_document_service_error(JUDGMENT_PAGE)
    assert not _is_document_service_error(None)


def test_mirror_error_page_falls_through_to_search_host(monkeypatch):
    sc, calls = scraper(monkeypatch, ERROR_PAGE, JUDGMENT_PAGE)
    dec = sc.fetch_decision(stub())
    assert dec is not None
    assert dec.source_url == SEARCH_URL
    assert calls == {"relevancy": 1, "search": 1}


def test_mirror_copy_is_used_when_present(monkeypatch):
    sc, calls = scraper(monkeypatch, JUDGMENT_PAGE, JUDGMENT_PAGE)
    dec = sc.fetch_decision(stub())
    assert dec is not None
    assert dec.source_url.startswith("http://relevancy.bger.ch/")
    assert calls == {"relevancy": 1, "search": 0}


def test_error_on_both_hosts_yields_none(monkeypatch):
    sc, calls = scraper(monkeypatch, ERROR_PAGE, ERROR_PAGE)
    assert sc.fetch_decision(stub()) is None
    assert calls == {"relevancy": 1, "search": 1}
