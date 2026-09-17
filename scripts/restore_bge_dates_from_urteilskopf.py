#!/usr/bin/env python3
"""
restore_bge_dates_from_urteilskopf.py — re-derive decision_date for `bge`
shard rows from their OWN header, gated by the volume year.

Why (audit 2026-09-10, see runbooks/bge_decision_dates_2026-09-10.md):
  * scrapers/bge.py read only the first Urteilskopf block, so every direct
    row carried the 1 January placeholder; chamber and docket_2 stayed NULL.
  * scripts/repair_decision_dates.py (one-off, 2026-03-12) then rewrote
    those placeholders from body text and, on es_bge.jsonl, overwrote CORRECT
    entscheidsuche dates with statute dates from the Regeste ("Übereinkommen
    vom 16. Mai 1972" → BGE 150 III 367 dated 1972-05-16). It stamped every
    rewritten row with `date_extraction` (method, metadata_date = the value
    it replaced) and moved that value into publication_date.

What the row itself still holds:
  * direct rows: full_text starts with the Urteilskopf lines
    ("... Abteilung i.S. A. gegen B. (Beschwerde in Zivilsachen)" /
    "5A_691/2023 vom 13. August 2024") — parse_urteilskopf() (shared with
    the fixed scraper) recovers the ruling date, docket_2 and chamber.
  * entscheidsuche rows: the Kopfzeile "Bundesgericht (BGE) Band III
    13.08.2024 BGE 150 III 367 (5A_691/2023)" carries the ruling date
    numerically; older volumes carry only the year.

Decision per row, in order (first hit wins; every step is volume-gated by
scrapers.bge.header_date_plausible: volume year - 3 .. volume year + 1, i.e.
late publications such as BGE 149 IV 97 = 6B_1079/2021 of 22.11.2021 are kept,
a date after the volume is not; the cite warning itself stays at ±1):
  1. header date from the row's own Urteilskopf / Kopfzeile   → method "urteilskopf" / "kopfzeile"
  2. the current decision_date if it passes its gate         → "keep" (no write)
     (a 03-12 body-text value: strict ±1; an untouched source value: the source gate)
  3. date_extraction.metadata_date if it passes the source gate → "metadata" (undo the 03-12 rewrite)
  4. 1 January of the volume year                            → "placeholder"
publication_date is reset to None where the 03-12 pass put the replaced
metadata date there (the direct scraper never sets it; the entscheidsuche
ingest sets None). Every changed row gets a `date_restore` stamp; the old
`date_extraction` stamp is kept as history.

Streaming, temp file + atomic replace, court == 'bge' rows only, DRY RUN by
default. Do not run on the VPS shards during the build window (they are the
build's input) — run after publish.py exits, then let the next full build
pick the shard up. Never scp the result into the git tree (MCP deploy path).

Usage:
  python3 scripts/restore_bge_dates_from_urteilskopf.py output/decisions/bge.jsonl
  python3 scripts/restore_bge_dates_from_urteilskopf.py output/decisions/es_bge.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from models import parse_date
from scrapers.bge import BGE_VOLUME_EPOCH, header_date_plausible, parse_urteilskopf

_KEY_RE = re.compile(
    r"(?:^|[\s_])(?:BGE|ATF|DTF)?[\s_]*(\d{1,3})[\s_]+([IVX]+[abAB]?)[\s_]+(\d{1,4})(?:$|[\s_,;)])"
)
_REF_LINE_RE = re.compile(r"^\d{1,3}\s+[IVX]+[abAB]?\s+\d{1,4}$")
_KOPFZEILE_DATE_RE = re.compile(r"\bBand\s+[IVX]+[ab]?\s+(\d{2}\.\d{2}\.\d{4})\b")


def volume_of(docket: str | None, decision_id: str | None) -> int | None:
    for s in (docket, decision_id):
        m = _KEY_RE.search(s or "")
        if m:
            return int(m.group(1))
    return None


def consistent(d: date | str | None, volume: int) -> bool:
    """Read-side window of the cite warning: |year - (volume + 1874)| <= 1.
    Used for the audit's definition of a bad date and for a value that the
    03-12 body-text pass wrote (a guess is not granted the late-publication
    allowance)."""
    s = str(d or "")
    return s[:4].isdigit() and abs(int(s[:4]) - (volume + BGE_VOLUME_EPOCH)) <= 1


def source_consistent(d: date | str | None, volume: int) -> bool:
    """Source-side gate (scrapers.bge.header_date_plausible): a value that
    came from the decision's own header or from the feed itself may trail
    the volume year by up to three years (BGE 149 IV 97 = 6B_1079/2021 of
    22.11.2021 in the 2023 volume) but never follow it by more than one."""
    s = str(d or "")
    if not s[:4].isdigit():
        return False
    return header_date_plausible(date(int(s[:4]), 1, 1), volume + BGE_VOLUME_EPOCH)


def header_date_direct(full_text: str, volume: int) -> tuple[date | None, dict]:
    """Urteilskopf lines of a direct CLIR row: after 'Urteilskopf', before 'Regeste'."""
    lines = (full_text or "")[:3000].split("\n")
    if "Urteilskopf" not in lines[:3]:
        return None, {}
    kopf: list[str] = []
    for line in lines[1:12]:
        s = line.strip()
        if s in ("Regeste", "Regesto", "Sachverhalt", "Erwägungen") or s.startswith("Regeste"):
            break
        if not s or _REF_LINE_RE.match(s):
            continue
        kopf.append(s)
    if not kopf:
        return None, {}
    meta = parse_urteilskopf(" ".join(kopf), volume_year=volume + BGE_VOLUME_EPOCH)
    return meta.get("decision_date"), meta


def header_date_es(full_text: str, volume: int) -> date | None:
    """Kopfzeile of an entscheidsuche row: 'Bundesgericht (BGE) Band III 13.08.2024 BGE ...'."""
    m = _KOPFZEILE_DATE_RE.search((full_text or "")[:400])
    if not m:
        return None
    d = parse_date(m.group(1))
    return d if d and source_consistent(d, volume) else None


def decide(obj: dict) -> tuple[str, str | None, dict]:
    """Return (method, new_iso_or_None, extra_fields)."""
    volume = volume_of(obj.get("docket_number"), obj.get("decision_id"))
    if volume is None:
        return "unparsed", None, {}
    cur = obj.get("decision_date")
    is_es = obj.get("source") == "entscheidsuche" or str(obj.get("decision_id", "")).startswith("bge_BGE_")
    extra: dict = {}
    if is_es:
        hd = header_date_es(obj.get("full_text") or "", volume)
        method = "kopfzeile"
    else:
        hd, meta = header_date_direct(obj.get("full_text") or "", volume)
        method = "urteilskopf"
        if meta.get("docket_2") and not (obj.get("docket_number_2") or "").strip():
            extra["docket_number_2"] = meta["docket_2"]
        if meta.get("chamber") and not (obj.get("chamber") or "").strip():
            extra["chamber"] = meta["chamber"]
    if hd is not None:
        return method, hd.isoformat(), extra
    de = obj.get("date_extraction") or {}
    if de:
        # The current value is the 03-12 pass's body-text guess: strict window
        # to keep it; otherwise fall back to the value it overwrote (a source
        # value, so it gets the source gate), then to the placeholder.
        if consistent(cur, volume):
            return "keep", None, extra
        md = str(de.get("metadata_date") or "")[:10]
        if md and source_consistent(md, volume):
            return "metadata", md, extra
    elif source_consistent(cur, volume):
        # Untouched scraper / entscheidsuche value: trust it within the gate.
        return "keep", None, extra
    return "placeholder", date(volume + BGE_VOLUME_EPOCH, 1, 1).isoformat(), extra


def run(path: Path, apply: bool, examples: int) -> Counter:
    stats: Counter = Counter()
    shown = 0
    fout = None
    tmp_path = None
    if apply:
        fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
                if obj.get("court") != "bge":
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["bge_rows"] += 1
                method, new, extra = decide(obj)
                old = obj.get("decision_date")
                stats[f"method:{method}"] += 1
                changed_date = new is not None and new != old
                if changed_date:
                    volume = volume_of(obj.get("docket_number"), obj.get("decision_id"))
                    if not consistent(old, volume):
                        stats["bad_fixed"] += 1
                    elif str(old or "").endswith("-01-01"):
                        stats["placeholder_refined"] += 1
                    else:
                        stats["consistent_date_changed"] += 1
                    if shown < examples:
                        shown += 1
                        print(f"  {obj.get('decision_id'):<32} {old} -> {new}  [{method}]"
                              + (f"  +{','.join(extra)}" if extra else ""), file=sys.stderr)
                if extra:
                    stats["fields_filled"] += 1
                if changed_date or extra:
                    stats["rows_changed"] += 1
                    if fout:
                        if changed_date:
                            de = obj.get("date_extraction") or {}
                            if de and obj.get("publication_date") and \
                                    str(obj.get("publication_date"))[:10] == str(de.get("metadata_date") or "")[:10]:
                                obj["publication_date"] = None
                                stats["publication_date_reset"] += 1
                            obj["date_restore"] = {"from": old, "to": new, "method": method, "at": now}
                            obj["decision_date"] = new
                        obj.update(extra)
                        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        continue
                if fout:
                    fout.write(line)
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["rows_changed"]:
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard", type=Path, help="bge.jsonl or es_bge.jsonl")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=12)
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply, args.examples)
    for k, v in sorted(stats.items()):
        print(f"{v:>8}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
