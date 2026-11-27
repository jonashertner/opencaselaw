"""Week 48 (20 - 26 November 2026), French: La Jungfrau, en or. -- v5, after four rounds of Jonas's
review, on the figure alone each time. v1 -> v2: the plate read as a small picture on a black page,
the figure was plain typeset CSS text, the gold was dim at full view -- fixed by cropping the plate
much larger and baking the figure into the gold raster as a gouged pixel mask. v2 -> v3: the gouge's
corner-to-corner gold gradient read as foil on a chocolate box, and its smoothly wavy edges (a round
grotesque, Arial Black, re-thresholded against a smooth noise field) didn't match Vallotton's own
angular "LA JUNGFRAU." capitals -- fixed with flat gold and an angular noise field, in a squarer face.
v3 -> v4: at 100% the angular noise-field edge still read as a fine dashed serration -- a pixel
artifact, not a gouge mark -- replaced with an actual polygon (below); separately, the font moved
from macOS Helvetica Neue (not redistributable) to a bundled OFL Anton, and `paint_numeral` was
fixed to centre on the glyph's own ink rather than its (asymmetric) raster box. v4 -> v5 (this
version): with Anton's narrower digits the figure read small and the upper half emptied out, and the
subtitle sat well clear of the plate's own stars, leaving a dead black band -- fixed by enlarging the
figure (~1.4x cap-height; facet/jitter/nick sizes now scale with it, as fractions of its own size
rather than fixed px), painting it into the combined canvas rather than the extension alone (so its
new height isn't capped by the extension's own), moving the subtitle down to sit just above the
plate's stars, and raising the top credit line's opacity for legibility at 540px.

Nothing else changes from v3 otherwise: the plate crop, the mark detection, the 861 cuts' own colour
and gradient, the flat numeral gold, the extension and its join are all as described below.

Felix Vallotton's woodcut "La Jungfrau" (1892; Bibliotheque nationale de France, copy btv1b6951651x,
public domain -- see weekly_poster_w48_assets/jungfrau/SOURCES.json) is a night: black ink sky with a
crescent moon and a scatter of stars, the white Jungfrau massif and a sea of clouds, and dark foreground
slopes left and right that Vallotton cut full of small white knife strokes -- grass flecks on the dark
meadow, stars in the dark sky. The plate supplies the whole image: cropped to keep the full left slope
(most of the plate's gold-worthy cuts) and the massif large and dominant -- which, at this enlargement,
means cropping the moon and the "F.V." label block out of frame; "LA JUNGFRAU." stays intact at the
foot. The plate runs the full width of the poster, bled to the bottom edge, and its own black sky is
sampled and carried on up to fill the rest of the page -- same ink colour, same faint grain, crossfaded
into the plate's own sky so the join does not show.

The marks: exactly `week["added"]` of Vallotton's own white knife cuts -- stars and grass flecks, never
drawn -- are printed as if from a second, gold block, each keeping its exact cut pixel shape. They are
DETECTED, not placed: the plate is thresholded into ink/paper, every 4-connected white component is
measured (area, bounding box), and a component is a candidate cut only if it is small (3-3000 px at the
3840px-wide working resolution) and taller than it is wide by a comfortable margin (height >= 0.45 x
width) -- the second test is what throws out the one real false lead this plate has: the thin white
slivers left between nearby parallel hatch-strokes in the clouds and the water-reflection band, which
are wide, flat and only a few pixels tall, the opposite shape of a grass fleck or a star. Candidates
that sit within 70px of any large white shape (area > 5000px: the massif or a cloud) are also dropped,
which is what keeps the jagged dark rock-crags picked out of the massif's own snow out of the count --
they would otherwise pass the size and shape tests. Of the candidates left (consistently several times
`week["added"]`, even with the moon and most of the right slope now out of frame), the largest
`week["added"]` by area are the ones printed gold; the rest of Vallotton's cuts stay paper-white.
"Largest by area" is a simple, legible rule and, in practice, picks out cuts that read clearly as marks
rather than the faintest flecks -- almost all on the left slope, with a handful of stars in the sky.

Colour: the plate's own sampled ink and paper tones (not an invented duotone), pushed to a deep,
poster-dark near-black and a warm cream so the extension above matches exactly. The gold is a bright,
saturated printed yellow-gold (not the dimmer, browner gold of v1) -- flat, no glow, no halo, each cut
exactly its own shape, with only a small per-cut brightness jitter and a corner-to-corner highlight
gradient for a hint of printed-ink sheen.

The figure "861" is set in Helvetica Neue Condensed Bold (squarer and tighter than v2's Arial Black,
closer to the proportions of Vallotton's own hand-cut label capitals) and rendered to a pixel mask in
Python, then GOUGED as a POLYGON: every contour of the glyphs -- each digit's outer silhouette and
each of its counters ("8"'s two bowls, "6"'s one) -- is traced pixel-exact by Moore-neighbour boundary
tracing (`_trace_all_contours`), simplified to a polygon whose edges are real chords of that contour,
not redrawn curves (`_simplify_polyline`), its vertices jittered a few px (`_jitter_vertices`), and
2-4 wedge-shaped nicks bitten into each digit's outer edge where a gouge would plausibly slip
(`_add_wedge_nicks`) -- then rasterised at several times the final resolution and box-downsampled for
a clean, solid antialiased edge (`render_numeral`): short straight facets and the odd deliberate notch,
the way Vallotton's own blocky capitals read, not the smoothly wavy or finely serrated edges of the
two earlier attempts. The mask is filled FLAT gold (`_numeral_gold`: the 861 cuts' own two tones
averaged into one, not their corner-to-corner gradient, which at this size read as a diagonal sheen --
foil on a chocolate box, not ink) with only a fine per-pixel brightness texture standing in for the
woodblock's own grain, baked into the same raster image as the 861 cuts before the page's text layer --
the figure and the slope's gold are one printing, not two different techniques side by side. A faint,
uniform paper-grain overlay, tiled from the plate's own sky texture, runs over the whole poster (sky,
slope and figure alike) on top of that.

Text is now only what the figure doesn't already say: a one-line "nouvelles decisions de justice
suisses" under it, "Semaine 48" and the date above it, and the credit + opencaselaw.ch tucked small
at the very top of the carried-up sky, calm and out of the figure's way.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350

ASSET = Path(__file__).with_name("weekly_poster_w48_assets") / "jungfrau" / "jungfrau_source.jpg"
ASSET_W, ASSET_H = 3840, 3119  # the plate, cropped narrow (left slope + massif) -- full width = poster width

FINAL_W, FINAL_H = W * 2, H * 2                       # 2x design canvas, matches render_png's scale
PLATE_ASPECT = ASSET_W / ASSET_H                      # ~1.231 -- a much taller crop than v1's 1.75
STRIP_H = round(FINAL_W / PLATE_ASPECT)               # the plate's own height at full poster width, 2x
EXT_H = FINAL_H - STRIP_H                             # the carried-up sky above it, 2x
WORK_SCALE = ASSET_W / FINAL_W                         # work (asset) px per final (2x) px
EXT_WORK_H = round(EXT_H * WORK_SCALE)                 # the extension, built at the asset's own resolution

# -- the one label block still in frame after the v2 crop, hand-identified on the plate (asset px) --
# not detected, since its own printed letterforms are themselves small, high-contrast, roughly-square
# ink shapes that would otherwise pollute mark detection on the paper around them.
LABEL_LA_JUNGFRAU = (0, 2935, 1060, ASSET_H)     # x0,y0,x1,y1
MARGIN = 8  # inset from the plate's own printed border line

THRESH = 135.0          # ink/paper split -- see the histogram note in detect_marks
AREA_MIN, AREA_MAX = 3, 3000
MIN_ASPECT = 0.45        # bh >= MIN_ASPECT * bw -- rejects the flat cloud/water hatch-gap slivers
BIG_AREA = 5000          # anything bigger is the massif or a cloud
BIG_DILATE = 70          # px buffer around those shapes a candidate must clear

PAPER = (231, 218, 193)
INK_DEEP = (23, 20, 24)
GOLD_HI = (255, 226, 132)   # brighter, cleaner than v1 -- a lit printed gold, not mustard
GOLD_LO = (226, 168, 58)

FONT_PATH = str(Path(__file__).with_name("weekly_poster_w48_assets") / "jungfrau" / "Anton-Regular.ttf")  # Google Fonts, SIL OFL 1.1
FONT_INDEX = 0  # Anton: condensed heavy grotesque, bundled (OFL) so the render does not depend on macOS fonts

CREDIT = "Félix Vallotton, La Jungfrau, 1892, gravure sur bois · BnF"
MONTHS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)x(2r+1) window, via an integral image."""
    csum = np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1)
    csum = np.pad(csum, ((1, 0), (1, 0)))
    h, w = a.shape[:2]
    ys, xs = np.arange(h), np.arange(w)
    y0, y1 = np.clip(ys - r, 0, h), np.clip(ys + r + 1, 0, h)
    x0, x1 = np.clip(xs - r, 0, w), np.clip(xs + r + 1, 0, w)
    Y0, X0 = np.meshgrid(y0, x0, indexing="ij")
    Y1, X1 = np.meshgrid(y1, x1, indexing="ij")
    s = csum[Y1, X1] - csum[Y0, X1] - csum[Y1, X0] + csum[Y0, X0]
    return s / ((Y1 - Y0) * (X1 - X0))


