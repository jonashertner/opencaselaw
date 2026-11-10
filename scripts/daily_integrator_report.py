#!/usr/bin/env python3
"""Daily integrator detection report.

Analyzes the nginx tier-1 access log to identify:
- High-volume IPs (potential commercial integrators)
- Client types (Claude, ChatGPT, Cursor, custom bots)
- MCP tool usage patterns
- Suspicious or unusual access patterns

Log source
----------
nginx's default ``access.log`` only receives the catch-all server's traffic
(a few dozen 301s a day); every real request is written to ``tier1.log``
(``/etc/nginx/conf.d/ocl-logging.conf``, 72 h retention, rotated as
``tier1.log-YYYYMMDD-HH[.gz]``)::

    log_format tier1 '$remote_addr $time_iso8601 "$request_method $uri" '
                     '$status $request_time "$http_user_agent"';

``$uri`` carries no query string and the format logs no byte count, so the
``size`` / ``bytes_total`` fields are always 0 (kept for the report's shape).

Output: JSON report + optional ntfy.sh push notification.
Designed to run daily via systemd timer.
"""

import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

# nginx tier1 format: IP TIMESTAMP "METHOD PATH" STATUS TIME "UA"
# The path may not be empty or contain a quote; a request line nginx could not
# parse is logged as "POST " (400) and is skipped.
LOG_RE = re.compile(
    r'^(?P<ip>\S+) (?P<time>\S+) '
    r'"(?P<method>\S+) (?P<path>[^"\s]+)[^"]*" '
    r'(?P<status>\d{3}) (?P<rtime>\S+) '
    r'"(?P<ua>[^"]*)"'
)

LOG_DIR = Path("/var/log/nginx")
LOG_GLOB = "tier1.log*"  # hot file + rotated tier1.log-YYYYMMDD-HH[.gz]

# Known client signatures
CLIENT_PATTERNS = [
    ("claude.ai", re.compile(r"Claude-User|anthropic", re.I)),
    ("claude-code", re.compile(r"claude-code", re.I)),
    ("chatgpt", re.compile(r"undici|openai-mcp|ChatGPT", re.I)),
    ("cursor", re.compile(r"Cursor/", re.I)),
    ("gemini", re.compile(r"Gemini|Google-Extended", re.I)),
    ("googlebot", re.compile(r"Googlebot|GoogleOther", re.I)),
    ("bingbot", re.compile(r"bingbot|msnbot", re.I)),
    ("python-bot", re.compile(r"python-httpx|python-requests|aiohttp|urllib", re.I)),
    ("node-bot", re.compile(r"^node$|node-fetch|axios", re.I)),
    ("browser", re.compile(r"Mozilla.*(?:Chrome|Firefox|Safari)", re.I)),
]

# Paths that indicate MCP tool usage (not just crawling): the legacy SSE
# transport (/, /sse, /messages/) and the streamable-HTTP endpoint (/mcp).
MCP_PATHS = re.compile(r"^/(sse|messages/|mcp(?:/|$)|$)")
API_PATHS = re.compile(r"^/api/")
SEO_PATHS = re.compile(r"^/(entscheid/|sitemap|robots\.txt)")

# Exclude from analysis
SKIP_PATHS = {"/health", "/metrics", "/metrics/all", "/dev", "/favicon.ico"}

OUTPUT_DIR = Path("/opt/caselaw/repo/logs/integrator_reports")
PRINT_FLAGS_MAX = 20  # --print lists at most this many flagged IPs


def classify_client(ua: str) -> str:
    for name, pat in CLIENT_PATTERNS:
        if pat.search(ua):
            return name
    return "unknown"


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _may_contain_entries_since(lf: Path, since: datetime) -> bool:
    """A log file's mtime is its last write, so a file last written before
    ``since`` cannot hold entries in the window. Skips the stale rotations
    (and their gunzip) on every run."""
    try:
        return lf.stat().st_mtime >= since.timestamp()
    except OSError:
        return True


def parse_logs(log_files: Iterable[Path], since: datetime | None = None) -> Iterator[dict]:
    """Yield tier1 log entries from the given files, oldest file first as given.

    Streams: a day is ~400k lines and the report only needs per-IP aggregates.
    """
    since_tz = _as_utc(since) if since is not None else None
    for lf in log_files:
        lf = Path(lf)
        if since_tz is not None and not _may_contain_entries_since(lf, since_tz):
            continue
        opener = gzip.open if lf.suffix == ".gz" else open
        try:
            with opener(lf, "rt", errors="replace") as f:
                for line in f:
                    m = LOG_RE.match(line)
                    if not m:
                        continue
                    d = m.groupdict()
                    try:
                        ts = _as_utc(datetime.fromisoformat(d["time"]))
                    except ValueError:
                        continue
                    if since_tz is not None and ts < since_tz:
                        continue
                    d["timestamp"] = ts
                    d["status"] = int(d["status"])
                    d["size"] = 0  # tier1 logs no byte count
                    yield d
        except Exception as e:
            print(f"Warning: could not read {lf}: {e}", file=sys.stderr)


