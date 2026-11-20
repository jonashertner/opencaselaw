"""Week 47 (13 - 19 November 2026), Romansh: Flurs da naiv (snowflakes). v5, cinematic macro.

Jonas, via the coordinator: "be unreasonably ambitious and visually exceptionally radical."
One Bentley crystal -- a stellar dendrite, Metropolitan Museum of Art, fetched at its full
4000x2526 original (File:-Snow Crystal- MET DP351917.jpg) -- sits at monumental scale
(1,450px on the 1,080px page), cropped by the frame edges, as if the snow had touched the
lens. It is one of the 974. A handful of huge, heavily defocused crystals (the five "showcase"
crops already built for earlier rounds) sit even closer, soft bokeh in the immediate
foreground, one of them grazing the numeral. Between the giant and the bokeh, 418 crystals
sit sharp in a single focal plane -- drawn only from crystals with a real stellar or
dendritic shape (not a plate), the same 56-photograph set used for the far tier's legibility
filter in v3. 550 small, hazy marks fill the sky behind all of it. The real, moonlit Bernina
skyline sits furthest back. 1 (macro) + 5 (near, defocused) + 418 (focal, sharp) + 550 (far,
atmosphere) = 974. See SOURCES.json for the full accounting and the honest reuse disclosure.

The skyline (v4-v5): a real single-viewpoint horizon from Muottas Muragl (LV03 789.4/152.0),
now computed against swissALTI3D (2m, resampled to 10m) instead of the 200m DEM used through
v3 -- the summits and aretes of Piz Bernina (reaching 4,043m in this data, against a true
summit of 4,049m), Piz Roseg and Piz Palu are real, not smoothed hills. The near ridge crest
carries a cold, faint "moonlight" highlight whose strength along the ridge is derived from the
DEM itself (a local east-west slope proxy, lit as if from the left), not drawn by hand. The
raw swissALTI3D tiles live only in scratch, per instruction; this module ships just the
computed horizon (skyline.json) and the per-azimuth light strength (ridge_light.json).

Romansh poster rule: almost no words. The figure, "novas decisiuns", the week and its dates,
opencaselaw.ch, and the short credit "Wilson A. Bentley · swisstopo" -- unchanged.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import json
import math
import random
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w47_assets") / "naiv"
CRYSTALS_DIR = ASSETS / "crystals"
SHOWCASE_DIR = ASSETS / "crystals_showcase"
MACRO_DIR = ASSETS / "macro"
SKYLINE = json.loads((ASSETS / "skyline.json").read_text(encoding="utf-8"))
RIDGE_LIGHT = json.loads((ASSETS / "ridge_light.json").read_text(encoding="utf-8"))
CRYSTAL_MANIFEST = json.loads((CRYSTALS_DIR / "manifest.json").read_text(encoding="utf-8"))
SHOWCASE_MANIFEST = json.loads((SHOWCASE_DIR / "manifest.json").read_text(encoding="utf-8"))
MACRO_MANIFEST = json.loads((MACRO_DIR / "manifest.json").read_text(encoding="utf-8"))
FAR_ELIGIBLE = json.loads((ASSETS / "far_eligible.json").read_text(encoding="utf-8"))

SKY_TOP = "#03050a"
SKY_MID = "#010203"
SKY_BOT = "#000000"
NEAR_RIDGE_TOP = "#182236"   # clearly lighter than the near-pure-black sky, so the mass reads
NEAR_RIDGE_BOT = "#070a12"   # as a solid dark mountain, not disappear into it
RIDGE_RIM = "rgba(188,208,255,.60)"
MOON_TINT = "rgba(168,198,255,.85)"   # the one cold blue accent, on the moonlit ridge only
INK = "#ffffff"
GLOW_COOL = "rgba(150,190,255,.55)"

RIDGE_Y1 = 1350

# boxes (css px) kept clear of dense/large crystal placement so "974", "novas decisiuns" and the
# short foot credit stay legible
NUMERAL_BOX = (90, 330, 990, 700)
TITLE_BOX = (280, 756, 800, 826)
FOOT_BOX = (260, 1248, 820, 1340)

# the four tiers (974 marks total): the monumental macro crystal, a few huge soft
# out-of-focus crystals close to "the lens", the sharp focal-plane majority (what reads as
# "974"), and a small-and-hazy atmosphere behind all of it.
MACRO_N = 1
NEAR_N, NEAR_SIZE, NEAR_BLUR, NEAR_OPACITY = 5, (300, 620), 16.0, (0.30, 0.52)
# wider size range and sparser packing than v5, spread over the whole page (see _instances) --
# v5's focal tier packed tight and narrow-sized at the foot and read as "a band of sticker
# crystals"
FOCAL_N, FOCAL_SIZE, FOCAL_BLUR, FOCAL_OPACITY = 418, (12, 92), 0.15, (0.45, 0.85)
# more of a veil: smaller, softer, dimmer than v5 -- "use more of the 550 far marks as fine haze"
FAR_N, FAR_SIZE, FAR_BLUR, FAR_OPACITY = 550, (6, 12), 3.0, (0.08, 0.18)

assert MACRO_N + NEAR_N + FOCAL_N + FAR_N == 974, MACRO_N + NEAR_N + FOCAL_N + FAR_N

# the monumental crystal: centred off to the left so it bleeds off the frame on three sides,
# behind the numeral (which stays legible on top of it). Pulled well up from an earlier draft
# that reached y=1625 and completely buried the "far behind" skyline -- the macro crystal's own
# bottom edge now clears the ridge (which, at this narrow true-scale azimuth crop, reaches as
# high as y=~1040) by a wide margin, leaving the moonlit mountain its own clear band.
MACRO_POS = (430.0, 230.0, 1450.0, -8.0)   # x, y, size_px, rotation_deg

# the 5 near, heavily defocused crystals -- huge soft bokeh close to "the lens". One (index 2)
# sits across the numeral's right edge: "partly occluded... provided it still reads instantly".
# None of the 5 crosses the mountain band any more: even at NEAR's low opacity + heavy blur, a
# soft crystal glow laid over the "solid" ridge read as the ridge itself being translucent --
# simplest, cleanest fix is to keep the whole mountain band free of every tier, front or back.
# (x, y, size_px, rotation_deg, showcase_idx)
NEAR = [
    (-40,  140, 480,  24, 0),
    (1150,  110, 440, -16, 3),
    (1010,  560, 320,  40, 1),
    (980,   890, 420,  10, 4),
    (90,    900, 400, -30, 2),
]
assert len(NEAR) == NEAR_N


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _crystal_defs() -> tuple[str, list[dict]]:
    """<defs> for every distinct crystal image, centred on its own local origin, plus the list
    of {id, long} so placement can size instances relative to each sprite's longest side."""
    out = []
    info = []
    for i, m in enumerate(CRYSTAL_MANIFEST):
        w, h = m["w"], m["h"]
        b64 = _b64(CRYSTALS_DIR / m["file"])
        out.append(f'<image id="c{i}" x="{-w/2:.1f}" y="{-h/2:.1f}" width="{w}" height="{h}" '
                   f'href="data:image/png;base64,{b64}"/>')
        info.append({"id": f"c{i}", "long": max(w, h)})
    for i, m in enumerate(SHOWCASE_MANIFEST):
        w, h = m["w"], m["h"]
        b64 = _b64(SHOWCASE_DIR / m["file"])
        out.append(f'<image id="s{i}" x="{-w/2:.1f}" y="{-h/2:.1f}" width="{w}" height="{h}" '
                   f'href="data:image/png;base64,{b64}"/>')
    for i, m in enumerate(MACRO_MANIFEST):
        w, h = m["w"], m["h"]
        b64 = _b64(MACRO_DIR / m["file"])
        out.append(f'<image id="m{i}" x="{-w/2:.1f}" y="{-h/2:.1f}" width="{w}" height="{h}" '
                   f'href="data:image/webp;base64,{b64}"/>')
    return "".join(out), info


