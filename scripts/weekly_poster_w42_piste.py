"""Week 42 (9-15 October 2026), English: a piste map.

The week painted as a Pistenplan. Every court is a run: it ends in the valley and its
length is the court's new decisions this week (one scale for every run, arc length solved
numerically, so a curved run is still exactly its count). Run colour is the week against a
usual week (median of the previous eight weeks): blue below it, red about it, black well above
it. Ticino (55 against a usual 12) is the one black run. Names only in the key, except a white
tag beside the three longest runs.

The terrain is a real massif: the Weisshorn (4,506 m) and the Bishorn (4,153 m) seen from the
east side of the Mattertal, rendered from swisstopo's swissALTI3D elevation model (2 m, © swisstopo;
far terrain from Copernicus GLO-30) by scripts/weekly_poster_w42_terrain/render_dem.py (README there), which writes
    terrain.png        the panorama, 2160 x 1880 (poster 0..940 at 2x), sky transparent
    terrain_runs.npz   the skyline, the screen-space fall-line field and the two summits
The runs are fitted to that terrain here (fit_runs): each run is the tail of a streamline of the
fall-line field, so it descends along the real slope; the two longest runs (Geneva, Zurich) start
on the two real summits, the streamline straightened or swung just enough that its length equals
the run's; the others are seeded along the skyline in the poster's order and start wherever their
length puts them. Every run still ends on the valley line with its exact length.

Thresholds (difficulty): blue if n < usual - max(5, usual/4); black if n > usual +
max(5, usual/2); red otherwise. The federal runs are coloured by the federal total, the
only usual figure the harness carries for them.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import math
import random
from pathlib import Path

import numpy as np

import weekly_poster as wp
from weekly_poster_designs import AREAS_EN, esc, page

NAME = dict(AREAS_EN)
W, H = wp.W, wp.H

BASE_Y = 900          # the valley floor: every run ends here
K = 3.5               # run length in px per decision
LIFT = 34             # the skyline passes at least this far above a run's number plate
PANEL_Y = 960         # the printed margin with the key begins here (the valley floor runs 900..960)
SCENE_Y = 940         # the terrain render covers 0..940; the valley floor is composited below the valley line
MIN_GAP = 22          # px between neighbouring runs anywhere along their paths
PLATE_GAP = 32        # px between the centres of two number plates
TINY = 40             # between the short runs standing in the valleys

TERRAIN = wp.REPO / "scripts" / "weekly_poster_w42_terrain"      # rendered DEM assets + pipeline (README there)
if not (TERRAIN / "terrain.png").exists():                        # development copy
    TERRAIN = wp.REPO / "output" / "posters" / "w42" / "mountain_lab"
TERRAIN_NAME = "Weisshorn and Bishorn, Valais"
TERRAIN_CREDIT = "swissALTI3D &copy; swisstopo"

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


# ── fitting the runs to the real terrain ──────────────────────────────────────
# Left to right across the panorama: Romandie and the federal courts on the Weisshorn's south
# flank, Geneva on the summit, Ticino and the small federal runs in the bowl under the north ridge,
# Zurich on the Bishorn, the German-speaking cantons along the shoulder to its right.
ORDER = ["JU", "NE", "FR", "VD", "bger@VD", "bvger", "GE", "TI", "bger@LU", "fed", "bstger", "SO", "BL",
         "ZH", "BS", "BE", "AG", "SG", "ZG", "SZ", "GR", "UR",
         "VS", "LU", "OW", "NW", "SH", "TG", "AR", "AI", "GL"]      # the last row: usually silent cantons


class Field:
    """The rendered terrain as the runs see it: skyline row per column, unit fall-line vectors
    (screen space, downward), the two summits."""

    def __init__(self, meta):
        self.fx, self.fy = meta["fx"].astype(float), meta["fy"].astype(float)
        self.skyline = meta["skyline"].astype(float)
        self.summits = [tuple(map(float, s)) for s in meta["summits"]]

    def dir(self, x, y, straight, wiggle=0.0):
        """Unit step direction at (x, y): the fall line blended toward straight down by `straight`;
        `wiggle` adds a gentle lateral swing (the run following the gullies) when the fall line
        alone is too plumb for the run's length."""
        xi, yi = int(min(max(x, 0), W - 1)), int(min(max(y, 0), SCENE_Y - 1))
        dx, dy = self.fx[yi, xi], self.fy[yi, xi]
        dx = dx * (1 - straight) + wiggle * math.cos(y / 38)
        dy = dy * (1 - straight) + straight
        n = math.hypot(dx, dy) or 1
        return dx / n, dy / n

    def streamline(self, x0, y0, straight=0.0, step=2.0, wiggle=0.0):
        pts = [(x0, y0)]
        x, y = x0, y0
        while y < BASE_Y:
            dx, dy = self.dir(x, y, straight, wiggle)
            if dy < .25:                                    # never traverse flat: keep descending
                dy, dx = .25, math.copysign(math.sqrt(1 - .0625), dx)
            x, y = x + dx * step, y + dy * step
            pts.append((x, y))
        (xa, ya), (xb, yb) = pts[-2], pts[-1]              # land exactly on the valley line
        t = (BASE_Y - ya) / (yb - ya) if yb != ya else 1
        pts[-1] = (xa + (xb - xa) * t, BASE_Y)
        return pts


