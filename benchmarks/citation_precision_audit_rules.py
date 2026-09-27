"""Rule-based adjudicator for the 400-sample citation-precision audit set.

This is *not* a substitute for human-graded adjudication. It checks each
sample against the documented per-stratum canonicalisation rules used by
the resolver and reports whether the recovered ``target_decision_id`` is
consistent with the recovered ``target_ref`` under those rules.

Schema v2 (2026-08-07): the verdict field is named ``rule_consistent``.
The v1 name ``adjudication`` suggested semantic validation this check
does not perform (see the external review of the resource paper). The
frozen 2026-05-21 sample file is a published artifact and must NOT be
rewritten: pass --no-write-back (default when the input is the frozen
v1 file) so verdicts are reported without mutating the artifact;
readers accept both names, with ``rule_consistent`` taking precedence.

What this catches:
  - Resolver bugs (target_ref's canonical form does not match
    target_decision_id's canonical form)
  - Database-integrity drift (a pin-cite outside the documented 30-page
    range heuristic)
  - Parsing errors (target_ref that doesn't match the expected per-stratum
    grammar at all → flagged as uncertain)

What this does NOT catch:
  - Source-text typos that the resolver faithfully matched (e.g.
    "BGE 138 I 16" when the author meant "BGE 138 I 61")
  - Cases where two different decisions share a docket pattern and the
    resolver picked the wrong one (requires human semantic judgment)
  - Pin-cite resolutions that fall within the 30-page heuristic but
    actually point into the *next* case (heuristic limitation, not a
    resolver bug)

These limitations are companion-paper scope (human-graded adjudication).

The adjudicator writes its verdict back into the source JSONL's
``adjudication`` and ``notes`` fields (keeping the file format used by
the manual TUI tool) and emits aggregate per-stratum statistics with
Wilson 95% confidence intervals to ``citation_precision_audit_results.json``.

Run:
    python3 -m benchmarks.citation_precision_audit_rules \\
        --sample benchmarks/citation_precision_sample_400.jsonl \\
        --out    benchmarks/citation_precision_audit_results.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Optional


# ── Parsers ──────────────────────────────────────────────────────────

_BGE_FORM = re.compile(
    r"^\s*(?:BGE|ATF|DTF)?\s*(\d+)\s+([IVX]+[ab]?)\s+(\d+)\s*$",
    flags=re.IGNORECASE,
)
# Accept the entscheidsuche (bge_BGE_V_D_P), historical (bge_V_D_P) and direct
# scraper (bge_V D P, Ia/Ib upper-case) ID forms (#40).
_DECISION_ID_BGE = re.compile(
    r"^bge_(?:BGE_|ATF_|DTF_)?(\d+)[_ ]([IVX]+[ab]?)[_ ](\d+)$",
    flags=re.IGNORECASE,
)
_DOCKET_SEP = re.compile(r"[_/\-\.\s]+")


def _parse_bge_form(s: str) -> Optional[tuple[int, str, int]]:
    m = _BGE_FORM.match(s)
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper(), int(m.group(3))


def _parse_bge_id(decision_id: str) -> Optional[tuple[int, str, int]]:
    m = _DECISION_ID_BGE.match(decision_id)
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper(), int(m.group(3))


def _normalise_docket(s: str) -> str:
    """Collapse any run of separator chars to '_'. Case-insensitive."""
    return _DOCKET_SEP.sub("_", s.strip())


# ── Per-stratum rules ────────────────────────────────────────────────

def adjudicate_docket_norm(row: dict) -> tuple[str, str]:
    """Check that the canonical form of target_ref appears as a suffix
    of the canonical form of target_decision_id. Avoids hard-coding the
    set of court prefixes (which is open-ended across cantons and
    chambers — be_zivilstraf, vd_omni, zh_verwaltungsgericht, etc.).
    Suffix-match is the right invariant: the resolver normalises a
    citation-form docket to the underlying decision-id suffix.
    """
    target_ref = row.get("target_ref", "")
    target_id = row.get("target_decision_id", "")
    if not target_ref or not target_id:
        return "uncertain", "missing target_ref or target_decision_id"
    a = _normalise_docket(target_ref)
    b = _normalise_docket(target_id)
    if not a or not b:
        return "uncertain", "empty canonical form"
    if b.endswith("_" + a) or b == a:
        return "correct", f"docket canonical {a!r} matches target_id suffix"
    return "wrong", f"canonical mismatch: target_ref→{a!r}  target_id→{b!r}"


def adjudicate_bge_exact(row: dict) -> tuple[str, str]:
    """Bare or prefixed BGE form. Parse 'V D P' (with or without BGE/ATF/DTF
    prefix), compare with target_decision_id's bge_BGE_V_D_P."""
    target_ref = row.get("target_ref", "")
    target_id = row.get("target_decision_id", "")
    ref_parts = _parse_bge_form(target_ref)
    if ref_parts is None:
        return "uncertain", f"unparseable BGE form: {target_ref!r}"
    id_parts = _parse_bge_id(target_id)
    if id_parts is None:
        return "wrong", f"target_id not in bge_BGE_V_D_P form: {target_id!r}"
    if ref_parts == id_parts:
        return "correct", f"BGE form matches exactly ({ref_parts[0]} {ref_parts[1]} {ref_parts[2]})"
    return "wrong", f"BGE form mismatch: ref={ref_parts}  id={id_parts}"


