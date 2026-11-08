#!/usr/bin/env python3
"""
restore_lu_dates.py — give the Luzern rows back the Entscheiddatum the portal
states for them.

Why (found 2026-11-08 while checking LU freshness):
  * The scraper (scrapers/cantonal/lu_gerichte.py) takes decision_date from the
    portal's own "Entscheiddatum:" field (gerichte.lu.ch/recht_sprechung/lgve/
    Ajax?EnId=<id>) and writes no publication_date. The entscheidsuche copies
    (es_lu_gerichte.jsonl) open with the portal's header, which states the same
    day: "Luzern Kantonsgericht 4. Abteilung 19.07.2013 V 13 31".
  * scripts/repair_decision_dates.py --all (one-off, 2026-03-12) took that value
    for a publication date: it moved it into publication_date, put the first
    "Urteil/Entscheid vom <date>" of the text into decision_date (in a Luzern
    ruling mostly the decision under appeal) and stamped the row with
    `date_extraction` (metadata_date = the value it replaced). EnId 11092
    (7H 24 52): portal 14.01.2025, served 2024-02-22; 11079: portal
    19.05.2025, served 2016-06-29. 1,554 of 4,055 direct rows and 391 of 3,944
    es rows carry the stamp; rows scraped after 2026-03-12 are fine.

Per row:
  * direct (id lu_gerichte_<EnId>), stamped, publication_date still equal to
    date_extraction.metadata_date: that value is the scraper's, the portal's
    Entscheiddatum -> decision_date = it, publication_date cleared ("restored";
    "agrees_pub_cleared" where decision_date already held it). With --portal
    TSV (from --fetch-portal) a re-read portal value wins over the stamp,
    provided the portal's Fallnummer is still the row's docket.
  * es copy (source entscheidsuche), stamped, header date for the row's own
    docket == metadata_date == publication_date -> the same ("es_restored").
  * anything else -> unchanged ("unstamped", "unprovable", ...).
Every changed row gets a `date_restore` stamp {from, to, method, at,
publication_date: {from, to}} (a previous stamp is kept under "prev"), so the
change is exactly reversible; `date_extraction` stays as provenance.

Both shards are planned together and must be repaired together: with only one
side restored, a direct row and its es copy carry two dates and the build
serves the ruling twice. Once both carry the portal date, build Pass 1
(court|docket|date) folds each pair and keeps the longer row (mostly the es
copy, which prepends header and Regeste to the same text): the court shrinks
by about 1,190 rows — a per-court swap-gate event, see the runbook.

The portal publishes one ruling as several LGVE entries (EnIds 10968 and 10969
are two Leitsätze of 7Q 20 4 of 3 June 2022). A restoration whose new key
would make Pass 1 delete a row whose text the kept row does not contain is
held ("held_dedup_collision", unchanged). The run refuses to write if any such
loss remains (plan() computes Pass 1's losses before and after).

Writing: every temp file (<shard>.tmp) is written and checked first (only
decision_date / publication_date / date_restore differ; same line count),
then each shard is hard-linked to <shard>.bak-<UTC date> (neither name matches
the build's *.jsonl glob) and swapped in atomically, one right after the
other. It refuses if a shard changed during the run.

DRY RUN by default, idempotent. Apply only while nothing appends to or reads
the shards (after the 01:00 scrape logged Done., before the 03:30 build): see
runbooks/lu_dates_2026-11-08.md.

Usage:
  python3 scripts/restore_lu_dates.py output/decisions/lu_gerichte.jsonl \
      output/decisions/es_lu_gerichte.jsonl [--apply] [--examples 20]
  # optional ground-truth re-read (one request per second):
  python3 scripts/restore_lu_dates.py output/decisions/lu_gerichte.jsonl \
      --fetch-portal lu_portal_dates.tsv [--fetch-all-rows | --sample N]
  python3 scripts/restore_lu_dates.py output/decisions/lu_gerichte.jsonl \
      output/decisions/es_lu_gerichte.jsonl --portal lu_portal_dates.tsv
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from build_fts5 import (
    _dedup_docket,
    _federation_header_date,
    _strip_federation_header,
    _window_share,
)
from models import make_canonical_key

COURT = "lu_gerichte"
METHOD_STAMP = "lu_portal_date_from_stamp"
METHOD_PORTAL = "lu_portal_reread"
METHOD_ES_HEADER = "lu_portal_date_es_header"
PORTAL_URL = "https://gerichte.lu.ch/recht_sprechung/lgve/Ajax"
INVALID_SIZE = 1995  # the portal's "no such EnId" page (scraper constant)
_ID_RE = re.compile(r"^lu_gerichte_(\d+)$")
_DMY_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Fields this script may change; every other key must be byte-identical.
CHANGED_FIELDS = ("decision_date", "publication_date", "date_restore",
                  "previous_decision_id", "previous_id_source")
ALIAS_SOURCE = "lu_date_fold"


# ── helpers ─────────────────────────────────────────────────────────────

def _iso(value) -> str | None:
    """YYYY-MM-DD of a stored date value, or None if it is not one."""
    s = str(value or "")[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def en_id(rec: dict) -> int | None:
    m = _ID_RE.match(str(rec.get("decision_id") or ""))
    return int(m.group(1)) if m else None


def is_direct(rec: dict) -> bool:
    return (rec.get("court") == COURT and rec.get("source") != "entscheidsuche"
            and en_id(rec) is not None)


def is_es_copy(rec: dict) -> bool:
    return rec.get("court") == COURT and rec.get("source") == "entscheidsuche"


def _norm_docket(d: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (d or "").upper())


def dedup_key(rec: dict) -> str:
    return make_canonical_key(rec.get("court") or "", _dedup_docket(rec), rec.get("decision_date"))


# A row is a copy of another when this share of its 60-character windows occurs
# verbatim in the other (contained()).
SAME_ENTRY_MIN_SHARE = 0.9


def _entry_text(rec: dict) -> str:
    return re.sub(r"\s+", " ", _strip_federation_header(rec.get("full_text") or "")).strip()


# ── the portal ──────────────────────────────────────────────────────────

def portal_fields(html: str) -> dict:
    """The header table of an Ajax?EnId= page as {field: text} (the scraper's
    own reading: th text without the colon -> td text)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            key = th.get_text(strip=True).rstrip(":")
            if key not in out:
                out[key] = td.get_text(strip=True)
    return out