def analyze(entries: Iterable[dict]) -> dict:
    """Produce integrator analysis report."""
    # Per-IP stats
    ip_stats = defaultdict(lambda: {
        "requests": 0, "mcp_requests": 0, "api_requests": 0,
        "seo_requests": 0, "user_agents": Counter(), "paths": Counter(),
        "methods": Counter(), "first_seen": None, "last_seen": None,
        "status_codes": Counter(), "bytes": 0,
    })

    # Global stats
    total = 0
    first_ts = last_ts = None
    client_counts = Counter()
    tool_calls = Counter()  # MCP tool call paths
    hourly = Counter()

    for e in entries:
        total += 1
        ts = e["timestamp"]
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts

        ip = e["ip"]
        path = e["path"]
        ua = e["ua"]
        client = classify_client(ua)

        # Skip noise
        if any(path.startswith(sp) for sp in SKIP_PATHS):
            continue

        s = ip_stats[ip]
        s["requests"] += 1
        s["user_agents"][ua] += 1
        s["paths"][path] += 1
        s["methods"][e["method"]] += 1
        s["status_codes"][str(e["status"])] += 1
        s["bytes"] += e["size"]

        if s["first_seen"] is None or ts < s["first_seen"]:
            s["first_seen"] = ts
        if s["last_seen"] is None or ts > s["last_seen"]:
            s["last_seen"] = ts

        if MCP_PATHS.match(path):
            s["mcp_requests"] += 1
        if API_PATHS.match(path):
            s["api_requests"] += 1
        if SEO_PATHS.match(path):
            s["seo_requests"] += 1

        client_counts[client] += 1
        hourly[ts.strftime("%Y-%m-%d %H:00")] += 1

        # Track MCP message paths (tool invocations)
        if "/messages/" in path:
            tool_calls[path] += 1

    if not total:
        return {"error": "no log entries found", "generated": datetime.now(timezone.utc).isoformat()}

    # Identify high-volume non-bot IPs (potential integrators)
    integrator_candidates = []
    for ip, s in sorted(ip_stats.items(), key=lambda x: -x[1]["requests"]):
        if s["requests"] < 10:
            continue
        top_ua = s["user_agents"].most_common(1)[0][0] if s["user_agents"] else ""
        client = classify_client(top_ua)

        # Skip known crawlers
        if client in ("googlebot", "bingbot"):
            continue

        duration_h = 0
        if s["first_seen"] and s["last_seen"]:
            duration_h = (s["last_seen"] - s["first_seen"]).total_seconds() / 3600

        integrator_candidates.append({
            "ip": ip,
            "requests": s["requests"],
            "mcp_requests": s["mcp_requests"],
            "api_requests": s["api_requests"],
            "client_type": client,
            "top_user_agent": top_ua[:200],
            "unique_paths": len(s["paths"]),
            "duration_hours": round(duration_h, 1),
            "first_seen": s["first_seen"].isoformat() if s["first_seen"] else None,
            "last_seen": s["last_seen"].isoformat() if s["last_seen"] else None,
            "bytes_total": s["bytes"],
            "is_high_volume": s["requests"] >= 50,
            "is_mcp_user": s["mcp_requests"] >= 5,
            "is_programmatic": client in ("python-bot", "node-bot", "unknown") and s["mcp_requests"] > 0,
        })

    # Flag likely commercial integrators
    commercial_flags = []
    for c in integrator_candidates:
        reasons = []
        if c["requests"] >= 100:
            reasons.append(f"high volume ({c['requests']} requests)")
        if c["is_programmatic"]:
            reasons.append(f"programmatic client ({c['client_type']})")
        if c["mcp_requests"] >= 20:
            reasons.append(f"heavy MCP usage ({c['mcp_requests']} tool calls)")
        if c["duration_hours"] >= 8:
            reasons.append(f"sustained access ({c['duration_hours']}h)")
        if reasons:
            commercial_flags.append({**c, "flags": reasons})

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "period": {
            "entries_analyzed": total,
            "first": first_ts.isoformat() if first_ts else None,
            "last": last_ts.isoformat() if last_ts else None,
        },
        "summary": {
            "total_requests": total,
            "unique_ips": len(ip_stats),
            "client_breakdown": dict(client_counts.most_common()),
            "mcp_sessions": sum(1 for s in ip_stats.values() if s["mcp_requests"] > 0),
        },
        "integrator_candidates": sorted(
            integrator_candidates, key=lambda x: -x["requests"]
        )[:30],
        "commercial_flags": commercial_flags,
        "hourly_volume": dict(sorted(hourly.items())[-48:]),  # last 48 hours
    }