# The poster shows the Bernina sector of the cast fan only (164-194 deg). Horizontal position
# still uses the true px/degree scale (W / fan width); the vertical scale is set so the whole
# silhouette occupies a fixed ~16% band at the foot of the page, per this round's explicit ask
# (a ~12% difference from strict true-scale on the vertical axis alone -- the horizontal scale,
# and the shape of every individual summit, stay exactly as cast).
VIEW_AZ = (164.0, 194.0)
RIDGE_BAND_FRAC = 0.16
RIDGE_BAND_PX = round(H * RIDGE_BAND_FRAC)          # ~216px
MOUNTAIN_TOP_Y = RIDGE_Y1 - RIDGE_BAND_PX           # the tallest the ridge can reach anywhere;
                                                     # _instances() uses the REAL per-column
                                                     # ridge height, not this flat line, as the
                                                     # snow ceiling (see _ceiling())


def _view_slice() -> slice:
    az0, az1 = SKYLINE["azimuth_deg"]
    n = len(SKYLINE["near_angle_deg"])
    i0 = round((VIEW_AZ[0] - az0) / (az1 - az0) * (n - 1))
    i1 = round((VIEW_AZ[1] - az0) / (az1 - az0) * (n - 1))
    return slice(i0, i1 + 1)


def _layer_points(angles: list[float], amin: float, ppd: float) -> list[tuple[float, float]]:
    n = len(angles)
    pts = []
    for i, a in enumerate(angles):
        x = i / (n - 1) * W
        y = RIDGE_Y1 - (a - amin) * ppd
        pts.append((x, min(y, RIDGE_Y1)))
    return pts


