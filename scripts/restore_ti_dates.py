#!/usr/bin/env python3
"""
restore_ti_dates.py — give Ticino rows the date their ruling's own heading
states ("Incarto n. 52.2019.467 Lugano 8 marzo 2024 In nome della Repubblica").

Why (report 2026-10-14; audit of both shards 2026-10-15, with the re-fetched
texts): ti_gerichte_32.2023.125 holds 2023-10-10, the date of the decision it
reviews ("la decisione del 10 ottobre 2023"), for a ruling of 11 April 2024.
8,477 rows of ti_gerichte.jsonl and 11,296 of es_ti_gerichte.jsonl disagree
with their heading; in 7,732 and 11,195 of them the stored date is another
dated event the text names (the contested decision, the appeal, a filing).

A row changes only when
  * the heading stands in the document head (first 900 characters): "Incarto
    n. <number> [initials] <place> <date>", followed by "In nome della
    Repubblica"; where the entscheidsuche Kopfzeile carries a date, it agrees;
  * the stored date is empty (filled), or earlier than the heading AND written
    elsewhere in the text (corrected: it is that other event's date).
A stored date later than the heading, or one the text never states, is left
alone and counted: the text alone cannot say which is right.

Both shards go together (the Tribuna and Vaud lesson: a twin repaired on one
side only comes back as a separate row with the wrong date): both are read
once up front to collect the direct scraper's decision_date per docket, and
that cross-shard date — two independent sources agreeing — is allowed to
override a disagreeing entscheidsuche Kopfzeile (2026-10-22 audit:
ti_gerichte_11.2016.86 and ti_gerichte_14.2013.162 both run correctly through
`decide` once the script is applied; the real gap was es's own Kopfzeile
blocking otherwise-good heading reads for 482 further rows, confirmed by
their direct twin). Every changed row gets a `date_restore` stamp
{from, to, method, at}.

Streaming, temp file + atomic replace (file mode kept), DRY RUN by default,
idempotent, stdlib only. ti_gerichte.jsonl is appended by the 01:00 scrape:
apply after it has logged Done. and before the 03:30 build, or after a build
exits and before the next scrape.

Usage:
  python3 scripts/restore_ti_dates.py output/decisions/ti_gerichte.jsonl output/decisions/es_ti_gerichte.jsonl
  python3 scripts/restore_ti_dates.py ... --apply
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

COURTS = ("ti_gerichte",)
_MONTHS = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
           "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
_NAMES = {v: k for k, v in _MONTHS.items()}
_DATE = r"(?P<d>1°|\d{1,2})\s*(?P<m>" + "|".join(_MONTHS) + r")\s+(?P<y>(?:19|20)\d{2})(?!\d)"
# Not anchored at the start: re-fetched texts can open with "Raccomandata", the
# entscheidsuche copies with their Kopfzeile ("Ticino Tribunale di appello ...
# 02.12.2016 15.2016.109 ... Incarto n. 15.2016.109 Lugano 2 dicembre 2016").
_HEADING_RE = re.compile(
    r"\bIncarto\s*n\.?\s*[\w./ -]{3,40}?\s+(?:[A-Za-z]{1,4}(?:/[A-Za-z]{1,4})?\s+)?"
    r"(?:Lugano|Bellinzona|Locarno|Mendrisio|Massagno|Giubiasco)\s*,?\s+" + _DATE
    + r"(?=[\s\S]{0,200}?In\s*nome\s*della\s*Repubblica)", re.I)
# The entscheidsuche Kopfzeile: "<Ticino|Tessin> <court> DD.MM.YYYY <docket>".
_KOPF_RE = re.compile(r"^\s*(?:Ticino|Tessin)\b.{0,200}?\b(\d{2})\.(\d{2})\.((?:19|20)\d{2})\b")
_HEAD_CHARS = 1000
# load_twin_dates value for a docket whose direct rows give no single usable
# date (they disagree, or carry none): unlike an absent docket (None), it
# confirms nothing — not even through the publication_date fallback.
AMBIGUOUS = "ambiguous"


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "")


def heading_date(text: str | None, today: str | None = None,
                  confirm: str | None = None) -> tuple[str | None, str]:
    """``confirm``, when given, is a date from a source independent of this
    row's own text (the direct scraper's twin row, or this row's own
    publication_date) — two sources agreeing outrank a disagreeing
    Kopfzeile, which is known to carry a stale date on some entscheidsuche
    copies (see ``kopfzeile_disagrees`` below)."""
    head = _flat(text)[:_HEAD_CHARS]
    m = _HEADING_RE.search(head)
    if not m or m.start() > 900:
        return None, "no_heading"
    d = 1 if m.group("d") == "1°" else int(m.group("d"))
    try:
        own = date(int(m.group("y")), _MONTHS[m.group("m").lower()], d).isoformat()
    except (ValueError, KeyError):
        return None, "invalid_date"
    if own > (today or date.today().isoformat()):
        return None, "heading_in_future"
    k = _KOPF_RE.search(head)
    if k:
        try:
            kd = date(int(k.group(3)), int(k.group(2)), int(k.group(1))).isoformat()
        except ValueError:
            kd = None
        if kd and kd != own:
            if confirm and confirm == own:
                return own, "heading_confirmed"
            return None, "kopfzeile_disagrees"
    return own, "heading"


def stated_in_text(iso: str, text: str | None) -> bool:
    """Is ``iso`` written in the text, as an Italian date ("10 ottobre 2023")
    or, after the heading, as a numeric dd.mm.yyyy / dd/mm/yyyy ("10.10.2023")?
    The numeric form is only read past the heading: the es Kopfzeile before it
    always carries the stored date, which would make this check vacuous."""
    try:
        y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    except ValueError:
        return False
    pat = r"\b%s\s*%s\s+%d\b" % ("1°" if d == 1 else d, _NAMES[m], y)
    alt = r"\b%d\s*%s\s+%d\b" % (d, _NAMES[m], y)
    numeric = r"\b%02d[.\/]%02d[.\/]%d\b" % (d, m, y)
    flat = _flat(text)
    if re.search(pat, flat, re.I) or re.search(alt, flat, re.I):
        return True
    h = _HEADING_RE.search(flat[:_HEAD_CHARS])
    return bool(h and re.search(numeric, flat[h.end():]))


def decide(obj: dict, today: str | None = None, twin_date: str | None = None) -> tuple[str, str | None]:
    """``twin_date``: the direct scraper's decision_date for the same docket
    (None when the docket has no direct row; AMBIGUOUS when its direct rows
    give no single date) — the independent corroboration a disagreeing
    Kopfzeile can be overridden by. Falls back to this row's own
    publication_date only when there is no direct row at all, in case that is
    how entscheidsuche encoded the true date."""
    pub = str(obj.get("publication_date") or "")[:10] or None
    confirm = None if twin_date == AMBIGUOUS else (twin_date or pub)
    own, how = heading_date(obj.get("full_text"), today, confirm)
    if own is None:
        return how, None
    cur = str(obj.get("decision_date") or "")[:10]
    if cur == own:
        return "agrees", None
    if not cur:
        return "filled", own
    if cur > own:
        return "stored_later_left_alone", None
    if not stated_in_text(cur, obj.get("full_text")):
        return "stored_not_in_text_left_alone", None
    return "corrected", own


def load_twin_dates(paths: list[Path]) -> dict[str, str]:
    """docket_number -> the direct scraper's decision_date, read from every
    given shard before any row is corrected (so it does not matter which
    shard — direct or entscheidsuche — is listed first). A docket whose
    direct rows disagree with each other on the date (or carry none) maps to
    AMBIGUOUS: never used to confirm a heading, and no publication_date
    fallback either. A docket with no direct row is absent from the dict."""
    seen: dict[str, set[str | None]] = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                # cheap pre-filter: skip entscheidsuche rows without a full parse
                if not stripped or '"source": "entscheidsuche"' in stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except ValueError:
                    continue
                if obj.get("court") not in COURTS or obj.get("source") == "entscheidsuche":
                    continue
                dn = obj.get("docket_number")
                if not dn:
                    continue
                seen.setdefault(dn, set()).add(str(obj.get("decision_date") or "")[:10] or None)
    return {dn: (next(iter(dates)) if len(dates) == 1 and None not in dates else AMBIGUOUS)
            for dn, dates in seen.items()}


def run(path: Path, apply: bool, examples: int, today: str | None = None,
        twin_dates: dict[str, str] | None = None) -> Counter:
    twin_dates = twin_dates or {}
    stats: Counter = Counter()
    shown = 0
    fout = None
    tmp_path = None
    if apply:
        # hidden, not *.jsonl: the build ingests every *.jsonl in the shard dir
        fd, tmp_path = tempfile.mkstemp(prefix=".", suffix=".jsonl.tmp", dir=str(path.parent))
        fout = os.fdopen(fd, "w", encoding="utf-8")
    replaced = False
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
                if obj.get("court") not in COURTS:
                    stats["other_court"] += 1
                    if fout:
                        fout.write(line)
                    continue
                stats["rows"] += 1
                outcome, new = decide(obj, today, twin_dates.get(obj.get("docket_number")))
                stats[outcome] += 1
                if new is None:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<36} {obj.get('decision_date')} -> {new}  [{outcome}]",
                          file=sys.stderr)
                if not fout:
                    continue
                rec = {"from": obj.get("decision_date"), "to": new, "method": "ti_heading", "at": now}
                if obj.get("date_restore"):
                    rec["prev"] = obj["date_restore"]
                obj["date_restore"] = rec
                obj["decision_date"] = new
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        if fout:
            fout.close()
            if stats["rows_changed"]:
                os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
                os.replace(tmp_path, str(path))
                replaced = True
    finally:
        # BaseException-safe (Ctrl-C, disk full): the temp file never outlives the run
        if fout and not fout.closed:
            fout.close()
        if tmp_path and not replaced and os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--apply", action="store_true", help="rewrite the shard (default: dry run)")
    ap.add_argument("--examples", type=int, default=12)
    a = ap.parse_args()
    twin_dates = load_twin_dates(a.shards)
    for shard in a.shards:
        print(f"== {shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = run(shard, a.apply, a.examples, twin_dates=twin_dates)
        print(json.dumps({"shard": str(shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
