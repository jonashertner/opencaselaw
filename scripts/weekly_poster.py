#!/usr/bin/env python3
"""Weekly LinkedIn poster: the decisions added to the corpus this week, one dot each.

Reads the committed daily snapshots of docs/stats.json from git history (no
database access, no network for the data), diffs the newest snapshot against
the one closest to seven days earlier, and renders a 1080x1350 (4:5) poster as
PNG via headless Chromium, plus a caption text file.

    .venv/bin/python scripts/weekly_poster.py                 # latest week, English
    .venv/bin/python scripts/weekly_poster.py --lang de --theme paper
    .venv/bin/python scripts/weekly_poster.py --end 2026-09-25

Counting rule: see weekly_delta. Every rise of a court's record count above its
highest count so far in the window is an addition, so a clean-up night (dedupe,
re-key) cannot hide the rulings added before it. The headline is a lower bound;
removed records are reported separately.
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATS_PATH = "docs/stats.json"

W, H = 1080, 1350  # LinkedIn portrait, 4:5
MARGIN = 72

# Tile-grid Switzerland: (column, row). The Confederation takes the free
# north-west corner at double width.
TILES = {
    "BS": (2, 0), "BL": (3, 0), "SH": (4, 0), "TG": (5, 0),
    "JU": (1, 1), "SO": (2, 1), "AG": (3, 1), "ZH": (4, 1), "AR": (5, 1), "AI": (6, 1),
    "NE": (1, 2), "BE": (2, 2), "LU": (3, 2), "ZG": (4, 2), "SZ": (5, 2), "SG": (6, 2),
    "VD": (1, 3), "FR": (2, 3), "OW": (3, 3), "NW": (4, 3), "GL": (5, 3), "GR": (6, 3),
    "GE": (0, 4), "VS": (2, 4), "UR": (4, 4), "TI": (5, 4),
}
FEDERAL = "CH"
# A canton's catch-all court code: rows a scraper could not attribute to the court that
# decided them. When a repair files them under that court, the named court's count rises
# and the catch-all's falls; weekly_delta counts that as re-filed, not new. zh_gerichte
# held 1,438 such rows until the 2026-10 ZH portal migration moved them.
CATCH_ALL = {"zh_gerichte"}
COLS, ROWS = 7, 5
CELL_W, CELL_H, GAP = 128, 144, 6
LABEL_H = 28  # room above the dots for the canton code and its count
UNITS = (1, 2, 5, 10, 20, 50)
PITCHES = (10, 9, 8, 7, 6)

THEMES = {
    # Swiss red ground, white marks.
    "red": {"bg": "#c01622", "ink": "#ffffff", "soft": "rgba(255,255,255,.74)",
            "ghost": "rgba(255,255,255,.17)", "rule": "rgba(255,255,255,.38)"},
    # The site's paper, red marks.
    "paper": {"bg": "#FCFCFB", "ink": "#1B1A17", "soft": "#5A574F",
              "ghost": "rgba(27,26,23,.11)", "rule": "rgba(27,26,23,.22)", "dot": "#c01622"},
}

STRINGS = {
    "en": {
        "week": "Week {w}, {y}",
        "headline": "court decisions added to the open corpus of Swiss case law this week",
        "unit1": "One dot is one decision.",
        "unitn": "One dot is {u} decisions.",
        "federal": "Federal",
        "type_note": "The size of a canton\u2019s letters is its share of the week.",
        "legend": "White blocks: German-speaking cantons. Black: French-speaking. Red letters: Ticino. No block: federal courts. Bilingual cantons by majority language.",
        "none": "Nothing new this week from {l}.",
        "f_total": "decisions since 1875",
        "f_courts": "courts and authorities",
        "f_cites": "resolved citations",
        "removed": "{n} records removed in clean-up over the same days.",
        "free": "Free to search, cite and download.",
        "million": "{v} m",
        "caption": (
            "Week {w} in Swiss case law: {n} decisions added to OpenCaseLaw between {a} and {b}.\n\n"
            "Most came from {top}. The corpus now holds {total} decisions from {courts} courts "
            "and authorities, back to 1875.{removed}\n\n"
            "Everything is free to search, cite and download: opencaselaw.ch"
        ),
        "cap_removed": " We also removed {n} duplicate or mis-keyed records.",
    },
    "de": {
        "week": "Woche {w}, {y}",
        "headline": "Entscheide, die diese Woche neu im offenen Schweizer Rechtsprechungskorpus sind",
        "unit1": "Ein Punkt ist ein Entscheid.",
        "unitn": "Ein Punkt sind {u} Entscheide.",
        "federal": "Bund",
        "type_note": "Die Gr\u00f6sse der Buchstaben ist der Anteil des Kantons an der Woche.",
        "legend": "Weisse Fl\u00e4chen: Deutschschweizer Kantone. Schwarz: Romandie. Rote Buchstaben: Tessin. Ohne Fl\u00e4che: Gerichte des Bundes. Zweisprachige Kantone nach Mehrheitssprache.",
        "none": "Diese Woche nichts Neues aus {l}.",
        "f_total": "Entscheide seit 1875",
        "f_courts": "Gerichte und Behörden",
        "f_cites": "aufgelöste Zitate",
        "removed": "{n} Einträge in denselben Tagen bereinigt.",
        "free": "Frei durchsuchbar, zitierbar, herunterladbar.",
        "million": "{v} Mio.",
        "caption": (
            "Woche {w} in der Schweizer Rechtsprechung: {n} Entscheide sind zwischen dem {a} und dem {b} "
            "neu bei OpenCaseLaw dazugekommen.\n\n"
            "Die meisten stammen von {top}. Der Korpus umfasst jetzt {total} Entscheide von {courts} "
            "Gerichten und Behörden, zurück bis 1875.{removed}\n\n"
            "Alles ist frei durchsuchbar, zitierbar und herunterladbar: opencaselaw.ch"
        ),
        "cap_removed": " Ausserdem haben wir {n} doppelte oder falsch zugeordnete Einträge entfernt.",
    },
    "fr": {
        "week": "Semaine {w}, {y}",
        "headline": "décisions ajoutées cette semaine au corpus ouvert de jurisprudence suisse",
        "unit1": "Un point, une décision.",
        "unitn": "Un point, {u} décisions.",
        "federal": "Confédération",
        "type_note": "La taille des lettres d\u2019un canton est sa part de la semaine.",
        "legend": "Blocs blancs: cantons al\u00e9maniques. Noirs: cantons romands. Lettres rouges: Tessin. Sans bloc: tribunaux f\u00e9d\u00e9raux. Cantons bilingues selon la langue majoritaire.",
        "none": "Rien de nouveau cette semaine pour {l}.",
        "f_total": "décisions depuis 1875",
        "f_courts": "tribunaux et autorités",
        "f_cites": "citations résolues",
        "removed": "{n} entrées retirées lors d'un nettoyage sur la même période.",
        "free": "Recherche, citation et téléchargement libres.",
        "million": "{v} mio",
        "caption": (
            "Semaine {w} dans la jurisprudence suisse: {n} décisions ajoutées à OpenCaseLaw entre le {a} "
            "et le {b}.\n\n"
            "La plupart viennent de {top}. Le corpus compte désormais {total} décisions de {courts} "
            "tribunaux et autorités, depuis 1875.{removed}\n\n"
            "Tout est libre d'accès, à rechercher, citer et télécharger: opencaselaw.ch"
        ),
        "cap_removed": " Nous avons aussi retiré {n} entrées en double ou mal attribuées.",
    },
}


# ── data ──────────────────────────────────────────────────────────────────────

def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_snapshots(ref: str, since_days: int = 21, repo: Path = REPO) -> list[dict]:
    """Every committed docs/stats.json of the last `since_days`, oldest first."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, check=True).stdout

    hashes = git("log", "--format=%H", f"--since={since_days} days ago", ref, "--", STATS_PATH).split()
    snaps: dict[str, dict] = {}
    for h in hashes:
        try:
            d = json.loads(git("show", f"{h}:{STATS_PATH}"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue  # a torn or half-written snapshot is not a data point
        if d.get("generated_at") and d.get("by_court"):
            snaps[d["generated_at"]] = d
    return drop_implausible([snaps[k] for k in sorted(snaps, key=parse_ts)])


def drop_implausible(snaps: list[dict]) -> list[dict]:
    """Without snapshots whose total is far below the window's median.

    A stats.json generated against a development database (a few ten thousand rows) has reached
    origin/main before; diffing against it would report the whole corpus as removed or added.
    """
    if not snaps:
        return snaps
    totals = sorted(s["total"] for s in snaps)
    median = totals[len(totals) // 2]
    keep = [s for s in snaps if s["total"] >= 0.8 * median]
    for s in snaps:
        if s["total"] < 0.8 * median:
            print(f"ignoring implausible snapshot {s['generated_at']}: total {s['total']} vs median {median}",
                  file=sys.stderr)
    return keep


def pick_window(snaps: list[dict], end: date | None = None, days: int = 7) -> tuple[dict, dict]:
    """(baseline, current): the newest snapshot up to `end`, and the last snapshot of the day `days` earlier.

    A day's build writes several snapshots (post-swap refresh, final). Ending the baseline on a day's
    last one makes the week exactly `days` builds, so no build is split across two weeks.
    """
    if end is not None:
        snaps = [s for s in snaps if parse_ts(s["generated_at"]).date() <= end]
    if len(snaps) < 2:
        raise SystemExit("need at least two stats.json snapshots in git history for this window")
    cur = snaps[-1]
    last_day = parse_ts(cur["generated_at"]).date() - timedelta(days=days)
    earlier = [s for s in snaps[:-1] if parse_ts(s["generated_at"]).date() <= last_day]
    base = earlier[-1] if earlier else snaps[0]
    return base, cur


def weekly_delta(snaps: list[dict]) -> dict:
    """Additions over a window of snapshots (oldest first), per court, canton and night.

    Per (court, canton), every rise above the highest count seen so far in the
    window is an addition. A clean-up night therefore cannot cancel the rulings
    added before it, and a count that dips and recovers is not counted twice.
    Rulings added after a clean-up, below the earlier peak, are missed: the
    result is a lower bound. Records removed = peak minus final count.

    Re-filing: rulings leaving a canton's catch-all bucket (CATCH_ALL) are taken off
    the additions of that canton's other courts, up to what the bucket lost, and are
    not counted as removed either ("refiled" per canton). New rulings that arrive in
    the same canton in a re-filing week are hidden by it: still a lower bound.
    """
    base, cur = snaps[0], snaps[-1]
    swiss = lambda canton: canton in TILES or canton == FEDERAL  # "CE" = non-Swiss ECtHR
    # Federal Supreme Court by seat ("bger@LU", "bger@VD") only when every snapshot in the
    # window carries the split; mixing split and unsplit snapshots would count a whole
    # seat's history as new.
    split = all(_seat_split_ok(s) for s in snaps)
    chambered = all(_chambers_ok(s) for s in snaps)
    rows = lambda snap: _chamber_rows(snap, _seat_rows(snap) if split else snap["by_court"]) if chambered \
        else (_seat_rows(snap) if split else snap["by_court"])  # noqa: E731
    # A court can appear once per canton (ECtHR: Swiss-respondent "CH" vs other states "CE").
    peak = {(c["court"], c["canton"]): c["count"] for c in rows(base) if swiss(c["canton"])}
    by_key: dict[tuple[str, str], int] = {}
    by_day: dict[date, dict[str, int]] = {}
    by_court_day: dict[date, dict[str, int]] = {}
    for snap in snaps[1:]:
        day = by_day.setdefault(parse_ts(snap["generated_at"]).date(), {})
        court_day = by_court_day.setdefault(parse_ts(snap["generated_at"]).date(), {})
        for c in rows(snap):
            key = (c["court"], c["canton"])
            if not swiss(c["canton"]):
                continue
            gain = c["count"] - peak.get(key, 0)
            if gain > 0:
                peak[key] = c["count"]
                by_key[key] = by_key.get(key, 0) + gain
                day[c["canton"]] = day.get(c["canton"], 0) + gain
                court_day[c["court"]] = court_day.get(c["court"], 0) + gain
    final = {(c["court"], c["canton"]): c["count"] for c in rows(cur)}
    # Rulings that leave a canton's catch-all bucket for a named court of the same canton
    # were in the corpus already: re-filed, not new. Take them off that canton's additions
    # (largest court and night first) and out of the removed count.
    refiled: dict[str, int] = {}
    for (court, canton), top in peak.items():
        if court not in CATCH_ALL:
            continue
        gains = {k: n for k, n in by_key.items() if k[1] == canton and k[0] not in CATCH_ALL}
        moved = min(max(0, top - final.get((court, canton), 0)), sum(gains.values()))
        if not moved:
            continue
        refiled[canton] = refiled.get(canton, 0) + moved
        left = moved
        for k in sorted(gains, key=lambda k: (-gains[k], k)):
            take = min(left, by_key[k])
            by_key[k] -= take
            left -= take
        left = moved
        for day in sorted(by_day, key=lambda d: (-by_day[d].get(canton, 0), d)):
            take = min(left, by_day[day].get(canton, 0))
            if take:
                by_day[day][canton] -= take
            left -= take
    by_key = {k: n for k, n in by_key.items() if n}
    by_court: dict[str, int] = {}
    by_canton: dict[str, int] = {}
    for (court, canton), n in by_key.items():
        by_court[court] = by_court.get(court, 0) + n
        by_canton[canton] = by_canton.get(canton, 0) + n
    return {
        "by_key": by_key,
        "by_court": by_court,
        "by_canton": by_canton,
        "by_day": by_day,
        "by_court_day": by_court_day,
        "added": sum(by_key.values()),
        "federal_split": split,
        "removed": sum(n - final.get(key, 0) for key, n in peak.items() if n > final.get(key, 0))
                   - sum(refiled.values()),
        "refiled": refiled,
        "from": parse_ts(base["generated_at"]),
        "to": parse_ts(cur["generated_at"]),
    }


def _seat_split_ok(snap: dict) -> bool:
    """The snapshot splits every court it names, and each split adds up to the court's count."""
    seats = snap.get("federal_seats") or {}
    if not seats:
        return False
    counts = {c["court"]: c["count"] for c in snap["by_court"] if c["canton"] == FEDERAL}
    return all(court in counts and sum(split.values()) == counts[court] for court, split in seats.items())


def _seat_rows(snap: dict) -> list[dict]:
    """by_court with each split federal court replaced by one row per seat."""
    seats = snap.get("federal_seats") or {}
    out = []
    for c in snap["by_court"]:
        split = seats.get(c["court"]) if c["canton"] == FEDERAL else None
        if split:
            out += [{**c, "court": f"{c['court']}@{seat}", "count": n} for seat, n in sorted(split.items())]
        else:
            out.append(c)
    return out


def _chambers_ok(snap: dict) -> bool:
    """The snapshot carries court_chambers and each source's codes add up to its count."""
    ch = snap.get("court_chambers") or {}
    if not ch:
        return False
    counts = {c["court"]: c["count"] for c in snap["by_court"]}
    return all(court in counts and sum(per.values()) == counts[court] for court, per in ch.items())


def _chamber_rows(snap: dict, rows: list[dict]) -> list[dict]:
    """rows with each multi-court source replaced by one row per recorded court code ("ge_gerichte#ATAS")."""
    ch = snap.get("court_chambers") or {}
    out = []
    for c in rows:
        per = ch.get(c["court"])
        if per:
            out += [{**c, "court": f"{c['court']}#{code}", "count": n} for code, n in sorted(per.items())]
        else:
            out.append(c)
    return out


def load_week(ref: str = "origin/main", end: date | None = None,
              seats: dict[str, int] | None = None) -> tuple[dict, dict]:
    """(delta, newest snapshot) for the week ending at `end` (default: the newest snapshot).

    `seats` ({"LU": n, "VD": n}) splits the Federal Supreme Court for a week whose snapshots do not
    all carry federal_seats yet: the week's figure is divided in the proportion of these counts (the
    week's new rows by docket prefix, counted in the database). From the first week whose snapshots
    all carry the field, the split is exact and `seats` is ignored.
    """
    since = 21 if end is None else (date.today() - end).days + 21
    snaps = load_snapshots(ref, since)
    base, cur = pick_window(snaps, end)
    lo, hi = parse_ts(base["generated_at"]), parse_ts(cur["generated_at"])
    delta = weekly_delta([s for s in snaps if lo <= parse_ts(s["generated_at"]) <= hi])
    if seats and not delta["federal_split"]:
        apply_seats(delta, seats)
    return delta, cur


def usual_week(delta: dict, ref: str = "origin/main", weeks: int = 8) -> dict:
    """A typical week before this one: per canton (and in total) the median of the previous `weeks` weeks.

    The median, not the mean, so a single clean-up or re-key week (14,000 Vaud rows in September)
    does not move it.
    """
    import statistics

    snaps = load_snapshots(ref, 7 * weeks + 30)
    end = delta["to"].date()
    past = []
    for k in range(1, weeks + 1):
        base, cur = pick_window(snaps, end - timedelta(days=7 * k))
        lo, hi = parse_ts(base["generated_at"]), parse_ts(cur["generated_at"])
        past.append(weekly_delta([s for s in snaps if lo <= parse_ts(s["generated_at"]) <= hi]))
    codes = [*TILES, FEDERAL]
    courts = {c for w in past for c in w["by_court"]} | set(delta["by_court"])
    return {"by_canton": {c: statistics.median(w["by_canton"].get(c, 0) for w in past) for c in codes},
            "by_court": {c: statistics.median(w["by_court"].get(c, 0) for w in past) for c in courts
                         if "@" not in c or all(c in w["by_court"] for w in past)},
            "added": statistics.median(w["added"] for w in past), "weeks": weeks}


def apply_seats(delta: dict, seats: dict[str, int]) -> None:
    """Replace the week's bger row by bger@LU / bger@VD in the proportion of `seats` (see load_week)."""
    key = ("bger", FEDERAL)
    n = delta["by_key"].get(key, 0)
    if not n or sum(seats.values()) <= 0:
        return
    total = sum(seats.values())
    exact = {seat: n * v / total for seat, v in seats.items()}
    split = {seat: int(x) for seat, x in exact.items()}
    for seat in sorted(exact, key=lambda k: exact[k] - split[k], reverse=True)[: n - sum(split.values())]:
        split[seat] += 1  # largest remainder, so the parts add up to the week's figure
    del delta["by_key"][key]
    delta["by_court"].pop("bger", None)
    for seat, v in split.items():
        if v:
            delta["by_key"][(f"bger@{seat}", FEDERAL)] = v
            delta["by_court"][f"bger@{seat}"] = v
    delta["federal_split"] = "proportional"


# ── layout ────────────────────────────────────────────────────────────────────

def dot_grid(max_cells: dict[str, int], widths: dict[str, int]) -> tuple[int, int]:
    """(unit, pitch): the largest dots at which the fullest tile still fits, one decision per dot if possible."""
    for unit in UNITS:
        for pitch in PITCHES:
            rows = (CELL_H - LABEL_H) // pitch
            if all(-(-n // unit) <= (widths[k] // pitch) * rows for k, n in max_cells.items()):
                return unit, pitch
    return UNITS[-1], PITCHES[-1]


def fmt(n: int, lang: str) -> str:
    return f"{n:,}".replace(",", "," if lang == "en" else "’")


def fmt_day(d: datetime, lang: str) -> str:
    if lang == "en":
        return f"{d.day} {d.strftime('%b')}"
    return f"{d.day}.{d.month}."


def tile_svg(code: str, label: str, n: int, x: int, y: int, w: int, unit: int, pitch: int,
             lang: str, t: dict) -> str:
    """One canton: a ghost dot lattice, filled bottom-up and left-to-right with this week's decisions."""
    cols, rows = w // pitch, (CELL_H - LABEL_H) // pitch
    r = round(pitch * 0.35, 2)
    x0 = x + (w - cols * pitch) / 2 + pitch / 2
    y_bottom = y + CELL_H - pitch / 2
    dots = -(-n // unit)
    ink = t.get("dot", t["ink"])
    out = [f'<g aria-label="{html.escape(label)}: {n}">']
    for i in range(cols * rows):
        row, col = divmod(i, cols)
        cx, cy = x0 + col * pitch, y_bottom - row * pitch
        fill = ink if i < dots else t["ghost"]
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}"/>')
    weight, colour = ("600", t["ink"]) if n else ("500", t["soft"])
    out.append(f'<text x="{x + 2}" y="{y + 17}" font-size="17" font-weight="{weight}" '
               f'fill="{colour}">{html.escape(label)}</text>')
    if n:
        out.append(f'<text x="{x + w - 2}" y="{y + 17}" font-size="17" font-weight="400" '
                   f'text-anchor="end" fill="{t["ink"]}">{fmt(n, lang)}</text>')
    out.append("</g>")
    return "".join(out)


def build_html(delta: dict, cur: dict, lang: str = "en", theme: str = "red") -> str:
    s, t = STRINGS[lang], THEMES[theme]
    counts = {k: delta["by_canton"].get(k, 0) for k in [*TILES, FEDERAL]}
    widths = {k: CELL_W for k in TILES} | {FEDERAL: 2 * CELL_W + GAP}
    unit, pitch = dot_grid(counts, widths)

    map_w = COLS * CELL_W + (COLS - 1) * GAP
    map_h = ROWS * CELL_H + (ROWS - 1) * GAP
    tiles = [tile_svg(FEDERAL, s["federal"], counts[FEDERAL], 0, 0, widths[FEDERAL], unit, pitch, lang, t)]
    for code, (c, r) in TILES.items():
        tiles.append(tile_svg(code, code, counts[code], c * (CELL_W + GAP), r * (CELL_H + GAP),
                              CELL_W, unit, pitch, lang, t))

    iso = delta["to"].isocalendar()
    week = s["week"].format(w=iso.week, y=iso.year)
    span = f"{fmt_day(delta['from'], lang)}–{fmt_day(delta['to'], lang)}"
    unit_note = s["unit1"] if unit == 1 else s["unitn"].format(u=unit)
    cites = cur.get("corpus", {}).get("citation_edges", 0)
    cites_txt = s["million"].format(v=f"{cites / 1e6:.1f}".replace(".", "." if lang == "en" else ","))
    figures = [
        (fmt(cur["total"], lang), s["f_total"]),
        (fmt(cur.get("court_count", len(cur["by_court"])), lang), s["f_courts"]),
        (cites_txt, s["f_cites"]),
    ]
    figs = "".join(f'<div class="fig"><b>{html.escape(v)}</b><span>{html.escape(l)}</span></div>'
                   for v, l in figures)
    removed = html.escape(s["removed"].format(n=fmt(delta["removed"], lang))) if delta["removed"] else ""

    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px}}
body{{background:{t['bg']};color:{t['ink']};font-family:'IBM Plex Sans','Helvetica Neue',Helvetica,Arial,sans-serif;
  font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased;
  padding:{MARGIN - 12}px {MARGIN}px {MARGIN - 16}px;display:flex;flex-direction:column}}
header{{display:flex;justify-content:space-between;align-items:baseline;font-size:22px;font-weight:500;letter-spacing:.005em}}
header .when{{font-weight:400;color:{t['soft']}}}
.lead{{display:grid;grid-template-columns:auto 1fr;column-gap:36px;align-items:end;margin-top:34px}}
.lead .n{{font-size:212px;font-weight:300;line-height:.8;letter-spacing:-.045em;margin-left:-10px}}
.lead p{{font-size:31px;line-height:1.2;font-weight:400;max-width:17em;padding-bottom:2px;text-wrap:balance}}
.map{{margin-top:40px}}
.map svg{{display:block;font-family:inherit}}
.unit{{margin-top:14px;font-size:19px;color:{t['soft']}}}
.figs{{margin-top:auto;display:grid;grid-template-columns:repeat(3,1fr);column-gap:28px;border-top:1.5px solid {t['rule']};padding-top:20px}}
.fig b{{display:block;font-size:40px;font-weight:400;letter-spacing:-.02em;line-height:1.05}}
.fig span{{display:block;font-size:19px;color:{t['soft']};margin-top:5px}}
footer{{margin-top:22px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;color:{t['soft']}}}
footer b{{color:{t['ink']};font-weight:600;font-size:22px}}
</style></head><body>
<header><span>OpenCaseLaw</span><span class="when">{html.escape(week)} &nbsp; {html.escape(span)}</span></header>
<div class="lead"><div class="n">{fmt(delta['added'], lang)}</div><p>{html.escape(s['headline'])}</p></div>
<div class="map"><svg width="{map_w}" height="{map_h}" viewBox="0 0 {map_w} {map_h}" role="img">{''.join(tiles)}</svg>
<p class="unit">{html.escape(unit_note)} {removed}</p></div>
<div class="figs">{figs}</div>
<footer><b>opencaselaw.ch</b><span>{html.escape(s['free'])}</span></footer>
</body></html>"""


# ── typographic treemap ("type" style) ────────────────────────────────────────

FRENCH = {"GE", "VD", "NE", "JU", "FR", "VS"}
ITALIAN = {"TI"}
RED, BLACK, WHITE = "#da291c", "#000000", "#ffffff"


def squarify(sizes: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap: one (x, y, w, h) per size, areas proportional, sizes sorted descending."""
    scale = w * h / sum(sizes)
    areas = [v * scale for v in sizes]
    rects: list[tuple[float, float, float, float]] = []

    def worst(row: list[float], side: float) -> float:
        total = sum(row)
        return max(max(side * side * a / (total * total), total * total / (side * side * a)) for a in row)

    def place(row: list[float]) -> None:
        nonlocal x, y, w, h
        total = sum(row)
        if w >= h:  # a column on the left
            cw, cy = total / h, y
            for a in row:
                rects.append((x, cy, cw, a / cw))
                cy += a / cw
            x, w = x + cw, w - cw
        else:  # a row on top
            rh, cx = total / w, x
            for a in row:
                rects.append((cx, y, a / rh, rh))
                cx += a / rh
            y, h = y + rh, h - rh

    row: list[float] = []
    for a in areas:
        side = min(w, h)
        if row and worst(row + [a], side) > worst(row, side):
            place(row)
            row = []
        row.append(a)
    if row:
        place(row)
    return rects


def cell_colours(code: str) -> tuple[str | None, str]:
    """(block, letters) by the canton's majority language; the Confederation has no block."""
    if code == FEDERAL:
        return None, WHITE
    if code in FRENCH:
        return BLACK, WHITE
    if code in ITALIAN:
        return WHITE, RED
    return WHITE, BLACK


def build_html_type(delta: dict, cur: dict, lang: str = "en") -> str:
    s = STRINGS[lang]
    edge = 28
    counts = sorted(((k, n) for k, n in delta["by_canton"].items() if n > 0), key=lambda kv: (-kv[1], kv[0]))
    silent = sorted(k for k in TILES if not delta["by_canton"].get(k))
    map_x, map_y, map_w, map_h = edge, 488, W - 2 * edge, 604
    rects = squarify([n for _, n in counts], map_x, map_y, map_w, map_h)

    cells = []
    unlabelled = []  # blocks too small to carry their count; listed under the map instead
    for (code, n), (x, y, w, h) in zip(counts, rects):
        block, ink = cell_colours(code)
        g = 2  # half the gutter between blocks
        x, y, w, h = x + g, y + g, w - 2 * g, h - 2 * g
        if block:
            cells.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{block}"/>')
        pad = min(10.0, 0.08 * min(w, h))
        label = h >= 72 and w >= 56  # room for the count under the letters
        lh = 28 if label else 0
        if not label:
            unlabelled.append(f"{code} {fmt(n, lang)}")
        cells.append(f'<text class="fit" fill="{ink}" data-x="{x + pad:.1f}" data-y="{y + pad:.1f}" '
                     f'data-w="{w - 2 * pad:.1f}" data-h="{h - 2 * pad - lh:.1f}">{code}</text>')
        if label:
            cells.append(f'<text class="n" x="{x + pad:.1f}" y="{y + h - pad:.1f}" fill="{ink}">{fmt(n, lang)}</text>')

    iso = delta["to"].isocalendar()
    week = s["week"].format(w=iso.week, y=iso.year)
    span = f"{fmt_day(delta['from'], lang)}–{fmt_day(delta['to'], lang)}"
    cites = cur.get("corpus", {}).get("citation_edges", 0)
    cites_txt = s["million"].format(v=f"{cites / 1e6:.1f}".replace(".", "." if lang == "en" else ","))
    figures = [
        (fmt(cur["total"], lang), s["f_total"]),
        (fmt(cur.get("court_count", len(cur["by_court"])), lang), s["f_courts"]),
        (cites_txt, s["f_cites"]),
    ]
    figs = "".join(f'<div><b>{html.escape(v)}</b> {html.escape(l)}</div>' for v, l in figures)
    notes = [", ".join(unlabelled) + "."] if unlabelled else []
    notes.append(s["legend"])
    tail = [s["none"].format(l=" ".join(silent))] if silent else []
    if delta["removed"]:
        tail.append(s["removed"].format(n=fmt(delta["removed"], lang)))
    if tail:
        notes.append(" ".join(tail))

    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<script>window.__ready=false</script>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{background:{RED};color:{WHITE};font-family:'IBM Plex Sans','Helvetica Neue',Helvetica,Arial,sans-serif;
  font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased;position:relative}}
svg{{position:absolute;inset:0}}
.fit{{font-family:'Archivo Black','Arial Black','Helvetica Neue',sans-serif;font-size:100px}}
.n{{font-family:'IBM Plex Sans',sans-serif;font-size:21px;font-weight:600}}
.top{{position:absolute;left:{edge}px;right:{edge}px;top:24px;display:flex;justify-content:space-between;font-size:21px;font-weight:600}}
.say{{position:absolute;left:{edge}px;right:{edge}px;top:402px;font-size:24px;line-height:1.25;font-weight:500}}
.say span{{font-weight:400}}
.notes{{position:absolute;left:{edge}px;right:{edge}px;top:1104px;font-size:16.5px;line-height:1.36;font-weight:400}}
.figs{{position:absolute;left:{edge}px;right:{edge}px;top:1226px;display:flex;justify-content:space-between;font-size:20px;
  border-top:3px solid {WHITE};padding-top:14px}}
.figs b{{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;font-size:27px;letter-spacing:-.01em;margin-right:4px}}
.end{{position:absolute;left:{edge}px;right:{edge}px;top:1288px;display:flex;justify-content:space-between;align-items:baseline;font-size:20px}}
.end b{{font-family:'Archivo Black','Arial Black',sans-serif;font-weight:400;font-size:27px;color:{BLACK}}}
</style></head><body>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{fmt(delta['added'], lang)} {html.escape(s['headline'])}">
<text class="fit" fill="{WHITE}" data-x="{edge}" data-y="72" data-w="{W - 2 * edge}" data-h="304">{fmt(delta['added'], lang)}</text>
{''.join(cells)}
</svg>
<div class="top"><span>OpenCaseLaw</span><span>{html.escape(week)} &nbsp; {html.escape(span)}</span></div>
<p class="say">{html.escape(s['headline'])}. <span>{html.escape(s['type_note'])}</span></p>
<p class="notes">{'<br>'.join(html.escape(n) for n in notes)}</p>
<div class="figs">{figs}</div>
<div class="end"><b>opencaselaw.ch</b><span>{html.escape(s['free'])}</span></div>
<script>
// Stretch each .fit text so its ink exactly fills its box (ink bounds, not the em box).
(async () => {{
  await document.fonts.load('100px "Archivo Black"');
  await document.fonts.ready;
  const ctx = document.createElement('canvas').getContext('2d');
  for (const el of document.querySelectorAll('.fit')) {{
    ctx.font = '100px "Archivo Black", "Arial Black", sans-serif';  // same as .fit
    const m = ctx.measureText(el.textContent), d = el.dataset;
    const iw = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
    const ih = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const sx = d.w / iw, sy = d.h / ih;
    el.setAttribute('transform',
      `translate(${{+d.x + m.actualBoundingBoxLeft * sx}} ${{+d.y + m.actualBoundingBoxAscent * sy}}) scale(${{sx}} ${{sy}})`);
  }}
  window.__ready = true;
}})();
</script>
</body></html>"""


def build_caption(delta: dict, cur: dict, lang: str) -> str:
    s = STRINGS[lang]
    top = sorted(delta["by_canton"].items(), key=lambda kv: -kv[1])[:3]
    names = [s["federal"] if k == FEDERAL else k for k, _ in top]
    joiner = {"en": " and ", "de": " und ", "fr": " et "}[lang]
    top_txt = ", ".join(names[:-1]) + joiner + names[-1] if len(names) > 1 else "".join(names)
    removed = s["cap_removed"].format(n=fmt(delta["removed"], lang)) if delta["removed"] else ""
    return s["caption"].format(
        w=delta["to"].isocalendar().week, n=fmt(delta["added"], lang),
        a=fmt_day(delta["from"], lang), b=fmt_day(delta["to"], lang), top=top_txt,
        total=fmt(cur["total"], lang), courts=cur.get("court_count", len(cur["by_court"])),
        removed=removed,
    )


def render_png(page_html: str, out: Path, scale: int = 2, require_fonts: bool = False) -> None:
    """Screenshot the poster. With `require_fonts`, fail rather than publish a poster set in fallback type."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:  # bundled Chromium missing or out of step with the package
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=scale)
        page.set_content(page_html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_function("window.__ready !== false")
        loaded = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').length")
        if require_fonts and not loaded:
            browser.close()
            raise SystemExit("web fonts did not load; not rendering a poster in fallback type")
        page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
        browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lang", choices=sorted(STRINGS), default="en")
    ap.add_argument("--style", choices=("type", "dots"), default="type",
                    help="type: canton letters sized by count; dots: tile map, one dot per decision")
    ap.add_argument("--theme", choices=sorted(THEMES), default="red", help="dots style only")
    ap.add_argument("--end", type=date.fromisoformat, help="last day of the window (default: newest snapshot)")
    ap.add_argument("--ref", default="origin/main", help="git ref carrying the nightly stats.json commits")
    ap.add_argument("--out-dir", type=Path, default=REPO / "output" / "posters")
    ap.add_argument("--html-only", action="store_true", help="write the HTML, skip the Chromium render")
    args = ap.parse_args(argv)

    delta, cur = load_week(args.ref, args.end)

    print(f"window {delta['from']:%Y-%m-%d %H:%M} -> {delta['to']:%Y-%m-%d %H:%M} UTC")
    print(f"added {delta['added']}  removed {delta['removed']}")
    for court, n in sorted(delta["by_court"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {court}")

    iso = delta["to"].isocalendar()
    variant = "type" if args.style == "type" else f"dots-{args.theme}"
    stem = f"opencaselaw-{iso.year}-w{iso.week:02d}-{args.lang}-{variant}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    page_html = (build_html_type(delta, cur, args.lang) if args.style == "type"
                 else build_html(delta, cur, args.lang, args.theme))
    (args.out_dir / f"{stem}.html").write_text(page_html, encoding="utf-8")
    (args.out_dir / f"{stem}.txt").write_text(build_caption(delta, cur, args.lang) + "\n", encoding="utf-8")
    if not args.html_only:
        render_png(page_html, args.out_dir / f"{stem}.png")
    print(f"wrote {args.out_dir / stem}.{{html,txt{'' if args.html_only else ',png'}}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
