#!/usr/bin/env python3
"""
compose_bge_trilingual_regeste.py — give the direct BGE rows (bge.jsonl) the
Regeste in all three languages, from fields they already hold.

Why (2026-10-14): scrapers/bge.py has always fetched the German, French and
Italian Regeste of every BGE (abstract_de / abstract_fr / abstract_it: 20,880
of 20,882 rows hold all three), but put only the German one into `regeste`.
The FTS index covers `regeste` and `full_text`, not the abstracts, and
get_regeste returns `regeste`; so a French or Italian query reached a BGE only
through its entscheidsuche copy (es_bge.jsonl), whose `regeste` holds all
three. That copy is what blocks retiring the feed (runbooks/bge_es_retirement.md).
The scraper now composes the field (scrapers.bge.trilingual_regeste); this
script does the same for the rows already stored. No network.

Per row (court bge, not an entscheidsuche row): regeste = German, French and
Italian Regeste joined by a blank line, when that differs from the stored
value. The stamp `regeste_restore` records the method and, unless the old value
was exactly abstract_de (the normal case, rollback = regeste := abstract_de),
the old value itself.

Streaming, temp file + atomic replace (file mode kept), DRY RUN by default,
idempotent. bge.jsonl is appended by the 09:00 federal scrape and the 10:20
late run: apply outside those and outside a full build's step 2.

Usage:
  python3 scripts/compose_bge_trilingual_regeste.py output/decisions/bge.jsonl
  python3 scripts/compose_bge_trilingual_regeste.py output/decisions/bge.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.bge import trilingual_regeste  # noqa: E402


def decide(obj: dict) -> tuple[str, str | None]:
    """(outcome, new regeste or None)."""
    if obj.get("court") != "bge" or obj.get("source") == "entscheidsuche" \
            or str(obj.get("decision_id") or "").startswith("bge_BGE_"):
        return "not_direct_bge", None
    regesten = {"de": obj.get("abstract_de"), "fr": obj.get("abstract_fr"), "it": obj.get("abstract_it")}
    have = sum(1 for v in regesten.values() if str(v or "").strip())
    if have == 0:
        return "no_abstracts", None
    new = trilingual_regeste(regesten)
    cur = obj.get("regeste") or ""
    if cur == new:
        return "already_trilingual", None
    if have == 1 and cur.strip() and len(cur) >= len(new):
        # one language only: fill an empty field (BGE 113 III 68 holds just the
        # Italian Regesto), never replace a stored Regeste with a shorter one
        return "single_language_kept", None
    return "composed", new


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
                outcome, new = decide(obj)
                stats[outcome] += 1
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<24} regeste {len(obj.get('regeste') or '')} -> "
                          f"{len(new)} chars", file=sys.stderr)
                if not fout:
                    continue
                old = obj.get("regeste")
                stamp = {"method": "trilingual_from_abstracts", "at": now}
                if old != obj.get("abstract_de"):
                    stamp["from"] = old
                    stats["old_value_kept_in_stamp"] += 1
                if obj.get("regeste_restore"):
                    stamp["prev"] = obj["regeste_restore"]
                obj["regeste_restore"] = stamp
                obj["regeste"] = new
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["rows_changed"]:
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(tmp_path, str(path))
        else:
            os.unlink(tmp_path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shard", type=Path, help="bge.jsonl (the direct shard)")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=8)
    a = ap.parse_args()
    print(f"== {a.shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
    stats = run(a.shard, a.apply, a.examples)
    print(json.dumps({"shard": str(a.shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
