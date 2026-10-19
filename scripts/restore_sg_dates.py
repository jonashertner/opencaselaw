#!/usr/bin/env python3
"""
restore_sg_dates.py — give St. Gallen rows back the date the court wrote for
the ruling itself, in the sg_publikationen and es_sg_gerichte shards.

Why (user report 2026-10-07):
  * scripts/repair_decision_dates.py (one-off, 2026-03-12, --all) rewrote SG
    rows with the first "Urteil/Entscheid vom <date>" of their text:
      sg_publikationen_VZ.2004.35 (portal Entscheiddatum 14.02.2005) became
        2004-08-10 — "Das angerufene Gericht schützte die Klage mit Urteil vom
        10. August 2004", the lower court;
      sg_gerichte_BZ.2006.83 (Kopfzeile 16.10.2007) became 2008-07-04 — "Das
        Kassationsgericht hat eine ... Nichtigkeitsbeschwerde mit Entscheid
        vom 4. Juli 2008 abgewiesen", the appeal.
    It stamped each rewritten row with `date_extraction` (metadata_date = the
    value it replaced) and moved that value into publication_date.
  * The same ruling is held twice (sg_gerichte from entscheidsuche, the direct
    sg_publikationen row): with one copy misdated, the build's cross-court dedup
    (docket + date) keeps both, and `cite` gives two dates for one ruling
    (106 dockets in both collections on Hugging Face, 105 dated differently).

The court's own statement of the date, in the row itself:
  * the citation trailer that closes every SG Regeste:
      "(Kantonsgericht, Präsidentin der III. Zivilkammer, 14. Februar 2005, VZ.2004.35)"
  * the Kopfzeile of an entscheidsuche row:
      "St.Gallen Kantonsgericht Zivilkammern (inkl. Einzelrichter) 16.10.2007 BZ.2006.83"
Both count only when they name the row's own docket. When both are present and
disagree, the row is left alone and counted as a conflict.

Per row (courts sg_*), in order:
  1. own date found, differs from decision_date, row carries the 03-12
     `date_extraction` stamp            -> decision_date = own date ("own_date")
  2. same without the stamp             -> counted ("unstamped_disagree"),
     changed only with --include-unstamped (the stored value then came from the
     portal itself; look at the examples before widening)
  3. otherwise                          -> unchanged
publication_date is cleared where the 03-12 pass planted the replaced value
there and that value is now the decision date. Every changed row gets a
`date_restore` stamp (a previous one is kept under "prev").

Once both copies carry the same date, the build's cross-court dedup
(build_fts5._cross_court_dedup, SG overlap group) folds them into one row —
the one with the full text — and records the other id as an alias.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent. Run
after publish.py exits, never during the build window.

Usage:
  python3 scripts/restore_sg_dates.py output/decisions/sg_publikationen.jsonl
  python3 scripts/restore_sg_dates.py output/decisions/es_sg_gerichte.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from models import parse_date  # noqa: E402

# "(<court>, <chamber>, 14. Februar 2005, VZ.2004.35)" — the date is the last
# comma field before the docket(s).
_TRAILER_RE = re.compile(
    r"\(([^()]{0,250}?),\s*(\d{1,2}\.\s*[A-Za-zÄÖÜäöüéû]+\s+\d{4})\s*,\s*([^(),]{2,80})\)"
)
# Kopfzeile: first line ending in "<dd.mm.yyyy> <docket>".
_KOPF_RE = re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})\s+(\S+)\s*$")


def _norm(docket: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (docket or "").upper())


def trailer_date(text: str | None, docket: str | None) -> str | None:
    """Date of the Regeste citation trailer that names ``docket``."""
    want = _norm(docket)
    if not want:
        return None
    found = set()
    for m in _TRAILER_RE.finditer(text or ""):
        if want in _norm(m.group(3)):
            d = parse_date(m.group(2))
            if d:
                found.add(d.isoformat())
    return found.pop() if len(found) == 1 else None


def kopfzeile_date(full_text: str | None, docket: str | None) -> str | None:
    """Date of the entscheidsuche Kopfzeile, when it names ``docket``."""
    first = (full_text or "").lstrip().split("\n", 1)[0]
    m = _KOPF_RE.search(first)
    if not m or _norm(m.group(2)) != _norm(docket):
        return None
    d = parse_date(m.group(1))
    return d.isoformat() if d else None


def own_date(obj: dict) -> tuple[str | None, str]:
    """(the ruling's own date, how it was found) — (None, reason) when absent
    or when the two sources disagree."""
    docket = obj.get("docket_number")
    head = (obj.get("full_text") or "")[:6000]
    tr = trailer_date(obj.get("regeste"), docket) or trailer_date(head, docket)
    kz = kopfzeile_date(obj.get("full_text"), docket)
    if tr and kz and tr != kz:
        return None, "conflict"
    if tr:
        return tr, "regeste_trailer"
    if kz:
        return kz, "kopfzeile"
    return None, "no_own_date"


def decide(obj: dict, include_unstamped: bool) -> tuple[str, str | None]:
    """(outcome, new decision_date or None)."""
    own, how = own_date(obj)
    if own is None:
        return how, None
    cur = str(obj.get("decision_date") or "")[:10]
    if cur == own:
        return "agrees", None
    if obj.get("date_extraction") or include_unstamped:
        return f"own_date:{how}", own
    return "unstamped_disagree", None


def run(path: Path, apply: bool, examples: int, include_unstamped: bool = False) -> Counter:
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
                if not str(obj.get("court") or "").startswith("sg_"):
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["sg_rows"] += 1
                outcome, new = decide(obj, include_unstamped)
                stats[outcome] += 1
                if (new or outcome == "unstamped_disagree") and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<40} {obj.get('decision_date')} -> "
                          f"{new or '(kept)'}  [{outcome}]", file=sys.stderr)
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                old = obj.get("decision_date")
                de = obj.get("date_extraction") or {}
                if de and str(obj.get("publication_date") or "")[:10] == str(de.get("metadata_date") or "")[:10] == new:
                    obj["publication_date"] = None
                    stats["publication_date_reset"] += 1
                restore = {"from": old, "to": new, "method": outcome, "at": now}
                if obj.get("date_restore"):
                    restore["prev"] = obj["date_restore"]
                obj["date_restore"] = restore
                obj["decision_date"] = new
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
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
    ap.add_argument("shard", type=Path, help="sg_publikationen.jsonl or es_sg_gerichte.jsonl")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--include-unstamped", action="store_true",
                    help="also correct rows the 2026-03-12 pass did not touch")
    ap.add_argument("--examples", type=int, default=15)
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {args.shard}", file=sys.stderr)
    stats = run(args.shard, args.apply, args.examples, args.include_unstamped)
    for k, v in sorted(stats.items()):
        print(f"{v:>8}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
