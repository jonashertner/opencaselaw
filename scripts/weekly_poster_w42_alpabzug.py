#!/usr/bin/env python3
"""Week 42 poster, Romansh edition: the week's courts come down from the alp, as a Scherenschnitt.

A procession in the Pays-d'Enhaut paper-cut tradition (Hauswirth, Saugy): black silhouettes on a cream ground,
walking in registers on black scalloped ground bands, inside a lace border; cut mountains, firs and a chalet behind.

The data: one animal per court with new decisions in the window (CH split into the federal courts by seat).
Courts with >= 10 new decisions are cows, courts with fewer are goats — in the real Alpabzug the goats walk at the
front. Order from the back (top left) to the front (bottom right): the closing herdsman with his dog, the cows
ascending by count, the lead cow (the biggest count) with the Alpkönigin's headdress, then the goats ascending,
then the Senn leading. Every animal that stands for a court carries its label (code + number) and a bell.

Bell rule, the same for both species: h = 9 + 36 * sqrt(n / top), where top is the biggest count. A cow's Treichel
is h tall (its cut area is ~0.62 h^2); a goat's round bell has the same area for the same n (diameter 0.89 h). So the
bell area is the same affine function of the count across the whole procession. The animal itself grows a little
with the count too. The people, the dog, the chalet and the trees carry no data.

Nothing about removed rows, nights or builds is shown; the counts are the harness's by_canton / by_court figures
and must add up to week["added"].

The silhouettes are traced from public-domain references (output/posters/w42/cow_lab/ref/SOURCES.md); the
development record is in cow_lab/ (NOTES.md with the research notes, ref_trace.py, embed_pieces.py).
"""
from __future__ import annotations

import math

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = wp.W, wp.H

MONTH_RM = ["schaner", "favrer", "mars", "avrigl", "matg", "zercladur", "fanadur", "avust", "settember",
            "october", "november", "december"]

T = {
    "kicker": "OpenCaseLaw",
    "title": "Davent da l’alp",
    "headline": "Emna {w} · dals {a} fin ils {b} {m} {y}",
    "big": "novas decisiuns",
    "explain": "Mintga vatga u chaura è in chantun u ina dretgira federala. La grondezza da sia s-chella: sias novas decisiuns questa emna.",
    "total": "{t} decisiuns dapi il 1875",
}

# Federal side, split by court (and the Federal Supreme Court by seat); the rest of CH is "autras".
FEDERAL = [("bger@VD", "TF Losanna"), ("bvger", "TAF"), ("bger@LU", "TF Lucerna"), ("bstger", "TPF")]
OTHERS = "autras"
GOAT_BELOW = 10   # courts with fewer new decisions walk as goats

# palette: black paper on cream
PAPER, MEADOW = "#f3eee2", "#e9e8d4"
BLACK = "#141210"
INK, INK_SOFT = "#141210", "#4a4540"


def herd(week: dict) -> list[tuple[str, int]]:
    """[(label, n)] sorted biggest first; the federal side split by court, cantons by code."""
    by_canton, by_court = week["by_canton"], week["by_court"]
    out = [(code, n) for code, n in by_canton.items() if code != wp.FEDERAL and n > 0]
    fed = 0
    for key, label in FEDERAL:
        n = by_court.get(key, 0)
        if n > 0:
            out.append((label, n))
        fed += n
    rest = by_canton.get(wp.FEDERAL, 0) - fed
    if rest > 0:
        out.append((OTHERS, rest))
    out.sort(key=lambda c: (-c[1], c[0]))
    assert sum(n for _, n in out) == week["added"], "the herd must add up to the week"
    return out


def bell_height(n: int, top: int) -> float:
    return 9 + 36 * math.sqrt(n / top)


# ── paper-cut geometry ───────────────────────────────────────────────────────────────────────────

def smooth(pts, closed=True, k=1 / 6) -> str:
    """Catmull-Rom through pts -> cubic bezier path. A point is (x, y) or (x, y, w) where w scales the
    tangent (0 = sharp corner, 1 = default smoothness)."""
    P = [(p[0], p[1], p[2] if len(p) > 2 else 1.0) for p in pts]
    n = len(P)
    d = [f"M{P[0][0]:.1f},{P[0][1]:.1f}"]
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = P[(i - 1) % n] if closed or i > 0 else P[i]
        p1 = P[i]
        p2 = P[(i + 1) % n]
        p3 = P[(i + 2) % n] if closed or i + 2 < n else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * k * p1[2], p1[1] + (p2[1] - p0[1]) * k * p1[2])
        c2 = (p2[0] - (p3[0] - p1[0]) * k * p2[2], p2[1] - (p3[1] - p1[1]) * k * p2[2])
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    if closed:
        d.append("Z")
    return " ".join(d)


def path(pts, fill=BLACK, closed=True) -> str:
    return f'<path d="{smooth(pts, closed)}" fill="{fill}"/>'


def rot(inner: str, angle: float, px: float, py: float) -> str:
    return f'<g transform="rotate({angle:.1f} {px} {py})">{inner}</g>' if angle else inner


def shift(pts, dx, dy=0):
    return [(p[0] + dx, p[1] + dy) + tuple(p[2:]) for p in pts]


def place(inner: str, x: float, ground: float, s: float, flip: bool, length: float) -> str:
    """Put a local drawing (facing right, feet on y=0, spanning x 0..length) at x with its feet on `ground`,
    scaled by s, mirrored to face left when `flip`."""
    tf = f"translate({x:.1f} {ground:.1f}) scale({-s if flip else s:.3f} {s:.3f})" + (f" translate(-{length} 0)" if flip else "")
    return f'<g transform="{tf}">{inner}</g>'


def anchor(x: float, ground: float, s: float, flip: bool, length: float, lx: float, ly: float) -> tuple[float, float]:
    """Poster coordinates of a local point under the same placement."""
    return (x + ((length - lx) if flip else lx) * s, ground + ly * s)