def _ridge() -> tuple[str, list[tuple[float, float]], list[float]]:
    """The real single-viewpoint horizon against swissALTI3D (Muottas Muragl -> Bernina group;
    see module docstring and SOURCES.json), as one SOLID SVG polygon (the higher of the
    near/far-layer angle per azimuth) filling the bottom RIDGE_BAND_FRAC of the page, plus the
    per-point moonlight strength."""
    sl = _view_slice()
    near = SKYLINE["near_angle_deg"][sl]
    far = SKYLINE["far_angle_deg"][sl]
    combo = [max(a, b) for a, b in zip(near, far)]
    amin, amax = min(combo), max(combo)
    ppd = RIDGE_BAND_PX / max(1e-6, (amax - amin))
    pts = _layer_points(combo, amin, ppd)
    n = len(pts)
    # ~15px along x, in index units (pts span the full page width)
    win = max(1, round(15.0 / (W / n)))
    light = _smooth(RIDGE_LIGHT[sl], win)

    d = f"M0,{RIDGE_Y1}L" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f"L{W},{RIDGE_Y1}Z"
    return d, pts, light


def _smooth(a: list[float], win: int) -> list[float]:
    """A simple Gaussian-ish box smoothing along the list (used to turn the per-column moonlight
    strength into a continuous gradient instead of visible per-sample stripes)."""
    if win <= 1:
        return list(a)
    half = win // 2
    out = []
    for i in range(len(a)):
        lo, hi = max(0, i - half), min(len(a), i + half + 1)
        out.append(sum(a[lo:hi]) / (hi - lo))
    return out


