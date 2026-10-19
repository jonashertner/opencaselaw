"""stats.json is written atomically (2026-09-05: two concurrent writers tore it)."""
from __future__ import annotations

import json
import threading

import generate_stats


def test_write_is_complete_and_leaves_no_temp_file(tmp_path):
    out = tmp_path / "stats.json"
    out.write_text('{"old": true}')
    generate_stats._write_json_atomic(out, {"total": 1, "name": "Zürich"})
    assert json.loads(out.read_text(encoding="utf-8")) == {"total": 1, "name": "Zürich"}
    assert [p.name for p in tmp_path.iterdir()] == ["stats.json"]


def test_concurrent_writers_never_tear_the_file(tmp_path, monkeypatch):
    out = tmp_path / "stats.json"
    payloads = [{"writer": i, "pad": "x" * 200_000} for i in range(2)]
    # Same process, so give each writer its own temp name as two pids would.
    ids = iter(range(100, 200))
    lock = threading.Lock()

    def fake_pid():
        with lock:
            return next(ids)

    monkeypatch.setattr(generate_stats.os, "getpid", fake_pid)
    threads = [
        threading.Thread(target=lambda p=p: [generate_stats._write_json_atomic(out, p) for _ in range(20)])
        for p in payloads
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(out.read_text())["writer"] in (0, 1)
    assert [p.name for p in tmp_path.iterdir()] == ["stats.json"]
