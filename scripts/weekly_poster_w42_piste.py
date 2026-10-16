"""Week 42 (9-15 October 2026), English: a piste map.

The week painted as a Pistenplan. Every court is a run: it ends in the valley and its
length is the court's new decisions this week (one scale for every run, arc length solved
numerically, so a curved run is still exactly its count). The massif is drawn to the runs:
the skyline passes over their tops, two summits (Geneva, Zurich) joined by a crest. Run colour
is the week against a usual week (median of the previous eight weeks): blue below it, red about
it, black well above it. Ticino (55 against a usual 12) is the one black run. Names only in the
key, except a white tag beside the three longest runs.

The terrain is painted to the runs (see terrain()): light from the upper left, a lit west face and
shadow faces east of each ridgeline, rock faces as rib clusters hanging from the ridges, three far
ranges in aerial perspective, a tree line that dissolves into single conifers. Seeded, so the render
is reproducible.

Thresholds (difficulty): blue if n < usual - max(5, usual/4); black if n > usual +
max(5, usual/2); red otherwise. The federal runs are coloured by the federal total, the
only usual figure the harness carries for them.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import math
import random

import weekly_poster as wp
from weekly_poster_designs import AREAS_EN, esc, page

NAME = dict(AREAS_EN)
W, H = wp.W, wp.H

BASE_Y = 900          # the valley floor: every run ends here
K = 3.5               # run length in px per decision
ANGLE = 67            # mean slope of a run, degrees from horizontal
PITCH = 32            # horizontal distance between neighbouring runs on one face
TINY = 40             # between the short runs standing in the valleys
LIFT = 34             # the skyline passes this far above a run's number plate
COS = math.cos(math.radians(ANGLE)) / math.sin(math.radians(ANGLE))  # horizontal drift per vertical px
PANEL_Y = 940         # the printed margin with the key begins here

COLOUR = {"blue": "#1a5fc7", "red": "#d2232a", "black": "#15181c"}
LEGEND = [("blue", "quieter than usual"), ("red", "about a usual week"), ("black", "far busier than usual")]


def difficulty(n: float, usual: float) -> str:
    if n < usual - max(5, usual / 4):
        return "blue"
    if n > usual + max(5, usual / 2):
        return "black"
    return "red"


def _runs(week: dict) -> list[dict]:
    """The runs: federal courts split by court, cantons with new decisions, each with its colour."""
    bc, bk, uc = week["by_canton"], week["by_court"], week["usual_canton"]
    fed = bc.get(wp.FEDERAL, 0)
    vd, lu, bvger, bstger = (bk.get(k, 0) for k in ("bger@VD", "bger@LU", "bvger", "bstger"))
    other = fed - vd - lu - bvger - bstger
    assert other >= 0
    fed_col = difficulty(fed, uc.get(wp.FEDERAL, 0))
    runs = [
        {"code": "bger@VD", "name": "Federal Supreme Court, Lausanne", "n": vd, "col": fed_col},
        {"code": "bger@LU", "name": "Federal Supreme Court, Lucerne", "n": lu, "col": fed_col},
        {"code": "bvger", "name": "Federal Administrative Court", "n": bvger, "col": fed_col},
        {"code": "bstger", "name": "Federal Criminal Court", "n": bstger, "col": fed_col},
        {"code": "fed", "name": "Other federal bodies", "n": other, "col": fed_col},
    ]
    for c, n in bc.items():
        if c != wp.FEDERAL and n > 0:
            runs.append({"code": c, "name": NAME[c], "n": n, "col": difficulty(n, uc.get(c, 0))})
    runs = [r for r in runs if r["n"] > 0]
    assert sum(r["n"] for r in runs) == week["added"]
    for i, r in enumerate(sorted(runs, key=lambda r: (-r["n"], r["name"])), 1):
        r["no"] = i
    return runs


# ── terrain-following runs ────────────────────────────────────────────────────
# Every run follows the same lateral swing as a function of height, so runs that are PITCH apart
# at the valley stay PITCH apart all the way up: parallel curves never cross.

def swing(y: float) -> float:
    return 13 * math.sin((y - 120) / 41) + 6 * math.sin((y + 30) / 17.5)


def run_x(r: dict, y: float) -> float:
    return r["base"] - r["face"] * (BASE_Y - y) * COS + (swing(y) - swing(BASE_Y)) * r.get("sw", 1.0)


def _height_for(length: float, face: int, sw: float = 1.0) -> float:
    """The vertical drop whose terrain-following path has exactly this arc length (bisection)."""
    probe = {"base": 0.0, "face": face, "sw": sw}

    def arc(h: float) -> float:
        n = max(8, int(h / 2))
        pts = [(run_x(probe, BASE_Y - h * i / n), BASE_Y - h * i / n) for i in range(n + 1)]
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(n))

    lo, hi = 0.0, length
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if arc(mid) < length else (lo, mid)
    return (lo + hi) / 2


# The panorama, left to right. One massif, two summits. The west face carries Romandie and the
# federal courts, the east face the German-speaking cantons; Ticino's black run comes off the
# central bowl below the crest. Runs on one face are parallel, PITCH apart; the short runs
# (fewer than ten decisions) stand in the valleys, TINY apart. ("fed" = other federal bodies.)
WEST_VALLEY = ["JU", "NE"]
WEST_FACE = ["GE", "bvger", "bger@VD", "VD", "FR"]      # summit first, leaning down-left
WEST_RIGHT = ["TI", "bger@LU", "fed", "bstger"]         # below the crest, leaning down-right
MID_VALLEY = ["SO", "BL"]
EAST_NEST = ["AG", "BE", "BS"]                          # leaning down-left, under the east summit
EAST_SUMMIT = "ZH"                                      # leaning down-right
EAST_RIGHT = ["SG", "ZG", "SZ", "GR", "UR"]
QUIET = ["VS", "LU", "OW", "NW", "SH", "TG", "AR", "AI", "GL"]  # usually silent; drawn in the east valley if present
SWING = {"west": 1.0, "crest": 0.9, "east": 1.1}


def _layout(runs: list[dict]) -> list[dict]:
    by = {r["code"]: r for r in runs}
    for c in WEST_VALLEY + WEST_FACE:
        if c in by:
            by[c].update(face=-1, sw=SWING["west"])
    for c in WEST_RIGHT + MID_VALLEY:
        if c in by:
            by[c].update(face=1 if c in WEST_RIGHT else -1, sw=SWING["crest"])
    for c in EAST_NEST + [EAST_SUMMIT] + EAST_RIGHT + QUIET:
        if c in by:
            by[c].update(face=-1 if c in EAST_NEST else 1, sw=SWING["east"])
    for r in runs:
        r["h"] = _height_for(r["n"] * K, r["face"], r["sw"])
    h = lambda c: by[c]["h"]  # noqa: E731

    x = 0
    for c in [c for c in WEST_VALLEY if c in by]:
        by[c]["base"] = x
        x += TINY
    face = [c for c in WEST_FACE if c in by]
    for i, c in enumerate(reversed(face)):             # FR ... GE, left to right
        by[c]["base"] = x + PITCH * i
    x = by[face[0]]["base"]
    right = [c for c in WEST_RIGHT if c in by]
    x += 2 * h(right[0]) * COS + PITCH                 # the first down-right run clears the summit run's line
    for i, c in enumerate(right):
        by[c]["base"] = x + PITCH * i
    x = by[right[-1]]["base"] + TINY
    for c in [c for c in MID_VALLEY if c in by]:
        by[c]["base"] = x
        x += TINY
    nest = [c for c in EAST_NEST if c in by]
    for i, c in enumerate(reversed(nest)):
        by[c]["base"] = x + PITCH * i
    tall = nest[0]
    xs = by[tall]["base"] - (h(EAST_SUMMIT) - 2 * h(tall)) * COS + PITCH   # the summit's start x, so AG clears its line
    by[EAST_SUMMIT]["base"] = xs + h(EAST_SUMMIT) * COS
    x = by[EAST_SUMMIT]["base"] + PITCH
    last = x
    for i, c in enumerate([c for c in EAST_RIGHT if c in by]):
        by[c]["base"] = last = x + PITCH * i
    x = last + TINY
    for c in [c for c in QUIET if c in by]:
        by[c]["base"] = x
        x += TINY

    left = min(min(run_x(r, BASE_Y - r["h"] * t / 10) for t in range(11)) for r in runs)
    right_ = max(max(run_x(r, BASE_Y - r["h"] * t / 10) for t in range(11)) for r in runs)
    shift = (W - (right_ - left)) / 2 - left          # centre the range
    for r in runs:
        r["base"] += shift
        r["top"] = (run_x(r, BASE_Y - r["h"]), BASE_Y - r["h"])
    return runs


def _run_shape(r: dict) -> tuple[str, str]:
    """(casing, run) polygons: the run tapers from 5.5 px at the top to 9 px in the valley."""
    n = max(10, int(r["h"] / 4))
    pts = [(run_x(r, BASE_Y - r["h"] * (1 - i / n)), BASE_Y - r["h"] * (1 - i / n)) for i in range(n + 1)]

    def band(extra: float) -> str:
        left, right = [], []
        for i, (x, y) in enumerate(pts):
            (x0, y0), (x1, y1) = pts[max(i - 1, 0)], pts[min(i + 1, n)]
            dx, dy = x1 - x0, y1 - y0
            d = math.hypot(dx, dy) or 1
            nx, ny = -dy / d, dx / d
            w = (5.5 + 3.5 * i / n) / 2 + extra
            left.append((x + nx * w, y + ny * w))
            right.append((x - nx * w, y - ny * w))
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in left + right[::-1])

    return band(2.2), band(0)


# ── the painted massif ────────────────────────────────────────────────────────

def catmull(points: list[tuple[float, float]], sharp: set[int] = frozenset(), per: int = 14) -> list[tuple[float, float]]:
    """A dense polyline through `points` (Catmull-Rom), with true corners at the `sharp` indices."""
    chains, start = [], 0
    for i in sorted(sharp):
        chains.append(points[start:i + 1])
        start = i
    chains.append(points[start:])
    out = []
    for ch in chains:
        if len(ch) < 2:
            continue
        p = [ch[0]] + ch + [ch[-1]]
        for i in range(1, len(p) - 2):
            p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
            for k in range(per):
                t = k / per
                t2, t3 = t * t, t * t * t
                x = .5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
                y = .5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
                out.append((x, y))
        out.append(ch[-1])
    return out


def poly(pts) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def _skyline(runs: list[dict]) -> tuple[list[tuple[float, float]], dict]:
    """The massif's skyline: over the lifted tops of the face runs, a crest between the two summits."""
    by = {r["code"]: r for r in runs}
    top = lambda c: (by[c]["top"][0], by[c]["top"][1] - LIFT)  # noqa: E731
    ge, zh = top("GE"), top(EAST_SUMMIT)
    west = [top(c) for c in reversed(WEST_FACE) if c in by]     # FR ... GE, rising
    lx = west[0][0]
    pts = [(-40, BASE_Y - 48), (lx - 150, BASE_Y - 70), (lx - 70, BASE_Y - 92)]
    pts += west[:-1]
    pts += [(ge[0] - 26, ge[1] + 40), ge]
    i_ge = len(pts) - 1
    # The crest: a saddle and two gendarmes between the summits.
    sx = (ge[0] + zh[0]) / 2
    saddle_y = max(ge[1], zh[1]) + 95
    pts += [(ge[0] + 34, ge[1] + 46), (ge[0] + 70, saddle_y - 30), (sx, saddle_y), (zh[0] - 60, saddle_y - 24),
            (zh[0] - 22, zh[1] + 36), zh]
    i_zh = len(pts) - 1
    # The east face: a long shoulder down to the valley.
    east = [top(c) for c in EAST_RIGHT if c in by]
    ex = east[-1][0] if east else zh[0] + 300
    drop = BASE_Y - 70 - zh[1]
    pts += [(zh[0] + 40, zh[1] + 60), (zh[0] + 95, zh[1] + drop * .42), (zh[0] + 150, zh[1] + drop * .62),
            (zh[0] + 205, zh[1] + drop * .82), (ex + 10, BASE_Y - 80), (ex + 90, BASE_Y - 62), (W + 40, BASE_Y - 50)]
    sky = catmull(pts, {i_ge, i_zh})
    assert all(y < BASE_Y for _, y in sky)
    return sky, {"ge": ge, "zh": zh, "saddle": (sx, saddle_y)}


