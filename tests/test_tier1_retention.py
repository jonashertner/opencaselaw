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
    pages = list((REPO / "docs").rglob("*.html")) + list((REPO / "tools" / "word-addin").glob("*.html"))
    bad = [str(p.relative_to(REPO)) for p in pages
           if hosts.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert bad == [], bad


def test_word_addin_fonts_resolve_locally():
    css = (REPO / "tools" / "word-addin" / "css" / "fonts.css").read_text(encoding="utf-8")
    files = re.findall(r"url\(\.\./fonts/([^)]+)\)", css)
    assert files and all((REPO / "tools" / "word-addin" / "fonts" / f).is_file() for f in files)


def test_nginx_keeps_no_ip_derived_label_or_long_lived_user_agent():
    conf = (REPO / "ops" / "nginx" / "ocl-logging.conf").read_text(encoding="utf-8")
    code = "\n".join(l for l in conf.splitlines() if not l.lstrip().startswith("#"))
    assert "log_format tier3" not in code and "$ocl_institution" not in code
    legacy = code[code.index("log_format legacy8765"):].split(";", 1)[0]
    assert "$http_user_agent" not in legacy and "$remote_addr" not in legacy


def test_journal_retention_is_bounded_by_time():
    conf = (REPO / "ops" / "journald" / "ocl-retention.conf").read_text(encoding="utf-8")
    days = int(re.search(r"^MaxRetentionSec=(\d+)day$", conf, re.M).group(1))
    assert re.search(r"^MaxFileSec=1day$", conf, re.M)
    assert days + 2 <= 14  # active file + retention + vacuum delay
