#!/usr/bin/env bash
# Install opencaselaw-smoke.{service,timer} on the VPS.
#
# The service runs `python -m quality.smoke` every 5 minutes against the
# live MCP server. On non-zero exit, systemd's OnFailure drop-in fires
# an ntfy.sh alert with the failing probes.
#
# Usage:
#   ssh root@vps 'bash -s' < scripts/install_smoke_timer.sh
#
# Idempotent — safe to re-run.
set -euo pipefail

REPO=/opt/caselaw/repo
NTFY_TOPIC=opencaselaw-smoke

cat >/etc/systemd/system/opencaselaw-smoke.service <<EOF
[Unit]
Description=OpenCaseLaw production-smoke probe
OnFailure=ntfy-alert@%n.service

[Service]
Type=oneshot
WorkingDirectory=$REPO
ExecStart=/usr/bin/python3 -m quality.smoke --url https://mcp.opencaselaw.ch \\
  --output /var/log/opencaselaw-smoke/latest.json
TimeoutStartSec=30
EOF

cat >/etc/systemd/system/opencaselaw-smoke.timer <<EOF
[Unit]
Description=Run OpenCaseLaw smoke probe every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Generic ntfy alert template — reused by Step 6b health check too. Every unit
# alerts on the operator topic from ops.env; the per-instance file overrides it,
# which keeps the smoke probe's I/O-pressure failures off that channel.
mkdir -p /etc/systemd/system/ntfy-alert@.service.d /etc/opencaselaw
echo "NTFY_TOPIC=$NTFY_TOPIC" >/etc/opencaselaw/ntfy-alert-opencaselaw-smoke.service.env
cat >/etc/systemd/system/ntfy-alert@.service <<EOF
[Unit]
Description=Send ntfy alert for failure of %i

[Service]
Type=oneshot
EnvironmentFile=-/opt/caselaw/ops.env
EnvironmentFile=-/etc/opencaselaw/ntfy-alert-%i.env
ExecStart=/bin/sh -c '/usr/bin/curl -sS -m 20 -d "Service %i failed at \$\$(date -Is)" "https://ntfy.sh/\$\${NTFY_TOPIC:?NTFY_TOPIC not set}"'
EOF

mkdir -p /var/log/opencaselaw-smoke

systemctl daemon-reload
systemctl enable --now opencaselaw-smoke.timer
systemctl status opencaselaw-smoke.timer --no-pager
echo
echo "Smoke timer installed. Logs: /var/log/opencaselaw-smoke/latest.json"
echo "Alerts: ntfy.sh/$NTFY_TOPIC"