def arc(pts) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def tail(pts, length):
    """The last `length` of a polyline (resampled start point), or None if it is too short."""
    if arc(pts) < length - 1e-6:
        return None
    acc, out = 0.0, [pts[-1]]
    for i in range(len(pts) - 1, 0, -1):
        d = math.dist(pts[i], pts[i - 1])
        if acc + d >= length:
            t = (length - acc) / d
            out.append((pts[i][0] + (pts[i - 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i - 1][1] - pts[i][1]) * t))
            break
        out.append(pts[i - 1])
        acc += d
    return out[::-1]


def path_gap(a, b) -> float:
    A, B = np.array(a[::2]), np.array(b[::2])
    return float(np.hypot(A[:, None, 0] - B[None, :, 0], A[:, None, 1] - B[None, :, 1]).min())


def fit_runs(runs: list[dict], meta) -> list[dict]:
    field = Field(meta)
    by = {r["code"]: r for r in runs}
    s1, s2 = field.summits[:2]
    if s1[1] > s2[1]:            # the higher summit takes the longer run
        s1, s2 = s2, s1
    anchors = {"GE": s1, "ZH": s2}

    # 1. the summit runs: from LIFT below the summit, the streamline straightened until its length
    #    equals the run's; if even the pure fall line is too short, swung through the gullies instead
    for code, (sx, sy) in anchors.items():
        r = by[code]
        L = r["n"] * K
        y0 = sy + LIFT
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if arc(field.streamline(sx, y0, straight=mid)) > L:
                lo = mid
            else:
                hi = mid
        pts = field.streamline(sx, y0, straight=hi)
        if arc(pts) > L + 2:            # even plumb is too long: the run starts lower on the face
            pts = tail(pts, L)
        elif arc(pts) < L - 1:
            lo_w, hi_w = 0.0, 2.5
            for _ in range(30):
                mid = (lo_w + hi_w) / 2
                if arc(field.streamline(sx, y0, straight=0, wiggle=mid)) < L:
                    lo_w = mid
                else:
                    hi_w = mid
            pts = field.streamline(sx, y0, straight=0, wiggle=hi_w)
        r["path"] = pts

    # 2. the others, seeded along the skyline in poster order
    order = [c for c in ORDER if c in by]
    i_ge, i_zh = order.index("GE"), order.index("ZH")
    west, mid, east = order[:i_ge], order[i_ge + 1:i_zh], order[i_zh + 1:]
    gx, zx = anchors["GE"][0], anchors["ZH"][0]
    placed = {"GE": by["GE"]["path"], "ZH": by["ZH"]["path"]}

    def candidate(code, xx):
        L = by[code]["n"] * K
        if not 4 <= xx <= W - 5:
            return None
        y0 = field.skyline[int(xx)] + LIFT
        if y0 >= BASE_Y - 8:
            return None
        t = tail(field.streamline(xx, y0, straight=0.25), L)
        if t is None or not 12 <= t[0][0] <= W - 12:
            return None
        if any(math.dist(t[0], p[0]) < PLATE_GAP for p in placed.values()):
            return None
        return t if all(path_gap(t, p) >= MIN_GAP for p in placed.values()) else None

    def seed_run(code, x, side):
        """A run on the streamline from the skyline near column x, shifted along `side` until it
        fits and clears its neighbours; failing that, the nearest free slope anywhere."""
        for k in range(60):
            t = candidate(code, min(max(x + side * k * 3, 4), W - 5))
            if t:
                by[code]["path"] = placed[code] = t
                return t[0][0]
        for k in range(1, 200):
            for xx in (x - 3 * k, x + 3 * k):
                t = candidate(code, xx)
                if t:
                    by[code]["path"] = placed[code] = t
                    return t[0][0]
        raise RuntimeError(f"no room for run {code}")

    x = gx - 30                                   # west of Geneva: outward, each a pitch further left
    for code in reversed(west):
        x = seed_run(code, x, -1) - (TINY if by[code]["n"] < 10 else 30)
    x = zx + 26                                   # east of Zurich: rightward along the shoulder
    for code in east:
        x = seed_run(code, x, +1) + (TINY if by[code]["n"] < 10 else 28)
    x = gx + 26                                   # the bowl between the summits
    for code in mid:
        x = seed_run(code, x, +1) + (TINY if by[code]["n"] < 10 else 26)

    for r in runs:
        p = r["path"]
        r["top"] = p[0]
        r["face"] = -1 if p[-1][0] < p[0][0] else 1       # which way the run leans (for the tag side)
        assert abs(arc(p) - r["n"] * K) < 1.5, (r["code"], arc(p), r["n"] * K)
        assert abs(p[-1][1] - BASE_Y) < .5
        assert field.skyline[int(min(max(p[0][0], 0), W - 1))] <= p[0][1] - LIFT + .5, r["code"]   # plate under the skyline
    return runs


