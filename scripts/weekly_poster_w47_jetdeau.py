"""Week 47 (13 - 19 November 2026), French: Jet d'eau.

Geneva's fountain on the Rade, rebuilt droplet by droplet out of the week's decisions.

The photograph: "Genève. Le jet d'eau vers le Kursaal", Photoglob-Wehrli view-card negative no.
17286, circa 1910, Swiss National Library (public domain -- see SOURCES.json). A tall portrait
plate, the plume alone against open sky, the quai and Kursaal skyline at its foot -- chosen over
five other Photoglob-Wehrli Jet d'eau plates in the same collection because it is the one where the
plume stands large, centred and unobstructed.

The photograph is toned night-indigo-to-floodlit-cream and supplies the whole poster: sky, lake,
skyline. Its own misty plume is dimmed, within its own measured silhouette, to a faint ghost -- then
rebuilt as exactly `week["added"]` generated droplets, one per decision, each one's brightness and
hue sampled at render time from this same photograph at its own position, so the droplets' tone,
grain and light come from the photograph and not an invented palette. The column's shape -- the
narrow needle, the waist, the bulges, the splash fanning out at the foot -- is fit from 17
brightness-profile measurements taken directly off the photograph (PLUME_PROFILE below), plus a
small deterministic sway/turbulence so the column reads as water, not a ruled line.

Mapping: Geneva's own 174 decisions are the brightest droplets, packed into the crest -- the needle
and its first flare. The remaining 800 make up the column and the splash: the other 17 cantons,
largest to smallest, stacked top to bottom through the column's body (544 decisions, ZH first, VS
last, just above the foot), and the 256 federal (CH) decisions as the splash that fans out where the
plume meets the Rade -- the single widest group, read as the nationwide reach of the federal courts
scattering outward at the base.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350

ASSET = Path(__file__).with_name("weekly_poster_w47_assets") / "jetdeau" / "jetdeau_source.jpg"
ASSET_W, ASSET_H = 2160, 2700  # exactly 2x the design space (1080x1350), so asset_px = design_xy * 2

# ---- the plume's measured silhouette ------------------------------------------------------------
# (t, half-width, centre-x), all in DESIGN space (1080x1350), read off the photograph: for each
# control height, the longest contiguous band of pixels brighter than the local sky/building
# baseline (robust median+MAD per row), restricted to the plume's x-window, found by script. t=0 at
# the tip, t=1 at the splash where the plume meets the quay. Nineteen measurements; the plume's own
# waist (t~0.37) and second narrowing (t~0.77, partly building-occluded) are real, not noise, and are
# kept smoothed rather than discarded.
PLUME_PROFILE = [
    # t,     width,  cx
    (0.000,   0.0,  450.9),
    (0.065,  14.8,  454.0),
    (0.115,  21.9,  444.9),
    (0.165,  47.3,  447.2),
    (0.215,  57.5,  446.3),
    (0.265,  49.3,  450.0),
    (0.316,  62.4,  443.4),
    (0.366,  29.6,  418.4),
    (0.416,  47.6,  430.9),
    (0.466,  60.7,  433.8),
    (0.516,  56.1,  436.6),
    (0.566,  56.7,  439.5),
    (0.616,  74.1,  442.3),
    (0.666, 104.0,  445.2),
    (0.717, 139.8,  448.0),
    (0.817, 139.6,  450.9),
    (1.000, 162.3,  452.3),
]
Y_TIP, Y_BASE = 142.4, 1278.8  # design-space y of t=0 and t=1

TS = np.array([p[0] for p in PLUME_PROFILE])
WS = np.array([p[1] for p in PLUME_PROFILE])
CS = np.array([p[2] for p in PLUME_PROFILE])


def envelope(t: np.ndarray, rng_seed: int = 47) -> tuple[np.ndarray, np.ndarray]:
    """(half_width, centre_x) at parameter t in [0, 1], the measured taper plus a small
    deterministic billow/sway so the column reads as moving water, not a ruled taper."""
    base_w = np.interp(t, TS, WS) / 2
    base_c = np.interp(t, TS, CS)
    # deterministic turbulence: a few fixed sine terms, phase chosen once and kept (not re-rolled
    # per render) so the column's look is stable across renders of the same week
    billow = (1 + 0.22 * np.sin(t * 27.1 + 1.3) + 0.14 * np.sin(t * 54.0 + 4.1)
              + 0.10 * np.sin(t * 11.0 + 0.4))
    sway = 10 * np.sin(t * 9.4 + 0.7) + 6 * np.sin(t * 21.3 + 2.2)
    return np.maximum(base_w * billow, 0.6), base_c + sway


def _levels(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr / 255.0, 0, 1)


# The photograph was shot by day; the poster is lit as if by night, floodlit -- a straight duotone
# of a daylight negative reads as flat sepia (tried first; dropped). So brightness is remapped
# through this curve before toning: the flat, high-key daylight sky (norm ~0.65-0.78 in this scan)
# is pushed down near black, and only the plume's own brightest water and the sunlit upper cornices
# of the quai buildings -- which happen to be the photograph's brightest pixels -- come forward as
# the warm "floodlit" highlight. Buildings and water, already the darkest tones in the frame, fall
# to near-black silhouette, the way a lit shoreline actually reads after dark.
NIGHT_CURVE_X = [0.00, 0.42, 0.60, 0.68, 0.74, 0.80, 0.87, 0.94, 1.00]
NIGHT_CURVE_Y = [0.00, 0.015, 0.03, 0.055, 0.10, 0.24, 0.52, 0.82, 1.00]


def _duotone(norm: np.ndarray, dark: tuple[int, int, int], light: tuple[int, int, int],
             night: bool = True) -> np.ndarray:
    g = np.interp(norm, NIGHT_CURVE_X, NIGHT_CURVE_Y) if night else np.clip(norm, 0, 1)
    out = np.empty(norm.shape + (3,), dtype=np.float64)
    for i in range(3):
        out[..., i] = dark[i] + (light[i] - dark[i]) * g
    return out


NIGHT_DARK = (8, 13, 30)       # deep indigo-black night sky
NIGHT_MID = (28, 36, 62)       # lake / distant shore, slightly lifted off pure black
NIGHT_LIGHT = (252, 232, 186)  # warm floodlit cream -- the plume's own colour at night

CREST_N = 174                   # GE
SPLASH_N = 256                  # CH, federal
# the 17 remaining cantons, largest to smallest -- the column's body, top to bottom
COLUMN_ORDER = ["ZH", "VD", "BL", "BE", "AG", "TI", "BS", "NE", "SG", "SZ", "JU", "SO", "NW", "GR", "ZG", "LU", "VS"]

CREDIT = {
    "fr": "Photoglob-Wehrli, « Genève. Le jet d’eau vers le Kursaal », v. 1910 — Bibliothèque nationale suisse, domaine public",
}


def _mix_rgb(c: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in c)
    return f"rgb({r},{g},{b})"


def load_gray() -> np.ndarray:
    """The photograph, despeckled (a median filter; the scan carries ordinary scattered dust -- scan
    cleanup, not a change to the photograph's content) and read once for both the background and the
    droplet sampling."""
    im = Image.open(ASSET).convert("L")
    assert im.size == (ASSET_W, ASSET_H), f"unexpected asset size {im.size}"
    im = im.filter(ImageFilter.MedianFilter(5))
    return np.asarray(im)


def _plume_mask(h: int, w: int) -> np.ndarray:
    """1 at the measured silhouette's centre, feathering to 0 at its edge, 0 above the tip (t<=0) --
    shared by the dimmed background ghost and the undimmed front-glow layer, so the two agree
    exactly on where the plume is."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    t_px = np.clip((yy / 2 - Y_TIP) / (Y_BASE - Y_TIP), -0.08, 1.12)
    hw_px, cx_px = envelope(t_px.ravel())
    hw_px = hw_px.reshape(t_px.shape) * 2  # back to asset px (asset is 2x design space)
    cx_px = cx_px.reshape(t_px.shape) * 2
    dist = np.abs(xx - cx_px) / np.maximum(hw_px * 1.35, 1.0)
    mask = np.clip(1.0 - dist, 0.0, 1.0) ** 1.4  # 1 at centre, feathers to 0 at the edge
    return np.where(t_px > 0, mask, 0.0)  # envelope()'s width floor must not reach above t=0


def build_background(gray: np.ndarray, mask: np.ndarray) -> str:
    """The toned night photograph, its own plume dimmed within its measured silhouette to a faint
    ghost (the droplets, plus the undimmed front-glow layer over the numerals, replace it as the
    legible mark); everything else -- sky, lake, skyline -- stays exactly as photographed, only
    re-toned."""
    h, w = gray.shape
    norm = _levels(gray)
    duo = _duotone(norm, NIGHT_DARK, NIGHT_LIGHT)

    # dim the photograph's own plume within its measured silhouette, pulling it part-way toward
    # night-black -- a ghost of the real glow behind the numerals and the generated droplets
    m3 = mask[..., None]
    dim = 0.56
    out = duo * (1 - dim * m3) + np.array(NIGHT_DARK) * (dim * m3)

    # a gentle vignette, top corners only -- standard photographic burn, not an invented shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    vy = (yy / h - 0.08) ** 2
    vx = ((xx / w) - 0.5) ** 2
    vig = np.clip(1.0 - 0.17 * np.clip(vy * 1.4 + vx * 0.5 - 0.10, 0, None), 0.78, 1.0)
    out *= vig[..., None]

    img = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=91)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_mist_front(gray: np.ndarray, mask: np.ndarray) -> str:
    """The plume's own glow, undimmed, as a transparent layer (alpha = the same silhouette mask)
    meant to sit screen-blended in FRONT of the numerals -- so the water, not just the droplets,
    visibly erupts through the figure, while the screen blend keeps the numerals' own light (and so
    their shape) showing through wherever the plume crosses them."""
    norm = _levels(gray)
    duo = _duotone(norm, NIGHT_DARK, NIGHT_LIGHT)
    rgba = np.empty(mask.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = np.clip(duo, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(mask * 235, 0, 255).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _sample(gray: np.ndarray, x: float, y: float) -> tuple[float, float]:
    """(mean, std) of a small patch of the photograph at design-space (x, y) -- the droplet's own
    brightness and grain, read off the photograph rather than invented."""
    ax, ay = int(round(x * 2)), int(round(y * 2))
    h, w = gray.shape
    x0, x1 = max(0, ax - 3), min(w, ax + 4)
    y0, y1 = max(0, ay - 3), min(h, ay + 4)
    if x1 <= x0 or y1 <= y0:
        return 200.0, 4.0
    patch = gray[y0:y1, x0:x1].astype(np.float64)
    return float(patch.mean()), float(patch.std())


def _droplet_t_samples(rng: random.Random, t_lo: float, t_hi: float, n: int) -> list[float]:
    """n values of t in [t_lo, t_hi], density weighted by the envelope's local width -- so a wider
    stretch of the column naturally carries more droplets, a narrower one fewer."""
    grid = np.linspace(t_lo, t_hi, 400)
    w, _ = envelope(grid)
    w = np.maximum(w, 0.8)
    cdf = np.cumsum(w)
    cdf = cdf / cdf[-1]
    out = []
    for _ in range(n):
        u = rng.random()
        idx = int(np.searchsorted(cdf, u))
        idx = min(idx, len(grid) - 1)
        # a touch of jitter within the local grid cell
        lo = grid[max(0, idx - 1)]
        hi = grid[min(len(grid) - 1, idx + 1)]
        out.append(rng.uniform(lo, hi))
    return out


def _tangent_deg(t: float) -> float:
    """The column's own local tangent, in degrees off vertical -- a droplet travelling with the
    water is drawn elongated along this line, not an arbitrary vertical."""
    eps = 0.004
    t0, t1 = max(0.0, t - eps), min(1.0, t + eps)
    _, c0 = envelope(np.array([t0]))
    _, c1 = envelope(np.array([t1]))
    dx = float(c1[0] - c0[0])
    dy = (t1 - t0) * (Y_BASE - Y_TIP)
    return math.degrees(math.atan2(dx, max(dy, 1e-6)))


def _droplet(rng: random.Random, t: float, band: str, gray: np.ndarray) -> dict:
    hw, cx = envelope(np.array([t]))
    hw, cx = float(hw[0]), float(cx[0])
    y = Y_TIP + t * (Y_BASE - Y_TIP)
    if band == "splash":
        spread = hw * 0.95 + 10
        lat = (rng.random() - 0.5 + rng.random() - 0.5) * spread  # triangular-ish, wide fan
        y += rng.uniform(-8, 22)
    elif band == "crest":
        # a dense, tight needle, not a clump: narrow spread, weighted hard toward the centreline
        spread = max(hw, 3.2) * 0.62
        lat = (rng.random() - 0.5 + rng.random() - 0.5 + rng.random() - 0.5) / 1.5 * spread
    else:
        spread = hw
        lat = (rng.random() - 0.5 + rng.random() - 0.5) * spread  # triangular: denser at centre
    x = cx + lat
    mean, std = _sample(gray, x, y)
    bright = mean / 255.0
    edge = min(1.0, abs(lat) / max(spread, 1.0))
    # size: small on average with a wide range (a few larger clumps of spray among many fine drops),
    # via a squared random draw so small droplets dominate
    if band == "crest":
        mix = 0.86 + 0.14 * bright - 0.08 * edge
        r = 0.9 + 3.0 * rng.random() ** 2.0
        op = 0.90 + 0.10 * rng.random()
        angle = _tangent_deg(t) + rng.uniform(-4, 4)
        elong = 2.6 + 1.0 * rng.random()
    elif band == "splash":
        mix = 0.38 + 0.42 * bright - 0.14 * edge
        r = 0.8 + 4.2 * rng.random() ** 2.2
        op = 0.46 + 0.32 * rng.random()
        # outward and down: droplets near the fan's centre fall straight, those at its edge fly
        # out at a steep angle -- a ballistic spray, not a vertical column
        angle = math.copysign(14 + 34 * edge, lat if lat else 1) + rng.uniform(-6, 6)
        elong = 1.4 + 0.9 * rng.random()
    else:
        depth = t  # a touch more shadow/weight lower in the column
        mix = 0.52 + 0.36 * bright - 0.18 * edge - 0.08 * depth
        r = 0.8 + 4.2 * rng.random() ** 1.9 * (0.5 + 0.5 * min(1.0, hw / 70))
        op = 0.62 + 0.32 * rng.random()
        angle = _tangent_deg(t) + lat / max(spread, 1.0) * 10 + rng.uniform(-5, 5)
        elong = 2.1 + 1.1 * rng.random()
    mix += (std - 5) / 255.0 * 0.6  # grain: local tonal variance nudges the droplet's own mix
    mix = max(0.0, min(1.0, mix))
    colour = tuple(NIGHT_MID[i] + (NIGHT_LIGHT[i] - NIGHT_MID[i]) * mix for i in range(3))
    core_mix = min(1.0, mix + 0.22 + 0.10 * rng.random())
    core = tuple(NIGHT_MID[i] + (NIGHT_LIGHT[i] - NIGHT_MID[i]) * core_mix for i in range(3))
    return {"x": x, "y": y, "r": max(0.55, r), "op": round(op, 3), "fill": _mix_rgb(colour),
            "core": _mix_rgb(core), "angle": round(angle, 1), "elong": round(elong, 2), "band": band}


def build_droplets(week: dict, gray: np.ndarray) -> list[dict]:
    by_canton = week["by_canton"]
    ge = by_canton.get("GE", 0)
    ch = by_canton.get("CH", 0)
    column = {k: by_canton[k] for k in COLUMN_ORDER if k in by_canton}
    assert ge == CREST_N, f"GE={ge}, expected {CREST_N} -- canton mix changed, re-check the mapping"
    assert ch == SPLASH_N, f"CH={ch}, expected {SPLASH_N}"
    assert sum(column.values()) + ge + ch == week["added"] == 974
    assert set(column) | {"GE", "CH"} == set(by_canton), "an unmapped canton would be silently dropped"

    rng = random.Random(197411)
    drops: list[dict] = []

    for t in _droplet_t_samples(rng, 0.0, 0.165, ge):
        drops.append(_droplet(rng, t, "crest", gray))

    t0, t1 = 0.165, 0.835
    span = t1 - t0
    total_col = sum(column.values())
    cursor = t0
    for canton in column:
        n = column[canton]
        frac = n / total_col
        lo, hi = cursor, min(t1, cursor + frac * span)
        cursor = hi
        for t in _droplet_t_samples(rng, lo, max(hi, lo + 1e-6), n):
            drops.append(_droplet(rng, t, "column", gray))

    for t in _droplet_t_samples(rng, 0.835, 1.0, ch):
        drops.append(_droplet(rng, t, "splash", gray))

    assert len(drops) == week["added"] == 974, f"{len(drops)} droplets, expected {week['added']}"
    return drops


def _svg_droplets(drops: list[dict]) -> str:
    # painter's order: splash first (it sits lowest/furthest), then column, then crest on top, so
    # the crest's bright marks are never buried under a wide splash droplet
    order = {"splash": 0, "column": 1, "crest": 2}
    drops = sorted(drops, key=lambda d: (order[d["band"]], d["y"]))
    out = []
    for d in drops:
        x, y, r, a = d["x"], d["y"], d["r"], d["angle"]
        rx_h, ry_h = r * 1.05, r * d["elong"] * 1.15          # halo: soft falloff, wider
        rx_c, ry_c = r * 0.55, r * d["elong"] * 0.68           # core: sharp, elongated with travel
        t = f'rotate({a:.1f} {x:.1f} {y:.1f})'
        out.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx_h:.2f}" ry="{ry_h:.2f}" '
                    f'transform="{t}" fill="{d["fill"]}" opacity="{d["op"] * 0.38:.3f}" filter="url(#halo)"/>')
        out.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{rx_c:.2f}" ry="{ry_c:.2f}" '
                    f'transform="{t}" fill="{d["core"]}" opacity="{d["op"]:.3f}" filter="url(#soft)"/>')
    return "".join(out)


