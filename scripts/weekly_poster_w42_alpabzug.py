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

The silhouettes were developed in output/posters/w42/cow_lab/ (cowlab.py, NOTES.md with the research notes).
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


# ── the cow ──────────────────────────────────────────────────────────────────────────────────────
# Local coordinates: facing right, hooves on y=0, tail head at x≈8, nose at x≈160 (COW_L), withers y≈-102.
COW_L = 160
COW_BELL = (112, -64)   # where the Treichel hangs: the chest, under the strap

# Barrel + neck, clockwise from the pin bone; the neck ends in a rounded stump under the head.
BODY = [
    (10, -89, .6), (16, -97), (28, -100), (56, -101), (86, -102),          # pin bone, hooks, straight topline, withers
    (102, -102), (113, -99), (121, -102), (126, -112, .5), (130, -100), (129, -86, .5),  # neck dips before the skull, poll, throat
    (124, -80), (120, -74, .6), (117, -69), (113, -62, .6), (108, -56),    # neck underline with the dewlap folds, brisket
    (98, -52), (80, -47), (62, -44), (46, -47), (36, -54),                 # elbow, low belly curve, flank
    (34, -70), (24, -84), (14, -90, .5),
]
# Head, level pose, pivot at the poll (127, -114). Clockwise from the poll; the back runs into the neck.
HEAD = [
    (125, -116, .6), (136, -114), (144, -108, .7), (150, -101), (155, -95, .8),   # broad heavy poll and forehead, brow, nasal line
    (160, -88, .9), (161, -80, .9), (157, -72, .7), (150, -70), (142, -73),       # big rounded nose, deep blunt muzzle, chin, cheek
    (135, -79), (130, -86, .5), (125, -90), (121, -100), (121, -110),             # deep throat under the jaw, into the neck, skull back
]
EAR = [(128, -105, .4), (122, -106.5), (116, -107.5), (111, -108, .3), (113, -104.5), (120, -102.5), (127, -101, .4)]
HORN = [(122, -114, .5), (118.5, -121), (120, -127.5), (125.5, -131, .5), (127, -125), (128.5, -119), (130.5, -113, .5)]
HORN_FAR = shift(HORN, -4, 2.5)
# Legs, finer than before (the originals' legs are thin): near hind, clockwise from the pin bone.
HIND = [
    (10, -88, .6), (4, -72), (4, -58), (7, -46), (11, -35, .4),
    (14.5, -22), (16.5, -10), (15.5, -6, .5), (15.5, -1, 0), (25, 0, 0), (26, -5, .3),
    (22.5, -9, .5), (21.5, -20), (21.5, -30, .5), (28, -40), (38, -50, .5), (43, -62), (42, -76), (34, -90), (20, -96),
]
FRONT = [
    (91, -78), (90, -60), (92.5, -46), (94.5, -38, .5), (95.5, -24), (96.5, -10), (95.5, -6, .5), (95.5, -1, 0),
    (104.5, 0, 0), (104.5, -5, .3), (102.5, -9, .5), (102.5, -22), (105.5, -36, .5), (110, -48), (114, -60), (113, -72), (104, -80),
]
UDDER = [(36, -54), (33, -44), (35, -36, .5), (44, -33, .5), (53, -36, .5), (55, -46), (50, -53)]
TAIL = [(10, -90), (1, -78), (-3, -60), (-3, -42), (0, -30)]
TUFT = [(0, -32, .5), (-5, -22), (-4, -12), (1, -6, .3), (5, -14), (4, -25)]


def leg(pts, angle, px, py, cleft_x) -> str:
    """A leg with a fine cut at the toe (the cloven hoof)."""
    return rot(path(pts) + f'<path d="M{cleft_x},0 L{cleft_x},-4" stroke="{PAPER}" stroke-width=".9"/>', angle, px, py)


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
    """The cow silhouette in local coordinates. pose: leg phase 0..2; head: 0 lowered, 1 level, 2 raised."""
    ph = [(-9, 10, 12, -10), (10, -9, -12, 11), (1, 3, -8, 8)][pose % 3]   # near hind, near front, far hind, far front
    out = leg(shift(HIND, -5), ph[2], 18, -90, 17) + leg(shift(FRONT, -5), ph[3], 97, -74, 97)   # far pair
    out += (f'<path d="{smooth(TAIL, closed=False)}" fill="none" stroke="{BLACK}" stroke-width="2.8" stroke-linecap="round"/>'
            + path(TUFT))
    out += path(BODY)
    out += path(UDDER) + f'<path d="M39,-35 L39,-29 M47,-34 L47,-28" stroke="{BLACK}" stroke-width="2.6" stroke-linecap="round"/>'
    out += leg(HIND, ph[0], 22, -90, 22) + leg(FRONT, ph[1], 100, -74, 101)                      # near pair
    ang = [12, 0, -6][head % 3]
    hd = (path(HORN_FAR) + f'<path d="{smooth(HORN)}" fill="none" stroke="{PAPER}" stroke-width="1.6"/>'   # slit between the horns
          + path(HORN) + path(EAR) + path(HEAD))
    hd += f'<circle cx="141" cy="-100" r="2.2" fill="{PAPER}"/>'
    hd += f'<path d="M159.5,-76 C156,-75 153.5,-74.5 150,-75" fill="none" stroke="{PAPER}" stroke-width="1.1" stroke-linecap="round"/>'
    if lead:
        hd += headdress()
    out += rot(hd, ang, 127, -114)
    # the strap: a light band cut round the base of the neck, with black diamonds (hearts for the lead cow)
    w = 11 if lead else 7
    out += f'<path d="M102,-100 C106,-89 110,-77 113,-64" fill="none" stroke="{PAPER}" stroke-width="{w}"/>'
    pts = [(104, -92), (106.5, -84), (109.5, -76), (112, -68)]
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
# Local: facing right, hooves on y=0, rump at x≈8, nose at x≈100 (GOAT_L), withers y≈-62, horn tips ≈ -104.
GOAT_L = 100
GOAT_BELL = (66, -46)   # the round bell hangs under the throat, at the base of the neck

