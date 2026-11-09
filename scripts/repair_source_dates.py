#!/usr/bin/env python3
"""Give held rows the decision date their source states (comparison report
2026-11-08, item #5; runbooks/source_dates_repair_2026-11.md).

One court per run. Its direct shard and its frozen entscheidsuche shard go
together: a twin repaired on one side only comes back as a second row under
another date (the Tribuna and Vaud lesson). The rules live in source_dates.py,
which the scrapers use for new rows, so a repaired row and a newly scraped one
get the same date from the same document:

  bge_egmr      the docket BGer mints from the judgment date (YYYYMMDD_appno_yy)
  gr_gerichte   PKG/PVG/POG entries: the trailer that closes the entry; the
                federation stub of an entry takes its direct twin's (same PDF);
                other undated rows: the "Urteil vom" head
  ti_gerichte   undated rows: the portal's "Data decisione" (--evidence, a
                polite refetch of each row's own source_url, see
                scripts/fetch_ti_decision_dates.py)
  ar_gerichte   AR GVP entries: the decision date in the official file name
  ur_gerichte   the Rechenschaftsbericht line "Obergericht, <date>, <docket>",
                else the head "Entscheid vom <date>"
  nw_gerichte   the head "Entscheid/Urteil/Beschluss vom <date>"
  bl_gerichte   the date of the baselland.ch slug, the Steuergericht trailer
                "Entscheid Nr. 61/2003 vom 4.7.2003", the KG head
  be_zivilstraf the letterhead "Bern, 5. Februar 2026" (head and signature)

A rule either only FILLS (replaces an empty date, a 1 January, a portal
sentinel or the scrape day) or may also CORRECT a stored date: only where the
rule reads the court's own statement unambiguously (docket, file name,
refetched portal field, an entry's closing trailer, head and signature
agreeing) or the stored date is known damage (the 2026-03-12 text-recovery
pass, stamped `date_extraction`; Tribuna rows stored before the 2026-09-27 fix).

Only decision_date changes, plus a `date_repair` stamp {from, to, rule, at,
prev}, so the change is exactly reversible. The script refuses to write if any
other field would change, a decision_id would change, or the line count
differs. A date edit changes the row's canonical_key (court|docket|date): use
--simulate to replay the build's insert collisions and dedup Pass 1 for the
court before and after, and check the per-court swap gate (80 %).

Streaming, DRY RUN by default. --apply hard-links <shard>.bak-dates-<UTC date>
beside each shard, writes <shard>.tmp (neither matches the build's *.jsonl
glob) and swaps it in atomically. Run outside the build window with no scrape
appending (runbook).

Usage:
  python3 scripts/repair_source_dates.py --court nw_gerichte \\
      output/decisions/nw_gerichte.jsonl output/decisions/es_nw_gerichte.jsonl [--simulate]
  ... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import source_dates as sd

COURTS = ("bge_egmr", "gr_gerichte", "ti_gerichte", "ar_gerichte", "ur_gerichte",
          "nw_gerichte", "bl_gerichte", "be_zivilstraf")
# base_tribuna read each row's own record from 2e46549d (2026-09-27); rows
# scraped before then may carry another row's date (scripts/restore_tribuna_dates.py).
TRIBUNA_FIX = "2026-09-28"
BE_PLACES = ("Bern", "Berne", "Biel", "Bienne", "Thun", "Burgdorf", "Langenthal", "Langnau")
STAMP = "date_repair"
FILL, CORRECT = "fill", "correct"
# Dates read off an identifier or a portal field, not out of running text:
# exempt from the docket-year/publication plausibility veto (a yearbook
# volume collects rulings decades older than its number: ARGVP 1988 1018 is
# a Regierungsrat decision of 28.11.1960).
IDENTIFIER_RULES = {"egmr_docket", "ar_gvp_filename", "gr_yearbook_trailer",
                    "gr_yearbook_twin_pdf", "ti_portal_data_decisione", "bl_slug",
                    "bl_slug_and_head"}


def _stored(rec: dict) -> str | None:
    v = str(rec.get("decision_date") or "").strip()[:10]
    return v or None


def _damaged(rec: dict) -> bool:
    """Date written by the 2026-03-12 text-recovery pass (repair_decision_dates.py
    stamps date_extraction); those grabbed statute and lower-court dates."""
    de = rec.get("date_extraction")
    return isinstance(de, dict) and bool(de.get("method"))


# ------------------------------------------------------------------ context

class Context:
    """What a rule may need beyond the row: twins in the other shard, the
    refetched TI evidence."""

    def __init__(self, court: str, shards: list[Path], evidence: Path | None, today: date,
                 fill_only: bool = False):
        self.court, self.today, self.fill_only = court, today, fill_only
        self.twin_moves: dict[tuple, str] = {}
        self.gr_trailer_by_pdf: dict[str, set] = defaultdict(set)
        self.ti_evidence: dict[str, dict] = {}
        if court == "gr_gerichte":
            for rec in _iter_records(shards):
                if rec.get("court") != court or not sd.GR_YEARBOOK.match(str(rec.get("docket_number") or "")):
                    continue
                key = sd.gr_yearbook_pdf_key(rec.get("pdf_url") or rec.get("source_url"))
                d = sd.gr_yearbook_trailer(rec.get("full_text"), rec.get("docket_number"), today)
                if key and d:
                    self.gr_trailer_by_pdf[key].add(d)
        if court == "ti_gerichte":
            if evidence is None:
                raise SystemExit("ti_gerichte needs --evidence (scripts/fetch_ti_decision_dates.py)")
            with open(evidence, encoding="utf-8") as fh:
                for line in fh:
                    e = json.loads(line)
                    self.ti_evidence[e["decision_id"]] = e


def _iter_records(shards: list[Path]):
    for p in shards:
        with open(p, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec


# -------------------------------------------------------------------- rules

def rule(rec: dict, ctx: Context) -> tuple[str | None, str, str]:
    """(source date or None, rule name, FILL|CORRECT) for one row of ctx.court."""
    court, today = ctx.court, ctx.today
    text = rec.get("full_text") or ""
    docket = str(rec.get("docket_number") or "")
    url = rec.get("pdf_url") or rec.get("source_url") or ""

    if court == "bge_egmr":
        return sd.egmr_docket_date(docket, today), "egmr_docket", CORRECT

    if court == "gr_gerichte":
        if sd.GR_YEARBOOK.match(docket.strip()):
            own = sd.gr_yearbook_trailer(text, docket, today)
            if own:
                return own, "gr_yearbook_trailer", CORRECT
            twins = ctx.gr_trailer_by_pdf.get(sd.gr_yearbook_pdf_key(url) or "", set())
            if len(twins) == 1:
                return next(iter(twins)), "gr_yearbook_twin_pdf", CORRECT
            return None, "gr_yearbook_no_trailer", FILL
        # The head agrees with the portal date on 71 of 72 rows scraped after
        # the Tribuna fix (one by a day): it may correct pre-fix rows only.
        return sd.head_vom_date(text, 2500, today), "gr_head", CORRECT

    if court == "ti_gerichte":
        e = ctx.ti_evidence.get(rec.get("decision_id"))
        if not e:
            return None, "ti_no_evidence", FILL
        return e.get("data_decisione"), "ti_portal_data_decisione", FILL

    if court == "ar_gerichte":
        if sd.is_ar_gvp(url):
            return sd.ar_filename_date(url, today), "ar_gvp_filename", CORRECT
        return sd.ar_filename_date(url, today), "ar_filename", FILL

    if court == "ur_gerichte":
        cite = sd.ur_citation_line(text, docket, today)
        if cite:
            return cite, "ur_citation_line", FILL
        # The head rule only for a full ruling (letterhead first); the
        # Rechenschaftsbericht excerpts name the reviewed decision "vom …".
        if "OBERGERICHT" in text[:400].upper() or "LANDGERICHT" in text[:400].upper():
            return sd.head_vom_date(text, 1500, today), "ur_head", FILL
        return None, "ur_none", FILL

    if court == "nw_gerichte":
        return sd.head_vom_date(text, 1500, today), "nw_head", FILL

    if court == "bl_gerichte":
        d = sd.bl_slug_date(docket, today)
        if d:
            # The ruling's own head names the same date as its file name: two
            # independent statements of the court, so they correct a stored
            # date (bl_wayback.jsonl: 2015-01-25 for "vom 25. Januar 2018",
            # 2018-01-03 for "vom 1. März 2018", 1995-02-15 recovered from a
            # cited date).
            if d == (sd.bl_kg_head(text, today) or sd.head_vom_date(text, 400, today)):
                return d, "bl_slug_and_head", CORRECT
            # Agrees with the stored date on 6,055 rows; where they differ
            # and the head is silent or disagrees, nothing says which is
            # right, so the slug only fills.
            return d, "bl_slug", FILL
        d = sd.bl_steuergericht_trailer(text, docket, today)
        if d:
            return d, "bl_steuergericht_trailer", FILL
        return sd.bl_kg_head(text, today), "bl_kg_head", FILL

    if court == "be_zivilstraf":
        head = sd.place_date_head(text, BE_PLACES, 1500, today)
        if not head:
            return None, "be_no_letterhead", FILL
        sign = sd.place_date_head(text[-3000:], BE_PLACES, 3000, today)
        if sign == head:
            return head, "be_letterhead_and_signature", CORRECT
        return head, "be_letterhead", FILL

    return None, "other", FILL


def _decide_own(rec: dict, ctx: Context) -> tuple[str | None, str]:
    """(new date or None, outcome class) from the row's own source."""
    if rec.get("court") != ctx.court:
        return None, "other_court"
    stored = _stored(rec)
    placeholder = sd.is_placeholder(stored, rec.get("scraped_at"))
    new, name, mode = rule(rec, ctx)
    if not new:
        return None, (f"{name}:no_source_left_placeholder" if placeholder else f"{name}:no_source")
    if new == stored:
        return None, f"{name}:agrees"
    pub = rec.get("publication_date")
    if ctx.court in ("be_zivilstraf", "gr_gerichte") and str(rec.get("scraped_at") or "") < TRIBUNA_FIX:
        pub = None  # the same string-table bug garbled the register's publication date
    if name not in IDENTIFIER_RULES and not sd.plausible(new, rec.get("docket_number"), pub):
        return None, f"{name}:implausible_left_alone"
    if placeholder:
        return new, f"{name}:{'filled' if not stored or stored.startswith('0000') else 'placeholder_replaced'}"
    # A letterhead without the matching signature line never corrects: it can
    # carry a wrong year (BK 22 322 heads "18. Januar 2022", stored 2023-01-18).
    may_correct = mode == CORRECT or (_damaged(rec) and name != "be_letterhead")
    if ctx.court in ("be_zivilstraf", "gr_gerichte") and name in ("be_letterhead_and_signature", "gr_head"):
        # The scraper reads each row's own record since the Tribuna fix:
        # never second-guess a post-fix row (65/66 agree with the letterhead,
        # the 66th by a day: the portal is the court's own field).
        may_correct = str(rec.get("scraped_at") or "") < TRIBUNA_FIX
    if may_correct and not ctx.fill_only:
        return new, f"{name}:corrected"
    return None, f"{name}:differs_left_alone"


