#!/usr/bin/env python3
"""
Bring the gerichte-zh.ch shard in line with what the portal says about each
decision (2026-10-02).

The portal's listing is the ground truth for a decision's metadata. A
whole-population comparison (37,336 portal entries against the served rows)
found the shard wrong or silent on four things, all fixed here from the
listing, never from guesses:

  1. Deciding court.  Every district's Arbeitsgericht / Mietgericht ruling was
     filed as zh_arbeitsgericht / zh_mietgericht (the Zürich courts) with the
     chamber dropped: 58 rulings of ten district courts. They move to their
     Bezirksgericht with the division as chamber; the id is re-minted and the
     old one kept in previous_decision_id, which build_fts5 records in
     decision_id_aliases so every existing link keeps resolving.

  2. Chamber.  "-" (the portal's "none") was stored as a chamber on ~6,300
     rows; it becomes NULL. Other chambers are taken from the listing.

  3. Leitsatz.  2,815 portal entries carry a headnote; none was ever stored
     (the scraper looked for an <em> the portal does not emit). It becomes the
     regeste where the row has none.

  4. Verweise.  4,374 entries say what happened next ("Weiterzug ans
     Bundesgericht, 6B_122/2024"); the value was read and thrown away. It
     becomes appeal_info where the row has none.

  5. Decision date.  368 served dates differ from the portal's. The judgment's
     own caption ("Urteil vom 10. Februar 2022") decides: in 267 it confirms
     our date and the portal's entry is the wrong one, so nothing changes; in
     28 it confirms the portal (typically a year off: 2020-02-10 served for a
     2022 judgment) and the date is corrected. Where the caption gives neither
     date, or there is none, the row is left as it is.

A row is matched to its portal entry by the TYPO3 document id in external_id
("zh_gerichte_<doc_id>"), never by docket: one docket can hold several
documents. Rows without that id (federation leftovers, the AGer-Z yearbooks)
are left alone.

De-listing.  Rows whose document is no longer in the listing are reported
("not on portal") and kept. --drop-docs removes the named ones, from the shard
and from the scraper state, and refuses any document the listing still shows.
That is a maintainer decision under docs/governance-and-removal-policy.md.

Usage (on the VPS, outside the build window, after `git merge --ff-only`):
    python3 scripts/migrate_zh_portal_metadata.py --fetch-listing listing.jsonl
    python3 scripts/migrate_zh_portal_metadata.py --listing listing.jsonl            # dry run
    python3 scripts/migrate_zh_portal_metadata.py --listing listing.jsonl --apply

--fetch-listing is one pass over the portal's search (about 35 requests, 2 s
apart, no PDFs). --apply rewrites the shard atomically, keeps a dated backup
next to it and appends the re-minted ids to the scraper state.
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
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import make_decision_id

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("migrate_zh_portal")

_EXT_RE = re.compile(r"^zh_gerichte_(\d+)$")
_SRC_RE = re.compile(r"entscheidDrucken\]=(\d+)$")
_PLACEHOLDER_REGESTE = "[PDF text extraction failed"

# The caption of a Zürich ruling: "Urteil vom 10. Februar 2022",
# "Beschluss und Teilurteil vom 6. Februar 2025", "Beschluss vom 20.06.2013".
# Anchored to a line of its own, so a date quoted in the reasoning ("mit
# Verfügung vom 20. Juni 2013 trat die Einzelrichterin …") is not a caption.
_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
           "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
           "november": 11, "dezember": 12}
_CAPTION_RE = re.compile(
    r"^[ \t]*(?:(?:Teil|Zwischen|Vor|End)?(?:[Uu]rteil|[Bb]eschluss|[Vv]erfügung(?:en)?|[Ee]ntscheid)"
    r"(?:[ \t]+und[ \t]+)?){1,2}[ \t]+vom[ \t]+"
    r"(\d{1,2})\.[ \t]*(?:(\d{1,2})\.|([A-Za-zä]+))[ \t]*((?:19|20)\d{2})[ \t]*$",
    re.MULTILINE,
)
CAPTION_HEAD_CHARS = 4000


def caption_dates(full_text: str | None) -> set[str]:
    """ISO dates of the caption lines in the head of a ruling."""
    out: set[str] = set()
    for m in _CAPTION_RE.finditer((full_text or "")[:CAPTION_HEAD_CHARS]):
        month = int(m.group(2)) if m.group(2) else _MONTHS.get(m.group(3).lower())
        if not month:
            continue
        try:
            out.add(date(int(m.group(4)), month, int(m.group(1))).isoformat())
        except ValueError:
            continue
    return out


def fetch_listing(out: Path, delay: float = 2.0) -> int:
    """Walk the portal's search once and write one stub per entry."""
    import requests

    from scrapers.cantonal.zh_gerichte import (
        EARLY_START, FIXED_PARAMS, LIVESEARCH_URL, TAG_SCHRITTE, ZHGerichteScraper,
    )

    parser = ZHGerichteScraper.__new__(ZHGerichteScraper)   # parsing only, no state
    session = requests.Session()
    session.headers["User-Agent"] = "OpenCaseLaw (opencaselaw.ch) metadata check"
    windows = [("", "01.01.1980")]
    von, today = EARLY_START, date.today()
    while von <= today:
        bis = min(von + timedelta(days=TAG_SCHRITTE - 1), today)
        windows.append((von.strftime("%d.%m.%Y"), bis.strftime("%d.%m.%Y")))
        von = bis + timedelta(days=1)

    n = 0
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for v, b in windows:
            params = dict(FIXED_PARAMS, entscheiddatum_von=v, entscheiddatum_bis=b)
            resp = session.get(LIVESEARCH_URL, params=params, timeout=120)
            resp.raise_for_status()
            declared = re.search(r'id="entscheideText">\s*<strong>(\d+)</strong>', resp.text)
            entries = len(re.findall(r'class="entscheid entscheid_nummer_', resp.text))
            # A window the portal says holds N entries must give N: a silently
            # short listing would leave rows unmigrated without a trace.
            if declared and int(declared.group(1)) != entries:
                raise SystemExit(f"window {v}–{b}: portal declares {declared.group(1)}, got {entries}")
            for stub in parser._parse_window(resp.text, f"{v}–{b}"):
                stub["decision_date"] = stub["decision_date"].isoformat()
                f.write(json.dumps(stub, ensure_ascii=False) + "\n")
                n += 1
            log.info(f"  {v or '…'}–{b}: {entries} entries")
            time.sleep(delay)
    os.replace(tmp, out)
    return n


