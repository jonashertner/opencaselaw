#!/usr/bin/env bash
# perfect_run_orchestrator — wait for the in-flight publish to land, then
# run every post-publish health step in order, capture state, and emit a
# single ntfy summary. Designed to be launched once via nohup at the
# start of a publish so the operator can close their session and come
# back to a clean post-state.
#
# Usage:
#   nohup bash /opt/caselaw/repo/scripts/perfect_run_orchestrator.sh \
#     >/opt/caselaw/repo/logs/perfect_run.log 2>&1 &
#
# Each step is wrapped in `|| true` so a non-fatal failure in one step
# never blocks the rest — we want the FULL state report, not an early
# bail-out.
set -uo pipefail

REPO=/opt/caselaw/repo
TS=$(date -u +%Y%m%d_%H%M)
LOGFILE="${REPO}/logs/perfect_run_${TS}.log"
NTFY_TOPIC="${NTFY_TOPIC:-opencaselaw-prod}"
NTFY_URL="${NTFY_URL:-https://ntfy.sh}"

# Re-exec with logging if not already
if [ -z "${PERFECT_RUN_LOGGED:-}" ]; then
  export PERFECT_RUN_LOGGED=1
  exec >>"$LOGFILE" 2>&1
fi

section() {
  echo
  echo "============================================================"
  echo "[$(date -u +%H:%M:%S)] $*"
  echo "============================================================"
}

# ── Phase 1: wait for publish to leave 'activating' ────────────────
section "Phase 1: waiting for opencaselaw-publish.service to leave activating"
WAIT_START=$(date +%s)
# systemctl is-active returns exit 3 for both "activating" and "failed"
# states — we MUST capture the printed state, not exit code, and never
# overlay with `|| echo unknown` (would clobber a valid "activating").
while true; do
  state=$(systemctl is-active opencaselaw-publish.service 2>/dev/null)
  state=${state:-unknown}
  elapsed=$(( $(date +%s) - WAIT_START ))
  case "$state" in
    activating|reloading)
      if (( elapsed > 21600 )); then    # 6h hard cap
        echo "[$(date -u)] giving up after 6h wait; publish stuck in $state"
        break
      fi
      # Tick every 30 min so the log shows we are alive
      if (( elapsed > 0 && elapsed % 1800 < 60 )); then
        echo "[$(date -u)] still $state (waited ${elapsed}s, ${elapsed} / 21600)"
      fi
      sleep 60
      continue
      ;;
    active|inactive|failed)
      echo "[$(date -u)] publish reached terminal state '$state' after ${elapsed}s"
      break
      ;;
    *)
      # Transient unknown — keep polling instead of bailing out.
      echo "[$(date -u)] transient state '$state' at ${elapsed}s; continuing"
      sleep 60
      continue
      ;;
  esac
done

PUBLISH_FINAL_STATE=$(systemctl is-active opencaselaw-publish.service 2>/dev/null)
PUBLISH_FINAL_STATE=${PUBLISH_FINAL_STATE:-unknown}
echo "publish final state: $PUBLISH_FINAL_STATE"

# ── Phase 2: capture publish.log Summary ──────────────────────────
section "Phase 2: publish.log Summary"
grep -A 30 "=== Summary ===" "${REPO}/logs/publish.log" | tail -40 || true
echo
echo "Last 12 log lines:"
tail -12 "${REPO}/logs/publish.log" || true

# ── Phase 3: re-link decision_structure if clobbered ──────────────
section "Phase 3: re-link decision_structure.db (idempotent)"
bash "${REPO}/scripts/relink_decision_structure.sh" || true

# ── Phase 4: trigger Wayback drain ─────────────────────────────────
section "Phase 4: kick the wayback timer (now table exists post-build)"
systemctl reset-failed opencaselaw-wayback.service 2>/dev/null || true
systemctl start opencaselaw-wayback.service || true
sleep 8
journalctl -u opencaselaw-wayback.service --no-pager -n 15 | tail -15 || true