# ── traced silhouettes ───────────────────────────────────────────────────────────────────────────
# The animals are cut from real silhouettes rather than drawn freehand: public-domain references from Wikimedia
# Commons, rasterised, traced and simplified (output/posters/w42/cow_lab/ref_trace.py; sources and licences in
# cow_lab/ref/SOURCES.md, all PD, no credit due). Cow: "Cowicon.svg" (Abu badali, PD, after the Swedish road-sign
# pictogram), its tail erased and recut standing off the body. Goat: "Goat (NIH BioArt 636 - 663087).svg"
# (Alexander Stewart / NIAID, PD). Dog: "Dog Silhouette 01.svg" (Amada44, PD).
# Local units: facing right, feet on y = 0. Each animal is the body cut at the belly line plus one piece per leg
# (points, pivot, foot); a leg piece reaches 8 % of the height up into the body and turns about its pivot there,
# so the legs can be set in walking phases without opening the outline. Douglas-Peucker at 0.4 % of the body length
# keeps the edges as clean scissor cuts. Regenerate with cow_lab/embed_pieces.py.
COW = dict(
    body="160.0,-68.9 159.1,-65.1 155.8,-62.3 146.6,-62.6 141.5,-63.8 128.8,-59.9 125.6,-55.7 118.8,-41.3 115.7,-38.5 111.9,-36.9 112.3,-36.3 83.7,-36.3 84.2,-37.5 76.1,-37.7 57.5,-36.3 46.2,-38.2 43.2,-36.3 4.2,-36.3 6.3,-43.5 4.9,-67.8 4.2,-64.1 0.1,-64.1 0.0,-65.2 0.5,-74.9 3.0,-80.9 9.0,-86.5 16.5,-89.3 25.0,-89.7 60.4,-86.3 94.9,-88.9 119.7,-88.2 129.9,-91.3 130.8,-91.9 130.7,-94.5 136.1,-96.4 139.1,-99.7 139.6,-98.6 138.2,-95.0 144.3,-91.7 149.6,-95.5 146.2,-87.6 149.8,-84.7 150.9,-81.0",
    legs=[
        ("18.0,-36.2 18.0,-30.8 11.5,-22.6 8.7,-12.2 8.9,-8.3 13.2,-0.1 4.5,-0.1 3.4,-2.2 4.4,-6.0 2.0,-7.5 4.6,-23.3 3.4,-34.4 6.3,-44.2 12.4,-44.2 12.4,-36.3",
         (9.4, -44.3), (8.9, -0.1)),
        ("52.2,-0.1 43.4,0.0 40.6,-4.9 38.3,-4.6 31.8,-14.1 23.5,-20.5 19.9,-32.3 19.2,-32.9 18.1,-31.0 18.1,-36.2 22.6,-36.3 22.6,-44.2 32.5,-44.2 32.6,-36.2 43.0,-36.2 42.1,-35.7 42.8,-31.6 41.7,-30.2 40.2,-30.6 38.2,-34.5 36.6,-34.1 36.8,-29.5 34.6,-29.0 33.1,-33.6 31.4,-33.6 31.6,-26.5 37.3,-15.5 46.0,-7.5 48.0,-4.1",
         (27.6, -44.3), (47.8, 0.0)),
        ("100.3,-36.2 100.3,-31.6 95.6,-35.7 82.5,-12.8 81.5,-6.9 86.3,-2.3 87.1,0.0 78.3,0.0 77.2,-2.4 77.9,-5.3 75.6,-6.4 81.2,-29.1 84.3,-37.4 80.1,-37.6 80.1,-44.2 88.8,-44.2 88.9,-36.2",
         (84.4, -44.3), (82.7, 0.0)),
        ("121.3,-25.9 118.8,-20.1 107.9,-8.4 102.6,-4.6 100.0,-1.2 96.5,-0.6 99.0,-9.1 102.0,-10.2 101.8,-13.1 107.9,-17.2 111.1,-23.4 109.3,-27.1 100.3,-31.5 100.3,-36.2 111.0,-36.2 111.1,-44.2 120.0,-44.2 117.0,-39.4 111.8,-36.8 118.9,-30.0",
         (115.6, -44.3), (97.4, -0.5)),
    ],
)   # H=99.7, belly=-36.3

