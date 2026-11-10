"""The collector's three hard properties, tested offline.

1. Privacy is structural: a record carrying an identifier key anywhere in
   its tree aborts the run before a byte is written.
2. Search traces are not exported (owner decision 2026-11-10): they stay
   on the server for their 30 days and nowhere else.
3. No dataset is exempt from the identifier scan: the per-IP cost ledger,
   archived raw from 2026-08-24, is no longer collected (2026-11-10).

Everything else (which datasets, which paths) is configuration the
manifest documents; these three are the properties that must not regress.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from metric_tools import UNKNOWN_TOOL  # noqa: E402
from scripts import collect_dev_data  # noqa: E402
from scripts.collect_dev_data import (  # noqa: E402
    Collector,
    _fold_unknown_tools,
    _scan,
)


def test_identifier_scan_finds_keys_at_any_depth():
    assert _scan({"ip": "1.2.3.4"})
    assert _scan({"a": {"b": [{"session_id": "x"}]}})
    assert _scan({"snapshot": {"clients": {"user_agent": "curl"}}})
    assert not _scan({"decision_id": "bge_1", "elapsed_s": 1.2,
                      "steps": {"2": True}})


def test_a_poisoned_record_aborts_before_anything_is_written(tmp_path):
    c = Collector(tmp_path)
    with pytest.raises(SystemExit, match="PRIVACY ABORT"):
        c._write_jsonl_gz("d", [{"ok": 1}, {"nested": {"ip": "x"}}], "s")
    assert not list(tmp_path.rglob("*.jsonl.gz")), \
        "abort must happen before the first write"


def test_clean_records_produce_a_shard_and_a_card(tmp_path):
    c = Collector(tmp_path)
    c._write_jsonl_gz("publish_runs", [{"type": "step", "elapsed_s": 3.1}],
                      "schema note here")
    d = tmp_path / "datasets" / "publish_runs"
    shard = d / f"{c.today}.jsonl.gz"
    assert shard.exists()
    with gzip.open(shard, "rt") as fh:
        assert json.loads(fh.readline())["type"] == "step"
    card = (d / "CARD.md").read_text()
    assert "schema note here" in card
    assert "May leave the private repo:** no" in card


def test_traces_have_no_export_path():
    """Removed, not switched off: no method, no flag, no marker."""
    assert not hasattr(Collector, "collect_traces")
    source = Path(collect_dev_data.__file__).read_text(encoding="utf-8")
    assert "--include-traces" not in source
    assert "search_traces_" not in source.split('"""', 2)[2]


def test_dry_run_writes_nothing(tmp_path):
    c = Collector(tmp_path, dry_run=True)
    c._write_jsonl_gz("d", [{"a": 1}], "s")
    assert not list(tmp_path.rglob("*"))


def test_the_ip_ledger_is_not_collected_and_would_abort(tmp_path, monkeypatch):
    """A ledger file on the server reaches no dataset; and were it ever
    wired back in, its `ip` key aborts like anywhere else."""
    repo = tmp_path / "repo"
    (repo / "logs").mkdir(parents=True)
    (repo / "logs" / "llm_cost_by_ip.jsonl").write_text(json.dumps(
        {"ts": "2999-01-01T00:00:00+00:00", "ip_pseudonym": "p1",
         "ip": "203.0.113.7", "feature": "attest", "cost_usd": 0.004}) + "\n")
    monkeypatch.setattr(collect_dev_data, "REPO", repo)
    dest = tmp_path / "dest"
    Collector(dest).collect()
    assert not (dest / "datasets" / "llm_cost_by_ip").exists()
    assert "llm_cost_by_ip" not in Path(collect_dev_data.__file__).read_text(
        encoding="utf-8").split('"""', 2)[2]
    with pytest.raises(SystemExit, match="PRIVACY ABORT"):
        Collector(tmp_path / "d2")._write_jsonl_gz(
            "llm_cost_by_ip", [{"ip": "203.0.113.7"}], "s")


# ── tools.email, 2026-11-01 ──────────────────────────────────────────
# One request to /api/email put `email` under `tools`: a counter for a URL segment,
# not an identifier, and it aborted the whole run. The counter folds; the scan
# itself stays as strict as it was.

STATS = {"calls": 1, "avg_ms": 0.5, "p50_ms": 0.0, "p95_ms": 0.0, "errors": 0,
         "substantive": 0, "empty": 1, "outcome_declared": 0,
         "outcome_from_status": 1, "empty_reasons": {"http_404": 1}}


def _metrics_record(tools):
    return {"flushed_at": "2999-01-01T00:00:00+00:00", "tools": tools}


def test_scan_itself_still_flags_a_tool_named_email():
    """The fix is in what reaches the scan, never in the scan."""
    assert _scan(_metrics_record({"email": STATS})) == ["$.tools.email"]


