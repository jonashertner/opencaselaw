"""Week 48 (20 - 26 November 2026), Italian: Mezzogiorno sulle Alpi (v6 -- a dense field, not flecks).

Giovanni Segantini, "Mezzogiorno sulle Alpi" / "High Noon in the Alps", 1892, oil on canvas -- Ohara
Museum of Art, Kurashiki (see weekly_poster_w48_assets/mezzogiorno/SOURCES.json; the Commons file's own
categories place THIS canvas, not Segantini's other 1891 version in St. Moritz, in the Ohara Museum --
checked before crediting it). The canvas supplies the whole image: a full-bleed 1080x1350 crop of the
public-domain Google Art Project scan (4373x4692, downloaded at full resolution), trimmed only on the
sides to the poster's 4:5 shape, keeping the shepherdess, the birch she leans on and her sheep exactly
as Segantini painted them. Nothing is drawn on top of the canvas: the only change is to 861 of his own
divisionist brushstrokes, described below.

v1 (weekly_poster_w48_mezzogiorno_v1.py) got the idea right but the figure read as a brown smudge: too
small, the wrong colour, too sparse at 540px/270px. v2 (weekly_poster_w48_mezzogiorno_v2.py) fixed size
and density with a big true-Gaussian bloom and a near-white cream core -- legible, but review called it
"fluffy cream clouds or wool, a glowing-font effect", the sky had gone dusky navy, and the numerals sat
flush against the top edge with "SETTIMANA 48" colliding into the "8". v3 fixes all three without
giving up the legibility v2 found:
  - colour: the core is now a crisp, saturated warm gold (255,212,110) grading up to a pale highlight
    only at each stroke's own brightest top-hat pixels -- not a flat tint -- and the bloom is cut from
    sigma 7 (a diffuse cloud) to sigma 3 with a lower gain (a faint cohesion between neighbours, not a
    glow); legibility lost to the smaller bloom is recovered with a finer selection grid and slightly
    more candidates per cell instead of bringing the glow back, per the brief for this round.
  - sky: deepened only 12% now (SKY_DARKEN=0.88, was 0.66) and the "tone down stray sparkle" pass is
    kept tight to a narrow band around the glyphs (25px, was 50px) so it can't dull the sky generally --
    checked against a full view that it still reads as Segantini's brilliant midday blue, not dusk.
  - layout: the numeral's top margin is now 100px (50px at 1x), clearly off the canvas edge, and the
    old top-left "SETTIMANA 48" meta block is gone -- "Settimana 48 · 20-26 novembre 2026" and the
    count line now sit as two centred lines directly below the numerals, above the mountains, shadow-
    free, over a small soft local darkening (not a shadow stack) the same baked-ellipse technique as
    the bottom credit pool, just fainter.

v3 (weekly_poster_w48_mezzogiorno_v3.py) was "good, now make it excellent": the three fixes above held,
but (1) the digits were tracked so wide (extra gap stretched to hit a width target) that "861" read as
three separate patches rather than one figure, (2) the strokes inside each digit were a near-uniform
gold at a near-uniform size -- legible, but flat, like an LED sign rather than paint catching light, and
(3) the two text lines under the figure crowded the mountain ridge and the shepherdess's hat. v4:
  - spacing: `_glyph_mask` no longer stretches to a target width; it uses a small fixed GLYPH_GAP (10px,
    close to the font's own natural advance) between characters, and the cap height is back up to ~72%
    of the sky band (GLYPH_CAP_HEIGHT=455, within the 72-75% asked for) now that spacing isn't fighting
    for the same room. The three digits read as one number again (checked against a grey-on-black mask
    render, not just the final composite, to confirm it before adding any strokes).
  - painted light: each selected stroke is now graded as ONE piece by its own SOURCE luminance (the
    original painted pixel brightness, before any top-hat processing) -- not by local contrast -- mapped
    through a 3-stop gradient (a deeper gold for the dimmer strokes, the main warm gold for most, a pale
    near-white only for the brightest tail), with a small amount of residual per-pixel texture on top so
    a single stroke isn't perfectly flat either. `_quality` now weights elongation far more heavily than
    area, so the selection favours genuinely stroke-shaped (long, thin) marks over near-round flecks,
    which is what reads as "brushwork" rather than a dot-matrix fill.
  - text: the under-figure caption is cut to one short line, "Settimana 48 · 20-26 novembre 2026", with
    real air above the ridge (the glyph's own smaller footprint freed up room); the decisions line moved
    down to the meadow foot, grouped with the credit and the URL, clear of the ridge and the hat entirely.

v4 (weekly_poster_w48_mezzogiorno_v4.py) kept the spacing/size/text fixes above, but regressed the
strokes themselves: the per-stroke source-luminance grading had a sign error (a gamma>1 on an already
0-1-normalised value pulls the MIDDLE of the range down, not up, so most strokes landed near
STROKE_DEEP=(222,145,55) instead of the intended STROKE_MID -- that deep stop reads as orange/ochre at
any real share of the total), and `_quality`'s heavy elongation weighting had no cap and no minimum
thickness or fill-ratio floor, so a handful of thin, sparse, diagonal 4-connected components (an
accidental scratch across several unrelated strokes, not a real single brushstroke) scored very highly
and were picked -- visible as the "ragged patch" in the "1", which has the least glyph area and so the
fewest alternative candidates to fall back on. v5:
  - colour: STROKE_DEEP raised to (242,196,96) -- a muted gold, never below that, never orange -- and
    the grading is now by RANK (each selected stroke's percentile position among the 861 by source
    luminance, which is uniform by construction) instead of a gamma-warped min-max, with explicit
    breakpoints (bottom 15% graded towards STROKE_DEEP, top 15% towards STROKE_PALE, the middle 70%
    flat STROKE_MID) -- so the distribution is controlled directly rather than emerging from whatever
    shape the gamma happened to produce.
  - shape: `_components` now also reports each component's fill ratio (area / bounding-box area).
    `good` requires GOOD_DIM_MIN=3 (excludes degenerate 1-2px slivers) and GOOD_FILL_MIN=0.35 (excludes
    sparse, jagged multi-stroke scratches that only look elongated because of their bounding box).
    `_quality`'s elongation term is now capped (ELONGATION_CAP=5.0) so one extreme sliver can no longer
    outrank a genuine, merely-quite-elongated brushstroke. The shared halo is cut again (sigma 3 -> 2.2,
    gain 7 -> 5) towards "no blur or softening on the stroke pixels themselves".
  - the "1": checked directly (not just inferred) that the stratified selection gives it close to its
    proportional share of the 861 -- its glyph area is ~26% of the total ink area and it receives ~27%
    of the selected strokes, the same density as the 8 and the 6, with a clean vertical, flag and foot.

v5 (weekly_poster_w48_mezzogiorno_v5.py) was crisp and clean but, measured directly, covered only ~47%
of each glyph's own ink area (strokes + their halo, against a 0.15 visibility cutoff on the halo) --
Jonas's "close up the digits look sparse, with too much blue between the strokes" is exactly that
number read back. 861 marks spread over v5's cap height (72% of the sky band) at v5's average stroke
area (~32px^2) cannot cover much more than that; getting to the ~55-65% asked for needed all three
levers the brief named, in order, not just one:
  - (a) `_quality` is now area-first (`area * (1 + 0.15 * min(elongation, 6))`, elongation a bonus not
    the driver) and the shape filters were loosened to admit the real stroke population this favours:
    GOOD_AREA_MAX 160->300, GOOD_DIM_MAX 45->60, plus a new GOOD_ELONGATION_MIN=1.3 (still "real,
    still elongated", just not elongation-dominant). Mean selected stroke area rose from ~32 to ~38px^2.
  - (c) TOPHAT_THRESH lowered 8.0->5.0, which admits the larger, more complete stroke shapes whose
    weaker edges fall just under the old threshold (this is what let (a) find bigger candidates at all
    -- raising the area ceiling alone doesn't help if nothing bigger was ever detected).
  - (b) GLYPH_CAP_HEIGHT brought back down to 420 (66% of the sky band, inside the "~62-68% if needed"
    the brief allowed), and GLYPH_GAP tightened 10->8 -- a smaller glyph needs less total gold to cover
    the same fraction of its own ink.
  Nothing is drawn or dilated: every selected pixel is still exactly the shape the paint itself has.
  What changed is which real strokes get selected (bigger, still genuinely stroke-shaped) and how much
  glyph they have to fill. HALO_SIGMA/HALO_GAIN are unchanged from v5 (2.2, 5.0) -- the halo was already
  "tiny"; it just had far too little core gold behind it to read as a field rather than scattered
  flecks. Measured coverage at these settings: 57.5% (half-way through the 55-65% target), reproduced
  by the module's own `__main__` sanity block below.

The sky is thousands of short, near-white and pale-blue divisionist strokes -- Segantini's own broken
brushwork for "brilliant Alpine light", not a flat wash. Detection (a top-hat filter, exactly as the
brief prescribes: luminance minus a true Gaussian blur): `sky_mask_of`'s colour test (blue, not the
white of the mountains or the birch's leaves, restricted to rows above the painted horizon) isolates
the sky; `lum - gaussian_blur(lum, 3.2)` then finds every pixel that stands out above its own local
neighbourhood, i.e. every stroke highlight, not just "anything bright". Thresholded, the result is
grouped into its real connected ink components (4-connected, via iterative vectorised label
propagation, not synthetic dots) -- tens of thousands of them -- then kept only when the mark itself is
a plausible single brushstroke (bounded area and longest side), rejecting noise flecks and the rare
merged blob, the same logic weekly_poster_w48_bern.py uses for window ticks.

The figure: "861" is set in Archivo Black (Google Fonts, OFL) -- v1 used Anton, whose numeral counters
are too narrow for an "8" built from sparse real strokes to read; Archivo Black's are wide and open.
It is rasterised once, as a MASK only (no glyph outline is ever drawn onto the poster) deciding which
strokes qualify. Stroke components whose centre falls inside that mask are split into a spatial grid
and drawn from round-robin, cell by cell, by quality (area, with a bonus for elongation) -- not a flat
top-n-by-quality over the whole glyph, which let the naturally busier patches of sky dominate and left
thin patches of the glyph empty at v1's smaller size. `assert len(selected) == week["added"]`. Each
keeps its own exact pixel shape -- at 100% each digit is a field of individual golden brush strokes,
not a synthetic dot or a blurred mass.

The credit line and opencaselaw.ch sit over a small soft darkened pool baked into the meadow itself
(a blurred ellipse, not a flat rectangle, so it reads as shade rather than a footer band) instead of a
stacked drop-shadow.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350

ASSETS = Path(__file__).with_name("weekly_poster_w48_assets") / "mezzogiorno"
ASSET = ASSETS / "mezzogiorno_source.jpg"
FONT_PATH = ASSETS / "ArchivoBlack-Regular.ttf"
ASSET_W, ASSET_H = 2160, 2700  # exactly 2x the design space

HORIZON = 632             # asset px row where the painted mountains begin
TOPHAT_SIGMA = 3.2        # true-Gaussian radius for the local-neighbourhood average
TOPHAT_THRESH = 5.0       # luminance units above that local average to count as a stroke highlight --
                          # lowered from v5's 8.0 so bigger, more complete stroke shapes (whose weaker
                          # edges fell under the old threshold) are detected at all; see GOOD_AREA_MAX
SUPPRESS_THRESH = 6.0     # lower bar: "any natural sparkle" worth toning down near the glyphs
GOOD_AREA_MAX = 300       # a plausible single brushstroke, asset px^2 -- raised from v5's 160 so the
                          # now area-first `_quality` has real bigger strokes to select (density lever a)
GOOD_DIM_MAX = 60         # its longest side, asset px -- raised from v5's 45 to match
GOOD_DIM_MIN = 3          # its shortest side, asset px -- excludes degenerate 1-2px slivers
GOOD_FILL_MIN = 0.35      # area / bounding-box area -- excludes sparse, jagged multi-stroke scratches
                          # that only look elongated because of their bounding box, not their own ink
GOOD_ELONGATION_MIN = 1.3 # still "real, still elongated" even though quality is now area-first --
                          # screens out near-round flecks before area gets to pick the biggest of them

GLYPH_CAP_HEIGHT = 420    # asset px -- ~66% of the HORIZON=632 sky band, inside the "~62-68% if needed"
                          # the brief allowed (density lever b) -- brought down from v5's 455 (72%) so
                          # the same 861 marks have less glyph area to cover
GLYPH_TOP = 100           # asset px (50px at 1x design scale) -- a clear top margin off the canvas
                          # edge; with GLYPH_CAP_HEIGHT above, the block is ~centred in the sky band
GLYPH_GAP = 8             # asset px between digits -- tightened slightly from v5's 10, still close to
                          # the font's own natural advance ("normal or slightly tight tracking")
CELL = 18.0               # stratified-selection grid cell, asset px -- finer than v5's 20 to match the
                          # smaller glyph

SKY_DARKEN = 0.88         # unlit sky multiplier -- v2's 0.66 read as dusk; this keeps Segantini's
                          # brilliant midday blue and relies on the strokes' own saturation instead
SUPPRESS_STRENGTH = 0.80  # how hard unselected sparkle near/inside the glyphs is pulled to local blue
SUPPRESS_BLUR = 35.0      # radius for "local sky colour" that suppressed flecks blend towards
SUPPRESS_MARGIN = 25      # asset px the suppression region extends past the glyph's own bbox -- a
                          # narrow band only, so it can't dull the sky generally (review's "limit the
                          # fleck suppression to the glyphs and a narrow band around them")

# painted light: each selected stroke is graded as ONE piece (not per pixel) by its own RANK among the
# 861, by source luminance (the original painted brightness, before any top-hat processing) -- rank,
# not the raw value through a gamma curve (v4's bug: a gamma>1 on an already-normalised 0-1 value pulls
# the MIDDLE of the range down, not up, so most strokes landed near STROKE_DEEP and read orange/ochre).
# Explicit breakpoints instead: bottom 15% by rank graded towards STROKE_DEEP, top 15% towards
# STROKE_PALE, the middle 70% flat STROKE_MID -- the bulk of the figure is the main warm gold, never
# orange (STROKE_DEEP itself is a muted gold floor, not orange).
STROKE_DEEP = np.array([242, 196, 96], dtype=np.float64)
STROKE_MID = np.array([255, 221, 130], dtype=np.float64)
STROKE_PALE = np.array([255, 248, 225], dtype=np.float64)
STROKE_RANK_DEEP = 0.15   # bottom this fraction (by source-luminance rank) grades towards STROKE_DEEP
STROKE_RANK_PALE = 0.85   # top (1 - this fraction) grades towards STROKE_PALE
STROKE_TEXTURE = 0.10     # +/- this fraction of per-pixel brightness variation within one stroke
                          # (from its own top-hat value), so a single stroke isn't perfectly flat --
                          # kept small: this is texture, not the blur/softening review asked to drop

HALO = np.array([250, 210, 130], dtype=np.float64)       # the faint shared halo, warmer/deeper than core
HALO_SIGMA = 2.2          # unchanged from v5 -- "tiny"; it was never the halo that was too small, it
HALO_GAIN = 5.0           # was the core gold behind it (see GOOD_AREA_MAX / GLYPH_CAP_HEIGHT above)

ELONGATION_BONUS_CAP = 6.0  # `_quality`'s elongation bonus saturates here -- area is the primary term
                            # now (density lever a), elongation only a tie-breaker between similarly
                            # sized candidates, so one extreme sliver still can't win on shape alone
COVERAGE_VIS_THRESH = 0.15  # halo opacity above which a glyph pixel counts as "covered", for the
                            # coverage figure the module's own sanity block reports

POOL_CENTER = (2580, 1080)   # asset px -- the soft shadow pool behind the bottom decisions/credit/url
POOL_RADIUS = (150, 780)     # (ry, rx) asset px -- taller than v3's (110,760): now covers three lines
                             # (the decisions line moved down here per review point 3) not two
POOL_BLUR = 30.0
POOL_STRENGTH = 0.52

CAPTION_POOL_RADIUS = (34, 300)   # (ry, rx) asset px -- a much smaller, fainter local tone behind the
CAPTION_POOL_BLUR = 18.0          # single short week/date line under the numerals now (review: "one
CAPTION_POOL_STRENGTH = 0.16      # short line with air"; most of the old scrim is no longer needed)

CREDIT = "Giovanni Segantini, Mezzogiorno sulle Alpi, 1892 · Ohara Museum of Art, Kurashiki"
WEEK_LABEL = "Settimana {w}"
COUNT_LINE = "{n} nuove decisioni dei tribunali svizzeri"


# ---- image helpers (true Gaussian via three box passes -- see weekly_poster_w48_bern.py) --------

def box_blur(a: np.ndarray, r: int) -> np.ndarray:
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
    """A true (round) Gaussian blur via three box_blur passes -- one pass alone has a square
    footprint; three in a row already look convincingly round (central-limit territory)."""
    r = max(1, round((math.sqrt(4 * sigma * sigma + 1) - 1) / 2))
    out = a
    for _ in range(3):
        out = box_blur(out, r)
    return out


def _label_components(mask: np.ndarray) -> np.ndarray:
    """4-connected component labels via iterative vectorised min-neighbour propagation (see
    weekly_poster_w48_bern.py's docstring in the sibling module for why this is vectorised rather
    than a per-pixel flood fill: the sky band here spans the full poster width, not a few small
    hand-traced boxes)."""
    h, w = mask.shape
    idx = np.arange(h * w, dtype=np.int64).reshape(h, w)
    big = h * w + 10
    work = np.where(mask, idx, big)
    while True:
        new = work.copy()
        new[1:, :] = np.minimum(new[1:, :], np.where(mask[1:, :] & mask[:-1, :], work[:-1, :], big))
        new[:-1, :] = np.minimum(new[:-1, :], np.where(mask[:-1, :] & mask[1:, :], work[1:, :], big))
        new[:, 1:] = np.minimum(new[:, 1:], np.where(mask[:, 1:] & mask[:, :-1], work[:, :-1], big))
        new[:, :-1] = np.minimum(new[:, :-1], np.where(mask[:, :-1] & mask[:, 1:], work[:, 1:], big))
        if np.array_equal(new, work):
            return np.where(mask, work, -1)
        work = new


def _components(binary: np.ndarray) -> list[dict]:
    """One dict per connected component of `binary` (True pixels): area, bounding-box height/width,
    fill ratio (area / bounding-box area -- low means a sparse, jagged shape, not a solid brushstroke),
    centroid, and its own exact pixel coordinates (ys, xs) -- never a synthetic shape."""
    labels = _label_components(binary)
    ys, xs = np.nonzero(labels >= 0)
    lbl = labels[ys, xs]
    order = np.argsort(lbl)
    ys, xs, lbl = ys[order], xs[order], lbl[order]
    _, starts = np.unique(lbl, return_index=True)
    starts = list(starts) + [len(lbl)]
    comps = []
    for i in range(len(starts) - 1):
        s0, s1 = starts[i], starts[i + 1]
        cy, cx = ys[s0:s1], xs[s0:s1]
        y0, y1, x0, x1 = cy.min(), cy.max(), cx.min(), cx.max()
        bh, bw = int(y1 - y0 + 1), int(x1 - x0 + 1)
        area = int(s1 - s0)
        comps.append({
            "area": area, "bh": bh, "bw": bw, "fill": area / (bh * bw),
            "cy": float(cy.mean()), "cx": float(cx.mean()), "ys": cy, "xs": cx,
        })
    return comps


def load_asset() -> np.ndarray:
    im = Image.open(ASSET).convert("RGB")
    assert im.size == (ASSET_W, ASSET_H), f"unexpected asset size {im.size}"
    return np.asarray(im, dtype=np.float64)


def sky_mask_of(arr: np.ndarray) -> np.ndarray:
    """True over the painted sky only: blue (B well above R, bright, not the white of the mountains
    or the birch's own leaves) and above the painted horizon (row 632 of this crop at 2x)."""
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mask = (b - r > 30) & (b > 110) & (r < 180)
    mask[HORIZON:, :] = False
    return mask


def _glyph_mask(n: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """The numeral's mask (Archivo Black, very heavy, wide-countered) used only to decide which real
    strokes qualify -- never drawn onto the poster itself. The font is sized to GLYPH_CAP_HEIGHT and
    each digit is drawn with GLYPH_GAP of extra spacing (small, close to the font's own natural
    advance) -- NOT stretched to hit a width target, which at v3's ~230px gap made "861" read as three
    separate patches instead of one figure."""
    s = str(n)
    probe = ImageFont.truetype(str(FONT_PATH), size=500)
    probe_bbox = ImageDraw.Draw(Image.new("L", (10, 10))).textbbox((0, 0), s, font=probe)
    size = round(500 * GLYPH_CAP_HEIGHT / (probe_bbox[3] - probe_bbox[1]))
    font = ImageFont.truetype(str(FONT_PATH), size=size)
    d = ImageDraw.Draw(Image.new("L", (10, 10)))
    widths = [d.textbbox((0, 0), ch, font=font)[2] - d.textbbox((0, 0), ch, font=font)[0] for ch in s]
    n_gaps = max(1, len(s) - 1)
    total_w = sum(widths) + GLYPH_GAP * n_gaps

    canvas = Image.new("L", (ASSET_W, ASSET_H), 0)
    draw = ImageDraw.Draw(canvas)
    full_bbox = d.textbbox((0, 0), s, font=font)
    x = (ASSET_W - total_w) / 2
    y = GLYPH_TOP - full_bbox[1]
    for ch in s:
        b = d.textbbox((0, 0), ch, font=font)
        draw.text((x - b[0], y), ch, font=font, fill=255)
        x += (b[2] - b[0]) + GLYPH_GAP

    glyph = np.asarray(canvas) > 128
    ys, xs = np.nonzero(glyph)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    return glyph, bbox


def _elongation(c: dict) -> float:
    return max(c["bh"], c["bw"]) / max(1, min(c["bh"], c["bw"]))


def _quality(c: dict) -> float:
    """Density lever (a): area-first now -- each mark carries more gold, which is what closes the gap
    between strokes that v5 left (measured coverage ~47%, below the 55-65% asked for). Elongation is
    still a bonus, so between two similarly-sized candidates the more stroke-shaped one wins, but it no
    longer dominates the ranking the way v5's elongation-first formula did (that let a handful of thin,
    sparse diagonal scratches outrank properly-filled strokes -- v4/v5's "ragged patch" failure mode).
    `good`'s own GOOD_FILL_MIN and GOOD_ELONGATION_MIN already screen out non-strokes before quality
    ever sees them, so this is purely about which REAL strokes carry the most gold."""
    return c["area"] * (1 + 0.15 * min(_elongation(c), ELONGATION_BONUS_CAP))


def _stratified_select(candidates: list[dict], n: int, cell: float, seed: int = 48) -> list[dict]:
    """n candidates spread evenly across the glyph's own filled area: candidates are bucketed into a
    `cell`x`cell` spatial grid, each bucket's own candidates ranked by `_quality`, then buckets are
    drawn from round-robin (one best-remaining candidate per bucket per round) so no single
    naturally-busier patch of sky can fill the quota while a thinner patch of the glyph is left empty
    -- the failure mode a flat top-n-by-quality had at v1's smaller glyph size."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for c in candidates:
        key = (int(c["cx"] // cell), int(c["cy"] // cell))
        buckets.setdefault(key, []).append(c)
    for v in buckets.values():
        v.sort(key=_quality, reverse=True)
    keys = list(buckets.keys())
    random.Random(seed).shuffle(keys)  # round order independent of raster/label order
    selected: list[dict] = []
    ptr = {k: 0 for k in keys}
    while len(selected) < n:
        progressed = False
        for k in keys:
            if len(selected) >= n:
                break
            p = ptr[k]
            if p < len(buckets[k]):
                selected.append(buckets[k][p])
                ptr[k] = p + 1
                progressed = True
        if not progressed:
            break
    return selected


def detect_strokes(arr: np.ndarray, n: int) -> tuple[list[dict], tuple[int, int, int, int]]:
    """n real stroke components, one per decision, spread across the '861' mask (see
    _stratified_select); raises with a clear message if the glyph doesn't hold n of them."""
    sky = sky_mask_of(arr)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    tophat = lum - gaussian_blur(lum, TOPHAT_SIGMA)
    binary = (tophat > TOPHAT_THRESH) & sky
    comps = _components(binary[:HORIZON + 4, :])
    good = [c for c in comps if 4 <= c["area"] <= GOOD_AREA_MAX
            and GOOD_DIM_MIN <= min(c["bh"], c["bw"]) and max(c["bh"], c["bw"]) <= GOOD_DIM_MAX
            and c["fill"] >= GOOD_FILL_MIN and _elongation(c) >= GOOD_ELONGATION_MIN]

    glyph, bbox = _glyph_mask(n)
    inside = [c for c in good if glyph[int(round(c["cy"])), int(round(c["cx"]))]]
    assert len(inside) >= n, (
        f"only {len(inside)} stroke candidates fall inside the '{n}' mask, need {n} -- "
        "widen GLYPH_CAP_HEIGHT, lower TOPHAT_THRESH, or loosen the component filter"
    )
    selected = _stratified_select(inside, n, CELL)
    assert len(selected) == n, "one lit stroke per decision"
    coverage = _coverage_of(glyph, selected, arr.shape[:2])
    assert 0.55 <= coverage <= 0.65, (
        f"stroke+halo coverage {coverage:.1%} of the glyph's own ink is outside the 55-65% target -- "
        "adjust GOOD_AREA_MAX/_quality (lever a), GLYPH_CAP_HEIGHT (lever b) or TOPHAT_THRESH (lever c)"
    )
    return selected, bbox


def _sel_mask_of(selected: list[dict], shape: tuple[int, int]) -> np.ndarray:
    sel_mask = np.zeros(shape, dtype=bool)
    for c in selected:
        sel_mask[c["ys"], c["xs"]] = True
    return sel_mask


def _halo_env_of(sel_mask: np.ndarray) -> np.ndarray:
    """The shared halo's opacity: a true-Gaussian blur of the selected pixels' own footprint, tonemapped
    with a highlight knee so overlapping strokes merge into one field without blowing out."""
    halo_amp = gaussian_blur(sel_mask.astype(np.float64), HALO_SIGMA)
    return 1 - np.exp(-halo_amp * HALO_GAIN)


def _coverage_of(glyph: np.ndarray, selected: list[dict], shape: tuple[int, int]) -> float:
    """Fraction of the glyph's own ink pixels that are "covered": either an exact selected stroke
    pixel, or within the shared halo's visible reach (COVERAGE_VIS_THRESH). This is the figure the
    brief asks to be measured and reported, not estimated."""
    sel_mask = _sel_mask_of(selected, shape)
    halo_env = _halo_env_of(sel_mask)
    covered = glyph & (sel_mask | (halo_env > COVERAGE_VIS_THRESH))
    return float(covered.sum()) / float(glyph.sum())


def build_background(arr: np.ndarray, selected: list[dict], glyph_bbox: tuple[int, int, int, int]) -> str:
    """The relit JPEG background: the unlit sky deepened, Segantini's own unselected sparkle inside
    and around the glyphs toned down towards the local sky blue, the `selected` strokes graded as
    painted light (see STROKE_DEEP/MID/PALE above) with a restrained shared halo, a small soft
    shadow-pool baked into the meadow behind the decisions/credit/url block, and a much fainter one
    behind the single week/date line under the numerals."""
    h, w, _ = arr.shape
    sky = sky_mask_of(arr)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    tophat = lum - gaussian_blur(lum, TOPHAT_SIGMA)

    out = arr.copy()
    out[sky] = out[sky] * SKY_DARKEN

    sel_mask = _sel_mask_of(selected, (h, w))

    # tone down Segantini's own unselected sparkle inside/around the glyph bbox towards the local
    # (already-deepened) sky colour, so only the 861 chosen strokes read as lit -- this is what keeps
    # the open counters of the 8/6 dark and clean rather than freckled.
    gx0, gy0, gx1, gy1 = glyph_bbox
    supp_region = np.zeros((h, w), dtype=bool)
    y0, y1 = max(0, gy0 - SUPPRESS_MARGIN), min(h, gy1 + SUPPRESS_MARGIN)
    x0, x1 = max(0, gx0 - SUPPRESS_MARGIN), min(w, gx1 + SUPPRESS_MARGIN)
    supp_region[y0:y1, x0:x1] = True
    supp_region &= sky
    natural_bright = (tophat > SUPPRESS_THRESH) & ~sel_mask & supp_region
    local = np.stack([gaussian_blur(out[..., i], SUPPRESS_BLUR) for i in range(3)], axis=-1)
    out[natural_bright] = (out[natural_bright] * (1 - SUPPRESS_STRENGTH)
                            + local[natural_bright] * SUPPRESS_STRENGTH)

    # the faint shared halo first, so immediate neighbours read as one dense field rather than isolated
    # flecks, then each stroke's own colour on top
    halo_env = _halo_env_of(sel_mask)
    glow = HALO[None, None, :] * halo_env[..., None]
    out = out * (1 - halo_env[..., None]) + glow

    # painted light: grade each selected stroke AS ONE PIECE by its own RANK among the 861, by source
    # luminance (the original painted brightness at its own pixels, before top-hat processing) -- not
    # local contrast, and not the raw value through a gamma curve (v4's bug -- see the module
    # docstring). Rank is uniform on [0, 1] by construction, so the explicit STROKE_RANK_DEEP/PALE
    # breakpoints directly control what fraction of strokes reach each end, rather than that fraction
    # emerging from whatever shape a gamma curve happens to produce on this week's actual luminances.
    lumsrcs = np.array([float(lum[c["ys"], c["xs"]].mean()) for c in selected])
    ranks = np.empty(len(selected))
    ranks[np.argsort(lumsrcs)] = np.arange(len(selected)) / max(1, len(selected) - 1)
    sel_color = np.zeros((h, w, 3), dtype=np.float64)
    for c, rank in zip(selected, ranks):
        if rank < STROKE_RANK_DEEP:
            base = STROKE_DEEP + (STROKE_MID - STROKE_DEEP) * (rank / STROKE_RANK_DEEP)
        elif rank > STROKE_RANK_PALE:
            base = STROKE_MID + (STROKE_PALE - STROKE_MID) * ((rank - STROKE_RANK_PALE) / (1 - STROKE_RANK_PALE))
        else:
            base = STROKE_MID
        # a little residual per-pixel texture (from the stroke's own top-hat value) keeps a single
        # stroke from reading perfectly flat -- texture, not the blur/softening review asked to drop
        local_t = np.clip((tophat[c["ys"], c["xs"]] - SUPPRESS_THRESH) / 30.0, 0, 1)
        variation = (1 - STROKE_TEXTURE) + 2 * STROKE_TEXTURE * local_t
        sel_color[c["ys"], c["xs"]] = base[None, :] * variation[:, None]
    out[sel_mask] = sel_color[sel_mask]

    # a small soft shadow pool baked into the meadow (a blurred ellipse, not a flat band) behind the
    # bottom credit/url text, so that text reads at 540px without a sticker-like drop shadow
    cy, cx = POOL_CENTER
    ry, rx = POOL_RADIUS
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    hard = (((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 < 1.0).astype(np.float64)
    soft = gaussian_blur(hard, POOL_BLUR)
    out = out * (1 - POOL_STRENGTH * soft)[..., None]

    # a much fainter local tone behind the two centred lines under the numerals, above the mountains
    cy2, cx2 = gy1 + 95, ASSET_W / 2
    ry2, rx2 = CAPTION_POOL_RADIUS
    hard2 = (((yy - cy2) / ry2) ** 2 + ((xx - cx2) / rx2) ** 2 < 1.0).astype(np.float64)
    soft2 = gaussian_blur(hard2, CAPTION_POOL_BLUR)
    out = out * (1 - CAPTION_POOL_STRENGTH * soft2)[..., None]

    out = np.clip(out, 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


MONTH_IT = {1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile", 5: "maggio", 6: "giugno",
            7: "luglio", 8: "agosto", 9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre"}


def design(week: dict, lang: str) -> str:
    assert lang == "it", "the Mezzogiorno sulle Alpi poster is Italian-only"
    n = week["added"]

    arr = load_asset()
    selected, glyph_bbox = detect_strokes(arr, n)
    bg_b64 = build_background(arr, selected, glyph_bbox)

    d0, d1 = week["from"], week["to"]
    week_date_line = f"{WEEK_LABEL.format(w=week['week_no'])} · {d0.day}–{d1.day} {MONTH_IT[d1.month]} {d1.year}"
    count_line = COUNT_LINE.format(n=wp.fmt(n, "it"))

    glyph_bottom_design = glyph_bbox[3] / 2  # asset px -> design px

    css = f"""
body{{background:#0b2a4a;color:#fdf6e3}}
.bg{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover}}
.weekdate{{position:absolute;left:0;width:{W}px;top:{glyph_bottom_design + 13:.0f}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:600;font-size:16px;letter-spacing:.04em;
   color:#fdf6e3}}
.key{{position:absolute;left:0;bottom:90px;width:{W}px;text-align:center;font-family:'Fraunces',serif;font-style:italic;font-weight:480;font-size:16px;color:#f6d98a;text-shadow:0 1px 4px rgba(0,0,0,.6)}}
.count{{position:absolute;left:0;bottom:114px;width:{W}px;text-align:center;
   font-family:'Fraunces',serif;font-style:italic;font-weight:480;font-variation-settings:'opsz' 20;
   font-size:19px;letter-spacing:.01em;color:#fdf3dd;text-shadow:0 1px 4px rgba(0,0,0,.5)}}
.credit{{position:absolute;left:0;bottom:42px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:400;font-size:13px;letter-spacing:.01em;
   color:rgba(255,250,235,.95);text-shadow:0 1px 4px rgba(0,0,0,.55)}}
.url{{position:absolute;left:0;bottom:66px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:700;font-size:16px;letter-spacing:.05em;
   color:#ffe9b0;text-shadow:0 1px 4px rgba(0,0,0,.55)}}
"""
    body = f"""<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="weekdate">{esc(week_date_line)}</div>
<div class="count">{esc(count_line)}</div>
<div class="key">Ogni pennellata d’oro è una decisione.</div>
<div class="url">opencaselaw.ch</div>
<div class="credit">{esc(CREDIT)}</div>"""
    return page("family=Fraunces:ital,opsz,wght@1,9..40,400..500"
                "&family=Archivo:wght@400..700", css, body, lang)


if __name__ == "__main__":
    import poster_w48  # noqa: E402  (sanity check, not used by poster_w48.py's own CLI)

    wk = poster_w48.load()
    arr_ = load_asset()
    sel_, bbox_ = detect_strokes(arr_, wk["added"])
    print(f"selected {len(sel_)} strokes == week['added'] {wk['added']}: {len(sel_) == wk['added']}")
    print(f"glyph bbox {bbox_}, width {bbox_[2]-bbox_[0]} ({(bbox_[2]-bbox_[0])/ASSET_W:.0%} of width), "
          f"height {bbox_[3]-bbox_[1]} ({(bbox_[3]-bbox_[1])/HORIZON:.0%} of sky band)")
    glyph_, _ = _glyph_mask(wk["added"])
    cov_ = _coverage_of(glyph_, sel_, arr_.shape[:2])
    print(f"stroke+halo coverage of the glyph's own ink: {cov_:.1%} (target 55-65%)")