GOAT = dict(
    body="115.0,-64.7 114.7,-61.7 113.0,-59.2 110.8,-58.1 105.0,-59.2 104.3,-58.4 104.3,-56.1 103.5,-56.1 102.3,-60.8 101.0,-61.0 100.7,-62.1 97.5,-61.5 92.7,-54.6 85.9,-47.8 78.7,-37.0 73.8,-33.1 72.5,-29.4 61.2,-29.4 60.9,-30.4 55.3,-29.4 43.4,-29.4 35.2,-31.5 34.3,-31.4 33.9,-29.4 22.6,-29.4 21.5,-30.5 20.6,-30.5 20.3,-29.4 12.5,-29.4 12.0,-33.6 11.3,-33.6 11.1,-35.5 10.5,-35.5 12.1,-43.1 11.7,-51.9 9.7,-51.7 8.2,-53.2 0.6,-52.9 0.6,-54.8 0.0,-55.4 0.0,-57.0 2.2,-57.9 2.2,-58.7 9.5,-57.3 13.0,-57.3 21.3,-61.8 27.1,-63.2 35.7,-63.7 56.6,-61.9 64.7,-63.6 74.9,-61.7 75.9,-64.2 81.9,-67.0 93.2,-76.0 95.2,-75.1 95.6,-76.2 98.6,-77.0 99.0,-79.8 97.8,-83.4 95.5,-88.7 92.2,-93.7 92.2,-94.7 93.1,-94.7 96.9,-91.2 97.7,-91.2 98.1,-96.7 99.0,-96.7 100.7,-90.9 104.3,-84.9 106.3,-78.8 106.3,-77.9 101.8,-76.8 101.8,-75.9 105.1,-73.5 105.5,-71.2 106.2,-70.8 105.8,-67.4 104.6,-67.4 104.0,-65.3 106.0,-65.9 106.4,-63.9 109.0,-63.2 109.3,-62.0 112.8,-62.1 113.1,-63.4 112.1,-64.4 111.9,-66.1 110.4,-67.2 109.7,-71.0 110.6,-71.0",
    legs=[
        ("19.3,-29.4 19.0,-27.0 17.0,-28.1 15.6,-27.6 16.3,-24.8 17.4,-24.7 17.4,-23.8 15.2,-17.3 14.5,-12.8 16.8,-7.6 16.8,-5.4 13.1,-4.6 9.1,-5.5 11.0,-23.1 12.5,-28.5 12.0,-33.6 11.2,-33.9 10.7,-37.2 15.3,-37.2 15.3,-29.4",
         (13.0, -37.2), (13.6, -4.6)),
        ("33.9,-29.4 33.2,-29.2 32.0,-26.6 31.0,-26.4 28.8,-21.6 28.1,-12.3 31.2,-8.5 31.0,-6.6 28.9,-6.3 24.9,-6.4 23.0,-10.6 23.0,-23.0 25.8,-29.7 22.6,-28.8 23.0,-37.2 28.4,-37.2 28.4,-29.4",
         (25.7, -37.2), (27.8, -6.3)),
        ("73.5,-6.5 73.5,-4.7 72.0,-4.1 71.7,-2.1 67.9,0.0 65.2,-0.1 62.8,-5.9 61.4,-6.4 63.7,-16.1 61.7,-23.8 61.3,-29.2 63.4,-29.4 63.4,-37.2 71.0,-37.2 71.0,-29.4 72.5,-29.2 70.7,-11.5 71.2,-9.4",
         (67.2, -37.2), (66.6, 0.0)),
    ],
)   # H=96.7, belly=-29.4

DOG = dict(
    body="40.0,-29.6 39.6,-28.9 38.7,-28.2 37.9,-27.9 36.4,-27.8 36.6,-27.3 37.6,-26.7 37.5,-26.1 36.2,-26.2 34.0,-27.8 32.4,-27.0 32.0,-26.1 31.9,-25.2 30.7,-23.3 30.5,-20.5 29.9,-18.5 26.7,-18.5 28.1,-19.5 28.2,-20.0 27.9,-20.3 26.3,-19.1 26.3,-18.5 17.9,-18.5 14.9,-21.6 12.6,-22.3 11.3,-21.2 10.4,-22.6 9.9,-22.3 10.2,-20.6 9.5,-18.5 2.7,-18.5 1.7,-26.4 2.2,-28.5 3.3,-30.3 1.0,-33.2 0.4,-35.4 0.7,-37.4 1.9,-39.1 3.5,-40.1 5.5,-40.4 6.6,-40.0 7.3,-39.4 8.0,-38.4 8.2,-37.6 7.5,-36.5 5.7,-35.4 5.3,-35.6 5.2,-36.1 5.7,-37.1 6.3,-37.7 6.3,-38.1 5.6,-38.5 4.9,-38.6 4.1,-38.4 3.3,-37.8 2.6,-36.4 2.5,-35.1 3.2,-33.7 4.2,-32.7 5.1,-32.5 7.3,-33.2 8.8,-33.5 11.7,-33.4 15.1,-32.9 19.7,-31.5 20.8,-31.4 25.8,-31.7 26.8,-32.9 26.6,-34.2 27.2,-35.1 28.2,-35.6 29.8,-35.8 31.5,-36.9 33.5,-36.8 35.3,-36.1 37.2,-34.6 37.5,-34.1 37.7,-32.3 39.6,-30.3",
    legs=[
        ("4.4,-18.5 4.4,-14.1 2.9,-12.0 1.9,-8.9 1.9,-7.0 2.4,-4.0 2.2,-2.7 1.6,-2.5 1.3,-2.7 0.5,-3.7 0.2,-4.7 0.0,-12.1 1.7,-14.7 2.5,-17.0 2.7,-18.8 2.2,-21.8 4.0,-21.8 4.0,-18.5",
         (3.1, -21.8), (1.8, -2.5)),
        ("9.5,-18.5 8.2,-14.9 6.6,-11.9 6.0,-8.5 6.0,-5.7 6.5,-4.9 7.3,-4.4 7.5,-3.3 6.1,-3.0 5.2,-3.4 3.8,-10.7 5.3,-14.9 5.1,-15.0 4.4,-14.2 4.4,-18.5 4.9,-18.5 4.9,-21.8 7.6,-21.8 7.6,-18.5",
         (6.2, -21.8), (6.4, -3.0)),
        ("24.7,-18.5 24.7,-16.9 23.4,-15.6 22.5,-14.1 21.7,-10.0 21.5,-3.6 21.8,-2.5 23.3,-0.8 23.3,-0.3 22.8,0.0 20.4,-0.1 20.0,-0.5 19.7,-3.5 19.2,-5.0 19.6,-9.4 19.8,-14.4 19.4,-16.2 19.6,-17.1 18.0,-18.5 19.7,-18.5 19.7,-21.8 22.4,-21.8 22.4,-18.5",
         (21.1, -21.8), (22.3, 0.0)),
        ("36.1,-1.2 35.7,-0.4 34.8,-0.2 32.9,-2.0 31.8,-5.0 31.1,-5.5 31.0,-6.5 30.6,-7.4 25.5,-15.3 25.5,-17.3 25.2,-17.4 24.7,-16.9 24.7,-18.5 26.6,-18.5 28.2,-19.6 28.1,-20.1 27.8,-20.3 26.6,-19.4 26.6,-21.8 29.9,-21.8 29.9,-18.6 29.4,-17.1 29.4,-15.0 30.7,-11.4 33.0,-6.8 33.7,-4.3",
         (28.3, -21.8), (34.9, -0.2)),
    ],
)   # H=40.4, belly=-18.5

