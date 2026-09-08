#!/usr/bin/env python3
"""
debug_suite.py — Full debugging sweep over the project.

Runs checks locally (code quality, tests, secrets, git hygiene) plus
server-side checks (systemd, nginx, DB integrity, recent errors).

Report is written to docs/reports/YYYY-MM-DD-debug-suite.md and printed.
Exits 0 if no HIGH-priority findings; 1 otherwise.

Usage:
    python3 scripts/debug_suite.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
SSH = ["ssh", "-i", str(Path.home() / ".ssh" / "caselaw"),
       "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
       "root@46.225.212.40"]


@dataclass
class Finding:
    severity: str           # HIGH / MED / LOW / INFO
    category: str
    summary: str
    detail: str = ""

@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    sections: list[tuple[str, str]] = field(default_factory=list)
    def add(self, f: Finding):
        self.findings.append(f)
    def section(self, name: str, text: str):
        self.sections.append((name, text))


def run(cmd, cwd=None, check=False, timeout=60):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                        timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} failed: {r.stderr[:200]}")
    return r


def ssh_run(cmd: str, timeout=60):
    return run(SSH + [cmd], timeout=timeout)


# ── 1. Local: Python syntax & imports ────────────────────────
def check_python_syntax(rep: Report):
    print("→ Python syntax & import check...")
    hot_files = [
        "mcp_server.py", "publish.py", "run_scraper.py",
        "run_all_scrapers.py", "generate_stats.py", "scrape_cantonal_laws.py",
    ]
    issues = []
    for f in hot_files:
        p = REPO / f
        if not p.exists():
            continue
        r = run([sys.executable, "-c", f"import ast; ast.parse(open('{p}').read())"])
        if r.returncode != 0:
            issues.append(f"{f}: {r.stderr.strip()[:200]}")
    rep.section("Python syntax", "\n".join(f"  ✅ {f}" for f in hot_files if (REPO / f).exists())
                                 + ("\n" + "\n".join(f"  ❌ {i}" for i in issues) if issues else ""))
    if issues:
        for i in issues:
            rep.add(Finding("HIGH", "syntax", i))


# ── 2. Local: ruff lint (if available) ───────────────────────
def check_lint(rep: Report):
    print("→ Lint check...")
    r = run(["ruff", "check", "--select=E9,F63,F7,F82,F401",
             "--exclude=.claude,.superpowers,artifacts,benchmarks,web_ui/node_modules,web_ui/dist",
             "."], cwd=str(REPO), timeout=60)
    if r.returncode == 127 or "No such file" in r.stderr:
        rep.section("Lint", "  (ruff not installed — skipped)")
        return
    errors = r.stdout.strip() or "(clean)"
    rep.section("Lint (ruff, critical checks)", errors[:3000])
    if r.returncode != 0 and "error" in r.stdout.lower():
        rep.add(Finding("MED", "lint", "ruff found issues", errors[:500]))


# ── 3. Local: tests ─────────────────────────────────────────
def check_tests(rep: Report):
    print("→ Running tests (full suite — allow a few minutes)...")
    # Don't set --timeout: a few tests legitimately take 10-30s. Don't set -x
    # either — we want to see all failures, not stop at first.
    r = run([sys.executable, "-m", "pytest", "--tb=no", "-q",
             "--ignore=web_ui"],
            cwd=str(REPO), timeout=600)
    output = r.stdout[-2000:] if len(r.stdout) > 2000 else r.stdout
    rep.section("Tests (pytest)", output or r.stderr[-800:])
    if r.returncode != 0 and "no tests" not in r.stdout.lower():
        lines = [l for l in r.stdout.split("\n") if "FAILED" in l][:5]
        rep.add(Finding("HIGH", "tests", f"pytest returned {r.returncode}",
                        "\n".join(lines) if lines else r.stdout[-600:]))


# ── 4. Local: secret scan ────────────────────────────────────
def check_secrets(rep: Report):
    print("→ Secret scan...")
    # Grep for API key patterns in tracked files (NOT .env.*)
    patterns = [
        r"sk-ant-api03-[A-Za-z0-9_-]{20,}",
        r"sk-[A-Za-z0-9]{40,}",
        r"AKIA[A-Z0-9]{16}",
        r"ghp_[A-Za-z0-9]{30,}",
        r"hf_[A-Za-z0-9]{30,}",
    ]
    tracked = run(["git", "ls-files"], cwd=str(REPO), timeout=30).stdout.splitlines()
    hits = []
    for path in tracked:
        if path.startswith((".env", "docs/", "benchmarks/")):
            continue
        if any(path.endswith(ext) for ext in (".png", ".pdf", ".gz", ".db", ".parquet", ".woff2")):
            continue
        p = REPO / path
        if not p.exists() or p.stat().st_size > 2_000_000:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in patterns:
            m = re.search(pat, content)
            if m:
                hits.append(f"{path}: {m.group()[:20]}...")
                break
    if hits:
        for h in hits:
            rep.add(Finding("HIGH", "secrets", h))
        rep.section("Secrets", "\n".join(f"  ❌ {h}" for h in hits))
    else:
        rep.section("Secrets", "  ✅ No key patterns found in tracked files")


# ── 5. Local: git hygiene ────────────────────────────────────
def check_git(rep: Report):
    print("→ Git hygiene...")
    status = run(["git", "status", "--porcelain"], cwd=str(REPO)).stdout
    modified = [l for l in status.splitlines() if l.startswith(" M")]
    untracked_src = [l for l in status.splitlines()
                     if l.startswith("??") and l.endswith(".py")]
    log_ahead = run(["git", "log", "--oneline", "origin/main..HEAD"],
                     cwd=str(REPO)).stdout.splitlines()
    rep.section("Git", f"""  Modified:        {len(modified)}
  Untracked .py:   {len(untracked_src)}
  Ahead of origin: {len(log_ahead)} commits""")
    if log_ahead:
        rep.add(Finding("LOW", "git", f"{len(log_ahead)} commits not pushed",
                        "\n".join(log_ahead[:5])))
    if untracked_src:
        rep.add(Finding("LOW", "git",
                         f"{len(untracked_src)} untracked .py files (probes/scripts?)",
                         "\n".join(l[3:] for l in untracked_src[:10])))


# ── 6. Server: systemd timers & services ─────────────────────
def check_systemd(rep: Report):
    print("→ Server systemd...")
    r = ssh_run("systemctl list-timers 'opencaselaw-*' --no-pager")
    timers = r.stdout

    r2 = ssh_run("systemctl list-units --failed --no-pager --no-legend")
    failed = r2.stdout.strip()

    r3 = ssh_run("systemctl is-active mcp-server@{8770..8777}")
    workers = r3.stdout.strip().split("\n")

    rep.section("Systemd",
                f"Timers:\n{timers}\nFailed units: {failed or '(none)'}\n"
                f"MCP workers: {', '.join(workers)}")

    if failed:
        # Ignore known-OK failures if any
        rep.add(Finding("MED", "systemd", "Failed systemd units present", failed))
    if any(w != "active" for w in workers):
        rep.add(Finding("HIGH", "systemd", f"MCP worker(s) not active: {workers}"))


# ── 7. Server: nginx config test ─────────────────────────────
def check_nginx(rep: Report):
    print("→ Server nginx...")
    r = ssh_run("nginx -t 2>&1")
    rep.section("Nginx", r.stdout.strip() + (r.stderr.strip() if r.stderr else ""))
    if "test is successful" not in (r.stdout + r.stderr):
        rep.add(Finding("HIGH", "nginx", "nginx -t failed",
                        r.stdout + r.stderr))


# ── 8. Server: disk & memory ─────────────────────────────────
def check_disk_mem(rep: Report):
    print("→ Server disk & memory...")
    r = ssh_run("df -h /mnt/HC_Volume_104655575 /; free -h | head -3; uptime")
    out = r.stdout
    rep.section("Disk / Memory / Load", out)

    # Parse disk usage
    for line in out.splitlines():
        if line.startswith("/dev/"):
            parts = line.split()
            if len(parts) >= 5:
                pct = parts[4].rstrip("%")
                try:
                    p = int(pct)
                    if p >= 90:
                        rep.add(Finding("HIGH", "disk",
                                         f"{parts[-1]} at {p}% used"))
                    elif p >= 80:
                        rep.add(Finding("MED", "disk",
                                         f"{parts[-1]} at {p}% used"))
                except ValueError:
                    pass


# ── 9. Server: DB integrity ──────────────────────────────────
def check_db(rep: Report):
    print("→ Server DB integrity (cheap checks only — quick_check is O(n) on 62GB DB)...")
    # Skip PRAGMA quick_check on the big decisions.db (62 GB, >60s to run).
    # Open the DB, run one SELECT to confirm readability + a simple stat.
    script = r"""
