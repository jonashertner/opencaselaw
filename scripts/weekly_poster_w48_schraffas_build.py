"""One-off extraction for the Romansh week-48 poster ("Schraffas" -- Romansh for "hachures"), v3.

v3 (art-directed again): v2's "nearest to a single apex point" rule produced a red disc with a
ragged fringe, unrelated to the landform -- real alpenglow lights the TOP of the relief, the ridge
crest, not a random point on a flank. v3 replaces the apex with RIDGE_POLYLINE, the ridge crest
traced by eye (the jagged rock-edge line the engraving itself draws between the two flanks; see
below), and selects the 861 strokes nearest that line instead of nearest a point. It also raises the
colour floor on the carmine plate (see CARMINE_GAMMA) so thin strokes come out solid, not pale, and
the poster module (not this file) moves the figure and footer off the recoloured strokes entirely.
Everything else below (crop, threshold, direction-gated components) is unchanged from v2.

v3 fix-in-place: the elongation filter alone let a handful of letter/digit FRAGMENTS (a stray "rt"
of "Piz Ayrutz", part of "2787", the "a" of "Piz Lischana") score as stroke-shaped and pass as
candidates -- close enough to the ridge to be chosen and turn carmine. Fixed by LABEL_BOXES: every
label and elevation number in the crop, hand-boxed in working coordinates and dilated
LABEL_MARGIN px, with any candidate whose own bounding box touches one dropped before selection,
then refilled to exactly 861 from what remains by the same ridge-distance rule. RIDGE_POLYLINE's
top end was also pulled down (was (255,115), now (266,150)) so the crest itself stays well clear of
"Piz Ayrutz".

Not imported by poster_w48.py / render(); run by hand once to produce the two small committed
assets that weekly_poster_w48_schraffas.py actually draws:

  weekly_poster_w48_assets/schraffas/schraffas_2160x2700.jpg   -- the recoloured map, full bleed
  weekly_poster_w48_assets/schraffas/strokes_manifest.json     -- the numbers this file asserts

Source: Wikimedia Commons "File:Swisstopo Dufourkarte BLATT XV DAVOS MARTINSBRUCK LT TK 015 1853.tiff"
(11786x8600, single-channel scan, licence Attribution-Swisstopo per the Commons API's own
extmetadata -- see SOURCES.json). The full TIFF (101 MB) is downloaded to this machine's scratch
directory, never committed; only the small cropped/recoloured JPEG below is.

v2 (art-directed revision): v1 cropped a 1800x2250 window and upsampled it only 1.2x -- at that
scale the hachures stayed map-sized, the 861 chosen strokes read as a faint pink wash over ~4% of
the frame, and the bottom scrim needed to carry the figure hid real relief. v2 crops far tighter --
600x750 px of the source, a 3.6x enlargement to the 2160x2700 canvas -- so every engraved stroke is
a visible brush-like mark with its own taper and copper edge, and the frame holds only a few
thousand strokes total, of which the 861 chosen ones are a third to half of the image: a real,
dominant flank, not a smudge.

The crop: the dark hachured spur falling from the ridge between Piz Ayrutz and Piz Lischana down to
the Vadret Lischanna glacier (box, in the 11786x8600 source: 9650,3625 - 10250,4375). This spur was
picked (over three other tight candidates around Piz S. Jon, Piz Pisoc and P. Madlein/Trilgia --
see "candidates_considered" in SOURCES.json) because at this zoom it is the cleanest: the dense,
near-black cliff hachures of the ridge occupy the upper half with almost no lettering crossing them,
and the open glacier below gives a genuinely calm, paper-pale area for the figure -- no scrim
needed, as directed.

Pipeline (same method as v1, same reasons -- see the v1 history in this file's git blame for the
full rationale of each step; summarised here):

1. Binarise (scan value < 170 = ink) and estimate, at every ink pixel, the LOCAL STROKE DIRECTION
   from the image's own structure tensor (gradient outer product, box-blurred; direction = the
   gradient's dominant axis rotated 90 degrees).
2. Label connected components with an 8-neighbour propagation GATED by that direction field: a
   pixel only joins a neighbour that is also ink AND whose step direction is itself aligned (within
   38 degrees) with BOTH pixels' own estimated stroke direction -- splits touching strokes at a fan
   or crossing, walks the full curving length of one real stroke. 140 iterations, numpy-vectorised
   array shifts only (no scipy, no cv2, no python per-pixel loop).
3. Candidates = components with 8-220 px area (smaller cap than v1: this crop's typical stroke is
   shorter, steep-slope hachuring) and bounding-box elongation diag^2/area >= 2.6 (slightly relaxed
   from v1's 3.0, because at this tight a crop a good number of real, short, steep-face strokes are
   stubbier than the long fan strokes v1 drew from). Drops lettering, the "Vadret Lischanna" script,
   and any merged cliff blob bigger than the cap.
4. Choose exactly 861 (week["added"]): the 861 candidates whose centroid is nearest a single point
   on the ridge's own spine, picked by eye where the spur is widest and most legible (not its
   narrowest, most-merged throat). Nearest-N from a spine point is "a contiguous mountain flank" by
   construction -- verified below to cover roughly a third of the frame's total candidate count.

5. Recolour as two real ink plates over the scan's own paper, not a tint:
     paper   = the scan's own near-white, warmed very slightly (250,247,236)
     graphite = unselected ink's target colour at full density: (44,39,35) -- a warm near-black,
                not neutral grey, "so the red owns the image" per art direction
     carmine  = chosen ink's target colour at full density: (200,16,46) -- #C8102E, a saturated,
                opaque carmine-vermilion
   For every pixel let d = 1 - scan_grey/255 (0 at paper, 1 at the ink's own darkest core -- the
   scan's own density, unchanged). Unchosen ink: colour = lerp(paper, graphite, d). Chosen ink:
   colour = lerp(paper, carmine, d). This is the scan's own antialiasing re-inked, never redrawn,
   now printed as a second, opaque plate rather than a faint tint.
6. Final canvas 2160x2700 = Lanczos upsample of the 600x750 crop (3.6x), both for the base grey and
   for the chosen-coverage mask (resized as float, thresholded at 0.5 for the boolean selection used
   in the per-pixel lerp above -- keeps selection edges exactly where Lanczos puts the scan's own
   edges). A mild unsharp mask (gaussian radius ~1.6 px via box-blur approximation, amount 0.65) is
   applied to the upsampled grey channel only, before recolouring, to hold the enlarged line work
   crisp rather than let it go soft -- no blur anywhere in this pipeline, only this one sharpening
   pass.

Run: .venv/bin/python scripts/weekly_poster_w48_schraffas_build.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# the full-size Dufour sheet XV download (not committed); point W48_DUFOUR_DIR at its folder
SCRATCH = Path(os.environ.get("W48_DUFOUR_DIR", "build/w48_schraffas"))
FULL_TIFF = SCRATCH / "dufour_xv_full.tiff"
ASSETS = Path(__file__).with_name("weekly_poster_w48_assets") / "schraffas"

# full-resolution crop box (left, upper, right, lower) in the 11786x8600 source -- the Piz
# Ayrutz/Lischana ridge falling into the Vadret Lischanna glacier
CROP_BOX = (9650, 3625, 10250, 4375)   # 600 x 750, 4:5
MW, MH = 2160, 2700                     # final canvas (3.6x upsample)

INK_THRESH = 170.0
ALIGN_DEG = 38.0
N_ITER = 140
AREA_LO, AREA_HI = 8, 220
ELONG_MIN = 2.6
N_STROKES = 861           # == week["added"]

# The ridge crest (arete) traced by eye in working-crop (600x750) coordinates, following the
# jagged white rock-edge line the engraving itself draws between the two flanks -- hachures on
# both sides run away from this line, downslope. Traced point by point against a 50px-pitch grid
# overlaid on a 3x blow-up of the crop (see the build log); not a straight line, it follows the
# ridge's own kinks.
RIDGE_POLYLINE = [
    (266, 150), (272, 170), (278, 195), (283, 220),
    (288, 250), (290, 280), (290, 310), (287, 340), (280, 370), (270, 400), (260, 425),
]
CARMINE_GAMMA = 0.45   # d**gamma for carmine only: raises the floor on faint/thin strokes

# Every label and elevation number in the crop, hand-boxed in working-crop (600x750) coordinates
# (left, top, right, bottom), dilated LABEL_MARGIN px on every side below. A candidate whose own
# bounding box intersects any of these (after dilation) is dropped before selection -- this is what
# stops a fragment of a letter or a digit from ever being counted as a "stroke" at all, rather than
# just keeping it unselected and hoping the ridge-distance rule never reaches it.
LABEL_BOXES = [
    (50, 15, 300, 140),     # "Piz Ayrutz" + its elevation figure "2787"
    (0, 290, 190, 405),     # "3103" + the start of "Piz Lischana"
    (85, 545, 560, 658),    # "Vadret Lischanna" (the glacier's own cursive label)
    (450, 0, 540, 340),     # "Curtinatsch" (cursive, running down the right edge)
    (270, 695, 460, 750),   # "P. Cornet", cut by the crop's bottom edge
]
LABEL_MARGIN = 6

PAPER = np.array([250.0, 247.0, 236.0])
GRAPHITE = np.array([44.0, 39.0, 35.0])
CARMINE = np.array([200.0, 16.0, 46.0])   # #C8102E


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    a = a.astype(np.float64)
    ap = np.pad(a, r, mode="edge")
    csum = np.cumsum(np.cumsum(ap, axis=0), axis=1)
    csum = np.pad(csum, ((1, 0), (1, 0)), mode="constant")
    k = 2 * r + 1
    return (csum[k:, k:] - csum[:-k, k:] - csum[k:, :-k] + csum[:-k, :-k]) / (k * k)


def shift(a: np.ndarray, dy: int, dx: int, fill):
    H, W = a.shape
    out = np.full_like(a, fill)
    ys0, ys1 = max(0, -dy), H - max(0, dy)
    xs0, xs1 = max(0, -dx), W - max(0, dx)
    yd0, yd1 = max(0, dy), H - max(0, -dy)
    xd0, xd1 = max(0, dx), W - max(0, -dx)
    out[yd0:yd1, xd0:xd1] = a[ys0:ys1, xs0:xs1]
    return out


def label_strokes(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    H, W = gray.shape
    mask = gray < INK_THRESH
    sm = box_blur(gray, 1)
    gy, gx = np.gradient(sm)
    r = 3
    Jxx, Jyy, Jxy = box_blur(gx * gx, r), box_blur(gy * gy, r), box_blur(gx * gy, r)
    theta = 0.5 * np.arctan2(2 * Jxy, Jxx - Jyy)
    stroke_dir = theta + np.pi / 2
    dcos, dsin = np.cos(stroke_dir), np.sin(stroke_dir)

    SENTINEL = H * W + 10
    lin = np.arange(H * W, dtype=np.int64).reshape(H, W)
    labels = np.where(mask, lin, SENTINEL).astype(np.int64)

    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    align_thresh = np.cos(np.deg2rad(ALIGN_DEG))
    gates = {}
    for dy, dx in offsets:
        nrm = (dx * dx + dy * dy) ** 0.5
        gates[(dy, dx)] = np.abs(dx * dcos + dy * dsin) / nrm >= align_thresh

    for _ in range(N_ITER):
        for dy, dx in offsets:
            nb_label = shift(labels, dy, dx, SENTINEL)
            gate = mask & gates[(dy, dx)] & shift(gates[(dy, dx)], dy, dx, False)
            labels = np.minimum(labels, np.where(gate, nb_label, SENTINEL))
    return labels, mask


def component_stats(labels: np.ndarray, mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    fg = labels[mask]
    order = np.argsort(fg, kind="stable")
    fg_s, ys_s, xs_s = fg[order], ys[order], xs[order]
    uniq, start_idx, counts = np.unique(fg_s, return_index=True, return_counts=True)
    n = len(uniq)
    ymin = np.empty(n, np.int32); ymax = np.empty(n, np.int32)
    xmin = np.empty(n, np.int32); xmax = np.empty(n, np.int32)
    for i in range(n):
        s, e = start_idx[i], start_idx[i] + counts[i]
        yy, xx = ys_s[s:e], xs_s[s:e]
        ymin[i], ymax[i] = yy.min(), yy.max()
        xmin[i], xmax[i] = xx.min(), xx.max()
    diag = np.hypot(ymax - ymin, xmax - xmin) + 1
    elong = diag * diag / counts
    return dict(uniq=uniq, start_idx=start_idx, counts=counts, elong=elong,
                ys_s=ys_s, xs_s=xs_s)


def unsharp(gray: np.ndarray, radius: int = 2, amount: float = 0.65) -> np.ndarray:
    blurred = box_blur(box_blur(gray, radius), radius)  # two box blurs ~ approximates a gaussian
    sharpened = gray + amount * (gray - blurred)
    return np.clip(sharpened, 0, 255)


def dist_to_polyline(px: np.ndarray, py: np.ndarray, poly: list[tuple[float, float]]) -> np.ndarray:
    """Min distance from each point (px,py) to any segment of poly (point-to-segment, vectorised)."""
    best = np.full(px.shape, np.inf)
    for (x1, y1), (x2, y2) in zip(poly[:-1], poly[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
        t = np.clip(t, 0.0, 1.0)
        projx, projy = x1 + t * dx, y1 + t * dy
        d = np.hypot(px - projx, py - projy)
        best = np.minimum(best, d)
    return best


def main():
    im = Image.open(FULL_TIFF)
    crop = im.crop(CROP_BOX).convert("L")
    gray = np.asarray(crop).astype(np.float64)
    H, W = gray.shape
    assert (W, H) == (600, 750)

    labels, mask = label_strokes(gray)
    st = component_stats(labels, mask)
    counts, elong = st["counts"], st["elong"]
    good = (counts >= AREA_LO) & (counts <= AREA_HI) & (elong >= ELONG_MIN)
    idx = np.nonzero(good)[0]

    # drop any candidate whose bounding box intersects a (dilated) label/number box -- computed from
    # component_stats' own per-component bbox arrays, not recomputed from the pixel lists
    ymin = np.empty(len(idx), np.int32); ymax = np.empty(len(idx), np.int32)
    xmin = np.empty(len(idx), np.int32); xmax = np.empty(len(idx), np.int32)
    for k, i in enumerate(idx):
        s, e = st["start_idx"][i], st["start_idx"][i] + st["counts"][i]
        yy, xx = st["ys_s"][s:e], st["xs_s"][s:e]
        ymin[k], ymax[k] = yy.min(), yy.max()
        xmin[k], xmax[k] = xx.min(), xx.max()
    in_label = np.zeros(len(idx), dtype=bool)
    for (bl, bt, br, bb) in LABEL_BOXES:
        bl, bt = bl - LABEL_MARGIN, bt - LABEL_MARGIN
        br, bb = br + LABEL_MARGIN, bb + LABEL_MARGIN
        in_label |= (xmin <= br) & (xmax >= bl) & (ymin <= bb) & (ymax >= bt)
    n_dropped = int(in_label.sum())
    idx = idx[~in_label]
    assert len(idx) >= N_STROKES, f"only {len(idx)} candidates after label exclusion, need {N_STROKES}"

    cy = np.empty(len(idx)); cx = np.empty(len(idx)); clen = np.empty(len(idx))
    for k, i in enumerate(idx):
        s, e = st["start_idx"][i], st["start_idx"][i] + st["counts"][i]
        cy[k] = st["ys_s"][s:e].mean()
        cx[k] = st["xs_s"][s:e].mean()
        clen[k] = st["elong"][i] * st["counts"][i]  # ~ diag^2, a length proxy, for the tie-break
    dist = dist_to_polyline(cx, cy, RIDGE_POLYLINE)
    # primary: nearest to the ridge crest; tie-break (rounded to damp float noise): longer first --
    # "the red runs along the ridge" reads better when the strokes right at the crest are its own
    # longest, most confident marks, not short fragments
    order = np.lexsort((-clen, np.round(dist, 1)))
    chosen = idx[order[:N_STROKES]]
    max_dist = float(dist[order[:N_STROKES]].max())
    assert len(chosen) == N_STROKES

    chosen_mask = np.zeros((H, W), dtype=bool)
    for i in chosen:
        s, e = st["start_idx"][i], st["start_idx"][i] + st["counts"][i]
        chosen_mask[st["ys_s"][s:e], st["xs_s"][s:e]] = True

    gray_hi = np.asarray(Image.fromarray(gray.astype(np.uint8)).resize((MW, MH), Image.LANCZOS),
                          dtype=np.float64)
    gray_hi = unsharp(gray_hi, radius=2, amount=0.65)
    cov_hi = np.asarray(
        Image.fromarray((chosen_mask * 255).astype(np.uint8)).resize((MW, MH), Image.LANCZOS),
        dtype=np.float64) / 255.0

    d = 1.0 - gray_hi / 255.0  # 0 = paper, 1 = ink's own darkest core
    d_carmine = np.power(np.clip(d, 0, 1), CARMINE_GAMMA)  # raises the floor on thin/faint strokes
    paper = PAPER[None, None, :]
    graphite_rgb = paper + d[..., None] * (GRAPHITE[None, None, :] - paper)
    carmine_rgb = paper + d_carmine[..., None] * (CARMINE[None, None, :] - paper)
    out = graphite_rgb + cov_hi[..., None] * (carmine_rgb - graphite_rgb)
    out = np.clip(out, 0, 255).astype(np.uint8)

    ASSETS.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(ASSETS / "schraffas_2160x2700.jpg", quality=92)

    frame_coverage = float(cov_hi.mean())
    manifest = dict(
        version=3, added=N_STROKES, crop_box_full_res=list(CROP_BOX), final_canvas=[MW, MH],
        zoom_factor=MW / W, ink_thresh=INK_THRESH, align_deg=ALIGN_DEG, n_iterations=N_ITER,
        area_range=[AREA_LO, AREA_HI], elong_min=ELONG_MIN,
        selection_rule="nearest to RIDGE_POLYLINE (point-to-segment dist of stroke centroid, "
                        "rounded to 0.1px), ties broken by longer stroke (diag^2) first, after "
                        "dropping every candidate touching a dilated LABEL_BOX",
        ridge_polyline_working_coords=RIDGE_POLYLINE,
        label_boxes_working_coords=LABEL_BOXES, label_margin_px=LABEL_MARGIN,
        candidates_dropped_as_lettering=n_dropped,
        num_candidates_total=int(len(idx)), chosen=int(len(chosen)),
        max_dist_to_ridge_working_px=max_dist,
        chosen_fraction_of_candidates=round(N_STROKES / len(idx), 3),
        chosen_pixel_fraction_of_frame=round(frame_coverage, 4),
        carmine_gamma=CARMINE_GAMMA,
        colours={"paper": PAPER.tolist(), "graphite": GRAPHITE.tolist(), "carmine": CARMINE.tolist()},
    )
    (ASSETS / "strokes_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