def adjudicate_bge_pincite(row: dict, max_pincite_gap: int = 30) -> tuple[str, str]:
    """Pin-cite-aware fallback. target_ref is a BGE form whose page may
    pinpoint into the body of a case starting at an earlier first page.
    Verify: V matches, D matches, target_ref_page >= target_id_first_page,
    and (target_ref_page - target_id_first_page) <= max_pincite_gap.
    """
    target_ref = row.get("target_ref", "")
    target_id = row.get("target_decision_id", "")
    ref_parts = _parse_bge_form(target_ref)
    if ref_parts is None:
        return "uncertain", f"unparseable BGE pincite form: {target_ref!r}"
    id_parts = _parse_bge_id(target_id)
    if id_parts is None:
        return "wrong", f"target_id not in bge_BGE_V_D_P form: {target_id!r}"
    rv, rd, rp = ref_parts
    iv, id_, ip = id_parts
    if rv != iv:
        return "wrong", f"volume mismatch: ref={rv} id={iv}"
    if rd != id_:
        return "wrong", f"division mismatch: ref={rd} id={id_}"
    if rp < ip:
        return "wrong", f"pin-cite page {rp} earlier than case start {ip} (V{rv} {rd})"
    gap = rp - ip
    if gap > max_pincite_gap:
        return "wrong", f"pin-cite gap {gap} pages exceeds {max_pincite_gap}-page heuristic"
    if gap == 0:
        return "correct", f"exact BGE match (V{rv} {rd} {rp})"
    return "correct", f"pin-cite within range (gap={gap} pages)"


_ADJUDICATORS = {
    "docket_norm": adjudicate_docket_norm,
    "bge_bare": adjudicate_bge_exact,
    "bge_norm": adjudicate_bge_exact,
    "bge_pincite": adjudicate_bge_pincite,
}


# ── Wilson 95% CI on a binomial proportion ───────────────────────────

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = (centre - half) / denom
    hi = (centre + half) / denom
    return (lo, hi)