python3 -c "
import sqlite3, os
DBS = {
    'decisions':     '/mnt/HC_Volume_104655575/output/decisions.db',
    'reference_graph': '/mnt/HC_Volume_104655575/output/reference_graph.db',
    'statutes':      '/mnt/HC_Volume_104655575/output/statutes.db',
    'cantonal_laws': '/mnt/HC_Volume_104655575/output/cantonal_laws.db',
    'ok_commentaries': '/mnt/HC_Volume_104655575/output/ok_commentaries.db',
}
for name, path in DBS.items():
    try:
        size_mb = os.path.getsize(path) / 1024 / 1024
        conn = sqlite3.connect(f'file:{path}?immutable=1', uri=True, timeout=3)
        tbls = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
        conn.close()
        print(f'{name}: {size_mb:.0f}MB, {len(tbls)} tables, readable=ok')
    except Exception as e:
        print(f'{name}: ERROR {e}')
"
"""
    r = ssh_run(script, timeout=30)
    rep.section("DB integrity", r.stdout.strip())
    for line in r.stdout.splitlines():
        if "ERROR" in line:
            rep.add(Finding("HIGH", "db", line))


# ── 10. Server: recent errors in logs ────────────────────────
def check_logs(rep: Report):
    print("→ Server recent errors...")
    # Journalctl scan for errors in last 24h on key services
    r = ssh_run(
        "journalctl -u 'opencaselaw-*' -u 'mcp-server@*' "
        "--since '24 hours ago' --priority=err --no-pager | head -40"
    )
    errs = r.stdout.strip()
    rep.section("Recent systemd errors (24h)", errs or "  (none)")
    if errs:
        lines = errs.splitlines()
        # Filter false-positives (timeout is expected before fix, publish=OK now)
        real_errors = [l for l in lines if "Failed with result" in l
                        or "Traceback" in l or "Error" in l][:10]
        if real_errors:
            rep.add(Finding("MED", "logs",
                             f"{len(real_errors)} error-level journal entries",
                             "\n".join(real_errors[:5])))

    # Nginx error log
    r2 = ssh_run("tail -50 /var/log/nginx/error.log | grep -iE 'crit|alert|emerg' | head -20")
    nginx_crit = r2.stdout.strip()
    if nginx_crit:
        rep.add(Finding("HIGH", "logs", "Critical nginx errors in recent log",
                        nginx_crit))


# ── 11. Server: scraper loadability ──────────────────────────
def check_scraper_imports(rep: Report):
    print("→ Scraper registry loadability...")
    script = r"""
