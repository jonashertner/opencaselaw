#!/usr/bin/env python3
"""
Recover the gerichte-zh.ch documents hidden behind a docket we already hold
(2026-10-02).

The scraper's id is the docket, so once a docket was held every further
document the portal lists under it was skipped. Checked against the whole
listing, document by document, with every unserved PDF read once:

  - Same PDF listed twice (579): nothing to recover.
  - Same decision, published twice (most same-date pairs): the Obergericht put
    up an edited extract ("LF180040.pdf") and the full anonymised judgment
    ("LF180040-O1.pdf") as two entries. Which of the two we hold was chance; in
    about a third of the pairs it is the extract. The fuller text replaces it,
    under the same decision_id.
  - Same decision, corrected or re-anonymised (near-identical text): the later
    upload replaces the earlier one. A re-anonymisation must not leave the
    older state in the corpus.
  - Another ruling of the same case on another day (interim order, costs,
    judgment after remand): added as its own row, id `<docket id>_d<YYYYMMDD>`,
    the form build_fts5 itself mints for a same-docket, other-date row.
  - A different ruling of the same day: listed as "held", not applied — the
    build keeps one row per court, docket and date, so it needs a decision.

Two steps, so nothing touches production until the result has been read:

    # anywhere with network (about 700 PDFs, 1.5 s apart):
    python3 scripts/backfill_zh_hidden_documents.py --build-patch patch.jsonl \\
        --shard <copy of zh_gerichte.jsonl> --listing listing.jsonl

    # on the VPS, outside the build window, AFTER migrate_zh_portal_metadata.py --apply:
    python3 scripts/backfill_zh_hidden_documents.py --apply-patch patch.jsonl            # dry run
    python3 scripts/backfill_zh_hidden_documents.py --apply-patch patch.jsonl --apply

--build-patch is resumable (it appends; documents already in the patch are
skipped). --apply-patch --apply rewrites the shard atomically with a dated
backup, appends the new ids to the scraper state and writes the document
sidecar state/zh_gerichte.docids.txt that lets the scraper tell a new document
of a held docket from one it already has.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_zh_portal_metadata import (  # noqa: E402
    _CAPTION_RE,
    CAPTION_HEAD_CHARS,
    _rows,
    doc_id_of,
    load_listing,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("backfill_zh_hidden")

SAME_RULING = 0.5       # containment at or above: two renderings of one ruling
SAME_VERSION = 0.9      # both directions at or above: corrected / re-anonymised
LONGER_BY = 1.1         # the fuller rendering replaces only when clearly fuller
_SHINGLE = 60


def _norm(text: str | None) -> str:
    return re.sub(r"\W+", "", (text or "").lower())


def _shingles(norm: str) -> set[str]:
    return {norm[i:i + _SHINGLE] for i in range(0, max(len(norm) - _SHINGLE, 1), _SHINGLE // 2)}


def overlap(a: str, b: str) -> tuple[float, float]:
    """(share of a's text found in b, share of b's text found in a), on
    normalised text. Sliding windows on the searched side, so an edit that
    shifts the text does not hide the match."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0, 0.0
    sa, sb = _shingles(na), _shingles(nb)
    in_b = sum(1 for s in sa if s in nb) / len(sa)
    in_a = sum(1 for s in sb if s in na) / len(sb)
    return in_b, in_a


def captions(text: str | None) -> set[str]:
    """The caption lines of a ruling ("Beschluss und Urteil vom 9. Juli 2014"),
    whitespace-folded. An extract has none: it opens with the Leitsatz."""
    head = (text or "")[:CAPTION_HEAD_CHARS]
    return {" ".join(m.group(0).split()).lower() for m in _CAPTION_RE.finditer(head)}


