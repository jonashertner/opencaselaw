#!/usr/bin/env python3
"""Week 42 (9-15 October 2026) poster harness: the week's numbers and a renderer.

Every week-42 design lives in scripts/weekly_poster_w42_<name>.py and exposes

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page

`week` (from load()) carries everything the designs may show:

  added          806 new decisions in the window
  from, to       first and last day shown ("9.10." - "15.10.")
  week_no        42
  total          decisions in the corpus at the end of the window
  by_canton      {canton: n}  CH = federal courts
  by_court       {court: n}   bger@VD / bger@LU = Federal Supreme Court by seat
  usual_canton   {canton: median of the previous eight weeks}
  usual_total    the same for the whole week
  outlook        acts that change on a date ahead (Fedlex), from stats.json
  housekeeping   corrections made this week (verified figures, see FACTS)

Figures that differ from the plain snapshot rule (see weekly_poster.weekly_delta):
  * Federal Supreme Court: the 112 rulings first scraped in the window (92
    Lausanne, 20 Luzern, counted in bger.jsonl), not its snapshot figure (189),
    which this week's duplicate folding inflated.
  * BGE: 0. No BGE was scraped this week; the snapshot +227 came from the
    build's duplicate folding after the 10-08 shard repair.
  * "removed" (15,013) is NOT shown as a loss: 13,799 of it is the duplicate
    BGE copies merged into one record per BGE on 15 October.

Usage:
  python3 scripts/poster_w42.py <name> <lang>     # -> output/posters/w42/<lang>-<name>.png (+ .html)
"""
from __future__ import annotations

import importlib
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import weekly_poster as wp  # noqa: E402

END = date(2026, 10, 15)
BGER_SCRAPED = {"bger@LU": 20, "bger@VD": 92, "bge@LU": 0, "bge@VD": 0}

# Verified this week (session notes 2026-10-14/15; shard counts before/after).
FACTS = {
    "bge_duplicates_merged": 13_799,      # bge rows 49,486 -> 35,687 on 15 Oct: one record per BGE
    "bge_trilingual_headnotes": 20_881,   # BGE Regeste now in German, French and Italian (searchable)
    "vaud_dates_corrected": 9_336,        # Vaud rulings now carry the date of their own heading
}

OUT = wp.REPO / "output" / "posters" / "w42"


def load() -> dict:
    delta, cur = wp.load_week("origin/main", END)
    wp.set_court_counts(delta, BGER_SCRAPED)
    usual = wp.usual_week(delta)
    first = (delta["from"] + timedelta(days=1)).date()
    return {
        "added": delta["added"],
        "from": first,
        "to": delta["to"].date(),
        "week_no": delta["to"].isocalendar().week,
        "total": cur.get("total"),
        "by_canton": dict(delta["by_canton"]),
        "by_court": dict(delta["by_court"]),
        "usual_canton": dict(usual["by_canton"]),
        "usual_total": usual["added"],
        "outlook": list(cur.get("upcoming_amendments") or []),
        "housekeeping": dict(FACTS),
        "delta": delta,      # the raw structure, for helpers in weekly_poster / weekly_poster_designs
        "cur": cur,
    }


def render(name: str, lang: str, week: dict | None = None) -> Path:
    mod = importlib.import_module(f"weekly_poster_w42_{name}")
    week = week or load()
    doc = mod.design(week, lang)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{lang}-{name}"
    path.with_suffix(".html").write_text(doc, encoding="utf-8")
    wp.render_png(doc, path.with_suffix(".png"))
    return path.with_suffix(".png")




# ── archive ─────────────────────────────────────────────────────────────

FINAL = {"de": "abstimmung", "fr": "releve", "it": "francobolli", "rm": "alpabzug", "en": "piste"}
CAPTION = {  # one line per post, in the poster's language (Jonas: minimal captions)
    "de": "Woche {w} in der Schweizer Rechtsprechung: {n} neue Entscheide. opencaselaw.ch",
    "fr": "Semaine {w} de la jurisprudence suisse : {n} nouvelles décisions. opencaselaw.ch",
    "it": "Settimana {w} della giurisprudenza svizzera: {n} nuove decisioni. opencaselaw.ch",
    "rm": "Emna {w}: {n} novas decisiuns. opencaselaw.ch",
    "en": "Week {w} in Swiss case law: {n} new decisions. opencaselaw.ch",
}


def archive(week: dict | None = None) -> dict:
    """Write the five final posters into docs/posters/ (WebP + index.json + latest/), as
    weekly_poster_editions --archive does. Writes files only; committing and pushing is a
    separate, approved step."""
    import weekly_poster_editions as ed

    week = week or load()
    rendered = {}
    for lang, name in FINAL.items():
        png = OUT / f"{lang}-{name}.png"
        if not png.exists():
            raise SystemExit(f"missing render: {png}")
        caption = CAPTION[lang].format(w=week["week_no"], n=wp.fmt(week["added"], lang))
        (OUT / f"{lang}-{name}.txt").write_text(caption + "\n", encoding="utf-8")
        rendered[lang] = (name, png, caption)
    return ed.archive_week(week["delta"], rendered)


if __name__ == "__main__":
    if sys.argv[1:] == ["archive"]:
        w = archive()
        print(f"archived {w['week']} ({w['added']} decisions): {[p['file'] for p in w['posters']]}")
    elif len(sys.argv) == 3:
        print(render(sys.argv[1], sys.argv[2]))
    else:
        raise SystemExit(__doc__)