def cut(points: str, fill=BLACK) -> str:
    """A traced piece: a polygon with its corners softened by a hairline stroke of the same colour."""
    return f'<polygon points="{points}" fill="{fill}" stroke="{fill}" stroke-width=".8" stroke-linejoin="round"/>'


def legs(animal: dict, angles, clefts: bool = True) -> str:
    """The leg pieces, each turned about its pivot by its angle (degrees, clockwise = foot back), with a fine cut at
    the toe for the cloven hoof."""
    out = ""
    for (pts, (px, py), (fx, fy)), a in zip(animal["legs"], angles):
        piece = cut(pts) + (f'<path d="M{fx:.1f},{fy:.1f} L{fx:.1f},{fy - 3.5:.1f}" stroke="{PAPER}" stroke-width=".9"/>' if clefts else "")
        out += rot(piece, a, px, py)
    return out


# ── the cow ──────────────────────────────────────────────────────────────────────────────────────
# Local coordinates: facing right, hooves on y=0, rump at x≈0, nose at x=160 (COW_L), topline y≈-90, horn tip -100.
COW_L = 160
COW_BELL = (124, -56)   # where the Treichel hangs: the front of the chest, at the foot of the strap
# Walking phases, angles for the four pieces in COW["legs"] order: far hind, near hind, far front, near front.
# Diagonal pairs swing together; the reference pose is phase 0 (near hind forward, near front back).
COW_PHASES = [(0, 0, 0, 0), (-7, 8, 8, -7), (-12, 14, 12, -12)]
# The tail, cut as one tapered shape: it leaves the pin bone, hangs a hair off the rump and ends in a brush tuft.
TAIL = [(6, -88, .3), (0, -80), (-3, -66), (-4, -52), (-5.5, -40), (-6, -30), (-3.5, -22, .2),
        (-1, -30), (-1, -40), (-1, -52), (0, -66), (2.5, -80)]
HORN_FAR = "130.5,-91 132,-96 134,-99.5 136,-99.5 135.5,-95 136.5,-91"   # the second horn, behind the near one


def headdress() -> str:
    """The Alpkönigin's crown: a cross frame with fir sprigs and flowers, as cut shapes (local coords, level head)."""
    s = f'<g><path d="M131,-116 L131,-148 M116,-136 L146,-136" stroke="{BLACK}" stroke-width="2.6" stroke-linecap="round"/>'
    s += f'<path d="M131,-147 L125,-131 L128,-133 L131,-129 L134,-133 L137,-131 Z" fill="{BLACK}"/>'
    s += (f'<path d="M123,-136 L116,-124 L120,-126 L123,-122 L126,-126 L129,-125 Z '
          f'M139,-136 L133,-125 L136,-126 L139,-122 L142,-126 L146,-124 Z" fill="{BLACK}"/>')
    for cx, cy, r in ((131, -150, 5), (115, -136, 4.4), (147, -136, 4.4), (131, -136, 4)):
        s += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{BLACK}"/><circle cx="{cx}" cy="{cy}" r="{r * .3:.1f}" fill="{PAPER}"/>'
        for i in range(6):
            a = i * math.pi / 3
            s += f'<circle cx="{cx + math.cos(a) * r * .66:.1f}" cy="{cy + math.sin(a) * r * .66:.1f}" r="{r * .17:.1f}" fill="{PAPER}"/>'
    return s + "</g>"


def cow_local(pose: int, head: int, lead: bool) -> str:
    """The cow silhouette in local coordinates. pose: leg phase 0..2; head is kept for the call sites (the traced
    head is not articulated)."""
    out = legs(COW, COW_PHASES[pose % 3])
    out += path(TAIL)
    out += cut(HORN_FAR) + cut(COW["body"])
    # the cut details: eye and mouth
    out += f'<circle cx="143" cy="-82" r="2.2" fill="{PAPER}"/>'
    out += f'<path d="M158.5,-66 C156,-65.2 153.5,-65 151,-65.6" fill="none" stroke="{PAPER}" stroke-width="1.1" stroke-linecap="round"/>'
    if lead:
        out += f'<g transform="translate(16 23)">{headdress()}</g>'   # the frame stands on the poll at (143, -91)
    # the strap: a light band cut round the base of the neck, with black diamonds (hearts for the lead cow)
    w = 11 if lead else 7
    out += f'<path d="M112,-89 C116,-78 120,-67 124,-56" fill="none" stroke="{PAPER}" stroke-width="{w}"/>'
    pts = [(114, -81), (116.5, -73), (119.5, -65.5), (122, -59)]
    if lead:
        out += "".join(f'<path d="M{x},{y + 1.6} C{x - 4},{y - 2} {x - 1.6},{y - 5} {x},{y - 2.2} '
                       f'C{x + 1.6},{y - 5} {x + 4},{y - 2} {x},{y + 1.6} Z" fill="{BLACK}"/>' for x, y in pts)
    else:
        out += "".join(f'<path d="M{x},{y - 2.2} l2.2,2.2 -2.2,2.2 -2.2,-2.2 Z" fill="{BLACK}"/>' for x, y in pts)
    return out