def twin_key(rec: dict) -> str:
    """The row's docket as the build's dedup sees it, without the date."""
    import build_fts5 as b
    from models import make_canonical_key
    r = dict(rec, docket_number=b._docket_normalize_inline(rec.get("docket_number")))
    return make_canonical_key(rec.get("court") or "", b._dedup_docket(r), None)


def prepare_twins(ctx: Context, shards: list[Path]) -> None:
    """Where one copy of a docket moves from date X to Y on its own source,
    a copy with no source of its own still dated X (the federation stub of
    the same ruling) moves with it. Without this the two stop sharing a
    canonical key and the build serves the ruling twice."""
    moves: dict[tuple, set] = defaultdict(set)
    for rec in _iter_records(shards):
        if rec.get("court") != ctx.court:
            continue
        new, _ = _decide_own(rec, ctx)
        if new:
            moves[(twin_key(rec), _stored(rec))].add(new)
    ctx.twin_moves = {k: next(iter(v)) for k, v in moves.items() if len(v) == 1}


def decide(rec: dict, ctx: Context) -> tuple[str | None, str]:
    """(new date or None, outcome class)."""
    new, outcome = _decide_own(rec, ctx)
    if new or not ctx.twin_moves:
        return new, outcome
    follow = ctx.twin_moves.get((twin_key(rec), _stored(rec)))
    if ctx.court == "bl_gerichte" and outcome.endswith(":differs_left_alone"):
        # The row's own source names a date it may only fill with; its twin
        # was corrected to that same date on a stronger source (the BL
        # federation stub's slug, confirmed by the Wayback copy's head).
        own = rule(rec, ctx)[0]
        if follow and own == follow and not ctx.fill_only:
            return follow, "twin_follow:corrected"
        return new, outcome
    if not outcome.split(":")[-1].startswith("no_source"):
        return new, outcome
    if follow and not ctx.fill_only:
        return follow, "twin_follow:corrected"
    if follow and sd.is_placeholder(_stored(rec), rec.get("scraped_at")):
        return follow, "twin_follow:placeholder_replaced"
    return new, outcome


