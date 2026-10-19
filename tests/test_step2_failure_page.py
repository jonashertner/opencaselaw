"""Step 2 failure pages at once, with what the operator needs (2026-10-09/10).

The 2026-10-09 swap refusal (12:21) paged only when the run ended (20:58); the
kept build was then deleted by the 03:30 run. Offline: temp files only, and
conftest blocks ntfy.sh.
"""
import sqlite3
import time

import publish


def _live_db(out, built_h_ago):
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out / "decisions.db"))
    conn.execute("CREATE TABLE decisions (decision_id TEXT)")
    conn.execute(f"PRAGMA user_version = {int(time.time() - built_h_ago * 3600)}")
    conn.commit()
    conn.close()


def test_message_names_the_reason_the_data_age_and_the_kept_build(tmp_path):
    vol = tmp_path / "vol"
    _live_db(vol / "output", built_h_ago=46)
    (vol / "output" / "decisions.db.tmp").write_bytes(b"\0" * 2048)
    log = tmp_path / "publish.log"
    log.write_text(
        "2026-10-09 12:21:07 publish INFO   | 2026-10-09 build_fts5 INFO db_generation set\n"
        "2026-10-09 12:21:07 publish INFO   | RuntimeError: pre-swap per-court gate: refusing to swap "
        "— court 'sg_gerichte' collapsed 3,141 → 1,989 rows\n"
        "2026-10-09 12:21:07 publish ERROR   exit code 1\n", encoding="utf-8")
    msg = publish._step2_failure_message(log_path=log, data_volume=str(vol))
    assert "refusing to swap" in msg and "sg_gerichte" in msg
    assert "previous database, 46 h old" in msg
    assert "decisions.db.tmp" in msg and "swap it in by hand" in msg


def test_without_a_kept_build_it_says_the_next_run_starts_over(tmp_path):
    vol = tmp_path / "vol"
    _live_db(vol / "output", built_h_ago=30)
    log = tmp_path / "publish.log"
    log.write_text("2026-10-10 03:30:14 publish ERROR PRE-FLIGHT FAILED: /mnt has 50.5 GB free\n",
                   encoding="utf-8")
    msg = publish._step2_failure_message(log_path=log, data_volume=str(vol))
    assert "PRE-FLIGHT FAILED" in msg and "30 h old" in msg and "starts from scratch" in msg


def test_missing_files_still_give_a_message(tmp_path):
    msg = publish._step2_failure_message(log_path=tmp_path / "nope.log", data_volume=str(tmp_path / "nope"))
    assert msg


def test_a_failed_step_2_pages_before_the_run_goes_on(monkeypatch):
    sent, order = [], []
    monkeypatch.setattr(publish, "_notify", lambda title, msg, **k: (sent.append((title, k)), order.append("page")))
    monkeypatch.setattr(publish, "_step2_failure_message", lambda: "why")
    # No run records, checkpoints or markers from a test run.
    for name in ("_append_run_record", "_save_checkpoint", "_clear_checkpoint", "_load_checkpoint"):
        monkeypatch.setattr(publish, name, lambda *a, **k: None)

    def step2(dry_run=False, full_rebuild=False, on_line=None):
        return False

    def step5(dry_run=False):
        order.append("step 5")
        return True

    monkeypatch.setattr(publish, "STEPS", [(2, "Build FTS5", step2), (5, "Stats", step5)])
    monkeypatch.setattr("sys.argv", ["publish.py", "--full-rebuild"])
    try:
        publish.main()
    except SystemExit:
        pass
    assert sent and sent[0][0].startswith("OpenCaseLaw: step 2 failed") and sent[0][1]["priority"] == "high"
    assert order.index("page") < order.index("step 5")
