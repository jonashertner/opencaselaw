#!/usr/bin/env python3
"""Give the 2007-2017 VPB rows their item number as docket, and every ch_vb row its real language.

After scripts/ch_vb_keep_vpb.py the shard holds 2,502 VPB/JAAC PDFs. 2,386 carry a
'JAAC <vol>.<n>' docket (1987-2006); the 116 from 2007-2017 carry only the portal id
('150000032'), so they cite as a number nobody can look up. Each of those PDFs prints its
item number in the header, next to its page range: '2007.10 (S. 199–212)',
'2007.3.2 (p. 98–121)', '2009.10a (S. 141-177)'. The first such number in the header is
the row's docket, written 'JAAC <n>' like the rest of the shard (mcp_server renders VPB /
JAAC / GAAC per language). A number two rows would share (a misprinted header, an annex
'Ergänzung zu Beitrag 2009.6') is left alone and listed: which row owns it is a reading,
not a pattern.

Every row is tagged 'de' by the entscheidsuche feed; about half are French. A row is
relabelled only when one language's function words clearly dominate its full text
(LANG_SHARE of the counted words and LANG_MARGIN times the runner-up); the trilingual
Regesten at the top of each item keep the uncertain ones at their current tag.

decision_id never changes. Dry run by default; --apply keeps a backup next to the shard,
leaves every untouched line byte-identical and replaces the shard atomically. Run it
outside the scrape and publish steps (the 01:00 UTC scrape, the 03:30 UTC build, the
20:00 UTC incremental). Row count is unchanged, so no swap-gate exemption is needed.

  python3 scripts/ch_vb_vpb_dockets.py            # list what would change
  python3 scripts/ch_vb_vpb_dockets.py --apply    # write it
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SHARD = REPO / "output" / "decisions" / "es_ch_vb.jsonl"
BACKUP_SUFFIX = ".bak-pre-vpb-dockets"
HEADER_CHARS = 1500
LANG_SHARE = 0.6
LANG_MARGIN = 2.0

_PORTAL_DOCKET = re.compile(r"^\d+$")
# '2007.10 (S. 199', '2007.3.2 (p. 98', '2009.10a (S. 141', '2012.4 (pag. 55'
_ITEM = re.compile(r"(?<![\d.])((?:19|20)\d{2}\.\d{1,3}(?:\.\d{1,2}|[a-z])?)\s*\((?:S|p|pp|pag)\.\s*\d")
_WORD = re.compile(r"[a-zàâäéèêëîïôöùûüç]+")
_FUNCTION_WORDS = {
    "de": set("der die und des den nicht ist für mit von dass werden wird auf eine einer".split()),
    "fr": set("le la les des et est pour une dans que qui du sur pas par aux".split()),
    "it": set("il della che per una dei delle con non sono nel alla gli degli".split()),
}


def item_number(text: str) -> str | None:
    """The item number the PDF prints in its header, or None."""
    m = _ITEM.search((text or "")[:HEADER_CHARS])
    return m.group(1) if m else None


def language_of(text: str) -> str | None:
    """'de' / 'fr' / 'it' when one language clearly dominates, else None."""
    counts = Counter()
    for w in _WORD.findall((text or "").lower()):
        for lang, words in _FUNCTION_WORDS.items():
            if w in words:
                counts[lang] += 1
    ranked = counts.most_common()
    if not ranked:
        return None
    best, n = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if n < LANG_SHARE * sum(counts.values()) or n < LANG_MARGIN * runner_up:
        return None
    return best


def plan(records: list[dict]) -> tuple[dict[int, dict], list[tuple[str, str]], list[str]]:
    """(index -> fields to set, collisions as (decision_id, number), ids with no number)."""
    taken = {str(r.get("docket_number")) for r in records}
    proposed: dict[int, str] = {}
    unnumbered: list[str] = []
    for i, r in enumerate(records):
        if not _PORTAL_DOCKET.match(str(r.get("docket_number") or "")):
            continue
        n = item_number(r.get("full_text") or "")
        if n:
            proposed[i] = f"JAAC {n}"
        else:
            unnumbered.append(r.get("decision_id", ""))
    owners = Counter(proposed.values())
    collisions = [(records[i].get("decision_id", ""), d) for i, d in proposed.items()
                  if owners[d] > 1 or d in taken]
    changes: dict[int, dict] = {}
    for i, d in proposed.items():
        if owners[d] == 1 and d not in taken:
            changes.setdefault(i, {})["docket_number"] = d
    for i, r in enumerate(records):
        lang = language_of(r.get("full_text") or "")
        if lang and lang != r.get("language"):
            changes.setdefault(i, {})["language"] = lang
    return changes, sorted(collisions), unnumbered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", default=str(DEFAULT_SHARD))
    ap.add_argument("--apply", action="store_true", help="write the corrected shard (default: dry run)")
    args = ap.parse_args(argv)

    shard = Path(args.shard)
    # Iterate the file, not str.splitlines(): the OCR text carries U+2028 / U+0085,
    # which splitlines() treats as line ends inside a JSON string.
    with open(shard, encoding="utf-8", newline="") as f:
        lines = [l for l in f if l.strip()]
    records = [json.loads(l) for l in lines]
    changes, collisions, unnumbered = plan(records)

    dockets = sum(1 for c in changes.values() if "docket_number" in c)
    langs = Counter((records[i].get("language"), c["language"]) for i, c in changes.items() if "language" in c)
    print(f"docket  {dockets} portal-id rows -> 'JAAC <n>'")
    for did, d in collisions:
        print(f"skip    {did}: {d} is claimed twice, left as is")
    for did in unnumbered:
        print(f"skip    {did}: no item number in the header")
    for (old, new), n in sorted(langs.items(), key=lambda kv: -kv[1]):
        print(f"lang    {old} -> {new}: {n} rows")
    print(f"rows {len(lines)}, {len(changes)} to change")
    if not args.apply:
        print("dry run: nothing written")
        return 0
    if not changes:
        print("nothing to change")
        return 0
    backup = shard.with_name(shard.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(shard, backup)
    tmp = shard.with_name(shard.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines):
            if i in changes:
                rec = {**records[i], **changes[i]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                f.write(line)
    os.replace(tmp, shard)
    print(f"applied: {shard}, {len(changes)} rows changed; backup {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
