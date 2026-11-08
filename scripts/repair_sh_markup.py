#!/usr/bin/env python3
"""Strip the editor markup that the Schaffhausen portal left in held rows.

Background (runbooks/tg_ge_sh_repair_2026-11.md): obergerichtsentscheide.sh.ch
serves some labels with HTML in them, and both the direct scraper and the
entscheidsuche copy stored it verbatim:

  sh_gerichte          7 dockets ("<div><span style=…>Nr. 63/2020/6</span><br></div>"),
                       the same markup inside their decision_ids, 220 titles ("… – <br>Wahl …")
  sh_obergericht (es)  7 dockets ("<p>Nr. 60/2018/23</p>", "63/2020/17<br>"), 30 regestes

The scraper now cleans the label and headline (scrapers.cantonal.sh_gerichte.
clean_html_text). This script applies the same cleaning to the held rows:

  * docket_number, title, regeste: tags become spaces, entities are decoded,
    whitespace collapses (regeste keeps its line breaks: <br> and </p> become
    newlines). The es rows keep their convention without the "Nr. " prefix.
  * a sh_gerichte row whose docket changed gets the decision_id the scraper
    now mints (make_decision_id on the clean docket), with the old id kept as
    previous_decision_id; build_fts5 records it in decision_id_aliases, so the
    old URL still resolves. It refuses if the new id is already held.
  * es rows keep their decision_ids: three of the seven polluted ones already
    have a clean twin in the same shard (sh_obergericht_60_2018_23 next to
    sh_obergericht__p_Nr._60_2018_23__p_), which the build's dedup then folds.

DRY RUN by default. --apply hard-links <shard>.bak-sh-markup-<UTC date> beside
each shard, writes <shard>.tmp (neither matches the build's *.jsonl glob),
checks the line count and that the shard did not change while it ran, and
swaps the new file in atomically.

Usage:
  python3 scripts/repair_sh_markup.py
  python3 scripts/repair_sh_markup.py --shard output/decisions/sh_gerichte.jsonl \
      --shard output/decisions/es_sh_obergericht.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bs4 import BeautifulSoup  # noqa: E402

from models import make_decision_id  # noqa: E402
from scrapers.cantonal.sh_gerichte import clean_html_text  # noqa: E402

DEFAULT_SHARDS = [REPO / "output" / "decisions" / "sh_gerichte.jsonl",
                  REPO / "output" / "decisions" / "es_sh_obergericht.jsonl"]
_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")


def clean_multiline(value: str | None) -> str | None:
    """Like clean_html_text, but <br> and block ends become line breaks."""
    if not value or not (_TAG_RE.search(value) or "&" in value):
        return value
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li)>", "\n", value)
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln) or None


def repair(rec: dict, held_ids: set[str]) -> tuple[dict, list[str]]:
    """(the repaired record, names of the fields changed)."""
    new = dict(rec)
    court = rec.get("court") or ""
    changed: list[str] = []
    docket = rec.get("docket_number") or ""
    if _TAG_RE.search(docket) or "&nbsp;" in docket:
        clean = clean_html_text(docket)
        if court == "sh_obergericht":
            clean = re.sub(r"^Nr\.\s*", "", clean)
        new["docket_number"] = clean
        changed.append("docket_number")
        if court == "sh_gerichte":
            new_id = make_decision_id("sh_gerichte", clean)
            if new_id != rec["decision_id"]:
                if new_id in held_ids:
                    raise SystemExit(f"refusing: {rec['decision_id']} -> {new_id}, which is already held")
                new["decision_id"] = new_id
                new["previous_decision_id"] = rec["decision_id"]
                new["previous_id_source"] = "sh_markup_repair"
                changed.append("decision_id")
    title = rec.get("title")
    if title and (_TAG_RE.search(title) or "&nbsp;" in title or "&amp;" in title):
        new["title"] = clean_html_text(title)
        changed.append("title")
    regeste = rec.get("regeste")
    if regeste and _TAG_RE.search(regeste):
        new["regeste"] = clean_multiline(regeste)
        changed.append("regeste")
    return new, changed


def run(shard: Path, apply: bool, examples: int = 0) -> Counter:
    st = os.stat(shard)
    with open(shard, "rb") as fh:
        raws = fh.readlines()
    recs = []
    for raw in raws:
        try:
            recs.append(json.loads(raw) if raw.strip() else None)
        except ValueError:
            recs.append(None)
    held_ids = {r["decision_id"] for r in recs if isinstance(r, dict) and r.get("decision_id")}
    stats: Counter = Counter()
    out = []
    shown = 0
    for raw, rec in zip(raws, recs):
        stats["lines"] += 1
        if not isinstance(rec, dict):
            out.append(raw)
            continue
        new, changed = repair(rec, held_ids)
        if not changed:
            out.append(raw)
            continue
        stats["rows_changed"] += 1
        for k in changed:
            stats[f"changed:{k}"] += 1
        if "decision_id" in changed:
            held_ids.add(new["decision_id"])
        if shown < examples:
            shown += 1
            print(f"  {rec['decision_id'][:70]!r} -> {new['decision_id'][:70]!r}: "
                  f"{rec.get('docket_number', '')[:60]!r} -> {new.get('docket_number')!r} {changed}",
                  file=sys.stderr)
        out.append(json.dumps(new, ensure_ascii=False).encode("utf-8") + b"\n")
    if apply and stats["rows_changed"]:
        tmp = shard.with_name(shard.name + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.writelines(out)
                fh.flush()
                os.fsync(fh.fileno())
            with open(tmp, "rb") as fh:
                written = sum(1 for _ in fh)
            if written != stats["lines"]:
                raise SystemExit(f"refusing: wrote {written} lines, read {stats['lines']}")
            now = os.stat(shard)
            if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise SystemExit(f"refusing: {shard} changed while the repair ran (a scrape?)")
            stamp = time.strftime("%Y%m%d", time.gmtime())
            backup = shard.with_name(f"{shard.name}.bak-sh-markup-{stamp}")
            if backup.exists():
                raise SystemExit(f"refusing to overwrite existing backup {backup}")
            os.link(shard, backup)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.replace(tmp, shard)
            print(f"rewrote {shard}; backup {backup}", file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", type=Path, action="append")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args(argv)
    for shard in args.shard or DEFAULT_SHARDS:
        if not shard.exists():
            print(f"shard not found: {shard}", file=sys.stderr)
            return 2
        print(("APPLY" if args.apply else "DRY RUN") + f": {shard}", file=sys.stderr)
        print(json.dumps(dict(sorted(run(shard, args.apply, args.examples).items())), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
