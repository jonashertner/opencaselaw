#!/bin/bash
# One-off, 2026-11-10: make the server match docs/datenschutz/ (privacy page).
# Run on the VPS as root, after the ff-merge that brings this file:
#
#   ssh -i ~/.ssh/caselaw root@46.225.212.40 \
#     'cd /opt/caselaw/repo && git fetch -q origin && git merge --ff-only origin/main \
#      && bash scripts/privacy_logs_cleanup_2026-11-10.sh'
#
# 1. nginx: stop writing tier3.log (an IP-derived university label plus the
#    install-cohort hash for 30 days, read by nothing) and drop the full
#    User-Agent from legacy8765.log.
# 2. Delete what the page no longer allows: tier3.log*, legacy8765 logs with
#    User-Agents, the three April 2026 tier2 files.
# 3. journald: bound retention to 14 days (ops/journald/ocl-retention.conf).
# 4. syslog: remove the API workers' uvicorn access lines (IP address + path).
#    They stopped at the 16:06 UTC worker restart (access_log=False); the
#    copies in /var/log/syslog* reach back about five weeks.
# 5. Deploy the Word add-in (self-hosted fonts on install.html).
#
# Each step prints what it did; the last block verifies.
set -euo pipefail
REPO=/opt/caselaw/repo
NICE="nice -n 19 ionice -c 3"
PAT='INFO: +[0-9a-fA-F.:]+:[0-9]+ - "'

echo "== 1. nginx"
V=/etc/nginx/sites-available/mcp-server
L=/etc/nginx/conf.d/ocl-logging.conf
mkdir -p /etc/nginx/backups
cp -a "$V" /etc/nginx/backups/mcp-server.bak.20261110-tier3
cp -a "$L" /etc/nginx/backups/ocl-logging.conf.bak.20261110
sed -i '/access_log \/var\/log\/nginx\/tier3\.log tier3;/d' "$V"
cp "$REPO/ops/nginx/ocl-logging.conf" "$L"
if nginx -t 2>&1; then
    systemctl reload nginx
    echo "nginx reloaded"
else
    cp -a /etc/nginx/backups/mcp-server.bak.20261110-tier3 "$V"
    cp -a /etc/nginx/backups/ocl-logging.conf.bak.20261110 "$L"
    nginx -t
    echo "nginx change rolled back; nothing else done" >&2
    exit 1
fi
sleep 2

echo "== 2. files"
cd /var/log/nginx
rm -fv tier3.log tier3.log-* tier2.log.1 tier2.log.2.gz tier2.log.3.gz legacy8765.log.*
: > legacy8765.log
rm -fv /etc/logrotate.d/ocl-tier3

echo "== 3. journald"
install -D -m 0644 "$REPO/ops/journald/ocl-retention.conf" /etc/systemd/journald.conf.d/ocl-retention.conf
systemctl restart systemd-journald
journalctl --rotate
journalctl --vacuum-time=12d

echo "== 4. syslog"
logrotate -f /etc/logrotate.d/rsyslog
cd /var/log
for f in syslog.1 syslog.2.gz syslog.3.gz syslog.4.gz; do
    [ -e "$f" ] || continue
    t="$f.filtered"
    case "$f" in
        *.gz) $NICE zcat "$f" | { grep -avE "$PAT" || [ $? -eq 1 ]; } | $NICE gzip > "$t" ;;
        *)    { $NICE grep -avE "$PAT" "$f" || [ $? -eq 1 ]; } > "$t" ;;
    esac
    chown --reference="$f" "$t"; chmod --reference="$f" "$t"; touch -r "$f" "$t"
    mv "$t" "$f"
    echo "filtered $f"
done

echo "== 5. Word add-in"
bash "$REPO/scripts/deploy_word_addin.sh" | tail -3

echo "== verify"
for f in /var/log/syslog /var/log/syslog.1 /var/log/syslog.2.gz /var/log/syslog.3.gz /var/log/syslog.4.gz; do
    [ -e "$f" ] && echo "$f uvicorn lines: $(zgrep -caE "$PAT" "$f" || true)"
done
ls /var/log/nginx | grep -cE '^tier3' | sed 's/^/tier3 files: /' || true
grep -c 'http_user_agent' "$L" | sed 's/^/user_agent in logging conf (tier1 and uniq formats only): /' || true
grep -rc 'tier3.log' "$V" | sed 's/^/tier3 in vhost: /' || true
systemd-analyze cat-config systemd/journald.conf | grep -E '^Max(Retention|File)Sec'
grep -c fonts.googleapis /var/www/word-addin/install.html | sed 's/^/google fonts in add-in: /' || true
