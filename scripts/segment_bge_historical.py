#!/usr/bin/env python3
"""
segment_bge_historical.py — cut every historical BGE row (volumes 1-79) of the
bge_historical shard down to its own ruling and date it from its own header.

Why (user report 2026-10-07, BGE 78 IV 83 "Fyg gegen Born"):
  * DFR serves each ruling as the scanned page range it is printed on, in
    double-page spreads, so the row's full_text also holds the tail of the
    preceding ruling and the head of the following one (1,336 of 14,578 rows
    carry two or more ruling headers). Search found the neighbours' sentences
    in the wrong ruling and a quotation from No 23 "verified" against No 22.
  * The date passes read the first date of that text: BGE 78 IV 83 was served
    as of 17 July 1951, a letter quoted in No 21; its header says 3 June 1952.
  * The structure extractor read the serial number "23." of the next ruling as
    Erwägung 23 of this one, so get_erwaegung returned another case's text.

What it does per row (bge_historical_segment.segment, see there for the rules):
  * own header placed on the reference page -> full_text becomes the text from
    that header to the next ruling's header; cited_decisions is recomputed from
    it; a `text_segment` stamp records the offsets, the old length and the old
    text's SHA-256.
  * decision_date: the date written in the own header, volume-gated
    (method "own_header"). When the header carries no readable date, a date a
    text pass put there is only kept if the header block holds it (day, year
    and any readable month) and it lies in the volume window; otherwise
    the row goes back to the 1 January volume placeholder ("placeholder"), which
    the API flags date_is_estimated — an honest estimate beats a neighbour's
    date. Rows whose header cannot be placed keep their text and date.
    Every changed date gets a `date_restore` stamp (the previous one is kept
    under "prev").
  * --apply also writes the replaced full_text of every cut row to
    <shard>.undo-presegment-<UTC date>.jsonl.bak beside the shard (undo file;
    never named *.jsonl, which the build would ingest as a shard).

Streaming, temp file + atomic replace, court 'bge_historical' rows only, DRY RUN
by default, idempotent (a second run changes nothing). Run after publish.py
exits, never during the build window (the shard is the build's input); the next
full build serves the cut text, re-extracts the structure from it and the
dataset export follows. Then regenerate the canonical-identity sidecar
(backfill_canonical_identity.py --write) so it drops its body-text dates.

Usage:
  python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl
  python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import bge_historical_segment as seg  # noqa: E402

COURT = "bge_historical"
BGE_VOLUME_EPOCH = 1874


def _placeholder(volume: int) -> str:
    return date(volume + BGE_VOLUME_EPOCH, 1, 1).isoformat()


def decide(obj: dict) -> dict:
    """The new field values for one row ({} when nothing changes) plus a
    '_why' entry naming the outcome, for the statistics."""
    if obj.get("source_recovery"):
        # already the ruling's own text, recovered from its neighbours' scans
        # (apply_bge_historical_recoveries.py); its gap markers are not headers
        return {"_why": "recovered"}
    vp = seg.volume_and_page(obj.get("docket_number")) or seg.volume_and_page(obj.get("decision_id"))
    if vp is None:
        return {"_why": "unparsed_reference"}
    volume, page = vp
    text = obj.get("full_text") or ""
    s = seg.segment(text, page)
    if s is None:
        return {"_why": "own_header_not_placed"}
    out: dict = {"_why": "placed"}
    if (s.start, s.end) != (0, len(text)):
        new_text = text[s.start:s.end].rstrip()
        out["full_text"] = new_text
        out["_cut"] = (s.start, len(text) - s.end)
        out["_segment"] = s
    old = str(obj.get("decision_date") or "")[:10]
    hd = seg.header_date(s.header, volume)
    if hd is not None:
        new_date, method = hd.isoformat(), "own_header"
    elif (old and not old.endswith("-01-01") and _in_volume_window(old, volume)
          and _header_holds(s.header, old)):
        new_date, method = old, "keep"
    else:
        new_date, method = _placeholder(volume), "placeholder"
    out["_date_method"] = method
    if new_date != old:
        out["decision_date"] = new_date
    return out


def _in_volume_window(iso: str, volume: int) -> bool:
    """The volume gate of the own-header dates (seg.historical_year_plausible):
    a stored date read from an OCR year ("vom 16. Juli 1991" in volume 47 =
    1921) is not kept."""
    return iso[:4].isdigit() and seg.historical_year_plausible(int(iso[:4]), volume)


def _header_holds(header: str, iso: str) -> bool:
    """True when the own header carries ``iso``: parsed whole, or — its month
    being OCR noise ("vom 10. Mal UMO", "du S ferner 1933") — its day number
    and its year both written there."""
    import re

    from models import parse_date

    header = seg.normalise_header_date(header)
    if seg.glued_day(header, int(iso[8:10])):
        return False        # "!3 aprile": the day lost a digit to OCR
    d = parse_date(header)
    if d is not None:
        return d.isoformat() == iso
    # A readable month must be the stored one: "vom 12~ Dezember 1928" holds
    # the day and the year of a stored 1928-10-12, not its month (BGE 54 II 464).
    months = seg.header_months(header)
    if months and int(iso[5:7]) not in months:
        return False
    year, day = iso[:4], str(int(iso[8:10]))
    return bool(re.search(rf"\b{year}\b", header) and re.search(rf"(?<!\d){day}\b", header))


def run(path: Path, apply: bool, examples: int) -> Counter:
    from models import extract_citations

    stats: Counter = Counter()
    shown = 0
    fout = undo = None
    tmp_path = None
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    undo_path = path.with_name(f"{path.name}.undo-presegment-{now.date().isoformat()}.jsonl.bak")
    if apply:
        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
        undo = open(undo_path, "a", encoding="utf-8")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    if fout:
                        fout.write(line)
                    continue
                try:
                    obj = json.loads(stripped)
                except ValueError:
                    stats["json_error"] += 1
                    if fout:
                        fout.write(line)
                    continue
                if obj.get("court") != COURT:
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                d = decide(obj)
                stats[f"outcome:{d['_why']}"] += 1
                if "_date_method" in d:
                    stats[f"date:{d['_date_method']}"] += 1
                changed = "full_text" in d or "decision_date" in d
                if "full_text" in d:
                    before, after = d["_cut"]
                    stats["text_cut"] += 1
                    stats["chars_removed"] += before + after
                    stats["cut_before"] += bool(before)
                    stats["cut_after"] += bool(after)
                if "decision_date" in d:
                    stats["date_changed"] += 1
                if changed and shown < examples:
                    shown += 1
                    cut = d.get("_cut", (0, 0))
                    print(f"  {obj.get('decision_id'):<30} -{cut[0]}/-{cut[1]} chars  "
                          f"{obj.get('decision_date')} -> {d.get('decision_date', '=')} "
                          f"[{d.get('_date_method')}]", file=sys.stderr)
                if not changed:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                if "full_text" in d:
                    old_text = obj.get("full_text") or ""
                    s = d["_segment"]
                    undo.write(json.dumps({"decision_id": obj.get("decision_id"),
                                           "full_text": old_text}, ensure_ascii=False) + "\n")
                    obj["text_segment"] = {
                        "method": "own_header", "start": s.start, "end": s.end,
                        "serial": s.serial, "chars_before": len(old_text),
                        "sha256_before": hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
                        "at": stamp,
                    }
                    obj["full_text"] = d["full_text"]
                    if len(d["full_text"]) > 200:
                        obj["cited_decisions"] = extract_citations(d["full_text"])
                if "decision_date" in d:
                    restore = {"from": obj.get("decision_date"), "to": d["decision_date"],
                               "method": d["_date_method"], "at": stamp}
                    if obj.get("date_restore"):
                        restore["prev"] = obj["date_restore"]
                    obj["date_restore"] = restore
                    obj["decision_date"] = d["decision_date"]
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        if undo:
            undo.close()
        raise
    if fout:
        fout.close()
        undo.close()
        if stats["rows_changed"]:
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
        if not undo_path.stat().st_size:
            undo_path.unlink()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard", type=Path, help="output/decisions/bge_historical.jsonl")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=12)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply, args.examples)
    for k, v in sorted(stats.items()):
        print(f"{v:>10}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
