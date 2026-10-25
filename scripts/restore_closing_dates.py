#!/usr/bin/env python3
"""
restore_closing_dates.py — give bger and ai_gerichte rows the date the ruling
itself states where the stored decision_date is another decision's date.

Why (external report 2026-10-24; audit of the HF mirror the next day):
  * bger: `scripts/repair_decision_dates.py --all` (2026-03-12) read the first
    "Entscheid vom <date>" of the text as the decision date and moved the
    stored date into publication_date. In a BGer ruling that first date is
    the decision under appeal ("gegen den Entscheid vom 17. Mai 2001 des
    Obergerichts"): BGer 7B.150/2001 of 14 August 2001 was cited as of
    17 May 2001. 1,209 rows of the February 2026 bootstrap, all of them with
    the true date still in publication_date, in the closing "Lausanne,
    14. August 2001" and, where the URL carries one, in the URL
    ("id=14.08.2001_7B.150/2001"). No row scraped since is affected.
  * ai_gerichte: the scraper dated a row by the ai.ch listing, i.e. when the
    ruling was put online (K 7-2021 of 16 November 2021: listed 19 January
    2022, after the Federal Supreme Court's 3 January 2022 ruling on it), and
    the 03-12 pass replaced some of those with a date from the body. Not one
    of the 85 rows whose closing line names their own docket agreed with it.

The ruling's own statement of its date:
  bger         the closing "<seat>, <date>" (Lausanne, Luzern, Bellinzona,
               Mon-Repos) of the last 3000 characters. Taken only when it
               equals publication_date (the 03-12 swap left the true date
               there) and, if the source URL carries a date, that one too.
  ai_gerichte  the last "Entscheid <docket> vom <date>" naming the row's own
               docket (scrapers.cantonal.ai_gerichte.own_decision_date), not
               before the docket's year. The listing date moves to
               publication_date when that is empty.

Every changed row gets a `date_restore` stamp {from, to, method, at} (a
previous stamp is kept under "prev"), so the change is exactly reversible.

Streaming, temp file + atomic replace, DRY RUN by default, idempotent. Apply
only while no process reads or appends the shard: after that night's scrape
has logged Done. and before the 03:30 full build (bger also has the hourly
bger-poller and the 10:20 late scrape).

Usage:
  python3 scripts/restore_closing_dates.py output/decisions/bger.jsonl
  python3 scripts/restore_closing_dates.py output/decisions/ai_gerichte.jsonl --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
_BGER_CLOSE_RE = re.compile(
    r"\b(?:Lausanne|Luzern|Lucerne|Lucerna|Losanna|Bellinzona|Mon[- ]Repos),?\s+"
    r"(?:den\s+|le\s+|il\s+|lo\s+)?(?P<d>1er|\d{1,2})(?:\.|º|°)?\s*(?P<m>" + _MONTH_ALT + r")"
    r"\s+(?P<y>(?:19|20)\d{2})(?!\d)", re.I)
_URL_DATE_RE = re.compile(r"id=(\d{2})\.(\d{2})\.(\d{4})_")

COURTS = ("bger", "ai_gerichte")


def bger_closing_date(text: str | None) -> str | None:
    """The date of the last closing "<seat>, <date>" of a BGer ruling."""
    found = None
    for m in _BGER_CLOSE_RE.finditer((text or "")[-3000:]):
        try:
            d = 1 if m.group("d").lower() == "1er" else int(m.group("d"))
            found = date(int(m.group("y")), _MONTHS[m.group("m").lower()], d).isoformat()
        except (ValueError, KeyError):
            continue
    return found


def url_date(url: str | None) -> str | None:
    m = _URL_DATE_RE.search(url or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


def decide(obj: dict) -> tuple[str, str | None]:
    """(outcome, new decision_date or None)."""
    court = str(obj.get("court") or "")
    cur = str(obj.get("decision_date") or "")[:10]
    if court == "bger":
        own = bger_closing_date(obj.get("full_text"))
        if not own:
            return "no_closing_date", None
        if own == cur:
            return "agrees", None
        if own != str(obj.get("publication_date") or "")[:10]:
            return "closing_not_publication_date", None
        u = url_date(obj.get("source_url"))
        if u and u != own:
            return "url_disagrees", None
        return ("filled" if not cur else "corrected"), own
    if court == "ai_gerichte":
        from scrapers.cantonal.ai_gerichte import own_decision_date
        d = own_decision_date(obj.get("full_text"), obj.get("docket_number"))
        if d is None:
            return "no_own_line", None
        own = d.isoformat()
        if own > date.today().isoformat():
            return "own_line_in_future", None
        if own == cur:
            return "agrees", None
        return ("filled" if not cur else "corrected"), own
    return "other_court", None


def apply_change(obj: dict, new: str, method: str, now: str) -> dict:
    restore = {"from": obj.get("decision_date"), "to": new, "method": method, "at": now}
    if obj.get("date_restore"):
        restore["prev"] = obj["date_restore"]
    obj["date_restore"] = restore
    old = str(obj.get("decision_date") or "")[:10]
    if (obj.get("court") == "ai_gerichte" and not obj.get("publication_date")
            and old and old > new):
        # The listing date: when ai.ch put the ruling online.
        obj["publication_date"] = old
        restore["publication_date"] = {"from": None, "to": old}
    obj["decision_date"] = new
    return obj


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
                if str(obj.get("court") or "") not in COURTS:
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new = decide(obj)
                stats[outcome] += 1
                if new and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<44} {obj.get('decision_date')} -> "
                          f"{new}  [{outcome}]", file=sys.stderr)
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                method = "bger_closing" if obj.get("court") == "bger" else "ai_own_line"
                obj = apply_change(obj, new, method, now)
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        if fout:
            fout.close()
            os.unlink(tmp_path)
        raise
    if fout:
        fout.close()
        if stats["rows_changed"]:
            # mkstemp creates 0600; keep the shard's own mode.
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
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
