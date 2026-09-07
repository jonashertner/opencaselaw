#!/usr/bin/env bash
# hf_cache_restart_verify.sh [restart|verify]
#
# restart (default): gated rolling restart of the mcp-server@ pool so the workers
#   pick up HF_HOME / HF_HUB_OFFLINE from .env.mcp, then verify that the pinpoint
#   semantic model is live in every worker. Built to run UNATTENDED from a
#   one-shot transient timer: it aborts (and notifies) instead of restarting
#   whenever anything looks off: publish or incremental pipeline still running
#   (build-window rule), code on disk not bootable,
#   env lines missing. The pipeline gate is re-evaluated before EVERY worker
#   restart, not once up front. Stops on the FIRST worker that does not come
#   back healthy (the repo's rolling_restart_workers.sh deliberately continues;
#   unattended we must not).
# verify: no restart; just the live per-worker check + journal counts, e.g. after
#   publish.py's own post-swap recycle (~10:07 UTC) has re-read .env.mcp.
#
# Canonical: scripts/hf_cache_restart_verify.sh (deployed via git: commit, push,
# `git merge --ff-only` on the VPS); run it from the repo path.
# Log: /root/hfcache_<mode>_<stamp>.log, journal dump: /root/hfcache_<mode>_<stamp>.journal
set -uo pipefail
MODE=${1:-restart}
case $MODE in restart|verify) ;; *) echo "usage: $0 [restart|verify]" >&2; exit 64;; esac
STAMP=$(date -u +%Y%m%d_%H%M%S)
LOG=/root/hfcache_${MODE}_${STAMP}.log
JOURNAL=/root/hfcache_${MODE}_${STAMP}.journal
: >>"$LOG" || { echo "cannot write $LOG" >&2; exit 1; }
exec > >(tee -a "$LOG") 2>&1
TEE_PID=$!   # bash 5 sets $! for a process substitution
REPO=/opt/caselaw/repo
ENV=$REPO/.env.mcp
CACHE=/opt/caselaw/hf_cache
MODEL_REF=$CACHE/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/refs/main
HEALTH_TRIES=${HEALTH_TRIES:-45}
restarted=""
# shellcheck disable=SC1091
set +u; . /opt/caselaw/ops.env 2>/dev/null || true; set -u
NTFY_TOPIC=${NTFY_TOPIC:-opencaselaw-prod}   # same fallback as perfect_run_orchestrator.sh

notify() {  # title, tags, body  (same ntfy topic the ops timers use)
  [ -n "${NTFY_TOPIC:-}" ] || { echo "notify skipped: NTFY_TOPIC unset"; return 0; }
  curl -sS --max-time 20 -H "Title: $1" -H "Tags: $2" -d "$3" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}
finish() {  # drain the tee before systemd reaps the cgroup
  trap - TERM INT PIPE EXIT
  exec >&- 2>&-
  wait "$TEE_PID" 2>/dev/null
}
died() {  # signal handler: SIGTERM from a timer timeout, SIGPIPE if tee died
  trap - TERM INT PIPE EXIT
  notify "hf_cache $MODE: DIED" "rotating_light" "killed at line $1; restarted so far:${restarted:- none}. Remaining workers keep their old env. Log: $LOG"
  exec >>"$LOG" 2>&1 || exec >/dev/null 2>&1   # tee may be gone; write the log directly
  echo "DIED: signal at line $1; restarted so far:${restarted:- none}"
  wait "$TEE_PID" 2>/dev/null
  exit 1
}
trap 'died $LINENO' TERM INT PIPE
trap finish EXIT
abort() {
  echo "ABORT: $1"
  notify "hf_cache $MODE: NOT done" "warning" "$1. Restarted so far:${restarted:- none}; the rest keep whatever env they booted with; publish.py's daily post-swap recycle (~10:07 UTC) re-reads .env.mcp. Log: $LOG"
  exit 1
}
gate() {  # build-window gate, called immediately before EVERY systemctl restart
  local pub inc t next ns now
  pub=$(systemctl show -p ActiveState --value opencaselaw-publish.service)
  inc=$(systemctl show -p ActiveState --value opencaselaw-publish-incremental.service)
  echo "gate: publish=$pub incremental=$inc"
  [ "$pub" != activating ] || abort "opencaselaw-publish.service is still running"
  [ "$inc" != activating ] || abort "opencaselaw-publish-incremental.service is still running"
  now=$(date +%s)
  for t in opencaselaw-publish.timer opencaselaw-publish-incremental.timer; do
    next=$(systemctl show -p NextElapseUSecRealtime --value "$t" 2>/dev/null)
    [ -n "$next" ] && [ "$next" != n/a ] || continue
    ns=$(date -d "$next" +%s 2>/dev/null) || continue
    [ $((ns - now)) -ge 600 ] || abort "$t fires in $((ns - now))s (< 10 min)"
  done
}

