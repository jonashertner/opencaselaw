"""Week 44 (23-29 October 2026), Rumantsch: the week's weight bends the country.

A sheet of graph paper with Switzerland on it, and on the country exactly as many dots as there
are new decisions. Every court then pulls on the sheet where it sits, with a force that grows with
its decisions of the week: the canton courts at the cantonal capital, the Federal Supreme Court at
Lausanne and Luzern, the Federal Administrative Court at St. Gallen, the Federal Criminal Court at
Bellinzona. Sheet, border and dots all move with the same field, so the outline shows where the
week was heavy. After a warped grid in Jonas's bookmarks. Almost no words, as for every Rumantsch
edition.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import weekly_poster as wp
from weekly_poster_designs import esc, page

GEO = Path(__file__).with_name("weekly_poster_geo.json")
MONTH_RM = ["schaner", "favrer", "mars", "avrigl", "matg", "zercladur", "fanadur", "avust", "settember",
            "october", "november", "december"]
# seats in the km grid of weekly_poster_geo.json (LV95 east - 2000, north - 1000)
SEAT = {"ZH": (683, 247), "BE": (600, 200), "LU": (666, 211), "UR": (691, 193), "SZ": (692, 208), "OW": (661, 194),
        "NW": (670, 201), "GL": (724, 212), "ZG": (682, 225), "FR": (578, 184), "SO": (607, 228), "BS": (611, 267),
        "BL": (622, 259), "SH": (689, 283), "AR": (748, 247), "AI": (749, 244), "SG": (746, 254), "GR": (759, 191),
        "AG": (646, 249), "TG": (710, 268), "TI": (722, 117), "VD": (538, 152), "VS": (594, 120), "NE": (561, 205),
        "GE": (500, 118), "JU": (593, 245)}
LAUSANNE, LUZERN, STGALLEN, BELLINZONA, BERN = SEAT["VD"], SEAT["LU"], SEAT["SG"], SEAT["TI"], SEAT["BE"]
SCALE = 2.78         # poster px per km
ORIGIN = (472, 186)  # the km point at the poster's left edge, on the height of CENTRE
CENTRE = 760         # px
PULL = 27.0          # km of pull one sigma from a seat that holds every decision
SOFT = 33.0          # km: how far a seat's pull reaches
INK, RED, PAPER = "#101010", "#e0301e", "#f1ede3"


def masses(week: dict) -> list[tuple[float, float, float]]:
    bc, court = week["by_canton"], week["by_court"]
    m = [(*SEAT[k], float(n)) for k, n in bc.items() if k != "CH" and n]
    m += [(*LAUSANNE, court.get("bger@VD", 0) + court.get("bge", 0)), (*LUZERN, court.get("bger@LU", 0)),
          (*STGALLEN, court.get("bvger", 0)), (*BELLINZONA, court.get("bstger", 0))]
    rest = bc["CH"] - sum(court.get(k, 0) for k in ("bger@VD", "bge", "bger@LU", "bvger", "bstger"))
    m.append((*BERN, float(rest)))
    return m


def warp(xy: np.ndarray, mass: list) -> np.ndarray:
    """Pull points (km, as an array of shape (n, 2)) towards the seats; a gentle, bounded lens."""
    out = xy.astype(np.float64).copy()
    total = sum(w for _, _, w in mass)
    for sx, sy, w in mass:
        d = np.stack([sx - xy[:, 0], sy - xy[:, 1]], axis=1)
        r2 = (d ** 2).sum(1) + 1e-6
        f = (w / total) ** .5 * PULL * np.exp(-r2 / (2 * SOFT ** 2)) * (np.sqrt(r2) / SOFT)
        out += d / np.sqrt(r2)[:, None] * f[:, None]
    return out


def to_px(km: np.ndarray) -> np.ndarray:
    return np.stack([(km[:, 0] - ORIGIN[0]) * SCALE, CENTRE - (km[:, 1] - ORIGIN[1]) * SCALE], axis=1)


def inside(pts: np.ndarray, ring: np.ndarray) -> np.ndarray:
    x, y = pts[:, 0], pts[:, 1]
    xa, ya, xb, yb = ring[:, 0], ring[:, 1], np.roll(ring[:, 0], -1), np.roll(ring[:, 1], -1)
    odd = np.zeros(len(pts), bool)
    for x0, y0, x1, y1 in zip(xa, ya, xb, yb):
        if y0 == y1:
            continue
        c = (y0 > y) != (y1 > y)
        odd ^= c & (x < (x1 - x0) * (y - y0) / (y1 - y0) + x0)
    return odd


def lattice(ring: np.ndarray, n: int) -> np.ndarray:
    """A square lattice with exactly n points inside the ring: find the pitch by bisection, then
    slide the lattice a little until the count is exact."""
    x0, y0, x1, y1 = ring[:, 0].min(), ring[:, 1].min(), ring[:, 0].max(), ring[:, 1].max()
    area = 0.5 * abs(np.dot(ring[:, 0], np.roll(ring[:, 1], -1)) - np.dot(ring[:, 1], np.roll(ring[:, 0], -1)))
    base = (area / n) ** .5
    for p in np.linspace(base * .97, base * 1.03, 49):
        for ox in np.linspace(0, p, 9, endpoint=False):
            for oy in np.linspace(0, p, 9, endpoint=False):
                gx, gy = np.meshgrid(np.arange(x0 + ox, x1, p), np.arange(y0 + oy, y1, p))
                pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
                keep = pts[inside(pts, ring)]
                if len(keep) == n:
                    return keep
    raise SystemExit("no lattice with the exact count")


def design(week: dict, lang: str = "rm") -> str:
    a, b = week["from"], week["to"]
    border = np.array(json.loads(GEO.read_text(encoding="utf-8"))["border"], dtype=np.float64)
    mass = masses(week)
    pts = lattice(border, week["added"])

    # the sheet: lines of the graph paper, each sampled finely so that they bend
    pitch = 9.0
    lines = []
    xs = np.arange(ORIGIN[0] - 36, ORIGIN[0] + 430, pitch)
    ys = np.arange(ORIGIN[1] - 240, ORIGIN[1] + 300, pitch)
    for x in xs:
        km = np.stack([np.full(320, x), np.linspace(ys[0], ys[-1], 320)], axis=1)
        lines.append(to_px(warp(km, mass)))
    for y in ys:
        km = np.stack([np.linspace(xs[0], xs[-1], 320), np.full(320, y)], axis=1)
        lines.append(to_px(warp(km, mass)))
    sheet = "".join('<polyline points="' + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in ln) + '"/>' for ln in lines)

    edge = to_px(warp(border, mass))
    outline = "M" + " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in edge) + "Z"
    dots = to_px(warp(pts, mass))
    marks = "".join(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="4.1"/>' for p in dots)

    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
  <g fill="none" stroke="{INK}" stroke-width=".55" opacity=".5">{sheet}</g>
  <path d="{outline}" fill="{PAPER}" fill-opacity=".72" stroke="{RED}" stroke-width="2.2" stroke-linejoin="round"/>
  <g fill="{RED}">{marks}</g>
</svg>
<div class="n">{esc(wp.fmt(week["added"], "rm"))}</div>
<div class="t">novas decisiuns</div>
<div class="d">Emna {week["week_no"]}<br>{a.day}–{b.day} {MONTH_RM[b.month - 1]} {b.year}</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Space Grotesk',sans-serif}}
.n{{position:absolute;left:38px;top:40px;font:700 196px/1 'Space Grotesk',sans-serif;letter-spacing:-.04em}}
.t{{position:absolute;left:46px;top:250px;font:500 36px/1 'Space Grotesk',sans-serif}}
.d{{position:absolute;right:46px;top:1236px;text-align:right;font:500 24px/1.25 'Space Grotesk',sans-serif}}
.k{{position:absolute;left:46px;top:1236px;font:500 24px/1.25 'Space Grotesk',sans-serif}}
.u{{position:absolute;left:46px;top:1270px;font:500 24px/1.25 'Space Grotesk',sans-serif}}
"""
    return page("family=Space+Grotesk:wght@500;700", css, body, lang)
