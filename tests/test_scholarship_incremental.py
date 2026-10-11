"""Incremental, budgeted, resumable scholarship harvesting (2026-10-11).

Publish step 2h killed harvest_all at 3600 s inside unige_law every Sunday:
a full re-walk of the 25 sources takes 4+ hours, so the twelve sources after
UNIGE were never harvested and UNIGE itself never finished. These tests pin
the replacement contract against a fake OAI server (no network):

* an OAI source harvests from its high-water mark and merges the delta;
* a deadline pauses the walk at a window boundary, merges progress, and the
  next run resumes from the saved window stack;
* a full re-harvest replaces the shard (deletions vanish) but never shrinks
  it by more than 10 %;
* gapped windows are retried on the next run instead of forcing a full walk;
* harvest_all gives every source a turn whatever one source does;
* SONAR's HTTP-422 noRecordsMatch is an empty window, not a failure (why
  unifr_law.jsonl never existed).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.scholarship import harvest_all, incremental, oai_pmh  # noqa: E402
from scrapers.scholarship.sources import ScholarshipSource  # noqa: E402

NOW = datetime(2026, 10, 11, 3, 30, tzinfo=timezone.utc)

IDENTIFY = (b'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            b'<Identify><granularity>YYYY-MM-DDThh:mm:ssZ</granularity></Identify></OAI-PMH>')


def _record(rid: str, title: str = "t", subject: str = "law",
            deleted: bool = False) -> str:
    if deleted:
        return (f'<record><header status="deleted"><identifier>{rid}</identifier>'
                f'<datestamp>2026-10-10T00:00:00Z</datestamp></header></record>')
    return f"""<record>
      <header><identifier>{rid}</identifier>
      <datestamp>2026-10-10T00:00:00Z</datestamp></header>
      <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
        xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>{title}</dc:title><dc:subject>{subject}</dc:subject>
      </oai_dc:dc></metadata></record>"""


def _page(records: list[str], token: str | None = None,
          size: int | None = None) -> bytes:
    attrs = f' completeListSize="{size}"' if size is not None else ""
    tok = f'<resumptionToken{attrs}>{token or ""}</resumptionToken>'
    return (f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<ListRecords>{"".join(records)}{tok}</ListRecords></OAI-PMH>').encode()


def _oai_error(code: str) -> bytes:
    return (f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            f'<error code="{code}"/></OAI-PMH>').encode()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeServer:
    def __init__(self, handler, clock: Clock | None = None, cost: float = 0.0):
        self.handler = handler
        self.queries: list[dict] = []
        self.clock = clock
        self.cost = cost

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.queries.append(q)
        if self.clock is not None and q.get("verb") == "ListRecords":
            self.clock.t += self.cost
        if q.get("verb") == "Identify":
            return io.BytesIO(IDENTIFY)
        body = self.handler(q)
        if isinstance(body, BaseException):
            raise body
        return io.BytesIO(body)

    def listrecords(self):
        return [q for q in self.queries if q.get("verb") == "ListRecords"]


@pytest.fixture
def serve(monkeypatch):
    def _install(handler, clock=None, cost=0.0):
        srv = FakeServer(handler, clock, cost)
        monkeypatch.setattr(oai_pmh.urllib.request, "urlopen", srv)
        monkeypatch.setattr(oai_pmh, "_RETRY_BACKOFF_S", (0, 0))
        monkeypatch.setattr(oai_pmh.time, "sleep", lambda s: None)
        if clock is not None:
            monkeypatch.setattr(oai_pmh.time, "monotonic", clock)
        return srv
    return _install


def _src(key="src", windowed=True, **kw) -> ScholarshipSource:
    return ScholarshipSource(key=key, name=key, kind="oai_pmh",
                             base_url="https://fake/oai", windowed=windowed,
                             subject_filter=("law",), **kw)


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open()]


def _write_shard(path: Path, ids: list[str], title: str = "old") -> None:
    path.write_text("".join(
        json.dumps({"source_record_id": i, "title": title}) + "\n" for i in ids))


def _state(tmp_path, key="src") -> dict:
    return incremental.load_state(tmp_path, key)


def _set_state(tmp_path, key="src", **st) -> None:
    incremental.save_state(tmp_path, key, st)


# ── SONAR 422 ───────────────────────────────────────────────────────────

def test_http_422_carrying_norecordsmatch_is_an_empty_window(serve, tmp_path):
    """SONAR's empty windows answer HTTP 422 + <error code="noRecordsMatch">.
    Read as a failure, every empty 1995 window became a gap and bisected
    further; unifr_law ran 4 h for 0 records."""
    def handler(q):
        if q.get("from", "").startswith("2020-01-01"):
            return urllib.error.HTTPError(
                "https://fake/oai", 422, "UNPROCESSABLE ENTITY", {},
                io.BytesIO(_oai_error("noRecordsMatch")))
        return _page([_record("a")], None, 1)

    srv = serve(handler)
    s = oai_pmh.harvest("https://fake/oai", "src", output_dir=tmp_path,
                        windowed=True, from_date="2020-01-01",
                        until_date="2020-01-02", subject_filter=["law"])
    assert s["gaps"] == [] and s["aborted"] is False
    # one request for the empty window — no retries, no bisection
    assert len(srv.listrecords()) == 1


def test_http_4xx_without_oai_body_is_still_a_failure(serve, tmp_path):
    def handler(q):
        return urllib.error.HTTPError("https://fake/oai", 403, "Forbidden",
                                      {}, io.BytesIO(b"<html>no</html>"))
    srv = serve(handler)
    s = oai_pmh.harvest("https://fake/oai", "src", output_dir=tmp_path,
                        from_date="2020-01-01", until_date="2020-01-02")
    assert s["aborted"] is True
    assert len(srv.listrecords()) == oai_pmh._FETCH_RETRIES


# ── incremental merge ───────────────────────────────────────────────────

def test_incremental_harvests_from_high_water_and_merges(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["keep", "upd", "gone", "nolaw"])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2027-01-01")

    def handler(q):
        return _page([_record("upd", title="new title"), _record("fresh"),
                      _record("gone", deleted=True),
                      _record("nolaw", subject="cooking")], None, 4)

    srv = serve(handler)
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["op"] == "incremental" and s["outcome"] == "clean"
    q = srv.listrecords()[0]
    assert q["from"].startswith("2026-10-03")           # high water − 1 day
    assert q["until"].startswith("2026-10-12")          # tomorrow
    rows = _rows(shard)
    assert [r["source_record_id"] for r in rows] == ["keep", "upd", "fresh"]
    assert rows[1]["title"] == "new title"              # updated in place
    st = _state(tmp_path)
    assert st["high_water"] == "2026-10-11T03:30:00Z"   # this run's start
    assert st["pending"] is None
    assert not (tmp_path / "src.jsonl.partial").exists()


def test_incremental_with_no_changes_keeps_the_shard(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["a", "b"])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2027-01-01")
    serve(lambda q: _oai_error("noRecordsMatch"))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["outcome"] == "clean"
    assert [r["source_record_id"] for r in _rows(shard)] == ["a", "b"]


# ── seeding from a pre-state shard ──────────────────────────────────────

def test_existing_shard_is_adopted_not_reharvested(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["a"])
    mtime = (NOW - timedelta(days=20)).timestamp()
    os.utime(shard, (mtime, mtime))

    srv = serve(lambda q: _page([_record("b")], None, 1))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["op"] == "incremental"
    # shard mtime − 1 day high water, − 1 day overlap
    assert srv.listrecords()[0]["from"].startswith("2026-09-19")
    st = _state(tmp_path)
    assert "2026-10-11" <= st["next_full_after"] < "2027-01-10"   # staggered
    assert {r["source_record_id"] for r in _rows(shard)} == {"a", "b"}


def test_shard_with_newer_aborted_sibling_gets_a_full_now(serve, tmp_path):
    """A .jsonl.aborted newer than the shard: the last full attempt failed
    (eth_research_collection, alexandria_law, zora_law on 2026-10-11)."""
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["a"])
    old = (NOW - timedelta(days=100)).timestamp()
    os.utime(shard, (old, old))
    (tmp_path / "src.jsonl.aborted").write_text("")
    serve(lambda q: _page([_record("a"), _record("b")], None, 2))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["op"] == "full"


def test_no_shard_and_no_state_means_full(serve, tmp_path):
    srv = serve(lambda q: _page([_record("a")], None, 1))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["op"] == "full" and s["outcome"] == "clean"
    assert srv.listrecords()[0]["from"].startswith(oai_pmh.WINDOW_FLOOR)
    st = _state(tmp_path)
    assert st["last_full"] and st["next_full_after"] == "2027-01-09"


# ── full re-harvest ─────────────────────────────────────────────────────

def test_clean_full_replaces_so_upstream_deletions_vanish(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, [f"r{i}" for i in range(20)])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2026-10-01")             # due
    # 19 of 20 come back: one deletion the server never reported
    serve(lambda q: _page([_record(f"r{i}") for i in range(1, 20)], None, 19))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["op"] == "full" and s["install"]["mode"] == "replace"
    assert "r0" not in {r["source_record_id"] for r in _rows(shard)}


def test_full_that_would_shrink_the_shard_merges_instead(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, [f"r{i}" for i in range(10)])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2026-10-01")
    serve(lambda q: _page([_record("r0"), _record("new")], None, 2))
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW)
    assert s["install"]["mode"] == "merge"
    assert len(_rows(shard)) == 11                       # nothing lost


# ── deadline, pause, resume ─────────────────────────────────────────────

def _three_windows(q):
    """[2020-01-01..2020-01-08] is oversize; its quarters serve 2 each."""
    f, u = q.get("from", ""), q.get("until", "")
    if f.startswith("2020-01-01") and u.startswith("2020-01-08"):
        return _page([_record("probe")], "tok", 99)
    rid = f[:10]
    return _page([_record(f"{rid}-a"), _record(f"{rid}-b")], None, 2)


def test_deadline_pauses_at_a_window_and_the_next_run_resumes(serve, tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(oai_pmh, "WINDOW_FLOOR", "2020-01-01")
    clock = Clock()
    srv = serve(_three_windows, clock=clock, cost=10.0)
    src = _src()
    # Full plan from the (patched) floor to tomorrow would be huge; pin the
    # plan to an 8-day window so the oversize probe bisects it.
    monkeypatch.setattr(oai_pmh, "_window_plan",
                        lambda w, f, u: [("2020-01-01", "2020-01-08", 0, None)])

    s1 = incremental.harvest_source(src, output_dir=tmp_path, now=NOW,
                                    deadline=clock.t + 15,
                                    max_window_records=3)
    assert s1["pending"] is True and s1["windows_left"] >= 1
    st = _state(tmp_path)
    assert st["pending"]["op"] == "full"
    assert st["pending"]["started_at"] == "2026-10-11T03:30:00Z"
    got_first = {r["source_record_id"] for r in _rows(tmp_path / "src.jsonl")}
    # progress merged now — and the full walks newest-first
    assert got_first == {"2020-01-05-a", "2020-01-05-b"}

    later = NOW + timedelta(days=7)
    s2 = incremental.harvest_source(src, output_dir=tmp_path, now=later,
                                    deadline=clock.t + 1000,
                                    max_window_records=3)
    assert s2["pending"] is False and s2["resumed"] is True
    assert s2["outcome"] == "clean"
    ids = [r["source_record_id"] for r in _rows(tmp_path / "src.jsonl")]
    assert len(ids) == len(set(ids))                      # no duplicates
    assert got_first < set(ids) and len(ids) == 4       # both halves
    st = _state(tmp_path)
    # high water is when the OPERATION started, not when it finished
    assert st["high_water"] == "2026-10-11T03:30:00Z"
    # the resumed run must not have re-probed the oversize parent
    parent = [q for q in srv.listrecords()
              if q.get("until", "").startswith("2020-01-08")
              and q.get("from", "").startswith("2020-01-01")]
    assert len(parent) == 1


def test_expired_budget_touches_nothing(serve, tmp_path):
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["a"])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2027-01-01")
    clock = Clock()
    srv = serve(lambda q: _page([_record("b")], None, 1), clock=clock)
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW,
                                   deadline=clock.t - 1)
    assert s["pending"] is True and srv.queries == []
    assert [r["source_record_id"] for r in _rows(shard)] == ["a"]


def test_killed_run_resumes_from_its_last_checkpoint(serve, tmp_path,
                                                     monkeypatch):
    """A SIGKILL skips every finally-block; the checkpoint written after each
    window (and a half-written partial line) must still resume cleanly."""
    monkeypatch.setattr(incremental, "_CHECKPOINT_EVERY_S", 0.0)
    monkeypatch.setattr(oai_pmh, "_window_plan",
                        lambda w, f, u: [("2020-01-01", "2020-01-08", 0, None)])
    calls = {"n": 0}

    def dying(q):
        calls["n"] += 1
        if calls["n"] == 3:                  # probe, first half, then …
            return KeyboardInterrupt()       # … stands in for the kill
        return _three_windows(q)

    serve(dying)
    with pytest.raises(KeyboardInterrupt):
        incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW,
                                   max_window_records=3)
    partial = tmp_path / "src.jsonl.partial"
    with partial.open("a") as fh:
        fh.write('{"source_record_id": "half')    # torn last line
    st = _state(tmp_path)
    assert st["pending"] and st["pending"]["stack"]

    serve(_three_windows)
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW,
                                   max_window_records=3)
    assert s["resumed"] is True and s["outcome"] == "clean"
    ids = [r["source_record_id"] for r in _rows(tmp_path / "src.jsonl")]
    assert len(ids) == len(set(ids)) and "half" not in "".join(ids)


# ── gap retry ───────────────────────────────────────────────────────────

def test_gapped_window_is_retried_next_run_then_dropped(serve, tmp_path,
                                                        monkeypatch):
    monkeypatch.setattr(oai_pmh, "_FORCE_MAX_PAGES", 2)
    shard = tmp_path / "src.jsonl"
    _write_shard(shard, ["a"])
    _set_state(tmp_path, high_water="2026-10-04T03:30:00Z",
               next_full_after="2027-01-01")

    def handler(q):
        if q.get("from", "").startswith("2026-10-03"):
            return _page([_record("x")], "loop", 99999)   # never ends
        return _page([_record("b")], None, 1)

    serve(handler)
    s = incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW,
                                   max_window_records=5)
    assert s["outcome"] == "gapped"
    st = _state(tmp_path)
    assert st["retry_windows"] and st["retry_windows"][0]["attempts"] == 1
    assert st["high_water"] == "2026-10-11T03:30:00Z"   # rest of the delta counted

    # Next week the retried window heals.
    healed = []

    def handler2(q):
        healed.append(q.get("from", ""))
        return _page([_record("x")], None, 1)

    serve(handler2)
    s = incremental.harvest_source(_src(), output_dir=tmp_path,
                                   now=NOW + timedelta(days=7),
                                   max_window_records=5)
    assert s["outcome"] == "clean"
    assert _state(tmp_path)["retry_windows"] == []
    assert any(f.startswith("2026-10-03") for f in healed)   # the retry ran
    assert "x" in {r["source_record_id"] for r in _rows(shard)}


def test_retry_window_is_dropped_after_max_attempts():
    gap = {"window": ["2026-04-03T21:00:00Z", "2026-04-03"],
           "reason": "residual_truncation"}
    tried = [{"window": gap["window"],
              "attempts": incremental.MAX_RETRY_ATTEMPTS}]
    assert incremental._retry_list(tried, [gap], "zora_law") == []
    assert incremental._retry_list([], [gap], "zora_law")[0]["attempts"] == 1


# ── harvest_all scheduling ──────────────────────────────────────────────

def test_one_slow_source_cannot_starve_the_rest(tmp_path, monkeypatch):
    """The 2026-10-11 failure shape: one source wants the whole hour. Every
    other source must still get its turn, the hog resumes in pass 2."""
    clock = Clock()
    monkeypatch.setattr(harvest_all.time, "monotonic", clock)
    srcs = [_src(k) for k in ("e_periodica", "unige_law", "zora_law",
                              "boris_law", "unifr_law")]
    calls: list[tuple[str, float]] = []

    def fake(src, *, deadline, output_dir):
        calls.append((src.key, deadline - clock.t))
        if src.key == "unige_law":                  # takes all it is given
            clock.t = deadline
            return {"source": src.key, "op": "full", "pending": True,
                    "windows_left": 40}
        clock.t += 5
        return {"source": src.key, "op": "incremental", "outcome": "clean",
                "pending": False}

    monkeypatch.setattr(harvest_all.incremental, "harvest_source", fake)
    out = harvest_all.schedule(srcs, budget_s=3300, output_dir=tmp_path)
    ran = [k for k, _ in calls]
    assert set(ran) == {s.key for s in srcs}            # everyone got a turn
    assert ran.count("unige_law") == 2                  # pass 2 continuation
    first_unige = next(d for k, d in calls if k == "unige_law")
    assert first_unige < 3300 / 3                       # a fair share, not all
    assert clock.t - 1000 <= 3300                       # within budget
    assert all("error" not in s for s in out)


def test_least_recently_refreshed_source_goes_first(tmp_path, monkeypatch):
    for key, ts in (("a", "2026-10-04T00:00:00Z"), ("b", None),
                    ("c", "2026-09-01T00:00:00Z")):
        if ts:
            incremental.save_state(tmp_path, key, {"last_success": ts})
    order = []
    monkeypatch.setattr(harvest_all.incremental, "harvest_source",
                        lambda src, **kw: order.append(src.key) or
                        {"source": src.key, "pending": False})
    harvest_all.schedule([_src("a"), _src("b"), _src("c")], budget_s=0,
                         output_dir=tmp_path)
    assert order == ["b", "c", "a"]


def test_hung_custom_adapter_is_killed_and_the_run_continues(tmp_path,
                                                             monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(harvest_all.subprocess, "run", fake_run)
    custom = ScholarshipSource(key="leges", name="x", kind="custom",
                               custom_module="scrapers.scholarship.leges")
    monkeypatch.setattr(harvest_all.incremental, "harvest_source",
                        lambda src, **kw: {"source": src.key, "pending": False})
    out = harvest_all.schedule([custom, _src("after")], budget_s=3300,
                               output_dir=tmp_path)
    by = {s["source"]: s for s in out}
    assert "timeout" in by["leges"]["error"]
    assert "error" not in by["after"]
    assert not incremental.load_state(tmp_path, "leges").get("last_success")


def test_custom_adapter_success_records_last_success(tmp_path, monkeypatch):
    payload = json.dumps({"summaries": [{"source": "leges", "records": 3}]},
                         indent=2)
    monkeypatch.setattr(
        harvest_all.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "noise\n" + payload))
    custom = ScholarshipSource(key="leges", name="x", kind="custom",
                               custom_module="scrapers.scholarship.leges")
    out = harvest_all.schedule([custom], budget_s=3300, output_dir=tmp_path)
    assert out[0]["records"] == 3
    assert incremental.load_state(tmp_path, "leges")["last_success"]


def test_state_files_are_invisible_to_the_builder_glob(serve, tmp_path):
    """build_legal_scholarship ingests output/legal_scholarship/*.jsonl."""
    clock = Clock()
    serve(_three_windows, clock=clock, cost=10.0)
    incremental.harvest_source(_src(), output_dir=tmp_path, now=NOW,
                               deadline=clock.t + 15, max_window_records=3)
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["src.jsonl"]