def treichel(cx: float, top: float, h: float, strap_w: float) -> str:
    """A Treichel hanging from (cx, top): tall trapezoid with gently concave sides flaring to the mouth, a thick
    rounded lip, a crown loop where the strap passes through (with its buckle), the clapper a small drop below the
    lip. Cut as a light shape with a black edge, so it reads inside and outside the silhouette. Area ~0.62 h^2."""
    w = h
    body = (f"M{cx - .21 * w:.1f},{top + .1 * h:.1f} C{cx - .2 * w:.1f},{top + .45 * h:.1f} {cx - .44 * w:.1f},{top + .72 * h:.1f} "
            f"{cx - .5 * w:.1f},{top + .88 * h:.1f} L{cx + .5 * w:.1f},{top + .88 * h:.1f} C{cx + .44 * w:.1f},{top + .72 * h:.1f} "
            f"{cx + .2 * w:.1f},{top + .45 * h:.1f} {cx + .21 * w:.1f},{top + .1 * h:.1f} Z")
    edge = max(1.4, h * 0.07)
    lip_h = max(2.0, h * .13)
    r = max(1.2, h * .1)
    bw, bh = strap_w * 1.25, strap_w * .8
    return (f'<circle cx="{cx:.1f}" cy="{top + .04 * h:.1f}" r="{r:.1f}" fill="{PAPER}" stroke="{BLACK}" stroke-width="{max(1.0, h * .05):.1f}"/>'
            f'<path d="{body}" fill="{PAPER}" stroke="{BLACK}" stroke-width="{edge:.1f}" stroke-linejoin="round"/>'
            f'<rect x="{cx - .54 * w:.1f}" y="{top + .86 * h:.1f}" width="{1.08 * w:.1f}" height="{lip_h:.1f}" rx="{lip_h / 2:.1f}" fill="{BLACK}"/>'
            f'<path d="M{cx:.1f},{top + .86 * h + lip_h:.1f} c{-.07 * h:.1f},{.05 * h:.1f} {-.07 * h:.1f},{.13 * h:.1f} 0,{.15 * h:.1f} '
            f'c{.07 * h:.1f},{-.02 * h:.1f} {.07 * h:.1f},{-.1 * h:.1f} 0,{-.15 * h:.1f} Z" fill="{BLACK}"/>'
            f'<rect x="{cx - bw / 2:.1f}" y="{top - bh * .7:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{bh * .2:.1f}" fill="{BLACK}"/>'
            f'<rect x="{cx - bw / 2 + bw * .22:.1f}" y="{top - bh * .7 + bh * .3:.1f}" width="{bw * .56:.1f}" height="{bh * .4:.1f}" fill="{PAPER}"/>')


def round_bell(cx: float, top: float, h: float) -> str:
    """A goat's round bell (Rolle) hanging from (cx, top), with the same cut area as a Treichel of height h:
    diameter 0.89 h. A light disc with a black edge, a small loop on top, the sound slit cut black across the
    lower half, a tiny clapper tongue below."""
    d = .89 * h
    r = d / 2
    cy = top + r + .06 * h
    edge = max(1.3, h * .07)
    return (f'<circle cx="{cx:.1f}" cy="{top + .05 * h:.1f}" r="{max(1.0, .09 * h):.1f}" fill="{PAPER}" stroke="{BLACK}" stroke-width="{max(.9, h * .045):.1f}"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{PAPER}" stroke="{BLACK}" stroke-width="{edge:.1f}"/>'
            f'<path d="M{cx - r * .3:.1f},{cy + r * .35:.1f} L{cx + r * .3:.1f},{cy + r * .35:.1f}" stroke="{BLACK}" stroke-width="{max(.9, h * .045):.1f}" stroke-linecap="round"/>'
            f'<circle cx="{cx:.1f}" cy="{cy + r + .04 * h:.1f}" r="{max(1.0, h * .06):.1f}" fill="{BLACK}"/>')


# ── the goat ─────────────────────────────────────────────────────────────────────────────────────
# Local: facing right, hooves on y=0, tail at x≈0, nose at x=115 (GOAT_L), withers y≈-63, horn tips ≈ -97.
GOAT_L = 115
GOAT_BELL = (88, -49)   # the round bell hangs at the front of the neck, at its base
# Phases for the three pieces in GOAT["legs"] order: far hind, near hind, the front pair (which overlap in the
# reference and move as one).
GOAT_PHASES = [(0, 0, 0), (8, -8, 6), (-6, 8, -7)]
GBEARD = "102.5,-58.5 106.5,-57.5 104.5,-48.5"


def goat_local(pose: int) -> str:
    """The goat silhouette: slim legs, beard, long backward-curving horns, short upright tail."""
    out = legs(GOAT, GOAT_PHASES[pose % 3])
    out += cut(GBEARD) + cut(GOAT["body"])
    out += f'<circle cx="104" cy="-72" r="1.6" fill="{PAPER}"/>'
    out += f'<path d="M113.5,-61.5 C111.5,-61 109.5,-61 107.5,-61.5" fill="none" stroke="{PAPER}" stroke-width=".9" stroke-linecap="round"/>'
    out += f'<path d="M81,-63 C85,-58 87,-53 88,-49" fill="none" stroke="{PAPER}" stroke-width="3.6"/>'   # the thin strap
    return out


# ── the people and the dog ───────────────────────────────────────────────────────────────────────
# Local: facing right, feet on y=0, about 120 tall to the hat, spanning x -24..30 (shifted to start at 0 by place()).
MAN_L = 54


