"""Date-aware identity for BVGer, BStGer and BPatGer (audit 2026-11-16:
same-docket second rulings were dropped by docket-keyed ids, e.g. BStGer
SK.2019.12's merits judgment of 2021-04-23 behind its 2019-09-25 referral).
Offline. Mirrors tests/test_bger_docket_identity.py."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import backfill_docket_collisions as bf  # noqa: E402
from models import make_decision_id  # noqa: E402
from scrapers.bpatger import BPatGerScraper  # noqa: E402
from scrapers.bstger import BStGerScraper  # noqa: E402
from scrapers.bvger import BVGerScraper  # noqa: E402

CLASSES = {"bvger": BVGerScraper, "bstger": BStGerScraper, "bpatger": BPatGerScraper}
DOCKETS = {"bvger": "B-5028/2009", "bstger": "SK.2019.12", "bpatger": "S2017_003"}
FIRST, SECOND = "2019-09-25", "2021-04-23"


class FakeState:
    def __init__(self, known, state_file):
        self._seen = set(known)
        self.state_file = str(state_file)

    def is_known(self, decision_id):
        return decision_id in self._seen


def scraper_with(court, tmp_path, known, sidecar_lines):
    sc = CLASSES[court].__new__(CLASSES[court])     # no network init
    sc.state = FakeState(known, tmp_path / f"{court}.jsonl")
    if sidecar_lines is not None:
        (tmp_path / f"{court}.dates.txt").write_text(
            "\n".join(sidecar_lines) + ("\n" if sidecar_lines else ""))
    sc._load_known_dates()
    return sc


def base(court, docket=None):
    return make_decision_id(court, docket or DOCKETS[court])


def pair(court, d_iso, did=None, docket=None):
    b = base(court, docket)
    return f"{b}\t{d_iso}\t{did or b}"


def stub(court, d, docket=None):
    return {"docket_number": docket or DOCKETS[court], "decision_date": d}


def held(court, tmp_path):
    return scraper_with(court, tmp_path, {base(court)}, [pair(court, FIRST)])


# ── legacy mode: byte-for-byte the old docket-keyed behaviour ────────────

@pytest.mark.parametrize("court", CLASSES)
def test_legacy_without_sidecar_skips_held_docket(court, tmp_path):
    sc = scraper_with(court, tmp_path, {base(court)}, None)
    assert sc._known_dates is None
    assert sc._stub_filter(stub(court, SECOND)) is None
    s = sc._stub_filter(stub(court, SECOND, docket="X-1/2026"))
    assert s["decision_id"] == make_decision_id(court, "X-1/2026")


@pytest.mark.parametrize("court", CLASSES)
def test_legacy_never_writes_sidecar(court, tmp_path):
    sc = scraper_with(court, tmp_path, set(), None)
    sc._mark_date(base(court), SECOND, base(court))
    assert not (tmp_path / f"{court}.dates.txt").exists()


@pytest.mark.parametrize("court", CLASSES)
def test_empty_sidecar_is_legacy(court, tmp_path):
    sc = scraper_with(court, tmp_path, {base(court)}, [])
    assert sc._known_dates is None


@pytest.mark.parametrize("court", CLASSES)
def test_half_seeded_sidecar_falls_back_to_legacy(court, tmp_path):
    known = {make_decision_id(court, f"A-{i}/2020") for i in range(300)}
    lines = [pair(court, "2020-01-01", docket=f"A-{i}/2020") for i in range(10)]
    sc = scraper_with(court, tmp_path, known, lines)
    assert sc._known_dates is None


# ── date-aware mode ──────────────────────────────────────────────────────

@pytest.mark.parametrize("court", CLASSES)
def test_same_date_is_held(court, tmp_path):
    sc = held(court, tmp_path)
    assert sc._stub_filter(stub(court, FIRST)) is None
    assert sc._stub_filter(stub(court, date(2019, 9, 25))) is None


@pytest.mark.parametrize("court", CLASSES)
def test_other_date_gets_suffixed_id_with_real_docket(court, tmp_path):
    sc = held(court, tmp_path)
    s = sc._stub_filter(stub(court, SECOND))
    assert s["decision_id"] == make_decision_id(court, f"{DOCKETS[court]}-D20210423")
    assert s["docket_number"] == DOCKETS[court]
    # claimed for the rest of the run: listed twice, yielded once
    assert sc._stub_filter(stub(court, SECOND)) is None


@pytest.mark.parametrize("court", CLASSES)
def test_held_docket_undated_listing_is_skipped(court, tmp_path):
    sc = held(court, tmp_path)
    assert sc._stub_filter(stub(court, None)) is None


@pytest.mark.parametrize("court", CLASSES)
def test_new_docket_two_rulings_in_one_run(court, tmp_path):
    sc = held(court, tmp_path)
    a = sc._stub_filter(stub(court, "2026-01-05", docket="Z-9/2026"))
    b = sc._stub_filter(stub(court, "2026-03-01", docket="Z-9/2026"))
    assert a["decision_id"] == make_decision_id(court, "Z-9/2026")
    assert b["decision_id"] == make_decision_id(court, "Z-9/2026-D20260301")


@pytest.mark.parametrize("court", CLASSES)
def test_pair_not_in_state_is_retried(court, tmp_path):
    # crash between fetch (sidecar written) and durable write (state marked)
    did2 = make_decision_id(court, f"{DOCKETS[court]}-D20210423")
    sc = scraper_with(court, tmp_path, {base(court)},
                      [pair(court, FIRST), pair(court, SECOND, did=did2)])
    s = sc._stub_filter(stub(court, SECOND))
    assert s["decision_id"] == did2


@pytest.mark.parametrize("court", CLASSES)
def test_mark_appends_once(court, tmp_path):
    sc = held(court, tmp_path)
    did2 = make_decision_id(court, f"{DOCKETS[court]}-D20210423")
    sc._mark_date(base(court), SECOND, did2)
    sc._mark_date(base(court), SECOND, did2)
    lines = (tmp_path / f"{court}.dates.txt").read_text().splitlines()
    assert lines.count(f"{base(court)}\t{SECOND}\t{did2}") == 1


# ── scraper integration ──────────────────────────────────────────────────

def test_bvger_fetch_keeps_suffixed_id_and_records_pair(tmp_path):
    sc = held("bvger", tmp_path)
    s = sc._stub_filter({**stub("bvger", "2010-06-23"), "_source": "icefaces",
                         "title": "B-5028/2009", "content_text": "Urteil vom 23. Juni 2010 " * 10})
    dec = sc.fetch_decision(s)
    assert dec.decision_id == "bvger_B-5028_2009-D20100623"
    assert dec.docket_number == "B-5028/2009"
    assert f"bvger_B-5028_2009\t2010-06-23\t{dec.decision_id}" in \
        (tmp_path / "bvger.dates.txt").read_text()


def test_bvger_fetch_legacy_uses_docket_id(tmp_path):
    sc = scraper_with("bvger", tmp_path, set(), None)
    dec = sc.fetch_decision({"docket_number": "A-1/2026", "decision_date": "2026-01-02",
                             "_source": "icefaces", "title": "A-1/2026"})
    assert dec.decision_id == "bvger_A-1_2026"
    assert not (tmp_path / "bvger.dates.txt").exists()


def test_bvger_published_pass_keeps_two_rulings_of_one_docket(tmp_path, monkeypatch):
    sc = held("bvger", tmp_path)
    docs = [{"d": "2019-09-25"}, {"d": "2021-04-23"}, {"d": "2021-04-23"}]
    monkeypatch.setattr(sc, "_wl_search", lambda *a, **k: (docs, len(docs), False))
    monkeypatch.setattr(sc, "_wl_parse", lambda doc: stub("bvger", doc["d"]))
    out = list(sc._discover_weblaw_published(lookback_days=0))
    assert [s["decision_id"] for s in out] == ["bvger_B-5028_2009-D20210423"]


def test_bstger_fetch_keeps_suffixed_id(tmp_path):
    sc = held("bstger", tmp_path)
    s = sc._stub_filter({**stub("bstger", SECOND), "headnote": "Escroquerie par métier"})
    dec = sc.fetch_decision(s)
    assert dec.decision_id == "bstger_SK.2019.12-D20210423"
    assert dec.docket_number == "SK.2019.12"


LISTING = """<html><body>
<a href="/rechtsprechung/entscheidanzeige/98/">S2017_003: 31.01.2017: Action en cession</a>
<a href="/rechtsprechung/entscheidanzeige/102/">S2017_003: 25.04.2017: boucle déployante</a>
<a href="/rechtsprechung/entscheidanzeige/120/">O2020_001: 02.02.2020: Patentverletzung</a>
</body></html>"""

DETAIL = """<html><body><table class="tx-is-courtcases">
<tr><td>Prozessnummer</td><td>S2017_003</td></tr>
<tr><td>Entscheiddatum</td><td>25.04.2017</td></tr>
<tr><td>Art des Verfahrens</td><td>Summarisches Verfahren</td></tr>
</table></body></html>"""


def test_bpatger_listing_prefilter(tmp_path):
    sc = scraper_with("bpatger", tmp_path, {"bpatger_S2017_003"},
                      ["bpatger_S2017_003\t2017-01-31\tbpatger_S2017_003"])
    urls = [s["url"] for s in sc._stubs_from_listing(LISTING, set())]
    assert urls == ["https://www.bundespatentgericht.ch/rechtsprechung/entscheidanzeige/102/",
                    "https://www.bundespatentgericht.ch/rechtsprechung/entscheidanzeige/120/"]


def test_bpatger_listing_legacy_skips_held_docket(tmp_path):
    sc = scraper_with("bpatger", tmp_path, {"bpatger_S2017_003"}, None)
    urls = [s["url"] for s in sc._stubs_from_listing(LISTING, set())]
    assert urls == ["https://www.bundespatentgericht.ch/rechtsprechung/entscheidanzeige/120/"]


class _Resp:
    status_code = 200

    def __init__(self, text):
        self.text = text
        self.content = text.encode()


def test_bpatger_detail_page_decides_identity(tmp_path, monkeypatch):
    sc = scraper_with("bpatger", tmp_path, {"bpatger_S2017_003"},
                      ["bpatger_S2017_003\t2017-01-31\tbpatger_S2017_003"])
    monkeypatch.setattr(sc, "get", lambda url, **k: _Resp(DETAIL))
    dec = sc.fetch_decision({"url": "https://www.bundespatentgericht.ch/x/102/"})
    assert dec.decision_id == "bpatger_S2017_003-D20170425"
    assert dec.docket_number == "S2017_003"


# ── backfill script ──────────────────────────────────────────────────────

def test_seed_pairs_keeps_docket_ids_only(tmp_path):
    rows = [
        {"decision_id": "bstger_SK.2019.12", "court": "bstger",
         "docket_number": "SK.2019.12", "decision_date": "2019-09-25"},
        {"decision_id": "bstger_SK.2019.12-D20210423", "court": "bstger",
         "docket_number": "SK.2019.12", "decision_date": "2021-04-23"},
        {"decision_id": "bstger_TPF_2022_1", "court": "bstger",           # TPF entry
         "docket_number": "SK.2021.5", "decision_date": "2022-01-10"},
        {"decision_id": "bstger_RR.2009.50", "court": "bstger",           # undated
         "docket_number": "RR.2009.50", "decision_date": None},
        {"decision_id": "bvger_A-1_2020", "court": "bvger",               # other court
         "docket_number": "A-1/2020", "decision_date": "2020-01-01"},
    ]
    shard = tmp_path / "bstger.jsonl"
    shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    pairs, undated, foreign = bf.seed_pairs("bstger", [shard])
    assert pairs == ["bstger_SK.2019.12\t2019-09-25\tbstger_SK.2019.12",
                     "bstger_SK.2019.12\t2021-04-23\tbstger_SK.2019.12-D20210423"]
    assert (undated, foreign) == (1, 1)


def test_audit_dates_default_excludes_disagreements(tmp_path):
    tsv = tmp_path / "x.tsv"
    tsv.write_text("kind\tes_id\tdocket\tes_date\tour_dates\tour_ids\n"
                   "second_ruling\ta\tB-1/2010\t2010-06-23\t2010-03-08\tx\n"
                   "date_disagreement\tb\tB-2/2010\t2011-01-01\t2010-12-31\ty\n")
    assert bf.audit_dates("bvger", tsv, False) == [date(2010, 6, 23)]
    assert bf.audit_dates("bvger", tsv, True) == [date(2010, 6, 23), date(2011, 1, 1)]


# ── `_dYYYYMMDD` rows of the earlier BVGer recovery (2026-11-18 trap) ────
# bvger.jsonl holds 461 second rulings under `bvger_<docket>_dYYYYMMDD`
# (scripts/recover_bvger_docket_collisions.py). They are not in state, and the
# first seed skipped them as another id scheme, so 26 of them would have been
# fetched again as -D duplicates.

def test_corpus_suffix_date():
    from scrapers._date_identity import corpus_suffix_date
    b = "bvger_B-5028_2009"
    assert corpus_suffix_date(f"{b}_d20210423", b) == "2021-04-23"
    assert corpus_suffix_date(b, b) is None
    assert corpus_suffix_date(f"{b}-D20210423", b) is None
    assert corpus_suffix_date(f"{b}_d2021042", b) is None
    assert corpus_suffix_date(f"{b}_d20210423x", b) is None
    assert corpus_suffix_date("bvger_B-5029_2009_d20210423", b) is None
    assert corpus_suffix_date(f"{b}_x_d20210423", b) is None


def test_seed_pairs_takes_suffix_d_rows(tmp_path):
    b = base("bvger")
    rows = [
        {"decision_id": b, "court": "bvger",
         "docket_number": DOCKETS["bvger"], "decision_date": FIRST},
        {"decision_id": f"{b}_d20210423", "court": "bvger",
         "docket_number": DOCKETS["bvger"], "decision_date": SECOND},
        {"decision_id": f"{b}_d20220101", "court": "bvger",       # date from id
         "docket_number": DOCKETS["bvger"], "decision_date": None},
        {"decision_id": "bvger_C-1_2020_d20200101", "court": "bvger",  # other docket
         "docket_number": DOCKETS["bvger"], "decision_date": "2020-01-01"},
    ]
    shard = tmp_path / "bvger.jsonl"
    shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    pairs, undated, foreign = bf.seed_pairs("bvger", [shard])
    assert pairs == [f"{b}\t{FIRST}\t{b}",
                     f"{b}\t{SECOND}\t{b}_d20210423",
                     f"{b}\t2022-01-01\t{b}_d20220101"]
    assert (undated, foreign) == (0, 1)


def test_loader_accepts_suffix_d_pair_without_state(tmp_path):
    b = base("bvger")
    sc = scraper_with("bvger", tmp_path, {b},
                      [pair("bvger", FIRST), pair("bvger", SECOND, f"{b}_d20210423")])
    assert f"{b}|{SECOND}" in sc._known_dates
    assert sc._stub_filter(stub("bvger", SECOND)) is None          # held, no -D
    s = sc._stub_filter(stub("bvger", "2022-03-03"))
    assert s["decision_id"] == make_decision_id("bvger", f"{DOCKETS['bvger']}-D20220303")


def test_loader_still_drops_unknown_dash_d_pair(tmp_path):
    b = base("bvger")
    did2 = make_decision_id("bvger", f"{DOCKETS['bvger']}-D20210423")
    sc = scraper_with("bvger", tmp_path, {b},
                      [pair("bvger", FIRST), pair("bvger", SECOND, did2)])
    assert f"{b}|{SECOND}" not in sc._known_dates                 # crash guard kept


def test_seed_then_fetch_skips_recovered_ruling(tmp_path):
    """The trap end to end: seed from a shard holding a `_d` row, then the
    listing of that ruling must not come back as a -D candidate."""
    b = base("bvger")
    shard = tmp_path / "bvger.jsonl"
    shard.write_text("\n".join(json.dumps(r) for r in [
        {"decision_id": b, "court": "bvger",
         "docket_number": DOCKETS["bvger"], "decision_date": FIRST},
        {"decision_id": f"{b}_d20210423", "court": "bvger",
         "docket_number": DOCKETS["bvger"], "decision_date": SECOND},
    ]) + "\n")
    pairs, _, _ = bf.seed_pairs("bvger", [shard])
    sc = scraper_with("bvger", tmp_path, {b}, pairs)
    assert sc._stub_filter(stub("bvger", SECOND)) is None
    assert sc._stub_filter(stub("bvger", FIRST)) is None
