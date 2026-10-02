#!/usr/bin/env python3
"""Rewrite docs/index.html's static fallback numbers from docs/stats.json.

The homepage hydrates its figures client-side from stats.json, which the
nightly rebuilds. But the HTML also carries hardcoded fallback values —
what every non-JS consumer sees, and crawlers are ~76 % of traffic. Found
2026-08-22 at two months' drift: 991'298 decisions rendered against a live
1'054'206, under a tagline that says "rebuilt every day". Nothing regenerated
them; this script exists so nothing has to be remembered.

Mirrors the hydration JS exactly (same ids, same formatting):
  #bignum, #f-decisions   ← total                (apostrophe thousands)
  #f-laws                 ← corpus.federal_laws + corpus.cantonal_laws
  #f-cites                ← corpus.citation_edges (one-decimal M)
  #f-echr                 ← by_court sum over the ECtHR courts
  #cov-courts             ← court_count
  #trust-delta            ← delta.total
  #trust-date             ← generated_at (YYYY-MM-DD)

Strict by design: any id it cannot find, or any implausible value, is a
non-zero exit with nothing written. The caller (publish.py step 6) treats
that as WARN, not fatal — a one-day-stale homepage is cosmetic, a failed
publish is not.

Usage:
    python3 scripts/sync_homepage_fallbacks.py            # rewrite in place
    python3 scripts/sync_homepage_fallbacks.py --check    # verify only, no write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "docs" / "index.html"
STATS = REPO / "docs" / "stats.json"

# The file encodes the typographic apostrophe as an entity; match it.
APOS = "&#8217;"


def fmt_thousands(n: int) -> str:
    """1054206 -> 1&#8217;054&#8217;206 — the entity form of the JS fmt()."""
    return f"{n:,}".replace(",", APOS)


def fmt_millions(n: int) -> str:
    """9836225 -> 9.8M — one decimal, matching the shipped '8.9M' style."""
    return f"{n / 1e6:.1f}M"