def classify(new_text: str, new_doc: str, held: dict) -> str:
    """How a hidden document relates to a held row of the same court, docket
    and date: identical | newer_version | older_version | fuller | shorter | distinct."""
    held_text = held.get("full_text") or ""
    if _norm(new_text) == _norm(held_text):
        return "identical"
    in_held, in_new = overlap(new_text, held_text)
    if max(in_held, in_new) < SAME_RULING:
        # Little shared text. Two rulings of one day each carry their own
        # caption ("Beschluss vom …" and "Urteil vom …"). A heavily edited
        # extract carries none, or the same one: then it is the same ruling
        # and the fuller text is the one to keep.
        cap_new, cap_held = captions(new_text), captions(held_text)
        if cap_new and cap_held and cap_new != cap_held:
            return "distinct"
        return "fuller" if len(new_text) > LONGER_BY * len(held_text) else "shorter"
    if min(in_held, in_new) >= SAME_VERSION:
        held_doc = doc_id_of(held)
        newer = held_doc is None or int(new_doc) > int(held_doc)
        return "newer_version" if newer else "older_version"
    return "fuller" if len(new_text) > LONGER_BY * len(held_text) else "shorter"


def dated_id(base_id: str, iso_date: str) -> str:
    """The id build_fts5 mints for a same-docket row of another date."""
    return f"{base_id}_d{iso_date.replace('-', '')}"


def build_patch(shard: Path, listing: dict[str, dict], out: Path, limit: int | None) -> Counter:
    from run_scraper import serialize_decision
    from scrapers.cantonal.zh_gerichte import ZHGerichteScraper

    stats: Counter = Counter()
    have_docs: set[str] = set()
    group: dict[tuple, list[dict]] = defaultdict(list)
    ids: set[str] = set()
    for row in _rows(shard):
        ids.add(row["decision_id"])
        doc = doc_id_of(row)
        if doc:
            have_docs.add(doc)
        group[(row.get("court"), row.get("docket_number"))].append(row)

    done: set[str] = set()
    if out.exists():
        for rec in _rows(out):
            done.add(str(rec["doc_id"]))
            # replay earlier decisions so a resumed run sees the same groups
            if rec["op"] in ("add", "replace"):
                row = rec["row"]
                key = (row["court"], row["docket_number"])
                if rec["op"] == "replace":
                    group[key] = [r for r in group[key] if r["decision_id"] != row["decision_id"]]
                group[key].append(row)
                ids.add(row["decision_id"])

    with tempfile.TemporaryDirectory() as tmp_state:
        scraper = ZHGerichteScraper(state_dir=Path(tmp_state))
        missing = sorted((s for d, s in listing.items() if d not in have_docs and d not in done),
                         key=lambda s: int(s["doc_id"]))
        log.info(f"{len(missing)} portal documents not in the shard and not yet in the patch")
        with open(out, "a", encoding="utf-8") as f:
            for n, stub in enumerate(missing):
                if limit is not None and stats["fetched"] >= limit:
                    break
                doc = str(stub["doc_id"])
                key = (stub["court_code"], stub["docket_number"])
                rows = group.get(key, [])
                rec: dict = {"doc_id": doc, "docket": stub["docket_number"], "court": stub["court_code"],
                             "date": stub["decision_date"]}
                if any(r.get("pdf_url") == stub["pdf_url"] for r in rows):
                    rec["op"] = "skip"
                    rec["why"] = "same_pdf"
                else:
                    stub_d = dict(stub, decision_date=date.fromisoformat(stub["decision_date"]))
                    decision = scraper.fetch_decision(stub_d)
                    stats["fetched"] += 1
                    text = decision.full_text if decision else ""
                    if not decision or text.startswith("[PDF text extraction failed"):
                        rec["op"] = "skip"
                        rec["why"] = "no_text"
                    else:
                        row = json.loads(serialize_decision(decision))
                        same_day = [r for r in rows if str(r.get("decision_date") or "")[:10] == stub["decision_date"]]
                        verdicts = [(classify(text, doc, r), r) for r in same_day]
                        kinds = [v for v, _ in verdicts]
                        if not rows:
                            rec["op"] = "add"
                            rec["why"] = "new_docket"
                        elif any(_norm(r.get("full_text")) == _norm(text) for r in rows):
                            # the same text listed again under another date
                            rec["op"] = "skip"
                            rec["why"] = "identical_other_date"
                        elif not same_day:
                            row["decision_id"] = dated_id(row["decision_id"], stub["decision_date"])
                            rec["op"] = "add"
                            rec["why"] = "other_date"
                        elif "identical" in kinds:
                            rec["op"] = "skip"
                            rec["why"] = "identical"
                        elif any(k in ("newer_version", "fuller") for k in kinds):
                            k, target = next((k, r) for k, r in verdicts if k in ("newer_version", "fuller"))
                            row["decision_id"] = target["decision_id"]
                            if not row.get("regeste") and target.get("regeste"):
                                row["regeste"] = target["regeste"]
                            for keep in ("previous_decision_id", "previous_id_source"):
                                if target.get(keep):
                                    row[keep] = target[keep]
                            rec["op"] = "replace"
                            rec["why"] = k
                            rec["was"] = {"doc_id": doc_id_of(target), "chars": len(target.get("full_text") or "")}
                        elif any(k in ("older_version", "shorter") for k in kinds):
                            k, target = next((k, r) for k, r in verdicts if k in ("older_version", "shorter"))
                            rec["op"] = "skip"
                            rec["why"] = k
                            # the extract's entry is where the Leitsatz lives
                            if stub.get("leitsatz") and not (target.get("regeste") or "").strip():
                                rec["op"] = "regeste"
                                rec["decision_id"] = target["decision_id"]
                                rec["regeste"] = stub["leitsatz"]
                                target["regeste"] = stub["leitsatz"]
                        else:
                            rec["op"] = "held"
                            rec["why"] = "distinct_same_day"
                            rec["against"] = [r["decision_id"] for _, r in verdicts]
                        if rec["op"] == "add" and row["decision_id"] in ids:
                            rec["op"] = "held"
                            rec["why"] = f"id_taken:{row['decision_id']}"
                        if rec["op"] in ("add", "replace"):
                            rec["row"] = row
                            rec["chars"] = len(text)
                            if rec["op"] == "replace":
                                group[key] = [r for r in rows if r["decision_id"] != row["decision_id"]]
                            group[key].append(row)
                            ids.add(row["decision_id"])
                stats[f"{rec['op']}:{rec['why']}"] += 1
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                if n % 50 == 0:
                    log.info(f"  {n}/{len(missing)} {rec['docket']} → {rec['op']} ({rec['why']})")
    return stats


