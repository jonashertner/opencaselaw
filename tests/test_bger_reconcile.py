"""BGer count reconciliation (2026-11-08): judgment years 2000 to mid-2024
were never re-swept after the 5f10adfe pagination fix, and the corpus rests on
the frozen entscheidsuche intake there (2C_1023/2018, 9C_806/2018, 1B_232/2010
missing). --reconcile compares each AZA window's hit count with the rulings
held in ALL bger shards and fetches only what no shard holds. Offline."""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from scrapers.bger import BgerScraper  # noqa: E402
import build_bger_held_index as bhi  # noqa: E402


# ── fake AZA pages ─────────────────────────────────────────────────────────

def aza_page(total: int, rows: list[tuple[str, str]]) -> str:
    """rows: (DD.MM.YYYY, docket)."""
    lis = "".join(
        f'<li><span><a href="/ext/eurospider/live/de/php/aza/http/index.php?'
        f'highlight_docid=aza%3A%2F%2F{d.replace(".", "-")}-{k.replace("/", "-")}">'
        f"{d} {k}</a></span><div><div>II. sozialrechtliche Abteilung</div>"
        f"<div>Invalidenversicherung</div><div>Titel</div></div></li>"
        for d, k in rows)
    return ('<html><body><div class="content">'
            f'<div class="ranklist_header center">{total} Urteile gefunden</div>'
            f'<div class="ranklist_content"><ol>{lis}</ol></div></div></body></html>')


class FakeState:
    def __init__(self, tmp: Path):
        self.state_file = tmp / "bger.jsonl"

    def is_known(self, decision_id):
        return False


class Resp:
    def __init__(self, text, url="https://www.bger.ch/x"):
        self.text = text
        self.url = url


def make_scraper(tmp: Path, held: list[tuple[str, str]], pages: dict, until: date):
    """pages: url-substring → html. held: (docket, YYYY-MM-DD)."""
    (tmp / "bger.held.tsv").write_text(
        "".join(f"{bhi.norm_docket(k)}\t{d}\n" for k, d in held))
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = FakeState(tmp)
    sc.until_date = until
    sc.evg_only = False
    sc.neuheiten_only = False
    sc._known_dates = None
    sc.reconcile = True
    sc._load_held_index()
    requested: list[str] = []

    def fake_get(url, retry=0):
        requested.append(url)
        for key in sorted(pages, key=len, reverse=True):
            if key in url:
                return Resp(pages[key], url)
        return Resp(aza_page(0, []), url)

    sc._get_with_pow = fake_get
    return sc, requested


def win(von: str, bis: str, page: int | None = None) -> str:
    s = f"von={von}&bis={bis}"
    return s + (f"&page={page}" if page else "")


def report(tmp: Path) -> list[dict]:
    p = tmp / "bger.reconcile.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture(autouse=True)
def _url_has_dates(monkeypatch):
    # Make the window dates visible in the URL whatever AZA_SEARCH_PATH looks like.
    monkeypatch.setattr(BgerScraper, "_aza_url",
                        staticmethod(lambda host, von, bis: f"{host}/aza?von={von}&bis={bis}"))


# ── window logic ───────────────────────────────────────────────────────────

def test_complete_window_costs_one_request_and_yields_nothing(tmp_path):
    held = [("9C_800/2018", "2018-12-13"), ("9C_801/2018", "2018-12-13"),
            ("2C_1/2018", "2018-12-14")]
    pages = {win("13.12.2018", "16.12.2018"): aza_page(3, [
        ("13.12.2018", "9C_800/2018"), ("13.12.2018", "9C_801/2018"),
        ("14.12.2018", "2C_1/2018")])}
    sc, req = make_scraper(tmp_path, held, pages, until=date(2018, 12, 16))
    assert list(sc._discover_via_search(date(2018, 12, 13))) == []
    assert len(req) == 1
    assert sc._reconcile_stats["complete"] == 1
    assert report(tmp_path)[-1]["event"] == "summary"


def test_short_window_pages_through_and_yields_only_the_missing(tmp_path):
    held = [(f"9C_{n}/2018", "2018-12-13") for n in range(800, 811)]   # 11 held
    rows = [("13.12.2018", f"9C_{n}/2018") for n in range(800, 811)]
    rows.append(("13.12.2018", "9C_806/2019"))                        # not held
    pages = {
        win("13.12.2018", "16.12.2018"): aza_page(12, rows[:10]),
        win("13.12.2018", "16.12.2018", 2): aza_page(12, rows[10:]),
    }
    sc, req = make_scraper(tmp_path, held, pages, until=date(2018, 12, 16))
    out = list(sc._discover_via_search(date(2018, 12, 13)))
    assert [s["docket_number"] for s in out] == ["9C_806/2019"]
    assert sum("&page=2" in u for u in req) == 1
    ev = [e for e in report(tmp_path) if e["event"] == "short"]
    assert ev and ev[0]["aza"] == 12 and ev[0]["held"] == 11


def test_es_held_ruling_is_never_refetched(tmp_path):
    """The held index, not scraper state, decides: state knows nothing here."""
    held = [("1B_231/2010", "2010-07-28")]
    pages = {win("28.07.2010", "31.07.2010"): aza_page(2, [
        ("28.07.2010", "1B_231/2010"), ("28.07.2010", "1B_232/2010")])}
    sc, _ = make_scraper(tmp_path, held, pages, until=date(2010, 7, 31))
    out = list(sc._discover_via_search(date(2010, 7, 28)))
    assert [s["docket_number"] for s in out] == ["1B_232/2010"]