def sky_at(sky: list[tuple[float, float]], x: float) -> float:
    lo, hi = 0, len(sky) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if sky[mid][0] < x:
            lo = mid
        else:
            hi = mid
    (x0, y0), (x1, y1) = sky[lo], sky[hi]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class Defs:
    """Collects gradients, filters and clip paths with unique ids."""

    def __init__(self):
        self.items, self.n = [], 0

    def add(self, body: str, prefix="d") -> str:
        self.n += 1
        ident = f"{prefix}{self.n}"
        self.items.append(body.replace("@ID@", ident))
        return ident

    def lin(self, a, b, stops) -> str:
        s = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{op}"/>' for o, c, op in stops)
        ident = self.add(f'<linearGradient id="@ID@" gradientUnits="userSpaceOnUse" x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}">{s}</linearGradient>')
        return f"url(#{ident})"

    def rad(self, c, r, stops, fx=None) -> str:
        s = "".join(f'<stop offset="{o}" stop-color="{col}" stop-opacity="{op}"/>' for o, col, op in stops)
        f = f' fx="{fx[0]:.1f}" fy="{fx[1]:.1f}"' if fx else ""
        ident = self.add(f'<radialGradient id="@ID@" gradientUnits="userSpaceOnUse" cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{r:.1f}"{f}>{s}</radialGradient>')
        return f"url(#{ident})"

    def clip(self, pts) -> str:
        ident = self.add(f'<clipPath id="@ID@"><polygon points="{poly(pts)}"/></clipPath>', "c")
        return f"url(#{ident})"

    def blur(self, sd: float) -> str:
        ident = self.add(f'<filter id="@ID@" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="{sd}"/></filter>', "f")
        return f"url(#{ident})"

    def svg(self) -> str:
        return "".join(self.items)


# ── sky and far ranges ───────────────────────────────────────────────────────

def sky_layer(d: Defs, rng: random.Random) -> str:
    g = d.lin((0, 0), (0, PANEL_Y), [(0, "#7f9fc9", 1), (.38, "#a9c2dd", 1), (.58, "#cfdde9", 1), (.72, "#e6edf2", 1)])
    out = [f'<rect width="{W}" height="{PANEL_Y}" fill="{g}"/>']
    # one thin cloud layer, stretched, behind the far range
    bl = d.blur(9)
    for cx, cy, rx, ry, op in [(250, 556, 170, 7, .45), (820, 578, 210, 6, .4), (1020, 548, 120, 5, .3)]:
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#fff" opacity="{op}" filter="{bl}"/>')
    return "".join(out)


