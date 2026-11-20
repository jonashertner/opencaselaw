#!/usr/bin/env python3
"""Week 47 film: prepare the textures (run once; the outputs are committed, the inputs are not).

    python3 scripts/weekly_poster_w47_snowfall_assets.py <macro source jpg> <terrain_lin.npy> <dir of Met scans>

- macro.jpg: Wilson A. Bentley's stellar dendrite (Metropolitan Museum of Art, DP351917, CC0), cut
  from the full 4000 x 2526 scan of the print: the dark disc of the print becomes black, the crystal
  keeps every arm, 2048 x 2048 greyscale.
- atlas.png (RGB) and atlas_far.png (grey): the Bentley crystals of the poster (naiv/crystals, see
  its SOURCES.json), each on a 256 px cell, background lifted to black; R is the sharp crystal, G and
  B two blurred copies, atlas_far a third, wider blur, for the lens's depth of field.
- terrain_{0..3}.jpg: the moonlit view rendered by weekly_poster_w47_snowfall_terrain.py, tone-mapped
  to display values, sharp and blurred three times.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
NAIV = HERE / "weekly_poster_w47_assets" / "naiv"
OUT = HERE / "weekly_poster_w47_assets" / "snowfall"
CELL, FIT = 256, 168
BLUR = (0.0, 3.0, 8.0)        # px on the 256 cell: R, G, B
BLUR_FAR = 18.0               # px on the 256 cell, stored at half size


def lum(a: np.ndarray) -> np.ndarray:
    return (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]) / 255.0


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="constant"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur(a: np.ndarray, s: float) -> np.ndarray:
    """Gaussian of sigma s, as three box passes per axis (zero outside)."""
    if s <= 0:
        return a.astype(np.float32)
    r = max(1, int(round((np.sqrt(4 * s * s + 1) - 1) / 2)))   # three boxes of width 2r+1 ~ sigma s
    out = a.astype(np.float32)
    for axis in (0, 1):
        for _ in range(3):
            out = _box(out, r, axis)
    return out


def cut_print(src: Path) -> np.ndarray:
    """One of Bentley's card prints at the Met: the crystal on the print's dark disc, the disc made
    black, the card and colour chart cut away. Returns a square greyscale array (0..1) centred on
    the crystal, at the scan's own resolution."""
    im = np.asarray(Image.open(src).convert("RGB"), np.float32)
    L = lum(im)
    H, W = L.shape
    # the card is the bright rectangle; the print's dark disc sits inside it
    bright = L > 0.6
    rows_b = np.nonzero(bright[:, : int(W * 0.8)].mean(axis=1) > 0.25)[0]
    cols_b = np.nonzero(bright[:, : int(W * 0.8)].mean(axis=0) > 0.25)[0]
    r0, r1, c0, c1 = rows_b.min(), rows_b.max(), cols_b.min(), cols_b.max()
    card = np.zeros_like(bright)
    card[r0:r1, c0:c1] = True
    dark = (L < 0.35) & card
    rows, cols = np.nonzero(dark)
    cy, cx = rows.mean(), cols.mean()
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - cy, xx - cx)
    disc_r = np.sqrt(dark.sum() / np.pi) * 1.02          # the disc's own area, a little generous
    inside = (r < disc_r * 0.97) & card
    bg = np.percentile(L[inside], 30)                     # the black of the disc
    crystal = np.clip((L - bg * 1.15) / (0.92 - bg * 1.15), 0, 1) * inside
    ys, xs = np.nonzero(crystal > 0.35)
    y0, y1, x0, x1 = np.percentile(ys, 0.2), np.percentile(ys, 99.8), np.percentile(xs, 0.2), np.percentile(xs, 99.8)
    side = int(max(y1 - y0, x1 - x0) * 1.10)
    cyc, cxc = int((y0 + y1) / 2), int((x0 + x1) / 2)
    canvas = np.zeros((side, side), np.float32)
    sy0, sx0 = cyc - side // 2, cxc - side // 2
    ys0, xs0 = max(sy0, 0), max(sx0, 0)
    ys1, xs1 = min(sy0 + side, H), min(sx0 + side, W)
    canvas[ys0 - sy0:ys1 - sy0, xs0 - sx0:xs1 - sx0] = crystal[ys0:ys1, xs0:xs1]
    cc = (side - 1) / 2
    yy, xx = np.mgrid[0:side, 0:side]
    rr = np.hypot(yy - cc, xx - cc) / (side / 2)
    return canvas * np.clip((1.0 - rr) / 0.04, 0, 1)


def macro(src: Path) -> dict:
    c = cut_print(src)
    Image.fromarray((c ** 0.95 * 255).astype(np.uint8)).resize((2048, 2048), Image.LANCZOS).save(
        OUT / "macro.jpg", quality=93, optimize=True)
    return {"src_px": c.shape[0], "file": src.name}


