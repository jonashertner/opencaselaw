"""A canton's shard is replaced only by a run at least as good as it.

scrape_canton() writes {canton}.jsonl.part and promotes it to {canton}.jsonl
with an atomic rename only when the run finished with no errors and did not
shrink the shard. On 2026-09-02 four transient 500s from zh.ch overwrote a
150-law ZH shard with a 146-law one, exited 1, and the monthly unit's `&&`
chain skipped the DB rebuild for the other 25 cantons. These tests pin the
promotion rule, the parked names the build must never glob, and the
--check-report exit codes the unit's last step relies on.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scrape_cantonal_laws as scl  # noqa: E402
from scrapers.cantonal_laws import CANTONAL_LAW_SCRAPERS  # noqa: E402


class StubScraper:
    """Yields `laws` stubs; raises for sr_numbers listed in `broken`."""
    laws: list[str] = []
    broken: set[str] = set()
    REQUEST_DELAY = 0.0

    def __init__(self, canton="XX"):
        self.canton = canton
        self.portal_count = len(self.laws)

    def enumerate_laws(self):
        for sr in self.laws:
            yield {"sr_number": sr, "title": f"Gesetz {sr}"}

    def fetch_law(self, stub):
        if stub["sr_number"] in self.broken:
            raise RuntimeError("500 Server Error")
        return {"canton": self.canton, "sr_number": stub["sr_number"],
                "title": stub["title"], "full_text": "Art. 1 Text", "articles": []}


@pytest.fixture
def stub(monkeypatch):
    mod = types.ModuleType("_stub_cantonal_scraper")
    mod.StubScraper = StubScraper
    monkeypatch.setitem(sys.modules, "_stub_cantonal_scraper", mod)
    monkeypatch.setitem(CANTONAL_LAW_SCRAPERS, "XX", ("_stub_cantonal_scraper", "StubScraper"))
    monkeypatch.setattr(StubScraper, "laws", [str(i) for i in range(10)])
    monkeypatch.setattr(StubScraper, "broken", set())
    return StubScraper


def _shard(path: Path, n: int, marker="old"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"canton": "XX", "sr_number": str(i),
                                        "title": marker, "full_text": marker}) + "\n"
                            for i in range(n)), encoding="utf-8")


def _titles(path: Path):
    return {json.loads(l)["title"] for l in path.read_text(encoding="utf-8").splitlines()}


def test_clean_run_replaces_the_shard(tmp_path, stub):
    _shard(tmp_path / "XX.jsonl", 10)
    r = scl.scrape_canton("XX", tmp_path)
    assert r["ok"] and r["promoted"] and not r["kept_previous"]
    assert _titles(tmp_path / "XX.jsonl") == {f"Gesetz {i}" for i in range(10)}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["XX.jsonl"]


def test_errors_keep_the_previous_shard(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(stub, "broken", {"3", "7"})
    _shard(tmp_path / "XX.jsonl", 10)
    r = scl.scrape_canton("XX", tmp_path)
    assert not r["ok"] and r["kept_previous"] and r["errors"] == 2 and r["previous"] == 10
    assert _titles(tmp_path / "XX.jsonl") == {"old"}, "the good shard must survive a failed run"
    parked = tmp_path / "XX.jsonl.failed"
    assert parked.exists() and len(parked.read_text().splitlines()) == 8
    assert not (tmp_path / "XX.jsonl.part").exists()


def test_a_clean_but_shrunken_run_is_not_promoted(tmp_path, stub, monkeypatch):
    """Zero errors is not enough: a portal that returns a short index looks
    like success and the build's override is wholesale (2026-08-19)."""
    _shard(tmp_path / "XX.jsonl", 100)
    monkeypatch.setattr(stub, "laws", [str(i) for i in range(50)])
    r = scl.scrape_canton("XX", tmp_path)
    assert not r["ok"] and r["kept_previous"] and r["errors"] == 0
    assert len((tmp_path / "XX.jsonl").read_text().splitlines()) == 100