def _range_profile(rng: random.Random, level: float, peaks: list[tuple[float, float, float, float]], noise: float):
    """y(x) for a range: `level` minus asymmetric peaks (cx, height, half-width left, right), with
    fractal detail that grows with height and a light smoothing so the summits are not needles."""
    ph = [rng.uniform(0, 6) for _ in range(4)]
    raw = []
    x = -60.0
    while x < W + 60:
        h = 0.0
        for cx, hh, wl, wr in peaks:
            w = wl if x < cx else wr
            t = 1 - abs(x - cx) / w
            if t > 0:
                h = max(h, hh * (t ** 1.25))
        rel = h / max(1, max(p[1] for p in peaks))
        n = (math.sin(x / 37 + ph[0]) + .7 * math.sin(x / 15 + ph[1]) + .5 * math.sin(x / 6.3 + ph[2]) + .3 * math.sin(x / 2.9 + ph[3]))
        raw.append((x, level - h + noise * n * (0.4 + rel)))
        x += 3
    pts = []
    for i, (x, y) in enumerate(raw):
        ys = [raw[j][1] for j in range(max(0, i - 2), min(len(raw), i + 3))]
        pts.append((x, sum(ys) / len(ys)))
    return pts


def far_ranges(d: Defs, rng: random.Random) -> str:
    """Three ranges in aerial perspective; each with its own peaks and faint lit/shadow facets."""
    out = []
    layers = [
        # level, base colour, lit colour, shadow colour, noise, peaks (cx, height, half-width left, right)
        (650, "#cbd9e7", "#dfe8f1", "#bccde0", 2.4,
         [(40, 70, 120, 90), (230, 52, 90, 110), (430, 96, 150, 120), (640, 60, 110, 130), (820, 104, 140, 160), (1010, 74, 110, 120), (1130, 50, 90, 90)]),
        (700, "#b9cbde", "#ccd9e8", "#a9bdd3", 3.2,
         [(-20, 66, 90, 150), (170, 92, 120, 140), (360, 56, 100, 90), (560, 42, 90, 120), (720, 78, 130, 110), (900, 112, 120, 170), (1080, 70, 110, 100)]),
        (755, "#a0b6cf", "#b9cadd", "#8ea4bf", 3.6,
         [(30, 70, 110, 120), (210, 46, 90, 90), (330, 58, 70, 110), (960, 56, 110, 90), (1100, 90, 130, 120)]),
    ]
    for level, base, lit, shade, noise, peaks in layers:
        prof = _range_profile(rng, level, peaks, noise)
        shape = prof + [(W + 60, BASE_Y + 2), (-60, BASE_Y + 2)]
        clip = d.clip(shape)
        out.append(f'<polygon fill="{base}" points="{poly(shape)}"/>')
        facets = []
        for cx, hh, wl, wr in peaks:
            ai = min(range(len(prof)), key=lambda i: prof[i][1] if abs(prof[i][0] - cx) < 24 else 1e9)
            apex = prof[ai]
            li = [p for p in prof if cx - wl * .9 <= p[0] <= apex[0]]
            ri = [p for p in prof if apex[0] <= p[0] <= cx + wr * .75]
            if len(li) > 2:
                g = d.lin(apex, (cx - wl * .5, level + 10), [(0, lit, .95), (1, lit, 0)])
                facets.append(f'<polygon fill="{g}" points="{poly(li + [(li[-1][0], level + 12), (li[0][0], level + 12)])}"/>')
            if len(ri) > 2:
                g = d.lin(apex, (cx + wr * .55, level + 10), [(0, shade, .9), (1, shade, 0)])
                facets.append(f'<polygon fill="{g}" points="{poly(ri + [(ri[-1][0], level + 12), (ri[0][0], level + 12)])}"/>')
            # a shaded gully or two down the right flank
            for k in range(2):
                gx = apex[0] + wr * rng.uniform(.08, .4)
                gy = _y_on(prof, gx)
                facets.append(f'<polygon fill="{shade}" opacity=".45" points="{poly([(gx, gy), (gx + 3, gy + 2), (gx + wr * .25 + 6, level + 10), (gx + wr * .25 - 4, level + 10)])}"/>')
        out.append(f'<g clip-path="{clip}">{"".join(facets)}</g>')
    # haze at the base of the far ranges
    hz = d.lin((0, 610), (0, 880), [(0, "#e6edf3", 0), (.5, "#e6edf3", .42), (1, "#eaf0f4", .85)])
    out.append(f'<rect x="0" y="560" width="{W}" height="{BASE_Y - 560}" fill="{hz}"/>')
    return "".join(out)


def _y_on(prof, x):
    return min(prof, key=lambda p: abs(p[0] - x))[1]


# ── the massif ───────────────────────────────────────────────────────────────

def _ridges(marks: dict, sky) -> dict:
    """The ridgelines that organise the faces (sharp lit/shadow splits)."""
    ge, zh, sd = marks["ge"], marks["zh"], marks["saddle"]
    sky_at_safe = lambda x: sky_at(sky, x)  # noqa: E731
    r1 = catmull([ge, (ge[0] + 9, ge[1] + 70), (ge[0] + 22, ge[1] + 160), (ge[0] + 30, ge[1] + 260), (ge[0] + 36, ge[1] + 340), (ge[0] + 48, BASE_Y - 60)], per=10)
    r4 = catmull([zh, (zh[0] - 4, zh[1] + 50), (zh[0] - 16, zh[1] + 120), (zh[0] - 34, zh[1] + 200), (zh[0] - 44, zh[1] + 270), (zh[0] - 40, BASE_Y - 90)], per=10)
    # a spur on the lit west face, coming toward the viewer (runs 3-5 ski beside it)
    r2 = catmull([(ge[0] - 118, sky_at_safe(ge[0] - 118) + 2), (ge[0] - 150, ge[1] + 230), (ge[0] - 190, ge[1] + 320), (ge[0] - 240, ge[1] + 400), (ge[0] - 300, BASE_Y - 40)], per=10)
    # a spur coming toward the viewer from the saddle
    r5 = catmull([(sd[0] + 6, sd[1] + 4), (sd[0] + 4, sd[1] + 60), (sd[0] - 4, sd[1] + 130), (sd[0] - 6, sd[1] + 200), (sd[0] + 2, BASE_Y - 40)], per=10)
    return {"r1": r1, "r2": r2, "r4": r4, "r5": r5}


def relief(sky, runs, rng: random.Random):
    """Micro-relief on the skyline: small upward bumps between the run tops (never at them, so every
    plate still sits under the skyline), larger along the crest and the east shoulder."""
    tops = sorted(r["top"][0] for r in runs)
    ph = [rng.uniform(0, 6) for _ in range(3)]
    out = []
    for x, y in sky:
        gap = min(abs(x - t) for t in tops)
        amp = min(1, max(0, (gap - 8) / 22))
        n = .5 + .5 * math.sin(x / 13 + ph[0]) * math.sin(x / 5.3 + ph[1]) + .3 * math.sin(x / 2.7 + ph[2])
        out.append((x, y - amp * 8 * max(0, n)))
    return out


def massif_body(d: Defs, sky, marks) -> str:
    ge, zh = marks["ge"], marks["zh"]
    g = d.lin((ge[0] - 160, ge[1]), (zh[0] + 320, BASE_Y), [(0, "#ffffff", 1), (.45, "#f4f7fa", 1), (1, "#d7e1eb", 1)])
    shape = sky + [(W + 60, BASE_Y + 2), (-60, BASE_Y + 2)]
    return f'<polygon fill="{g}" points="{poly(shape)}"/>'