def test_a_url_segment_named_email_folds_into_unknown():
    rec = _metrics_record({"cite": STATS, "email": STATS, "phpinfo.php": STATS})
    out = _fold_unknown_tools(rec)
    assert set(out["tools"]) == {"cite", UNKNOWN_TOOL}
    assert out["tools"]["cite"] == STATS
    bucket = out["tools"][UNKNOWN_TOOL]
    assert bucket["calls"] == 2
    assert bucket["outcome_from_status"] == 2
    assert bucket["empty_reasons"] == {"http_404": 2}
    assert not _scan(out)
    assert "email" in rec["tools"], "the source record is not modified"


def test_folding_adds_to_the_bucket_the_server_already_wrote():
    served = {**STATS, "calls": 5}
    out = _fold_unknown_tools(_metrics_record({UNKNOWN_TOOL: served,
                                               "email": STATS}))
    assert out["tools"][UNKNOWN_TOOL]["calls"] == 6
    assert out["tools"][UNKNOWN_TOOL]["avg_ms"] == served["avg_ms"]


def test_a_record_with_only_known_tools_is_untouched():
    rec = _metrics_record({"cite": STATS, "word-addin:laws": STATS,
                           UNKNOWN_TOOL: STATS})
    assert _fold_unknown_tools(rec) is rec


@pytest.mark.parametrize("tools", [
    # the value is the identifier, not a counter
    {"ip": "203.0.113.7"},
    {"email": "someone@example.org"},
    # an identifier nested under a known or an unknown tool name
    {"cite": {**STATS, "session_id": "s_1"}},
    {"probe": {**STATS, "client_ip": "203.0.113.7"}},
    {"email": {**STATS, "user_agent": "curl"}},
    # an identifier key inside a counter that does fold
    {"probe": {**STATS, "empty_reasons": {"ip": 1}}},
    # a malformed bucket must not swallow the name beside it
    {UNKNOWN_TOOL: "203.0.113.7", "email": STATS},
])
def test_real_identifiers_under_tools_still_abort(tmp_path, tools):
    c = Collector(tmp_path)
    with pytest.raises(SystemExit, match="PRIVACY ABORT"):
        c._write_jsonl_gz("daily_metrics",
                          [_fold_unknown_tools(_metrics_record(tools))], "s")
    assert not list(tmp_path.rglob("*.jsonl.gz"))


def test_folding_only_touches_tools(tmp_path):
    """An identifier key anywhere else in the record is not rescued."""
    rec = {"flushed_at": "2999-01-01T00:00:00+00:00", "email": "x",
           "clients": {"ip": 1}, "tools": {"probe": STATS}}
    assert sorted(_scan(_fold_unknown_tools(rec))) == ["$.clients.ip", "$.email"]


def test_no_other_dataset_is_folded():
    """_write_jsonl_gz stays generic: the fold is applied to daily_metrics at
    its read site, so a `tools` key in any other dataset still aborts."""
    source = Path(collect_dev_data.__file__).read_text(encoding="utf-8")
    assert source.count("_fold_unknown_tools(r)") == 1
    with pytest.raises(SystemExit, match="PRIVACY ABORT"):
        Collector(Path("/nonexistent"), dry_run=True)._write_jsonl_gz(
            "llm_usage", [_metrics_record({"email": STATS})], "s")


def _run_collect(tmp_path, monkeypatch, record):
    src = tmp_path / "repo" / "output" / "research_logs"
    src.mkdir(parents=True)
    (src / "daily_metrics.jsonl").write_text(json.dumps(record) + "\n")
    monkeypatch.setattr(collect_dev_data, "REPO", tmp_path / "repo")
    collect_dev_data.Collector(tmp_path / "dest", dry_run=True).collect()


def test_a_run_survives_a_line_with_tools_email(tmp_path, monkeypatch, caplog):
    """The 2026-11-01 failure, through collect() as the timer runs it."""
    caplog.set_level("INFO", logger="collect_dev_data")
    _run_collect(tmp_path, monkeypatch,
                 _metrics_record({"cite": STATS, "email": STATS}))
    assert "[daily_metrics] dry-run: 1 records" in caplog.text


def test_a_run_still_aborts_on_a_real_identifier(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match=r"\$\.tools\.ip"):
        _run_collect(tmp_path, monkeypatch,
                     _metrics_record({"ip": "203.0.113.7"}))


def test_a_failed_run_alerts():
    """The 2026-11-01 abort failed the unit and nobody heard about it."""
    unit = (REPO / "systemd" / "opencaselaw-devdata.service").read_text()
    assert "OnFailure=ntfy-alert@%n.service" in unit
