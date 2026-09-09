#!/usr/bin/env python3
"""Re-verify every statute reference in docs/claude/legal.local.md against the
live OpenCaseLaw server (network; `make smoke` class, not part of `make test`).

The playbook for Anthropic's Claude for Legal plugin states what Swiss
provisions say. Its references were retrieved with get_law on the date
recorded in docs/claude/verified-references.json; statutes change, so this
script runs the server's statute audit (the same rail attest_response uses)
over the file, section by section, and fails on any reference that no longer
resolves.

Usage:
    python scripts/check_playbook_refs.py [--base https://mcp.opencaselaw.ch] [--file PATH]

Exit codes: 0 all references resolve; 1 at least one statute issue;
2 transport or server error (quota, PII guard, network).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_FILE = REPO / "docs" / "claude" / "legal.local.md"
DEFAULT_BASE = "https://mcp.opencaselaw.ch"

# Same shape as tests/test_claude_legal_playbook.py: "Art. 100 Abs. 1 OR",
# "Art. 14 Abs. 2bis OR", "Art. 60 Abs. 1 lit. a DSG", "Art. 340a OR".
LAWS = "OR|ZGB|DSG|UWG|StGB|URG|IPRG|ZPO|KG"
ARTICLE_RE = re.compile(
    rf"Art\.\s+(\d+[a-z]?)"
    rf"(?:\s+Abs\.\s+\d+(?:bis|ter)?)?"
    rf"(?:\s+lit\.\s+[a-z])?"
    rf"\s+({LAWS})\b"
)


def sections(text: str) -> list[tuple[str, str]]:
    """Split on level-2/3 headings so each request stays small and a failure
    names the section it came from."""
    out: list[tuple[str, str]] = []
    title = "(preamble)"
    buf: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{2,3} ", line):
            if buf:
                out.append((title, "\n".join(buf)))
            title = line.lstrip("# ").strip()
            buf = []
        buf.append(line)
    if buf:
        out.append((title, "\n".join(buf)))
    return out


def attest(base: str, text: str) -> dict:
    body = json.dumps({
        "draft_text": text,
        # Only the statute rail is wanted here; the playbook's short quotes
        # are below the quote rail's 60-character floor anyway.
        "audit_quotes": False,
        "audit_grounding": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/attest",
        data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "opencaselaw-check-playbook-refs/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default=DEFAULT_BASE, help="server base URL")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help="playbook file")
    args = ap.parse_args(argv)

    text = args.file.read_text(encoding="utf-8")
    failures = 0
    submitted: set[tuple[str, str]] = set()
    for title, chunk in sections(text):
        refs = {(law, art) for art, law in ARTICLE_RE.findall(chunk)}
        if not refs:
            continue
        submitted |= refs
        try:
            result = attest(args.base, chunk)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            print(f"ERROR {exc.code} on section '{title}': {detail}", file=sys.stderr)
            return 2
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"ERROR on section '{title}': {exc}", file=sys.stderr)
            return 2
        if "error" in result:
            print(f"ERROR on section '{title}': {result['error']}", file=sys.stderr)
            return 2
        # The REST response carries no per-rail counters (the MCP tool does);
        # the statute rail reports only its failures, as issues with
        # category "statute" and keys citation / problem / suggestion.
        issues = [i for i in result.get("issues", []) if i.get("category") == "statute"]
        for issue in issues:
            failures += 1
            print(f"FAIL [{title}] {issue.get('citation')}: {issue.get('problem')} "
                  f"({issue.get('suggestion', '')[:160]})")
    print(f"statute references submitted: {len(submitted)}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