echo "== $(date -u) mode=$MODE host=$(hostname)"
units=$(systemctl list-units 'mcp-server@*.service' --state=active --no-legend --plain | awk '{print $1}')
[ -n "$units" ] || abort "no active mcp-server@ units"
n=$(echo "$units" | wc -l | tr -d ' ')
echo "units ($n): $(echo $units | tr '\n' ' ')"
grep -qE '^HF_HOME=/opt/caselaw/hf_cache$' "$ENV" || abort "HF_HOME line missing from .env.mcp"
grep -qE '^HF_HUB_OFFLINE=1$' "$ENV"          || abort "HF_HUB_OFFLINE line missing from .env.mcp"
[ -f "$MODEL_REF" ] || abort "offline cache copy missing under $CACHE"

if [ "$MODE" = restart ]; then
  echo "== gate: no publish pipeline may be running (re-checked before each worker restart)"
  echo "publish.log (03:30 full run):        $(tail -1 "$REPO/logs/publish.log" 2>/dev/null | cut -c1-160)"
  echo "incremental_nightly.log (20:00 run): $(tail -1 "$REPO/logs/incremental_nightly.log" 2>/dev/null | cut -c1-160)"
  gate
  echo "== gate: code on disk must be bootable (a conflicted stash pop once left markers in mcp_server.py)"
  unmerged=$(git -c safe.directory="$REPO" -C "$REPO" ls-files -u) || abort "git unavailable in $REPO"
  [ -z "$unmerged" ] || abort "unmerged paths in git: $(echo "$unmerged" | awk '{print $4}' | sort -u | head -5 | tr '\n' ' ')"
  markers=$(grep -rlE '^(<<<<<<< |>>>>>>> )' --include='*.py' --exclude-dir=.git --exclude-dir=output --exclude-dir=logs "$REPO" | head -5)
  [ -z "$markers" ] || abort "conflict markers in: $(echo $markers)"
  python3 -c "import ast; ast.parse(open('$REPO/mcp_server.py').read())" || abort "mcp_server.py does not parse"
  SINCE=@$(date +%s)   # epoch: unambiguous for journalctl regardless of the host TZ
  echo "== RAM before: $(free -g | sed -n 2p)"
  echo "== rolling restart, one worker at a time, health-gated, stop on first unhealthy"
  for u in $units; do
    p=${u#mcp-server@}; p=${p%.service}
    gate
    echo "restarting $u ..."
    systemctl restart "$u"
    ok=0
    for _ in $(seq 1 "$HEALTH_TRIES"); do
      if curl -fsS "http://127.0.0.1:${p}/health" >/dev/null 2>&1; then ok=1; break; fi
      sleep 1
    done
    if [ "$ok" = 1 ]; then
      echo "  $u healthy"; restarted="$restarted $u"
    else
      echo "  $u NOT healthy after ${HEALTH_TRIES}s, stopping here; the rest keep the old env"
      systemctl status "$u" --no-pager 2>&1 | head -12
      journalctl -u "$u" --since "$SINCE" --no-pager -q | tail -20 | cut -c1-200
      notify "hf_cache restart STOPPED" "rotating_light" "$u did not become healthy; restarted so far:${restarted:- none}. Remaining workers untouched. Log: $LOG"
      exit 1
    fi
  done
fi

echo "== env actually seen by the workers"
applied=0
for u in $units; do
  pid=$(systemctl show -p MainPID --value "$u")
  if tr '\0' '\n' < /proc/"$pid"/environ 2>/dev/null | grep -q '^HF_HOME=/opt/caselaw/hf_cache$'; then applied=$((applied+1)); fi
done
echo "workers with HF_HOME in their environment: $applied/$n"
[ "$applied" = "$n" ] || abort "only $applied/$n workers have the new env (not restarted yet?)"

echo "== live check per worker: FR paraphrase of BGE 146 III 25 E. 3.2 vs the DE decision (expect source=semantic, e_number=3.2)"
# The claim is a French paraphrase of E. 3.2 of the German judgment. It was
# scored against the stored MiniLM vectors before being adopted: best cosine
# 0.82 on E. 3.2 (verified through worker 8770 on 2026-09-04). The earlier
# keyword-bag claim ("législateur maintenir système double délai prescription
# amiante") scored 0.35, below PINPOINT_SEMANTIC_MEDIUM=0.55, so it reported
# "0/8 live" on a working fleet. Any replacement claim must be scored first.
# First call per worker warms the lazy model load (the pinpoint threads race on
# _SEMANTIC_MODEL_TRIED during the very first rescue), second call is the check.
# Each call goes through the normal REST search path, i.e. with LLM query
# expansion (Haiku, 2 s timeout) when LLM_EXPANSION_ENABLED: up to 2*n small
# Anthropic requests per run and a non-deterministic top-5. Accepted; there is
# no request-level switch to turn expansion off.
Q='q=Le%20Conseil%20des%20Etats%20avait%20propos%C3%A9%20une%20solution%20transitoire%20sp%C3%A9ciale%20pour%20les%20dommages%20corporels%20caus%C3%A9s%20par%20l%27amiante%2C%20supprim%C3%A9e%20ensuite%20en%20raison%20de%20la%20cr%C3%A9ation%20du%20fonds%20d%27indemnisation%20des%20victimes%20de%20l%27amiante.&court=bge&date_from=2019-11-06&date_to=2019-11-06&limit=5&include_pinpoint=true'
WANT=bge_BGE_146_III_25
check_port() {
  curl -s --max-time 120 "http://127.0.0.1:$1/api/decisions?$Q" | python3 -c '
import sys, json
p, want = sys.argv[1], sys.argv[2]
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(p, "NO JSON:", e); sys.exit(0)
rs = d.get("results") or []
hit = [r for r in rs if r.get("decision_id") == want]
if not hit:
    print(p, want, "not in top", len(rs), [r.get("decision_id") for r in rs]); sys.exit(0)
pp = hit[0].get("pinpoint")
ok = isinstance(pp, dict) and pp.get("source") == "semantic" and str(pp.get("e_number")) == "3.2"
print(p, want, "OK semantic" if ok else "NOT semantic", json.dumps(pp, ensure_ascii=False)[:200])
' "$1" "$WANT"
}
sem_ok=0
for u in $units; do
  p=${u#mcp-server@}; p=${p%.service}
  check_port "$p" >/dev/null   # warm
  out=$(check_port "$p"); echo "$out"
  case "$out" in *"OK semantic"*) sem_ok=$((sem_ok+1));; esac
done
sleep 5
# Journal per unit since ITS OWN boot (epoch, so the host TZ does not matter):
# a fleet-wide window from the oldest boot would attribute lines from the
# replaced process of a later-restarted worker to the current fleet.
echo "== journal per worker since its own boot -> $JOURNAL"
: > "$JOURNAL"
for u in $units; do
  ts=$(systemctl show -p ActiveEnterTimestamp --value "$u")
  s=$(date -d "$ts" +%s 2>/dev/null) || { echo "$u: cannot parse boot time '$ts'"; continue; }
  echo "$u booted $ts"
  journalctl -u "$u" --since "@$s" --no-pager -q >> "$JOURNAL"
done
loaded=$(grep -c 'loaded pinpoint semantic model' "$JOURNAL")
failed=$(grep -c 'semantic model load failed' "$JOURNAL")
echo "loaded=$loaded failed=$failed (expect $n / 0)"
grep -E 'loaded pinpoint semantic model|semantic model load failed' "$JOURNAL" | cut -c1-220 | head -10
echo "== RAM after: $(free -g | sed -n 2p)"
for u in $units; do
  m=$(systemctl show -p MemoryCurrent --value "$u")
  case $m in ''|*[!0-9]*) m=n/a;; *) m=$((m/1048576))MB;; esac   # "[not set]" without memory accounting
  echo "$u mem=$m"
done
summary="semantic pinpoint live on $sem_ok/$n workers; journal loaded=$loaded failed=$failed; RAM available $(free -g | awk 'NR==2{print $7}') GB. Log: $LOG"
if [ "$sem_ok" = "$n" ] && [ "$loaded" = "$n" ] && [ "$failed" = 0 ]; then
  notify "hf_cache $MODE: OK" "white_check_mark" "$summary"
else
  notify "hf_cache $MODE: CHECK" "warning" "$summary"
fi
echo "== $(date -u) done: $summary"
