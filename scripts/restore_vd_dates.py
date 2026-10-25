#!/usr/bin/env python3
"""
restore_vd_dates.py — give Vaud Tribunal cantonal rows the date their
ruling's own heading states, and the language their text is written in:
vd_findinfo (es_vd_findinfo.jsonl, the frozen entscheidsuche copy of
findinfo-tc.vd.ch) and vd_gerichte (vd_gerichte.jsonl, the direct scraper of
prestations.vd.ch).

Why (report 2026-10-13; audit of the shard 2026-10-14):
  * 331 rows carry the ingest's 2021-01-01 placeholder ("AI 73/24 - 366/2025"
    dated 2021-01-01) although the text opens with the ruling's heading
    ("RECUSATION CIVILE Séance du 1 er mars 2023").
  * 6,334 dated rows disagree with that heading, 5,795 of them earlier than
    it: the stored value is mostly the date of the decision under review
    ("Jug / 2015 / 329" stored 2015-03-13, "Séance du 23 juillet 2015"; the
    Kopfzeile and publication_date say 23.07.2015 too). Ten sampled rows read
    against the text: the heading was the ruling's date in all ten.
  * vd_gerichte holds the same rulings with the same wrong dates for 3,673 rows
    ("Prononcé du 25 juin 2019" stored 2018-11-01; 119 of them scraped in
    2026-09 by the current scraper, so its date field is wrong for a subset).
    Repairing only vd_findinfo splits 33 pairs the cross-court dedup folds
    today, and the vd_gerichte copy comes back with the wrong date; both
    shards go together.
  * 141 rows are tagged "de" while their text (or, for rows without a body,
    their keyword line) is French; the ingest's detector falls back to "de".

The ruling's own date: the first "Arrêt / Jugement / Séance / Décision /
Ordonnance / Prononcé … du <date>" of the header block ("Audience du …" under a
Cour d'appel pénale header only), i.e. before the panel
("Composition", "Présidence") and before the operative text ("Statuant",
"Vu", "La Chambre …"). It counts only when it stands between the
header's underline rules (a date in the facts never does), is not in the
future, and a Kopfzeile date ("Vaud
Tribunal cantonal <Cour> 22.08.2017 …"), where present, agrees.

Left alone (ambiguous, see ambiguous()): a stored date exactly one year off on the
same day, and a stored date under 15 days off that the text never states.

Per row (courts vd_findinfo, vd_gerichte):
  * heading date found and differs from (or fills) decision_date -> decision_date
  * tagged "de", text French by stop-word count                  -> language "fr"
Every changed row gets a `date_restore` / `language_restore` stamp {from, to,
method, at}, so the change is exactly reversible.

Streaming, temp file + atomic replace (file mode kept), DRY RUN by default,
idempotent, stdlib only. es_vd_findinfo.jsonl is frozen; vd_gerichte.jsonl is
appended by the 01:00 scrape, so apply after it has logged Done. and before
the 03:30 full build.

Usage:
  python3 scripts/restore_vd_dates.py output/decisions/es_vd_findinfo.jsonl output/decisions/vd_gerichte.jsonl
  python3 scripts/restore_vd_dates.py output/decisions/es_vd_findinfo.jsonl output/decisions/vd_gerichte.jsonl --apply
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

COURTS = ("vd_findinfo", "vd_gerichte")
_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
}
_DATE = (r"(?P<d>1\s?er|\d{1,2})\s+(?P<m>" + "|".join(sorted(_MONTHS, key=len, reverse=True))
         + r")\s+(?P<y>(?:19|20)\d{2})(?!\d)")
# Only qualifiers of the designation may stand before "du <date>" ("Arrêt sur
# requête", "Jugement par défaut", "Décision du juge unique du …").
_QUAL = (r"(?:sur|incident(?:e)?|partiel(?:le)?|final(?:e)?|par|d[ée]faut|de|la|du|juge|"
         r"unique|pr[ée]sidentiel(?:le)?|requ[êe]te|r[ée]cusation|civile|p[ée]nale)")
_HEADING_RE = re.compile(
    r"\b(?P<kind>Arr[êe]t|Jugement|S[ée]ance|D[ée]cision|Ordonnance|Prononc[ée]|Audience)"
    r"(?:\s+" + _QUAL + r"){0,4}\s+du\s+" + _DATE, re.I)
# "Audience du 17 août 2023" heads every Cour d'appel pénale judgment and is its
# date; without it 81 of 1,468 portal-checked rows (all CAPE) kept the first
# instance's date (2026-10-19). Elsewhere an "Audience" is a hearing that the
# written ruling can follow by months (Cour civile CM10.017229: 18.08 vs
# 06.12.2010), so it counts only under a Cour d'appel pénale header.
# Matched with the spaces removed: the PDF extraction splits letters ("CO UR D’ APP EL PE NAL E").
_CAPE_RE = re.compile(r"COURD.APPELP[ÉE]NALE", re.I)
# End of the header block: the panel, or the start of the ruling itself.
_BLOCK_END_RE = re.compile(
    r"\b(?:Composition|Pr[ée]sidence|Statuant|Vu\b|Cause pendante|La Chambre|Le Tribunal|"
    r"La Cour|Le Juge|La Juge|Le Président|La Présidente)")
_KOPF_RE = re.compile(r"^\s*(?:Vaud|Waadt) Tribunal cantonal [^\n]{0,160}?\b(\d{2})\.(\d{2})\.((?:19|20)\d{2})\b")
_FR_RE = re.compile(r"\b(?:le|la|les|des|du|est|que|qui|une|dans|pour|par|au|aux|sur|pas|"
                    r"recours|arrêt|tribunal|cantonal)\b", re.I)
_DE_RE = re.compile(r"\b(?:der|die|das|und|nicht|ist|den|von|mit|dem|ein|eine|auf|für|zu|"
                    r"Urteil|Gericht)\b", re.I)
_HEAD_CHARS = 5000


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "")[:_HEAD_CHARS]


# The ruling's heading stands between the underline rules of the header block:
# "COUR D'APPEL PENALE ______ Séance du 23 juillet 2015 ______ Composition".
# A date in the facts ("dans une séance du 21 mai 2014", "Par jugement incident
# du 28 janvier 2011") never does; without this anchor the first match of a text
# that has no "Composition" line is such a date.
_RULE_BEFORE_RE = re.compile(r"_{5,}[^_]{0,60}$")
_RULE_AFTER_RE = re.compile(r"^\W{0,3}(?:_{3,}|Composition|Pr[ée]sidence)")


def heading_date(text: str | None, docket: str | None = None,
                 today: str | None = None) -> tuple[str | None, str]:
    """(the ruling's own date from its header block, reason)."""
    t = _flat(text)
    end = _BLOCK_END_RE.search(t)
    m = None
    for cand in _HEADING_RE.finditer(t, 0, end.start() if end else len(t)):
        if cand.group("kind").lower() == "audience" and not _CAPE_RE.search(re.sub(r"\s+", "", t[:cand.start()])):
            continue
        if _RULE_BEFORE_RE.search(t[max(0, cand.start() - 70):cand.start()]) and \
                _RULE_AFTER_RE.search(t[cand.end():cand.end() + 40]):
            m = cand
            break
    if not m:
        return None, "no_heading"
    d = 1 if m.group("d").replace(" ", "").lower() == "1er" else int(m.group("d"))
    try:
        own = date(int(m.group("y")), _MONTHS[m.group("m").lower()], d).isoformat()
    except (ValueError, KeyError):
        return None, "invalid_date"
    if own > (today or date.today().isoformat()):
        return None, "heading_in_future"
    # No bound from the number or from publication_date: a findinfo number
    # ("HC / 2018 / 27", "Jug / 2020 / 2") carries the PUBLICATION year, and
    # publication can come long after the ruling; publication_date in the
    # entscheidsuche copy often holds the ruling date itself. The structural
    # anchor above is what keeps other dates out.
    k = _KOPF_RE.search(text or "")
    if k:
        try:
            kd = date(int(k.group(3)), int(k.group(2)), int(k.group(1))).isoformat()
        except ValueError:
            kd = None
        if kd and kd != own:
            return None, "kopfzeile_disagrees"
    return own, "heading"


def text_language(obj: dict) -> str | None:
    body = f"{obj.get('regeste') or ''} {obj.get('full_text') or ''}"[:_HEAD_CHARS]
    fr, de = len(_FR_RE.findall(body)), len(_DE_RE.findall(body))
    if fr > de:
        return "fr"
    if de > fr:
        return "de"
    return None


_ANY_DATE_RE = re.compile(_DATE, re.I)


def _dates_in_text(text: str | None) -> set[str]:
    out = set()
    for m in _ANY_DATE_RE.finditer(_flat(text)):
        d = 1 if m.group("d").replace(" ", "").lower() == "1er" else int(m.group("d"))
        try:
            out.add(date(int(m.group("y")), _MONTHS[m.group("m").lower()], d).isoformat())
        except (ValueError, KeyError):
            pass
    return out


def ambiguous(cur: str, own: str, text: str | None) -> str | None:
    """Reason to leave a disagreement alone, or None. Checked 2026-10-14 on the
    163 rows the live scraper stored since 2026-09 that disagree with their
    heading: where the stored date is a dated event of the text (the dispositif
    "communiqué aux parties le …", the lower court's ruling, a filing) or is
    impossible, the heading is the ruling's date; but a stored date one year
    off on the same day may be the court's own heading typo (written early in
    the year), and a stored date a few days off that the text never states
    cannot be judged from the text."""
    try:
        a, b = date.fromisoformat(cur), date.fromisoformat(own)
    except ValueError:
        return None
    if (a.month, a.day) == (b.month, b.day) and abs(a.year - b.year) == 1:
        return "year_off_by_one"
    if abs((a - b).days) < 15 and cur not in _dates_in_text(text):
        return "small_unexplained_gap"
    return None


def decide(obj: dict, today: str | None = None) -> tuple[list[str], dict]:
    """(outcomes, field changes)."""
    out: list[str] = []
    changes: dict = {}
    own, how = heading_date(obj.get("full_text"), obj.get("docket_number"), today)
    cur = str(obj.get("decision_date") or "")[:10]
    why = ambiguous(cur, own, obj.get("full_text")) if own and cur and own != cur \
        and not cur.endswith("-01-01") else None
    if own is None:
        out.append(how)
    elif own == cur:
        out.append("date_agrees")
    elif why:
        out.append(why)
    else:
        out.append("date_filled" if not cur or cur.endswith("-01-01") else "date_corrected")
        changes["decision_date"] = own
    if obj.get("language") == "de" and text_language(obj) == "fr":
        out.append("language_fixed")
        changes["language"] = "fr"
    return out, changes


def run(path: Path, apply: bool, examples: int, today: str | None = None) -> Counter:
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
                outcomes, changes = decide(obj, today)
                stats.update(outcomes)
                if changes and shown < examples:
                    shown += 1
                    print(f"  {obj.get('decision_id'):<44} {changes} [{','.join(outcomes)}]", file=sys.stderr)
                if not changes:
                    if fout:
                        fout.write(line)
                    continue
                stats["rows_changed"] += 1
                if not fout:
                    continue
                for field, stamp in (("decision_date", "date_restore"), ("language", "language_restore")):
                    if field in changes:
                        rec = {"from": obj.get(field), "to": changes[field],
                               "method": "vd_heading" if field == "decision_date" else "vd_text_language",
                               "at": now}
                        if obj.get(stamp):
                            rec["prev"] = obj[stamp]
                        obj[stamp] = rec
                        obj[field] = changes[field]
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
    ap.add_argument("--examples", type=int, default=15)
    a = ap.parse_args()
    for shard in a.shards:
        print(f"== {shard} ({'APPLY' if a.apply else 'dry run'})", file=sys.stderr)
        stats = run(shard, a.apply, a.examples)
        print(json.dumps({"shard": str(shard), **dict(sorted(stats.items()))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
