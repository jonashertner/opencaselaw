#!/usr/bin/env python3
"""
apply_bge_historical_recoveries.py — write the rulings recovered in
runbooks/historical_bge_recovered_2026-10-08/ into the bge_historical shard.

Five references that source_defects.py withholds (their DFR document holds
another ruling) are recovered from DFR material at hand: 22 I 12 from its own
scan, 39 I 469 by OCR of the two neighbouring scans, and the first and last
pages of 52 I 23, 39 and 149 from the neighbouring rulings' scans (partial; the
missing pages are marked in the text). build.py in that directory shows how;
manifest.json holds each text's SHA-256, date, pages and sources.

Per listed reference the shard row is replaced by a row built the way the
scraper builds one (same fields, same serialisation), with the recovered text,
the date of the ruling's own header, and a `source_recovery` stamp (pages held
and missing, sources, method, text SHA-256). A missing row is added. Rows of
other courts are never touched.

The stamp tells the other repairs to leave the row alone:
scripts/segment_bge_historical.py and scripts/repair_bge_historical_sources.py
skip stamped rows. source_defects.py keeps withholding the references until its
entries are changed after the build has served the recovered text.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent (a row
already holding the recovered text is left). --apply writes every replaced row,
as it was, to <shard>.recovery-<UTC date>.jsonl beside the shard (undo file).
Run outside the build window: after the full publish, before the 01:00 UTC
nightly scrape. The next full build serves the result.

Usage:
  python3 scripts/apply_bge_historical_recoveries.py output/decisions/bge_historical.jsonl
  python3 scripts/apply_bge_historical_recoveries.py output/decisions/bge_historical.jsonl --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import source_defects

COURT = "bge_historical"
RECOVERY_DIR = REPO / "runbooks" / "historical_bge_recovered_2026-10-08"
MANIFEST_REL = "runbooks/historical_bge_recovered_2026-10-08/manifest.json"
# scrapers/bge_historical.py fetch_decision
SECTION_AREAS = {
    "I": "Öffentliches Recht",
    "II": "Zivilrecht",
    "III": "Schuldbetreibung und Konkurs",
    "IV": "Strafrecht",
    "V": "Sozialversicherungsrecht",
}
STAMP_KEYS = ("ruling", "completeness", "pages_held", "pages_missing", "date_source",
              "sources", "method", "text_sha256")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(recovery_dir: Path = RECOVERY_DIR) -> dict[str, dict]:
    """The manifest entries by decision_id, each with its verified text.
    Refuses a text that is not the manifest's, and a reference that
    source_defects does not list."""
    manifest = json.loads((recovery_dir / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for e in manifest["recoveries"]:
        text = (recovery_dir / e["file"]).read_text(encoding="utf-8").removesuffix("\n")
        if _sha256(text) != e["text_sha256"]:
            raise ValueError(f"{e['file']}: the text is not the one in the manifest")
        if source_defects.lookup(e["docket_number"]) is None:
            raise ValueError(f"{e['reference']}: not a source_defects entry")
        out[f"{COURT}_{e['docket_number']}"] = {**e, "text": text}
    return out


def build_row(e: dict, now: datetime) -> dict:
    """The shard row for one recovery, as the scraper would write it."""
    from models import Decision, extract_citations

    volume, section, page = e["docket_number"].split("_")
    ref = f"BGE {volume} {section} {page}"
    text = e["text"]
    d = Decision(
        decision_id=f"{COURT}_{e['docket_number']}",
        court=COURT,
        canton="CH",
        docket_number=e["docket_number"],
        decision_date=date.fromisoformat(e["decision_date"]),
        language=e["language"],
        title=ref,
        legal_area=SECTION_AREAS.get(section),
        bge_reference=ref,
        collection=ref,
        full_text=text,
        source_url=e["source_url"],
        cited_decisions=extract_citations(text) if len(text) > 200 else [],
        scraped_at=now,
    )
    row = d.model_dump()
    for key, val in row.items():            # run_scraper.serialize_decision
        if isinstance(val, (date, datetime)):
            row[key] = val.isoformat()
    row["source_recovery"] = {**{k: e.get(k) for k in STAMP_KEYS},
                              "manifest": MANIFEST_REL,
                              "at": now.isoformat(timespec="seconds")}
    return row


def is_recovered(obj: dict, entries: dict[str, dict]) -> bool:
    """True when the row already holds its recovered text."""
    e = entries.get(obj.get("decision_id"))
    return bool(e and (obj.get("source_recovery") or {}).get("text_sha256") == e["text_sha256"]
                and _sha256(obj.get("full_text") or "") == e["text_sha256"])


def run(path: Path, apply: bool, entries: dict[str, dict] | None = None) -> Counter:
    entries = load() if entries is None else entries
    stats: Counter = Counter()
    now = datetime.now(timezone.utc)
    undo_path = path.with_name(f"{path.name}.recovery-{now.date().isoformat()}.jsonl")
    pending = dict(entries)
    fout = undo = None
    tmp_path = None
    if apply:
        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
        undo = open(undo_path, "a", encoding="utf-8")  # noqa: SIM115 (closed on every path below)

    def emit(line: str) -> None:
        if fout:
            fout.write(line)

    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    emit(line)
                    continue
                try:
                    obj = json.loads(stripped)
                except ValueError:
                    stats["json_error"] += 1
                    emit(line)
                    continue
                did = obj.get("decision_id")
                if obj.get("court") != COURT or did not in entries:
                    emit(line)
                    continue
                pending.pop(did, None)
                if is_recovered(obj, entries):
                    stats["already_recovered"] += 1
                    emit(line)
                    continue
                stats[f"replaced:{entries[did]['completeness']}"] += 1
                stats["rows_changed"] += 1
                print(f"  {did}: replaced ({entries[did]['completeness']})", file=sys.stderr)
                if fout:
                    undo.write(stripped + "\n")
                    fout.write(json.dumps(build_row(entries[did], now), ensure_ascii=False) + "\n")
        for did, e in pending.items():
            stats[f"added:{e['completeness']}"] += 1
            stats["rows_changed"] += 1
            print(f"  {did}: added ({e['completeness']}); the shard had no row", file=sys.stderr)
            emit(json.dumps(build_row(e, now), ensure_ascii=False) + "\n")
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
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply)
    for k, v in sorted(stats.items()):
        print(f"{v:>10}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
