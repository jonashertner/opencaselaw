"""The /datenschutz/ page promises that a Tier-1 entry (IP address) is gone
before it is 72 hours old. Two things make that true, and both are pinned
here (2026-11-10): logrotate must actually run hourly, and the shred must
fire on age in minutes, not in whole days."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_tier1_shred_fires_before_72_hours():
    conf = (REPO / "ops" / "logrotate" / "ocl-tier1").read_text(encoding="utf-8")
    assert "hourly" in conf
    m = re.search(r"-name 'tier1\.log-\*' -mmin \+(\d+)", conf)
    assert m, "shred must select by minutes, not -mtime days"
    # a rotated file holds the hour before its mtime; the next run may come an
    # hour late: mtime age + 1 h of content + 1 h of jitter must stay <= 72 h
    assert int(m.group(1)) + 120 <= 72 * 60


def test_logrotate_runs_tier1_hourly():
    timer = (REPO / "systemd" / "ocl-tier1-rotate.timer").read_text(encoding="utf-8")
    service = (REPO / "systemd" / "ocl-tier1-rotate.service").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer
    assert "logrotate /etc/logrotate.d/ocl-tier1" in service


def test_site_pages_load_nothing_from_third_parties():
    hosts = re.compile(r"(src|href)=\"https://(fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com)")
    bad = [str(p.relative_to(REPO)) for p in (REPO / "docs").rglob("*.html")
           if hosts.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert bad == [], bad
