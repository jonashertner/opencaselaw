#!/usr/bin/env python3
"""
check_output_freshness.py — DEADMAN alarm on stale pipeline OUTPUTS.

Independent of state/last_publish_success.json. That marker is written only
on a fully-successful publish, so it DISARMS exactly when the pipeline fails:
during the 2026-06-03..06 QC-gate freeze (a single bad date cascade-skipped
HF upload + git push for 4 nights) the swap kept serving fresh data but the
published OUTPUTS silently froze and NO ongoing alert fired. This deadman
checks the outputs themselves and pages via ntfy if any is older than a
budget — so a silent freeze surfaces within one timer interval.

Four independent signals (each best-effort; a check that can't determine
freshness is logged, not paged, to avoid transient-failure false alarms):
  1. HuggingFace mirror lastModified  (voilaj/swiss-caselaw)         [Step 4]
  2. last commit touching docs/quality.json  (the QC dashboard push)
  3. last commit touching docs/stats.json    (the stats/feeds push)  [Step 6]
  4. age of the local structure/ exports (structure.parquet nightly,
     erwaegungen_paragraphs.parquet weekly), with the skip reason from
     structure/export_status.json                                    [Step 3]

Plus one layout check of the HF repo, paged separately (own state file,
default priority): parquet files outside the published prefixes, and a card
config that reads a non-court file or skips a court
(scripts/check_hf_unmanaged_parquet.py).

Run on its OWN timer (e.g. every 6h), separate from the publish unit, so it
fires even when the publish never runs. Best-effort, never raises.

Usage:
    python3 scripts/check_output_freshness.py
    python3 scripts/check_output_freshness.py --max-age-hours 36 --no-ntfy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Env-driven since 2026-08-24 (one operator topic via NTFY_TOPIC in
# /opt/caselaw/ops.env drop-ins); legacy publish topic as fallback.
NTFY_URL = (
    os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    + "/"
    + os.environ.get("NTFY_TOPIC", "opencaselaw-publish")
)
REPO = Path(__file__).resolve().parent.parent
HF_API = "https://huggingface.co/api/datasets/voilaj/swiss-caselaw"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat((s or "").strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _age_h(dt: datetime, now: datetime) -> float:
    return (now - dt).total_seconds() / 3600.0


def check_hf(now: datetime, budget: float) -> str | None:
    """HF mirror lastModified. None if fresh OR if the check itself failed
    (transient — logged, not paged)."""
    try:
        req = urllib.request.Request(HF_API, headers={"User-Agent": "ocl-output-freshness/1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        lm = _parse_iso(data.get("lastModified"))
        if lm is None:
            print("hf: no lastModified field", file=sys.stderr)
            return None
        age = _age_h(lm, now)
        if age > budget:
            return f"STALE HF mirror: {age:.0f}h since last upload (lastModified {data.get('lastModified')})"
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"hf check skipped: {e}", file=sys.stderr)
    return None


def check_git_path(path_spec: str, label: str, now: datetime, budget: float) -> str | None:
    """Age of the most recent LOCAL commit touching path_spec. The publish
    commits these on its push step, which is skipped during a cascade freeze,
    so a frozen output = an old commit. None if fresh or undeterminable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%ct", "--", path_spec],
            capture_output=True, text=True, timeout=15,
        )
        ts = out.stdout.strip()
        if not ts:
            print(f"git: no commit found for {path_spec}", file=sys.stderr)
            return None
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        age = _age_h(dt, now)
        if age > budget:
            return f"STALE {label}: {age:.0f}h since last automated commit ({path_spec})"
    except Exception as e:  # noqa: BLE001
        print(f"git check {path_spec} skipped: {e}", file=sys.stderr)
    return None


STRUCTURE_EXPORTS = (
    # (file under <dataset>/structure/, export_status.json count key, cadence)
    ("structure.parquet", "structure", "nightly"),
    ("erwaegungen_paragraphs.parquet", "erwaegungen_paragraphs", "weekly"),
)


def check_structure_exports(now: datetime, dataset_dir: Path, nightly_budget: float,
                            weekly_budget: float) -> list[str]:
    """The structure add-ons of export_parquet.py keep the last good file
    whenever they are skipped (projected over budget, no time left, error),
    and step 4 uploads that file again — so the HF mirror's lastModified stays
    fresh while structure/structure.parquet sat at its 2026-09-10 file for
    four weeks (2026-10-07). Pages on the age of the local files and names the
    skip reason the last run recorded. A missing file is logged, not paged."""
    sdir = dataset_dir / "structure"
    counts: dict = {}
    ran_at = None
    try:
        status = json.loads((sdir / "export_status.json").read_text())
        counts = status.get("counts") or {}
        ran_at = status.get("at")
    except FileNotFoundError:
        print(f"structure: no {sdir / 'export_status.json'}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"structure: export_status.json unreadable: {e}", file=sys.stderr)
    alerts: list[str] = []
    for name, key, cadence in STRUCTURE_EXPORTS:
        path = sdir / name
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except FileNotFoundError:
            print(f"structure: {path} not found", file=sys.stderr)
            continue
        except OSError as e:
            print(f"structure check {path} skipped: {e}", file=sys.stderr)
            continue
        age = _age_h(written, now)
        if age > (nightly_budget if cadence == "nightly" else weekly_budget):
            msg = f"STALE structure/{name} ({cadence}): {age:.0f}h since last written"
            why = counts.get(f"{key}_skipped")
            if why:
                msg += f"; the last export run ({ran_at}) skipped it: {why}"
            alerts.append(msg)
    return alerts


def check_hf_layout() -> list[str] | None:
    """Parquet files outside the published prefixes of the HF repo, and a card
    config that reads a non-court file or skips a court (see
    scripts/check_hf_unmanaged_parquet.py). [] if clean; None if the check
    itself failed (transient — logged, not paged, and no all-clear either)."""
    try:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from scripts.check_hf_unmanaged_parquet import (
            HF_REPO_ID,
            fetch_remote_card,
            layout_findings,
            list_remote_files,
        )
        sha, paths = list_remote_files(HF_REPO_ID, "main")
        return [f"HF {sha[:8]}: {x}" for x in layout_findings(paths, fetch_remote_card(HF_REPO_ID, sha))]
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"hf layout check skipped: {e}", file=sys.stderr)
        return None