def build_replacements(stats: dict) -> list[tuple[str, str, re.Pattern, str]]:
    """(label, expected_new_text, pattern, replacement) per element.

    Patterns anchor on the id attribute and replace only the text node before
    the closing tag or inner <span>, so surrounding markup survives verbatim.
    """
    total = int(stats["total"])
    laws = int(stats["corpus"]["federal_laws"]) + int(stats["corpus"]["cantonal_laws"])
    cites = int(stats["corpus"]["citation_edges"])
    courts = int(stats["court_count"])
    delta = int((stats.get("delta") or {}).get("total") or 0)
    gen_date = str(stats["generated_at"])[:10]

    # ECtHR strand — summed from by_court exactly as the hydration JS does,
    # NOT from interesting_stats.echr_switzerland (weekly cadence, and its
    # total counts all 46 respondent states despite the name). by_court
    # splits each ECtHR court by canton ('CE' Council-of-Europe-wide, 'CH'
    # Swiss-respondent), so every row counts, and a court that starts
    # ingesting (ecthr_committee: defined, zero rows today) appears on its own.
    echr = sum(
        int(r.get("count") or 0)
        for r in (stats.get("by_court") or [])
        if str(r.get("court", "")).startswith("ecthr_")
        or r.get("court") in ("hudoc_ch", "bge_egmr")
    )

    # Sanity floor: refuse to write numbers that would themselves embarrass.
    if not (950_000 < total < 5_000_000):
        raise SystemExit(f"implausible total {total}; refusing")
    if not (15_000 < laws < 100_000):
        raise SystemExit(f"implausible laws {laws}; refusing")
    if not (1_000_000 < cites < 100_000_000):
        raise SystemExit(f"implausible citation_edges {cites}; refusing")
    if not (50 < courts < 500):
        raise SystemExit(f"implausible court_count {courts}; refusing")

    def text_then_close(elem_id: str) -> re.Pattern:
        return re.compile(rf'(id="{elem_id}"[^>]*>)[^<]*(</)')

    def text_then_span(elem_id: str) -> re.Pattern:
        return re.compile(rf'(id="{elem_id}"[^>]*>)[^<]*(<span)')

    rows = [
        ("bignum", fmt_thousands(total),
         text_then_close("bignum"), rf"\g<1>{fmt_thousands(total)}\g<2>"),
        ("f-decisions", fmt_thousands(total),
         text_then_span("f-decisions"), rf"\g<1>{fmt_thousands(total)} \g<2>"),
        ("f-laws", fmt_thousands(laws),
         text_then_span("f-laws"), rf"\g<1>{fmt_thousands(laws)} \g<2>"),
        ("f-cites", fmt_millions(cites),
         text_then_span("f-cites"), rf"\g<1>{fmt_millions(cites)} \g<2>"),
        ("cov-courts", str(courts),
         text_then_close("cov-courts"), rf"\g<1>{courts}\g<2>"),
        ("trust-delta", str(delta),
         text_then_close("trust-delta"), rf"\g<1>{delta}\g<2>"),
        ("trust-date", gen_date,
         text_then_close("trust-date"), rf"\g<1>{gen_date}\g<2>"),
    ]

    # Redesign 2026-08-29: three more no-JS fallbacks. Same strictness as the
    # core rows — the ids exist in the redesigned page; if a future edit drops
    # one, the non-zero exit surfaces it as the nightly WARN.
    schol = int(((stats.get("corpus") or {}).get("scholarship_publications")) or 0)
    if 10_000 < schol < 500_000:
        rows.append(("f-scholarship", fmt_thousands(schol),
                     text_then_close("f-scholarship"),
                     rf"\g<1>{fmt_thousands(schol)}\g<2>"))
    else:
        print(f"WARNING: implausible scholarship_publications {schol} — "
              f"leaving #f-scholarship, syncing the rest", file=sys.stderr)
    # "new today" carries its own sign (+229, −2409, bare 0); the sign sits in
    # front of the span, so the pattern takes the whole .d-n box. A fixed "+"
    # in the markup read "+-2409" on the night of a clean-up.
    sign = "+" if delta > 0 else "\u2212" if delta < 0 else ""
    rows.append(("d-today", f'{sign}<span id="d-today">{abs(delta)}',
                 re.compile(r'(class="d-n[^"]*"[^>]*>)[^<]*(<span id="d-today">)[^<]*(</)'),
                 rf"\g<1>{sign}\g<2>{abs(delta)}\g<3>"))
    rows.append(("stamp", gen_date,
                 text_then_close("stamp"), rf"\g<1>{gen_date}\g<2>"))

    # f-echr degrades instead of blocking. Every other value above reads a
    # stable top-level key; this one sums a list filtered by court-code
    # prefix, so a renamed code or an aggregation that starts excluding the
    # ECtHR courts would zero it. Refusing the whole run over that would
    # leave the decisions count unrepaired — which is the drift that
    # actually bit on 2026-08-22 (991'298 shown against a live 1'054'206).
    # A stale ECtHR number is the smaller harm than a stale headline, so
    # skip only this element and let the rest sync.
    if 5_000 < echr < 100_000:
        rows.append(
            ("f-echr", fmt_thousands(echr),
             text_then_span("f-echr"), rf"\g<1>{fmt_thousands(echr)} \g<2>")
        )
    else:
        print(f"WARNING: implausible ECtHR total {echr} from by_court — "
              f"leaving #f-echr at its current value, syncing the rest",
              file=sys.stderr)

    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify the HTML already matches stats.json; write nothing")
    ap.add_argument("--index", type=Path, default=INDEX)
    ap.add_argument("--stats", type=Path, default=STATS)
    args = ap.parse_args()

    stats = json.loads(args.stats.read_text(encoding="utf-8"))
    html = args.index.read_text(encoding="utf-8")

    changed: list[str] = []
    stale: list[str] = []
    for label, new_text, pattern, replacement in build_replacements(stats):
        m = pattern.search(html)
        if not m:
            print(f"ERROR: element #{label} not found — homepage markup changed; "
                  f"update this script's patterns", file=sys.stderr)
            return 1
        current = html[m.start(): m.end()]
        already = new_text in current
        if args.check:
            if not already:
                stale.append(f"#{label}: expected {new_text!r} in {current!r}")
            continue
        new_html, n = pattern.subn(replacement, html, count=1)
        if n and new_html != html:
            html = new_html
            changed.append(f"#{label} -> {new_text}")

    if args.check:
        if stale:
            print("STALE:\n  " + "\n  ".join(stale))
            return 1
        print("homepage fallbacks match stats.json")
        return 0

    if changed:
        args.index.write_text(html, encoding="utf-8")
        print("updated: " + ", ".join(changed))
    else:
        print("already current — nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
