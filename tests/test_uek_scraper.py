"""Offline tests for the UEK/COPA/TOB scraper (scrapers/uek.py).

Golden HTML fixtures (invariant #8, no live network) mirror three real
takeover.ch pages captured 2026-11-06:
  - tests/fixtures/uek_listing.html       — a trimmed /transactions/all
  - tests/fixtures/uek_detail_0956.html   — a 2026 Verfügung with offer-side
                                             "Angebotsdokumente" that must be
                                             excluded (Angebotsprospekt,
                                             Fairness Opinion)
  - tests/fixtures/uek_detail_0001.html   — the 1997 Empfehlung whose
                                             per-transaction listing tooltip
                                             is empty
  - tests/fixtures/uek_detail_0100.html   — a Meldeverfahren exemption with
                                             no Board ruling at all
  - tests/fixtures/uek_docpage_4078.html  — the document page for 956/01
  - tests/fixtures/uek_docpage_301.html   — the document page for 0001/01
                                             (French, title not empty there)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.uek import (  # noqa: E402
    UEKScraper,
    _extract_decision_html_text,
    _parse_title_date,
    _parse_title_type,
    parse_decision_articles,
    parse_listing,
)

FIXTURES = REPO / "tests" / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


LISTING_HTML = _read("uek_listing.html")
DETAIL_0956_HTML = _read("uek_detail_0956.html")
DETAIL_0001_HTML = _read("uek_detail_0001.html")
DETAIL_0100_HTML = _read("uek_detail_0100.html")
DOCPAGE_4078_HTML = _read("uek_docpage_4078.html")
DOCPAGE_301_HTML = _read("uek_docpage_301.html")


# ── pure parsing helpers (no network, no state) ──────────────────────────


def test_parse_listing():
    stubs = parse_listing(LISTING_HTML)
    nrs = [s["transaction_nr"] for s in stubs]
    assert nrs == ["0957", "0956", "0100", "0001"]
    assert stubs[1]["detail_url"].endswith("/transactions/detail/nr/0956")


def test_parse_decision_articles_excludes_offer_documents():
    """0956 has one Board ruling and two offer-side documents (Angebotsprospekt,
    Fairness Opinion) that must never be picked up."""
    stubs = parse_decision_articles(DETAIL_0956_HTML, "0956")
    assert len(stubs) == 1
    s = stubs[0]
    assert s["docket_number"] == "956/01"
    assert s["doc_id"] == "4078"
    assert s["pdf_url"] == "https://www.takeover.ch/download/contentelements/nr/4078/lang/de"
    assert s["url_lang"] == "de"
    assert s["listing_date"] == "04.11.2026"


def test_parse_decision_articles_empty_title_still_yields_docket():
    """The 1997 case has an empty tooltip/h3 on the *listing* article, but the
    docket is still recoverable from 'Eintscheid-Nr.: 0001 / 01'."""
    stubs = parse_decision_articles(DETAIL_0001_HTML, "0001")
    assert len(stubs) == 1
    s = stubs[0]
    assert s["docket_number"] == "1/01"          # leading zero stripped from the transaction part
    assert s["doc_id"] == "301"
    assert s["url_lang"] == "fr"


def test_parse_decision_articles_no_ruling():
    """A pure notification-procedure exemption has no 'Entscheide der UEK'
    section at all — nothing to ingest."""
    assert parse_decision_articles(DETAIL_0100_HTML, "0100") == []


def test_extract_decision_html_text_recent_verfuegung():
    text, title = _extract_decision_html_text(DOCPAGE_4078_HTML)
    assert title == "Verfügung 956/01 vom 3. November 2026"
    assert "Sachverhalt" in text
    assert "Erwägungen" in text
    assert "Dispositiv" in text
    assert "FinfraG" in text


def test_extract_decision_html_text_old_french_empfehlung():
    text, title = _extract_decision_html_text(DOCPAGE_301_HTML)
    assert title == "Recommandation Compagnie Suisse de Réassurances du 17 décembre 1997"
    assert "Commission des OPA" in text
    assert "recommandation suivante" in text


def test_parse_title_date_all_three_languages():
    assert _parse_title_date("Verfügung 956/01 vom 3. November 2026") == "3. November 2026"
    assert _parse_title_date("Recommandation X du 17 décembre 1997") == "17 décembre 1997"
    assert _parse_title_date("Decisione 42/01 del 5 marzo 2010") == "5 marzo 2010"


def test_parse_title_type_normalizes_across_languages():
    assert _parse_title_type("Verfügung 956/01 vom 3. November 2026") == "Verfügung"
    assert _parse_title_type("Recommandation Compagnie Suisse du 17 décembre 1997") == "Empfehlung"
    assert _parse_title_type("Décision 42/01 du 1er janvier 2020") == "Verfügung"
    assert _parse_title_type("Keine Ahnung was das ist") is None


# ── scraper-level discover_new / fetch_decision, network mocked ──────────


def _fake_get(urls: dict[str, str]):
    """Return a .get(url) stand-in that serves canned .text by exact URL."""

    def fake_get(url, **k):
        class R:
            text = urls[url]

        return R()

    return fake_get


def _scraper(monkeypatch, tmp_path):
    s = UEKScraper(state_dir=tmp_path)
    pages = {
        "https://www.takeover.ch/transactions/all": LISTING_HTML,
        "https://www.takeover.ch/transactions/detail/nr/0957": DETAIL_0100_HTML,  # no ruling, reused fixture
        "https://www.takeover.ch/transactions/detail/nr/0956": DETAIL_0956_HTML,
        "https://www.takeover.ch/transactions/detail/nr/0100": DETAIL_0100_HTML,
        "https://www.takeover.ch/transactions/detail/nr/0001": DETAIL_0001_HTML,
        "https://www.takeover.ch/transactions/document/id/4078": DOCPAGE_4078_HTML,
        "https://www.takeover.ch/transactions/document/id/301": DOCPAGE_301_HTML,
    }
    monkeypatch.setattr(s, "get", _fake_get(pages))
    return s


def test_discover_new_finds_two_rulings_and_skips_the_exemption(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new())
    dockets = sorted(st["docket_number"] for st in stubs)
    assert dockets == ["1/01", "956/01"]
    assert all(st["lang_suffix"] is None and st["use_pdf_text"] is False for st in stubs)


def test_discover_new_since_filter_uses_listing_date(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new(since_date=date(2020, 1, 1)))
    # Only 956/01 (listing date 04.11.2026) survives; 1/01 (17.12.1997) is filtered out.
    assert [st["docket_number"] for st in stubs] == ["956/01"]


def test_discover_new_skips_known_decisions(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    s.state.mark_scraped("uek_956_01")
    stubs = list(s.discover_new())
    assert [st["docket_number"] for st in stubs] == ["1/01"]


def test_fetch_decision_recent_verfuegung(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stub = parse_decision_articles(DETAIL_0956_HTML, "0956")[0]
    stub["use_pdf_text"] = False
    stub["lang_suffix"] = None
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.decision_id == "uek_956_01"
    assert d.court == "uek"
    assert d.canton == "CH"
    assert d.docket_number == "956/01"
    assert d.decision_date == date(2026, 11, 3)        # from the title, not the 04.11.2026 listing date
    assert d.decision_type == "Verfügung"
    assert d.legal_area == "Übernahmerecht"
    assert d.title == "Verfügung 956/01 vom 3. November 2026"
    assert d.source_url == "https://www.takeover.ch/transactions/document/id/4078"
    assert d.pdf_url == "https://www.takeover.ch/download/contentelements/nr/4078/lang/de"
    assert "Sachverhalt" in d.full_text


def test_fetch_decision_old_empfehlung_gets_title_from_document_page(monkeypatch, tmp_path):
    """The listing tooltip for 0001/01 is empty; fetch_decision must still
    recover a real title/date/type from the document page itself."""
    s = _scraper(monkeypatch, tmp_path)
    stub = parse_decision_articles(DETAIL_0001_HTML, "0001")[0]
    stub["use_pdf_text"] = False
    stub["lang_suffix"] = None
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.decision_id == "uek_1_01"
    assert d.decision_date == date(1997, 12, 17)
    assert d.decision_type == "Empfehlung"
    assert d.title == "Recommandation Compagnie Suisse de Réassurances du 17 décembre 1997"
    assert d.language == "fr"


# ── defensive multi-language-variant dedup (not observed live, see module
#    docstring; takeover.ch mirrors a missing translation's bytes rather
#    than 404ing, so a hash check guards against minting a false duplicate) ─


DETAIL_MULTILANG_HTML = """
<section class="transactiondetail">
<h2>Entscheide der UEK</h2>
<article class="decision list-item" id="transaction9001">
  <div class="inner"><span class="col0 dv-tt" data-tooltip="Datum: 01.01.2020">01.01.2020</span><span class="col1 dv-tt" data-tooltip="Eintscheid-Nr.: 0700 / 01">0700 / 01</span><span class="col2 dv-tt" data-tooltip="Verfügung 700/01 vom 1. Januar 2020"><a class="item-anchor" href="/transactions/document/id/9001"><h3>Verfügung 700/01 vom 1. Januar 2020</h3></a></span><span class="col4"><a href="/download/contentelements/nr/9001/lang/de">PDF Version</a></span></div>
  <div class="inner"><span class="col0 dv-tt" data-tooltip="Datum: 01.01.2020">01.01.2020</span><span class="col1 dv-tt" data-tooltip="Eintscheid-Nr.: 0700 / 01">0700 / 01</span><span class="col2 dv-tt" data-tooltip="Verfügung 700/01 vom 1. Januar 2020"><a class="item-anchor" href="/transactions/document/id/9001"><h3>Verfügung 700/01 vom 1. Januar 2020</h3></a></span><span class="col4"><a href="/download/contentelements/nr/9001/lang/fr">PDF Version</a></span></div>
