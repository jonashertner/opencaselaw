"""EMARK 1993-1998 recovery (scrapers/emark.py) — offline.

The pre-1999 volumes of the ARK archive are one HTML file per PRINTED PAGE
(``{year}/{YY}{NN}{PPP}PUB.htm``: volume year, Nr., page) and are only reachable
through the keyword/statute indices ``d/stichw-98.htm`` / ``d/gesetz-98.htm``.
The enumeration ``{year}/{nr:02d}.htm`` that serves 1999-2006 404s on every one
of them, which the nightly reported as 196 NoneReturns.

Golden fixtures (verbatim from ark-cra.rekurskommissionen.ch, 2026-09-09,
re-encoded to UTF-8):
  emark_stichw98_excerpt.html   head + a slice of the keyword index
  emark_1993_9301001PUB.html    EMARK 1993 Nr. 1, S. 1 (German, judgment of 1992)
  emark_1998_9822191PUB.html    EMARK 1998 Nr. 22, S. 191 (French)
  emark_1999_01_head.html       head of the 1999+ one-file layout (French)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.emark import (  # noqa: E402
    BASE_URL,
    PRE1999_INDEX_URLS,
    YEAR_RANGES,
    EMARKScraper,
    extract_decision_date,
    html_to_text,
    parse_pre1999_index,
    pre1999_page_url,
)
from models import make_decision_id  # noqa: E402

FIX = REPO / "tests" / "fixtures"
INDEX_HTML = (FIX / "emark_stichw98_excerpt.html").read_text(encoding="utf-8")
PAGE_1993 = (FIX / "emark_1993_9301001PUB.html").read_text(encoding="utf-8")
PAGE_1998 = (FIX / "emark_1998_9822191PUB.html").read_text(encoding="utf-8")
PAGE_1999 = (FIX / "emark_1999_01_head.html").read_text(encoding="utf-8")


def _page(year: int, nr: int, page: int, body: str) -> str:
    """A minimal archive page in the pre-1999 table layout."""
    return (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Type" '
        'content="text/html;charset=utf-8"><title>EMARK - JICRA - GICRA '
        f"{year} {nr:02d} {page:03d}</title></head><body>"
        f'<table><tr><td><h1><strong>{year} / {nr}&nbsp; - <font size="5">{page}</font>'
        f"</strong></h1></td></tr><tr><td>{body}</td></tr></table></body></html>"
    )


class _Resp:
    def __init__(self, text: str):
        self.content = text.encode("utf-8")
        self.encoding = "utf-8"
        self.text = text


def _http_error(url: str, status: int) -> requests.HTTPError:
    r = requests.Response()
    r.status_code = status
    r.url = url
    return requests.HTTPError(str(status), response=r)


def _http_404(url: str) -> requests.HTTPError:
    return _http_error(url, 404)


def _scraper(monkeypatch, tmp_path, pages: dict[str, str]):
    """EMARKScraper whose GET is served from `pages` (url -> html); anything
    else is a 404, exactly like the static archive. Every 1999+ id is marked
    scraped so discovery only has the pre-1999 branch to talk about."""
    s = EMARKScraper(state_dir=tmp_path)
    for year, max_nr in YEAR_RANGES.items():
        if year >= 1999:
            for nr in range(1, max_nr + 1):
                s.state.mark_scraped(make_decision_id("emark", f"EMARK-{year}-{nr}"))
    calls: list[str] = []
    failing: dict[str, int] = {}   # url -> HTTP status of a non-404 failure

    def fake_get(url, **k):
        calls.append(url)
        if url in failing:
            raise _http_error(url, failing[url])
        if url in pages:
            return _Resp(pages[url])
        raise _http_404(url)

    monkeypatch.setattr(s, "get", fake_get)
    s._calls = calls  # type: ignore[attr-defined]
    s._pages = pages  # type: ignore[attr-defined]
    s._failing = failing  # type: ignore[attr-defined]
    return s


def _both_indices(html: str) -> dict[str, str]:
    return {u: html for u in PRE1999_INDEX_URLS}


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------

def test_index_hrefs_map_to_year_nr_and_pages():
    found = parse_pre1999_index(INDEX_HTML)
    # 9301001PUB → 1993 Nr. 1, S. 1; the index text says "1993 Nr. 1" / "S. 4"
    assert found[(1993, 1)] == {1, 4}
    assert found[(1997, 8)] == {53, 56}          # "1997 Nr. 8" + "1997 Nr. 8, S. 56"
    assert found[(1998, 22)] == {191}
    assert found[(1993, 33)] == {230}
    assert len(found) == 72
    assert {y: sum(1 for k in found if k[0] == y) for y in range(1993, 1999)} == {
        1993: 14, 1994: 10, 1995: 16, 1996: 9, 1997: 12, 1998: 11,
    }
    # the Mitteilung href (../1994/mit_d0494.htm) is not a decision
    assert "mit_d0494" in INDEX_HTML
    assert all(isinstance(k[1], int) for k in found)


def test_page_url_layout():
    assert pre1999_page_url(1993, 1, 1) == f"{BASE_URL}/1993/9301001PUB.htm"
    assert pre1999_page_url(1997, 8, 56) == f"{BASE_URL}/1997/9708056PUB.htm"
    assert pre1999_page_url(1998, 22, 191) == f"{BASE_URL}/1998/9822191PUB.htm"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_pre1999_is_index_driven_newest_first(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, _both_indices(INDEX_HTML))
    stubs = list(s.discover_new())
    assert len(stubs) == 72
    assert [u for u in s._calls] == list(PRE1999_INDEX_URLS)   # no page probing at discovery
    assert stubs[0]["docket_number"] == "EMARK-1998-34"
    assert stubs[-1]["docket_number"] == "EMARK-1993-1"
    first_1993 = next(x for x in stubs if x["docket_number"] == "EMARK-1993-1")
    assert first_1993["year"] == 1993 and first_1993["nr"] == 1
    assert first_1993["index_pages"] == [1, 4]
    assert first_1993["start_page"] == 1
    assert first_1993["url"] == f"{BASE_URL}/1993/9301001PUB.htm"
    assert first_1993["decision_id"] == "emark_EMARK-1993-1"
    # no enumerated pre-1999 URL of the old shape sneaks through
    assert not any(x["url"].endswith("/01.htm") for x in stubs)


def test_discover_merges_both_indices_and_dedupes(monkeypatch, tmp_path):
    only_a = '<a href="../1995/9505048PUB.htm"><i>1995</i> Nr. 5</a>'
    only_b = ('<a href="../1995/9505053PUB.htm"><i>1995</i> Nr. 5, S. 53</a>'
              '<a href="../1996/9641359PUB.htm"><i>1996</i> Nr. 41</a>')
    s = _scraper(monkeypatch, tmp_path, {PRE1999_INDEX_URLS[0]: only_a, PRE1999_INDEX_URLS[1]: only_b})
    stubs = {x["docket_number"]: x for x in s.discover_new()}
    assert set(stubs) == {"EMARK-1995-5", "EMARK-1996-41"}
    assert stubs["EMARK-1995-5"]["index_pages"] == [48, 53]


def test_discover_skips_scraped_but_not_gap_cached(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, _both_indices(INDEX_HTML))
    s.state.mark_scraped("emark_EMARK-1998-34")
    # the years of failed enumeration left these ids in the gap cache — the
    # index proves they exist, so the cache must not hide them
    s.state.mark_gap("emark_EMARK-1998-33")
    assert s.state.is_known("emark_EMARK-1998-33")
    dockets = [x["docket_number"] for x in s.discover_new()]
    assert "EMARK-1998-34" not in dockets
    assert "EMARK-1998-33" in dockets
    assert len(dockets) == 71


def test_discover_survives_one_index_down(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, {PRE1999_INDEX_URLS[1]: INDEX_HTML})
    assert len(list(s.discover_new())) == 72


def test_discover_since_1999_skips_indices(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, _both_indices(INDEX_HTML))
    assert list(s.discover_new(since_date=date(1999, 1, 1))) == []
    assert s._calls == []


def test_discover_since_1997_filters_years(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, _both_indices(INDEX_HTML))
    years = {x["year"] for x in s.discover_new(since_date=date(1997, 6, 1))}
    assert years == {1997, 1998}


# ---------------------------------------------------------------------------
# Fetch: page walk
# ---------------------------------------------------------------------------

def test_fetch_1993_walks_pages_forward_until_404(monkeypatch, tmp_path):
    pages = {
        pre1999_page_url(1993, 1, 1): PAGE_1993,
        pre1999_page_url(1993, 1, 2): _page(1993, 1, 2, "<p>Aus den Erwägungen:</p><p>1. Zweite Seite Text.</p>"),
        pre1999_page_url(1993, 1, 3): _page(1993, 1, 3, "<p>2. Dritte Seite Text.</p>"),
        pre1999_page_url(1993, 1, 4): _page(1993, 1, 4, "<p>3. Vierte und letzte Seite.</p>"),
    }
    s = _scraper(monkeypatch, tmp_path, pages)
    stub = {"docket_number": "EMARK-1993-1", "year": 1993, "nr": 1,
            "index_pages": [1, 4], "start_page": 1, "url": pre1999_page_url(1993, 1, 1)}
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.decision_id == "emark_EMARK-1993-1"
    assert d.docket_number == "EMARK-1993-1"
    assert d.court == "emark" and d.canton == "CH"
    assert d.decision_date == date(1992, 9, 1)          # "Urteil der ARK vom 1. September 1992"
    assert d.language == "de"
    assert d.source_url == pre1999_page_url(1993, 1, 1)
    assert d.title.startswith("Art. 16, 17a AsylG und 13 EMRK")
    assert d.regeste.startswith("Art. 16, 17a AsylG und 13 EMRK: Ausreisefrist")
    assert "Grundsatzentscheid: [1]" not in d.regeste
    assert "[1] Entscheid der Präsidentenkonferenz" not in d.regeste   # footnote body, not regeste
    # pages in order, each introduced by its own printed page header
    t = d.full_text
    assert t.index("1993 / 1 - 1") < t.index("Zweite Seite") < t.index("Dritte Seite") < t.index("letzte Seite")
    assert "1993 / 1 - 4" in t
    assert "EMARK - JICRA - GICRA" not in t                     # <title> stripped
    assert "Bei Nichteintretensentscheiden ist dem abgewiesenen Asylbewerber" in t
    # walk: page 0 is never asked for, page 5 was probed once and ended the decision
    probed = [u for u in s._calls]
    assert pre1999_page_url(1993, 1, 5) in probed
    assert pre1999_page_url(1993, 1, 6) not in probed
    assert not any(u.endswith("9301000PUB.htm") for u in probed)


def test_fetch_walks_backwards_to_the_true_start_page(monkeypatch, tmp_path):
    # index only cites S. 56 of 1997 Nr. 8; the decision starts on S. 53
    pages = {
        pre1999_page_url(1997, 8, p): _page(1997, 8, p, body)
        for p, body in {
            53: "<p>8. <u>Estratto della sentenza della CRA dell'8 aprile 1997</u></p>"
                "<p>Art. 5 PA: il provvedimento di stralcio non è una decisione.</p>",
            54: "<p>Pagina 54.</p>",
            55: "<p>Pagina 55.</p>",
            56: "<p>b) Nell'ambito della propria competenza funzionale.</p>",
            57: "<p>Pagina 57, fine.</p>",
        }.items()
    }
    s = _scraper(monkeypatch, tmp_path, pages)
    stub = {"docket_number": "EMARK-1997-8", "year": 1997, "nr": 8,
            "index_pages": [56], "start_page": 56, "url": pre1999_page_url(1997, 8, 56)}
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.source_url == pre1999_page_url(1997, 8, 53)
    assert d.decision_date == date(1997, 4, 8)
    t = d.full_text
    assert t.index("1997 / 8 - 53") < t.index("Pagina 54") < t.index("Pagina 55") < t.index("competenza") < t.index("fine")
    assert t.startswith("1997 / 8 - 53")
    # 52 probed once (404 = start boundary), 58 probed once (404 = end boundary)
    assert s._calls.count(pre1999_page_url(1997, 8, 52)) == 1
    assert s._calls.count(pre1999_page_url(1997, 8, 58)) == 1
    assert s._calls.count(pre1999_page_url(1997, 8, 51)) == 0


def test_fetch_hole_inside_indexed_range_does_not_truncate(monkeypatch, tmp_path):
    pages = {
        pre1999_page_url(1998, 22, 191): PAGE_1998,
        # 192 missing from the archive
        pre1999_page_url(1998, 22, 193): _page(1998, 22, 193, "<p>Page 193 suite.</p>"),
        pre1999_page_url(1998, 22, 194): _page(1998, 22, 194, "<p>Page 194 fin.</p>"),
    }
    s = _scraper(monkeypatch, tmp_path, pages)
    stub = {"docket_number": "EMARK-1998-22", "year": 1998, "nr": 22,
            "index_pages": [191, 193], "start_page": 191, "url": pre1999_page_url(1998, 22, 191)}
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.decision_date == date(1998, 6, 18)      # "décision de la CRA du 18 juin 1998"
    assert d.language == "fr"
    assert d.title == "Art. 14a, al. 4 LSEE : exigibilité de l'exécution du renvoi."
    assert "Page 193 suite" in d.full_text and "Page 194 fin" in d.full_text
    assert "Analyse de la situation en Ethiopie." in d.full_text
    # hard-wrapped source lines are joined, so the sentence is intact
    assert "ne peut pas être raisonnablement exigée si elle implique" in d.full_text


def test_fetch_start_page_404_returns_none(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, {})
    stub = {"docket_number": "EMARK-1996-3", "year": 1996, "nr": 3,
            "index_pages": [19], "start_page": 19, "url": pre1999_page_url(1996, 3, 19)}
    assert s.fetch_decision(stub) is None


def test_fetch_500_mid_walk_aborts_and_the_next_run_retries(monkeypatch, tmp_path):
    """A non-404 failure on page 2 must not persist a truncated decision:
    fetch_decision raises, the run loop counts an error, the id is neither
    scraped nor gap-cached, and the next run (archive healthy again) gets the
    whole decision. (Before the fix the pages fetched before the error were
    returned as the decision and mark_scraped hid the truncation for good.)"""
    pages = {
        pre1999_page_url(1993, 1, 1): PAGE_1993,
        pre1999_page_url(1993, 1, 2): _page(1993, 1, 2, "<p>Zweite Seite Text.</p>"),
        pre1999_page_url(1993, 1, 3): _page(1993, 1, 3, "<p>Dritte und letzte Seite.</p>"),
    }
    only = '<a href="../1993/9301001PUB.htm"><i>1993</i> Nr. 1</a>'
    pages.update(_both_indices(only))
    s = _scraper(monkeypatch, tmp_path, pages)
    s._failing[pre1999_page_url(1993, 1, 2)] = 500
    stub = {"docket_number": "EMARK-1993-1", "year": 1993, "nr": 1,
            "index_pages": [1], "start_page": 1, "url": pre1999_page_url(1993, 1, 1)}

    with pytest.raises(requests.HTTPError) as exc:
        s.fetch_decision(stub)
    assert exc.value.response.status_code == 500
    # the walk stopped at the failure: page 3 was never asked for
    assert pre1999_page_url(1993, 1, 3) not in s._calls

    # through the run loop: an error, no decision, nothing marked
    s._calls.clear()
    got = s.run()
    assert got == []
    assert s.last_run_errors == 1 and s.last_run_skips == 0
    s.mark_run_complete(got)
    assert not s.state.is_known("emark_EMARK-1993-1")

    # next night, archive healthy: the decision is re-discovered and complete
    s._failing.clear()
    got = s.run()
    assert [d.docket_number for d in got] == ["EMARK-1993-1"]
    t = got[0].full_text
    assert t.index("1993 / 1 - 1") < t.index("Zweite Seite Text") < t.index("letzte Seite")
    assert s.last_run_errors == 0


def test_fetch_500_in_backward_walk_aborts_too(monkeypatch, tmp_path):
    # index cites S. 56; walking back, S. 55 answers 500 (not 404): abort
    pages = {pre1999_page_url(1997, 8, p): _page(1997, 8, p, f"<p>Pagina {p}.</p>") for p in (53, 54, 55, 56, 57)}
    s = _scraper(monkeypatch, tmp_path, pages)
    s._failing[pre1999_page_url(1997, 8, 55)] = 503
    stub = {"docket_number": "EMARK-1997-8", "year": 1997, "nr": 8,
            "index_pages": [56], "start_page": 56, "url": pre1999_page_url(1997, 8, 56)}
    with pytest.raises(requests.HTTPError):
        s.fetch_decision(stub)
    assert pre1999_page_url(1997, 8, 54) not in s._calls
    assert pre1999_page_url(1997, 8, 57) not in s._calls
    # the 404 boundary is unchanged: without the failure the walk is complete
    s._failing.clear()
    d = s.fetch_decision(stub)
    assert d is not None and d.source_url == pre1999_page_url(1997, 8, 53)
    assert "Pagina 57" in d.full_text


def test_pre1999_regeste_stops_before_the_footnote_bodies():
    # EMARK 1993 Nr. 1: the summary block is four lines (DE head-note + text,
    # FR head-note + text); the fifth line of the page is the footnote body
    # "[1] Entscheid der Präsidentenkonferenz ..." and is not regeste.
    lines = [l for l in html_to_text(PAGE_1993).split("\n") if l.strip()]
    regeste = EMARKScraper._pre1999_regeste(lines)
    got = regeste.split("\n")
    assert got[0].startswith("Art. 16, 17a AsylG und 13 EMRK")
    assert got[1].startswith("Bei Nichteintretensentscheiden")
    assert got[2].startswith("Art. 16, 17a LA et 13 CEDH")
    assert got[3].startswith("En cas de décision de non-entrée en matière")
    assert len(got) == 4
    assert not any(l.startswith("[") for l in got)
    assert "Décision de principe : [2]" not in regeste
    # every regeste line is a verbatim line of the page (sliced, never composed)
    assert all(l in lines for l in got)


def test_run_end_to_end_reports_no_none_returns(monkeypatch, tmp_path):
    only = ('<a href="../1993/9301001PUB.htm"><i>1993</i> Nr. 1</a>'
            '<a href="../1998/9822191PUB.htm"><i>1998</i> Nr. 22</a>')
    pages = {
        pre1999_page_url(1993, 1, 1): PAGE_1993,
        pre1999_page_url(1993, 1, 2): _page(1993, 1, 2, "<p>Zweite Seite.</p>"),
        pre1999_page_url(1998, 22, 191): PAGE_1998,
    }
    pages.update(_both_indices(only))
    s = _scraper(monkeypatch, tmp_path, pages)
    got = s.run()
    assert [d.docket_number for d in got] == ["EMARK-1998-22", "EMARK-1993-1"]
    assert s.last_run_skips == 0 and s.last_run_errors == 0
    s.mark_run_complete(got)
    # idempotent: a second run has nothing to do and touches only the indices
    s._calls.clear()
    assert s.run() == []
    assert s._calls == list(PRE1999_INDEX_URLS)


def test_run_honours_max_decisions(monkeypatch, tmp_path):
    pages = {pre1999_page_url(1993, 1, 1): PAGE_1993, pre1999_page_url(1998, 22, 191): PAGE_1998}
    pages.update(_both_indices(INDEX_HTML))
    s = _scraper(monkeypatch, tmp_path, pages)
    got = s.run(max_decisions=1)
    # newest first: 1998 Nr. 34 is not served here, so it is a None return,
    # then 1998 Nr. 33 ... until 1998 Nr. 22 is found
    assert [d.docket_number for d in got] == ["EMARK-1998-22"]


# ---------------------------------------------------------------------------
# 1999+ layout unchanged, date fix shared
# ---------------------------------------------------------------------------

def test_fetch_1999_layout_still_parses(monkeypatch, tmp_path):
    url = f"{BASE_URL}/1999/01.htm"
    s = _scraper(monkeypatch, tmp_path, {url: PAGE_1999})
    d = s.fetch_decision({"docket_number": "EMARK-1999-1", "year": 1999, "nr": 1, "url": url})
    assert d is not None
    assert d.decision_id == "emark_EMARK-1999-1"
    assert d.decision_date == date(1999, 2, 1)      # "décision de la CRA du 1er février 1999"
    # (language is not asserted: the trimmed head is a trilingual Regeste)
    assert d.source_url == url
    assert "Art. 3, 17, al. 1 et 17a LAsi" in d.full_text


def test_enumeration_404_is_still_a_none_return(monkeypatch, tmp_path):
    url = f"{BASE_URL}/2006/33.htm"
    s = _scraper(monkeypatch, tmp_path, {})
    assert s.fetch_decision({"docket_number": "EMARK-2006-33", "year": 2006, "nr": 33, "url": url}) is None


def test_decision_date_is_the_header_date_not_a_cited_instrument():
    # EMARK 2001 Nr. 12 was served as 1949-08-12 (Geneva Conventions) because
    # the German pattern was tried first over the whole 2000-char window.
    text = (
        "EMARK - JICRA - GICRA 2001 12/094\n2001 / 12 - 094\n"
        "Extrait de la décision de la CRA du 15 mars 2001, F.H., Bosnie-Herzégovine\n"
        "Art. 3 LAsi, art. 50 du Protocole additionnel aux Conventions de Genève du 12 août 1949\n"
        "Art. 3 AsylG, Art. 50 des Zusatzprotokolls zu den Genfer Abkommen vom 12. August 1949\n"
    )
    assert extract_decision_date(text, 2001) == date(2001, 3, 15)

    # a French decision whose German Regeste cites a dated instrument
    text = ("Extraits de la décision de la CRA du 6 octobre 2005, M.O., Macédoine\n"
            "Kreisschreiben vom 14. Februar 2003\n")
    assert extract_decision_date(text, 2005) == date(2005, 10, 6)

    # only implausible dates → None (caller falls back to 1 January of the volume)
    assert extract_decision_date("Accord du 16 octobre 1980, art. 50", 2002) is None
    # volume N may carry a judgment of N-1 (EMARK 1993 Nr. 1 = 1 September 1992)
    assert extract_decision_date("Auszug aus dem Urteil der ARK vom 1. September 1992", 1993) == date(1992, 9, 1)
    assert extract_decision_date("Estratto della sentenza della CRA dell'8 aprile 1997", 1997) == date(1997, 4, 8)


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

def test_html_to_text_keeps_inline_runs_and_joins_wrapped_lines():
    html = ('<html><head><title>EMARK 1997 08 053</title></head><body><table><tr><td>'
            '<h1><strong>1997 / 8&nbsp; - <font size="5">53</font></strong></h1></td></tr>'
            '<tr><td><u>Decisione di principi</u>o: <a href="#1">[1]</a><br>'
            '<em>Art. 5 PA: il provvedimento di stralcio non è una\n'
            '    decisione ai sensi di questa disposizione.</em><br><br>'
            '1. Primo punto.<img src="x.gif"></td></tr></table></body></html>')
    assert html_to_text(html).split("\n") == [
        "1997 / 8 - 53",
        "Decisione di principio: [1]",
        "Art. 5 PA: il provvedimento di stralcio non è una decisione ai sensi di questa disposizione.",
        "1. Primo punto.",
    ]