def man_local(kind: str) -> str:
    """A herdsman in Tracht: round hat, short jacket, knee breeches, stockings, shoes, a long staff. `kind` is
    'senn' (the leader, a pack basket on his back) or 'herd' (the closing man)."""
    o = 24  # x offset so the figure starts at local 0
    def P(pts):
        return path(shift(pts, o))
    out = ""
    # back leg (stocking + shoe), then breeches, front leg
    back = [(-3, -44), (-7.5, -30), (-9.5, -18, .6), (-9, -6, .5), (-14, -1.5, .3), (-14, 0, 0), (-3, 0, 0), (-3.5, -6, .5), (-3.5, -18, .6), (-2, -30), (1, -44)]
    front = [(7, -44), (9, -30), (10.5, -18, .6), (11.5, -6, .5), (11, -1.5, .3), (11, 0, 0), (21, 0, 0), (20, -1.5, .3), (17.5, -6, .5), (16.5, -18, .6), (16, -30), (13, -44)]
    out += rot(P(back), 9, o - 1, -44) + rot(P(front), -8, o + 10, -44)
    out += P([(-8, -68), (13, -68), (15, -56, .6), (15, -42, .5), (4, -39), (-9, -42, .5), (-10, -56, .6)])   # breeches to the knee
    out += P([(-8, -94, .5), (10, -94, .5), (13, -84), (12, -72), (14, -66), (-10, -66), (-11, -72), (-12, -84)])   # short jacket
    out += P([(-3, -96, .4), (5, -96, .4), (8, -104), (6, -111, .6), (-2, -112, .6), (-6, -105)])            # head on a short neck
    out += f'<rect x="{o - 13}" y="-114" width="26" height="3" rx="1.5" fill="{BLACK}"/>'                     # hat brim
    out += P([(-7, -114), (-6, -123, .6), (7, -123, .6), (8, -114)])                                        # hat crown
    # the staff and the arm holding it, hand at chest height
    out += f'<path d="M{o + 22},0 L{o + 22},-118" stroke="{BLACK}" stroke-width="2.2" stroke-linecap="round"/>'
    out += P([(9, -90), (14, -88), (24, -78, .5), (22, -72, .5), (12, -80), (8, -84)])
    if kind == "senn":   # a pack basket on his back, with two woven lines cut light
        out += P([(-26, -98, .5), (-10, -100, .5), (-8, -70, .5), (-26, -72, .5)])
        out += f'<path d="M{o - 24},-90 L{o - 11},-91 M{o - 24},-80 L{o - 11},-81" stroke="{PAPER}" stroke-width="1.2"/>'
        out += f'<path d="M{o - 10},-96 L{o - 2},-92 M{o - 10},-74 L{o - 4},-70" stroke="{BLACK}" stroke-width="2"/>'   # straps
    return out


DOG_L = 40


def dog_local() -> str:
    """The herding dog trotting beside the closing man, tail curled over the back."""
    return legs(DOG, (0, 0, 0, 0), clefts=False) + cut(DOG["body"]) + f'<circle cx="33" cy="-33" r="1" fill="{PAPER}"/>'


# ── the scene ────────────────────────────────────────────────────────────────────────────────────
MID_RIDGE = [(0, 560), (90, 515), (170, 548), (250, 495), (340, 540), (420, 512), (520, 556), (610, 508), (700, 546),
             (780, 520), (870, 556), (950, 522), (1080, 560)]


def mountains() -> str:
    """Two black cut ranges behind, separated by a fine paper line, snow as cut slits; in front, the alp itself:
    a cream hill with a black cut edge, with a paper band above its ridge on which the firs and the chalet stand."""
    back = ("M0,500 L60,452 L130,478 L200,436 L270,470 L350,420 L430,462 L500,432 L560,458 L640,402 L720,450 "
            "L800,418 L870,452 L950,412 L1020,446 L1080,420 L1080,640 L0,640 Z")
    far = ("M0,520 L70,480 L140,505 L215,445 L265,470 L320,460 L395,500 L470,448 L530,476 L590,428 L650,466 "
           "L715,452 L790,500 L860,454 L930,484 L1000,462 L1080,500 L1080,640 L0,640 Z")
    mid = "M" + " L".join(f"{x},{y}" for x, y in MID_RIDGE) + " L1080,660 L0,660 Z"
    snow = ("M350,420 L336,434 L346,431 L352,437 L361,432 L368,436 Z M640,402 L626,418 L636,414 L643,421 L652,415 L660,420 Z "
            "M950,412 L938,425 L947,422 L953,428 L961,423 L968,427 Z "
            "M215,445 L197,460 L208,457 L218,464 L228,459 L237,463 Z M590,428 L572,446 L585,442 L593,451 L603,444 L612,449 Z "
            "M860,454 L845,469 L856,465 L864,472 L873,466 L880,470 Z M470,448 L458,461 L467,458 L474,464 L484,460 Z")
    ridge = "M" + " L".join(f"{x},{y}" for x, y in MID_RIDGE)
    return (f'<path d="{back}" fill="{BLACK}"/>'
            f'<path d="{far}" fill="{BLACK}" stroke="{PAPER}" stroke-width="3" stroke-linejoin="round"/>'
            f'<path d="{snow}" fill="{PAPER}"/>'
            f'<path d="{ridge}" fill="none" stroke="{PAPER}" stroke-width="56" stroke-linejoin="round"/>'
            f'<path d="{mid}" fill="{MEADOW}" stroke="{BLACK}" stroke-width="3" stroke-linejoin="round"/>')


