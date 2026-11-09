"""be_direktionen: BKD and DIJ sources, legacy dockets, JAXForms pools (offline).

Found 2026-11-08 (entscheidsuche comparison, item #9): BKD and DIJ rulings of
2025-26 were missing because be_direktionen listed only GSI and KAIO. BKD
publishes a static table like GSI; DIJ publishes its Grundbuch and
Notariatsaufsicht rulings through JAXForms data pools that the browser renders,
so the page HTML holds no links. The pools' JSON endpoint is read directly.

Fixtures are cut from the live responses of 2026-11-08:
  be_direktionen_tables_20261108.html   two BKD rows (2025.BKD.2427, legacy
                                        300.01-19) and two GSI rows (2026.GSI.604,
                                        legacy GEF.2012-0035)
  be_dij_gba_pool_20261108.json         four Grundbuch entries (one French,
                                        two multi-docket numbers)
  be_dij_notariat_pool_20261108.json    five Notariat entries: one ruling listed
                                        twice (two holdings), one Verwaltungsgericht
                                        and one Bundesgericht ruling to leave out
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrapers.cantonal.be_direktionen as bd  # noqa: E402
from scrapers.cantonal.be_direktionen import BEDirektionenScraper  # noqa: E402

FIX = REPO / "tests" / "fixtures"
TABLES = (FIX / "be_direktionen_tables_20261108.html").read_text()
GBA = json.loads((FIX / "be_dij_gba_pool_20261108.json").read_text())
NOTARIAT = json.loads((FIX / "be_dij_notariat_pool_20261108.json").read_text())


class _Resp:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload
        self.content = b""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _scraper(monkeypatch, tmp_path, *, tables=TABLES, pools=None):
    pools = {"77e7438e": GBA, "3a44138f": NOTARIAT} if pools is None else pools
    s = BEDirektionenScraper(state_dir=tmp_path)
    calls = []

    def fake_get(url, params=None, **k):
        calls.append((url, params))
        if url == bd.POOL_URL:
            for prefix, payload in pools.items():
                if params["id"].startswith(prefix):
                    return _Resp(payload=payload)
            return _Resp(payload={"results": []})
        # only the BKD listing carries the fixture table; GSI/KAIO are empty
        return _Resp(tables if "bkd.be.ch" in url else "<html></html>")

    monkeypatch.setattr(s, "get", fake_get)
    s._calls = calls
    return s


def test_bkd_new_and_legacy_dockets(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, pools={})
    by = {x["docket_number"]: x for x in s.discover_new()}
    assert by["2025.BKD.2427"]["decision_date"] == "07.08.2025"
    assert by["2025.BKD.2427"]["chamber"] == "BKD"
    assert by["2025.BKD.2427"]["title"] == "Schülerin mit Long Covid"
    # "300.01-19" (BKD before 2020) has no year.DIR.N shape and was skipped
    assert by["300.01-19"]["decision_date"] == "03.05.2019"
    assert by["300.01-19"]["chamber"] == "BKD"
    # GEF (now GSI) before 2018: "GEF.2012-0035", the docket the frozen
    # be_weitere copy carries; chamber from the docket, not the listing
    assert by["GEF.2012-0035"]["chamber"] == "GEF"
    assert by["GEF.2012-0035"]["decision_date"] == "28.01.2014"
    assert by["GEF.2012-0035"]["pdf_url"].endswith("/rechtsprechung-alt/2012-0035.pdf")
    assert by["2026.GSI.604"]["chamber"] == "GSI"


def test_pool_request_shape(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    list(s.discover_new())
    pool_calls = [p for u, p in s._calls if u == bd.POOL_URL]
    assert {p["id"] for p in pool_calls} == {pid for _, pid, _, _ in bd.DATA_POOLS}
    for p in pool_calls:
        assert p["handler"] == "jaxGenericDataPoolHandler"
        assert p["paging"] == "false"
        assert p["displayID"] == "standard"


def test_dij_grundbuch_pool(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, tables="<html></html>")
    by = {x["docket_number"]: x for x in s.discover_new()}
    st = by["2025.DIJ.10481"]                      # the comparison's example
    assert st["decision_date"] == "2026-03-27"
    assert st["chamber"] == "DIJ"
    assert st["decision_type"] == "Beschwerdeentscheid"
    assert st["pdf_url"] == ("https://www.gba.dij.be.ch/content/dam/gba_dij/dokumente/de/"
                             "entscheide/Beschwerdeentscheid%202025.DIJ.10481%2027.03.2026.pdf")
    assert st["regeste"] and "<br" not in st["regeste"]
    assert st["abstract_fr"]
    assert st["legal_area"]
    # irregular spacing around "/" is normalised; chamber from the first docket
    assert "2019.JGK.6290 / 2019.JGK.6370" in by
    assert by["2019.JGK.6290 / 2019.JGK.6370"]["chamber"] == "JGK"
    assert "2022.DIJ.5528/5529" in by              # kept as published (= the frozen copy)


def test_dij_notariat_pool_merges_holdings_and_skips_courts(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, tables="<html></html>")
    stubs = list(s.discover_new())
    by = {x["docket_number"]: x for x in stubs}
    # Verwaltungsgericht and Bundesgericht rulings have their own scrapers
    assert "100.2017.129" not in by
    assert "2C_183/2018" not in by
    # one PDF, two holdings -> one ruling with both topics
    st = by["26.11-14.46"]
    assert sum(1 for x in stubs if x["docket_number"] == "26.11-14.46") == 1
    assert "Individualisierungspflicht" in st["title"] and "Eintragungsvoraussetzungen" in st["title"]
    assert "Individualisierungspflicht" in st["regeste"] and "Betreibungen" in st["regeste"]
    assert st["decision_type"] == "Disziplinarentscheid"
    assert st["chamber"] == "DIJ"                  # legacy number: the listing's directorate
    assert by["2023.DIJ.10685"]["decision_date"] == "2025-08-05"


def test_portal_count_is_every_listed_ruling(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    stubs = list(s.discover_new())
    # 4 table rows + 4 Grundbuch + 2 Notariat (5 entries: one merged, two courts)
    assert len(stubs) == 10
    assert s.portal_count == 10


def test_known_rulings_still_count_for_portal(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path)
    from models import make_decision_id
    s.state.mark_scraped(make_decision_id("be_direktionen", "2025.DIJ.10481"))
    stubs = list(s.discover_new())
    assert "2025.DIJ.10481" not in {x["docket_number"] for x in stubs}
    assert s.portal_count == 10


def test_pool_failure_is_logged_not_fatal(monkeypatch, tmp_path, caplog):
    s = _scraper(monkeypatch, tmp_path, pools={"77e7438e": {"results": []}, "3a44138f": NOTARIAT})
    stubs = list(s.discover_new())
    assert {x["docket_number"] for x in stubs} >= {"2025.BKD.2427", "26.11-14.46"}
    assert any("returned no entries" in r.message for r in caplog.records)


def test_fetch_uses_pool_metadata(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, tables="<html></html>")
    stub = {x["docket_number"]: x for x in s.discover_new()}["2025.DIJ.10481"]
    text = ("Direktion für Inneres und Justiz des Kantons Bern. Beschwerdeentscheid vom "
            "27. März 2026 in der Beschwerdesache betreffend Handänderungssteuer. " * 3)
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(bd, "_extract_pdf_text", lambda content: text)
    d = s.fetch_decision(stub)
    assert d.decision_id == "be_direktionen_2025.DIJ.10481"
    assert str(d.decision_date) == "2026-03-27"
    assert d.chamber == "DIJ"
    assert d.regeste == stub["regeste"]
    assert d.abstract_de == stub["regeste"] and d.abstract_fr == stub["abstract_fr"]
    assert d.source_url == d.pdf_url == stub["pdf_url"]


# --- DIJ file names that differ from the pool's DateiName (2026-11-09) -------------
# The night of 11-09 had 33 DIJ PDFs answer 404. Five resolve under a corrected
# name (whitespace collapsed, or the other language's folder and prefix); the
# rest are dead links and are cached as gaps so they are not retried nightly.

import requests  # noqa: E402

DAM = "/dam/gba_dij/dokumente/"


def test_pool_paths_collapse_whitespace():
    p = bd._pool_paths(DAM + "de/entscheide/Beschwerdeentscheid 2022.DIJ.295  15.11.2022.pdf")
    assert p[0] == DAM + "de/entscheide/Beschwerdeentscheid 2022.DIJ.295 15.11.2022.pdf"
    p = bd._pool_paths(DAM + "de/entscheide/Beschwerdeentscheid 2018.JGK.4099 \n"
                             "2018.JGK.4101 24.02.2020.pdf")
    assert p[0] == DAM + "de/entscheide/Beschwerdeentscheid 2018.JGK.4099 2018.JGK.4101 24.02.2020.pdf"


def test_pool_paths_other_language_folder():
    # listed under de/notariat as "Entscheid", stored under fr/notariat as "Decision"
    p = bd._pool_paths(DAM + "de/notariat/entscheide/Entscheid 26.11-15.64 09.09.2016.pdf")
    assert p[1] == DAM + "fr/notariat/entscheide/Decision 26.11-15.64 09.09.2016.pdf"
    # listed under fr as "Décision sur recours", stored under de as "Beschwerdeentscheid"
    p = bd._pool_paths(DAM + "fr/entscheide/Décision sur recours 2024.DIJ.8653 19.02.2025.pdf")
    assert p[1] == DAM + "de/entscheide/Beschwerdeentscheid 2024.DIJ.8653 19.02.2025.pdf"
    assert p[2] == DAM + "fr/entscheide/Decision sur recours 2024.DIJ.8653 19.02.2025.pdf"
    assert len(p) <= 3 and len(set(p)) == len(p)


def test_pool_paths_unknown_layout_is_left_alone():
    assert bd._pool_paths("/dam/other/x  y.pdf") == ["/dam/other/x y.pdf"]


def test_pool_stub_carries_candidates(monkeypatch, tmp_path):
    s = _scraper(monkeypatch, tmp_path, tables="<html></html>")
    st = {x["docket_number"]: x for x in s.discover_new()}["2025.DIJ.10481"]
    assert st["pdf_urls"][0] == st["pdf_url"]
    assert any("/fr/entscheide/Decision%20sur%20recours%202025.DIJ.10481" in u
               for u in st["pdf_urls"])


def _http_error(code):
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(f"{code} Client Error", response=resp)


TEXT = ("Direktion für Inneres und Justiz des Kantons Bern. Entscheid in der "
        "Notariatsaufsichtssache. " * 3)


def test_fetch_falls_back_to_the_next_name(monkeypatch, tmp_path):
    s = BEDirektionenScraper(state_dir=tmp_path)
    stub = {"docket_number": "26.11-15.64", "decision_date": "2016-09-09",
            "pdf_url": "https://h/a.pdf", "pdf_urls": ["https://h/a.pdf", "https://h/b.pdf"],
            "chamber": "26"}
    tried = []

    def fake_get(url, **k):
        tried.append(url)
        if url.endswith("a.pdf"):
            raise _http_error(404)
        return _Resp()

    monkeypatch.setattr(s, "get", fake_get)
    monkeypatch.setattr(bd, "_extract_pdf_text", lambda content: TEXT)
    d = s.fetch_decision(stub)
    assert tried == ["https://h/a.pdf", "https://h/b.pdf"]
    assert d.source_url == d.pdf_url == "https://h/b.pdf"


def test_fetch_dead_link_is_gap_cached(monkeypatch, tmp_path):
    s = BEDirektionenScraper(state_dir=tmp_path)
    stub = {"docket_number": "26.11-14.46", "pdf_url": "https://h/a.pdf",
            "pdf_urls": ["https://h/a.pdf", "https://h/b.pdf"]}

    def fake_get(url, **k):
        raise _http_error(404)

    monkeypatch.setattr(s, "get", fake_get)
    assert s.fetch_decision(stub) is None
    assert s.state.is_known("be_direktionen_26.11-14.46")


def test_fetch_transient_error_is_not_gap_cached(monkeypatch, tmp_path):
    s = BEDirektionenScraper(state_dir=tmp_path)
    stub = {"docket_number": "2025.DIJ.1", "pdf_url": "https://h/a.pdf",
            "pdf_urls": ["https://h/a.pdf", "https://h/b.pdf"]}
    tried = []

    def fake_get(url, **k):
        tried.append(url)
        raise _http_error(503)

    monkeypatch.setattr(s, "get", fake_get)
    assert s.fetch_decision(stub) is None
    assert tried == ["https://h/a.pdf"]          # no name-guessing on a server error
    assert not s.state.is_known("be_direktionen_2025.DIJ.1")


def test_static_table_stub_without_candidates_still_fetches(monkeypatch, tmp_path):
    s = BEDirektionenScraper(state_dir=tmp_path)
    stub = {"docket_number": "2026.GSI.604", "pdf_url": "https://h/g.pdf", "decision_date": ""}
    monkeypatch.setattr(s, "get", lambda url, **k: _Resp())
    monkeypatch.setattr(bd, "_extract_pdf_text", lambda content: TEXT)
    assert s.fetch_decision(stub).pdf_url == "https://h/g.pdf"