def portal_date(fields: dict) -> str | None:
    m = _DMY_RE.search(fields.get("Entscheiddatum") or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def portal_docket(fields: dict, eid: int) -> str:
    """The docket the scraper derives from the same page."""
    return fields.get("Fallnummer") or fields.get("LGVE") or f"LU-{eid}"


def load_portal(path: Path) -> dict[int, dict]:
    """TSV written by fetch_portal: EnId, status, Entscheiddatum (ISO), docket."""
    out: dict[int, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            out[int(parts[0])] = {"status": parts[1], "date": parts[2] or None, "docket": parts[3]}
    return out


def fetch_portal(shard: Path, out: Path, *, all_rows: bool = False, sample: int = 0,
                 delay: float = 1.0, seed: int = 20261108, session=None) -> Counter:
    """Re-read the portal for the shard's stamped direct rows (or all direct rows,
    or a random sample of N direct rows) and append one TSV line per EnId to
    ``out``. Resumable: EnIds already in ``out`` are skipped."""
    if out.name.endswith(".jsonl"):
        raise SystemExit("refusing: the build ingests every *.jsonl beside the shards")
    wanted: list[int] = []
    for rec in _iter_records(shard):
        if (isinstance(rec, dict) and is_direct(rec)
                and (all_rows or sample or rec.get("date_extraction"))):
            wanted.append(en_id(rec))
    if sample:
        wanted = sorted(random.Random(seed).sample(wanted, min(sample, len(wanted))))
    done = set(load_portal(out)) if out.exists() else set()
    todo = [e for e in wanted if e not in done]
    if session is None:
        import requests

        from base_scraper import BaseScraper
        session = requests.Session()
        session.headers["User-Agent"] = BaseScraper.USER_AGENT
    stats: Counter = Counter(wanted=len(wanted), already=len(wanted) - len(todo))
    with open(out, "a", encoding="utf-8") as fh:
        if not done:
            fh.write("# EnId\tstatus\tEntscheiddatum\tdocket\t(fetched "
                     f"{datetime.now(timezone.utc).isoformat(timespec='seconds')})\n")
        for i, eid in enumerate(todo):
            if i and delay:
                time.sleep(delay)
            status, iso, docket = "error", "", ""
            for attempt in range(3):
                try:
                    r = session.get(PORTAL_URL, params={"EnId": eid}, timeout=30)
                except Exception:  # noqa: BLE001 - network: retry, then record
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    status = f"http_{r.status_code}"
                    time.sleep(5 * (attempt + 1))
                    continue
                if len(r.content) == INVALID_SIZE or "Keine oder ung" in r.text:
                    status = "invalid"
                    break
                fields = portal_fields(r.text)
                iso = portal_date(fields) or ""
                docket = portal_docket(fields, eid)
                status = "ok" if iso else "no_date"
                break
            stats[f"status:{status}"] += 1
            fh.write(f"{eid}\t{status}\t{iso}\t{docket}\n")
            fh.flush()
            if (i + 1) % 100 == 0:
                print(f"  fetched {i + 1}/{len(todo)}", file=sys.stderr)
    return stats


# ── the decision per row ────────────────────────────────────────────────

def decide(rec: dict, portal: dict[int, dict] | None = None) -> tuple[str, str | None, bool, str]:
    """(outcome, new decision_date or None, clear publication_date?, method)."""
    if is_es_copy(rec):
        return decide_es(rec)
    if not is_direct(rec):
        return "other", None, False, ""
    eid = en_id(rec)
    cur = _iso(rec.get("decision_date"))
    pub = _iso(rec.get("publication_date"))
    stamp = rec.get("date_extraction") or {}
    restore = rec.get("date_restore") or {}
    p = (portal or {}).get(eid)
    p_date = None
    if p is not None and p.get("status") == "ok" and p.get("date"):
        if _norm_docket(p.get("docket")) != _norm_docket(rec.get("docket_number")):
            return "portal_docket_mismatch", None, False, ""
        p_date = p["date"]

    if not stamp:
        if p_date and p_date != cur:
            return "unstamped_portal_differs", None, False, ""
        return "unstamped", None, False, ""

    if str(restore.get("method") or "").startswith("lu_portal") and restore.get("to") == cur:
        if p_date and p_date != cur:
            return "restored_portal_differs", None, False, ""
        return "already_restored", None, False, ""

    meta = _iso(stamp.get("metadata_date"))
    provable = meta is not None and pub == meta
    if p_date:
        target, method = p_date, METHOD_PORTAL
    elif provable:
        target, method = meta, METHOD_STAMP
    else:
        return "unprovable", None, False, ""
    clear_pub = provable and rec.get("publication_date") is not None
    if target == cur:
        return ("agrees_pub_cleared" if clear_pub else "agrees"), None, clear_pub, method
    return "restored", target, clear_pub, method


def decide_es(rec: dict) -> tuple[str, str | None, bool, str]:
    """An entscheidsuche copy (es_lu_gerichte.jsonl). Its text opens with the
    portal's header, "Luzern Kantonsgericht 4. Abteilung 19.07.2013 V 13 31";
    the 03-12 pass treated the same value as a publication date there too.
    Restored only where the header (for the row's own docket), the stamp's
    metadata_date and publication_date all hold the same day. Without this the
    restored direct rows and their copies would carry different dates, and the
    build would keep both (one ruling twice)."""
    cur = _iso(rec.get("decision_date"))
    stamp = rec.get("date_extraction") or {}
    restore = rec.get("date_restore") or {}
    if not stamp:
        return "es_unstamped", None, False, ""
    if str(restore.get("method") or "").startswith("lu_portal") and restore.get("to") == cur:
        return "es_already_restored", None, False, ""
    meta = _iso(stamp.get("metadata_date"))
    header = _federation_header_date(rec.get("full_text"), rec.get("docket_number"))
    if not (meta and header == meta and _iso(rec.get("publication_date")) == meta):
        return "es_unprovable", None, False, ""
    if meta == cur:
        return "es_agrees_pub_cleared", None, True, METHOD_ES_HEADER
    return "es_restored", meta, True, METHOD_ES_HEADER


def apply_change(rec: dict, new_date: str | None, clear_pub: bool, method: str, now: str,
                 alias: str | None = None) -> dict:
    out = dict(rec)
    if alias:
        out["previous_decision_id"] = alias
        out["previous_id_source"] = ALIAS_SOURCE
    if not new_date and not clear_pub:
        return out
    stamp = {"from": rec.get("decision_date"), "to": new_date or rec.get("decision_date"),
             "method": method, "at": now}
    if clear_pub:
        stamp["publication_date"] = {"from": rec.get("publication_date"), "to": None}
        out["publication_date"] = None
    if rec.get("date_restore"):
        stamp["prev"] = rec["date_restore"]
    out["date_restore"] = stamp
    if new_date:
        out["decision_date"] = new_date
    return out


# ── the shard ───────────────────────────────────────────────────────────

def _iter_records(path: Path):
    """(raw line, parsed record or None) is too heavy to keep; yields records
    (dict, or None for blank/unparsable lines) in file order."""
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                yield None
                continue
            try:
                yield json.loads(line)
            except ValueError:
                yield None


def _check_invariants(before: dict, after: dict) -> None:
    for k in set(before) | set(after):
        if k in CHANGED_FIELDS:
            continue
        if before.get(k) != after.get(k) or (k in before) != (k in after):
            raise SystemExit(f"refusing: {before.get('decision_id')} would change {k}")


def _pass1_len(rec: dict) -> int:
    """build Pass 1 keeps the row with the most full_text + regeste."""
    return len(rec.get("full_text") or "") + len(rec.get("regeste") or "")


def _content(rec: dict) -> str:
    """What a row says, for the containment test: Regeste and text, without
    the entscheidsuche header, whitespace collapsed."""
    return re.sub(r"\s+", " ", f"{rec.get('regeste') or ''} {_entry_text(rec)}").strip()


def contained(x: str, y: str) -> bool:
    """x is a copy of (part of) y: SAME_ENTRY_MIN_SHARE of the 60-character
    windows of x's first CONTAIN_PREFIX characters occur verbatim in y."""
    if not x or not y:
        return False
    return _window_share(x[:CONTAIN_PREFIX], y) >= SAME_ENTRY_MIN_SHARE


CONTAIN_PREFIX = 6000

Row = tuple  # (shard index, decision_id)


def _pass1_losses(groups: dict[str, list], length: dict, content: dict) -> set:
    """Rows build Pass 1 would delete although the row it keeps does not hold
    their text: a ruling lost, not a copy folded."""
    lost = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda r: length[r])
        lost |= {r for r in members if r != keep and not contained(content[r], content[keep])}
    return lost


def plan(shards: list[Path], portal: dict[int, dict] | None = None,
         examples: int = 0) -> tuple[list[Counter], set, list, dict]:
    """Decide every row of the given shards together (they hold one court:
    lu_gerichte.jsonl and es_lu_gerichte.jsonl), then hold back each
    restoration whose new dedup key (build Pass 1: court|docket|date) would make
    the build delete a ruling text it keeps today.

    Why: the portal publishes one ruling as several LGVE entries (EnIds 10968
    and 10969 are two Leitsätze of 7Q 20 4 of 3 June 2022), and entscheidsuche
    holds one of them; with the right date they share a key, and Pass 1 keeps
    only the longest. A restoration may take a key when Pass 1 would then
    delete only rows whose text the kept row contains (a copy of the same
    entry: the es copy is the direct text with header and Regeste prepended).
    Otherwise it is held ("held_dedup_collision", row unchanged). Restorations
    are considered direct rows first, by EnId, then es rows by id.

    Returns (per-shard stats, held rows, losses, alias_for): ``losses`` lists
    rows Pass 1 would newly lose after the change (run() refuses unless it is
    empty); ``alias_for`` maps the row Pass 1 will keep to the id of the row it
    newly drops (written as previous_decision_id)."""
    stats = [Counter() for _ in shards]
    new_date_of: dict[Row, str] = {}
    old_key: dict[Row, str] = {}
    new_key: dict[Row, str] = {}
    for i, shard in enumerate(shards):
        for rec in _iter_records(shard):
            stats[i]["lines"] += 1
            if not isinstance(rec, dict):
                stats[i]["unparsed"] += 1
                continue
            row = (i, rec.get("decision_id"))
            if row in old_key:
                raise SystemExit(f"refusing: {row[1]} occurs twice in {shard}")
            _o, new_date, _c, _m = decide(rec, portal)
            old_key[row] = dedup_key(rec)
            if new_date:
                new_date_of[row] = new_date
                new_key[row] = make_canonical_key(rec.get("court") or "", _dedup_docket(rec), new_date)

    def groups(keys: dict[Row, str]) -> dict[str, list]:
        g: dict[str, list] = {}
        for row, key in keys.items():
            if "||" not in key:        # build Pass 1 skips an empty docket
                g.setdefault(key, []).append(row)
        return g

    before = groups(old_key)
    target = groups({**old_key, **new_key})
    # Rows whose text matters: members of any group with two or more rows,
    # before or after.
    need = {r for g in (before, target) for m in g.values() if len(m) > 1 for r in m}
    length: dict[Row, int] = {}
    content: dict[Row, str] = {}
    prev_id: dict[Row, str] = {}
    for i, shard in enumerate(shards):
        for rec in _iter_records(shard):
            if isinstance(rec, dict) and (i, rec.get("decision_id")) in need:
                length[(i, rec["decision_id"])] = _pass1_len(rec)
                prev_id[(i, rec["decision_id"])] = (rec.get("previous_decision_id") or "").strip()
                content[(i, rec["decision_id"])] = _content(rec)
    lost_before = _pass1_losses(before, length, content)

    # Greedy: start from today's keys; move one restoration at a time.
    current = {k: list(v) for k, v in before.items()}
    where = dict(old_key)
    held: set = set()
    order = sorted(new_key, key=lambda r: (0, en_id({"decision_id": r[1]}) or 0, "")
                   if en_id({"decision_id": r[1]}) is not None else (1, 0, str(r[1])))
    for row in order:
        key = new_key[row]
        src = where[row]
        if "||" in key:
            continue
        dst = current.get(key, [])
        if not dst:
            ok = True
        else:
            trial_dst = dst + [row]
            keep = max(trial_dst, key=lambda r: length[r])
            lost_now = {r for r in trial_dst if r != keep and not contained(content[r], content[keep])}
            ok = lost_now <= lost_before
        if not ok:
            held.add(row)
            continue
        if src in current and row in current[src]:
            current[src].remove(row)
        current.setdefault(key, []).append(row)
        where[row] = key
    losses = sorted(_pass1_losses(current, length, content) - lost_before)

    # Keep the ids answerable. Builds before f65d40a2 record no alias for the
    # rows Pass 1 deletes, so a row this change newly makes it drop would stop
    # resolving (and the per-court swap gate would count it lost); later builds
    # record a dedup_pass1 alias too, which INSERT OR IGNORE leaves to ours. The row Pass 1 keeps takes the
    # dropped id as previous_decision_id: the build records it in
    # decision_id_aliases, get_decision resolves it, the gate counts it. Only
    # where the kept row is unambiguous (no tie on length), has no previous id
    # of its own, and absorbs exactly one newly dropped row.
    def kept(members: list) -> Row | None:
        top = max(length[r] for r in members)
        best = [r for r in members if length[r] == top]
        return best[0] if len(best) == 1 else None

    dropped_before = set()
    for members in before.values():
        if len(members) > 1:
            k = kept(members)
            dropped_before |= set(members) - ({k} if k else set(members))
    newly: dict[Row, list] = {}
    alias_stats = Counter()
    for members in current.values():
        if len(members) < 2:
            continue
        k = kept(members)
        for r in members:
            if r == k or r in dropped_before:
                continue
            if k is None:
                alias_stats["alias_skipped_tie"] += 1
            else:
                newly.setdefault(k, []).append(r)
    alias_for: dict[Row, str] = {}
    for k, rows in newly.items():
        if len(rows) == 1 and not prev_id.get(k):
            alias_for[k] = rows[0][1]
            alias_stats["alias_recorded"] += 1
        else:
            alias_stats["alias_unavailable"] += len(rows)

    shown = 0
    for i, shard in enumerate(shards):
        for rec in _iter_records(shard):
            if not isinstance(rec, dict):
                continue
            row = (i, rec.get("decision_id"))
            outcome, new_date, clear_pub, _method = decide(rec, portal)
            if row in held:
                outcome, new_date, clear_pub = "held_dedup_collision", None, False
            elif new_date and len(current.get(new_key[row], ())) > 1:
                stats[i]["restored_onto_copy"] += 1
            stats[i][f"outcome:{outcome}"] += 1
            if new_date:
                cur = _iso(rec.get("decision_date"))
                if cur:
                    gap = abs((date.fromisoformat(new_date) - date.fromisoformat(cur)).days)
                    stats[i]["restored_gap>=365d"] += gap >= 365
                    stats[i]["restored_gap<=31d"] += gap <= 31
                if shown < examples:
                    shown += 1
                    print(f"  {row[1]:<28} {rec.get('docket_number')!s:<16} "
                          f"{rec.get('decision_date')} -> {new_date}", file=sys.stderr)
            if row in alias_for:
                stats[i]["alias_on_kept_row"] += 1
            if new_date or clear_pub or row in alias_for:
                stats[i]["rows_changed"] += 1
            k_before, k_after = old_key[row], (new_key[row] if new_date else old_key[row])
            if "||" not in k_after:
                stats[i]["key_shared_before"] += len(before.get(k_before, ())) > 1
                stats[i]["key_shared_after"] += len(current.get(k_after, ())) > 1
    total_before = sum(len(m) for m in before.values()) - len(before)
    total_after = sum(len(m) for m in current.values()) - len([k for k, m in current.items() if m])
    for s in stats:
        s["pass1_folds_before"] = total_before
        s["pass1_folds_after"] = total_after
        s["pass1_lost_text_before"] = len(lost_before)
        s["pass1_lost_text_new"] = len(losses)
    for s in stats:
        s.update({f"{k}_total": v for k, v in alias_stats.items()})
    return stats, held, losses, alias_for


def _write_tmp(shard: Path, i: int, held: set, alias_for: dict, portal, now: str,
               expect: Counter) -> Path:
    tmp = shard.with_name(shard.name + ".tmp")
    if tmp.exists():
        raise SystemExit(f"refusing: {tmp} exists (a previous run died?); look, then remove it")
    written = changed = 0
    with open(shard, "rb") as fin, open(tmp, "wb") as fout:
        for raw in fin:
            written += 1
            line = raw.strip()
            rec = None
            if line:
                try:
                    rec = json.loads(line)
                except ValueError:
                    rec = None
            if not isinstance(rec, dict):
                fout.write(raw)
                continue
            _outcome, new_date, clear_pub, method = decide(rec, portal)
            row = (i, rec.get("decision_id"))
            if row in held:
                new_date, clear_pub = None, False
            alias = alias_for.get(row)
            if not new_date and not clear_pub and not alias:
                fout.write(raw)
                continue
            new = apply_change(rec, new_date, clear_pub, method, now, alias)
            _check_invariants(rec, new)
            changed += 1
            fout.write(json.dumps(new, ensure_ascii=False).encode("utf-8") + b"\n")
        fout.flush()
        os.fsync(fout.fileno())
    if written != expect["lines"] or changed != expect["rows_changed"]:
        raise SystemExit(f"refusing: {shard}: writing saw {written} lines / {changed} changes, "
                         f"planning {expect['lines']} / {expect['rows_changed']}")
    with open(tmp, "rb") as fh:
        if sum(1 for _ in fh) != written:
            raise SystemExit(f"refusing: {tmp}'s line count differs")
    return tmp


def run(shards: list[Path], apply: bool, portal: dict[int, dict] | None = None,
        examples: int = 0) -> list[Counter]:
    """Plan all shards together; with ``apply``, write every temp file first,
    then back up and swap the shards one right after the other (the direct and
    es copies must change together, or the build serves a ruling twice)."""
    if len(set(map(str, shards))) != len(shards):
        raise SystemExit("refusing: the same shard twice")
    before = [os.stat(s) for s in shards]
    stats, held, losses, alias_for = plan(shards, portal, examples)
    if losses:
        for i, did in losses[:20]:
            print(f"  would lose: {did} ({shards[i].name})", file=sys.stderr)
        raise SystemExit(f"refusing: build Pass 1 would delete {len(losses)} rows whose text "
                         "the row it keeps does not contain")
    if not apply or not any(s["rows_changed"] for s in stats):
        return stats
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp = time.strftime("%Y%m%d", time.gmtime())
    todo = [(i, s) for i, s in enumerate(shards) if stats[i]["rows_changed"]]
    for _i, s in todo:
        backup = s.with_name(f"{s.name}.bak-{stamp}")
        if backup.exists():
            raise SystemExit(f"refusing to overwrite existing backup {backup}")
    tmps: dict[int, Path] = {}
    try:
        for i, s in todo:
            tmps[i] = _write_tmp(s, i, held, alias_for, portal, now, stats[i])
        for i, s in enumerate(shards):
            st = os.stat(s)
            b = before[i]
            if (st.st_size, st.st_mtime_ns, st.st_ino) != (b.st_size, b.st_mtime_ns, b.st_ino):
                raise SystemExit(f"refusing: {s} changed while this ran (a scrape appending?)")
        for i, s in todo:
            backup = s.with_name(f"{s.name}.bak-{stamp}")
            os.link(s, backup)  # zero-cost, same inode; not *.jsonl
            os.chmod(tmps[i], before[i].st_mode & 0o7777)
            os.replace(tmps[i], s)
            stats[i]["applied"] = 1
            print(f"rewrote {s}; backup {backup}", file=sys.stderr)
    finally:
        for t in tmps.values():
            if t.exists():
                t.unlink()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path,
                    help="lu_gerichte.jsonl, and es_lu_gerichte.jsonl (planned together)")
    ap.add_argument("--apply", action="store_true", help="rewrite the shards (default: dry run)")
    ap.add_argument("--portal", type=Path, help="TSV from --fetch-portal; its dates win over the stamp")
    ap.add_argument("--examples", type=int, default=0, help="print this many restored rows")
    ap.add_argument("--fetch-portal", type=Path, metavar="TSV",
                    help="instead of repairing, re-read the portal for the stamped direct rows into TSV")
    ap.add_argument("--fetch-all-rows", action="store_true", help="with --fetch-portal: every direct row")
    ap.add_argument("--sample", type=int, default=0, help="with --fetch-portal: N random direct rows")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between portal requests")
    a = ap.parse_args(argv)
    for s in a.shards:
        if not s.exists():
            print(f"shard not found: {s}", file=sys.stderr)
            return 2
    if a.fetch_portal:
        stats = fetch_portal(a.shards[0], a.fetch_portal, all_rows=a.fetch_all_rows,
                             sample=a.sample, delay=a.delay)
        print(json.dumps(dict(sorted(stats.items()))))
        return 0
    portal = load_portal(a.portal) if a.portal else None
    print(("APPLY" if a.apply else "DRY RUN") + ": " + " ".join(map(str, a.shards)), file=sys.stderr)
    stats = run(a.shards, a.apply, portal, a.examples)
    for s, st in zip(a.shards, stats):
        print(json.dumps({"shard": s.name, **dict(sorted(st.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