def generate_alert(report: dict) -> str | None:
    """Generate alert text if commercial integrators detected."""
    flags = report.get("commercial_flags", [])
    if not flags:
        return None

    # No address in the alert, ever (2026-11-10): until then the top five
    # flagged IPs went daily to the public ntfy.sh topic "opencaselaw",
    # readable by anyone who knew the name. The alert says that something
    # was flagged and how much; the addresses stay on the server, in the
    # journal (--print) and the JSON report.
    lines = [f"🔍 {len(flags)} potential commercial integrator(s) detected:\n"]
    for n, f in enumerate(flags[:5], 1):
        lines.append(f"  #{n} — {f['requests']} req, {f['client_type']}")
        for reason in f["flags"]:
            lines.append(f"    → {reason}")
    lines.append("\nDetails on the server: journalctl -u opencaselaw-integrator-report")
    return "\n".join(lines)


def notify(title: str, message: str) -> bool:
    """Send the ntfy alert to the operator topic. Returns whether it was sent.

    The topic comes from the environment (NTFY_TOPIC, set by the ntfy.conf
    drop-in from /opt/caselaw/ops.env), never from the code: an ntfy.sh
    topic is open to anyone who knows its name. Unset, nothing is sent.
    """
    import os
    import urllib.request
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return False
    base = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/{topic}",
            data=message.encode(),
            headers={"Title": title, "Priority": "default"},
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def print_summary(report: dict) -> None:
    if "error" in report or "summary" not in report:
        print(f"\n[integrator_report] No data: {report.get('error', 'summary missing')}")
        return
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"Period: {report['period']['first']} → {report['period']['last']}")
    print(f"Total requests: {s['total_requests']}, Unique IPs: {s['unique_ips']}")
    print(f"MCP sessions: {s['mcp_sessions']}")
    print("\nClient breakdown:")
    for client, count in s["client_breakdown"].items():
        print(f"  {client}: {count}")

    candidates = report.get("integrator_candidates", [])
    print(f"\nIntegrator candidates: {len(candidates)} (top 10 by volume)")
    for c in candidates[:10]:
        print(
            f"  {c['ip']} — {c['requests']} req, {c['mcp_requests']} mcp, "
            f"{c['api_requests']} api, {c['duration_hours']}h ({c['client_type']})"
        )

    flags = report.get("commercial_flags", [])
    if flags:
        # A real day flags hundreds of IPs; the JSON keeps them all, the
        # journal gets the top of the list.
        print(f"\n⚠️  Commercial integrator candidates ({len(flags)}, top {PRINT_FLAGS_MAX} by volume):")
        for f in flags[:PRINT_FLAGS_MAX]:
            print(f"  {f['ip']} — {f['requests']} req ({f['client_type']})")
            for r in f["flags"]:
                print(f"    → {r}")
        if len(flags) > PRINT_FLAGS_MAX:
            print(f"  ... and {len(flags) - PRINT_FLAGS_MAX} more in the JSON report")
    else:
        print("\nNo commercial integrators flagged.")


def main(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(description="Daily integrator detection report")
    parser.add_argument("--days", type=int, default=1, help="Days of logs to analyze")
    parser.add_argument("--notify", action="store_true", help="Send ntfy alert if commercial use detected")
    parser.add_argument("--output", type=Path, help="Output JSON file (default: auto-dated in logs/)")
    parser.add_argument("--print", action="store_true", dest="print_report", help="Print summary to stdout")
    parser.add_argument("--log-dir", type=Path, default=LOG_DIR,
                        help=f"Directory holding {LOG_GLOB} (default: {LOG_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze and print only: write no report file, send no notification")
    args = parser.parse_args(argv)

    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    # Find log files
    log_files = sorted(args.log_dir.glob(LOG_GLOB))
    if not log_files:
        print(f"Warning: no {LOG_GLOB} files in {args.log_dir}", file=sys.stderr)

    report = analyze(parse_logs(log_files, since=since))

    # Save report
    if args.dry_run:
        print("[dry-run] report not written, no notification sent")
    else:
        out_path = args.output or OUTPUT_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Report saved to {out_path}")

    # Print summary
    if args.print_report:
        print_summary(report)

    # Send alert
    if args.notify:
        alert = generate_alert(report)
        if alert and args.dry_run:
            print(f"\n[dry-run] alert that would be sent:\n{alert}")
        elif alert:
            if notify("OpenCaseLaw Integrator Alert", alert):
                print("Alert sent via ntfy")
            else:
                print("Alert not sent (NTFY_TOPIC unset or send failed)")


if __name__ == "__main__":
    main()
