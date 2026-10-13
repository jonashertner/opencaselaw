#!/usr/bin/env python3
"""
restore_tribuna_dates.py — give Tribuna rows stored before 2026-09-27 the date
their ruling's own heading states.

Why (external report 2026-10-13; audit of the shards the same night):
  * Until 2e46549d (2026-09-27) base_tribuna read a row's decision date from
    the strings after its docket in the GWT-RPC string table. That table is
    deduplicated, so a row whose date an earlier row already carried got
    another row's date, its createDate or its Rechtskraft date instead:
      fr_gerichte "604 2026 58"  stored 2026-10-26, "Arrêt du 1er septembre 2026"
      be_verwaltungsgericht "200 2025 737"  stored 2025-10-13,
        "Urteil der Einzelrichterin vom 11. November 2025"
    The scraper has read each row's own record since then (sample of every
    post-fix row checked against its heading: all agree), but rows already
    stored kept the old value; the scraper never rewrites a stored row.

The court's own statement of the date: the heading at the top of the ruling
("Urteil vom 14. November 2025", "Arrêt du 1er septembre 2026", "Decisione
del 15 novembre 2024", "Präsidialverfügung vom 30. Juli 2025"). It counts only
  * when it stands in the document head, BEFORE the parties block ("in
    Sachen", "Parteien", "gegen", "contre", "betreffend", "Objet" …): the
    lower-court decision a ruling reviews is named after the parties;
  * when its year is not before the docket's year and the date not after
    today;
  * for fr_gerichte, when the closing "Fribourg, le …" agrees (if present).

Per row (Tribuna shards), in order:
  1. heading date found, row scraped before the fix, differs from (or fills)
     decision_date                      -> decision_date = heading date
  2. same for a row scraped after the fix -> counted ("post_fix_disagree"),
     never changed (the fixed scraper read the row's own record)
  3. otherwise                          -> unchanged
Every changed row gets a `date_restore` stamp {from, to, method, at} (a
previous stamp is kept under "prev"), so the change is exactly reversible.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent. Self-
contained (stdlib only) so a dry run can be piped to the host. Apply only
while no process reads or appends the shard: after that night's scrape has
logged Done. and before the 03:30 full build.

Usage:
  python3 scripts/restore_tribuna_dates.py output/decisions/fr_gerichte.jsonl
  python3 scripts/restore_tribuna_dates.py output/decisions/fr_gerichte.jsonl --apply
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

TRIBUNA_COURTS = (
    "fr_gerichte", "be_verwaltungsgericht", "be_zivilstraf", "be_anwaltsaufsicht",
    "be_bvd", "be_steuerrekurs", "gr_gerichte", "zg_gerichte", "zg_obergericht",
)
# 2e46549d "tribuna: read each row's date from its own record", 2026-09-27
# 11:50 +0200. Rows scraped from then on carry the row record's own date.
FIX_AT = "2026-09-27T09:50"

_MONTHS = {
    # de
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6,
    "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
    # it
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "dicembre": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = (r"(?:(?P<d>1er|\d{1,2})\.?\s*(?P<m>" + _MONTH_ALT + r")\s+(?P<y>(?:19|20)\d{2})(?!\d)"
         r"|(?P<nd>\d{1,2})\.(?P<nm>\d{1,2})\.(?P<ny>(?:19|20)\d{2})(?!\d))")
# Words that may stand between the designation and its date when they only
# qualify who decided ("des Einzelrichters", "du juge unique", "par défaut",
# "der II. Kammer"). Any other word means the date belongs to something else:
# "Ce jugement remplace partiellement celui rendu le 21 mars 2019", "arrêt du
# Tribunal fédéral du 24 septembre 2019", "mit Verfügung der
# Staatsanwaltschaft Graubünden vom 2. Juni 2006" (all seen in the 10-13
# dry-run sample, all wrong).
_QUALIFIER = (
    r"(?:des|der|de|du|della|dell'|di|par|défaut|juge|unique|giudice|unico|"
    r"Einzelrichters|Einzelrichterin|Präsidenten|Präsidentin|Vizepräsidenten|"
    r"Vizepräsidentin|Instruktionsrichters|Instruktionsrichterin|Vorsitzenden|"
    r"presidente|[IVX]{1,4}\.|\w*(?:kammer|abteilung|Kammer|Abteilung)|"
    r"Abteilungspräsidenten|Abteilungspräsidentin|Kammerpräsidenten|Kammerpräsidentin)"
)
# The ruling's own designation, up to four qualifiers, then the date.
_HEADING_RE = re.compile(
    r"\b(?:Urteil|Entscheid|Endentscheid|Zwischenentscheid|Beschluss|Verfügung|"
    r"Präsidialverfügung|Arrêt|Jugement|Décision|Ordonnance|Prononcé|"
    r"Sentenza|Decisione|Decreto|Ordinanza)"
    r"(?:\s+" + _QUALIFIER + r"){0,4}\s+(?:vom|du|del|dal|dell'|le)?\s*" + _DATE,
    re.I,
)
# Where the parties block starts: anything after this is the case, not the
# ruling's own heading (the reviewed decision is named here).
_PARTIES_RE = re.compile(
    r"\b(?:in\s+Sachen|Parteien|Parties|Parti|gegen|contre|contro|betreffend|"
    r"concernant|concernente|Gegenstand|Objet|Oggetto|Beschwerdeführer\w*|"
    r"recourante?s?|ricorrente|appelante?s?)\b",
    re.I,
)
_SIGN_RE = re.compile(r"\b(?:Fribourg|Freiburg)\s*,?\s*(?:le|den)\s+" + _DATE, re.I)
_HEAD_CHARS = 900


def _iso(m: re.Match) -> str | None:
    try:
        if m.group("y"):
            d = 1 if m.group("d").lower() == "1er" else int(m.group("d"))
            return date(int(m.group("y")), _MONTHS[m.group("m").lower()], d).isoformat()
        return date(int(m.group("ny")), int(m.group("nm")), int(m.group("nd"))).isoformat()
    except (ValueError, KeyError):
        return None


def docket_year(docket: str | None) -> int | None:
    """Filing year of a Tribuna docket: '200 2025 737', 'BZ 2025 80',
    'BK 25 614' -> 2025. None when the docket carries no year."""
    parts = re.split(r"[\s_/.-]+", (docket or "").strip())
    for p in parts:
        if re.fullmatch(r"(?:19|20)\d{2}", p):
            return int(p)
    for p in parts[1:]:
        if re.fullmatch(r"\d{2}", p):
            return 2000 + int(p)
    return None


def heading_date(text: str | None, docket: str | None = None,
                 court: str | None = None, today: str | None = None) -> tuple[str | None, str]:
    """(the date the ruling's own heading states, reason) — (None, reason)
    when there is no heading date we can trust."""
    t = text or ""
    head = t[:_HEAD_CHARS]
    parties = _PARTIES_RE.search(head)
    limit = parties.start() if parties else len(head)
    m = _HEADING_RE.search(head, 0, limit)
    if not m:
        return None, "no_heading"
    own = _iso(m)
    if not own:
        return None, "invalid_date"
    today = today or date.today().isoformat()
    if own > today:
        return None, "heading_in_future"
    dy = docket_year(docket)
    if dy is not None and int(own[:4]) < dy:
        return None, "before_docket_year"
    if court == "fr_gerichte":
        signs = [_iso(s) for s in _SIGN_RE.finditer(t[-4000:])]
        signs = [s for s in signs if s]
        if signs and signs[-1] != own:
            return None, "signature_disagrees"
    return own, "heading"


def decide(obj: dict, today: str | None = None) -> tuple[str, str | None]:
    """(outcome, new decision_date or None)."""
    own, how = heading_date(obj.get("full_text"), obj.get("docket_number"),
                            obj.get("court"), today)
    if own is None:
        return how, None
    cur = str(obj.get("decision_date") or "")[:10]
    if cur == own:
        return "agrees", None
    if str(obj.get("scraped_at") or "") >= FIX_AT:
        return "post_fix_disagree", None
    return ("filled" if not cur else "corrected"), own


def run(path: Path, apply: bool, examples: int, today: str | None = None) -> Counter:
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
                if str(obj.get("court") or "") not in TRIBUNA_COURTS:
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new = decide(obj, today)
                stats[outcome] += 1
                if (new or outcome == "post_fix_disagree") and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<44} {obj.get('decision_date')} -> "
                          f"{new or '(kept)'}  [{outcome}]", file=sys.stderr)
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                restore = {"from": obj.get("decision_date"), "to": new,
                           "method": "tribuna_heading", "at": now}
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
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=15)
    a = ap.parse_args()
    for shard in a.shards:
        print(f"== {shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = run(shard, a.apply, a.examples)
        print(json.dumps({"shard": str(shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