# ── Phase 5: smoke probe ──────────────────────────────────────────
section "Phase 5: smoke probe of public endpoints"
systemctl reset-failed opencaselaw-smoke.service 2>/dev/null || true
systemctl start opencaselaw-smoke.service || true
sleep 6
cat /var/log/opencaselaw-smoke/latest.json 2>/dev/null | head -30 || true

# ── Phase 6: disk + worker state ──────────────────────────────────
section "Phase 6: disk + workers + timers"
df -h / /mnt/HC_Volume_104655575 | head -3
echo
for p in {8770..8777}; do
  printf 'mcp@%s: %s  ' "$p" "$(systemctl is-active mcp-server@${p}.service)"
done
echo
echo
systemctl list-timers --no-pager 2>/dev/null \
  | grep -E "opencaselaw" | head -10

# ── Phase 7: live decisions.db snapshot ───────────────────────────
section "Phase 7: live decisions.db state"
python3 - <<'PYEOF' || true
import sqlite3
c = sqlite3.connect("file:/opt/caselaw/repo/output/decisions.db?mode=ro&immutable=1",
                    uri=True, timeout=30)
n = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
nc = c.execute("SELECT COUNT(DISTINCT court) FROM decisions").fetchone()[0]
latest = c.execute("SELECT MAX(decision_date) FROM decisions WHERE decision_date <= '2026-12-31'").fetchone()[0]
nw = c.execute("SELECT COUNT(*) FROM decisions WHERE instr(docket_number, char(10)) > 0").fetchone()[0]
print(f"rows: {n:,}, courts: {nc}, latest: {latest}, newline-dockets: {nw}")
cols = [r[1] for r in c.execute("PRAGMA table_info(decisions)")]
if "content_hash" in cols:
    nh = c.execute("SELECT COUNT(*) FROM decisions WHERE content_hash IS NOT NULL").fetchone()[0]
    print(f"content_hash populated: {nh:,} / {n:,}  ({100*nh/n:.1f}%)")
try:
    nq = c.execute("SELECT COUNT(*) FROM wayback_queue").fetchone()[0]
    npend = c.execute("SELECT COUNT(*) FROM wayback_queue WHERE attempted_at IS NULL").fetchone()[0]
    n200 = c.execute("SELECT COUNT(*) FROM wayback_queue WHERE status_code = 200").fetchone()[0]
    print(f"wayback_queue: total={nq:,}, archived={n200:,}, pending={npend:,}")
except sqlite3.OperationalError:
    print("wayback_queue table NOT present (build may not have provisioned it)")
PYEOF

# ── Phase 8: gate verdict ──────────────────────────────────────────
section "Phase 8: QC gate verdict (docs/quality.json summary)"
python3 - <<'PYEOF' || true
import json
try:
    d = json.load(open("/opt/caselaw/repo/docs/quality.json"))
    s = d["summary"]
    print(f"run_at: {d.get('run_at')}")
    print(f"publish_safe: {s.get('publish_safe')}")
    print(f"total: {s.get('total')}, passed: {s.get('passed')}, "
          f"critical: {s.get('critical_failures')}, "
          f"warnings: {s.get('warning_failures')}")
except Exception as e:
    print(f"ERROR reading quality.json: {e}")
PYEOF

# ── Phase 9: final ntfy summary ───────────────────────────────────
section "Phase 9: emit ntfy summary"
SUMMARY="publish: ${PUBLISH_FINAL_STATE}
$(grep -E 'Step .*: (OK|FAILED)' ${REPO}/logs/publish.log | tail -15)
$(df -h / /mnt/HC_Volume_104655575 | tail -2)"
curl -fsS -H "Title: OpenCaseLaw perfect_run done — $PUBLISH_FINAL_STATE" \
     -H "Priority: default" \
     -d "$SUMMARY" \
     "${NTFY_URL}/${NTFY_TOPIC}" >/dev/null 2>&1 || true

echo
echo "============================================================"
echo "[$(date -u +%H:%M:%S)] perfect_run_orchestrator complete"
echo "Final state: $PUBLISH_FINAL_STATE"
echo "Log: $LOGFILE"
echo "============================================================"
