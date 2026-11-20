#!/usr/bin/env python3
"""Fold the SEM Weisungen file-rename twins and fix their language labels.

SEM renamed its PDFs ("20020708-rs-fza.pdf" -> "20020708-rs-fza-d.pdf",
"rs-ch-fl" -> "rs-CH-FL-d"). The scraper took the file name as identity, so
the append-only shard holds both names as separate documents: 37 twins with
identical text (user report 2026-11-20). scrapers/practice/sem_weisungen.py
now drops the language suffix from the identity; this script brings the
stored rows in line with it:

  * every line gets the id the fixed scraper computes from its pdf_url;
  * lines sharing an id collapse to the most recently scraped one;
  * the language label follows the text (text_language), as in the scraper.

Dry run by default: prints what would change. --apply writes a backup next to
the shard (<shard>.bak-<UTC stamp>) and replaces the shard atomically. Run it
when no practice scrape or practice-DB build is running; the next practice DB
build picks it up.

    python3 scripts/repair_sem_weisungen_twins.py output/practice/sem_weisungen.jsonl
    python3 scripts/repair_sem_weisungen_twins.py output/practice/sem_weisungen.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice.base import slugify  # noqa: E402
from scrapers.practice.sem_weisungen import file_identity, text_language  # noqa: E402


def normalized(rec: dict) -> dict:
    doc_number, file_lang = file_identity(rec.get("pdf_url") or rec.get("doc_number") or "")
    lang = file_lang or "de"
    doc_id = f"sem_weisungen_{slugify(doc_number)}"
    if lang != "de":
        doc_id = f"{doc_id}_{lang}"
    out = dict(rec)
    out["doc_id"] = doc_id
    out["doc_number"] = doc_number
    body = rec.get("body_text") or ""
    out["language"] = text_language(body, lang) if body else lang
    return out


def repair(lines: list[str]) -> tuple[list[str], dict]:
    recs = [json.loads(line) for line in lines if line.strip()]
    norm = [normalized(r) for r in recs]
    keep: dict[str, int] = {}
    for i, r in enumerate(norm):
        prev = keep.get(r["doc_id"])
        if prev is None or (r.get("scraped_at") or "") >= (norm[prev].get("scraped_at") or ""):
            keep[r["doc_id"]] = i
    kept = sorted(keep.values())
    stats = {
        "lines_in": len(recs),
        "lines_out": len(kept),
        "ids_in": len({r["doc_id"] for r in recs}),
        "ids_out": len(keep),
        "renamed": sum(1 for i in kept if norm[i]["doc_id"] != recs[i]["doc_id"]),
        "relabelled": [(norm[i]["doc_id"], recs[i].get("language"), norm[i]["language"])
                       for i in kept if norm[i]["language"] != recs[i].get("language")],
        "dropped": [recs[i]["doc_id"] for i in range(len(recs)) if i not in set(kept)],
        "text_differs": [],
    }
    by_id: dict[str, set] = {}
    for r, n in zip(recs, norm):
        by_id.setdefault(n["doc_id"], set()).add(r.get("content_hash"))
    stats["text_differs"] = sorted(k for k, v in by_id.items() if len(v) > 1)
    out = [json.dumps(norm[i], ensure_ascii=False) + "\n" for i in kept]
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shard", type=Path)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    lines = a.shard.read_text(encoding="utf-8").splitlines(keepends=True)
    out, st = repair(lines)
    print(f"lines {st['lines_in']} -> {st['lines_out']}, ids {st['ids_in']} -> {st['ids_out']}, "
          f"rows renamed {st['renamed']}")
    for d in st["dropped"]:
        print("  drop", d)
    for did, old, new in st["relabelled"]:
        print(f"  language {did}: {old} -> {new}")
    if st["text_differs"]:
        print("  NOTE folded ids whose stored texts differ (newest kept):", st["text_differs"])
    if not a.apply:
        print("dry run; pass --apply to write")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = a.shard.with_name(a.shard.name + f".bak-{stamp}")
    shutil.copy2(a.shard, backup)
    tmp = a.shard.with_name(a.shard.name + ".tmp")
    tmp.write_text("".join(out), encoding="utf-8")
    os.replace(tmp, a.shard)
    print(f"written; backup {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