</article>
</section>
"""


def test_multilang_variant_dedup_drops_byte_identical_copy(monkeypatch, tmp_path):
    s = UEKScraper(state_dir=tmp_path)

    pages = {
        "https://www.takeover.ch/transactions/all": (
            '<article class="transaction list-item"><div class="inner">'
            '<a class="item-anchor" href="/transactions/detail/nr/0700"><h3>X</h3></a>'
            "</div></article>"
        ),
        "https://www.takeover.ch/transactions/detail/nr/0700": DETAIL_MULTILANG_HTML,
    }
    pdf_bytes = {
        "https://www.takeover.ch/download/contentelements/nr/9001/lang/de": (b"%PDF-1.4 real content", "application/octet-stream"),
        # takeover.ch's observed mirroring behaviour: a missing French version
        # comes back as the SAME bytes as German instead of 404ing.
        "https://www.takeover.ch/download/contentelements/nr/9001/lang/fr": (b"%PDF-1.4 real content", "application/octet-stream"),
    }

    def fake_get(url, **k):
        class R:
            pass

        r = R()
        if url in pages:
            r.text = pages[url]
        elif url in pdf_bytes:
            content, ctype = pdf_bytes[url]
            r.content = content
            r.headers = {"Content-Type": ctype}
        else:
            raise AssertionError(f"unexpected URL {url}")
        return r

    monkeypatch.setattr(s, "get", fake_get)
    stubs = list(s.discover_new())
    # Both /lang/de and /lang/fr are byte-identical -> only one real variant survives.
    assert len(stubs) == 1
    assert stubs[0]["use_pdf_text"] is True
    assert stubs[0]["lang_suffix"] == "de"


def test_listing_names_target_and_offeror_for_the_title():
    from scrapers.uek import _full_title, parse_listing
    rows = {t["transaction_nr"]: t for t in parse_listing((FIXTURES / "uek_listing.html").read_text(encoding="utf-8"))}
    assert rows["0956"]["target"] == "Meier Tobler Group AG"
    assert rows["0956"]["offeror"] == "Ahlsell AB (publ), Stockholm, Schweden"
    assert _full_title("Verfügung 956/01 vom 3. November 2026", rows["0956"]) == (
        "Verfügung 956/01 vom 3. November 2026 — Meier Tobler Group AG "
        "(Anbieter: Ahlsell AB (publ), Stockholm, Schweden)")
