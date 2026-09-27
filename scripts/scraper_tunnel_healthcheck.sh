#!/bin/bash
# scraper_tunnel_healthcheck.sh — detect zombie SOCKS tunnel
#
# The Mac mini reverse SOCKS tunnel (127.0.0.1:1080) is used by ju_gerichte
# and ne_gerichte scrapers to reach court portals that block Hetzner IPs.
#
# When the Mac mini sleeps, the SSH session stays "established" per TCP
# (keepalive timer hasn't expired) but traffic forwarding is dead. This
# script detects that state and kills the zombie so the Mac mini can reconnect
# cleanly on next wake.
#
# Ran hourly via opencaselaw-tunnel-healthcheck.timer.

set -u

LISTENER=$(ss -tln 2>/dev/null | grep -c ':1080')
if [ "$LISTENER" -eq 0 ]; then
    echo "$(date -u +%FT%TZ) tunnel: NO LISTENER (Mac mini not connected)"
    exit 0
fi

# Listener present — probe through it with a 5s budget.
# --socks5-hostname forces DNS through the tunnel too, matching scraper config.
if timeout 10 curl -s -o /dev/null -w '' --socks5-hostname 127.0.0.1:1080 \
    --max-time 8 'https://api.ipify.org/' 2>/dev/null; then
    echo "$(date -u +%FT%TZ) tunnel: OK"
    exit 0
fi

echo "$(date -u +%FT%TZ) tunnel: ZOMBIE — killing listener-owner pid"
pid=$(ss -tlnp 2>/dev/null | grep ':1080' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$pid" ]; then
    kill -9 "$pid" 2>/dev/null && echo "  killed pid $pid"
fi
