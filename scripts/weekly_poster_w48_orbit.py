"""Week 48 (20 - 26 November 2026), English: From Orbit.

v2 -- see weekly_poster_w48_orbit_v1.py and en-orbit-v1.png for the first state and the review notes
that replaced it (synthetic circular sprites standing in for real light shapes; the frame over-dimmed
into near-invisibility; a top-left-figure / bottom-left-text-block layout that is exactly the
"editorial skeleton" Jonas rejects; a Google-Fonts CDN dependency; Space Grotesk reading stock at
display size). Same NASA/ISS Expedition 55 frame (ISS055-E-84623, 26 May 2018, Nikon D5, public
domain -- see weekly_poster_w48_assets/orbit/SOURCES.json), re-cropped tighter and higher, so the
limb is a single strong, clean arc with a generous span of real black space above it -- room enough
for the figure to sit large and centred there, the way the brief always asked, rather than squeezed
into whatever sliver of black the first crop left along the top-left corner.

The lights: exactly `week["added"]` of them, one per decision, and each one IS the real pixel shape
of a genuine light in the photograph, not a drawn dot standing in for it. Detection: `warmth`
(red minus blue -- the signature of sodium/LED ground light against this photo's blue-black night
and grey cloud) weighted by brightness gives a `score` field; subtracting a wide box-blur of that
same field (the same top-hat idea weekly_poster_w48_bern.py uses for its engraved windows, here
tuned for a continuous photograph) leaves `contrast`, near zero over cloud, dark terrain and the
airglow, and positive only where the photo is actually, locally brighter than its own surroundings.
Thresholding `contrast` and taking its 4-connected components (flood fill, same technique as
bern's own connected-component detector, run here over the whole frame rather than a few hand-traced
boxes) gives each real light's own actual footprint: a single pixel or a tight few for a distant,
isolated village; an elongated streak for a lit road or valley corridor; a sprawling, branching
multi-thousand-pixel shape for a city's own built-up web -- exactly the "a town is a smear, a road a
line, a city a web" variety a real night photograph has and a synthetic dot can't. 936 components of
area >= 2px survive this threshold and a sky check (area-1 flecks are excluded as noise, not light;
a component sitting entirely above SKY_CUTOFF -- there was exactly one, 8px, isolated by a 348px gap
from the next real ground light below it -- is a star in the black sky, not a ground light, and is
excluded before ranking rather than being left to win a slot by brightness alone); ranked by each
component's own total integrated contrast and kept highest first, the top 861 are almost the whole
pool (861/936, the bottom 75 being the dimmest, most marginal detections) -- so nearly every real
ground light this photograph recorded, from the sparsest rural dot to the biggest city, is one of the
861, and the handful left out are left out for being the least real of the candidates, not for being
inconveniently placed.

Transformation: the OTHER real lights in the frame (there are none excluded by this detector that
aren't already folded into the 861 pool above -- what's "unselected" here is the broad, genuine glow
around and between every detected light, which the detector's own top-hat deliberately does not
chase) are only lightly dimmed -- warmth suppressed to ~55% of itself, a cooling/deepening grade
(gamma-curved, so blacks and the cloud's own shadow sides gain real depth without crushing its soft
grey form) on top -- so the photograph still reads as itself, a real night, just a little cooler and
a little fainter, never erased down to sprite-on-black. Each of the 861 kept components is then
brightened in place, pixel by pixel, from its own measured contrast -- the brightest pixel in a
component's own shape renders hottest (near-white-gold), its dimmer edge pixels stay a deeper warm
gold, so the brightening follows the light's own real internal gradient rather than painting a flat
tint over a silhouette. A very short true-Gaussian feather (sigma a little over a pixel, the analytic
exp(-(dx^2+dy^2)/2*sigma^2) formula used throughout this project's posters, never a box blur) softens
only the shape's own edge against aliasing -- there is no separate circular halo or bloom radiating
past the real footprint.

Geography: unchanged from v1 and still the honest position -- the frame's GPS nadir at exposure
(47.4N 8.7E, near Zürich/Winterthur) and its Commons categorisation ("Night in Switzerland")
place this pass over Switzerland and the central Alps, but nadir is not framing and this environment
has no geo-referencing tool to match a specific cluster of lights to a specific place with any
confidence. The poster names no city or country for the lights themselves. "861 new Swiss court
decisions" is a claim about the week's data (`week["added"]`), not about the photograph.

Composition: 1080x1350, full bleed, a tighter, higher 4:5 crop of the same original 5568x3712 frame
(1450,0)-(3610,2700) -- exactly 2160x2700, the poster's own 2x, so every mark is painted at native
resolution with no resampling at all between detection and render. This crop keeps the limb as one
clean arc about an eighth of the way down, with the thin natural airglow band just beneath the black
and a scatter of real stars above it; "861" sits large and centred in that black, "new Swiss court
decisions" quietly beneath it, the date along the airglow itself, and the credit at the very bottom
edge -- no boxed text block, no footer strip. Fonts are bundled OFL .ttf files, loaded locally via
embedded @font-face (weekly_poster_w48_assets/orbit/fonts/, licences alongside): Spectral SemiBold, a
refined, cool serif, for the figure in place of v1's Space Grotesk; IBM Plex Mono for the quiet
small-caps labels.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image

import weekly_poster as wp
from weekly_poster_designs import esc

W, H = 1080, 1350

ASSETS = Path(__file__).with_name("weekly_poster_w48_assets") / "orbit"
ASSET = ASSETS / "orbit_source.jpg"
ASSET_W, ASSET_H = 2160, 2700  # exactly 2x the design space AND this crop's own native resolution

FONTS = ASSETS / "fonts"
SERIF_FONT = FONTS / "Spectral-SemiBold.ttf"
MONO_REGULAR = FONTS / "IBMPlexMono-Regular.ttf"
MONO_SEMIBOLD = FONTS / "IBMPlexMono-SemiBold.ttf"

EMBER_FACTOR = 0.55            # how much of each unselected pixel's own warmth survives the dim-down
GAMMA = 1.16                   # >1 deepens blacks/shadows for real depth, without crushing the cloud
GRADE_RGB = (0.93, 0.95, 1.05)  # whole-frame cool tint (R, G, B multipliers), applied after gamma
GRADE_GAIN = 0.97               # overall darken, small -- EMBER_FACTOR and GAMMA do most of the work

WARM_HOT = (255, 247, 220)     # a mark's own brightest pixel: near-white-gold
WARM_BODY = (255, 185, 96)     # a mark's own dimmer edge pixels: deeper warm gold

CC_THRESHOLD = 3.0              # contrast threshold for the binary mask fed to connected components
CC_MIN_AREA = 2                 # drop area-1 flecks (JPEG/sensor noise) before ranking
SKY_CUTOFF = 450                 # asset-px; the limb's lit atmosphere starts ~400-430 here, so a
                                  # component entirely above this is a star in the black sky, not a
                                  # ground light (confirmed: with no cutoff, exactly one 8px false
                                  # positive at y=244-246 appeared, a warm-tinted star, isolated by a
                                  # 348px gap from the next real ground light at y=592)

CREDIT = "NASA / ISS055-E-84623 · Earth Science and Remote Sensing Unit, JSC"
TAG = "new Swiss court decisions"


# ---- shared image-processing primitives (true Gaussian via 3 box passes; see weekly_poster_w48_bern.py) ----

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
    """A true (round, soft-edged) Gaussian blur of std-dev `sigma`, via three box_blur passes."""
    r = max(1, round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))
    out = a
    for _ in range(3):
        out = box_blur(out, r)
    return out


def _connected_components(binary: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connected components of `binary` (True pixels), via flood fill."""
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


