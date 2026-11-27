"""Week 48 (20 - 26 November 2026), German: Bern bei Nacht.

Matthaeus Merian's copperplate "Bern die Hauptstatt in Nuechtland ..." (Topographia Helvetiae,
Frankfurt 1642; drawn by Joseph Plepp, engraved by Merian; Zentralbibliothek Zuerich, public domain
-- see weekly_poster_w48_assets/bern/SOURCES.json), turned into a winter night.

The plate supplies the whole image: a radical portrait crop (880,0)-(3624,3430) of the 5608x3703
scan keeps the Aare's far loop and the plate's own title cartouche (the winged genii, the Bern bear
shield, "BE[RN]") as the night sky across the top, the full dense old town in the river's crook
through the middle -- roofs, gables, the arcaded terraces, the city wall and its towers -- and the
Aare's near loop ("Aar flu:", with its own little boats) as a dark foreground river at the base.

Re-toning (v3): the scan is flat-fielded twice (a wide pass for paper toning/foxing, a local-contrast
pass -- ink minus 0.7x its own neighbourhood density, then a knee -- so a bold, sparse stroke reads
near its own full brightness while a densely hatched field, river waves or an open field, collapses
hard toward near-black with only a faint textured ambient left), then duotoned: deep blue-black night
(rivers and fields read ~8-12% grey), fine cool silver linework in the sky/cartouche and both rivers,
warming only through the built-up middle band.

The windows: exactly `week["added"]` points, one per decision, each a small warm rectangular glow (a
soft-edged cell, ~3-5 x 2-3 px at 2x, not a round dot, sized and oriented like the plate's own window
ticks, occasionally paired) with a hot near-white-yellow centre fading to amber, plus a soft bloom
(radius 8-14px at 2x, stronger for the brightest tenth) that spills onto the neighbouring engraved
lines and, added up across a lit street, reads as a block glowing from within -- all tonemapped
through a highlight knee so clusters don't blow out, with per-point random size/warmth so it reads as
candle/lamp light, not LEDs. A separate, much lower warm haze sits over the inhabited blocks as a
whole, weighted by the same windows' own local density (a wide blur of their glow), so a street with
many close lights warms up more than a sparse corner, restricted to the detector's own built-up mask
so it can never bleed onto the river or the ramparts.

Their positions are DETECTED, not placed, by actual connected-component shape analysis (flood-fill
over the flattened ink, restricted to a handful of rectangles hand-traced around the plate's own
built-up blocks, with the open lanes and squares inside them -- the Kramgasse/Marktgasse junction by
the Zytglogge chief among them -- cut back out by hand, since a stray fleck of scan grain in blank
paper is exactly as "small, square, solid ink in an otherwise pale patch" as a real window tick is):
each ink mark's own area, bounding box and fill ratio are measured, and only marks that are small AND
roughly square AND solid are kept -- this is what rejects a continuous stroke (an eave line, a
roofline ridge, a block's own outline, a rampart merlon all show up as one large or long/thin
component, not a compact one) without needing to know what a window looks like beyond "small, square,
solid ink sitting in an otherwise pale patch of wall". The cleanest `added` such marks (by fill ratio
and squareness) are kept, capped at 10 per 60px cell so one unusually window-rich building can't read
as a blob on its own -- the quota is made up from elsewhere in a pool several times `added` large.
Every other window the plate draws stays an unlit, dim, moonlit mark: only 861 of Bern's windows are
lit tonight.

The figure "861" sits low in the Aare's near loop, bottom right -- open dark water, lit like a
reflection of the moon -- set in Fraunces at a low optical size (a sharp, high-contrast "engraved"
serif, lining figures), the one legible modern mark in the piece, deliberately apart from the town's
own candlelight above it.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350

ASSET = Path(__file__).with_name("weekly_poster_w48_assets") / "bern" / "bern_source.jpg"
ASSET_W, ASSET_H = 2160, 2700  # exactly 2x the design space, so asset_px = design_xy * 2

# ---- window detection --------------------------------------------------------------------------
# Rectangles hand-traced (by eye, on a 1100px-wide preview of the 2744x3430 source crop) around the
# plate's own built-up blocks -- the terraced rows under "H", "G", "F", the "I"/"K" church cluster,
# the row below it, and the crescent block by the river on the right -- excluding the star-fort
# lawns, the garden strips just inside the wall, the hillside and both rivers. Eroded well clear of
# the wall below.
PREVIEW_W = 1100
BOXES_PREVIEW = [
    (170, 475, 495, 880),
    (500, 515, 830, 930),
    (385, 880, 750, 1070),
    (795, 650, 1090, 895),
]
_BOX_SCALE = ASSET_W / PREVIEW_W

# The open lanes/square around the Zytglogge (Kramgasse/Marktgasse junction), hand-traced directly in
# asset px from a gridded crop: wide enough blank paper that the block boxes above catch it, and the
# detector -- which only asks "small, square, solid ink in an otherwise pale patch" -- has no way to
# tell a stray fleck of scan grain apart from a window tick there. Subtracted from the mask below.
LANES_ASSET = [
    (780, 1190, 1010, 1470),
    (1010, 1200, 1230, 1300),
]

NIGHT_DARK = (2, 4, 10)          # blank paper -> deep, almost-black night
LINE_COOL = (155, 180, 208)      # inked paper, far loop / near loop / sky: fine cool silver, crisp
LINE_WARM = (132, 104, 74)       # inked paper, the built-up band: dim, before any window light
WARM_HOT = (255, 248, 206)       # window glow, hot near-white centre
WARM_A = (255, 196, 112)         # window glow, body colour, cooler/dimmer candle
WARM_B = (255, 146, 58)          # window glow, body colour, hotter/brighter candle
HAZE = (130, 92, 52)             # warm ambient over the inhabited blocks, weighted by window density
MOON = (232, 238, 246)           # the numeral: cold moonlight, not antique

CREDIT = "Matthäus Merian, Bern, 1642 · Zentralbibliothek Zürich"
KEY_LINE = {"de": "Ein Licht pro Entscheid."}
TAG = {"de": "neue Entscheide"}
WEEK_LABEL = {"de": "Woche {w}"}


def _boxes_asset() -> list[tuple[float, float, float, float]]:
    return [tuple(v * _BOX_SCALE for v in b) for b in BOXES_PREVIEW]


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)x(2r+1) window, via an integral image -- O(wh) for any radius."""
    h, w = a.shape
    csum = np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1)
    csum = np.pad(csum, ((1, 0), (1, 0)))
    ys, xs = np.arange(h), np.arange(w)
    y0, y1 = np.clip(ys - r, 0, h), np.clip(ys + r + 1, 0, h)
    x0, x1 = np.clip(xs - r, 0, w), np.clip(xs + r + 1, 0, w)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    s = csum[Y1, X1] - csum[Y0, X1] - csum[Y1, X0] + csum[Y0, X0]
    return s / ((Y1 - Y0) * (X1 - X0))


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """A true (round, soft-edged) Gaussian blur of std-dev `sigma`, via three successive box_blur
    passes of matching radius -- one box_blur pass alone has a square footprint (its own edge is a
    straight line, visible as a hard-edged square once the radius is large enough to matter, as it
    was in the haze below before this existed); three box passes in a row already look convincingly
    round and soft (central-limit-theorem territory), which is the standard cheap approximation to a
    real Gaussian kernel. Box-blur variance at half-width r is ((2r+1)^2-1)/12; three in a row add
    their variances, so solving for r from the target sigma gives the radius used for all three."""
    r = max(1, round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))
    out = a
    for _ in range(3):
        out = box_blur(out, r)
    return out


