#!/usr/bin/env python3
"""Week 44 (23-29 October 2026) poster harness: the week's numbers and a renderer.

Every week-44 design lives in scripts/weekly_poster_w44_<name>.py and exposes

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page

`week` (from load()) carries what the designs may show:

  added          1,139 new Swiss decisions in the window
  from, to       first and last day shown ("23.10." - "29.10.")
  week_no        44
  by_canton      {canton: n}  CH = federal courts
  by_court       {court: n}   bger@VD / bger@LU = Federal Supreme Court by seat
  languages      {lang: n}
  usual_canton   {canton: median of the previous eight weeks, as published}
  usual_total    the same for the whole week (1,000)
  international  the sources that arrived this week and are not counted in `added`:
                 total, on_switzerland, un, cjeu, icj

The figures are in weekly_poster_w44_data.json, whose note says how they were verified. Do not
take them from the snapshots this week: the UN decisions on Switzerland carry canton CH, so the
snapshot rule gives 1,445.

Usage:
  python3 scripts/poster_w44.py <name>:<lang> ...   # -> output/posters/w44/<lang>-<name>.png (+ .html)
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

END = date(2026, 10, 29)
DATA = Path(__file__).with_name("weekly_poster_w44_data.json")
OUT = wp.REPO / "output" / "posters" / "w44"


def load() -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    v = data["verified"]
    assert v["added"] == sum(v["by_canton"].values()) == sum(v["by_court"].values())
    assert v["added"] == sum(v["languages"].values())
    delta, _ = wp.load_week("origin/main", END)
    delta["added"] = v["added"]   # what archive_week records
    return {
        "added": v["added"],
        "from": END - timedelta(days=6),
        "to": END,
        "week_no": END.isocalendar().week,
        "by_canton": v["by_canton"],
        "by_court": v["by_court"],
        "languages": v["languages"],
        "usual_canton": data["usual_canton"],
        "usual_total": data["usual_total"],
        "international": data["international"],
        "delta": delta,
    }


def render(name: str, lang: str, week: dict | None = None) -> Path:
    mod = importlib.import_module(f"weekly_poster_w44_{name}")
    week = week or load()
    doc = mod.design(week, lang)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{lang}-{name}"
    path.with_suffix(".html").write_text(doc, encoding="utf-8")
    wp.render_png(doc, path.with_suffix(".png"), require_fonts=True)
    return path.with_suffix(".png")


# ── archive ─────────────────────────────────────────────────────────────

FINAL = {"de": "obligation2", "fr": "leman2", "it": "divisionismo3", "rm": "gravitad2", "en": "earth2"}
MAP = "2026-w44/map/"
MAP_URL = "opencaselaw.ch/posters/" + MAP
CAPTION = {  # one line per post, in the poster's language (Jonas: minimal captions)
    "de": "Woche {w} in der Schweizer Rechtsprechung: {n} neue Entscheide. Animierte Karte: " + MAP_URL,
    "fr": "Semaine {w} de la jurisprudence suisse : {n} nouvelles décisions. Carte animée : " + MAP_URL,
    "it": "Settimana {w} della giurisprudenza svizzera: {n} nuove decisioni. Mappa animata: " + MAP_URL,
    "rm": "Emna {w}: {n} novas decisiuns. " + MAP_URL,
    "en": "Week {w} in Swiss case law: {n} new decisions. Animated map: " + MAP_URL,
}


def archive(week: dict | None = None) -> dict:
    """Write the five final posters into docs/posters/ (WebP + index.json + latest/), as
    poster_w43.archive does. Writes files only; committing and pushing is a separate, approved step."""
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
    entry = ed.archive_week(week["delta"], rendered)
    index = ed.ARCHIVE / "index.json"
    manifest = json.loads(index.read_text(encoding="utf-8"))
    for w in manifest["weeks"]:
        if w["week"] == entry["week"]:
            w["map"] = MAP
    index.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return entry


if __name__ == "__main__":
    week = load()
    for arg in sys.argv[1:]:
        name, lang = arg.split(":")
        print(render(name, lang, week))