cd /opt/caselaw/repo && python3 -c "
import importlib
from run_scraper import SCRAPERS
issues = []
for key, (mod_name, cls_name) in SCRAPERS.items():
    try:
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
    except Exception as e:
        issues.append(f'{key} ({mod_name}.{cls_name}): {type(e).__name__}: {e}')
print(f'{len(SCRAPERS)} scrapers in registry')
if issues:
    print('ISSUES:')
    for i in issues:
        print(f'  {i}')
else:
    print('All import cleanly.')
"
"""
    r = ssh_run(script)
    rep.section("Scraper registry", r.stdout.strip())
    if "ISSUES:" in r.stdout:
        rep.add(Finding("HIGH", "scrapers", "Scraper class(es) fail to import",
                        r.stdout))


# ── 12. Server: MCP tool listing ─────────────────────────────
def check_mcp_tools(rep: Report):
    print("→ MCP tool listing...")
    script = r"""
python3 -c "
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
async def check():
    async with sse_client('http://localhost:8770/sse') as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f'{len(tools.tools)} tools advertised')
            for t in tools.tools:
                # Validate schema has required properties defined
                schema = t.inputSchema or {}
                req = schema.get('required', [])
                props = list((schema.get('properties') or {}).keys())
                missing = [r for r in req if r not in props]
                if missing:
                    print(f'  BAD {t.name}: required not in properties: {missing}')
