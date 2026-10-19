"""scripts/migrate_bs_gerichte_ids.py — re-key the BS shard to the decision number."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.migrate_bs_gerichte_ids import migrate, rekey_row  # noqa: E402


def _row(court, docket, docket2, **extra):
    r = {"decision_id": f"{court}_{docket}", "court": court, "canton": "BS",
         "docket_number": docket, "docket_number_2": docket2, "full_text": "x"}
    r.update(extra)
    return r


def test_rekey_moves_id_to_decision_number_and_keeps_the_old_one():
    r = _row("bs_appellationsgericht", "SB.2013.5", "AG.2020.102")
    assert rekey_row(r) == "rekey bs_appellationsgericht_SB.2013.5 -> bs_appellationsgericht_AG.2020.102"
    assert r["decision_id"] == "bs_appellationsgericht_AG.2020.102"
    assert r["previous_decision_id"] == "bs_appellationsgericht_SB.2013.5"
    assert r["docket_number"] == "SB.2013.5"        # the cited case number is untouched
    assert r["docket_number_2"] == "AG.2020.102"


def test_idempotent_and_scoped_to_the_direct_courts():
    already = _row("bs_sozialversicherungsgericht", "IV.2017.108", "SVG.2018.352")
    already["decision_id"] = "bs_sozialversicherungsgericht_SVG.2018.352"
    assert rekey_row(already) is None and "previous_decision_id" not in already
    es = _row("bs_gerichte", "AK.2012.25", "AG.2014.64")       # entscheidsuche leftover: untouched
    es["decision_id"] = "bs_gerichte_AK.2012.25_BS_APG_001"
    assert rekey_row(es) is None
    assert es["decision_id"] == "bs_gerichte_AK.2012.25_BS_APG_001"


def test_migrate_refuses_missing_decision_number_and_collisions():
    rows = [_row("bs_appellationsgericht", "SB.2013.5", ""),
            _row("bs_appellationsgericht", "VD.2019.1", "AG.2020.1"),
            _row("bs_appellationsgericht", "VD.2019.2", "AG.2020.1")]
    stats, notes, errors = migrate(rows)
    assert stats["no_docket2"] == 1 and stats["id_collision"] == 1
    assert any(e.startswith("no_docket2") for e in errors)
    assert any("duplicate id after re-key" in e for e in errors)


def test_migrate_counts_per_court():
    rows = [_row("bs_appellationsgericht", "SB.2013.5", "AG.2014.40"),
            _row("bs_appellationsgericht", "SB.2013.5", "AG.2020.102"),
            _row("bs_sozialversicherungsgericht", "IV.2017.108", "SVG.2018.352")]
    stats, notes, errors = migrate(rows)
    assert not errors
    assert stats["rekeyed"] == 3
    assert stats["rekeyed:bs_appellationsgericht"] == 2
    assert len({r["decision_id"] for r in rows}) == 3


def test_cli_rewrites_shard_atomically_and_appends_state(tmp_path):
    shard = tmp_path / "bs_gerichte.jsonl"
    state = tmp_path / "state.jsonl"
    rows = [_row("bs_appellationsgericht", "SB.2013.5", "AG.2014.40"),
            _row("bs_sozialversicherungsgericht", "IV.2017.108", "SVG.2018.352")]
    shard.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    state.write_text("bs_appellationsgericht_SB.2013.5\n", encoding="utf-8")

    cmd = [sys.executable, str(REPO / "scripts" / "migrate_bs_gerichte_ids.py"),
           "--shard", str(shard), "--state", str(state)]
    dry = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True, cwd=REPO)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stderr and state.read_text() == "bs_appellationsgericht_SB.2013.5\n"

    real = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    assert real.returncode == 0, real.stderr
    out = [json.loads(l) for l in shard.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["decision_id"] for r in out] == [
        "bs_appellationsgericht_AG.2014.40", "bs_sozialversicherungsgericht_SVG.2018.352"]
    assert all(r["previous_decision_id"] for r in out)
    assert not list(tmp_path.glob("*.tmp"))
    # old ids stay (harmless), the new ones are appended so the scrape does not re-fetch
    lines = state.read_text(encoding="utf-8").splitlines()
    assert lines == ["bs_appellationsgericht_SB.2013.5",
                     "bs_appellationsgericht_AG.2014.40",
                     "bs_sozialversicherungsgericht_SVG.2018.352"]

    again = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    assert again.returncode == 0 and "nothing to do" in again.stderr