def ridge_y(x: float) -> float:
    for (x0, y0), (x1, y1) in zip(MID_RIDGE, MID_RIDGE[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return MID_RIDGE[-1][1]


def fir(x: float, base: float, h: float) -> str:
    """A cut fir: three tiers and a trunk."""
    w = h * .42
    return (f'<path d="M{x:.1f},{base - h:.1f} L{x + w * .45:.1f},{base - h * .62:.1f} L{x + w * .22:.1f},{base - h * .62:.1f} '
            f'L{x + w * .75:.1f},{base - h * .32:.1f} L{x + w * .4:.1f},{base - h * .32:.1f} L{x + w:.1f},{base - h * .06:.1f} '
            f'L{x + w * .12:.1f},{base - h * .06:.1f} L{x + w * .12:.1f},{base:.1f} L{x - w * .12:.1f},{base:.1f} L{x - w * .12:.1f},{base - h * .06:.1f} '
            f'L{x - w:.1f},{base - h * .06:.1f} L{x - w * .4:.1f},{base - h * .32:.1f} L{x - w * .75:.1f},{base - h * .32:.1f} '
            f'L{x - w * .22:.1f},{base - h * .62:.1f} L{x - w * .45:.1f},{base - h * .62:.1f} Z" fill="{BLACK}"/>')


def chalet(x: float, base: float, w: float) -> str:
    """A cut chalet on the ridge: wide low roof with eaves, a chimney, windows and a door cut light."""
    h = w * .5
    r = w * .28   # roof rise
    out = (f'<path d="M{x - w * .5:.1f},{base:.1f} L{x - w * .5:.1f},{base - h + r:.1f} L{x + w * .5:.1f},{base - h + r:.1f} '
           f'L{x + w * .5:.1f},{base:.1f} Z" fill="{BLACK}"/>'
           f'<path d="M{x - w * .58:.1f},{base - h + r + 2:.1f} L{x:.1f},{base - h - 2:.1f} L{x + w * .58:.1f},{base - h + r + 2:.1f} '
           f'L{x + w * .58:.1f},{base - h + r + 5:.1f} L{x - w * .58:.1f},{base - h + r + 5:.1f} Z" fill="{BLACK}"/>'
           f'<rect x="{x + w * .18:.1f}" y="{base - h - r * .1:.1f}" width="{w * .07:.1f}" height="{r * .6:.1f}" fill="{BLACK}"/>')
    ww, wh = w * .11, h * .3
    for k in (-1.5, -.5, .5, 1.5):
        out += f'<rect x="{x + k * w * .22 - ww / 2:.1f}" y="{base - h + r + 6:.1f}" width="{ww:.1f}" height="{wh:.1f}" fill="{PAPER}"/>'
    out += f'<rect x="{x - ww * .6:.1f}" y="{base - h * .42:.1f}" width="{ww * 1.2:.1f}" height="{h * .42:.1f}" fill="{PAPER}"/>'
    return out


def ridge_scenery() -> str:
    """Clusters of firs standing on the alp's ridge, and one chalet, with gaps."""
    xs = [14, 32, 48, 120, 138, 158, 176, 300, 318, 338, 460, 478, 578, 596, 740, 758, 780, 836, 950, 968, 988, 1050, 1068]
    out = []
    for i, x in enumerate(xs):
        h = 14 + 12 * abs(math.sin(i * 2.3 + .7))
        out.append(fir(x, ridge_y(x) + 1.5, h))
    out.append(chalet(668, ridge_y(668) + 1.5, 46))
    return "".join(out)


def ground_band(d: str) -> str:
    """The register's ground: a black band with a scalloped edge (beads along both edges), as the cut bands the
    processions walk on in the originals."""
    return (f'<path d="{d}" fill="none" stroke="{BLACK}" stroke-width="30" stroke-linecap="round" stroke-dasharray="0 11"/>'
            f'<path d="{d}" fill="none" stroke="{BLACK}" stroke-width="24" stroke-linecap="round"/>')


def border() -> str:
    """A lace border in the tradition: two fine lines, between them a running row of hearts and leaf sprigs,
    a rosette in each corner. Thin, so the headline keeps the room."""
    o1, o2 = 14, 44            # outer and inner line insets
    mid = (o1 + o2) / 2
    out = [f'<rect x="{o1}" y="{o1}" width="{W - 2 * o1}" height="{H - 2 * o1}" fill="none" stroke="{BLACK}" stroke-width="1.6"/>',
           f'<rect x="{o2}" y="{o2}" width="{W - 2 * o2}" height="{H - 2 * o2}" fill="none" stroke="{BLACK}" stroke-width="1.2"/>']

    def heart(cx, cy, r):
        return (f'<path d="M{cx:.1f},{cy + r:.1f} C{cx - 1.9 * r:.1f},{cy - .2 * r:.1f} {cx - .9 * r:.1f},{cy - 1.4 * r:.1f} {cx:.1f},{cy - .35 * r:.1f} '
                f'C{cx + .9 * r:.1f},{cy - 1.4 * r:.1f} {cx + 1.9 * r:.1f},{cy - .2 * r:.1f} {cx:.1f},{cy + r:.1f} Z" fill="{BLACK}"/>')

    def sprig(cx, cy, horizontal):
        # a short stem with three leaf pairs, drawn along the band
        s = f'<g transform="translate({cx:.1f} {cy:.1f}) rotate({0 if horizontal else 90})">'
        s += f'<path d="M-11,0 L11,0" stroke="{BLACK}" stroke-width="1.1"/>'
        for k in (-7, 0, 7):
            s += (f'<path d="M{k},0 C{k - 2},-3 {k + 1},-6 {k + 3},-6 C{k + 4},-3 {k + 2},-1 {k},0 Z" fill="{BLACK}"/>'
                  f'<path d="M{k},0 C{k - 2},3 {k + 1},6 {k + 3},6 C{k + 4},3 {k + 2},1 {k},0 Z" fill="{BLACK}"/>')
        return s + "</g>"

    def rosette(cx, cy):
        s = f'<circle cx="{cx}" cy="{cy}" r="9" fill="{BLACK}"/><circle cx="{cx}" cy="{cy}" r="2.4" fill="{PAPER}"/>'
        for i in range(8):
            a = i * math.pi / 4
            s += f'<circle cx="{cx + math.cos(a) * 5.6:.1f}" cy="{cy + math.sin(a) * 5.6:.1f}" r="1.6" fill="{PAPER}"/>'
        return s

    step = 44
    # top and bottom rows, symmetric about the centre
    for y in (mid, H - mid):
        for i, x in enumerate(range(int(mid) + 28, W - int(mid) - 20, step)):
            out.append(heart(x, y, 5.2) if i % 2 == 0 else sprig(x, y, True))
    for x in (mid, W - mid):
        for i, y in enumerate(range(int(mid) + 28, H - int(mid) - 20, step)):
            out.append(heart(x, y, 5.2) if i % 2 == 0 else sprig(x, y, False))
    for cx in (mid, W - mid):
        for cy in (mid, H - mid):
            out.append(rosette(cx, cy))
    return "".join(out)


def design(week: dict, lang: str = "rm") -> str:
    lang = "rm"  # the Romansh edition; the harness passes the language through
    a, b = week["from"], week["to"]
    animals = herd(week)
    top = max(n for _, n in animals)
    cows = [c for c in animals if c[1] >= GOAT_BELOW]     # biggest first
    goats = [c for c in animals if c[1] < GOAT_BELOW]
    # the file, back to front: closing herdsman + dog, cows ascending, lead cow, goats ascending, the Senn
    file_ = ([("herd", None, 0)] + [("cow", lab, n) for lab, n in cows[::-1]]
             + [("goat", lab, n) for lab, n in goats[::-1]] + [("senn", None, 0)])
    # Register extents (first and last animal centre). The middle register is drawn in a little at both ends: its
    # lead cow (the biggest, head and headdress towards the left border) and the AG cow's tail at the right must clear
    # the lace border by 12 px.
    spans = [(110, 952), (140, 952), (110, 970)]
    grounds = [728, 938, 1148]                       # the three registers, far to near
    k0 = len(cows) - 7                               # the top register: the herdsman and the smaller cows
    rows = [file_[:k0 + 1], file_[k0 + 1:k0 + 8], file_[k0 + 8:]]
    flips = [False, True, False]                     # walking direction per register: →, ←, →
    out, bells, labels = [], [], []
    for r, (ground, row, flip) in enumerate(zip(grounds, rows, flips)):
        k = len(row)
        left, right = spans[r]
        pitch = (right - left) / max(1, k - 1)
        for i, (kind, label, n) in enumerate(row):
            cx = (right - i * pitch) if flip else (left + i * pitch)   # the file reads on from the register before
            if kind == "cow":
                s = 0.76 + 0.16 * math.sqrt(n / top)
                lead = n == top
                x = cx - 82 * s
                out.append(place(cow_local((i + r) % 3, 2 if lead else (i * 2 + r) % 3, lead), x, ground, s, flip, COW_L))
                bx, by = anchor(x, ground, s, flip, COW_L, *COW_BELL)
                bells.append(treichel(bx, by, bell_height(n, top), (11 if lead else 7) * s))
            elif kind == "goat":
                s = 0.82 + 0.18 * math.sqrt(n / GOAT_BELOW)
                x = cx - 57 * s
                out.append(place(goat_local((i + r) % 3), x, ground, s, flip, GOAT_L))
                bx, by = anchor(x, ground, s, flip, GOAT_L, *GOAT_BELL)
                bells.append(round_bell(bx, by, bell_height(n, top)))
            else:
                x = cx - 27
                out.append(place(man_local(kind), x, ground, 1.0, flip, MAN_L))
                if kind == "herd":   # the dog trots a little ahead of the closing man
                    out.append(place(dog_local(), cx + (-62 if flip else 26), ground, 1.0, flip, DOG_L))
            if label is not None:
                labels.append(f'<text x="{cx:.1f}" y="{ground + 48}" class="num">{wp.fmt(n, lang)}</text>'
                              f'<text x="{cx:.1f}" y="{ground + 69}" class="code">{esc(label)}</text>')
    left, right = spans[0]
    road = (f"M-30,{grounds[0] - 118} C30,{grounds[0] - 70} 50,{grounds[0]} {left - 20},{grounds[0]} L{right + 20},{grounds[0]} "
            f"C{right + 110},{grounds[0]} {right + 110},{grounds[1]} {right + 20},{grounds[1]} L{left - 20},{grounds[1]} "
            f"C{left - 110},{grounds[1]} {left - 110},{grounds[2]} {left - 20},{grounds[2]} L{right + 20},{grounds[2]} "
            f"C{right + 60},{grounds[2]} {right + 90},{grounds[2] + 30} {W + 30},{grounds[2] + 70}")
    m = MONTH_RM[b.month - 1]
    month = ("d’" if m[0] in "aeiou" else "da ") + m
    body = f"""
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{PAPER}"/>
{mountains()}
<path d="M0,618 C200,600 420,630 620,608 C800,590 960,622 1080,604 L1080,1350 L0,1350 Z" fill="{MEADOW}"/>
{ridge_scenery()}
<g transform="translate(0 11)">{ground_band(road)}</g>
{''.join(out)}
{''.join(bells)}
<g text-anchor="middle">{''.join(labels)}</g>
<path d="M0,0 H{W} V{H} H0 Z M44,44 V{H - 44} H{W - 44} V44 Z" fill="{PAPER}" fill-rule="evenodd"/>
{border()}
</svg>
<div class="hd">
 <div class="k">{T["kicker"]}</div>
 <div class="t">{T["title"]}</div>
 <div class="h">{T["headline"].format(w=week["week_no"], a=a.day, b=b.day, m=month, y=b.year)}</div>
 <div class="n"><b>{wp.fmt(week["added"], lang)}</b> <span>{T["big"]}</span></div>
 <div class="x">{T["explain"]}</div>
</div>
<div class="ft"><b>opencaselaw.ch</b><span>{T["total"].format(t=wp.fmt(week["total"], lang))}</span></div>"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Inter',system-ui,sans-serif}}
.hd{{position:absolute;left:70px;right:70px;top:58px;text-align:center}}
.k{{font-size:15px;font-weight:600;letter-spacing:.24em;text-transform:uppercase;color:{INK_SOFT}}}
.t{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:58px;line-height:1.05;margin-top:8px;letter-spacing:-.01em}}
.h{{font-size:23px;font-weight:500;margin-top:10px;color:{INK_SOFT}}}
.n{{margin-top:2px;line-height:1}}
.n b{{font-family:'Fraunces',Georgia,serif;font-weight:700;font-size:150px;letter-spacing:-.04em;color:{INK}}}
.n span{{font-family:'Fraunces',Georgia,serif;font-weight:500;font-size:44px;letter-spacing:-.01em;margin-left:6px}}
.x{{font-family:'Fraunces',Georgia,serif;font-style:italic;font-size:19.5px;margin:8px auto 0;max-width:760px;text-wrap:balance;color:{INK_SOFT}}}
.num{{font-family:'Fraunces',Georgia,serif;font-weight:700;font-size:28px;fill:{INK}}}
.code{{font-size:13.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;fill:{INK}}}
.ft{{position:absolute;left:70px;right:70px;bottom:60px;display:flex;justify-content:space-between;align-items:baseline;font-size:18px;border-top:1px solid rgba(20,18,16,.4);padding-top:12px;color:{INK}}}
.ft b{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:26px}}"""
    return page("family=Fraunces:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Inter:wght@400;500;600;700", css, body, lang)