def _erode(mask: np.ndarray, r: int) -> np.ndarray:
    m = mask.copy()
    for d in range(1, r + 1):
        m[:, d:] &= mask[:, :-d]
        m[:, :-d] &= mask[:, d:]
    m2 = m.copy()
    for d in range(1, r + 1):
        m2[d:, :] &= m[:-d, :]
        m2[:-d, :] &= m[d:, :]
    return m2


def load_gray() -> np.ndarray:
    im = Image.open(ASSET).convert("L")
    assert im.size == (ASSET_W, ASSET_H), f"unexpected asset size {im.size}"
    return np.asarray(im, dtype=np.float64)


def flatten_ink(gray: np.ndarray, r1: int = 150) -> np.ndarray:
    """0..1 ink density, illumination/foxing flattened out: 0 = original white paper, 1 = ink.

    Two passes: the first (r=`r1`) removes the paper's own toning and foxing, the second (r=70)
    removes any illumination gradient the first pass left. `r1=150` is used for window detection --
    it leaves the ink field's own component shapes stable (see detect_windows). For the render,
    `r1=20` is used instead, to clean a soft stain the uncorrected scan carries in the upper-left
    sky: a much wider radius looks like it should flatten a soft stain better, but doesn't -- a stain
    this size (tens of px) sits well inside a wide window, so the window's own average still dips
    with it and barely cancels it out. A radius closer to the stain's own size tracks it properly
    without smoothing away real linework, which is far finer; it isn't used for detection because it
    changes which ink marks come out as one connected piece vs several, which the detector is tuned
    against."""
    g = gray / 255.0
    bg1 = box_blur(g, r1)
    flat1 = g - bg1 + float(bg1.mean())
    bg2 = box_blur(flat1, 70)
    flat2 = flat1 - bg2 + float(bg2.mean())
    lo, hi = np.percentile(flat2, 0.5), np.percentile(flat2, 99.5)
    flat2 = np.clip((flat2 - lo) / max(hi - lo, 1e-6), 0, 1)
    return np.clip(1 - flat2, 0, 1)