def _run_shape(r: dict) -> tuple[str, str]:
    """(casing, run) polygons along the fitted path: the run tapers from 5.5 px at the top to 9 px
    in the valley."""
    pts = r["path"][::2] + [r["path"][-1]] if len(r["path"]) > 4 else r["path"]
    n = len(pts) - 1

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


def _tag(r: dict, f) -> str:
    """A horizontal white tag beside the plate of one of the three longest runs."""
    tx, ty = r["top"]
    text = f'{r["name"]}  {f(r["n"])}'
    w = 8.6 * len(text) + 18
    side = -1 if r["face"] < 0 else 1
    x = tx + side * 24 + (-w if side < 0 else 0)
    x = min(max(x, 8), W - w - 8)
    y = ty - 19 - 13
    return (f'<rect class="tag" x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="26" rx="3"/>'
            f'<text class="tagt" x="{x + 9:.0f}" y="{y + 17.5:.0f}">{esc(r["name"])}'
            f'<tspan class="tagn"> {f(r["n"])}</tspan></text>')


def _conifer(x, y, h, col) -> str:
    return (f'<path fill="{col}" d="M{x:.1f},{y - h:.1f}l{h * .34:.1f},{h * .62:.1f}h{-h * .68:.1f}z'
            f'M{x:.1f},{y - h * .62:.1f}l{h * .42:.1f},{h * .62:.1f}h{-h * .84:.1f}z"/>')