KEY_LINE = {
    "fr": "Une goutte par décision · au sommet, les {ge} de Genève · à la base, les {ch} des tribunaux et autorités fédéraux",
}


def design(week: dict, lang: str) -> str:
    assert lang == "fr", "the Jet d'eau poster is French-only"
    n = week["added"]
    ge, ch = week["by_canton"]["GE"], week["by_canton"]["CH"]
    assert (ge, ch) == (CREST_N, SPLASH_N), "key line's wording no longer matches the data"

    gray = load_gray()
    mask = _plume_mask(gray.shape[0], gray.shape[1])

    bg_b64 = build_background(gray, mask)
    mist_b64 = build_mist_front(gray, mask)
    drops = build_droplets(week, gray)
    svg_drops = _svg_droplets(drops)

    d0, d1 = week["from"], week["to"]
    week_line = f"Semaine {week['week_no']} · {d0.day}–{d1.day} novembre {d1.year}"
    key_line = KEY_LINE["fr"].format(ge=wp.fmt(ge, lang), ch=wp.fmt(ch, lang))

    # the numeral's own fill sits BELOW the plume's brightest droplets on purpose: screen-blended
    # water lightens visibly where it crosses the figure, rather than vanishing into an
    # already-white glyph -- the headroom is what makes the eruption actually read
    numeral_fill = _mix_rgb(tuple(NIGHT_DARK[i] + (NIGHT_LIGHT[i] - NIGHT_DARK[i]) * 0.70 for i in range(3)))

    css = f"""
body{{background:#0a1024;color:{_mix_rgb(NIGHT_LIGHT)}}}
.bg{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover}}
.mist{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover;mix-blend-mode:screen}}
.drops{{mix-blend-mode:screen}}
.scrim{{position:absolute;left:0;right:0;bottom:0;height:420px;
   background:linear-gradient(to bottom,rgba(8,13,30,0) 0%,rgba(6,10,24,.55) 55%,rgba(5,8,20,.86) 100%)}}
.n{{position:absolute;left:0;top:300px;width:{W}px;text-align:center;
   font-family:'Fraunces',serif;font-weight:680;font-size:430px;line-height:0.86;
   font-variation-settings:'opsz' 144,'SOFT' 40;font-variant-numeric:lining-nums tabular-nums;
   letter-spacing:-0.01em;color:{numeral_fill};text-shadow:0 0 50px rgba(200,170,120,.28),0 10px 46px rgba(0,0,0,.5)}}
.tag{{position:absolute;left:0;top:706px;width:{W}px;text-align:center;
   font-family:'Fraunces',serif;font-style:italic;font-weight:420;font-size:52px;
   font-variation-settings:'opsz' 24;letter-spacing:.01em;color:#f3e6cc;
   text-shadow:0 2px 18px rgba(0,0,0,.5)}}
.meta{{position:absolute;left:68px;top:826px;width:276px;text-align:left}}
.meta .week2{{font-family:'Archivo',sans-serif;font-weight:700;font-size:23px;letter-spacing:.015em;
   color:#f1e6d2;text-shadow:0 2px 10px rgba(0,0,0,.6)}}
.meta .key{{margin-top:12px;font-family:'Archivo',sans-serif;font-weight:400;font-size:13.5px;
   line-height:1.48;letter-spacing:.005em;color:rgba(241,230,210,.88);text-shadow:0 1px 8px rgba(0,0,0,.6)}}
.meta .url2{{margin-top:14px;font-family:'Archivo',sans-serif;font-weight:700;font-size:27px;
   letter-spacing:.02em;color:#fff;text-shadow:0 2px 10px rgba(0,0,0,.6)}}
.credit{{position:absolute;left:0;bottom:40px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:400;font-size:14.5px;letter-spacing:.01em;
   color:rgba(241,230,210,.72)}}
"""
    body = f"""<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="n">{wp.fmt(n, lang)}</div>
<img class="mist" src="data:image/png;base64,{mist_b64}" alt="">
<svg class="drops" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>
<filter id="soft" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="0.45"/></filter>
<filter id="halo" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="1.9"/></filter>
</defs>{svg_drops}</svg>
<div class="scrim"></div>
<div class="tag">nouvelles décisions</div>
<div class="meta">
<div class="week2">{esc(week_line)}</div>
<div class="key">{esc(key_line)}</div>
<div class="url2">opencaselaw.ch</div>
</div>
<div class="credit">{esc(CREDIT["fr"])}</div>"""
    return page("family=Fraunces:ital,opsz,wght,SOFT@0,9..144,100..900,0..100;1,9..144,100..900,0..100"
                "&family=Archivo:wght@400..700", css, body, lang)