def load_listing(path: Path) -> dict[str, dict]:
    listing: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                stub = json.loads(line)
                listing[str(stub["doc_id"])] = stub
    return listing


def doc_id_of(row: dict) -> str | None:
    """The portal's document id: external_id, else the print-view source_url
    (both are written by the scraper from the same listing entry)."""
    m = _EXT_RE.match(row.get("external_id") or "")
    if not m:
        m = _SRC_RE.search(row.get("source_url") or "")
    return m.group(1) if m else None


def target(row: dict, stub: dict, taken: set[str]) -> tuple[dict, list[str]]:
    """Changes the listing asks for on one row: ({field: value}, [reasons])."""
    changes: dict = {}
    why: list[str] = []

    court, chamber = stub["court_code"], stub.get("chamber")
    if court != row.get("court"):
        new_id = make_decision_id(court, row.get("docket_number") or "")
        if new_id in taken:
            # Another row already holds that id (a second document of the
            # docket). Re-keying would drop one of them; leave it for the
            # document-aware identity pass.
            why.append(f"court_blocked:{new_id}")
        else:
            changes.update(court=court, decision_id=new_id,
                           previous_decision_id=row["decision_id"],
                           previous_id_source="zh_deciding_court_2026_10")
            why.append(f"court:{row.get('court')}→{court}")
    if "court_blocked" not in " ".join(why) and (chamber or None) != (row.get("chamber") or None):
        changes["chamber"] = chamber
        why.append("chamber")

    regeste = (row.get("regeste") or "").strip()
    if stub.get("leitsatz") and (not regeste or regeste.startswith(_PLACEHOLDER_REGESTE)):
        changes["regeste"] = stub["leitsatz"]
        why.append("regeste")
    if stub.get("verweise") and not (row.get("appeal_info") or "").strip():
        changes["appeal_info"] = stub["verweise"]
        why.append("appeal_info")
    if stub.get("entscheidart") and not (row.get("decision_type") or "").strip():
        changes["decision_type"] = stub["entscheidart"]
        why.append("decision_type")

    portal_date = stub.get("decision_date")
    if portal_date and str(row.get("decision_date") or "")[:10] != portal_date:
        captions = caption_dates(row.get("full_text"))
        # Only when the ruling's caption names the portal's date and nothing
        # else: two independent sources against the stored one.
        if captions == {portal_date}:
            changes["decision_date"] = portal_date
            why.append("decision_date")
        else:
            why.append("date_differs_kept")
    return changes, why