def snow_forms(d: Defs, sky, marks, ridges, rng: random.Random) -> str:
    """Modelled snow: shadow faces east of each ridgeline (sharp at the ridge, fading away), concave
    hollows, convex bulges, cast shadows and couloirs. Clipped to the massif by the caller."""
    ge, zh, sd = marks["ge"], marks["zh"], marks["saddle"]
    r1, r2, r4, r5 = ridges["r1"], ridges["r2"], ridges["r4"], ridges["r5"]
    SH, SH2 = "#6a8bb6", "#8aa6c8"
    out = []
    soft = d.blur(7)
    softer = d.blur(14)
    fine = d.blur(2.2)

    # 1. GE east face: shadow right of r1 down into the bowl. Sharp at the ridge.
    g = d.lin((ge[0] + 20, ge[1] + 150), (ge[0] + 150, ge[1] + 200), [(0, SH, .62), (.5, SH, .32), (1, SH, 0)])
    out.append(f'<polygon fill="{g}" points="{poly(r1 + [(ge[0] + 200, BASE_Y), (sd[0] + 90, sd[1] + 120), (sd[0] + 40, sd[1] - 10), (ge[0] + 90, ge[1] + 40)])}"/>')
    # 2. the bowl under the saddle: deep concave shadow, soft
    g = d.rad((sd[0] + 20, sd[1] + 150), 170, [(0, SH, .4), (1, SH, 0)])
    out.append(f'<ellipse cx="{sd[0] + 20}" cy="{sd[1] + 150}" rx="170" ry="170" fill="{g}"/>')
    # 3. spur r5 from the saddle: lit left flank, shaded right flank
    g = d.lin((sd[0] + 2, sd[1] + 120), (sd[0] + 60, sd[1] + 120), [(0, SH, .3), (1, SH, 0)])
    out.append(f'<polygon fill="{g}" points="{poly(r5 + [(sd[0] + 90, BASE_Y), (sd[0] + 100, sd[1] + 60), (sd[0] + 60, sd[1])])}"/>')
    g = d.lin((sd[0] + 2, sd[1] + 120), (sd[0] - 40, sd[1] + 120), [(0, "#ffffff", .55), (1, "#ffffff", 0)])
    out.append(f'<polygon fill="{g}" filter="{fine}" points="{poly(r5 + [(sd[0] - 50, BASE_Y), (sd[0] - 50, sd[1] + 40)])}"/>')
    # 4. ZH east face: shadow right of r4, running under the east shoulder.
    g = d.lin((zh[0] - 24, zh[1] + 150), (zh[0] + 150, zh[1] + 200), [(0, SH, .5), (.08, SH, .34), (.5, SH, .26), (1, SH, 0)])
    out.append(f'<polygon fill="{g}" points="{poly(r4 + [(zh[0] + 60, BASE_Y), (zh[0] + 330, BASE_Y), (zh[0] + 330, zh[1] + 190), (zh[0] + 80, zh[1] + 30)])}"/>')
    # the shadow eases out where the slope flattens toward the valley
    g = d.lin((0, BASE_Y - 160), (0, BASE_Y - 20), [(0, "#ffffff", 0), (1, "#ffffff", .5)])
    out.append(f'<rect x="{ge[0]}" y="{BASE_Y - 170}" width="{W - ge[0]}" height="170" fill="{g}"/>')
    # the west spur r2: lit left flank (warm white), shaded right flank, sharp along the ridge
    g = d.lin((ge[0] - 190, ge[1] + 320), (ge[0] - 130, ge[1] + 335), [(0, SH2, .4), (1, SH2, 0)])
    out.append(f'<polygon fill="{g}" points="{poly(r2 + [(ge[0] - 200, BASE_Y), (ge[0] - 60, BASE_Y - 60), (ge[0] - 70, ge[1] + 240), (ge[0] - 100, ge[1] + 140)])}"/>')
    g = d.lin((ge[0] - 190, ge[1] + 320), (ge[0] - 236, ge[1] + 306), [(0, "#ffffff", .9), (1, "#ffffff", 0)])
    out.append(f'<polygon fill="{g}" points="{poly(r2 + [(ge[0] - 400, BASE_Y - 30), (ge[0] - 330, ge[1] + 300), (ge[0] - 230, ge[1] + 150), (ge[0] - 140, ge[1] + 120)])}"/>')
    # the east shoulder: steeper upper slope darker, flattening paler (concave profile)
    g = d.lin((zh[0] + 120, zh[1] + 80), (zh[0] + 240, zh[1] + 330), [(0, SH, .38), (1, SH, 0)])
    out.append(f'<polygon fill="{g}" filter="{soft}" points="{poly([(zh[0] + 20, zh[1] + 20), (zh[0] + 420, zh[1] + 330), (zh[0] + 420, BASE_Y), (zh[0] + 60, BASE_Y)])}"/>')
    # 5. lit west face of GE: warm light, with convex bulges (bright) and hollows (cool)
    g = d.lin((ge[0] - 60, ge[1] + 60), (ge[0] - 320, BASE_Y), [(0, "#ffffff", .9), (1, "#ffffff", 0)])
    out.append(f'<polygon fill="{g}" points="{poly([ge, (ge[0] - 40, ge[1] + 120), (ge[0] - 260, BASE_Y - 40), (ge[0] - 380, BASE_Y - 60), (ge[0] - 200, ge[1] + 200)])}"/>')
    for cx, cy, rx, ry, rot, op in [(ge[0] - 150, ge[1] + 290, 60, 150, -58, .22), (ge[0] - 250, ge[1] + 400, 48, 110, -62, .18), (zh[0] - 70, zh[1] + 230, 40, 120, -70, .2),
                                    (ge[0] - 60, ge[1] + 330, 34, 130, -60, .16), (ge[0] - 330, ge[1] + 440, 40, 70, -50, .14)]:
        rr = max(rx, ry)
        ident = d.add(f'<radialGradient id="@ID@" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{SH2}" stop-opacity="{op}"/><stop offset="1" stop-color="{SH2}" stop-opacity="0"/></radialGradient>')
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({rot} {cx} {cy})" fill="url(#{ident})"/>')
    # a slope break on the west face: the convex shoulder lit above, a soft shadow band below it
    brk = catmull([(ge[0] - 400, ge[1] + 440), (ge[0] - 300, ge[1] + 330), (ge[0] - 190, ge[1] + 250), (ge[0] - 90, ge[1] + 200), (ge[0] - 30, ge[1] + 150)], per=8)
    below = [(x + 14, y + 46) for x, y in brk]
    g = d.lin((ge[0] - 200, ge[1] + 250), (ge[0] - 180, ge[1] + 300), [(0, SH2, .34), (1, SH2, 0)])
    out.append(f'<polygon fill="{g}" filter="{fine}" points="{poly(brk + below[::-1])}"/>')
    above = [(x - 6, y - 26) for x, y in brk]
    g = d.lin((ge[0] - 200, ge[1] + 250), (ge[0] - 210, ge[1] + 222), [(0, "#ffffff", .8), (1, "#ffffff", 0)])
    out.append(f'<polygon fill="{g}" filter="{soft}" points="{poly(brk + above[::-1])}"/>')
    # soft ribs down the west face, parallel to the fall line
    for i in range(6):
        x0 = ge[0] - 60 - i * 58
        y0 = sky_at(sky, x0) + 20
        pts_a = [(x0 - (y - y0) * .42, y) for y in range(int(y0), BASE_Y - 30, 20)]
        if len(pts_a) < 3:
            continue
        wdt = rng.uniform(10, 22)
        out.append(f'<polygon fill="{SH2}" opacity="{rng.uniform(.14, .24):.2f}" filter="{soft}" points="{poly(pts_a + [(x + wdt, y) for x, y in pts_a[::-1]])}"/>')
    # 6. cast shadow of the GE summit ridge across the bowl (soft, long)
    out.append(f'<polygon fill="{SH}" opacity=".26" filter="{softer}" points="{poly([(ge[0] + 40, ge[1] + 40), (ge[0] + 160, ge[1] + 120), (sd[0] + 40, sd[1] + 140), (ge[0] + 90, ge[1] + 300)])}"/>')
    out.append(f'<polygon fill="{SH}" opacity=".22" filter="{softer}" points="{poly([(zh[0] + 20, zh[1] + 30), (zh[0] + 200, zh[1] + 170), (zh[0] + 240, zh[1] + 300), (zh[0] + 60, zh[1] + 200)])}"/>')
    # 7. couloirs: soft shaded streaks following the fall line, converging into the bowls
    def couloir(a, b, w0, w1, op):
        mid = ((a[0] + b[0]) / 2 + rng.uniform(-8, 8), (a[1] + b[1]) / 2)
        c = catmull([a, mid, b], per=8)
        left, right = [], []
        for i, (x, y) in enumerate(c):
            w = (w0 + (w1 - w0) * i / (len(c) - 1)) / 2
            left.append((x - w, y)); right.append((x + w, y))
        return f'<polygon fill="{SH}" opacity="{op}" filter="{fine}" points="{poly(left + right[::-1])}"/>'
    for i in range(5):
        a = (ge[0] + 36 + i * 17, ge[1] + 140 + i * 10)
        b = (sd[0] - 30 + i * 18, sd[1] + 190 + i * 12)
        out.append(couloir(a, b, 3, 9, .3 - i * .03))
    for i in range(6):
        a = (zh[0] + 30 + i * 26, zh[1] + 105 + i * 22)
        b = (zh[0] + 70 + i * 36, zh[1] + 300 + i * 12)
        out.append(couloir(a, b, 3, 11, .3 - i * .03))
    for i in range(3):
        a = (sd[0] + 24 + i * 15, sd[1] + 44)
        b = (sd[0] + 20 + i * 20, sd[1] + 210)
        out.append(couloir(a, b, 2.5, 7, .26))
    # 8. avalanche fans at the bottom of the couloirs (pale, textured)
    for cx, cy in [(ge[0] + 110, ge[1] + 330), (zh[0] + 130, zh[1] + 350)]:
        g = d.lin((cx, cy - 70), (cx, cy + 30), [(0, "#ffffff", .5), (1, "#ffffff", 0)])
        out.append(f'<polygon fill="{g}" filter="{fine}" points="{poly([(cx - 8, cy - 70), (cx + 8, cy - 70), (cx + 60, cy + 30), (cx - 50, cy + 30)])}"/>')
    # 9. the ridgelines themselves: a hair of light along the lit edge
    for r, op in ((r1, .8), (r2, .7), (r4, .45)):
        out.append(f'<polyline fill="none" stroke="#ffffff" stroke-opacity="{op}" stroke-width="1.1" points="{poly(r)}"/>')
    return "".join(out)


def cornice(d: Defs, marks, sky) -> str:
    """A wind-built snow cornice on the saddle, overhanging the lee (right) side; blue shadow under
    its lip, a thin crack line where it meets the slope."""
    sx, sy = marks["saddle"]
    y = lambda x: sky_at(sky, x)  # noqa: E731
    lip = catmull([(sx - 60, y(sx - 60)), (sx - 40, y(sx - 40) - 5), (sx - 15, y(sx - 15) - 9), (sx + 8, y(sx + 8) - 10), (sx + 26, y(sx + 26) - 8),
                   (sx + 42, y(sx + 42) - 2), (sx + 50, y(sx + 50) + 6), (sx + 44, y(sx + 44) + 14), (sx + 30, y(sx + 30) + 10),
                   (sx + 10, y(sx + 10) + 2), (sx - 20, y(sx - 20) + 1), (sx - 60, y(sx - 60) + 2)], per=8)
    under = catmull([(sx + 50, y(sx + 50) + 6), (sx + 70, y(sx + 70) + 30), (sx + 56, y(sx + 56) + 52), (sx + 30, y(sx + 30) + 34), (sx + 44, y(sx + 44) + 14)], per=6)
    g = d.lin((sx + 40, y(sx + 40) + 12), (sx + 60, y(sx + 60) + 50), [(0, "#5f80ab", .7), (1, "#5f80ab", 0)])
    bl = d.blur(1.5)
    return (f'<polygon fill="{g}" filter="{bl}" points="{poly(under)}"/>'
            f'<polygon fill="#ffffff" points="{poly(lip)}"/>'
            f'<polyline fill="none" stroke="#8ea8c8" stroke-width=".9" stroke-opacity=".8" points="{poly([(x, yy) for x, yy in lip if x > sx + 20][:12])}"/>')


# ── rock ─────────────────────────────────────────────────────────────────────

ROCK = {"dark": "#4a5461", "mid": "#596471", "light": "#68737f", "warm": "#8a8782", "cool": "#404b58"}


def _along(line, t):
    """Point at fraction t of a polyline's length."""
    seg = [math.dist(line[i], line[i + 1]) for i in range(len(line) - 1)]
    total = sum(seg) or 1
    want = t * total
    for i, L in enumerate(seg):
        if want <= L or i == len(seg) - 1:
            f = 0 if L == 0 else want / L
            return (line[i][0] + (line[i + 1][0] - line[i][0]) * f, line[i][1] + (line[i + 1][1] - line[i][1]) * f)
        want -= L
    return line[-1]


def rock_face(d: Defs, rng: random.Random, top, depth, lean=.3, ribs=9, tilt=-14.0, shade=.5, cap=0.0) -> str:
    """A rock face as a cluster of ribs hanging from the polyline `top` (a ridge or the skyline).
    Each rib is an irregular tall polygon leaning down-slope by `lean` (dx per dy), its length a
    random share of `depth`; snow shows in the gullies between them. Strata, cracks and ledge snow
    are drawn once over the union. `shade` 0..1 darkens the whole face (shadow side). `cap` > 0 adds
    a summit block of that depth over the first point."""
    polys = []
    # the head: solid rock along the ridge, its lower edge ragged
    head_d = depth * rng.uniform(.22, .34)
    upper = [(x, y - 3) for x, y in top]
    lower = []
    for k in range(len(top) * 2 + 1):
        t = k / (len(top) * 2)
        x, y = _along(top, t)
        dd = head_d * rng.uniform(.55, 1.35)
        lower.append((x + lean * dd + rng.uniform(-3, 3), y + dd))
    polys.append(upper + lower[::-1])
    for i in range(ribs):
        t = (i + rng.uniform(.1, .9)) / ribs
        ax, ay = _along(top, t)
        L = depth * rng.uniform(.45, 1.0) * (1 - .35 * abs(t - .4))
        w = rng.uniform(16, 36) * (.55 + .5 * L / depth)
        ln = lean + rng.uniform(-.14, .14)
        pts_l, pts_r = [], []
        n = 6
        for k in range(n + 1):
            f = k / n
            y = ay + L * f
            x = ax + ln * L * f
            ww = w * (1 - .6 * f ** 1.4) * rng.uniform(.8, 1.2) if k < n else w * .12
            pts_l.append((x - ww / 2 + rng.uniform(-3, 3), y + rng.uniform(-3, 3)))
            pts_r.append((x + ww / 2 + rng.uniform(-3, 3), y + rng.uniform(-3, 3)))
        pts_l[0] = (ax - w / 2, ay - 3)
        pts_r[0] = (ax + w / 2, ay - 3)
        polys.append(pts_l + pts_r[::-1])
    if cap:
        ax, ay = top[0]
        polys.append([(ax - 4, ay - 2), (ax + cap * .5 + 6, ay + cap * .35), (ax + cap * .9, ay + cap), (ax + cap * .2, ay + cap * 1.1), (ax - cap * .25, ay + cap * .7), (ax - cap * .2, ay + cap * .3)])
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    ident = d.add('<clipPath id="@ID@">' + "".join(f'<polygon points="{poly(p)}"/>' for p in polys) + "</clipPath>", "c")
    base = _mix(ROCK["mid"], ROCK["cool"], shade)
    out = []
    # the face casts a soft shadow down-right onto the snow
    sh = d.blur(5)
    for p in polys:
        out.append(f'<polygon fill="#5b7ba8" opacity=".2" filter="{sh}" points="{poly([(x + 5, y + 7) for x, y in p])}"/>')
    for p in polys:
        # each rib: darker at the top and on its right edge
        px = [x for x, _ in p]
        g = d.lin((min(px), 0), (max(px), 0), [(0, _mix(ROCK["light"], ROCK["cool"], shade * .6), 1), (1, _mix(ROCK["dark"], ROCK["cool"], shade), 1)])
        out.append(f'<polygon fill="{g}" points="{poly(p)}"/>')
    t = math.radians(tilt)
    dx, dy = math.cos(t), math.sin(t)
    nx, ny = -dy, dx
    inner = []
    span = math.hypot(maxx - minx, maxy - miny)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    sv = -span / 2
    i = 0
    while sv < span / 2:
        w = rng.uniform(4, 9)
        col = (ROCK["dark"], ROCK["light"], ROCK["mid"], ROCK["cool"])[i % 4]
        u = -span / 2
        while u < span / 2:
            seg = rng.uniform(8, 26)
            if rng.random() < .35:
                u += seg
                continue
            o = rng.uniform(-1.5, 1.5)
            ww = w * rng.uniform(.4, 1.0)
            a0 = (cx + dx * u + nx * (sv + o), cy + dy * u + ny * (sv + o))
            a1 = (cx + dx * (u + seg) + nx * (sv + o + rng.uniform(-1, 1)), cy + dy * (u + seg) + ny * (sv + o + rng.uniform(-1, 1)))
            inner.append(f'<polygon fill="{col}" opacity=".7" points="{poly([a0, a1, (a1[0] + nx * ww, a1[1] + ny * ww), (a0[0] + nx * ww, a0[1] + ny * ww)])}"/>')
            u += seg
        sv += w * 1.6
        i += 1
    for _ in range(int(ribs * 4)):
        px, py = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        ln = rng.uniform(3, 10)
        inner.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{px + rng.uniform(-3, 3):.1f}" y2="{py + ln:.1f}" stroke="{ROCK["cool"]}" stroke-width=".9" stroke-opacity=".7"/>')
    for _ in range(int(ribs * 2.5)):   # snow on the ledges, mostly left (lit)
        px, py = rng.uniform(minx, cx), rng.uniform(miny + 6, maxy)
        ln = rng.uniform(3, 9)
        inner.append(f'<polygon fill="#f3f6fa" opacity="{rng.uniform(.4, .8):.2f}" points="{poly([(px, py), (px + dx * ln, py + dy * ln), (px + dx * ln + .3, py + dy * ln + .9), (px, py + 1.0)])}"/>')
    gl = d.lin((minx, miny), (maxx, miny + (maxy - miny) * .5), [(0, ROCK["warm"], .5 * (1 - shade)), (.45, ROCK["warm"], 0), (.7, ROCK["cool"], 0), (1, ROCK["cool"], .5)])
    inner.append(f'<rect x="{minx}" y="{miny}" width="{maxx - minx}" height="{maxy - miny}" fill="{gl}"/>')
    out.append(f'<g clip-path="url(#{ident})">{"".join(inner)}</g>')
    return "".join(out)


def scree(d: Defs, rng: random.Random, apex, width, depth) -> str:
    """A scree fan below a cliff: grey fading into the snow, with a scatter of stones."""
    ax, ay = apex
    g = d.lin((ax, ay), (ax, ay + depth), [(0, ROCK["light"], .6), (.4, ROCK["light"], .25), (1, ROCK["light"], 0)])
    fan = [(ax - 6, ay), (ax + 6, ay), (ax + width / 2, ay + depth), (ax - width / 2, ay + depth)]
    out = [f'<polygon fill="{g}" points="{poly(fan)}"/>']
    for _ in range(int(width * depth / 60)):
        t = rng.random() ** .6
        x = ax + rng.uniform(-1, 1) * width / 2 * t
        y = ay + depth * t
        r = rng.uniform(.3, .7)
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{ROCK["mid"]}" opacity="{.55 - .45 * t:.2f}"/>')
    return "".join(out)


def _sky_seg(sky, x0, x1):
    return [p for p in sky if x0 <= p[0] <= x1]


def rocks(d: Defs, sky, marks, ridges, rng: random.Random) -> tuple[str, str]:
    """(rock inside the massif, gendarmes standing on the skyline)."""
    ge, zh, sd = marks["ge"], marks["zh"], marks["saddle"]
    r1, r5 = ridges["r1"], ridges["r5"]
    out, teeth = [], []
    # GE: the summit block and a rock face down the shadow side of the summit ridge (r1) and the crest
    top = [(ge[0] - 3, ge[1] + 2)] + _sky_seg(sky, ge[0] + 1, ge[0] + 48)
    out.append(rock_face(d, rng, top, 150, lean=.3, ribs=13, tilt=-16, shade=.45, cap=46))
    out.append(rock_face(d, rng, r1[2:len(r1) // 3], 70, lean=.5, ribs=8, tilt=-16, shade=.55))
    out.append(scree(d, rng, (ge[0] + 55, ge[1] + 150), 70, 50))
    # the crest GE -> saddle -> ZH: a broken rock band under the skyline, gendarmes on it
    out.append(rock_face(d, rng, _sky_seg(sky, ge[0] + 50, sd[0] - 22), 46, lean=.35, ribs=7, tilt=-20, shade=.5))
    out.append(rock_face(d, rng, _sky_seg(sky, sd[0] + 30, zh[0] - 24), 40, lean=.1, ribs=6, tilt=-8, shade=.3))
    for x in (ge[0] + 62, ge[0] + 86, sd[0] - 60, sd[0] + 50, sd[0] + 72, zh[0] - 56):
        y = sky_at(sky, x)
        tooth = [(x - 6, y + 10), (x - 3, y - rng.uniform(3, 9)), (x + 2, y - rng.uniform(0, 4)), (x + 6, y + 3), (x + 9, y + 14)]
        teeth.append(f'<polygon fill="{ROCK["dark"]}" points="{poly(tooth)}"/>'
                     f'<polygon fill="{ROCK["warm"]}" opacity=".7" points="{poly([tooth[0], tooth[1], (x - 1, y + 5)])}"/>')
    # ZH: summit block, rock down the east face and along the upper shoulder
    # (the face starts a little right of the summit and leans further than the Zurich run, which skirts it)
    top = _sky_seg(sky, zh[0] + 14, zh[0] + 76)
    out.append(rock_face(d, rng, top, 118, lean=.58, ribs=12, tilt=-12, shade=.5, cap=26))
    out.append(scree(d, rng, (zh[0] + 86, zh[1] + 140), 70, 50))
    out.append(rock_face(d, rng, _sky_seg(sky, zh[0] + 125, zh[0] + 215), 64, lean=.45, ribs=8, tilt=-24, shade=.4))
    out.append(scree(d, rng, (zh[0] + 190, sky_at(sky, zh[0] + 190) + 70), 90, 55))
    out.append(rock_face(d, rng, _sky_seg(sky, zh[0] + 240, zh[0] + 300), 38, lean=.5, ribs=5, tilt=-22, shade=.35))
    # outcrops on the saddle spur and the GE spur, low cliff on the west flank
    return "".join(out), "".join(teeth)


# ── forest and valley ────────────────────────────────────────────────────────

def _shade_at(x: float, marks) -> float:
    """0 = lit lower slope, 1 = in the shadow of a ridge (bowl, east face)."""
    ge, zh, sd = marks["ge"], marks["zh"], marks["saddle"]
    if x < ge[0] + 20:
        return 0.1
    if x < sd[0] + 10:
        return 0.9
    if x < zh[0] - 10:
        return 0.35
    if x < zh[0] + 300:
        return 0.85
    return 0.5


def _mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    rb, gb, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def _conifer(x, y, h, col, w=.34) -> str:
    """A conifer standing at (x, y): two stacked tiers and a trunk."""
    return (f'<path fill="{col}" d="M{x:.1f},{y - h:.1f}l{h * w:.1f},{h * .62:.1f}h{-h * w * 2:.1f}z'
            f'M{x:.1f},{y - h * .62:.1f}l{h * w * 1.25:.1f},{h * .62:.1f}h{-h * w * 2.5:.1f}z"/>')


def forest(d: Defs, sky, marks, rng: random.Random) -> str:
    LIT, SHA, TOP = "#3b684b", "#1e382a", "#5a8a66"
    out = []

    def line(x):   # the upper edge of the main tree mass
        return 852 + 10 * math.sin(x / 88) + 6 * math.sin(x / 31 + 1) + 3 * math.sin(x / 11)

    top = [(x, max(line(x), sky_at(sky, x) + 14)) for x in range(-10, W + 20, 4)]
    foot = [(x, BASE_Y - 2 + 5 * math.sin(x / 23 + 2) * math.sin(x / 7.1)) for x in range(W + 20, -14, -6)]
    mass = top + foot
    mclip = d.clip(mass)
    g = d.lin((0, 845), (0, BASE_Y), [(0, LIT, 1), (1, SHA, 1)])
    out.append(f'<polygon fill="{g}" points="{poly(mass)}"/>')
    inner = []
    for x in range(0, W, 10):
        sh = _shade_at(x, marks)
        if sh > .2:
            inner.append(f'<rect x="{x}" y="800" width="10" height="110" fill="{SHA}" opacity="{sh * .5:.2f}"/>')
    # texture inside the mass: lit tops and dark gaps
    for _ in range(420):
        tx = rng.uniform(0, W)
        ty = rng.uniform(line(tx) + 3, BASE_Y + 2)
        if ty < sky_at(sky, tx) + 16:
            continue
        sh = _shade_at(tx, marks)
        if rng.random() < .5:
            inner.append(_conifer(tx, ty, rng.uniform(5, 9), _mix(TOP, LIT, sh * .8), w=.3))
        else:
            inner.append(_conifer(tx, ty, rng.uniform(5, 9), _mix(SHA, "#122218", .5), w=.3))
    out.append(f'<g clip-path="{mclip}">{"".join(inner)}</g>')
    # the upper edge dissolves into individual tops; the slope above thins out
    x = -6.0
    while x < W + 6:
        base = max(line(x), sky_at(sky, x) + 14)
        sh = _shade_at(x, marks)
        col = _mix(LIT, SHA, sh * .8)
        out.append(_conifer(x, base + rng.uniform(1, 5), rng.uniform(7, 12), col))
        if rng.random() < .5:   # a tree standing out at the foot of the mass
            out.append(_conifer(x + rng.uniform(-2, 2), BASE_Y + rng.uniform(-1, 4), rng.uniform(6, 10), _mix(SHA, LIT, .3)))
        x += rng.uniform(4.5, 7)
    # the slope above the line: tongues of forest rising in clusters, thinning upward
    for _ in range(26):
        gx = rng.uniform(-10, W + 10)
        base = max(line(gx), sky_at(sky, gx) + 14)
        rise = rng.uniform(10, 55)
        sh = _shade_at(gx, marks)
        for _ in range(rng.randint(6, 18)):
            dy = rise * rng.random() ** 1.6
            tx, ty = gx + rng.uniform(-14, 14) * (1 + dy / 40), base - dy + rng.uniform(-3, 3)
            if ty > sky_at(sky, tx) + 12:
                out.append(_conifer(tx, ty, rng.uniform(5, 9.5), _mix(_mix(LIT, SHA, sh * .8), "#2c5a3e", .3)))
    # small groups on the lower slopes
    for _ in range(16):
        gx, gy = rng.uniform(0, W), rng.uniform(760, 830)
        if gy < sky_at(sky, gx) + 30:
            continue
        sh = _shade_at(gx, marks)
        for _ in range(rng.randint(3, 8)):
            tx, ty = gx + rng.uniform(-18, 18), gy + rng.uniform(-7, 7)
            if ty > sky_at(sky, tx) + 12:
                out.append(_conifer(tx, ty, rng.uniform(5, 8.5), _mix(LIT, SHA, sh * .7 + rng.uniform(-.1, .1))))
    return "".join(out)


def valley(d: Defs, rng: random.Random) -> str:
    g = d.lin((0, BASE_Y), (0, PANEL_Y), [(0, "#e8edf0", 1), (1, "#eef1f1", 1)])
    out = [f'<rect x="0" y="{BASE_Y}" width="{W}" height="{PANEL_Y - BASE_Y}" fill="{g}"/>']
    # soft tonal variation on the snow plain
    bl = d.blur(8)
    for cx, op in [(200, .25), (620, .2), (950, .22)]:
        out.append(f'<ellipse cx="{cx}" cy="{BASE_Y + 26}" rx="170" ry="9" fill="#c9d6e3" opacity="{op}" filter="{bl}"/>')
    # a faint road along the valley, below the finish zone
    pts = [(x, BASE_Y + 26 + 3 * math.sin(x / 140) + 1.5 * math.sin(x / 47)) for x in range(-10, W + 20, 10)]
    out.append(f'<polyline fill="none" stroke="#cfd8df" stroke-width="3.2" points="{poly(pts)}"/>'
               f'<polyline fill="none" stroke="#f6f8f9" stroke-width="1.2" points="{poly(pts)}"/>')
    # a scatter of trees on the valley floor, away from the finish zone
    for _ in range(70):
        x, y = rng.uniform(0, W), rng.uniform(BASE_Y + 18, PANEL_Y - 2)
        out.append(_conifer(x, y, rng.uniform(5, 9), rng.choice(["#3e6d4e", "#2f5a3f", "#4a7a58"])))
    return "".join(out)


def grain(d: Defs) -> str:
    ident = d.add('<filter id="@ID@" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".012 .03" numOctaves="4" seed="11"/>'
                  '<feColorMatrix type="matrix" values="0 0 0 0 .45  0 0 0 0 .56  0 0 0 0 .7  0 0 0 .42 0"/></filter>', "f")
    return f'<rect x="0" y="380" width="{W}" height="{BASE_Y - 380}" filter="url(#{ident})"/>'


def terrain(sky, marks, runs, rng: random.Random) -> tuple[str, str, list]:
    d = Defs()
    sky = relief(sky, runs, rng)
    ridges = _ridges(marks, sky)
    massif_poly = sky + [(W + 60, BASE_Y + 2), (-60, BASE_Y + 2)]
    mclip = d.clip(massif_poly)
    scene_clip = d.clip([(0, 0), (W, 0), (W, PANEL_Y), (0, PANEL_Y)])
    above = d.clip([(0, 0), (W, 0), (W, BASE_Y), (0, BASE_Y)])
    rock_in, teeth = rocks(d, sky, marks, ridges, rng)
    parts = [
        sky_layer(d, rng),
        f'<g clip-path="{above}">{far_ranges(d, rng)}</g>',
        massif_body(d, sky, marks),
        f'<g clip-path="{mclip}">{snow_forms(d, sky, marks, ridges, rng)}{grain(d)}</g>',
        cornice(d, marks, sky),
        f'<g clip-path="{mclip}">{rock_in}</g>{teeth}',
        valley(d, rng),
        f'<g clip-path="{scene_clip}">{forest(d, sky, marks, rng)}</g>',
        f'<polyline fill="none" stroke="#fff" stroke-width=".9" stroke-opacity=".6" points="{poly(sky)}"/>',
    ]
    return d.svg(), "".join(parts), sky


def _tag(r: dict, f) -> str:
    """A horizontal white tag beside the plate of one of the three longest runs."""
    tx, ty = r["top"]
    text = f'{r["name"]}  {f(r["n"])}'
    w = 8.6 * len(text) + 18
    side = -1 if r["face"] < 0 else 1
    x = tx + side * 24 + (-w if side < 0 else 0)
    y = ty - 19 - 13
    return (f'<rect class="tag" x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="26" rx="3"/>'
            f'<text class="tagt" x="{x + 9:.0f}" y="{y + 17.5:.0f}">{esc(r["name"])}'
            f'<tspan class="tagn"> {f(r["n"])}</tspan></text>')


def design(week: dict, lang: str = "en") -> str:
    f = lambda n: wp.fmt(n, lang)  # noqa: E731
    a, b = week["from"], week["to"]
    runs = _layout(_runs(week))
    rng = random.Random(42)
    sky, marks = _skyline(runs)
    for r in runs:  # every plate sits below the skyline
        assert sky_at(sky, r["top"][0]) < r["top"][1] - 30, (r["code"], sky_at(sky, r["top"][0]), r["top"])

    defs, terrain_svg, sky = terrain(sky, marks, runs, rng)
    for r in runs:  # the relief only lifts the skyline, never onto a plate
        assert sky_at(sky, r["top"][0]) < r["top"][1] - 30

    paths, plates, tags = [], [], []
    for r in sorted(runs, key=lambda r: -r["n"]):
        casing, body = _run_shape(r)
        paths.append(f'<polygon class="casing" points="{casing}"/><polygon fill="{COLOUR[r["col"]]}" points="{body}"/>')
        tx, ty = r["top"]
        plates.append(f'<g transform="translate({tx:.1f},{ty - 19:.1f})"><circle r="13" fill="{COLOUR[r["col"]]}" class="plate"/>'
                      f'<text class="no">{r["no"]}</text></g>')
        if r["no"] <= 3:
            tags.append(_tag(r, f))

    order = sorted(runs, key=lambda r: r["no"])
    per = -(-len(order) // 3)
    key_cols = "".join(
        '<div class="col">' + "".join(
            f'<div class="row"><span class="pl" style="background:{COLOUR[r["col"]]}">{r["no"]}</span>'
            f'<span class="nm">{esc(r["name"])}</span><span class="ct">{f(r["n"])}</span></div>'
            for r in order[i * per:(i + 1) * per]) + "</div>"
        for i in range(3))
    legend = "".join(f'<span class="lg"><i style="background:{COLOUR[k]}"></i>{t}</span>' for k, t in LEGEND)
    ti = by_code(runs, "TI")
    assert ti["col"] == "black"

    body = f"""
<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">
<defs>{defs}</defs>
{terrain_svg}
{''.join(paths)}{''.join(plates)}{''.join(tags)}
</svg>
<div class="head">
<div class="eyebrow">OpenCaseLaw &middot; Piste map of the week</div>
<div class="big">{f(week['added'])}<span> new decisions</span></div>
<div class="when">Week {week['week_no']} &middot; {a.day}&ndash;{b.day} {b.strftime('%B %Y')}</div>
<div class="how">Every run is a court. Its length: the court's new decisions this week.
Its colour: the week against a usual one.</div>
</div>
<div class="key">
<div class="legend">{legend}<span class="scale">Run length = decisions</span></div>
<div class="rule">Every run ends in the valley. Usual week = median of the previous eight weeks; blue: below three quarters of it, black: more than half above it.</div>
<div class="cols">{key_cols}</div>
</div>
<div class="foot"><span>opencaselaw.ch</span><span>{f(week['total'])} decisions, 1875 to date &middot; open data</span></div>
"""
    css = f"""
body{{background:#f5f2ea;font-family:'Barlow',Helvetica,Arial,sans-serif;color:#13233a}}
.casing{{fill:#fff;opacity:.9}}
.plate{{stroke:#fff;stroke-width:2.5}}
.no{{font-size:14px;font-weight:700;fill:#fff;text-anchor:middle;dominant-baseline:central}}
.tag{{fill:#fff;stroke:#1b2a3c;stroke-opacity:.25;stroke-width:1}}
.tagt{{font-size:15px;font-weight:600;fill:#13233a}}.tagn{{font-weight:800}}
.head{{position:absolute;left:64px;top:58px;width:700px}}
.eyebrow{{font-size:19px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#1d3c62}}
.big{{font-size:118px;font-weight:800;line-height:1;letter-spacing:-.02em;margin-top:14px}}
.big span{{font-size:46px;font-weight:600;letter-spacing:0;margin-left:14px}}
.when{{font-size:30px;font-weight:600;margin-top:10px}}
.how{{font-size:20px;line-height:1.3;margin-top:14px;max-width:560px;color:#1d3c62}}
.key{{position:absolute;left:64px;right:64px;top:{PANEL_Y + 22}px}}
.legend{{display:flex;gap:26px;align-items:center;font-size:18px;font-weight:600}}
.lg{{display:flex;align-items:center;gap:9px;white-space:nowrap}}
.lg i{{display:inline-block;width:36px;height:7px;border-radius:4px;box-shadow:0 0 0 2px #fff}}
.scale{{margin-left:auto;font-weight:500;font-size:15.5px;color:#4d5c70}}
.rule{{font-size:15.5px;color:#4d5c70;margin-top:8px;padding-bottom:10px;border-bottom:1px solid #cfc9bb}}
.cols{{display:flex;gap:22px;margin-top:10px}}
.col{{min-width:0}}.col:nth-child(1){{flex:1.5}}.col:nth-child(2){{flex:1.5}}.col:nth-child(3){{flex:.95}}
.row{{display:flex;align-items:center;gap:9px;height:25px;font-size:17px}}
.pl{{flex:none;width:24px;height:24px;border-radius:50%;color:#fff;font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center}}
.nm{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ct{{flex:none;font-weight:700;min-width:38px;text-align:right;font-variant-numeric:tabular-nums}}
.foot{{position:absolute;left:64px;right:64px;bottom:36px;display:flex;justify-content:space-between;font-size:21px;font-weight:600;color:#13233a;border-top:1px solid #cfc9bb;padding-top:12px}}
.foot span:last-child{{font-weight:500}}"""
    return page("family=Barlow:wght@400;500;600;700;800", css, body, lang)


def by_code(runs: list[dict], code: str) -> dict:
    return next(r for r in runs if r["code"] == code)