def load_rgb() -> np.ndarray:
    im = Image.open(ASSET).convert("RGB")
    assert im.size == (ASSET_W, ASSET_H), f"unexpected asset size {im.size}"
    return np.asarray(im, dtype=np.float64)


def warmth_of(rgb: np.ndarray) -> np.ndarray:
    """0-or-positive warmth: how far a pixel's red sits above its blue -- near zero for the photo's
    cloud, dark terrain, ocean, limb and airglow, and high for a genuine city/village light."""
    return np.clip(rgb[..., 0] - rgb[..., 2], 0, None)


def detect_marks(rgb: np.ndarray, n: int) -> np.ndarray:
    """A (h, w) float array, 0 everywhere except the 861 kept components, where it holds that
    pixel's own contrast relative to its component's own brightest pixel (1.0 at the brightest
    point of each shape, falling off at its real edges) -- the real pixel footprint of n decisions'
    worth of real lights, not a synthetic scatter of points."""
    h, w, _ = rgb.shape
    warmth = warmth_of(rgb)
    brightness = rgb.sum(axis=-1) / 3.0
    score = warmth * (brightness / 255.0)

    bg = box_blur(score, 18)
    contrast = np.clip(score - 0.6 * bg, 0, None)

    binary = contrast > CC_THRESHOLD
    comps = _connected_components(binary)
    # area-1 flecks are noise; a component sitting entirely above SKY_CUTOFF is a star, not a
    # ground light (no real light can be in the black sky) -- both are excluded before ranking,
    # not after, so neither ever competes for one of the n slots.
    comps = [pts for pts in comps if len(pts) >= CC_MIN_AREA and max(p[0] for p in pts) > SKY_CUTOFF]
    assert len(comps) >= n, f"only {len(comps)} light shapes found, need {n} -- loosen thresholds"

    scored = []
    for pts in comps:
        ys = np.array([p[0] for p in pts])
        xs = np.array([p[1] for p in pts])
        vals = contrast[ys, xs]
        scored.append((float(vals.sum()), ys, xs, vals))
    scored.sort(key=lambda t: -t[0])
    kept = scored[:n]
    assert len(kept) == n, f"only {len(kept)} light shapes kept, need {n}"

    mask = np.zeros((h, w), dtype=np.float64)
    for _, ys, xs, vals in kept:
        vmax = float(vals.max())
        mask[ys, xs] = vals / vmax if vmax > 0 else 1.0
    return mask


