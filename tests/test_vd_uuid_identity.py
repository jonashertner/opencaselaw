"""uuid-keyed identity for the Vaud scraper (2026-09-04: prestations.vd.ch
stopped returning affaireHit.numero, so docket-keyed ids re-fetched 8,133 held
decisions under new ids in one night). Offline. Mirrors
tests/test_bger_docket_identity.py."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import Decision, make_decision_id  # noqa: E402
from scrapers.cantonal import vd_gerichte as vd  # noqa: E402
from scrapers.cantonal.vd_gerichte import VDGerichteScraper, uuid_from_pdf_url  # noqa: E402

U1 = "d390c864-54e4-4504-a3d8-b110244779f5"
U2 = "3c4bc54f-14cd-4b4d-a35c-5df196a64a66"
U3 = "79335a63-b857-45b2-a6a0-e514add0b71a"
PDF = f"{vd.API_URL}/decision/download/"


class FakeState:
    def __init__(self, known):
        self._seen = set(known)

    def is_known(self, decision_id):
        return decision_id in self._seen

    def mark_scraped(self, decision_id):
        self._seen.add(decision_id)

    def count(self):
        return len(self._seen)


def scraper_with(tmp_path, known, sidecar_lines):
    sc = VDGerichteScraper.__new__(VDGerichteScraper)   # no network init
    sc.state = FakeState(known)
    sc.state_dir = tmp_path
    if sidecar_lines is not None:
        (tmp_path / "vd_gerichte.uuids.txt").write_text(
            "\n".join(sidecar_lines) + ("\n" if sidecar_lines else ""))
    sc._load_known_uuids()
    return sc


def hit(uuid, affaire_no=None, hit_no=None, d="2017-09-04"):
    return {"decisionHit": {
        "id": uuid, "numero": hit_no, "dateDecision": d, "datePublication": d,
        "affaireHit": {"numero": affaire_no} if affaire_no is not None else None,
        "natureAffaire": "x", "resume": "", "articlesDeLoi": {}, "resultats": [],
        "conceptsJurivoc": [],
    }}


def decision(did, uuid):
    return Decision(
        decision_id=did, court="vd_gerichte", canton="VD", docket_number=did,
        decision_date=date(2017, 9, 4), language="fr", full_text="x" * 300,
        source_url=f"{vd.BASE_URL}/", pdf_url=f"{PDF}{uuid}",
    )


# ── helpers ───────────────────────────────────────────────────────────────

def test_uuid_from_pdf_url():
    assert uuid_from_pdf_url(f"{PDF}{U1}") == U1
    assert uuid_from_pdf_url(f"{PDF}{U1.upper()}/") == U1
    assert uuid_from_pdf_url(f"{vd.BASE_URL}/") is None
    assert uuid_from_pdf_url(None) is None


# ── id minting ────────────────────────────────────────────────────────────

def test_affaire_number_keeps_the_historical_scheme(tmp_path):
    sc = scraper_with(tmp_path, set(), None)
    stub = sc._parse_search_item(hit(U1, affaire_no="ZD17.028583", hit_no="249"))
    assert stub["decision_id"] == make_decision_id("vd_gerichte", "ZD17.028583")
    assert stub["docket_number"] == "ZD17.028583"
    assert stub["uuid"] == U1


def test_real_docket_without_affaire_number_is_its_own_key(tmp_path):
    sc = scraper_with(tmp_path, set(), None)
    stub = sc._parse_search_item(hit(U1, affaire_no=None, hit_no="AI 210/17 - 249/2017"))
    assert stub["decision_id"] == make_decision_id("vd_gerichte", "AI 210/17 - 249/2017")
    assert stub["docket_number"] == "AI 210/17 - 249/2017"


def test_bare_sequence_number_is_keyed_on_the_uuid(tmp_path):
    """'641' repeats every year; two rulings must never share an id."""
    sc = scraper_with(tmp_path, set(), None)
    a = sc._parse_search_item(hit(U2, affaire_no=None, hit_no="641", d="2024-03-05"))
    b = sc._parse_search_item(hit(U3, affaire_no=None, hit_no="641", d="2026-08-19"))
    assert a["decision_id"] != b["decision_id"]
    assert a["decision_id"] == make_decision_id("vd_gerichte", U2)
    assert a["docket_number"] == "641"          # display value stays real
    assert b["decision_id"] == make_decision_id("vd_gerichte", U3)


def test_no_number_at_all_is_keyed_on_the_uuid(tmp_path):
    sc = scraper_with(tmp_path, set(), None)
    stub = sc._parse_search_item(hit(U2, affaire_no=None, hit_no=None))
    assert stub["decision_id"] == make_decision_id("vd_gerichte", U2)
    assert stub["docket_number"] == U2


# ── discovery: sidecar ────────────────────────────────────────────────────

def test_without_sidecar_only_the_id_check_applies(tmp_path):
    held = make_decision_id("vd_gerichte", "ZD17.028583")
    sc = scraper_with(tmp_path, {held}, None)
    assert sc._known_uuids == {}
    assert not sc._is_new({"decision_id": held, "uuid": U1})
    # the 2026-09-04 failure mode: same decision, new id -> looks new
    assert sc._is_new({"decision_id": "vd_gerichte_AI 210_17 - 249_2017", "uuid": U1})


def test_sidecar_blocks_a_held_uuid_under_any_id(tmp_path):
    held = make_decision_id("vd_gerichte", "ZD17.028583")
    sc = scraper_with(tmp_path, {held}, [f"{U1}\t{held}"])
    assert sc._known_uuids == {U1: held}
    assert not sc._is_new({"decision_id": "vd_gerichte_AI 210_17 - 249_2017", "uuid": U1})
    assert not sc._is_new({"decision_id": "vd_gerichte_641", "uuid": U1.upper()})
    assert sc._is_new({"decision_id": "vd_gerichte_x", "uuid": U2})


def test_sidecar_tolerates_comments_blank_and_garbage_lines(tmp_path):
    sc = scraper_with(tmp_path, set(), ["# seeded 2026-09-04", "", "not-a-uuid\tx", f"{U2}\tvd_gerichte_y"])
    assert sc._known_uuids == {U2: "vd_gerichte_y"}


def test_discover_skips_held_uuids_and_yields_the_rest(tmp_path, monkeypatch):
    held = make_decision_id("vd_gerichte", "ZD17.028583")
    sc = scraper_with(tmp_path, {held}, [f"{U1}\t{held}"])
    sc._init_session = lambda: True
    page = {"response": {"totalElements": 3, "totalPages": 1, "content": [
        hit(U1, affaire_no=None, hit_no="AI 210/17 - 249/2017"),   # held by uuid
        hit(U2, affaire_no=None, hit_no="641"),                    # new
        hit(U3, affaire_no="ZD26.000001", hit_no="7"),             # new, old scheme
    ]}}
    calls = []
    sc._search = lambda df, dt, page=0, by="decision": (calls.append((df, dt, page, by)) or page_for(df))

    def page_for(df):
        return page if df[:2] == [date.today().year, date.today().month] else None
    today = date.today()
    stubs = list(sc.discover_new(since_date=date(today.year, today.month, 1)))
    assert [s["uuid"] for s in stubs] == [U2, U3]
    assert stubs[0]["decision_id"] == make_decision_id("vd_gerichte", U2)


# ── durable write hook ────────────────────────────────────────────────────

def test_mark_run_complete_appends_uuids_once(tmp_path):
    sc = scraper_with(tmp_path, set(), [])
    d1 = decision("vd_gerichte_ZD26.000001", U2)
    d2 = decision("vd_gerichte_ZD26.000002", U3)
    sc.mark_run_complete([d1, d2])
    sc.mark_run_complete([d1])                    # idempotent
    lines = (tmp_path / "vd_gerichte.uuids.txt").read_text().splitlines()
    assert lines == [f"{U2}\tvd_gerichte_ZD26.000001", f"{U3}\tvd_gerichte_ZD26.000002"]
    assert sc.state.is_known("vd_gerichte_ZD26.000001")
    assert not sc._is_new({"decision_id": "vd_gerichte_641", "uuid": U2})


def test_mark_run_complete_without_sidecar_creates_it(tmp_path):
    sc = scraper_with(tmp_path, set(), None)
    sc.mark_run_complete([decision("vd_gerichte_ZD26.000001", U2)])
    assert (tmp_path / "vd_gerichte.uuids.txt").read_text() == f"{U2}\tvd_gerichte_ZD26.000001\n"


# ── seed + dedupe script ──────────────────────────────────────────────────

def _shard(tmp_path):
    recs = [
        {"decision_id": "vd_gerichte_ZD17.028583", "pdf_url": f"{PDF}{U1}", "full_text": "a"},
        {"decision_id": "vd_gerichte_585", "pdf_url": f"{PDF}{U2}", "full_text": "b"},
        {"decision_id": "vd_gerichte_AI 210_17 - 249_2017", "pdf_url": f"{PDF}{U1}", "full_text": "a"},
        {"decision_id": "vd_gerichte_nopdf", "pdf_url": None, "full_text": "c"},
    ]
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "vd_gerichte.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return p


def test_script_check_seed_and_dedupe(tmp_path, capsys):
    from scripts import vd_uuid_sidecar as s
    shard = _shard(tmp_path)
    sidecar = tmp_path / "vd_gerichte.uuids.txt"
    first, dups, stats = s.analyse(shard)
    assert first == {U1: "vd_gerichte_ZD17.028583", U2: "vd_gerichte_585"}
    assert dups == [("vd_gerichte_AI 210_17 - 249_2017", "vd_gerichte_ZD17.028583", U1)]
    assert stats["no_uuid"] == 1

    assert s.main(["--shard", str(shard), "--sidecar", str(sidecar), "--dedupe"]) == 0   # dry-run
    assert shard.read_text().count("\n") == 4                                            # untouched

    assert s.main(["--shard", str(shard), "--sidecar", str(sidecar), "--dedupe", "--apply", "--seed"]) == 0
    kept = [json.loads(l)["decision_id"] for l in shard.read_text().splitlines()]
    assert kept == ["vd_gerichte_ZD17.028583", "vd_gerichte_585", "vd_gerichte_nopdf"]
    backups = list(tmp_path.glob("vd_gerichte.jsonl.bak-*"))
    assert len(backups) == 1 and backups[0].read_text().count("\n") == 4
    dropped = list(tmp_path.glob("vd_gerichte.jsonl.dropped-*.txt"))
    assert dropped and dropped[0].read_text() == "vd_gerichte_AI 210_17 - 249_2017\n"
    assert sidecar.read_text() == f"{U1}\tvd_gerichte_ZD17.028583\n{U2}\tvd_gerichte_585\n"
    # the sidecar now protects the kept ids from a re-fetch under any scheme
    sc = scraper_with(tmp_path, {"vd_gerichte_ZD17.028583"}, None)
    assert not sc._is_new({"decision_id": "vd_gerichte_AI 210_17 - 249_2017", "uuid": U1})


# ── one-time self-seed from the corpus shard ──────────────────────────────

def test_missing_sidecar_is_seeded_from_the_shard(tmp_path, monkeypatch):
    shard = _shard(tmp_path / "out")
    monkeypatch.setenv(vd.SHARD_ENV, str(shard))
    sc = scraper_with(tmp_path, set(), None)          # no sidecar yet
    assert sc._known_uuids == {U1: "vd_gerichte_ZD17.028583", U2: "vd_gerichte_585"}
    text = (tmp_path / "vd_gerichte.uuids.txt").read_text()
    assert text.startswith("# seeded from ")
    assert f"{U1}\tvd_gerichte_ZD17.028583\n" in text
    # the 2026-09-04 duplicate is now blocked without any manual step
    assert not sc._is_new({"decision_id": "vd_gerichte_AI 210_17 - 249_2017", "uuid": U1})


def test_missing_sidecar_and_missing_shard_is_the_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv(vd.SHARD_ENV, str(tmp_path / "nowhere.jsonl"))
    sc = scraper_with(tmp_path, set(), None)
    assert sc._known_uuids == {}
    assert not (tmp_path / "vd_gerichte.uuids.txt").exists()


def test_existing_sidecar_is_not_reseeded(tmp_path, monkeypatch):
    shard = _shard(tmp_path / "out")
    monkeypatch.setenv(vd.SHARD_ENV, str(shard))
    sc = scraper_with(tmp_path, set(), [f"{U3}\tvd_gerichte_only"])
    assert sc._known_uuids == {U3: "vd_gerichte_only"}


# ── the sidecar must grow in production, not only under mark_run_complete ──
# run_scraper.py marks state per decision (scraper.state.mark_scraped) and
# never calls BaseScraper.mark_run_complete; on 2026-09-05 the sidecar was
# seeded once and then stayed flat through a 5,589-decision run.

def test_on_decision_persisted_appends_the_uuid(tmp_path):
    sc = scraper_with(tmp_path, set(), [])
    sc.on_decision_persisted(decision("vd_gerichte_ZD26.000001", U2))
    sc.on_decision_persisted(decision("vd_gerichte_ZD26.000001", U2))     # idempotent
    assert (tmp_path / "vd_gerichte.uuids.txt").read_text() == f"{U2}\tvd_gerichte_ZD26.000001\n"
    assert not sc._is_new({"decision_id": "vd_gerichte_641", "uuid": U2})


def test_run_scraper_calls_the_persistence_hook_after_marking_state():
    src = (REPO / "run_scraper.py").read_text(encoding="utf-8")
    i = src.index("scraper.state.mark_scraped(decision.decision_id)")
    tail = src[i:i + 900]
    assert 'getattr(scraper, "on_decision_persisted", None)' in tail
    assert "hook(decision)" in tail