# ── Main ─────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--sample", type=Path,
        default=Path("benchmarks/citation_precision_sample_400.jsonl"),
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Results JSON. Default: the repo artifact when analysing the "
             "frozen sample, otherwise <sample>.results.json next to the "
             "sample — so a run over a working copy or a temp file cannot "
             "overwrite the committed results (it did: a test analysing a "
             "tmp copy rewrote sample_file to a pytest path, 2026-08-09).",
    )
    p.add_argument(
        "--write-back", action="store_true", default=None,
        help="Write verdicts+notes back into the sample JSONL. Default: on "
             "for working files, OFF for the frozen v1 artifact "
             "citation_precision_sample_400.jsonl (published with the "
             "2026-05-21 release; must stay byte-identical).",
    )
    p.add_argument(
        "--no-write-back", action="store_false", dest="write_back",
    )
    args = p.parse_args(argv)

    if not args.sample.exists():
        print(f"error: sample file not found: {args.sample}", file=sys.stderr)
        return 2
    _FROZEN = "citation_precision_sample_400.jsonl"
    is_frozen_repo_artifact = (
        args.sample.name == _FROZEN
        and args.sample.resolve() == Path("benchmarks", _FROZEN).resolve())
    if args.write_back is None:
        args.write_back = args.sample.name != _FROZEN
    if args.out is None:
        args.out = (Path("benchmarks/citation_precision_audit_results.json")
                    if is_frozen_repo_artifact
                    else args.sample.with_suffix(".results.json"))

    rows: list[dict] = []
    meta: Optional[dict] = None
    with args.sample.open() as f:
        for line in f:
            r = json.loads(line)
            if r.get("q_id") == "_meta":
                meta = r
                continue
            rows.append(r)

    # Adjudicate each row
    verdicts: dict[str, dict[str, int]] = {}
    enriched: list[dict] = []
    for r in rows:
        mt = r["match_type"]
        adj_fn = _ADJUDICATORS.get(mt)
        if adj_fn is None:
            verdict, note = "uncertain", f"no adjudicator for match_type {mt!r}"
        else:
            verdict, note = adj_fn(r)
        r_out = dict(r)
        r_out["rule_consistent"] = verdict
        r_out["schema_version"] = 2
        # v1 alias kept so downstream readers of enriched output keep
        # working during the transition; remove after Wave-2 retooling.
        r_out["adjudication"] = verdict
        r_out["notes"] = note
        enriched.append(r_out)
        verdicts.setdefault(mt, {"correct": 0, "wrong": 0, "uncertain": 0})
        verdicts[mt][verdict] += 1

    # Compute per-stratum aggregate stats + Wilson CIs
    per_stratum: dict[str, dict] = {}
    overall_correct = 0
    overall_wrong = 0
    overall_uncertain = 0
    overall_n = 0
    for mt in sorted(verdicts):
        v = verdicts[mt]
        n = v["correct"] + v["wrong"] + v["uncertain"]
        # Precision under "uncertain is treated as worst case (wrong)" lower
        # bound and "uncertain as best case (correct)" upper bound. Use
        # 'wrong' as the failure metric for the headline.
        successes = v["correct"]
        lo, hi = wilson_ci(successes, n)
        per_stratum[mt] = {
            "n": n,
            "correct": v["correct"],
            "wrong": v["wrong"],
            "uncertain": v["uncertain"],
            "precision_point": successes / n if n else None,
            "precision_ci95_low": lo,
            "precision_ci95_high": hi,
        }
        overall_correct += v["correct"]
        overall_wrong += v["wrong"]
        overall_uncertain += v["uncertain"]
        overall_n += n

    o_lo, o_hi = wilson_ci(overall_correct, overall_n)
    overall = {
        "n": overall_n,
        "correct": overall_correct,
        "wrong": overall_wrong,
        "uncertain": overall_uncertain,
        "precision_point": (overall_correct / overall_n) if overall_n else None,
        "precision_ci95_low": o_lo,
        "precision_ci95_high": o_hi,
    }

    result = {
        "schema": "citation_precision_audit_rules/v1",
        "method": "rule-based mechanical resolver-consistency adjudication",
        "method_notes": (
            "Checks each sample against the documented per-stratum "
            "canonicalisation rules used by the resolver. Catches resolver "
            "bugs and DB-integrity drift; does not substitute for "
            "human-graded semantic verification."
        ),
        "sample_file": str(args.sample),
        "sample_meta": meta,
        "by_stratum": per_stratum,
        "overall": overall,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}")

    # Summary to stdout
    print()
    print(f"  {'stratum':<14s}  {'n':>5s}  {'correct':>8s}  {'wrong':>6s}  "
          f"{'unc':>4s}  {'precision':>16s}")
    for mt in sorted(per_stratum):
        s = per_stratum[mt]
        pp = s["precision_point"] * 100 if s["precision_point"] is not None else 0
        lo = s["precision_ci95_low"] * 100
        hi = s["precision_ci95_high"] * 100
        print(f"  {mt:<14s}  {s['n']:>5d}  {s['correct']:>8d}  {s['wrong']:>6d}  "
              f"{s['uncertain']:>4d}  {pp:>6.2f}% [{lo:.1f},{hi:.1f}]")
    pp = overall["precision_point"] * 100 if overall["precision_point"] is not None else 0
    print(f"  {'OVERALL':<14s}  {overall['n']:>5d}  {overall['correct']:>8d}  "
          f"{overall['wrong']:>6d}  {overall['uncertain']:>4d}  "
          f"{pp:>6.2f}% "
          f"[{overall['precision_ci95_low']*100:.1f},"
          f"{overall['precision_ci95_high']*100:.1f}]")

    if args.write_back:
        # Atomic: write to tmp, replace
        tmp = args.sample.with_suffix(args.sample.suffix + ".tmp")
        with tmp.open("w") as f:
            if meta is not None:
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for r in enriched:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(args.sample)
        print(f"  → wrote adjudication back into {args.sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
