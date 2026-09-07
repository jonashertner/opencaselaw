#!/usr/bin/env bash
# Close the coverage gaps reported by check_scraper_freshness.
#
# Written 2026-08-25 after the first scraper-freshness alert ever to reach a
# human (the topic was unified on 08-24; before that it pushed to a topic
# nobody was subscribed to, which is why every gap said "seit 11 Tagen").
#
# Reported gaps, 2026-08-25 03:00 UTC:
#   be_verwaltungsgericht  2,159 missing (portal 11,594 / ours 9,435)  <- 79% of the backlog, = GitHub #68
#   vs_gerichte              399 missing (4,995 / 4,596)
#   gr_gerichte               50 missing (14,856 / 14,806)
#   ne_gerichte               45 missing (7,701 / 7,656)               <- needs SOCKS tunnel
#   ne_jurisprudence_adm      37 missing (1,648 / 1,611)               <- needs SOCKS tunnel
#   ju_gerichte               29 missing (1,170 / 1,141)               <- needs SOCKS tunnel
#
# WHY TWO GROUPS: jura.ch and ne.ch block Hetzner IPs at TCP (CLAUDE.md
# invariant #6), so NE/JU can only be scraped through the reverse SOCKS tunnel
# from the MacBook. If the tunnel is down those three CANNOT work, and this
# script skips them loudly instead of burning an hour failing.
#
# SAFETY
#   - Scrapers write ONLY to state/ (coverage.db, *.jsonl) and output/, never
#     to the serving DBs. Invariant #1 is untouched.
#   - Run under nice+ionice: the QC gate is starved by concurrent load on
#     weekdays (measured 08-24), and this may overlap the 03:30-17:00 build.
#   - Sequential, never parallel — these are polite scrapers against public
#     portals and parallelism would look like an attack.
#   - Per-court timeout so one stuck portal cannot block the rest.
#   - Nothing here reaches users until the next full build ingests it, so
#     there is no reason to rush it into the build window.
#
# Usage:
#   bash scripts/rescan_coverage_gaps.sh            # all, tunnel ones auto-skipped if down
#   bash scripts/rescan_coverage_gaps.sh --no-tunnel # only the three that work from Hetzner
#   bash scripts/rescan_coverage_gaps.sh --wait     # hold until the publish lock clears

set -uo pipefail

REPO=/opt/caselaw/repo
LOGDIR=/root/rescan_$(date -u +%Y-%m-%d)
LOCK=/tmp/opencaselaw-publish.lock
SOCKS_HOST=127.0.0.1
SOCKS_PORT=1080

NO_TUNNEL=0
WAIT_FOR_BUILD=0
for a in "$@"; do
    case "$a" in
        --no-tunnel) NO_TUNNEL=1 ;;
        --wait)      WAIT_FOR_BUILD=1 ;;
        *) echo "unknown flag: $a" >&2; exit 2 ;;
    esac
done

mkdir -p "$LOGDIR"
cd "$REPO" || exit 1

# Courts that work from the Hetzner IP directly. Biggest LAST so the quick
# wins land first and a long Bern run cannot delay them.
DIRECT=(gr_gerichte vs_gerichte be_verwaltungsgericht)
# Courts that require the MacBook SOCKS tunnel.
TUNNELED=(ju_gerichte ne_jurisprudence_adm ne_gerichte)

if [ "$WAIT_FOR_BUILD" = "1" ]; then
    while [ -f "$LOCK" ]; do
        echo "$(date -u +%H:%M) publish lock held — waiting 10 min"
        sleep 600
    done
fi

if [ -f "$LOCK" ]; then
    echo "NOTE: the nightly build is RUNNING. Scrapers do not touch the serving"
    echo "      DBs, but they add load, and the QC gate is load-sensitive."
    echo "      Use --wait to hold until it finishes (~17:00 UTC)."
    echo
fi

run_one() {
    local court="$1" limit_s="$2"
    local log="$LOGDIR/$court.log"
    echo "=== $court  start $(date -u +%H:%M:%S)"
    OCL_SCRAPER_RESCAN_ALL=1 timeout "$limit_s" \
        nice -n 15 ionice -c 3 \
        python3 run_scraper.py "$court" -v >"$log" 2>&1
    local rc=$?
    local new
    new=$(grep -cE "new|added|inserted" "$log" 2>/dev/null || echo "?")
    case $rc in
        0)   echo "    OK   rc=0   (log: $log)" ;;
        124) echo "    TIMEOUT after ${limit_s}s — partial, safe to re-run (log: $log)" ;;
        *)   echo "    FAIL rc=$rc  (log: $log)"; tail -3 "$log" | sed "s/^/      /" ;;
    esac
}

echo "############ direct scrapes (no tunnel needed) ############"
for c in "${DIRECT[@]}"; do
    # Bern is 11,594 portal entries on a full rescan — give it room.
    if [ "$c" = "be_verwaltungsgericht" ]; then run_one "$c" 14400; else run_one "$c" 5400; fi
done

echo
echo "############ tunnel-dependent scrapes ############"
if [ "$NO_TUNNEL" = "1" ]; then
    echo "skipped (--no-tunnel)"
elif ! nc -z -w3 "$SOCKS_HOST" "$SOCKS_PORT" 2>/dev/null; then
    echo "SKIPPED: SOCKS tunnel is DOWN (nothing listening on $SOCKS_HOST:$SOCKS_PORT)."
    echo "  jura.ch and ne.ch block Hetzner IPs, so these cannot run without it."
    echo "  Start the configured private scraper tunnel, then retry."
    echo "  Then re-run:              bash scripts/rescan_coverage_gaps.sh"
    echo "  Affected: ${TUNNELED[*]} (111 decisions total — the small half of the backlog)"
else
    echo "tunnel is UP — proceeding"
    for c in "${TUNNELED[@]}"; do run_one "$c" 5400; done
fi

echo
echo "############ done $(date -u +%H:%M:%S) ############"
echo "Logs: $LOGDIR"
echo
echo "Verify the gaps actually closed (re-runs the same check that alerted):"
echo "  python3 $REPO/scripts/check_scraper_freshness.py --no-ntfy"
echo
echo "New decisions reach users only after the next full build ingests them."
