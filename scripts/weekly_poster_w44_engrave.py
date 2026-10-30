#!/usr/bin/env python3
"""Turn a photograph into a wave-line engraving, as a greyscale alpha PNG for one ink.

Every pixel asks how far it lies from the nearest of a stack of wavy lines; it is inked when that
distance is under the darkness of the picture there, so the lines swell in the shadows and thin to
hairs in the lights. A slow field bends the stack, and the lightness of the picture bends it a
little more, which is what lifts the forms out of the page.

    python3 scripts/weekly_poster_w44_engrave.py <photo> <out.png> [--crop l,t,r,b] [--size WxH]
"""
from __future__ import annotations

import argparse

import numpy as np
from PIL import Image, ImageFilter, ImageOps

SS = 2             # supersampling, for clean line edges


def field(w: int, h: int) -> np.ndarray:
    """The slow drift of the lines, in pixels of output size."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    x, y = x / SS, y / SS
    return (5.0 * np.sin(x / 83 + y / 310) + 2.6 * np.sin(x / 37 - y / 190 + 1.3)
            + 12 * np.sin(x / 390 + y / 240 + .6) + 1.1 * np.sin(x / 15 + y / 70)) * SS


def engrave(img: Image.Image, size: tuple[int, int], gamma: float, relief: float, lift: float,
            pitch: float, sharpen: float, oval: bool = False) -> Image.Image:
    w, h = size[0] * SS, size[1] * SS
    grey = ImageOps.autocontrast(img.convert("L"), cutoff=(1, 1)).resize((w, h), Image.LANCZOS)
    tone = grey.filter(ImageFilter.GaussianBlur(.8 * SS))
    soft = grey.filter(ImageFilter.GaussianBlur(9 * SS))
    wide = grey.filter(ImageFilter.GaussianBlur(40 * SS))
    M = np.asarray(soft, dtype=np.float32) / 255
    L = np.asarray(tone, dtype=np.float32) / 255
    L = np.clip(L + sharpen * (L - np.asarray(wide, dtype=np.float32) / 255), 0, 1)
    dark = np.clip(1 - L + lift, 0, 1) ** gamma
    if oval:  # the lines thin out to nothing towards an oval edge, as on a bond's vignette
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.hypot((xx - w / 2) / (w * .5), (yy - h * .47) / (h * .5))
        fade = np.clip((.97 - r) / .24, 0, 1)
        dark *= fade ** .8
    else:
        fade = 1.0
    y = np.mgrid[0:h, 0:w][0].astype(np.float32)
    v = (y + field(w, h) + relief * M * SS) / (pitch * SS)
    dist = np.abs(v - np.round(v)) * 2      # 0 on a line, 1 midway between two
    ink = np.clip((dark * .96 + .03 * fade - dist) * pitch * SS * .5 + .5, 0, 1) * (fade > 0)
    out = Image.fromarray((ink * 255).astype(np.uint8), "L")
    return out.resize(size, Image.LANCZOS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("out")
    ap.add_argument("--crop")
    ap.add_argument("--size", default="1920x1240")
    ap.add_argument("--gamma", type=float, default=1.15)
    ap.add_argument("--relief", type=float, default=30)
    ap.add_argument("--lift", type=float, default=0.0)
    ap.add_argument("--pitch", type=float, default=7.0)      # px between lines at output size
    ap.add_argument("--sharpen", type=float, default=.9)
    ap.add_argument("--oval", action="store_true")
    a = ap.parse_args()
    im = ImageOps.exif_transpose(Image.open(a.photo)).convert("RGB")
    if a.crop:
        im = im.crop(tuple(int(v) for v in a.crop.split(",")))
    size = tuple(int(v) for v in a.size.split("x"))
    engrave(im, size, a.gamma, a.relief, a.lift, a.pitch, a.sharpen, a.oval).save(a.out, optimize=True)