def test_small_shrinkage_within_the_floor_is_promoted(tmp_path, stub, monkeypatch):
    _shard(tmp_path / "XX.jsonl", 10)
    monkeypatch.setattr(stub, "laws", [str(i) for i in range(9)])   # 90 % of 10
    r = scl.scrape_canton("XX", tmp_path)
    assert r["ok"] and r["promoted"]
    assert len((tmp_path / "XX.jsonl").read_text().splitlines()) == 9


def test_failure_with_no_previous_shard_leaves_no_shard(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(stub, "broken", {"0"})
    r = scl.scrape_canton("XX", tmp_path)
    assert not r["ok"] and not r["kept_previous"] and r["previous"] is None
    assert not (tmp_path / "XX.jsonl").exists()
    assert (tmp_path / "XX.jsonl.failed").exists()


def test_pilot_runs_never_touch_the_shard(tmp_path, stub):
    _shard(tmp_path / "XX.jsonl", 10)
    r = scl.scrape_canton("XX", tmp_path, max_laws=2)
    assert r["ok"] and not r["promoted"] and r["fetched"] == 2
    assert _titles(tmp_path / "XX.jsonl") == {"old"}
    assert len((tmp_path / "XX.jsonl.pilot").read_text().splitlines()) == 2


def test_parked_files_are_invisible_to_the_build(tmp_path):
    """build() selects shards with glob('*.jsonl'); nothing parked may match."""
    for name in ("XX.jsonl.part", "XX.jsonl.failed", "XX.jsonl.pilot", scl.REPORT_NAME):
        (tmp_path / name).write_text("{}")
    assert list(tmp_path.glob("*.jsonl")) == []


def _report(tmp_path, cantons, age_h=0.0):
    finished = datetime.now(timezone.utc) - timedelta(hours=age_h)
    (tmp_path / scl.REPORT_NAME).write_text(json.dumps(
        {"finished_at": finished.isoformat(), "output_dir": str(tmp_path), "cantons": cantons}))


def test_check_report_exit_codes(tmp_path, capsys):
    assert scl.check_report(tmp_path) == 2                       # no report at all
    _report(tmp_path, {"AG": {"ok": True, "fetched": 5}})
    assert scl.check_report(tmp_path) == 0
    _report(tmp_path, {"AG": {"ok": True, "fetched": 5},
                       "ZH": {"ok": False, "fetched": 146, "errors": 4,
                              "kept_previous": True, "previous": 150}})
    assert scl.check_report(tmp_path) == 1
    assert "ZH" in capsys.readouterr().out
    _report(tmp_path, {"AG": {"ok": True}}, age_h=scl.REPORT_MAX_AGE_H + 1)
    assert scl.check_report(tmp_path) == 2                       # stale = the run never finished


def test_write_report_then_check(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(stub, "broken", {"1"})
    _shard(tmp_path / "XX.jsonl", 10)
    results = [scl.scrape_canton("XX", tmp_path)]
    scl.write_report(tmp_path, results)
    assert scl.check_report(tmp_path) == 1


def test_mount_retries_survives_a_transient_500():
    """Two 500s then a 200 on the same URL must come back as the 200."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import requests

    from scrapers.cantonal_laws import mount_retries

    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            code = 500 if len(hits) <= 2 else 200
            self.send_response(code); self.send_header("Content-Length", "2"); self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a):  # silence
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        s = requests.Session(); mount_retries(s, backoff_factor=0)
        r = s.get(f"http://127.0.0.1:{srv.server_port}/erlass", timeout=5)
        assert r.status_code == 200 and len(hits) == 3
        hits.clear()
        s404 = requests.Session(); mount_retries(s404, backoff_factor=0)
        H.do_GET = lambda self: (hits.append(self.path), self.send_response(404),
                                 self.send_header("Content-Length", "0"), self.end_headers())
        assert s404.get(f"http://127.0.0.1:{srv.server_port}/x", timeout=5).status_code == 404
        assert len(hits) == 1, "4xx must not be retried"
    finally:
        srv.shutdown()