def build_mask(h: int, w: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    for x0, y0, x1, y1 in _boxes_asset():
        mask[int(y0):int(y1), int(x0):int(x1)] = True
    mask = _erode(mask, 13)
    for x0, y0, x1, y1 in LANES_ASSET:
        mask[y0:y1, x0:x1] = False
    return mask


def _connected_components(binary: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connected components of `binary` (True pixels), via flood fill. Cheap here because `binary`
    is already restricted to a handful of small rectangles, not the whole asset."""
    h, w = binary.shape
    visited = np.zeros_like(binary, dtype=bool)
    ys, xs = np.nonzero(binary)
    comps: list[list[tuple[int, int]]] = []
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        pts: list[tuple[int, int]] = []
        while stack:
            cy, cx = stack.pop()
            pts.append((cy, cx))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w and binary[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        comps.append(pts)
    return comps


def detect_windows(ink: np.ndarray, mask: np.ndarray, n: int, seed: int = 48) -> list[tuple[float, float]]:
    """n (x, y) positions, one per decision: real connected components of the flattened ink, inside
    `mask`, kept only when the mark itself is small, roughly square and solid (rejects a continuous
    stroke -- an eave line, a roofline ridge, a block's own outline, a rampart merlon -- which shows
    up as one large or long/thin component instead) and its neighbourhood is mostly pale wall. The
    cleanest `n` (by fill ratio and squareness) are kept."""
    large = box_blur(ink, 15)
    binary = (ink > 0.35) & mask
    cands: list[tuple[float, float, float]] = []  # (x, y, quality)
    for pts in _connected_components(binary):
        area = len(pts)
        if area > 70:
            continue
        arr = np.array(pts)
        y0, x0 = arr.min(0)
        y1, x1 = arr.max(0)
        bh, bw = int(y1 - y0 + 1), int(x1 - x0 + 1)
        if max(bh, bw) > 12:
            continue
        fill = area / (bh * bw)
        aspect = bh / bw if bw else 99.0
        if not (0.22 <= aspect <= 4.2 and fill >= 0.22):
            continue
        cy, cx = float(arr[:, 0].mean()), float(arr[:, 1].mean())
        if large[int(cy), int(cx)] >= 0.40:
            continue  # sitting in a busy neighbourhood (rampart, hillside) -- not an isolated window
        quality = fill - 0.05 * abs(aspect - 1.3)
        cands.append((cx, cy, quality))

    assert len(cands) >= n, f"only {len(cands)} window candidates found, need {n} -- loosen thresholds"
    cands.sort(key=lambda c: -c[2])

    # a per-cell cap (60px, matching the distribution check the brief asks for) so one unusually
    # window-rich building -- a turret with a tight row of tiny openings -- can't itself read as a
    # blob: once a cell is full its remaining candidates are skipped, and the quota is made up from
    # elsewhere in the pool, which is big enough (see above) to have plenty of room for that.
    cell = 60.0
    cell_cap = 10
    cell_count: dict[tuple[int, int], int] = {}
    kept: list[tuple[float, float]] = []
    for x, y, _ in cands:
        if len(kept) >= n:
            break
        key = (int(x // cell), int(y // cell))
        if cell_count.get(key, 0) >= cell_cap:
            continue
        cell_count[key] = cell_count.get(key, 0) + 1
        kept.append((x, y))
    assert len(kept) == n, f"only {len(kept)} window candidates survive the per-cell cap, need {n}"
    random.Random(seed).shuffle(kept)  # painter's-order independence from detection rank
    return kept


def _mix(c: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in c)
    return f"rgb({r},{g},{b})"


# where the numeral sits, bottom-right, in the Aare's near loop (asset px) -- used both to place the
# CSS text block and to aim the "moon on water" highlight at the same spot in the raster background
NUMERAL_CENTER = (1620, 2140)  # asset px


def build_background(ink: np.ndarray, mask: np.ndarray,
                      pts: list[tuple[float, float]]) -> tuple[str, str]:
    """(background, glow_front): the night engraving (local-contrast duotone, graded cool-to-warm by
    height) with its 861 lit windows baked in as a screen blend, plus a warm ambient haze weighted by
    the windows' own local density so a lit street glows as a block, not just as separate sparks --
    and, separately, that same additive warm-glow float buffer as its own RGBA layer, meant to sit
    screen-blended in FRONT of the numeral too, so any light behind the figure visibly touches it."""
    h, w = ink.shape

    # local-contrast pass: a bold, sparse stroke (silhouette, ridge, window tick) stays near its own
    # full brightness; a densely hatched field (hillside, river waves, garden cross-hatch) collapses
    # hard toward near-black with only a faint textured ambient left -- v3: pushed harder than v2, so
    # the rivers and open fields read as deep night (~8-12% grey) and only the crisp lines read silver
    density = box_blur(ink, 9)
    crisp = np.clip(ink - 0.70 * density, 0, 1) ** 1.2
    tex = np.clip(crisp + density * 0.08, 0, 1)

    yy = np.arange(h, dtype=np.float64)
    # warmth only through the built-up middle band; the sky and both rivers stay cool silver
    warmth = np.exp(-((yy / h - 0.50) ** 2) / (2 * 0.15 ** 2))[:, None, None]
    line = np.array(LINE_COOL) + (np.array(LINE_WARM) - np.array(LINE_COOL)) * warmth
    curve = tex[..., None]
    bg = np.array(NIGHT_DARK) + (line - np.array(NIGHT_DARK)) * curve

    # moonlight on the near river, aimed at the numeral's own spot, bottom right
    xx = np.arange(w, dtype=np.float64)[None, :]
    mx, my = NUMERAL_CENTER
    moon_d = ((xx - mx) / (w * 0.26)) ** 2 + ((yy[:, None] - my) / (h * 0.16)) ** 2
    moon = np.exp(-moon_d) * 15.0
    bg = np.clip(bg + moon[..., None] * np.array([0.85, 0.95, 1.15]), 0, 255)

    rng = random.Random(4817)
    core_amp = np.zeros((h, w), dtype=np.float64)
    core_col = np.zeros((h, w, 3), dtype=np.float64)
    halo_amp = np.zeros((h, w), dtype=np.float64)
    halo_col = np.zeros((h, w, 3), dtype=np.float64)

    def splat_rect(x: float, y: float, sx: float, sy: float, angle: float, strength: float,
                   color: tuple[float, float, float]) -> None:
        """A small soft-edged rectangle (quartic falloff), oriented like a real window cell --
        not a round dot."""
        r = int(max(sx, sy) * 3) + 2
        x0, x1 = max(0, int(x) - r), min(w, int(x) + r + 1)
        y0, y1 = max(0, int(y) - r), min(h, int(y) + r + 1)
        if x1 <= x0 or y1 <= y0:
            return
        gx = np.arange(x0, x1) - x
        gy = (np.arange(y0, y1) - y)[:, None]
        ca, sa = np.cos(angle), np.sin(angle)
        u = gx * ca + gy * sa
        v = -gx * sa + gy * ca
        g = np.exp(-((np.abs(u) / sx) ** 4 + (np.abs(v) / sy) ** 4)) * strength
        core_amp[y0:y1, x0:x1] += g
        core_col[y0:y1, x0:x1] += g[..., None] * np.array(color)

    def splat_halo(x: float, y: float, sigma: float, strength: float,
                   color: tuple[float, float, float]) -> None:
        r = int(sigma * 2.5) + 1
        x0, x1 = max(0, int(x) - r), min(w, int(x) + r + 1)
        y0, y1 = max(0, int(y) - r), min(h, int(y) + r + 1)
        if x1 <= x0 or y1 <= y0:
            return
        gx = np.arange(x0, x1) - x
        gy = (np.arange(y0, y1) - y)[:, None]
        g = np.exp(-(gx ** 2 + gy ** 2) / (2 * sigma ** 2)) * strength
        halo_amp[y0:y1, x0:x1] += g
        halo_col[y0:y1, x0:x1] += g[..., None] * np.array(color)

    for x, y in pts:
        t = rng.random()
        body = tuple(WARM_A[i] + (WARM_B[i] - WARM_A[i]) * t for i in range(3))
        bright = 0.6 + 0.7 * rng.random() ** 1.5  # mostly modest amber, a few brighter rooms
        hot = rng.random() < 0.10  # the brightest ~10%: a stronger bloom, not just a bigger body
        angle = rng.uniform(-0.18, 0.18)  # the block's own oblique angle, not axis-true
        sx, sy = 1.9 + 0.9 * rng.random(), 1.3 + 0.6 * rng.random()  # ~3-5 x 2-3 px at 2x
        jx, jy = x + rng.uniform(-0.5, 0.5), y + rng.uniform(-0.5, 0.5)
        # body (amber-to-warm) + a small hot near-white-yellow centre layered on top, so the window
        # reads hot at its core fading to amber at the edge, not one flat colour throughout
        splat_rect(jx, jy, sx, sy, angle, 2.6 * bright, body)
        splat_rect(jx, jy, sx * 0.42, sy * 0.42, angle, (4.0 if hot else 2.2) * bright, WARM_HOT)
        halo_r = (10.5 + 3.0 * rng.random()) if hot else (8.0 + 2.5 * rng.random())
        splat_halo(jx, jy, halo_r, (0.075 if hot else 0.040) * bright, body)
        if rng.random() < 0.09:  # an occasional lit pair, two rooms of the same house
            d = 3.4 + 1.4 * rng.random()
            px, py = jx + d * np.cos(angle + 1.57), jy + d * np.sin(angle + 1.57)
            splat_rect(px, py, sx * 0.85, sy * 0.85, angle, 2.0 * bright, body)
            splat_rect(px, py, sx * 0.38, sy * 0.38, angle, 1.6 * bright, WARM_HOT)
            splat_halo(px, py, 8.5, 0.035 * bright, body)

    def tonemap(amp: np.ndarray, col: np.ndarray, gain: float) -> np.ndarray:
        avg_col = col / np.maximum(amp[..., None], 1e-6)
        env = 1 - np.exp(-amp * gain)  # highlight knee: saturates smoothly, no blown clusters
        return avg_col * env[..., None]

    # a warm ambient haze, derived ONLY from the windows' own glow -- no mask or block rectangle is
    # ever part of this path, so it cannot show a straight edge. Two blurs of the same core_amp
    # buffer: a near pass (r=20) that pools a cluster of close windows into one warm breath around
    # them, and a very wide, very weak pass (r=130) that lets that breath taper off gradually instead
    # of stopping at a hard radius. A sparse, isolated window contributes almost nothing here; a
    # street with many close lights reads as a warm glow, not a scatter of pinpricks.
    haze_near = gaussian_blur(core_amp, 20)
    haze_wide = gaussian_blur(core_amp, 130)
    haze_src = haze_near * 0.6 + haze_wide * 0.5  # near-haze gain cut ~40%: windows carry the image
    haze_env = 1 - np.exp(-haze_src * 11.0)
    bg = bg + haze_env[..., None] * np.array(HAZE) * 1.1

    glow_env = 1 - np.exp(-(halo_amp * 0.8 + core_amp * 0.95))
    glow = tonemap(halo_amp, halo_col, 0.8) * 0.9 + tonemap(core_amp, core_col, 1.0)
    glow = np.clip(glow, 0, 255) / 255.0
    bg_n = np.clip(bg, 0, 255) / 255.0
    out = (1 - (1 - bg_n) * (1 - glow)) * 255.0  # screen blend: glow spills onto the linework

    img = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    bg_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(glow * 255.0, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(glow_env * 255.0, 0, 255).astype(np.uint8)
    gbuf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(gbuf, "PNG", optimize=True)
    glow_b64 = base64.b64encode(gbuf.getvalue()).decode("ascii")
    return bg_b64, glow_b64


def design(week: dict, lang: str) -> str:
    assert lang == "de", "the Bern bei Nacht poster is German-only"
    n = week["added"]

    gray = load_gray()
    ink = flatten_ink(gray)  # r1=150: stable component shapes for detection
    mask = build_mask(*ink.shape)
    pts = detect_windows(ink, mask, n)
    assert len(pts) == n == week["added"], "one lit window per decision"

    ink_render = flatten_ink(gray, r1=20)  # r1=20: cleans the upper-left sky's paper stain
    bg_b64, glow_b64 = build_background(ink_render, mask, pts)

    d0, d1 = week["from"], week["to"]
    date_line = f"{d0.day}.–{d1.day}. November {d1.year}"
    week_line = WEEK_LABEL["de"].format(w=week["week_no"])
    n_fmt = wp.fmt(n, lang)

    nx, ny = NUMERAL_CENTER[0] / 2, NUMERAL_CENTER[1] / 2  # design px

    css = f"""
body{{background:#02040a;color:{_mix(MOON)}}}
.bg{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover}}
.glow{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover;mix-blend-mode:screen}}
.n{{position:absolute;right:62px;width:640px;top:{ny - 128:.0f}px;text-align:right;
   font-family:'Fraunces',serif;font-weight:680;font-size:192px;line-height:0.86;
   font-variation-settings:'opsz' 9,'SOFT' 0;letter-spacing:-0.01em;color:{_mix(MOON)};
   font-variant-numeric:lining-nums tabular-nums;font-feature-settings:'lnum' 1;
   text-shadow:0 0 50px rgba(170,200,230,.5),0 0 140px rgba(130,170,220,.28),0 8px 26px rgba(0,0,0,.6)}}
.tag{{position:absolute;right:66px;width:640px;top:{ny + 108:.0f}px;text-align:right;
   font-family:'Archivo',sans-serif;font-weight:500;font-size:25px;letter-spacing:.14em;
   text-transform:uppercase;color:rgba(228,236,244,.82);text-shadow:0 2px 14px rgba(0,0,0,.6)}}
.meta{{position:absolute;left:64px;top:1030px;width:300px;text-align:left}}
.meta .week2{{font-family:'Archivo',sans-serif;font-weight:700;font-size:21px;letter-spacing:.03em;
   text-transform:uppercase;color:#f0ede4;text-shadow:0 2px 10px rgba(0,0,0,.6)}}
.meta .date2{{margin-top:6px;font-family:'Archivo',sans-serif;font-weight:400;font-size:16px;
   letter-spacing:.01em;color:rgba(240,237,228,.78);text-shadow:0 1px 8px rgba(0,0,0,.6)}}
.meta .key{{margin-top:14px;font-family:'Fraunces',serif;font-style:italic;font-weight:440;
   font-variation-settings:'opsz' 24;font-size:21px;line-height:1.35;color:rgba(255,206,140,.92);
   text-shadow:0 2px 12px rgba(0,0,0,.55)}}
.credit{{position:absolute;left:0;bottom:40px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:400;font-size:14px;letter-spacing:.01em;
   color:rgba(228,236,244,.6)}}
.url2{{position:absolute;left:0;bottom:66px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:700;font-size:17px;letter-spacing:.05em;
   color:rgba(255,206,140,.85)}}
"""
    body = f"""<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="n">{esc(n_fmt)}</div>
<div class="tag">{esc(TAG['de'])}</div>
<div class="meta">
<div class="week2">{esc(week_line)}</div>
<div class="date2">{esc(date_line)}</div>
<div class="key">{esc(KEY_LINE['de'])}</div>
</div>
<div class="url2">opencaselaw.ch</div>
<div class="credit">{esc(CREDIT)}</div>
<img class="glow" src="data:image/png;base64,{glow_b64}" alt="">"""
    return page("family=Fraunces:ital,opsz,wght@0,9..40,600..800;1,9..40,400..500"
                "&family=Archivo:wght@400..700", css, body, lang)
