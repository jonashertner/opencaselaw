#!/usr/bin/env python3
"""Week 48 (20 - 26 November 2026) poster harness: the week's numbers and a renderer.

Every week-47 design lives in scripts/weekly_poster_w48_<name>.py and exposes

    design(week: dict, lang: str) -> str      # a complete 1080x1350 HTML page

`week` (from load()) carries what the designs may show:

  added          new Swiss decisions in the window (usual arrivals only)
  from, to       first and last day shown (20.11. - 26.11.)
  week_no        48
  by_canton      {canton: n}  CH = federal courts and bodies
  by_court       {court: n}   bger@VD / bger@LU = Federal Supreme Court by seat
  languages      {lang: n}
  by_year        {year of decision: n}
  by_month       {"YYYY-MM" of decision: n}
  dates          {"CH" | "KT": {day of decision: n}}
  by_arrival_day {"YYYY-MM-DD": n}  the build that picked the row up, Fri - Thu
  fresh_30d      decided in the 30 days before the week's end
  centroid_lv03  [E, N] km, the arrivals' centre of gravity by court seat

The figures are in weekly_poster_w48_data.json, written by weekly_poster_w48_build_data.py from the
public nightly deltas.

Usage:
  python3 scripts/poster_w48.py <name>:<lang> ...   # -> output/posters/w48/<lang>-<name>.png (+ .html)
  python3 scripts/poster_w48.py --archive           # the five of FINAL into docs/posters/
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

END = date(2026, 11, 26)
DATA = Path(__file__).with_name("weekly_poster_w48_data.json")
OUT = wp.REPO / "output" / "posters" / "w48"


def load(with_delta: bool = False) -> dict:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    v = data["verified"]
    assert v["added"] == sum(v["by_canton"].values()) == sum(v["by_court"].values())
    assert v["added"] == sum(v["languages"].values()) == sum(v["by_year"].values())
    assert v["added"] == sum(sum(d.values()) for d in v["dates"].values())
    assert v["added"] == sum(v["by_arrival_day"].values())
    week = {
        "added": v["added"],
        "from": END - timedelta(days=6),
        "to": END,
        "week_no": END.isocalendar().week,
        "by_canton": v["by_canton"],
        "by_court": v["by_court"],
        "languages": v["languages"],
        "by_year": {int(y): n for y, n in v["by_year"].items()},
        "by_month": v["by_month"],
        "dates": v["dates"],
        "by_arrival_day": v["by_arrival_day"],
        "fresh_30d": v["fresh_30d"],
        "centroid_lv03": v["centroid_lv03"],
        "preliminary": data.get("preliminary", False),
    }
    if with_delta:  # only the archive step needs the snapshot window (git history of docs/stats.json)
        delta, _ = wp.load_week("origin/main", END)
        delta["added"] = v["added"]
        week["delta"] = delta
    return week


def render(name: str, lang: str, week: dict | None = None) -> Path:
    mod = importlib.import_module(f"weekly_poster_w48_{name}")
    week = week or load()
    doc = mod.design(week, lang)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{lang}-{name}"
    path.with_suffix(".html").write_text(doc, encoding="utf-8")
    wp.render_png(doc, path.with_suffix(".png"), require_fonts=True)
    return path.with_suffix(".png")


# ── archive ─────────────────────────────────────────────────────────────

FINAL = {"de": "bern", "fr": "jungfrau", "it": "mezzogiorno", "rm": "schraffas", "en": "orbit"}
CAPTION = {  # one line per post, in the poster's language (Jonas: minimal captions)
    "de": "Woche {w} in der Schweizer Rechtsprechung: {n} neue Entscheide.",
    "fr": "Semaine {w} de la jurisprudence suisse : {n} nouvelles décisions.",
    "it": "Settimana {w} della giurisprudenza svizzera: {n} nuove decisioni.",
    "rm": "Emna {w}: {n} novas decisiuns.",
    "en": "Week {w} in Swiss case law: {n} new decisions.",
}


def archive(week: dict | None = None) -> dict:
    """Write the five final posters into docs/posters/ (WebP + index.json + latest/), as
    poster_w46.archive does. Writes files only; committing and pushing is a separate, approved step."""
    import weekly_poster_editions as ed

    week = week or load(with_delta=True)
    if week["preliminary"]:
        raise SystemExit("data is preliminary (no 2026-11-26 delta); rebuild the data before archiving")
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
    if sys.argv[1:] == ["--archive"]:
        print(archive())
        raise SystemExit
    week = load()
    for arg in sys.argv[1:]:
        name, lang = arg.split(":")
        print(render(name, lang, week))
