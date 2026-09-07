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

HEALTH_TRIES="${HEALTH_TRIES:-15}"

units=$(systemctl list-units 'mcp-server@*.service' --state=active --no-legend --plain | awk '{print $1}')
if [ -z "$units" ]; then
    echo "rolling_restart_workers: no active mcp-server@ units found" >&2
    exit 0
fi

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
    else
        echo "  WARNING: $u not healthy after ${HEALTH_TRIES}s — continuing" >&2
    fi
done