asyncio.run(check())
"
"""
    r = ssh_run(script)
    rep.section("MCP tool schemas", r.stdout.strip())
    if "BAD " in r.stdout:
        rep.add(Finding("HIGH", "mcp",
                         "MCP tool has malformed JSON schema", r.stdout))


# ── 13. Dependencies audit ───────────────────────────────────
def check_deps(rep: Report):
    print("→ Dependency audit...")
    r = run([sys.executable, "-m", "pip", "list", "--outdated",
             "--format=json"], timeout=60)
    try:
        outdated = json.loads(r.stdout) if r.stdout else []
    except json.JSONDecodeError:
        outdated = []
    # Filter: only ones in pyproject.toml
    pyproj = (REPO / "pyproject.toml").read_text()
    critical = [d for d in outdated if d["name"].lower() in pyproj.lower()]
    text = (f"  {len(outdated)} outdated packages ({len(critical)} in pyproject.toml)\n"
            + "\n".join(f"  - {d['name']} {d['version']} → {d['latest_version']}"
                        for d in critical[:15]))
    rep.section("Dependencies", text)


# ── Entry ────────────────────────────────────────────────────
def main():
    start = time.time()
    rep = Report()

    checks = [
        ("Python syntax",          check_python_syntax),
        ("Lint",                    check_lint),
        ("Tests",                   check_tests),
        ("Secrets",                 check_secrets),
        ("Git hygiene",             check_git),
        ("Dependencies",            check_deps),
        ("Systemd",                 check_systemd),
        ("Nginx",                   check_nginx),
        ("Disk & memory",           check_disk_mem),
        ("DB integrity",            check_db),
        ("Recent log errors",       check_logs),
        ("Scraper imports",         check_scraper_imports),
        ("MCP tool schemas",        check_mcp_tools),
    ]
    for name, fn in checks:
        try:
            fn(rep)
        except Exception as e:
            rep.add(Finding("MED", "suite", f"Check '{name}' errored: {type(e).__name__}: {e}"))

    # ── Print report ──
    sev_order = {"HIGH": 0, "MED": 1, "LOW": 2, "INFO": 3}
    rep.findings.sort(key=lambda f: sev_order.get(f.severity, 9))
    high = [f for f in rep.findings if f.severity == "HIGH"]
    med = [f for f in rep.findings if f.severity == "MED"]
    low = [f for f in rep.findings if f.severity == "LOW"]

    print(f"\n{'=' * 70}\n  DEBUG SUITE RESULTS — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n{'=' * 70}")
    print(f"  HIGH: {len(high)}   MED: {len(med)}   LOW: {len(low)}   "
           f"(ran in {time.time()-start:.0f}s)")
    for f in rep.findings:
        print(f"\n  [{f.severity}] ({f.category}) {f.summary}")
        if f.detail:
            for line in f.detail.splitlines()[:3]:
                print(f"        {line[:120]}")

    # ── Write Markdown report ──
    out = REPO / "docs" / "reports" / f"{datetime.now(timezone.utc):%Y-%m-%d}-debug-suite.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    md = [f"# Debug Suite Report — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n"]
    md.append(f"**Summary:** HIGH={len(high)}, MED={len(med)}, LOW={len(low)}")
    md.append(f"  runtime {time.time()-start:.0f}s\n")
    md.append("## Findings (by severity)\n")
    if not rep.findings:
        md.append("✅ **No findings** — all checks clean.\n")
    for f in rep.findings:
        md.append(f"### [{f.severity}] ({f.category}) {f.summary}")
        if f.detail:
            md.append(f"```\n{f.detail}\n```")
        md.append("")
    md.append("\n## Check outputs\n")
    for name, text in rep.sections:
        md.append(f"### {name}\n```\n{text}\n```\n")
    out.write_text("\n".join(md))
    print(f"\nReport written to: {out.relative_to(REPO)}")

    sys.exit(1 if high else 0)


if __name__ == "__main__":
    main()