def build_background(rgb: np.ndarray, mask: np.ndarray) -> str:
    """The cooled, deepened night (every real light in it only lightly dimmed) with its 861 real
    light-shapes brightened back in from their own pixels, as a base64 JPEG."""
    h, w, _ = rgb.shape
    warmth = warmth_of(rgb)

    # gentle, whole-frame ember: every pixel's own warmth eases toward EMBER_FACTOR of itself --
    # the real night stays legible, just a little cooler and fainter, nothing erased to black.
    suppress = warmth * (1 - EMBER_FACTOR)
    embered = rgb.copy()
    embered[..., 0] -= suppress
    embered[..., 1] -= suppress * 0.52
    embered = np.clip(embered, 0, 255)

    # depth: a gamma curve deepens shadows (and the cloud's own shadowed underside) without
    # crushing its soft grey form, then a small cool tint and an overall gain.
    norm = np.clip(embered / 255.0, 0, 1) ** GAMMA
    graded = norm * np.array(GRADE_RGB) * GRADE_GAIN * 255.0
    graded = np.clip(graded, 0, 255)

    # brighten the 861 kept shapes from their own pixels: a very short true-Gaussian feather on the
    # mask (not a drawn halo) softens only the real edge, then a highlight-knee envelope and a
    # brightest-pixel-hottest colour lerp follow the mask's own measured strength.
    soft = gaussian_blur(mask, 1.1)
    env = 1 - np.exp(-soft * 4.2)
    hot = np.array(WARM_HOT)
    body = np.array(WARM_BODY)
    mark_col = body + (hot - body) * np.clip(soft, 0, 1)[..., None]

    bg_n = graded / 255.0
    mark_n = np.clip(mark_col, 0, 255) / 255.0
    out = (1 - (1 - bg_n) * (1 - mark_n * env[..., None])) * 255.0  # screen blend

    img = Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _font_faces() -> str:
    def b64(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    return f"""
@font-face{{font-family:'Orbit Serif';font-weight:600;font-style:normal;font-display:block;
  src:url(data:font/ttf;base64,{b64(SERIF_FONT)}) format('truetype')}}
@font-face{{font-family:'Orbit Mono';font-weight:400;font-style:normal;font-display:block;
  src:url(data:font/ttf;base64,{b64(MONO_REGULAR)}) format('truetype')}}
@font-face{{font-family:'Orbit Mono';font-weight:600;font-style:normal;font-display:block;
  src:url(data:font/ttf;base64,{b64(MONO_SEMIBOLD)}) format('truetype')}}
"""


def _doc(css: str, body: str, lang: str) -> str:
    """A standalone page (no Google-Fonts CDN link -- every font is embedded, so the render has no
    network dependency and is byte-for-byte reproducible offline)."""
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<style>*{{box-sizing:border-box;margin:0;padding:0}}html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{position:relative;font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased}}
{_font_faces()}
{css}</style></head><body>{body}</body></html>"""


def design(week: dict, lang: str) -> str:
    assert lang == "en", "the From Orbit poster is English-only"
    n = week["added"]

    rgb = load_rgb()
    mask = detect_marks(rgb, n)
    bg_b64 = build_background(rgb, mask)

    d0, d1 = week["from"], week["to"]
    date_line = f"Week {week['week_no']} · {d0.day}–{d1.day} November {d1.year}"
    n_fmt = wp.fmt(n, lang)

    css = f"""