def clean_sprite(path: Path) -> tuple[np.ndarray, dict]:
    a = np.asarray(Image.open(path).convert("RGBA"), np.float32)
    al = a[..., 3] / 255.0
    P = lum(a) * al                                           # the sprites carry the crystal in alpha
    h, w = P.shape
    yy, xx = np.mgrid[0:h, 0:w]
    ys, xs = np.nonzero(al > 0.02)
    cy, cx = ys.mean(), xs.mean()
    rad = np.hypot(yy - cy, xx - cx)
    rmax = np.percentile(rad[al > 0.02], 99)
    disc = rad < rmax * 0.9
    outer = (rad > rmax * 0.72) & (rad < rmax * 0.9)
    bg = max(float(np.percentile(P[disc], 12)), float(np.median(P[outer])))   # the print's own backdrop
    e = np.clip((P - bg * 1.2 - 0.02) / max(1e-3, 0.95 - bg * 1.2), 0, 1) ** 1.1
    # ring test: a crystal that fills its disc to the rim was cut with its bright background
    ring = (rad > rmax * 0.86) & (rad < rmax * 0.97)
    rim = float((e[ring] > 0.28).mean())
    return e, {"bg": bg, "rim": rim, "mean": float(e[disc].mean())}


# whole, isolated crystals only (picked by eye from contact sheets; plates that fill their print's
# disc read as coins and are left out). Met prints are re-cut from the scans, so no arm is clipped.
MET_PICK = ["DP351882", "DP351883", "DP351889", "DP351890", "DP351892", "DP351899", "DP351904", "DP351907",
            "DP351908", "DP351909", "DP351910", "DP351911", "DP351913", "DP351914", "DP351917", "DP351918",
            "DP351919", "DP351920", "DP351922", "DP351923", "DP351927", "DP351928", "DP351930", "DP351931"]
NAIV_PICK = [47, 49, 50, 57, 61, 82, 86, 90, 94, 95, 97, 98, 100]     # naiv/crystals, cut for the poster


def _cell(e: np.ndarray) -> np.ndarray:
    h, w = e.shape
    s = FIT / max(h, w)
    im = Image.fromarray(e.astype(np.float32)).resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    cell = np.zeros((CELL, CELL), np.float32)
    ox, oy = (CELL - im.width) // 2, (CELL - im.height) // 2
    cell[oy:oy + im.height, ox:ox + im.width] = np.clip(np.asarray(im, np.float32), 0, 1)
    return cell


def atlas(met_dir: Path) -> dict:
    man = json.loads((NAIV / "crystals" / "manifest.json").read_text(encoding="utf-8"))
    cells, info = [], []
    for dp in MET_PICK:
        src = next(met_dir.glob(f"*MET_{dp}.jpg"))
        cells.append(_cell(cut_print(src)))
        info.append({"source": src.name, "via": "re-cut from the Met scan"})
    for i in NAIV_PICK:
        e, st = clean_sprite(NAIV / "crystals" / man[i]["file"])
        cells.append(_cell(e))
        info.append({"source": man[i]["source"], "via": f"naiv/crystals/{man[i]['file']}"})
    # even out the light: a solid plate must not outshine a fine dendrite of the same size
    for k, cell in enumerate(cells):
        yy, xx = np.mgrid[0:CELL, 0:CELL]
        disc = np.hypot(yy - CELL / 2, xx - CELL / 2) < FIT / 2
        m = float(cell[disc].mean())
        if m > 0.24:
            cells[k] = cell * (0.24 / m)
            info[k]["dimmed"] = round(0.24 / m, 3)
    n = len(cells)
    cols = 8
    rows = (n + cols - 1) // cols
    A = np.zeros((rows * CELL, cols * CELL, 3), np.float32)
    F = np.zeros((rows * CELL // 2, cols * CELL // 2), np.float32)
    for k, cell in enumerate(cells):
        r, c = divmod(k, cols)
        for ch, s_ in enumerate(BLUR):
            A[r * CELL:(r + 1) * CELL, c * CELL:(c + 1) * CELL, ch] = blur(cell, s_)
        far = np.asarray(Image.fromarray(blur(cell, BLUR_FAR)).resize((CELL // 2, CELL // 2), Image.BILINEAR))
        F[r * CELL // 2:(r + 1) * CELL // 2, c * CELL // 2:(c + 1) * CELL // 2] = far
    Image.fromarray((np.clip(A, 0, 1) * 255).astype(np.uint8)).save(OUT / "atlas.png", optimize=True)
    Image.fromarray((np.clip(F, 0, 1) * 255).astype(np.uint8)).save(OUT / "atlas_far.png", optimize=True)
    return {"n": n, "cols": cols, "rows": rows, "cell": CELL, "fit": FIT, "sprites": info}


def terrain(npy: Path) -> dict:
    a = np.load(npy)
    v = np.clip(a / 0.8, 0, 1) ** (1 / 2.0)
    H, W = v.shape
    Image.fromarray((v * 255).astype(np.uint8)).save(OUT / "terrain_0.jpg", quality=90, optimize=True)
    sizes = []
    for lvl, (sig, div) in enumerate(((6.0, 2), (24.0, 4), (80.0, 8)), start=1):
        b = blur(v, sig)
        im = Image.fromarray((np.clip(b, 0, 1) * 255).astype(np.uint8)).resize((W // div, H // div), Image.BILINEAR)
        im.save(OUT / f"terrain_{lvl}.jpg", quality=90, optimize=True)
        sizes.append([sig / 2, W // div, H // div])           # sigma in css px
    return {"levels_sigma_css": [0] + [s[0] for s in sizes]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    met = Path(sys.argv[3])
    meta = {"macro": macro(Path(sys.argv[1])), "atlas": atlas(met), "terrain": terrain(Path(sys.argv[2]))}
    (OUT / "textures.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    a = meta["atlas"]
    print("atlas", a["n"], meta["macro"], meta["terrain"])


if __name__ == "__main__":
    main()