def post_ntfy(body: str, title: str, priority: str = "high") -> bool:
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=body.encode("utf-8", errors="replace"),
            headers={
                "Title": title.encode("ascii", errors="replace").decode("ascii"),
                "Priority": priority,
                "Tags": "rotating_light",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001
        print(f"ntfy post failed: {e}", file=sys.stderr)
        return False


def _digest(alerts: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(alerts)).encode("utf-8", errors="replace")).hexdigest()


def maybe_dispatch(alerts: list[str], now: datetime, state_dir: Path, renag_hours: float, *,
                   state_name: str = "output_freshness_last_dispatched.json",
                   title: str = "opencaselaw OUTPUTS STALE",
                   header: str = ("stale OUTPUT signal(s) — the publish may be silently frozen "
                                  "(HF mirror / dashboard / git push not updated)"),
                   recovered: tuple[str, str] = ("All pipeline outputs are fresh again.",
                                                 "opencaselaw outputs recovered"),
                   priority: str = "high") -> str:
    """Dedup-on-change + daily re-nag while still stale; one all-clear on recovery.
    The keyword arguments give a second alert family its own state and wording."""
    state_dir.mkdir(parents=True, exist_ok=True)
    sf = state_dir / state_name
    prev: dict = {}
    if sf.exists():
        try:
            prev = json.loads(sf.read_text())
        except Exception:
            prev = {}

    if not alerts:
        if prev.get("digest"):
            post_ntfy(recovered[0], recovered[1], priority="default")
            try:
                sf.unlink()
            except OSError:
                pass
            return "all-clear"
        return "ok"

    digest = _digest(alerts)
    last_dt = _parse_iso(prev.get("dispatched_at"))
    renag = last_dt is not None and _age_h(last_dt, now) >= renag_hours
    if digest == prev.get("digest") and not renag:
        return "unchanged"
    body = f"{len(alerts)} {header}:\n\n" + "\n".join(alerts)
    if not post_ntfy(body, f"{title} ({len(alerts)})", priority=priority):
        return "post-failed"
    try:
        sf.write_text(json.dumps({"digest": digest, "dispatched_at": now.isoformat(),
                                  "alerts": alerts}, indent=2))
    except OSError as e:
        print(f"state write failed: {e}", file=sys.stderr)
    return "posted"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-age-hours", type=float, default=36.0,
                    help="alert if an output is older than this (default 36h: tolerates one missed daily publish)")
    ap.add_argument("--renag-hours", type=float, default=24.0,
                    help="re-notify while still stale after this gap (default 24h)")
    ap.add_argument("--state-dir", default=str(REPO / "state"))
    ap.add_argument("--dataset-dir", default=str(REPO / "output" / "dataset"),
                    help="export_parquet.py output (publish DATASET_DIR)")
    ap.add_argument("--weekly-max-age-hours", type=float, default=8 * 24 + 12,
                    help="budget for the weekly (Sunday) structure paragraphs export "
                         "(default 8.5 days: tolerates one late Sunday)")
    ap.add_argument("--no-ntfy", action="store_true", help="skip ntfy (local debug)")
    a = ap.parse_args()

    now = _now()
    alerts: list[str] = []
    for sig in (
        lambda: check_hf(now, a.max_age_hours),
        lambda: check_git_path("docs/quality.json", "QC dashboard (quality.json)", now, a.max_age_hours),
        lambda: check_git_path("docs/stats.json", "stats/feeds push (stats.json)", now, a.max_age_hours),
    ):
        r = sig()
        if r:
            alerts.append(r)
    alerts += check_structure_exports(now, Path(a.dataset_dir), a.max_age_hours,
                                      a.weekly_max_age_hours)

    if alerts:
        print(f"=== {len(alerts)} stale OUTPUT signal(s) at {now.isoformat()} ===")
        for x in alerts:
            print("  " + x)
    else:
        print(f"All pipeline outputs fresh (<{a.max_age_hours:.0f}h) at {now.isoformat()}")

    if not a.no_ntfy:
        print(f"ntfy: {maybe_dispatch(alerts, now, Path(a.state_dir), a.renag_hours)}")

    # Separate family: not staleness, and not urgent at night. Same dedup + daily re-nag.
    layout = check_hf_layout()
    for x in layout or []:
        print("  " + x)
    if layout is not None and not a.no_ntfy:
        r = maybe_dispatch(
            layout, now, Path(a.state_dir), a.renag_hours,
            state_name="hf_layout_last_dispatched.json",
            title="opencaselaw HF dataset layout",
            header="problem(s) in the HF dataset layout (runbooks/hf_root_parquet_cleanup_2026-10-07.md)",
            recovered=("The HF dataset layout is clean again.", "opencaselaw HF layout recovered"),
            priority="default")
        print(f"ntfy (hf layout): {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