def apply_patch(shard: Path, patch: Path, out=None) -> tuple[Counter, list[str], list[str]]:
    """Stream the shard with the patch applied. Returns (counters, new ids, notes)."""
    stats: Counter = Counter()
    notes: list[str] = []
    replace: dict[str, dict] = {}
    regeste: dict[str, str] = {}
    added: dict[str, dict] = {}
    for rec in _rows(patch):
        if rec["op"] == "replace":
            did = rec["row"]["decision_id"]
            if did in added:
                added[did] = rec["row"]      # fuller rendering of a row this patch adds
            else:
                replace[did] = rec["row"]    # a later version wins
        elif rec["op"] == "add":
            added[rec["row"]["decision_id"]] = rec["row"]
        elif rec["op"] == "regeste":
            if rec["decision_id"] in added:
                if not (added[rec["decision_id"]].get("regeste") or "").strip():
                    added[rec["decision_id"]]["regeste"] = rec["regeste"]
            else:
                regeste[rec["decision_id"]] = rec["regeste"]
        elif rec["op"] == "held":
            stats["held_not_applied"] += 1

    seen: set[str] = set()
    docs: set[str] = set()
    for row in _rows(shard):
        did = row["decision_id"]
        if did in replace and doc_id_of(row) != doc_id_of(replace[did]):
            new = replace[did]
            notes.append(f"replace {did}: {len(row.get('full_text') or '')} → "
                         f"{len(new.get('full_text') or '')} chars")
            row = new
            stats["replaced"] += 1
        elif did in regeste and not (row.get("regeste") or "").strip():
            row["regeste"] = regeste[did]
            stats["regeste_filled"] += 1
        seen.add(did)
        docs.add(doc_id_of(row) or "")
        if out is not None:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    stats["replace_target_missing"] = sum(1 for d in replace if d not in seen)

    new_ids: list[str] = []
    for row in added.values():
        if row["decision_id"] in seen or (doc_id_of(row) or "") in docs:
            stats["add_already_present"] += 1        # second run, or the scraper got there first
            continue
        seen.add(row["decision_id"])
        new_ids.append(row["decision_id"])
        stats["added"] += 1
        if out is not None:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats, new_ids, notes