def _moonlight_svg(pts: list[tuple[float, float]], light: list[float]) -> str:
    """A graded cold highlight tucked against the ridge crest ONLY (not a band across the whole
    mass), strength per point taken from the DEM's own local slope (ridge_light.json) -- snow
    faces that would catch a light from the left read brighter. The band height is proportional
    to each column's OWN height above the foot (never more than a third of it), so on the low
    shoulders of the ridge the highlight cannot swallow the whole visible mass and read as a
    separate pale ribbon -- it only ever shades the upper slopes of a solid, dark mountain."""
    out = []
    n = len(pts)
    step = max(1, n // 360)
    for i in range(0, n - step, step):
        x0, y0 = pts[i]
        x1, y1 = pts[min(i + step, n - 1)]
        lv = (light[i] + light[min(i + step, n - 1)]) / 2
        if lv < 0.12:
            continue
        col_h = min(RIDGE_Y1 - y0, RIDGE_Y1 - y1)
        band_h = max(4.0, min(26.0, col_h * 0.32))
        op = lv * 0.34
        out.append(f'<polygon points="{x0:.1f},{y0:.1f} {x1:.1f},{y1:.1f} '
                   f'{x1:.1f},{y1 + band_h:.1f} {x0:.1f},{y0 + band_h:.1f}" '
                   f'fill="{MOON_TINT}" opacity="{op:.3f}"/>')
    return "".join(out)


def _ridge_at(pts: list[tuple[float, float]], x: float) -> float:
    n = len(pts)
    t = max(0.0, min(1.0, x / W)) * (n - 1)
    i = min(int(t), n - 2)
    f = t - i
    return pts[i][1] * (1 - f) + pts[i + 1][1] * f


def _in_box(x: float, y: float, box: tuple[float, float, float, float], pad: float = 0.0) -> bool:
    x0, y0, x1, y1 = box
    return x0 - pad < x < x1 + pad and y0 - pad < y < y1 + pad


def _y_sample(rng: random.Random, bottom_bias: float) -> float:
    u = rng.uniform(0.0, 1.0)
    return -10 + (u ** (1.0 / bottom_bias)) * (H + 20)


class _Grid:
    """A coarse spatial hash so placement can reject points that land too close to an
    already-placed mark of comparable size."""

    def __init__(self, cell: float = 24.0):
        self.cell = cell
        self.buckets: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return int(x // self.cell), int(y // self.cell)

    def ok(self, x: float, y: float, size: float, min_factor: float) -> bool:
        kx, ky = self._key(x, y)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ox, oy, osize in self.buckets.get((kx + dx, ky + dy), ()):
                    min_d = (size + osize) * 0.5 * min_factor
                    if (x - ox) ** 2 + (y - oy) ** 2 < min_d * min_d:
                        return False
        return True

    def add(self, x: float, y: float, size: float) -> None:
        self.buckets.setdefault(self._key(x, y), []).append((x, y, size))


def _use(cid: str, x: float, y: float, size_px: float, native_long: float, deg: float,
         opacity: float, blur: float, glow: bool = False, dim: bool = False) -> str:
    scale = size_px / native_long
    styles = []
    if dim:
        styles.append("filter:brightness(.72) saturate(.85)")
    if glow:
        # "brighten toward white with a faint cool glow" -- a brightness lift plus a soft cold
        # drop-shadow, not a flat recolour
        styles.append("filter:brightness(1.18) drop-shadow(0 0 5px rgba(160,198,255,.45))")
    fill = f' style="{";".join(styles)}"' if styles else ""
    filt = f' filter="url(#blur{round(blur * 10)})"' if blur > 0.01 else ""
    return (f'<use href="#{cid}" transform="translate({x:.1f},{y:.1f}) rotate({deg:.1f}) '
            f'scale({scale:.4f})" opacity="{opacity:.2f}"{filt}{fill}/>')


def _instances(rng: random.Random, ridge_pts: list[tuple[float, float]]) -> tuple[str, str, str]:
    """FAR_N + FOCAL_N random marks, the 5 hand-placed near/defocused crystals, and the 1
    hand-placed macro crystal. Returns (behind-macro, focal-plane, front-of-everything)."""
    out_far, out_focal, out_near = [], [], []
    grid_far = _Grid(cell=20.0)
    grid_focal = _Grid(cell=26.0)
    far_pool = [_CRYSTAL_INFO[i] for i in FAR_ELIGIBLE]
    # the focal plane is restricted to the same real-stellar/dendritic set used for legibility
    # in v3 ("prefer stellar and dendritic crystals ... plates are fine far away")
    focal_pool = far_pool

    # both the far and focal tiers are hard-excluded from the mountain mass itself: "no snow
    # crystals behind the mountain (only in front of it)" -- only the 5 hand-placed NEAR
    # crystals (drawn after, i.e. in front of, the ridge) may cross into that band. FOCAL and
    # FAR render in their OWN svg layers, both with a higher z-index than the sky/mountain svg,
    # so it is not enough to cap the mark's *centre* above the mountain surface -- its bounding
    # radius must clear it too, or the top of a large mark still paints over the "solid" ridge.
    # The ceiling follows the REAL ridge height at that mark's own x (not a flat line at the
    # tallest summit), so snow falls all the way down to the actual mountainside at every
    # column instead of stopping at a uniform height and leaving a bare gap above the lower
    # shoulders of the ridge.

    def _ceiling(x: float, size: float) -> float:
        return _ridge_at(ridge_pts, x) - 4 - size * 0.6

    placed = 0
    attempts = 0
    while placed < FAR_N and attempts < FAR_N * 10:
        attempts += 1
        size = rng.uniform(*FAR_SIZE)
        x = rng.uniform(-10, W + 10)
        y_max = _ceiling(x, size)
        y = rng.uniform(-10, y_max)   # uniform: a veil across the whole sky, not bottom-heavy
        if _in_box(x, y, NUMERAL_BOX) and rng.random() < 0.72:
            continue
        if not grid_far.ok(x, y, size, 0.50):
            continue
        grid_far.add(x, y, size)
        cinfo = rng.choice(far_pool)
        out_far.append(_use(cinfo["id"], x, y, size, cinfo["long"], rng.uniform(0, 360),
                             rng.uniform(*FAR_OPACITY), FAR_BLUR))
        placed += 1
    while placed < FAR_N:
        size = rng.uniform(*FAR_SIZE)
        x = rng.uniform(0, W)
        cinfo = rng.choice(far_pool)
        out_far.append(_use(cinfo["id"], x, rng.uniform(0, _ceiling(x, size)),
                             size, cinfo["long"], rng.uniform(0, 360),
                             rng.uniform(*FAR_OPACITY), FAR_BLUR))
        placed += 1

    # focal: spread over the WHOLE page (not banked at the foot), sparser, a much wider size
    # range so it doesn't read as a uniform layer of same-sized plates; only a few land near the
    # text, and the macro crystal's own area is fair game (focal already draws in front of it)
    placed = 0
    attempts = 0
    while placed < FOCAL_N and attempts < FOCAL_N * 14:
        attempts += 1
        size = rng.uniform(*FOCAL_SIZE)
        x = rng.uniform(-10, W + 10)
        y_max = _ceiling(x, size)
        y = rng.uniform(-10, y_max)
        if _in_box(x, y, NUMERAL_BOX, 6) and rng.random() < 0.90:
            continue
        if _in_box(x, y, TITLE_BOX, 10) and rng.random() < 0.95:
            continue
        if not grid_focal.ok(x, y, size, 0.95):   # looser packing -> visibly sparser
            continue
        grid_focal.add(x, y, size)
        cinfo = rng.choice(focal_pool)
        opacity = rng.uniform(*FOCAL_OPACITY)
        out_focal.append(_use(cinfo["id"], x, y, size, cinfo["long"], rng.uniform(0, 360),
                               opacity, FOCAL_BLUR, glow=True))
        placed += 1
    while placed < FOCAL_N:
        size = rng.uniform(*FOCAL_SIZE)
        x = rng.uniform(0, W)
        cinfo = rng.choice(focal_pool)
        out_focal.append(_use(cinfo["id"], x, rng.uniform(0, _ceiling(x, size)),
                               size, cinfo["long"], rng.uniform(0, 360),
                               rng.uniform(*FOCAL_OPACITY), FOCAL_BLUR, glow=True))
        placed += 1

    for x, y, size, deg, sidx in NEAR:
        m = SHOWCASE_MANIFEST[sidx]
        scale = size / max(m["w"], m["h"])
        out_near.append(f'<use href="#s{sidx}" transform="translate({x:.1f},{y:.1f}) '
                        f'rotate({deg:.1f}) scale({scale:.4f})" opacity="{rng.uniform(*NEAR_OPACITY):.2f}" '
                        f'filter="url(#blur{round(NEAR_BLUR * 10)})" '
                        f'style="filter:brightness(1.1) drop-shadow(0 0 10px rgba(160,198,255,.35))"/>')

    return "".join(out_far), "".join(out_focal), "".join(out_near)


_CRYSTAL_INFO = None  # filled by design() the first time it runs


def _blur_filters() -> str:
    levels = sorted({round(b * 10) for b in (FAR_BLUR, FOCAL_BLUR, NEAR_BLUR) if b > 0.01})
    out = []
    for lv in levels:
        out.append(f'<filter id="blur{lv}" x="-80%" y="-80%" width="260%" height="260%">'
                    f'<feGaussianBlur stdDeviation="{lv / 10:.2f}"/></filter>')
    return "".join(out)


TITLE = {"rm": "novas decisiuns"}
WEEK_LINE = "Emna {w} · {a}–{b} november {y}"
CREDIT = "Wilson A. Bentley · swisstopo"


def design(week: dict, lang: str = "rm") -> str:
    global _CRYSTAL_INFO
    n = week["added"]
    assert n == MACRO_N + NEAR_N + FOCAL_N + FAR_N, (n, "974 marks are built into the layout")

    rng = random.Random(197400 + n)  # deterministic: the figure itself seeds the snowfall

    defs_imgs, crystal_info = _crystal_defs()
    _CRYSTAL_INFO = crystal_info

    ridge_d, ridge_pts, ridge_light = _ridge()
    moonlight_svg = _moonlight_svg(ridge_pts, ridge_light)

    a, b = week["from"], week["to"]
    numeral_text = wp.fmt(n, "rm")

    far_svg, focal_svg, near_svg = _instances(rng, ridge_pts)

    mx, my, msize, mdeg = MACRO_POS
    mm = MACRO_MANIFEST[0]
    mscale = msize / max(mm["w"], mm["h"])
    macro_svg = (f'<use href="#m0" transform="translate({mx:.1f},{my:.1f}) rotate({mdeg:.1f}) '
                f'scale({mscale:.4f})" opacity="0.97" '
                f'style="filter:brightness(1.1) contrast(1.04)"/>')

    body = f"""
<svg class="sky" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="skygrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{SKY_TOP}"/>
      <stop offset="52%" stop-color="{SKY_MID}"/>
      <stop offset="100%" stop-color="{SKY_BOT}"/>
    </linearGradient>
    <linearGradient id="nearridgegrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{NEAR_RIDGE_TOP}"/>
      <stop offset="100%" stop-color="{NEAR_RIDGE_BOT}"/>
    </linearGradient>
    {_blur_filters()}
    {defs_imgs}
  </defs>
  <rect x="0" y="0" width="{W}" height="{H}" fill="url(#skygrad)"/>
  <g class="atmosphere">{far_svg}</g>
  <path d="{ridge_d}" fill="url(#nearridgegrad)"/>
  <g class="moonlight">{moonlight_svg}</g>
  <path d="M0,{ridge_pts[0][1]:.1f}L{"L".join(f"{x:.1f},{y:.1f}" for x, y in ridge_pts)}"
        fill="none" stroke="{RIDGE_RIM}" stroke-width="1.1"/>
</svg>
<svg class="macro-layer" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{macro_svg}</svg>
<div class="numeral">{esc(numeral_text)}</div>
<div class="title">{esc(TITLE.get(lang, TITLE["rm"]))}</div>
<svg class="focal-layer" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{focal_svg}</svg>
<svg class="near-layer" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{near_svg}</svg>
<div class="foot">
  <div class="week">{esc(WEEK_LINE.format(w=week['week_no'], a=a.day, b=b.day, y=b.year))}</div>
  <div class="url">opencaselaw.ch</div>
  <div class="credit">{esc(CREDIT)}</div>
</div>"""

    css = f"""
body{{background:{SKY_BOT};color:{INK};font-family:'Fraunces',serif;overflow:hidden}}
.sky{{position:absolute;left:0;top:0;z-index:0}}
.macro-layer{{position:absolute;left:0;top:0;z-index:1;pointer-events:none}}
.focal-layer{{position:absolute;left:0;top:0;z-index:3;pointer-events:none}}
.near-layer{{position:absolute;left:0;top:0;z-index:5;pointer-events:none}}
.numeral{{position:absolute;left:0;top:300px;width:{W}px;text-align:center;
  font:600 452px/.86 'Fraunces',serif;font-variation-settings:'opsz' 144,'SOFT' 0,'WONK' 0;
  font-variant-numeric:lining-nums tabular-nums;letter-spacing:-.025em;color:{INK};
  text-shadow:0 0 3px rgba(255,255,255,.9),0 0 22px {GLOW_COOL},0 2px 0 rgba(0,0,0,.4),
  0 0 20px rgba(0,0,0,.80),0 0 46px rgba(0,0,0,.65),0 0 80px rgba(0,0,0,.45);
  z-index:2}}
.title{{position:absolute;left:0;top:756px;width:{W}px;text-align:center;
  font:400 54px/1 'Fraunces',serif;font-variation-settings:'opsz' 60;letter-spacing:.01em;
  color:{INK};text-shadow:0 0 2px rgba(255,255,255,.85),0 0 14px {GLOW_COOL},
  0 0 18px rgba(0,0,0,.85),0 0 36px rgba(0,0,0,.70);z-index:4}}
.foot{{position:absolute;left:0;right:0;bottom:54px;text-align:center;z-index:6}}
.week{{font:600 23px/1 'Fraunces',serif;font-variation-settings:'opsz' 30;letter-spacing:.04em;
  color:{INK};text-shadow:0 1px 6px rgba(0,0,0,.8)}}
.url{{margin-top:9px;font:600 25px/1 'Archivo',sans-serif;letter-spacing:.05em;color:{INK};
  text-shadow:0 1px 6px rgba(0,0,0,.8)}}
.credit{{margin:10px auto 0;font:400 15px/1.3 'Archivo',sans-serif;letter-spacing:.015em;
  color:rgba(255,255,255,.72);text-shadow:0 1px 5px rgba(0,0,0,.8)}}
"""
    return page("family=Fraunces:ital,opsz,wght,SOFT,WONK@0,9..144,300..900,0..100,0..1;1,9..144,300..900,0..100,0..1"
                "&family=Archivo:wght@500;600", css, body, lang)