def test_docket_held_under_another_date_is_reported_not_fetched(tmp_path):
    held = [("6B_371/2015", "2015-05-11")]               # our date differs
    pages = {win("04.05.2015", "07.05.2015"): aza_page(1, [("04.05.2015", "6B_371/2015")])}
    sc, _ = make_scraper(tmp_path, held, pages, until=date(2015, 5, 7))
    assert list(sc._discover_via_search(date(2015, 5, 4))) == []
    conflicts = [e for e in report(tmp_path) if e["event"] == "date_conflict"]
    assert conflicts == [{"event": "date_conflict", "docket": "6B_371/2015",
                          "aza_date": "2015-05-04", "held_dates": ["2015-05-11"],
                          "url": conflicts[0]["url"]}]


def test_split_window_skips_complete_days(tmp_path):
    # 4 days, 120 hits → daily split; day 1 complete, day 2 short by one.
    held = ([(f"8C_{n}/2018", "2018-11-20") for n in range(30)]
            + [(f"2C_{n}/2018", "2018-11-21") for n in range(29)])
    pages = {
        win("20.11.2018", "23.11.2018"): aza_page(120, []),
        win("20.11.2018", "20.11.2018"): aza_page(30, []),
        win("21.11.2018", "21.11.2018"): aza_page(30, [("21.11.2018", "2C_1023/2018")]),
    }
    sc, req = make_scraper(tmp_path, held, pages, until=date(2018, 11, 23))
    out = list(sc._discover_via_search(date(2018, 11, 20)))
    assert [s["docket_number"] for s in out] == ["2C_1023/2018"]
    # day 1 complete: no pagination for it; day 2 short: pages 2-3 requested
    assert not any("von=20.11.2018&bis=20.11.2018&page" in u for u in req)
    assert sum("von=21.11.2018&bis=21.11.2018&page" in u for u in req) == 2
    assert sc._reconcile_stats["days_complete"] >= 1


def test_without_reconcile_behaviour_is_unchanged(tmp_path):
    held = [("9C_800/2018", "2018-12-13")]
    pages = {win("13.12.2018", "16.12.2018"): aza_page(1, [("13.12.2018", "9C_800/2018")])}
    sc, _ = make_scraper(tmp_path, held, pages, until=date(2018, 12, 16))
    sc.reconcile = False
    out = list(sc._discover_via_search(date(2018, 12, 13)))
    assert [s["docket_number"] for s in out] == ["9C_800/2018"]   # state decides, as before


# ── guard rails ────────────────────────────────────────────────────────────

def test_missing_index_refuses_to_run(tmp_path):
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = FakeState(tmp_path)
    with pytest.raises(RuntimeError, match="held index"):
        sc._load_held_index()


def test_discover_new_rejects_recent_since_before_any_request(tmp_path):
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = FakeState(tmp_path)
    sc.reconcile = True
    sc.neuheiten_only = False
    sc.evg_only = False
    sc._init_session = lambda: pytest.fail("network touched")
    with pytest.raises(RuntimeError, match="backfill sweep"):
        list(sc.discover_new(date.today() - timedelta(days=10)))


def test_discover_new_loads_index_before_the_session(tmp_path):
    sc = BgerScraper.__new__(BgerScraper)
    sc.state = FakeState(tmp_path)
    sc.reconcile = True
    sc.neuheiten_only = False
    sc.evg_only = False
    sc._init_session = lambda: pytest.fail("network touched")
    with pytest.raises(RuntimeError, match="held index"):
        list(sc.discover_new(date(2010, 1, 1)))


# ── held index builder ─────────────────────────────────────────────────────

def test_builder_reads_all_shards_and_dedupes(tmp_path):
    a = tmp_path / "bger.jsonl"
    b = tmp_path / "es_bger.jsonl"
    a.write_text("\n".join(json.dumps(r) for r in [
        {"decision_id": "bger_9C_800_2018", "docket_number": "9C_800/2018",
         "docket_number_2": None, "decision_date": "2018-12-13", "court": "bger",
         "full_text": 'a "decision_date": "1999-01-01" quote'},
        {"decision_id": "bger_x", "docket_number": "4P.253/1999", "decision_date": None,
         "court": "bger"},
    ]) + "\n")
    b.write_text("\n".join(json.dumps(r) for r in [
        {"decision_id": "bger_9C_800_2018", "docket_number": "9C 800/2018",
         "decision_date": "2018-12-13", "court": "bger"},          # same ruling
        {"decision_id": "bger_1B_231_2010", "docket_number": "1B_231/2010",
         "decision_date": "2010-07-28T00:00:00", "court": "bger"},
        {"decision_id": "bge_x", "docket_number": "BGE 140 III 1",
         "decision_date": "2014-01-01", "court": "bge"},
    ]) + "\n")
    out = tmp_path / "bger.held.tsv"
    assert bhi.main(["--shard", str(a), "--shard", str(b),
                     "--shard", str(tmp_path / "missing.jsonl"), "--out", str(out)]) == 0
    assert out.read_text().splitlines() == ["1B2312010\t2010-07-28", "9C8002018\t2018-12-13"]
    assert not (tmp_path / "bger.held.tsv.tmp").exists()


def test_builder_refuses_empty_index(tmp_path):
    empty = tmp_path / "bger.jsonl"
    empty.write_text("")
    out = tmp_path / "bger.held.tsv"
    assert bhi.main(["--shard", str(empty), "--out", str(out)]) == 1
    assert not out.exists()


def test_norm_docket_matches_scraper():
    for d in ("4P.253/1999", "9C 806/2018", "i 123/05", None):
        assert bhi.norm_docket(d) == BgerScraper._norm_docket(d)
