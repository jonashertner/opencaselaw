"""Week 45 (30 October - 5 November 2026), English: German and French neck and neck.

This week 434 of the new decisions are in German and 422 in French, with 49 in Italian. The map
splits Switzerland along its language border, the Röstigraben, and fills each language's area
with exactly as many dots as there are decisions in that language, evenly spaced. A decision is
placed by its language, not its court, so the federal courts' rulings fall on their language's
side too. The French side is smaller, so its dots sit closer together. The French side is drawn a
little apart from the rest, so the Röstigraben shows as what its name says, a ditch.

Language areas: cantons by official language, with the bilingual cantons split by district
(swissBOUNDARIES3D districts, scripts/weekly_poster_w45_assets/language_districts.json): Sense and
See to German in Fribourg, the Bernese Jura to French, Upper Valais (Goms, Brig, Visp, Raron,
Leuk) to German, Moesa and Bernina to Italian in Graubünden. Romansh areas count with German
here: no decision of the week is in Romansh.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import weekly_poster as wp
from weekly_poster_designs import esc, page

HERE = Path(__file__).resolve().parent
CANTON_LANG = {"FR": "fr", "VD": "fr", "NE": "fr", "GE": "fr", "JU": "fr", "VS": "fr", "TI": "it"}  # else de
SCALE = 2.66                 # px per km
ORIGIN = (470.0, 298.0)      # km at the map's top left
GRABEN = (-15.0, 5.0)        # px: how far the French side stands apart
UPPER_VALAIS = 700.0         # km: no part of Valais lies east of this; Upper Valais comes from its districts
TOP = 430.0                  # px
PAPER, INK = "#f2f0eb", "#151515"
COLOUR = {"de": "#151515", "fr": "#d6281e", "it": "#6b7f8e"}
TINT = {"de": "#e2ded5", "fr": "#f1d6cf", "it": "#dce2e6"}
NAME = {"de": "German", "fr": "French", "it": "Italian"}


def px(km: np.ndarray) -> np.ndarray:
    return np.stack([(km[:, 0] - ORIGIN[0]) * SCALE, TOP + (ORIGIN[1] - km[:, 1]) * SCALE], axis=1)


def inside(pts: np.ndarray, rings: list[np.ndarray]) -> np.ndarray:
    """Even-odd test against a set of rings (outer rings and holes alike)."""
    x, y = pts[:, 0], pts[:, 1]
    odd = np.zeros(len(pts), bool)
    for ring in rings:
        xa, ya = ring[:, 0], ring[:, 1]
        xb, yb = np.roll(xa, -1), np.roll(ya, -1)
        for x0, y0, x1, y1 in zip(xa, ya, xb, yb):
            if y0 == y1:
                continue
            c = (y0 > y) != (y1 > y)
            odd ^= c & (x < (x1 - x0) * (y - y0) / (y1 - y0) + x0)
    return odd


def regions() -> tuple[dict, dict, list]:
    data = json.loads((HERE / "weekly_poster_cantons.json").read_text(encoding="utf-8"))
    over = json.loads((HERE / "weekly_poster_w45_assets" / "language_districts.json").read_text(encoding="utf-8"))
    cantons = {k: [[np.array(r) for r in poly] for poly in v] for k, v in data["cantons"].items()}
    overlays = {lg: [[np.array(r) for r in poly] for d in ds for poly in d["polys"]] for lg, ds in over["overlays"].items()}
    overlays["leuk"] = [[np.array(r) for r in poly] for poly in over["valais_german_west"]["polys"]]
    lakes = [np.array(r) for lk in data["lakes"] for r in lk["rings"]]
    return cantons, overlays, lakes


def language_of(pts: np.ndarray, cantons: dict, overlays: dict, lakes: list) -> np.ndarray:
    lang = np.full(len(pts), "", dtype=object)
    for k, polys in cantons.items():
        hit = inside(pts, [r for p in polys for r in p])
        lang[hit] = CANTON_LANG.get(k, "de")
    for lg, polys in overlays.items():
        if lg == "leuk":
            continue
        for p in polys:
            hit = inside(pts, p) & (lang != "")
            lang[hit] = lg
    # Valais: German in the five districts of Upper Valais
    vs = inside(pts, [r for p in cantons["VS"] for r in p])
    leuk = np.zeros(len(pts), bool)
    for p in overlays["leuk"]:
        leuk |= inside(pts, p)
    lang[vs] = np.where(leuk[vs] | (pts[vs, 0] > UPPER_VALAIS), "de", "fr")
    lang[inside(pts, lakes)] = ""
    return lang


def lattice(lang_of, region: str, n: int, box: tuple) -> np.ndarray:
    """A square lattice with exactly n points in the language area: pitch by bisection, then slide."""
    x0, y0, x1, y1 = box
    cache = {}

    def pts(p, ox, oy):
        gx, gy = np.meshgrid(np.arange(x0 + ox, x1, p), np.arange(y0 + oy, y1, p))
        P = np.stack([gx.ravel(), gy.ravel()], axis=1)
        key = (round(p, 4), round(ox, 3), round(oy, 3))
        if key not in cache:
            cache[key] = P[lang_of(P) == region]
        return cache[key]
    lo, hi = 0.5, 30.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if len(pts(mid, 0, 0)) > n:
            lo = mid
        else:
            hi = mid
    for p in np.linspace(lo * .985, lo * 1.015, 13):
        for ox in np.linspace(0, p, 7, endpoint=False):
            for oy in np.linspace(0, p, 7, endpoint=False):
                q = pts(p, ox, oy)
                if len(q) == n:
                    return q
    raise SystemExit(f"no lattice with exactly {n} points for {region}")


def path(rings) -> str:
    return "".join("M" + " ".join(f"{a:.1f},{b:.1f}" for a, b in px(r)) + "Z" for r in rings)


def design(week: dict, lang: str = "en") -> str:
    a, b = week["from"], week["to"]
    cantons, overlays, lakes = regions()
    lang_of = lambda P: language_of(P, cantons, overlays, lakes)  # noqa: E731
    box = (485.0, 74.0, 834.0, 296.0)
    counts = week["languages"]
    assert set(counts) <= set(COLOUR)

    # the language areas, tinted: a fine raster classified once, drawn as small squares
    step = 0.8
    gx, gy = np.meshgrid(np.arange(box[0], box[2], step), np.arange(box[1], box[3], step))
    G = np.stack([gx.ravel(), gy.ravel()], axis=1)
    L = lang_of(G)
    tint = []
    for lg in ("de", "fr", "it"):
        sel = px(G[L == lg]) + (GRABEN if lg == "fr" else (0, 0))
        s = step * SCALE + .35
        tint.append(f'<g fill="{TINT[lg]}">' + "".join(f'<rect x="{x - s / 2:.1f}" y="{y - s / 2:.1f}" width="{s:.2f}" height="{s:.2f}"/>' for x, y in sel) + "</g>")

    dots = []
    for lg, n in counts.items():
        P = px(lattice(lang_of, lg, n, box)) + (GRABEN if lg == "fr" else (0, 0))
        dots.append(f'<g fill="{COLOUR[lg]}">' + "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6"/>' for x, y in P) + "</g>")

    month = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
  {''.join(tint)}
  {''.join(dots)}
</svg>
<div class="n">{esc(wp.fmt(week["added"], "en"))}</div>
<div class="t">new decisions in week {week["week_no"]},<br>{a.day} {month[a.month - 1]} – {b.day} {month[b.month - 1]} {b.year}</div>
<div class="h">Neck and neck across the Röstigraben</div>
<div class="v fr"><b>{esc(wp.fmt(counts.get("fr", 0), "en"))}</b>in French</div>
<div class="v de"><b>{esc(wp.fmt(counts.get("de", 0), "en"))}</b>in German</div>
<div class="v it"><b>{esc(wp.fmt(counts.get("it", 0), "en"))}</b>in Italian</div>
<div class="k">One dot per decision, in the area of its language.<br>Federal rulings count with their language.</div>
<div class="u">opencaselaw.ch</div>
<div class="c">Borders: © swisstopo</div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Archivo',sans-serif}}
svg{{position:absolute;left:0;top:0}}
.n{{position:absolute;left:58px;top:52px;font:800 188px/1 'Archivo',sans-serif;letter-spacing:-.04em}}
.t{{position:absolute;left:468px;top:84px;font:600 31px/1.22 'Archivo',sans-serif}}
.h{{position:absolute;left:62px;top:270px;font:700 50px/1.05 'Archivo',sans-serif;letter-spacing:-.015em}}
.v{{position:absolute;font:600 22px/1.1 'Archivo',sans-serif}}
.v b{{display:block;font:800 76px/1 'Archivo',sans-serif;letter-spacing:-.03em}}
.v.fr{{left:62px;top:1060px;color:{COLOUR["fr"]}}}
.v.de{{left:430px;top:1060px;color:{COLOUR["de"]}}}
.v.it{{left:790px;top:1060px;color:{COLOUR["it"]}}}
.k{{position:absolute;left:62px;top:1222px;font:400 21px/1.35 'Archivo',sans-serif;color:#444}}
.u{{position:absolute;right:58px;top:1222px;font:700 22px/1.35 'Archivo',sans-serif}}
.c{{position:absolute;right:58px;top:1252px;font:400 15px/1.35 'Archivo',sans-serif;color:#777}}
"""
    return page("family=Archivo:wght@400;600;700;800", css, body, lang)