GBODY = [
    (10, -58, .6), (18, -62), (34, -63), (48, -62), (60, -64),            # rump, back, withers
    (70, -70), (80, -78), (85, -82, .5), (88, -72), (87, -62, .5),         # neck rising to the poll, throat
    (80, -56), (72, -50), (64, -45), (54, -40), (40, -36), (28, -39),      # neck underline, chest, belly
    (20, -46), (12, -52),
]
GHEAD = [
    (84, -84, .6), (92, -82), (98, -75, .7), (102, -67, .8), (101, -61, .7), (97, -57, .6),   # poll, forehead, nose, muzzle, chin
    (92, -56), (86, -60), (83, -68), (82, -78),                                                # jaw, throat, into the neck
]
GBEARD = [(96, -57, .5), (93, -50), (91, -44, .2), (90, -50), (89, -56)]
GEAR = [(84, -80, .4), (78, -83), (72, -86), (69, -87, .3), (72, -82), (78, -79), (83, -77, .4)]
GHORN = [(83, -85, .5), (78, -92), (71, -100), (66, -104, .3), (72, -97), (80, -88), (90, -83, .5)]
GHIND = [
    (10, -56, .6), (6, -44), (6, -32), (8, -22, .5), (10, -10), (9, -1, 0), (16, 0, 0), (16.5, -5, .3),
    (16, -12), (16, -22, .5), (20, -32), (26, -40, .5), (28, -50), (22, -58),
]
GFRONT = [
    (56, -52), (54, -40), (54, -28, .5), (54.5, -12), (53.5, -1, 0), (60, 0, 0), (60.5, -5, .3), (60.5, -14),
    (61, -28, .5), (63, -40), (66, -50), (62, -56),
]
GTAIL = [(10, -60, .5), (5, -70, .3), (8, -72, .3), (14, -63, .5)]
GUDDER = [(27, -40), (25, -34, .6), (29, -30, .6), (35, -31, .6), (38, -36)]


def goat_local(pose: int) -> str:
    """The goat silhouette: slim legs, beard, backward-curving horns (two, with a paper slit), short upright tail."""
    ph = [(-10, 10, 12, -10), (10, -10, -12, 10), (2, 2, -8, 8)][pose % 3]
    out = rot(path(shift(GHIND, -4)), ph[2], 14, -56, ) + rot(path(shift(GFRONT, -4)), ph[3], 58, -52)
    out += path(GTAIL) + path(GBODY) + path(GUDDER)
    out += rot(path(GHIND), ph[0], 18, -56) + rot(path(GFRONT), ph[1], 60, -52)
    ang = [8, 0, -8][pose % 3]
    hd = (path(shift(GHORN, -3, 2)) + f'<path d="{smooth(GHORN)}" fill="none" stroke="{PAPER}" stroke-width="1.3"/>'
          + path(GHORN) + path(GEAR) + path(GHEAD) + path(GBEARD))
    hd += f'<circle cx="91" cy="-75" r="1.6" fill="{PAPER}"/>'
    hd += f'<path d="M101,-62 C98.5,-61.5 96.5,-61.5 94,-62" fill="none" stroke="{PAPER}" stroke-width=".9" stroke-linecap="round"/>'
    out += rot(hd, ang, 84, -82)
    out += f'<path d="M62,-66 C64,-60 65.5,-53 66,-46" fill="none" stroke="{PAPER}" stroke-width="3.6"/>'   # the thin strap
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
    """A small herding dog trotting, ears up, tail curled over the back."""
    out = path([(4, -17, .6), (9, -22), (22, -23), (31, -21), (35, -17, .5), (33, -12), (26, -10), (12, -10), (6, -12)])   # body
    out += path([(31, -23, .5), (37, -27), (45, -24, .4), (46, -21, .3), (42, -18, .5), (36, -16), (32, -17)])          # head, pointed muzzle
    out += path([(35, -27, .3), (34, -34, .2), (38, -28, .3)]) + path([(38, -27, .3), (39, -34, .2), (42, -27, .3)])   # ears up
    out += f'<path d="M6,-20 C2,-26 1,-32 6,-34 C9,-35 10,-31 7,-29" fill="none" stroke="{BLACK}" stroke-width="2.2" stroke-linecap="round"/>'  # tail
    for x0, a in ((9, -12), (13, 10), (26, 10), (30, -11)):   # legs, thin
        out += rot(path([(x0 - 1.6, -12), (x0 + 1.6, -12), (x0 + 1.4, -1, .3), (x0 + 2.4, 0, 0), (x0 - 2.4, 0, 0), (x0 - 1.4, -1, .3)]), a, x0, -12)
    out += f'<circle cx="38.5" cy="-23.5" r="1" fill="{PAPER}"/>'
    return out


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
    left, right = 110, 970
    grounds = [728, 938, 1148]                       # the three registers, far to near
    k0 = len(cows) - 7                               # the top register: the herdsman and the smaller cows
    rows = [file_[:k0 + 1], file_[k0 + 1:k0 + 8], file_[k0 + 8:]]
    flips = [False, True, False]                     # walking direction per register: →, ←, →
    out, bells, labels = [], [], []
    for r, (ground, row, flip) in enumerate(zip(grounds, rows, flips)):
        k = len(row)
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
                x = cx - 52 * s
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
