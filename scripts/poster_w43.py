#!/usr/bin/env python3
"""Week 43 (16-22 October 2026) poster harness: the week's numbers and a renderer.

Every week-43 design lives in scripts/weekly_poster_w43_<name>.py and exposes

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page

`week` (from load()) carries what the designs may show:

  added          769 new decisions in the window
  from, to       first and last day shown ("16.10." - "22.10.")
  week_no        43
  total          decisions in the corpus at the end of the window
  by_canton      {canton: n}  CH = federal courts
  by_court       {court: n}   bger@VD / bger@LU = Federal Supreme Court by seat
  usual_canton   {canton: median of the previous eight weeks, as published}
  usual_total    the same for the whole week (1,023)
  prev_canton    {canton: n} of week 42 as published (poster_w42.load, with its corrections)
  prev_total     806
  series         [(last day, added)] per week since April (see SERIES)
  bge            the week's new leading decisions (see BGE)
  arrivals       [(decision date, day it entered the corpus)] per new decision (data file)

Verified row by row (23 October): every decision appended to the court shards between the
15 and 22 October build cut-offs (03:30 UTC) was looked up by decision_id in the live database.
769 Swiss decisions are present; the cantons match the snapshot rule exactly. Two federal
figures differ from it and are overridden: the Federal Supreme Court has 136 new rows (113
Lausanne, 23 Luzern), not 137, and the snapshot's one ECtHR judgment counted under CH is not a
new row (this week's three ECtHR judgments are against other states, canton CE, excluded as
always). Not present: 3 Vaud and 2 Jura rows folded as duplicates by the build.

Usage:
  python3 scripts/poster_w43.py <name> <lang>     # -> output/posters/w43/<lang>-<name>.png (+ .html)
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import weekly_poster as wp  # noqa: E402

END = date(2026, 10, 22)
DATA = Path(__file__).with_name("weekly_poster_w43_data.json")
OUT = wp.REPO / "output" / "posters" / "w43"


# Figures published for earlier weeks that differ from the plain snapshot rule (see poster_w42).
PUBLISHED_FIXES = {date(2026, 10, 8): {"bger": 175}}     # week 41: Federal Supreme Court rows first scraped


def usual_week(delta: dict, weeks: int = 8) -> dict:
    """wp.usual_week, but each past week as published: weeks 41 and 42 carry their corrections
    (801 and 806, not the raw 724 and 1,110), so the median compares like with like."""
    import statistics
    import poster_w42

    fixes = {**PUBLISHED_FIXES, poster_w42.END: poster_w42.BGER_SCRAPED}
    snaps = wp.load_snapshots("origin/main", 7 * weeks + 30)
    end = delta["to"].date()
    past = []
    for k in range(1, weeks + 1):
        e = end - timedelta(days=7 * k)
        base, cur = wp.pick_window(snaps, e)
        lo, hi = wp.parse_ts(base["generated_at"]), wp.parse_ts(cur["generated_at"])
        d = wp.weekly_delta([s for s in snaps if lo <= wp.parse_ts(s["generated_at"]) <= hi])
        if e in fixes:
            wp.set_court_counts(d, fixes[e])
        past.append(d)
    codes = [*wp.TILES, wp.FEDERAL]
    return {"by_canton": {c: statistics.median(w["by_canton"].get(c, 0) for w in past) for c in codes},
            "added": statistics.median(w["added"] for w in past), "weeks": weeks}


def load() -> dict:
    import poster_w42

    delta, cur = wp.load_week("origin/main", END)
    usual = usual_week(delta)
    prev = poster_w42.load()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    v = data["verified"]
    wp.set_court_counts(delta, {"bger@VD": v["bger@VD"], "bger@LU": v["bger@LU"], "ecthr_chamber": 0})
    assert delta["added"] == v["added"] == len(data["arrivals"]), (delta["added"], v["added"])
    return {
        "added": delta["added"],
        "from": (delta["from"] + timedelta(days=1)).date(),
        "to": delta["to"].date(),
        "week_no": delta["to"].isocalendar().week,
        "total": cur.get("total"),
        "by_canton": dict(delta["by_canton"]),
        "by_court": dict(delta["by_court"]),
        "usual_canton": dict(usual["by_canton"]),
        "usual_total": usual["added"],
        "prev_canton": dict(prev["by_canton"]),
        "prev_total": prev["added"],
        "prev_court": dict(prev["by_court"]),
        "series": [tuple(r) for r in data["series"]],
        "bge": data["bge"],
        "arrivals": [tuple(r) for r in data["arrivals"]],
        "delta": delta,
        "cur": cur,
    }


def render(name: str, lang: str, week: dict | None = None) -> Path:
    mod = importlib.import_module(f"weekly_poster_w43_{name}")
    week = week or load()
    doc = mod.design(week, lang)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{lang}-{name}"
    path.with_suffix(".html").write_text(doc, encoding="utf-8")
    wp.render_png(doc, path.with_suffix(".png"), require_fonts=True)
    return path.with_suffix(".png")


# ── archive ─────────────────────────────────────────────────────────────

FINAL = {"de": "leitentscheide", "fr": "attente", "it": "bilance", "rm": "tessla", "en": "index"}
CAPTION = {  # one line per post, in the poster's language (Jonas: minimal captions)
    "de": "Woche {w} in der Schweizer Rechtsprechung: {n} neue Entscheide. opencaselaw.ch",
    "fr": "Semaine {w} de la jurisprudence suisse : {n} nouvelles décisions. opencaselaw.ch",
    "it": "Settimana {w} della giurisprudenza svizzera: {n} nuove decisioni. opencaselaw.ch",
    "rm": "Emna {w}: {n} novas decisiuns. opencaselaw.ch",
    "en": "Week {w} in Swiss case law: {n} new decisions. opencaselaw.ch",
}


def archive(week: dict | None = None) -> dict:
    """Write the five final posters into docs/posters/ (WebP + index.json + latest/), as
    poster_w42.archive does. Writes files only; committing and pushing is a separate, approved step."""
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
    week = load()
    for arg in sys.argv[1:]:
        name, lang = arg.split(":")
        print(render(name, lang, week))