def _rows(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def migrate(shard: Path, listing: dict[str, dict], out=None,
            drop_docs: frozenset[str] = frozenset(),
            dropped_ids: list[str] | None = None) -> tuple[Counter, list[str], list[str]]:
    """Stream the shard; write the migrated rows to `out` when given.
    Returns (counters, notes, re-minted ids). Ids of de-listed rows are
    appended to `dropped_ids`."""
    stats: Counter = Counter()
    notes: list[str] = []
    new_ids: list[str] = []
    still_listed = sorted(d for d in drop_docs if d in listing)
    if still_listed:
        raise SystemExit(f"--drop-docs: still on the portal, refusing: {', '.join(still_listed)}")

    taken: set[str] = set()
    doc_of_id: dict[str, str | None] = {}
    for r in _rows(shard):
        taken.add(r["decision_id"])
        doc_of_id[r["decision_id"]] = doc_id_of(r)
    # A scrape that ran with the new court mapping before this migration has
    # already fetched a mis-filed document again under its right id. Then the
    # old row is that same document a second time: it is dropped and the
    # right row inherits its id as the alias.
    superseded: dict[str, str] = {}      # old id -> right id
    for r in _rows(shard):
        doc = doc_id_of(r)
        stub = listing.get(doc) if doc else None
        if not stub or stub["court_code"] == r.get("court"):
            continue
        right = make_decision_id(stub["court_code"], r.get("docket_number") or "")
        if right != r["decision_id"] and doc_of_id.get(right) == doc:
            superseded[r["decision_id"]] = right
    alias_for = {right: old for old, right in superseded.items()}

    for row in _rows(shard):
        stats["rows"] += 1
        if row["decision_id"] in superseded:
            stats["dropped_refetched_twin"] += 1
            notes.append(f"drop {row['decision_id']} (same document as {superseded[row['decision_id']]})")
            continue
        if row["decision_id"] in alias_for and not row.get("previous_decision_id"):
            row["previous_decision_id"] = alias_for[row["decision_id"]]
            row["previous_id_source"] = "zh_deciding_court_2026_10"
            stats["alias_added"] += 1
        doc = doc_id_of(row)
        stub = listing.get(doc) if doc else None
        if doc and doc in drop_docs:
            stats["delisted"] += 1
            notes.append(f"drop {row['decision_id']} (doc {doc}, withdrawn at the source)")
            if dropped_ids is not None:
                dropped_ids.append(row["decision_id"])
            continue
        if not doc:
            stats["no_portal_doc_id"] += 1
        elif not stub:
            stats["not_on_portal"] += 1
            notes.append(f"not on portal: {row['decision_id']} (doc {doc})")
        else:
            changes, why = target(row, stub, taken)
            for w in why:
                stats[w.split(":")[0]] += 1
            if "decision_date" in changes:
                notes.append(f"date {row['decision_id']}: {row.get('decision_date')} → "
                             f"{changes['decision_date']} (caption and portal agree)")
            if "decision_id" in changes:
                taken.add(changes["decision_id"])
                new_ids.append(changes["decision_id"])
                notes.append(f"refile {row['decision_id']} → {changes['decision_id']}")
            for w in why:
                if w.startswith("court_blocked"):
                    notes.append(f"blocked {row['decision_id']} → {w.split(':', 1)[1]} (id taken)")
            if changes:
                stats["rows_changed"] += 1
                row.update(changes)
        if out is not None:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats, notes, new_ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shard", default="output/decisions/zh_gerichte.jsonl")
    ap.add_argument("--state", default="state/zh_gerichte.jsonl")
    ap.add_argument("--listing", help="portal listing written by --fetch-listing")
    ap.add_argument("--fetch-listing", metavar="PATH", help="walk the portal and write the listing here")
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--drop-docs", default="", metavar="IDS",
                    help="comma-separated portal document ids to de-list (must be gone from the listing)")
    ap.add_argument("--verbose", action="store_true", help="print every re-filed row")
    args = ap.parse_args()

    if args.fetch_listing:
        n = fetch_listing(Path(args.fetch_listing))
        log.info(f"{args.fetch_listing}: {n} portal entries")
        return 0
    if not args.listing:
        ap.error("--listing or --fetch-listing is required")

    shard = Path(args.shard)
    if not shard.exists():
        log.error(f"shard not found: {shard}")
        return 2
    listing = load_listing(Path(args.listing))
    log.info(f"listing: {len(listing)} portal entries")
    if len(listing) < 30_000:
        log.error("listing holds fewer than 30,000 entries — refusing (incomplete fetch?)")
        return 2

    drop_docs = frozenset(d.strip() for d in args.drop_docs.split(",") if d.strip())
    dropped_ids: list[str] = []
    if not args.apply:
        stats, notes, _ = migrate(shard, listing, drop_docs=drop_docs)
    else:
        fd, tmp = tempfile.mkstemp(dir=str(shard.parent), prefix=shard.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            stats, notes, new_ids = migrate(shard, listing, out, drop_docs, dropped_ids)

    for k, v in sorted(stats.items()):
        log.info(f"  {k}: {v}")
    for n in notes:
        if args.verbose or n.startswith(("blocked", "not on portal", "drop", "date")):
            log.info("  " + n)

    if not args.apply:
        log.info("dry run — nothing written (use --apply)")
        return 0

    backup = shard.with_name(f"{shard.name}.bak-portalmeta-{date.today():%Y%m%d}")
    shutil.copy2(shard, backup)
    os.replace(tmp, shard)
    log.info(f"wrote {shard} (backup {backup.name})")

    state = Path(args.state)
    known: set[str] = set()
    if state.exists():
        known = {ln.strip() for ln in open(state, encoding="utf-8") if ln.strip()}
    if dropped_ids and state.exists():
        # A de-listed id leaves the state too, so a later re-publication is
        # discovered as new (same rule as runbooks/bger_withdrawn_decisions.md).
        gone = set(dropped_ids)
        kept = [ln for ln in open(state, encoding="utf-8") if ln.strip() not in gone]
        tmp_state = state.with_name(state.name + ".tmp")
        tmp_state.write_text("".join(kept), encoding="utf-8")
        os.replace(tmp_state, state)
        known -= gone
        log.info(f"state {state}: -{len(gone)} de-listed ids")
    add = [i for i in new_ids if i not in known]
    if add:
        with open(state, "a", encoding="utf-8") as f:
            f.writelines(i + "\n" for i in add)
    log.info(f"state {state}: +{len(add)} ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