def _valley(runs: list[dict], rng: random.Random) -> str:
    """The valley floor under the mountain foot: fog where the slopes meet the floor, a gently
    undulating snow plain in soft light, tree groups at the foot (never on a run's finish), the
    floor fading into the paper tone of the key panel over its last 30 px."""
    ends = [r["path"][-1][0] for r in runs]
    clear = lambda x, margin=18: all(abs(x - e) > margin for e in ends)  # noqa: E731
    out = []
    # the floor: top edge undulating a few px around the valley line, hidden in the fog
    edge = [(x, BASE_Y - 3 + 3.5 * math.sin(x / 61 + .7) + 2 * math.sin(x / 23)) for x in range(-10, W + 20, 10)]
    out.append(f'<polygon fill="url(#floor)" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in edge)} {W + 20},{PANEL_Y + 2} -10,{PANEL_Y + 2}"/>')
    # soft modelling on the plain: long, faint cool hollows and a lit crest
    for cx, cy, rx, op in [(190, 926, 170, .22), (560, 934, 220, .18), (900, 924, 180, .2), (1040, 940, 120, .15)]:
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="5" fill="#b9cadf" opacity="{op}" filter="url(#fog)"/>')
    out.append(f'<ellipse cx="420" cy="914" rx="260" ry="4" fill="#ffffff" opacity=".5" filter="url(#fog)"/>')
    # fog where the slopes meet the floor
    out.append(f'<rect x="0" y="{BASE_Y - 34}" width="{W}" height="52" fill="url(#fogg)"/>')
    # a soft shadow where the slope meets the plain, below the fog
    out.append(f'<rect x="0" y="{BASE_Y + 4}" width="{W}" height="26" fill="url(#footsh)"/>')
    # tree groups at the foot, standing in the fog edge, clear of every finish
    for _ in range(70):
        gx = rng.uniform(10, W - 10)
        if not clear(gx, 24):
            continue
        n = rng.randint(3, 9)
        for _ in range(n):
            tx = gx + rng.uniform(-13, 13)
            ty = BASE_Y + rng.uniform(-7, 8)
            if clear(tx):
                out.append(_conifer(tx, ty, rng.uniform(6, 11), rng.choice(["#2f5a43", "#3a6a4e", "#264a38"])))
    # a thin scatter further out on the plain, fading with the paper
    for _ in range(60):
        tx, ty = rng.uniform(0, W), rng.uniform(BASE_Y + 12, PANEL_Y - 14)
        op = 1 - max(0, (ty - (PANEL_Y - 40)) / 30)
        if op > .1:
            out.append(_conifer(tx, ty, rng.uniform(5, 8.5), rng.choice(["#3a6a4e", "#46765a", "#2f5a43"])).replace('<path ', f'<path opacity="{op:.2f}" '))
    return "".join(out)


def _terrain_uri() -> str:
    png = (TERRAIN / "terrain.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def design(week: dict, lang: str = "en") -> str:
    f = lambda n: wp.fmt(n, lang)  # noqa: E731
    a, b = week["from"], week["to"]
    meta = np.load(TERRAIN / "terrain_runs.npz")
    runs = fit_runs(_runs(week), meta)

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
<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
<defs>
<linearGradient id="skyg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7f9fc9"/><stop offset=".4" stop-color="#a9c2dd"/><stop offset=".62" stop-color="#d2dfeb"/><stop offset=".75" stop-color="#e6edf2"/></linearGradient>
<linearGradient id="floor" gradientUnits="userSpaceOnUse" x1="0" y1="{BASE_Y - 6}" x2="0" y2="{PANEL_Y}"><stop offset="0" stop-color="#e3eaf1"/><stop offset=".45" stop-color="#ebeff0"/><stop offset="1" stop-color="#f5f2ea"/></linearGradient>
<linearGradient id="fogg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#eef3f8" stop-opacity="0"/><stop offset=".55" stop-color="#eef3f8" stop-opacity=".7"/><stop offset="1" stop-color="#eef3f8" stop-opacity="0"/></linearGradient>
<linearGradient id="footsh" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9fb4cf" stop-opacity=".28"/><stop offset="1" stop-color="#9fb4cf" stop-opacity="0"/></linearGradient>
<filter id="cloud" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="7"/></filter>
<filter id="fog" x="-20%" y="-300%" width="140%" height="700%"><feGaussianBlur stdDeviation="4"/></filter>
</defs>
<rect width="{W}" height="{SCENE_Y}" fill="url(#skyg)"/>
<ellipse cx="300" cy="372" rx="170" ry="4" fill="#fff" opacity=".28" filter="url(#cloud)"/>
<ellipse cx="900" cy="396" rx="200" ry="4" fill="#fff" opacity=".24" filter="url(#cloud)"/>
<image x="0" y="0" width="{W}" height="{SCENE_Y}" preserveAspectRatio="none" xlink:href="{_terrain_uri()}"/>
{_valley(runs, random.Random(42))}
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
<div class="rule">Every run ends in the valley. Usual week = median of the previous eight weeks; blue: below three quarters of it, black: more than half above it.
<span class="terrain">Terrain: {TERRAIN_NAME}, {TERRAIN_CREDIT}.</span></div>
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
.terrain{{display:block;margin-top:3px;font-size:13.5px;color:#6b7788}}
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