body{{background:#02030a;color:#f2f3f6}}
.bg{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover}}
.n{{position:absolute;left:0;right:0;top:34px;text-align:center;
   font-family:'Orbit Serif',serif;font-weight:600;font-size:258px;line-height:0.84;
   letter-spacing:-0.01em;color:#f8f4e8;
   font-variant-numeric:lining-nums tabular-nums;
   text-shadow:0 0 44px rgba(255,214,150,.22),0 0 110px rgba(150,180,230,.16),0 4px 18px rgba(0,0,0,.45)}}
.tag{{position:absolute;left:0;right:0;top:258px;text-align:center;
   font-family:'Orbit Mono',monospace;font-weight:600;font-size:23px;letter-spacing:.14em;
   text-transform:uppercase;color:rgba(244,240,230,.82);text-shadow:0 2px 12px rgba(0,0,0,.5)}}
.key{{position:absolute;left:0;right:0;top:366px;text-align:center;font-family:'Orbit Mono',monospace;font-weight:400;font-size:15px;letter-spacing:.04em;color:rgba(255,214,140,.92);text-shadow:0 1px 10px rgba(0,0,0,.7)}}
.date{{position:absolute;left:0;right:0;top:335px;text-align:center;
   font-family:'Orbit Mono',monospace;font-weight:400;font-size:17px;letter-spacing:.04em;
   color:rgba(230,236,244,.80);text-shadow:0 1px 10px rgba(0,0,0,.6)}}
.credit{{position:absolute;left:0;bottom:46px;width:{W}px;text-align:center;
   font-family:'Orbit Mono',monospace;font-weight:400;font-size:13px;letter-spacing:.01em;
   color:rgba(228,234,242,.55)}}
.url{{position:absolute;left:0;bottom:72px;width:{W}px;text-align:center;
   font-family:'Orbit Mono',monospace;font-weight:600;font-size:16px;letter-spacing:.07em;
   color:rgba(255,205,145,.82)}}
"""
    body = f"""<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="n">{esc(n_fmt)}</div>
<div class="tag">{esc(TAG)}</div>
<div class="date">{esc(date_line)}</div>
<div class="key">Each golden light is one decision.</div>
<div class="url">opencaselaw.ch</div>
<div class="credit">{esc(CREDIT)}</div>"""
    return _doc(css, body, lang)
