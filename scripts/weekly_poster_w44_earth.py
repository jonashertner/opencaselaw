"""Week 44 (23-29 October 2026), English: the week the whole earth arrived.

After the cover of the Whole Earth Catalog (1968): a black sheet, the planet alone in the middle, the
title in white. The week's own figure stays Swiss, 1,139 new decisions; the planet is the
international law that entered the corpus the same week, drawn with one dot per decision and none
to spare (8,714 in all: UN treaty bodies and Working Group on Arbitrary Detention, Court of Justice
of the EU, International Court of Justice). The 291 UN decisions on Switzerland are gathered into
the red dot, whose area equals 291 white ones, on Switzerland.

The view is NASA's Blue Marble Next Generation (July 2004, public domain, NASA Earth Observatory)
in orthographic projection; the dots are placed by weighted stippling, so they follow its light.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

import weekly_poster as wp
from weekly_poster_designs import esc, page

ASSET = Path(__file__).with_name("weekly_poster_w44_assets") / "bluemarble_grey.jpg"
LAT0, LON0 = 24.0, 10.0           # the centre of the view
SWISS = (46.8, 8.2)
CX, CY, RADIUS = 540, 640, 412
DOT = 1.55
MONTH_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
            "November", "December"]
RED = "#e8331f"


def project(lat: float, lon: float) -> tuple[float, float]:
    """A point on the globe as x, y on the disc of radius 1."""
    p, l, p0, l0 = map(math.radians, (lat, lon, LAT0, LON0))
    x = math.cos(p) * math.sin(l - l0)
    y = math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0)
    return x, y


def density(size: int) -> np.ndarray:
    """How strongly each pixel of the disc should attract dots."""
    art = np.asarray(Image.open(ASSET).convert("L"), dtype=np.float32) / 255
    h, w = art.shape
    v, u = np.mgrid[1:-1:size * 1j, -1:1:size * 1j]
    r2 = u ** 2 + v ** 2
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    p0, l0 = math.radians(LAT0), math.radians(LON0)
    lat = np.arcsin(np.clip(z * math.sin(p0) + v * math.cos(p0), -1, 1))
    lon = l0 + np.arctan2(u, z * math.cos(p0) - v * math.sin(p0))
    py = np.clip(((math.pi / 2 - lat) / math.pi * h).astype(int), 0, h - 1)
    px = ((lon + math.pi) / (2 * math.pi) * w).astype(int) % w
    light = np.clip(art[py, px] * 1.5, 0, 1) ** .75
    limb = np.clip(r2, 0, 1) ** 9 * .55
    d = (.012 + light * (z ** .5) + limb)
    d[r2 >= 1] = 0
    return d


def nearest(pts: np.ndarray, size: int) -> np.ndarray:
    """For every pixel, the index of the nearest point: jump flooding, so it needs numpy alone."""
    v, u = np.mgrid[0:size, 0:size].astype(np.float32)
    grid = np.full((size, size), -1, dtype=np.int32)
    ij = np.clip(pts.astype(int), 0, size - 1)
    grid[ij[:, 1], ij[:, 0]] = np.arange(len(pts))
    best = np.where(grid >= 0, 0.0, np.inf).astype(np.float32)
    step = size // 2
    while step >= 1:
        for dy, dx in ((-step, 0), (step, 0), (0, -step), (0, step), (-step, -step), (-step, step), (step, -step), (step, step)):
            cand = np.roll(grid, (dy, dx), axis=(0, 1))
            seen = cand >= 0
            dist = np.full_like(best, np.inf)
            dist[seen] = (u[seen] - pts[cand[seen], 0]) ** 2 + (v[seen] - pts[cand[seen], 1]) ** 2
            closer = dist < best
            grid[closer], best[closer] = cand[closer], dist[closer]
        step //= 2
    return grid


def stipple(n: int, size: int = 800, rounds: int = 16) -> np.ndarray:
    """n points on the unit disc, spread by weighted Lloyd relaxation (Secord's stippling)."""
    d = density(size)
    rng = np.random.default_rng(44)
    ys, xs = np.nonzero(d)
    weight = d[ys, xs]
    pts = np.stack([xs, ys], axis=1).astype(np.float32)[rng.choice(len(xs), n, replace=False, p=weight / weight.sum())]
    for _ in range(rounds):
        owner = nearest(pts, size)[ys, xs]
        mass = np.bincount(owner, weight, n)
        sx = np.bincount(owner, weight * xs, n)
        sy = np.bincount(owner, weight * ys, n)
        ok = mass > 0
        pts[ok, 0], pts[ok, 1] = sx[ok] / mass[ok], sy[ok] / mass[ok]
    return pts / (size - 1) * 2 - 1


def design(week: dict, lang: str = "en") -> str:
    a, b = week["from"], week["to"]
    intl = week["international"]
    on_ch = intl["on_switzerland"]
    pts = stipple(intl["total"] - on_ch)
    circles = "".join(f'<circle cx="{CX + x * RADIUS:.1f}" cy="{CY + y * RADIUS:.1f}" r="{DOT}"/>' for x, y in pts)
    sx, sy = project(*SWISS)
    # area of the red dot = 291 white dots
    red = f'<circle cx="{CX + sx * RADIUS:.1f}" cy="{CY - sy * RADIUS:.1f}" r="{DOT * math.sqrt(on_ch):.1f}" fill="{RED}"/>'

    body = f"""
<div class="m">WHOLE EARTH CASE LAW</div>
<div class="a">access to law</div>
<svg width="1080" height="1350" viewBox="0 0 1080 1350"><g fill="#f3efe6">{circles}</g>{red}</svg>
<div class="n">{esc(wp.fmt(week["added"], "en"))}</div>
<div class="t">new Swiss decisions<br>Week {week["week_no"]}, {a.day}–{b.day} {MONTH_EN[b.month - 1]} {b.year}</div>
<div class="r"><b>+ {esc(wp.fmt(intl["total"], "en"))}</b> international decisions, one dot each:<br>UN treaty bodies,
EU Court of Justice,<br>International Court of Justice.<br><span class="red">●</span> {on_ch} of them concern Switzerland.</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:#050505;color:#f3efe6;font-family:'Libre Franklin',sans-serif}}
.m{{position:absolute;left:0;right:0;top:36px;text-align:center;font:900 78px/1 'Libre Franklin',sans-serif;letter-spacing:-.022em}}
.a{{position:absolute;left:0;right:0;top:120px;text-align:center;font:italic 400 28px/1 'Libre Franklin',sans-serif}}
.n{{position:absolute;left:36px;top:1094px;font:800 168px/1 'Libre Franklin',sans-serif;letter-spacing:-.045em}}
.t{{position:absolute;left:44px;top:1284px;font:500 21px/1.2 'Libre Franklin',sans-serif}}
.r{{position:absolute;right:44px;top:1146px;text-align:right;font:400 21px/1.3 'Libre Franklin',sans-serif}}
.r b{{font-weight:800}}
.red{{color:{RED}}}
.u{{position:absolute;right:44px;top:1302px;font:600 21px/1 'Libre Franklin',sans-serif}}
"""
    return page("family=Libre+Franklin:ital,wght@0,400;0,500;0,600;0,800;0,900;1,400", css, body, lang)
