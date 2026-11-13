#!/usr/bin/env python3
"""Leaves for the week-46 French poster, cut from a book of nature prints.

Source: Constantin von Ettingshausen, "Die Blatt-Skelete der Dikotyledonen", Vienna 1861 (the author
died in 1897; public domain). Its figures are Naturselbstdrucke: the dried leaf itself was pressed
into a lead plate and printed from it, so every figure is the venation of one real leaf, white on
a black block. Scan: Internet Archive, item bub_gb_WiclAQAAMAAJ (digitised by Google).

Input: a folder of page images of that scan (https://archive.org/download/bub_gb_WiclAQAAMAAJ/page/n<N>_w1500.jpg).
Output: leaves.png, a sheet of the cut-out leaves, and leaves.json with each one's box on it and the
page it comes from. In the sheet the red channel holds the print (the veins), the green channel the
leaf's whole outline filled in.

  python3 build_leaves.py <folder of page images> [max leaves]
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
TALL = 170                      # the longer side of a stored leaf, in pixels


def blocks(grey: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes of the black blocks on a page: dark, roughly rectangular, big enough to hold a leaf."""
    h, w = grey.shape
    k = 4
    small = np.asarray(Image.fromarray(grey).resize((w // k, h // k), Image.BOX))
    # shrink first, so that letters and rules vanish and only the blocks are left to walk through
    dark = Image.fromarray(((small < 105) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))   # veins shut
    dark = dark.filter(ImageFilter.MinFilter(15)).filter(ImageFilter.MaxFilter(11))
    work = dark.copy()
    out = []
    px = work.load()
    for y in range(0, work.height, 3):
        for x in range(0, work.width, 3):
            if px[x, y] == 255:
                ImageDraw.floodfill(work, (x, y), 128)
                a = np.asarray(work) == 128
                ys, xs = np.nonzero(a)
                x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
                fill = a.sum() / ((x1 - x0) * (y1 - y0))
                if (x1 - x0) > 40 and (y1 - y0) > 40 and fill > 0.45 and (x1 - x0) < 0.9 * work.width:
                    out.append((x0 * k, y0 * k, x1 * k, y1 * k))
                ImageDraw.floodfill(work, (x, y), 1)
                px = work.load()
    return out


def leaf(grey: np.ndarray, box: tuple) -> tuple[np.ndarray, np.ndarray] | None:
    """The print inside one block: (veins 0..1, filled outline 0..1), cropped to the leaf."""
    x0, y0, x1, y1 = box
    m = max(6, int(0.05 * min(x1 - x0, y1 - y0)))                 # clear of the block's own edge
    a = Image.fromarray(grey[y0 + m:y1 - m, x0 + m:x1 - m])
    f = min(1.0, 2.0 * TALL / max(a.size))                        # twice the stored size is enough to work on
    a = np.asarray(a.resize((max(1, round(a.width * f)), max(1, round(a.height * f))), Image.LANCZOS), dtype=np.float32)
    lo, hi = np.percentile(a, 30), np.percentile(a, 99.5)
    if hi - lo < 60:
        return None
    veins = np.clip((a - lo - 0.22 * (hi - lo)) / (0.6 * (hi - lo)), 0, 1)
    # the leaf's whole shape: the veins closed into one patch (grown until the meshes shut, then
    # shrunk back), and of the patches the largest, so that the block's own edges drop out
    k = int(max(17, 0.09 * min(a.shape))) | 1
    lines = Image.fromarray(((veins > 0.35) * 255).astype(np.uint8))
    pad = Image.new("L", (lines.width + 2 * k, lines.height + 2 * k), 0)
    pad.paste(lines, (k, k))
    shut = pad.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k)).crop((k, k, k + lines.width, k + lines.height))
    ring = Image.new("L", (shut.width + 2, shut.height + 2), 0)       # what the ground cannot reach from outside
    ring.paste(shut, (1, 1))
    ImageDraw.floodfill(ring, (0, 0), 128)
    shut = Image.fromarray(((np.asarray(ring)[1:-1, 1:-1] != 128) * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(5))
    q = 3
    small = shut.resize((max(1, shut.width // q), max(1, shut.height // q)), Image.NEAREST)
    best, px = None, small.load()
    for y in range(0, small.height, 2):
        for x in range(0, small.width, 2):
            if px[x, y] == 255:
                ImageDraw.floodfill(small, (x, y), 128)
                part = np.asarray(small) == 128
                if best is None or part.sum() > best.sum():
                    best = part
                ImageDraw.floodfill(small, (x, y), 1)
                px = small.load()
    if best is None:
        return None
    keep = np.asarray(Image.fromarray((best * 255).astype(np.uint8)).resize(shut.size, Image.NEAREST)) > 0
    body = (np.asarray(shut) > 0) & keep
    if body.mean() < 0.12 or body.mean() > 0.9:
        return None
    near = np.asarray(Image.fromarray(((veins > 0.3) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(15))) > 0
    if (near & body).sum() < 0.93 * body.sum():                   # a patch without veins is not leaf
        return None
    ys, xs = np.nonzero(body)
    yy0, yy1, xx0, xx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    if min(yy1 - yy0, xx1 - xx0) < 40:
        return None
    cut = body[yy0:yy1, xx0:xx1]
    # whole leaves only: one that the block cut off lies along the box's side; one whose outline did
    # not close has bites in it, which closing the shape once more would fill
    if max(cut[0].mean(), cut[-1].mean(), cut[:, 0].mean(), cut[:, -1].mean()) > 0.3:
        return None
    big = 31
    pad = Image.new("L", (cut.shape[1] + 2 * big, cut.shape[0] + 2 * big), 0)
    pad.paste(Image.fromarray((cut * 255).astype(np.uint8)), (big, big))
    full = np.asarray(pad.filter(ImageFilter.MaxFilter(big)).filter(ImageFilter.MinFilter(big))) > 0
    if cut.sum() < 0.93 * full.sum():
        return None
    return veins[yy0:yy1, xx0:xx1] * cut, cut.astype(np.float32)


def main(folder: str, limit: int = 320) -> None:
    found = []
    for path in sorted(Path(folder).glob("*.jpg"), key=lambda p: int(p.stem.lstrip("n") or 0)):
        grey = np.asarray(Image.open(path).convert("L"))
        for box in blocks(grey):
            cut = leaf(grey, box)
            if cut is not None:
                found.append((path.stem, cut))
    print(len(found), "leaves found")
    # an even choice across the book, so that the sheet holds many kinds
    if len(found) > limit:
        found = [found[i] for i in np.linspace(0, len(found) - 1, limit).astype(int)]
    tiles = []
    for page, (veins, body) in found:
        s = TALL / max(veins.shape)
        size = (max(1, round(veins.shape[1] * s)), max(1, round(veins.shape[0] * s)))
        v = Image.fromarray((veins * 255).astype(np.uint8)).resize(size, Image.LANCZOS)
        b = Image.fromarray((body * 255).astype(np.uint8)).resize(size, Image.LANCZOS)
        tiles.append((page, v, b))
    cols = 20
    rows = -(-len(tiles) // cols)
    sheet = Image.new("RGB", (cols * TALL, rows * TALL), 0)
    meta = []
    for i, (page, v, b) in enumerate(tiles):
        x, y = (i % cols) * TALL, (i // cols) * TALL
        sheet.paste(Image.merge("RGB", (v, b, Image.new("L", v.size, 0))), (x, y))
        meta.append([x, y, v.width, v.height, page])
    sheet.save(HERE / "leaves.png", optimize=True)
    (HERE / "leaves.json").write_text(json.dumps({
        "source": "C. von Ettingshausen, Die Blatt-Skelete der Dikotyledonen, Wien 1861 (public domain); "
                  "scan: archive.org/details/bub_gb_WiclAQAAMAAJ",
        "leaves": meta}, separators=(",", ":")) + "\n")
    print(len(tiles), "stored,", (HERE / "leaves.png").stat().st_size, "bytes")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 320)