def repaired(rec: dict, new: str, outcome: str, today: date) -> dict:
    out = dict(rec)
    out["decision_date"] = new
    stamp = {"from": rec.get("decision_date"), "to": new, "rule": outcome.split(":")[0],
             "at": today.isoformat()}
    if isinstance(rec.get(STAMP), dict):
        stamp["prev"] = rec[STAMP]
    out[STAMP] = stamp
    return out


def check(before: dict, after: dict) -> None:
    keys = set(before) | set(after)
    for k in keys - {"decision_date", STAMP}:
        if before.get(k) != after.get(k):
            raise SystemExit(f"refusing: {before.get('decision_id')} would change {k}")


# ------------------------------------------------------------------- rewrite

def run(shards: list[Path], ctx: Context, apply: bool, examples: int = 0,
        sample: list | None = None) -> dict[str, Counter]:
    stats: dict[str, Counter] = {}
    rnd = random.Random(20261108)
    for shard in shards:
        st: Counter = Counter()
        stats[shard.name] = st
        tmp = shard.with_name(shard.name + ".tmp")
        fout = open(tmp, "wb") if apply else None
        shown = 0
        try:
            with open(shard, "rb") as fh:
                for raw in fh:
                    st["lines"] += 1
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        rec = None
                    if not isinstance(rec, dict):
                        st["unparsed"] += 1
                        if fout:
                            fout.write(raw)
                        continue
                    new, outcome = decide(rec, ctx)
                    st[f"outcome:{outcome}"] += 1
                    if new is None:
                        if fout:
                            fout.write(raw)
                        continue
                    out = repaired(rec, new, outcome, ctx.today)
                    check(rec, out)
                    st["rows_changed"] += 1
                    if sample is not None:
                        sample.append((rnd.random(), shard.name, rec.get("decision_id"),
                                       rec.get("docket_number"), rec.get("decision_date"), new,
                                       outcome, rec.get("pdf_url") or rec.get("source_url")))
                    if shown < examples:
                        shown += 1
                        print(f"  {shard.name} {rec.get('decision_id')}: {rec.get('decision_date')} -> "
                              f"{new} [{outcome}]", file=sys.stderr)
                    if fout:
                        fout.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\n")
            if fout:
                fout.flush()
                os.fsync(fout.fileno())
                fout.close()
                fout = None
                with open(tmp, "rb") as fh:
                    written = sum(1 for _ in fh)
                if written != st["lines"]:
                    raise SystemExit(f"refusing: {shard}: wrote {written} lines, read {st['lines']}")
                if st["rows_changed"]:
                    stamp = time.strftime("%Y%m%d", time.gmtime())
                    backup = shard.with_name(f"{shard.name}.bak-dates-{stamp}")
                    if backup.exists():
                        raise SystemExit(f"refusing to overwrite existing backup {backup}")
                    os.link(shard, backup)  # zero-cost, keeps the original inode
                    os.chmod(tmp, os.stat(shard).st_mode & 0o7777)
                    os.replace(tmp, shard)
                    print(f"rewrote {shard}; backup {backup}", file=sys.stderr)
        finally:
            if fout:
                fout.close()
            if tmp.exists():
                tmp.unlink()
    return stats