def write_docids(shard: Path, patch: Path | None, sidecar: Path) -> int:
    """doc_id <TAB> decision_id <TAB> date <TAB> pdf_url for every document we
    hold or have looked at and decided not to store."""
    lines: dict[str, str] = {}
    for row in _rows(shard):
        doc = doc_id_of(row)
        if doc:
            lines[doc] = (f"{doc}\t{row['decision_id']}\t{str(row.get('decision_date') or '')[:10]}"
                          f"\t{row.get('pdf_url') or ''}")
    if patch is not None and patch.exists():
        for rec in _rows(patch):
            doc = str(rec["doc_id"])
            if rec["op"] in ("skip", "regeste", "held") and doc not in lines:
                lines[doc] = f"{doc}\t-\t{rec.get('date') or ''}\t"
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text("".join(v + "\n" for _, v in sorted(lines.items(), key=lambda kv: int(kv[0]))),
                   encoding="utf-8")
    os.replace(tmp, sidecar)
    return len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shard", default="output/decisions/zh_gerichte.jsonl")
    ap.add_argument("--state", default="state/zh_gerichte.jsonl")
    ap.add_argument("--listing", help="portal listing (migrate_zh_portal_metadata.py --fetch-listing)")
    ap.add_argument("--build-patch", metavar="PATH", help="fetch the hidden documents and write the patch")
    ap.add_argument("--limit", type=int, help="with --build-patch: stop after this many fetches")
    ap.add_argument("--apply-patch", metavar="PATH", help="apply a patch to the shard (dry run without --apply)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    shard = Path(args.shard)
    if not shard.exists():
        log.error(f"shard not found: {shard}")
        return 2

    if args.build_patch:
        if not args.listing:
            ap.error("--build-patch needs --listing")
        stats = build_patch(shard, load_listing(Path(args.listing)), Path(args.build_patch), args.limit)
        for k, v in sorted(stats.items()):
            log.info(f"  {k}: {v}")
        return 0

    if not args.apply_patch:
        ap.error("--build-patch or --apply-patch is required")
    patch = Path(args.apply_patch)
    if not args.apply:
        stats, new_ids, notes = apply_patch(shard, patch)
    else:
        fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            stats, new_ids, notes = apply_patch(shard, patch, out)
    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    if args.verbose:
        for n in notes:
            log.info("  " + n)
    if stats["replace_target_missing"]:
        log.error("a row to replace is not in the shard — was the metadata migration applied first? "
                  "Nothing written.")
        if args.apply:
            os.unlink(tmp)
        return 2
    if not args.apply:
        log.info("dry run — nothing written (use --apply)")
        return 0

    backup = shard.with_name(f"{shard.name}.bak-hidden-{date.today():%Y%m%d}")
    shutil.copy2(shard, backup)
    os.replace(tmp, shard)
    log.info(f"wrote {shard} (backup {backup.name})")

    state = Path(args.state)
    known = {ln.strip() for ln in open(state, encoding="utf-8")} if state.exists() else set()
    add = [i for i in new_ids if i not in known]
    if add:
        with open(state, "a", encoding="utf-8") as f:
            f.writelines(i + "\n" for i in add)
    log.info(f"state {state}: +{len(add)} ids")
    sidecar = state.with_name("zh_gerichte.docids.txt")
    log.info(f"sidecar {sidecar}: {write_docids(shard, patch, sidecar)} documents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
