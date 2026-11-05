#!/usr/bin/env bash
# Rolling restart of every active mcp-server@ worker, one at a time, each
# gated on its /health endpoint before the next goes down.
#
# Why this exists (2026-07): opencaselaw-practice.service restarted workers
# 8770-8773 SIMULTANEOUSLY every Saturday 06:00 UTC — violating the
# rolling-restart rule and covering only half the
# pool since the 4->8 expansion (66ad3f6), so workers 8774-8777 served a
# stale practice.db forever. Discovery via list-units mirrors
# publish.py:_recycle_mcp_workers — the pool size lives in systemd, not here.
#
# Deliberately no `set -e`: one sick worker must not abort the loop and leave
# the rest of the fleet un-restarted.
#
# After the loop, scripts/record_deployment.py appends the restart (commit,
# UTC window, healthy ports, search models) to logs/deployments.jsonl, the
# source of docs/deployments.{md,json}. Best effort: a logging failure or
# hang never changes this script's outcome. DEPLOY_CAUSE labels the entry;
# OCL_DEPLOY_LOG=0 skips it; OCL_DEPLOY_LOG_PATH moves the log.

HEALTH_TRIES="${HEALTH_TRIES:-15}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

units=$(systemctl list-units 'mcp-server@*.service' --state=active --no-legend --plain | awk '{print $1}')
if [ -z "$units" ]; then
    echo "rolling_restart_workers: no active mcp-server@ units found" >&2
    exit 0
fi

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ok_ports=""
failed_ports=""
for u in $units; do
    p="${u#mcp-server@}"
    p="${p%.service}"
    echo "restarting $u ..."
    systemctl restart "$u"
    ok=0
    for _ in $(seq 1 "$HEALTH_TRIES"); do
        if curl -fsS "http://127.0.0.1:${p}/health" >/dev/null 2>&1; then
            ok=1
            break
        fi
        sleep 1
    done
    if [ "$ok" = 1 ]; then
        echo "  $u healthy"
        ok_ports="$ok_ports $p"
    else
        echo "  WARNING: $u not healthy after ${HEALTH_TRIES}s — continuing" >&2
        failed_ports="$failed_ports $p"
    fi
done
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ "${OCL_DEPLOY_LOG:-1}" != "0" ]; then
    limit=""
    command -v timeout >/dev/null 2>&1 && limit="timeout 60"
    $limit python3 "$SCRIPT_DIR/record_deployment.py" record \
        --started "$started" --finished "$finished" \
        --ok "$ok_ports" --failed "$failed_ports" \
        --cause "${DEPLOY_CAUSE:-rolling_restart_workers.sh}" \
        || echo "rolling_restart_workers: deployment log not written (continuing)" >&2
fi
exit 0