# ---------------------------------------------------------------- simulation

def simulate(shards: list[Path], ctx: Context) -> dict:
    """Replay what the build does with this court's rows, before and after the
    repair: insert in direct-first order (decision_id collisions: same
    canonical key = duplicate skipped, else <id>_dYYYYMMDD or dropped without a
    clean date), then dedup Pass 1 (same canonical key: keep the longest).
    Content-aware Pass 2 and the federation twin fold run after this and only
    remove more; they are not replayed. Returns counts and the rows Pass 1
    newly folds after the repair (to check they are one ruling)."""
    import build_fts5 as b
    from models import make_canonical_key

    order = sorted(shards, key=lambda p: (p.name.startswith("es_"), p.name))

    def replay(after: bool):
        ids: dict[str, str] = {}
        rows: dict[str, tuple] = {}
        dropped = Counter()
        for shard in order:
            for rec in _iter_records([shard]):
                if rec.get("court") != ctx.court:
                    continue
                if after:
                    new, _ = decide(rec, ctx)
                    if new:
                        rec = dict(rec, decision_date=new)
                text, reg = rec.get("full_text") or "", rec.get("regeste") or ""
                if len(text) < 10 and len(reg) < 10:
                    dropped["stub"] += 1
                    continue
                rec = dict(rec)
                rec["docket_number"] = b._docket_normalize_inline(rec.get("docket_number"))
                d = b._date_normalize_inline(rec.get("decision_date"))
                if not d:
                    d = b._date_recover_inline(ctx.court, text, rec.get("docket_number"))
                    d = (d if isinstance(d, str) else d.isoformat()) if d else None
                rec["decision_date"] = d
                ck = make_canonical_key(ctx.court, b._dedup_docket(rec), d)
                did = rec["decision_id"]
                if did in ids:
                    if ids[did] == ck:
                        dropped["same_id_same_key"] += 1
                        continue
                    ds = str(d or "").replace("-", "")
                    if len(ds) != 8:
                        dropped["same_id_no_clean_date"] += 1
                        continue
                    did = f"{did}_d{ds}"
                    if did in ids:
                        dropped["same_id_d_taken"] += 1
                        continue
                ids[did] = ck
                rows[did] = (ck, len(text) + len(reg), shard.name)
        # Pass 1 groups every key with a docket part (build: NOT LIKE '%||%'),
        # an empty date part included.
        groups = defaultdict(list)
        for did, (ck, ln, src) in rows.items():
            if ck.split("|")[1]:
                groups[ck].append((ln, did, src))
        pass1 = {}
        for ck, members in groups.items():
            if len(members) > 1:
                members.sort(reverse=True)
                for _, did, src in members[1:]:
                    pass1[did] = (members[0][1], ck)
        return rows, dropped, pass1

    b_rows, b_drop, b_p1 = replay(False)
    a_rows, a_drop, a_p1 = replay(True)
    # Rows of one docket that share a date today and would not after the
    # repair: a twin repaired on one side only (comes back as a duplicate).
    dates_b: dict[str, set] = defaultdict(set)
    dates_a: dict[str, set] = defaultdict(set)
    for rec in _iter_records(order):
        if rec.get("court") != ctx.court:
            continue
        dk = make_canonical_key(ctx.court, b._dedup_docket(rec), None)
        dates_b[dk].add(_stored(rec))
        new, _ = decide(rec, ctx)
        dates_a[dk].add(new or _stored(rec))
    twins_split = sum(1 for dk in dates_b if len(dates_b[dk]) == 1 and len(dates_a[dk]) > 1)
    served_b = len(b_rows) - len(b_p1)
    served_a = len(a_rows) - len(a_p1)
    new_folds = {k: v for k, v in a_p1.items() if k not in b_p1}
    return {
        "inserted_before": len(b_rows), "inserted_after": len(a_rows),
        "dropped_before": dict(b_drop), "dropped_after": dict(a_drop),
        "pass1_before": len(b_p1), "pass1_after": len(a_p1),
        "served_before": served_b, "served_after": served_a,
        "served_ratio": round(served_a / served_b, 4) if served_b else None,
        "swap_gate_ok": (served_b < 500) or served_a >= 0.8 * served_b,
        "new_pass1_folds": len(new_folds),
        "twins_split": twins_split,
        "new_pass1_examples": sorted(new_folds.items())[:20],
        "d_twins_before": sum(1 for k in b_rows if "_d" in k[-10:]),
        "d_twins_after": sum(1 for k in a_rows if "_d" in k[-10:]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--court", required=True, choices=COURTS)
    ap.add_argument("shards", nargs="+", type=Path, help="the court's direct and es_ shards")
    ap.add_argument("--evidence", type=Path, help="ti_gerichte: refetched portal dates (jsonl)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--simulate", action="store_true", help="replay the build's dedup before/after")
    ap.add_argument("--examples", type=int, default=0, help="print this many changed rows")
    ap.add_argument("--sample", type=int, default=0, help="print a random sample of N changed rows")
    ap.add_argument("--fill-only", action="store_true",
                    help="replace only empty/placeholder dates, never correct a stored one")
    ap.add_argument("--today", help="YYYY-MM-DD (tests)")
    args = ap.parse_args(argv)
    for p in args.shards:
        if not p.exists():
            print(f"shard not found: {p}", file=sys.stderr)
            return 2
        if p.suffix != ".jsonl":
            print(f"not a shard: {p}", file=sys.stderr)
            return 2
    if args.apply and args.simulate:
        print("--simulate is a dry-run report; run it before --apply", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else date.today()
    ctx = Context(args.court, args.shards, args.evidence, today, args.fill_only)
    prepare_twins(ctx, args.shards)
    print(("APPLY" if args.apply else "DRY RUN") + f": {args.court} {[str(p) for p in args.shards]}",
          file=sys.stderr)
    sample: list = [] if args.sample else None
    stats = run(args.shards, ctx, args.apply, args.examples, sample)
    report: dict = {k: dict(sorted(v.items())) for k, v in stats.items()}
    if args.simulate:
        report["simulation"] = simulate(args.shards, ctx)
    if sample:
        report["sample"] = [s[1:] for s in sorted(sample)[:args.sample]]
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
