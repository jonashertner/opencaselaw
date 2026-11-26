#!/usr/bin/env python3
"""
restore_ge_dates.py — give the Geneva rows back the date of the ruling, which
the 2026-03-12 date pass replaced with a procedural date.

Why (Mnemosyne reports of 2026-11-09 and 2026-11-25; counted 2026-11-25):
  * The scraper (scrapers/cantonal/ge_gerichte.py) takes decision_date from the
    official listing ("ACJC/1085/2024 du 03.09.2024") and writes no
    publication_date. That date is the ruling's: its header says the same,
    "ARRÊT DE LA COUR DE JUSTICE Chambre civile DU MARDI 3 SEPTEMBRE 2024".
  * scripts/repair_decision_dates.py --all (one-off, 2026-03-12) globbed every
    shard, ge_gerichte included, took the listing date for a publication date,
    moved it into publication_date and put the first date it found in the text
    into decision_date, stamping the row with `date_extraction`
    (metadata_date = the value it replaced). In a Geneva ruling that first date
    is procedural: the notification line the Chambre civile prints above the
    header ("communiqué aux parties par plis recommandés du 11 septembre 2024"),
    the filing of the appeal (DAS, DCSO: "Recours … formé en date du"), or the
    decision under appeal (ACPR, DAAJ, AARP: "contre l'ordonnance rendue le").
    ACJC/1085/2024 is served as 11 September 2024; DAS/192/2020 as
    12 August 2020 (decided 18 November 2020).
  * HF-mirror count 2026-11-25: 36,590 ge_gerichte rows whose decision_date
    differs from the header date while publication_date equals it.

Per row (court ge_gerichte, direct and entscheidsuche rows alike):
  * restored when all three agree on one day: the header date read from the
    ruling's own head (ge_header_date), the stamp's metadata_date and
    publication_date; and the header year equals the year of the ruling number
    the text names (ACJC/1085/2024 -> 2024), where it names one. Then
    decision_date = that day and publication_date is cleared (the scraper
    writes none). Rows whose decision_date already holds it are left alone.
  * anything else -> unchanged, counted by reason ("unstamped",
    "no_header", "header_ne_stamp", "pub_ne_stamp", "year_mismatch", ...).
Every changed row gets a `date_restore` stamp {from, to, method, at,
publication_date: {from, to}} (a previous stamp is kept under "prev"), so the
change is exactly reversible; `date_extraction` stays as provenance.

Direct rows and entscheidsuche copies never share a dedup key: the direct row's
docket is the ruling number (ACJC/1085/2024), the copy's the case number
(C/7337/2018), and ge_gerichte is not a DECISION_NUMBER_COURTS court. But one
case number carries several rulings, so two copies of one case can land on the
same day. A restoration whose new key would make build Pass 1
(court|docket|date) delete a row whose text the kept row does not contain is
held ("held_dedup_collision", unchanged); the run refuses to write if any such
loss remains. The machinery is restore_lu_dates.py's.

Writing: every temp file (<shard>.tmp) is written and checked first (only
decision_date / publication_date / date_restore / previous_decision_id /
previous_id_source differ; same line count), then each shard is hard-linked to
<shard>.bak-<UTC date> (neither name matches the build's *.jsonl glob) and
swapped in atomically, one right after the other. It refuses if a shard
changed during the run.

DRY RUN by default, idempotent. Apply only while nothing appends to or reads
the shards (both publish units inactive, no ge scrape running).

Usage:
  python3 scripts/restore_ge_dates.py output/decisions/ge_gerichte.jsonl \
      output/decisions/es_ge_gerichte.jsonl [--apply] [--examples 20]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from build_fts5 import _dedup_docket  # noqa: E402
from models import make_canonical_key  # noqa: E402
from scripts.restore_lu_dates import (  # noqa: E402
    _check_invariants,
    _content,
    _iso,
    _iter_records,
    _pass1_len,
    _pass1_losses,
    contained,
    dedup_key,
)

COURT = "ge_gerichte"
METHOD = "ge_header_date"
ALIAS_SOURCE = "ge_date_fold"

# ── the ruling's own date ───────────────────────────────────────────────

_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
           "decembre": 12}
_DATE_RE = re.compile(r"\b(\d{1,2})\s?(?:er)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)
_ANCHOR_RE = re.compile(r"POUVOIR JUDICIAIRE", re.I)
# Where the head ends: the parties, the recitals, the procedure or prose.
# A date after any of these is somebody else's (the judgment under appeal in
# "Vu le jugement JTPI/431/2021 du 15 janvier 2021", the filing in "Recours …
# formé en date du", a statute in "loi sur le Tribunal fédéral du 17 juin 2005").
_HEAD_END_RE = re.compile(
    r"\b(?:Entre|En la cause|en la cause|dans la cause|Statuant|Vu|Vus|Attendu|ATTENDU|"
    r"Recours|Plainte|Requete|Demande|Appel|Siegeant|EN FAIT|En fait|MINISTERE PUBLIC|"
    r"Le Tribunal|La Chambre|La Cour|Le recours|loi)\b")
HEAD_WINDOW = 700
# The ruling number the head names: ACJC/1085/2024, DCSO/177/18, ATA/8/2024.
# Not the case numbers (C/, A/, P/, AC/, PM/, PS/, AP/): a case runs for years.
_RULING_PREFIXES = ("ACJC|ACPR|ATAS|ATA|DAAJ|DAAJP|DAS|DCSO|CAPH|JTAPI|AARP|ACJP|ACOM|OCA|"
                    "OARP|OCPR|DCCR|DCPR|ACST|ATAX|JTDP|JTCO|CABL|DITAI")
_RULING_NO_RE = re.compile(r"\b(" + _RULING_PREFIXES + r")\s?/\s?\d+\s?/\s?(\d{2,4})\b")
# A direct row's id is minted from the listing's ruling number.
_RULING_ID_RE = re.compile(r"^ge_gerichte_(?:" + _RULING_PREFIXES + r")_\d+_(\d{4})$")


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def ge_header_date(text: str | None) -> str | None:
    """ISO date of the ruling, from its head: the first "<d> <month> <yyyy>"
    between "POUVOIR JUDICIAIRE" and the start of the parties or recitals
    ("DU MARDI 3 SEPTEMBRE 2024", "Arrêt du 8 janvier 2024", "du 1 er mars
    2006"). The notification line and the entscheidsuche preamble stand above
    the anchor and never count. None when the text has no such head."""
    head = _fold((text or "")[:6000])
    a = _ANCHOR_RE.search(head)
    if not a:
        return None
    block = head[a.end():a.end() + HEAD_WINDOW]
    end = _HEAD_END_RE.search(block)
    if end:
        block = block[:end.start()]
    m = _DATE_RE.search(block)
    if not m:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    except ValueError:
        return None


def ruling_year(text: str | None) -> int | None:
    """Year of the ruling number the head names (from the anchor on, or the
    entscheidsuche preamble's "ACJC/876/2016 du 24.06.2016"), else None."""
    head = _fold((text or "")[:6000])
    m = _RULING_NO_RE.search(head)
    if not m:
        return None
    y = int(m.group(2))
    if y < 100:
        y += 2000 if y < 50 else 1900
    return y


def id_year(rec: dict) -> int | None:
    """Year of the ruling number the row's id was minted from, else None
    (entscheidsuche ids carry the case number)."""
    m = _RULING_ID_RE.match(str(rec.get("decision_id") or ""))
    return int(m.group(1)) if m else None


# ── the decision per row ────────────────────────────────────────────────

def decide(rec: dict) -> tuple[str, str | None, bool, str]:
    """(outcome, new decision_date or None, clear publication_date?, method)."""
    if rec.get("court") != COURT:
        return "other", None, False, ""
    cur = _iso(rec.get("decision_date"))
    pub = _iso(rec.get("publication_date"))
    stamp = rec.get("date_extraction") or {}
    restore = rec.get("date_restore") or {}
    if str(restore.get("method") or "") == METHOD and restore.get("to") == cur:
        return "already_restored", None, False, ""
    if not stamp:
        return "unstamped", None, False, ""
    if stamp.get("reverted"):
        return "stamp_reverted", None, False, ""
    meta = _iso(stamp.get("metadata_date"))
    header = ge_header_date(rec.get("full_text"))
    if not header:
        return "no_header", None, False, ""
    if header == cur:
        return "agrees", None, False, ""
    if meta != header:
        return "header_ne_stamp", None, False, ""
    if pub != header:
        return "pub_ne_stamp", None, False, ""
    # The header's year must be the ruling number's (the id's, or the one the
    # text names: the text can carry a typo, ACJC/1652/2017 for ACJC_1652_2016,
    # and so can the header, "30 MAI 2013" for ACJC/656/2014).
    years = {ruling_year(rec.get("full_text")), id_year(rec)} - {None}
    if years and int(header[:4]) not in years:
        return "year_mismatch", None, False, ""
    return "restored", header, rec.get("publication_date") is not None, METHOD


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


# ── the shards (plan, then write) ───────────────────────────────────────

Row = tuple  # (shard index, decision_id)


def plan(shards: list[Path], examples: int = 0) -> tuple[list[Counter], set, list, dict]:
    """Decide every row of the given shards together (ge_gerichte.jsonl and
    es_ge_gerichte.jsonl), then hold back each restoration whose new dedup key
    would make build Pass 1 delete a ruling text it keeps today (see
    restore_lu_dates.plan; restorations are considered in decision_id order).

    Returns (per-shard stats, held rows, losses, alias_for)."""
    stats = [Counter() for _ in shards]
    old_key: dict[Row, str] = {}
    new_key: dict[Row, str] = {}
    first_decision: dict[Row, tuple] = {}
    for i, shard in enumerate(shards):
        for rec in _iter_records(shard):
            stats[i]["lines"] += 1
            if not isinstance(rec, dict):
                stats[i]["unparsed"] += 1
                continue
            row = (i, rec.get("decision_id"))
            _o, new_date, _c, _m = decide(rec)
            if row in old_key:
                # ge_gerichte.jsonl repeats ids (6,973 on 2026-11-25, each pair
                # identical in date). The build inserts the first line and
                # ignores a repeat with the same key, but serves a repeat with
                # another date as <id>_dYYYYMMDD: every line of an id must get
                # the same decision, or the repair splits a ruling in two.
                if (_o, new_date, dedup_key(rec)) != first_decision[row]:
                    raise SystemExit(f"refusing: the lines of {row[1]} in {shard} differ "
                                     f"({first_decision[row]} vs {(_o, new_date, dedup_key(rec))})")
                stats[i]["repeated_id_lines"] += 1
                continue
            first_decision[row] = (_o, new_date, dedup_key(rec))
            old_key[row] = dedup_key(rec)
            if new_date:
                new_key[row] = make_canonical_key(rec.get("court") or "", _dedup_docket(rec), new_date)

    def groups(keys: dict[Row, str]) -> dict[str, list]:
        g: dict[str, list] = {}
        for row, key in keys.items():
            if "||" not in key:        # build Pass 1 skips an empty docket
                g.setdefault(key, []).append(row)
        return g

    before = groups(old_key)
    target = groups({**old_key, **new_key})
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

    current = {k: list(v) for k, v in before.items()}
    where = dict(old_key)
    held: set = set()
    for row in sorted(new_key, key=lambda r: (str(r[1]), r[0])):
        key = new_key[row]
        if "||" in key:
            continue
        dst = current.get(key, [])
        if dst:
            trial = dst + [row]
            keep = max(trial, key=lambda r: length[r])
            lost_now = {r for r in trial if r != keep and not contained(content[r], content[keep])}
            if not lost_now <= lost_before:
                held.add(row)
                continue
        src = where[row]
        if src in current and row in current[src]:
            current[src].remove(row)
        current.setdefault(key, []).append(row)
        where[row] = key
    losses = sorted(_pass1_losses(current, length, content) - lost_before)

    # A row the change newly makes Pass 1 drop stays answerable: the row it
    # keeps takes the dropped id as previous_decision_id (one per kept row, no
    # tie on length, kept row without a previous id of its own).
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
            outcome, new_date, clear_pub, _method = decide(rec)
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
                    print(f"  {row[1]:<34} {rec.get('docket_number')!s:<18} "
                          f"{rec.get('decision_date')} -> {new_date}", file=sys.stderr)
            if row in alias_for:
                stats[i]["alias_on_kept_row"] += 1
            if new_date or clear_pub or row in alias_for:
                stats[i]["rows_changed"] += 1
    total_before = sum(len(m) for m in before.values()) - len(before)
    total_after = sum(len(m) for m in current.values()) - len([k for k, m in current.items() if m])
    for s in stats:
        s["pass1_folds_before"] = total_before
        s["pass1_folds_after"] = total_after
        s["pass1_lost_text_before"] = len(lost_before)
        s["pass1_lost_text_new"] = len(losses)
        s.update({f"{k}_total": v for k, v in alias_stats.items()})
    return stats, held, losses, alias_for


def _write_tmp(shard: Path, i: int, held: set, alias_for: dict, now: str, expect: Counter) -> Path:
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
            _outcome, new_date, clear_pub, method = decide(rec)
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


def run(shards: list[Path], apply: bool, examples: int = 0) -> list[Counter]:
    """Plan all shards together; with ``apply``, write every temp file first,
    then back up and swap the shards one right after the other."""
    if len(set(map(str, shards))) != len(shards):
        raise SystemExit("refusing: the same shard twice")
    before = [os.stat(s) for s in shards]
    stats, held, losses, alias_for = plan(shards, examples)
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
            tmps[i] = _write_tmp(s, i, held, alias_for, now, stats[i])
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
                    help="ge_gerichte.jsonl, and es_ge_gerichte.jsonl (planned together)")
    ap.add_argument("--apply", action="store_true", help="rewrite the shards (default: dry run)")
    ap.add_argument("--examples", type=int, default=0, help="print this many restored rows")
    a = ap.parse_args(argv)
    for s in a.shards:
        if not s.exists():
            print(f"shard not found: {s}", file=sys.stderr)
            return 2
    print(("APPLY" if a.apply else "DRY RUN") + ": " + " ".join(map(str, a.shards)), file=sys.stderr)
    stats = run(a.shards, a.apply, a.examples)
    for s, st in zip(a.shards, stats):
        print(json.dumps({"shard": s.name, **dict(sorted(st.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