def flatten_ink(gray: np.ndarray, steepness: float = 11.0) -> np.ndarray:
    """0..1 ink density (0 = paper, 1 = ink). Unlike a toning correction over a page of fine
    linework, this plate is genuinely bimodal -- two big solid fields, ink and paper (see the
    histogram note in the module's development) -- so a plain logistic curve around `THRESH`
    reproduces the plate's own bold contrast cleanly, softened just enough (a few px) to keep the
    scan's own anti-aliased edges rather than aliasing them."""
    return 1.0 / (1.0 + np.exp((gray - THRESH) / steepness))


def _connected_components(binary: np.ndarray) -> list[np.ndarray]:
    """4-connected components of `binary`, via flood fill. ~2s over the plate's ~2.6M ink/paper
    pixels -- cheap enough to just run on the whole crop rather than hand-traced sub-regions."""
    h, w = binary.shape
    visited = np.zeros_like(binary, dtype=bool)
    ys, xs = np.nonzero(binary)
    comps: list[np.ndarray] = []
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        pts = [(sy, sx)]
        while stack:
            cy, cx = stack.pop()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w and binary[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
                    pts.append((ny, nx))
        comps.append(np.array(pts))
    return comps


def detect_marks(gray: np.ndarray, n: int, seed: int = 48) -> list[np.ndarray]:
    """n components (each an (k,2) array of asset-px (y,x) points), one per decision: real
    connected components of the thresholded plate, small and taller-than-wide (see module
    docstring for what that rejects), clear of the massif/clouds/moon. The `n` largest by area are
    kept -- a simple, legible rule, and in practice the ones that read clearly as cuts rather than
    the faintest flecks."""
    h, w = gray.shape
    mask = np.zeros((h, w), dtype=bool)
    mask[MARGIN:h - MARGIN, MARGIN:w - MARGIN] = gray[MARGIN:h - MARGIN, MARGIN:w - MARGIN] > THRESH
    x0, y0, x1, y1 = LABEL_LA_JUNGFRAU
    mask[y0:y1, x0:x1] = False

    comps = _connected_components(mask)

    big_mask = np.zeros((h, w), dtype=bool)
    for c in comps:
        if len(c) > BIG_AREA:
            big_mask[c[:, 0], c[:, 1]] = True
    clear_of_big = box_blur(big_mask.astype(np.float64), BIG_DILATE) <= 0

    cands: list[tuple[int, np.ndarray]] = []
    for c in comps:
        area = len(c)
        if not (AREA_MIN <= area <= AREA_MAX):
            continue
        y0, x0 = c.min(0)
        y1, x1 = c.max(0)
        bh, bw = int(y1 - y0 + 1), int(x1 - x0 + 1)
        if bh < MIN_ASPECT * bw:
            continue
        cy, cx = int(c[:, 0].mean()), int(c[:, 1].mean())
        if not clear_of_big[cy, cx]:
            continue
        cands.append((area, c))

    assert len(cands) >= n, f"only {len(cands)} mark candidates found, need {n} -- loosen thresholds"
    cands.sort(key=lambda t: -t[0])
    kept = [c for _, c in cands[:n]]
    random.Random(seed).shuffle(kept)  # paint order independent of detection rank
    return kept


def colorize(gray: np.ndarray, ink_density: np.ndarray) -> np.ndarray:
    paper = np.array(PAPER, dtype=np.float64)
    ink = np.array(INK_DEEP, dtype=np.float64)
    d = ink_density[..., None]
    return paper * (1 - d) + ink * d


def _gold_gradient(xs: np.ndarray, ys: np.ndarray, x0: float, y0: float, bw: float, bh: float,
                    jitter: float) -> np.ndarray:
    """(n,3) gold colour per (x,y), a corner-to-corner highlight-to-shadow gradient times a
    brightness jitter -- the one piece of variation `paint_gold` and the numeral mask share, so the
    figure reads as struck from the same gold block as the 861 cuts."""
    hi, lo = np.array(GOLD_HI, dtype=np.float64), np.array(GOLD_LO, dtype=np.float64)
    t = (((xs - x0) / bw + (ys - y0) / bh) / 2.0)[:, None]
    return (hi * (1 - t) + lo * t) * jitter


def paint_gold(base: np.ndarray, ink_density: np.ndarray, marks: list[np.ndarray], seed: int = 4821) -> np.ndarray:
    """Recolour exactly the marks' own pixels gold, blended against the same local ink density as
    everything else (so a cut's soft scan edge keeps its own anti-aliasing) -- flat, no glow, no
    halo: a per-mark brightness jitter and a corner-to-corner highlight gradient are the only
    variation, for a hint of printed-ink sheen rather than a uniform sticker."""
    out = base.copy()
    ink = np.array(INK_DEEP, dtype=np.float64)
    rng = random.Random(seed)
    for c in marks:
        y0, x0 = c.min(0)
        y1, x1 = c.max(0)
        bh, bw = max(int(y1 - y0), 1), max(int(x1 - x0), 1)
        jitter = 0.92 + 0.26 * rng.random()
        ys, xs = c[:, 0], c[:, 1]
        gold = _gold_gradient(xs, ys, x0, y0, bw, bh, jitter)
        d = ink_density[ys, xs][:, None]
        out[ys, xs] = gold * (1 - d) + ink * d
    return out


_MOORE_NEI = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _moore_trace(mask: np.ndarray, start: tuple[int, int], back: tuple[int, int]) -> list[tuple[int, int]]:
    """The closed boundary loop of `mask` reachable from `start` by Moore-neighbour tracing --
    `back` is a neighbour of `start` already known to be background, which fixes which of the two
    loops through `start` (the outer silhouette, or a specific hole) gets traced: the walk always
    turns the same way around whichever side `back` sits on."""
    h, w = mask.shape

    def fg(p: tuple[int, int]) -> bool:
        y, x = p
        return 0 <= y < h and 0 <= x < w and bool(mask[y, x])

    back_idx = _MOORE_NEI.index((back[0] - start[0], back[1] - start[1]))
    boundary = [start]
    cur = start
    for _ in range(8 * h * w):  # a generous, finite cap -- real contours are far shorter
        moved = False
        for k in range(1, 9):
            idx = (back_idx + k) % 8
            dy, dx = _MOORE_NEI[idx]
            nxt = (cur[0] + dy, cur[1] + dx)
            if fg(nxt):
                back_idx = (idx + 4) % 8
                cur = nxt
                moved = True
                break
        if not moved or cur == start:
            break
        boundary.append(cur)
    return boundary


def _trace_all_contours(mask: np.ndarray) -> tuple[list, list]:
    """(outer_contours, hole_contours) of every shape in `mask`: one outer loop per 4-connected
    foreground component (`_connected_components`, already used for mark detection), and one hole
    loop per background component that does NOT touch the image border -- the border-connected
    background component is "outside"; anything else fully enclosed is a hole (a digit's counter,
    e.g. both bowls of "8"). Each outer loop is traced from its component's topmost-then-leftmost
    point (always on the true outer silhouette, never inside a hole); each hole loop is traced from
    the foreground pixel just above the hole's own topmost-leftmost point, with that hole pixel
    itself as the known background neighbour -- which is what makes `_moore_trace` hug that hole's
    inner wall rather than wandering off onto the outer silhouette."""
    h, w = mask.shape
    outers = []
    for c in _connected_components(mask):
        ys = c[:, 0]
        top_y = ys.min()
        top_x = c[ys == top_y][:, 1].min()
        start = (int(top_y), int(top_x))
        outers.append(_moore_trace(mask, start, (start[0], start[1] - 1)))

    holes = []
    for c in _connected_components(~mask):
        if c[:, 0].min() == 0 or c[:, 0].max() == h - 1 or c[:, 1].min() == 0 or c[:, 1].max() == w - 1:
            continue  # touches the border -- this is the open background, not a hole
        ys = c[:, 0]
        ty = ys.min()
        tx = c[ys == ty][:, 1].min()
        hy, hx = int(ty), int(tx)
        holes.append(_moore_trace(mask, (hy - 1, hx), (hy, hx)))
    return outers, holes


def _simplify_polyline(points: list, seg_len: float, rng: np.random.RandomState) -> list:
    """`points` (a dense pixel contour) reduced to vertices roughly `seg_len` apart by arc length
    (each target jittered +/-30% so spacing doesn't look mechanically even) -- every kept vertex is
    a real point on the original contour, so each segment between two of them is the straightened
    chord across that stretch: a polygonal simplification, not a redraw."""
    pts = np.asarray(points, dtype=np.float64)
    out = [pts[0]]
    acc = 0.0
    target = seg_len * rng.uniform(0.7, 1.3)
    for i in range(1, len(pts)):
        acc += float(np.linalg.norm(pts[i] - pts[i - 1]))
        if acc >= target:
            out.append(pts[i])
            acc = 0.0
            target = seg_len * rng.uniform(0.7, 1.3)
    return out


def _jitter_vertices(vertices: list, mag: float, rng: np.random.RandomState) -> list:
    return [v + rng.uniform(-mag, mag, size=2) for v in vertices]


def _add_wedge_nicks(vertices: list, n_nicks: int, depth: tuple[float, float],
                      rng: np.random.RandomState) -> list:
    """`n_nicks` short straight edges of this polygon replaced with a V: the edge's midpoint pushed
    `depth` px toward the polygon's own centroid (a reasonable stand-in for "inward" on a shape this
    compact), inserted as a new vertex -- a wedge bitten out of the stroke where a gouge would slip,
    not a wobble."""
    verts = list(vertices)
    if len(verts) < 4:
        return verts
    centroid = np.mean(verts, axis=0)
    for i in sorted(rng.choice(len(verts), size=min(n_nicks, len(verts)), replace=False), reverse=True):
        a, b = verts[i], verts[(i + 1) % len(verts)]
        mid = (a + b) / 2
        inward = centroid - mid
        norm = np.linalg.norm(inward)
        if norm < 1e-6:
            continue
        verts.insert(i + 1, mid + inward / norm * rng.uniform(*depth))
    return verts


def _numeral_gold(alpha: np.ndarray, seed: int = 48) -> np.ndarray:
    """(h,w,3) flat gold -- the cuts' own two tones averaged into one, not the corner-to-corner
    gradient `paint_gold` uses for a small mark -- plus a fine, per-pixel brightness texture (a
    touch of smoothed noise, +/-7%) standing in for the woodblock's own grain and a real block's
    slightly uneven inking, so the figure doesn't go flat-vector but also never ramps light-to-dark
    across the whole glyph the way the gradient did (gold foil, not ink, Jonas's first note)."""
    hi, lo = np.array(GOLD_HI, dtype=np.float64), np.array(GOLD_LO, dtype=np.float64)
    flat = (hi + lo) / 2
    rng = np.random.RandomState(seed + 99)
    noise = box_blur(rng.uniform(-1, 1, size=alpha.shape), 1)
    texture = 1 + noise * 0.07
    return flat[None, None, :] * texture[..., None]


def render_numeral(text: str, cap_h: int, seed: int = 48, super_: int = 3) -> np.ndarray:
    """A cleanly antialiased (h,w) alpha mask of `text` set in Helvetica Neue Condensed Bold
    (squarer and closer to Vallotton's own "LA JUNGFRAU." capitals than a plain grotesque) at
    cap-height `cap_h` px, GOUGED as a polygon rather than by re-thresholding a noise field (an
    earlier, rejected attempt: at 100% it read as a fine dashed serration, a pixel artifact, not a
    gouge mark): every contour -- the outer silhouette of each digit and each of its counters/holes
    -- is traced pixel-exact (`_trace_all_contours`), simplified to a polygon whose edges are real
    chords of that contour roughly 30-70px apart at this function's own scale (`_simplify_polyline`),
    its vertices jittered a few px (`_jitter_vertices`), and 2-4 wedge-shaped nicks per digit bitten
    into its outer polygon (`_add_wedge_nicks`) -- then rasterised and downsampled `super_`x (box
    averaging) for a clean, solid edge: no per-pixel noise, just the glyph's own shape, straightened
    and chipped."""
    rng = np.random.RandomState(seed)
    target_h = cap_h * super_
    # calibrate the point size needed for this font/text to come out close to target_h: tracing
    # (and, worse, the two flood fills under it) is O(pixels), so rendering several times bigger
    # than needed -- as a flat "pointsize = target_h * 3" guess did before this fix, 80s per render
    # instead of a few -- is pure waste, not extra precision.
    calib = ImageFont.truetype(FONT_PATH, 200, index=FONT_INDEX)
    calib_bbox = ImageDraw.Draw(Image.new("L", (10, 10), 0)).textbbox((0, 0), text, font=calib)
    point_size = max(1, round(target_h * 200 / (calib_bbox[3] - calib_bbox[1]) * 1.05))
    font = ImageFont.truetype(FONT_PATH, point_size, index=FONT_INDEX)
    probe = ImageDraw.Draw(Image.new("L", (10, 10), 0))
    bbox = probe.textbbox((0, 0), text, font=font)
    pad = max(8, int(target_h * 0.1))
    w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=255)
    mask = np.asarray(im) > 127
    ys, _ = np.nonzero(mask)
    scale = target_h / (ys.max() - ys.min() + 1)

    outers, holes = _trace_all_contours(mask)
    # facet/jitter/nick sizes as fractions of target_h, not fixed px, so they scale with the figure
    # (ratios fixed from the sizes that read well at the original cap-height of ~890 WORK-px)
    seg_len = target_h * 0.0562
    jitter_mag = target_h * 0.0034
    nick_depth = (target_h * 0.0135, target_h * 0.0293)

    def process(contour: list, outer: bool) -> list:
        verts = _simplify_polyline([(p[0] * scale, p[1] * scale) for p in contour], seg_len, rng)
        verts = _jitter_vertices(verts, jitter_mag, rng)
        if outer:
            verts = _add_wedge_nicks(verts, rng.randint(2, 5), nick_depth, rng)
        return verts

    outer_polys = [process(c, True) for c in outers]
    hole_polys = [process(c, False) for c in holes]

    big_w = int(w * scale) + pad * 2
    big_h = int(h * scale) + pad * 2
    canvas = Image.new("L", (big_w, big_h), 0)
    draw = ImageDraw.Draw(canvas)
    for poly in outer_polys:
        draw.polygon([(p[1], p[0]) for p in poly], fill=255)
    for poly in hole_polys:
        draw.polygon([(p[1], p[0]) for p in poly], fill=0)

    final = canvas.resize((max(1, big_w // super_), max(1, big_h // super_)), Image.BOX)
    return np.asarray(final, dtype=np.float64) / 255.0


def paint_numeral(canvas: np.ndarray, text: str, cap_h: int, cx: int, top: int, seed: int = 48) -> None:
    """Bakes `text`, gouged and gold, into `canvas` in place, horizontally centred on `cx`, its
    cap-height top edge at `top` -- flat gold (see `_numeral_gold`), the same ink the 861 cuts are
    struck from, not a different technique laid over the image."""
    alpha = render_numeral(text, cap_h, seed)
    gold = _numeral_gold(alpha, seed)
    h, w = alpha.shape
    # centre on the ink, not the raster: the glyph canvas carries asymmetric padding/side bearings
    ink_cols = np.flatnonzero((alpha > 0.5).any(axis=0))
    ink_mid = (int(ink_cols[0]) + int(ink_cols[-1])) // 2 if ink_cols.size else w // 2
    x0, y0 = cx - ink_mid, top
    region = canvas[y0:y0 + h, x0:x0 + w]
    canvas[y0:y0 + h, x0:x0 + w] = gold * alpha[..., None] + region * (1 - alpha[..., None])


def seamless_tile(gray_band: np.ndarray, size: int = 256) -> np.ndarray:
    """A `size`x`size` seamless grain tile from a flat patch of the plate's own sky: high-frequency
    residual only (the paper/scan grain, not the illumination), mirrored into a 2x2 block so
    opposite edges always match when the tile repeats."""
    h, w = gray_band.shape
    y0 = max(0, h // 2 - size // 4)
    x0 = max(0, w // 2 - size // 4)
    patch = gray_band[y0:y0 + size // 2, x0:x0 + size // 2]
    residual = patch - box_blur(patch, 3)
    residual = residual - residual.mean()
    top = np.concatenate([residual, residual[:, ::-1]], axis=1)
    block = np.concatenate([top, top[::-1, :]], axis=0)
    return block


def _declutter(band: np.ndarray) -> np.ndarray:
    """The band with every star (and any other bright outlier) smoothed away, leaving only the
    paper/scan grain -- so tiling it below doesn't repeat an identifiable star at a regular
    interval, which would read as drawn rather than found. Three passes, widening radius and
    tightening the outlier margin, since a star's own bright core still pulls up a small-radius
    local average right where it sits (a single pass leaves a dim ghost at the brightest stars)."""
    clean = band.copy()
    for r, thr in ((4, 15), (8, 12), (14, 10)):
        local_bg = box_blur(clean, r)
        outlier = clean > local_bg + thr
        clean[outlier] = local_bg[outlier]
    return clean


def build_extension(gray: np.ndarray, strip: np.ndarray, feather: int = 60) -> np.ndarray:
    """The carried-up night above the plate: a star-free patch of the plate's own sky grain (rows
    10:180, decluttered -- see `_declutter`), coloured the same way as the plate, mirror-tiled to
    height, then crossfaded into the plate's own top rows (`strip`, the already-coloured woodcut)
    over `feather` px so the join is a gradual hand-off between two patches of the same sky rather
    than a hard edge between a generated texture and a scanned one."""
    band_gray = _declutter(gray[10:180, :])
    band_d = flatten_ink(band_gray)
    band = colorize(band_gray, band_d)
    tile_h = band.shape[0]
    n_tiles = -(-EXT_WORK_H // tile_h) + 1
    tiles = [band[::-1] if i % 2 else band for i in range(n_tiles)]
    full = np.concatenate(tiles, axis=0)
    ext = full[-EXT_WORK_H:].copy()
    alpha = np.linspace(0, 1, feather)[:, None, None]
    ext[-feather:] = ext[-feather:] * (1 - alpha) + strip[:feather] * alpha
    return ext


def design(week: dict, lang: str) -> str:
    assert lang == "fr", "the Jungfrau poster is French-only"
    n = week["added"]

    src = Image.open(ASSET).convert("RGB")
    assert src.size == (ASSET_W, ASSET_H), f"unexpected asset size {src.size}"
    gray = np.asarray(src.convert("L"), dtype=np.float64)

    marks = detect_marks(gray, n)
    assert len(marks) == n == week["added"], "one gold cut per decision"

    ink_density = flatten_ink(gray)
    base = colorize(gray, ink_density)
    strip = paint_gold(base, ink_density, marks)

    ext = build_extension(gray, strip)
    canvas = np.concatenate([ext, strip], axis=0)

    # the numeral is painted into the COMBINED canvas, not just the extension: at this enlarged size
    # (v5) its ink would run past the extension's own height, and there is no reason it shouldn't --
    # the plate's own sky, just below the seam, is the same flat ink colour, so the figure can sit
    # across the join invisibly, same as the extension's texture already does.
    numeral_cap_h = round(350 * WORK_SCALE * 2)      # design px -> work px (WORK = 2x final = 2x*2x design)
    numeral_top = round(95 * WORK_SCALE * 2)
    paint_numeral(canvas, wp.fmt(n, lang), numeral_cap_h, ASSET_W // 2, numeral_top)

    canvas = np.clip(canvas, 0, 255).astype("uint8")
    img = Image.fromarray(canvas, "RGB").resize((FINAL_W, FINAL_H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    bg_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    grain = seamless_tile(gray[20:220, :])
    g8 = np.clip(grain * 2.2 + 128, 0, 255).astype("uint8")
    grain_img = Image.fromarray(g8, "L").convert("RGBA")
    ga = np.asarray(grain_img).copy()
    ga[..., 3] = 255
    gbuf = io.BytesIO()
    Image.fromarray(ga, "RGBA").save(gbuf, "PNG", optimize=True)
    grain_b64 = base64.b64encode(gbuf.getvalue()).decode("ascii")

    d0, d1 = week["from"], week["to"]
    date_line = f"{d0.day}–{d1.day} {MONTHS_FR[d1.month]} {d1.year}"
    week_line = f"Semaine {week['week_no']}"

    css = f"""
body{{background:{_mix(INK_DEEP)};color:{_mix(PAPER)}}}
.bg{{position:absolute;inset:0;width:{W}px;height:{H}px;object-fit:cover}}
.grain{{position:absolute;inset:0;width:{W}px;height:{H}px;background-image:url(data:image/png;base64,{grain_b64});
   background-size:96px 96px;mix-blend-mode:overlay;opacity:.5}}
.credit{{position:absolute;left:0;top:20px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:500;font-size:13.5px;letter-spacing:.015em;
   color:rgba(238,228,208,.82);text-shadow:0 1px 6px rgba(0,0,0,.6)}}
.url{{position:absolute;left:0;top:39px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:700;font-size:14.5px;letter-spacing:.05em;
   color:rgba(243,205,138,.95);text-shadow:0 1px 6px rgba(0,0,0,.6)}}
.kicker{{position:absolute;left:0;top:82px;width:{W}px;text-align:center;
   font-family:'Archivo',sans-serif;font-weight:700;font-size:17px;letter-spacing:.17em;
   text-transform:uppercase;color:rgba(238,199,133,.92)}}
.key{{position:absolute;left:0;top:564px;width:{W}px;text-align:center;font-family:'Fraunces',serif;font-style:italic;font-weight:440;font-size:19px;line-height:1.3;color:rgba(242,198,96,.95)}}
.desc{{position:absolute;left:0;top:524px;width:{W}px;text-align:center;
   font-family:'Fraunces',serif;font-style:italic;font-weight:440;font-variation-settings:'opsz' 28;
   font-size:27px;line-height:1.3;color:rgba(231,218,193,.9);padding:0 90px}}
"""
    body = f"""<img class="bg" src="data:image/jpeg;base64,{bg_b64}" alt="">
<div class="grain"></div>
<div class="credit">{esc(CREDIT)}</div>
<div class="url">opencaselaw.ch</div>
<div class="kicker">{esc(week_line)} · {esc(date_line)}</div>
<div class="desc">nouvelles décisions de justice suisses</div>
<div class="key">Chaque entaille dorée est une décision.</div>"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900"
                "&family=Archivo:wght@400..700", css, body, lang)


def _mix(c: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in c)
    return f"rgb({r},{g},{b})"
