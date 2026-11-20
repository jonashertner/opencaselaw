#!/usr/bin/env python3
"""Week 47 film, backdrop: the Bernina group by moonlight, seen from Muottas Muragl, from the real ground.

    python3 scripts/weekly_poster_w47_snowfall_terrain.py <dir of swissALTI3D 2 m tiles> [--k 2]

Reads the swissALTI3D tiles (swisstopo, 2 m, files named <E km>-<N km>.tif), averages them to a 4 m
grid, and renders the view from the terrace of Muottas Muragl (LV95 2'789'000 / 1'155'240, ground
2,451 m by the swisstopo height service, eye 3 m above it) towards the Bernina group, with a
rectilinear lens (horizontal field 32 degrees, centred on azimuth 178 degrees) whose principal point
is shifted down the frame, so verticals stay vertical and the summits sit in the lower half.

Real: every height, the earth's curvature and refraction (k = 0.13), the line of sight. Drawn: the
light (a low moon from the east-north-east), snow on every slope under 40 degrees, bare rock above 55,
the haze with distance, the sky. Italy lies outside swissALTI3D, so the ground beyond the border is
left out; from this terrace it sits behind the Bernina group anyway.

Writes four greyscale JPEGs for the film (the sharp view and three blurred copies for the lens's
depth of field) to scripts/weekly_poster_w47_assets/snowfall/.
"""
from __future__ import annotations

import json
import os
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
OUT = HERE / "weekly_poster_w47_assets" / "snowfall"

VIEW_E, VIEW_N = 2789000.0, 1155240.0   # Muottas Muragl, terrace by the funicular's top station
GROUND = 2451.3                         # swisstopo height service at that point
EYE = 3.0
AZ0, HFOV = float(os.environ.get('AZ0', 179.0)), float(os.environ.get('HFOV', 26.0))                 # centre azimuth and horizontal field, degrees
W_CSS, H_CSS = 1080, 1350
HORIZON = 0.74                          # principal point (eye level) as a fraction of the frame height
CELL = 4.0                              # metres
R_EFF = 6371000.0 / (1 - 0.13)          # refraction-corrected earth radius
MOON_AZ, MOON_ALT = 88.0, 17.0          # degrees
R_MAX = 27000.0
R_MIN = 40.0                            # the summit's own few metres of ground stay out of frame


def _light() -> tuple[float, float, float]:
    az, alt = math.radians(MOON_AZ), math.radians(MOON_ALT)
    return (math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt))


def _surface(a: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Snow/rock albedo x moonlight, and albedo x sky light, on one grid."""
    gy, gx = np.gradient(a, cell)
    nx, ny, nz = -gx, gy, np.ones_like(a)
    nl = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / nl, ny / nl, nz / nl
    slope = np.degrees(np.arccos(nz))
    L = _light()
    direct = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
    snow = np.clip((52.0 - slope) / 14.0, 0, 1)
    albedo = 0.13 + 0.77 * snow
    return albedo * direct, albedo * (0.55 + 0.45 * nz)


def mosaic(tiles: Path) -> tuple[np.ndarray, float, float, np.ndarray, np.ndarray]:
    """The 4 m height grid, plus the light the 2 m ground sends back, averaged to 4 m: each 4 m cell
    keeps the rock and snow of its own four 2 m cells, so the texture of the faces is the ground's own."""
    names = sorted(tiles.glob("*-*.tif"))
    keys = [tuple(int(v) for v in p.stem.split("-")) for p in names]
    e0, e1 = min(k[0] for k in keys), max(k[0] for k in keys) + 1
    n0, n1 = min(k[1] for k in keys), max(k[1] for k in keys) + 1
    per = int(1000 / CELL)
    shape = ((n1 - n0) * per, (e1 - e0) * per)
    z = np.full(shape, np.nan, np.float32)
    dl = np.zeros(shape, np.float32)
    sk = np.zeros(shape, np.float32)
    for p, (e, n) in zip(names, keys):
        a = np.array(Image.open(p), np.float32)
        assert a.shape == (500, 500), (p, a.shape)
        a[a < -1000] = np.nan
        r = (n1 - 1 - n) * per
        c = (e - e0) * per
        z[r:r + per, c:c + per] = a.reshape(per, 2, per, 2).mean(axis=(1, 3))
        af = np.where(np.isnan(a), np.nanmean(a) if np.isfinite(a).any() else 0, a)
        d2, s2 = _surface(af, 2.0)
        dl[r:r + per, c:c + per] = d2.reshape(per, 2, per, 2).mean(axis=(1, 3))
        sk[r:r + per, c:c + per] = s2.reshape(per, 2, per, 2).mean(axis=(1, 3))
    return z, e0 * 1000.0, n1 * 1000.0, dl, sk          # grids, west edge, north edge (metres)


def _boxmean(a: np.ndarray, r: int) -> np.ndarray:
    out = a.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
        n = out.shape[axis]
        out = (np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis) - np.take(c, np.arange(0, n), axis=axis)) / (2 * r + 1)
    return out.astype(np.float32)


def sample(a: np.ndarray, col: np.ndarray, row: np.ndarray) -> np.ndarray:
    """Bilinear, NaN outside."""
    h, w = a.shape
    ok = (col >= 0) & (row >= 0) & (col < w - 1) & (row < h - 1)
    c = np.where(ok, col, 0)
    r = np.where(ok, row, 0)
    c0, r0 = c.astype(np.int64), r.astype(np.int64)
    fc, fr = c - c0, r - r0
    v = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c0 + 1] * fc * (1 - fr)
         + a[r0 + 1, c0] * (1 - fc) * fr + a[r0 + 1, c0 + 1] * fc * fr)
    return np.where(ok, v, np.nan)


def shading(z: np.ndarray, dl: np.ndarray, sk: np.ndarray) -> np.ndarray:
    """Moonlit snow and rock: (2 m light x cast shadow + 2 m sky light x openness)."""
    zf = np.where(np.isnan(z), np.nanmin(z), z)
    az, alt = math.radians(MOON_AZ), math.radians(MOON_ALT)

    # cast shadows: march towards the moon on a 12 m grid
    k = 3
    zs = zf[::k, ::k]
    h, w = zs.shape
    rr, cc = np.mgrid[0:h, 0:w].astype(np.float32)
    lit = np.ones_like(zs, bool)
    step = CELL * k
    dx, dy = math.sin(az), -math.cos(az)        # columns east, rows south
    rise = math.tan(alt)
    d = step
    while d < 9000:
        hz = sample(zs, cc + dx * d / step, rr + dy * d / step)
        lit &= ~(hz > zs + rise * d + 2.0)
        d += step if d < 1500 else step * 2
    shadow = np.asarray(Image.fromarray(lit.astype(np.float32)).resize((w * k, h * k), Image.BILINEAR))[:zf.shape[0], :zf.shape[1]]
    pad = np.ones_like(zf)
    pad[:shadow.shape[0], :shadow.shape[1]] = shadow
    # openness: gullies and hollows see less sky (and less moon), crests more; two scales, 24 m and 120 m
    cav = 0.6 * (zf - _boxmean(zf, 6)) / 12.0 + 0.4 * (zf - _boxmean(zf, 30)) / 50.0
    open_ = np.clip(1.0 + cav, 0.45, 1.25)
    return (dl * pad * (0.7 + 0.3 * open_) + 0.13 * sk * open_).astype(np.float32)


def render(z: np.ndarray, west: float, north: float, dl: np.ndarray, sk: np.ndarray, k: int = 2) -> tuple:
    W, H = W_CSS * k, H_CSS * k
    f = (W / 2) / math.tan(math.radians(HFOV / 2))
    cy = H * HORIZON
    shade = shading(z, dl, sk)
    eye = GROUND + EYE

    # distances: 1 m steps nearby, then growing by 0.12 % per step
    rs, r = [], R_MIN
    while r < R_MAX:
        rs.append(r)
        r += max(1.0, r * 0.0004)
    rs = np.array(rs, np.float32)
    xs = np.arange(W, dtype=np.float32) + 0.5
    daz = np.arctan((xs - W / 2) / f)                       # per column
    az = np.radians(AZ0) + daz
    Y = np.empty((len(rs), W), np.float32)
    V = np.empty((len(rs), W), np.float32)
    for i in range(0, len(rs), 256):
        rb = rs[i:i + 256][:, None]                         # (b, 1)
        e = VIEW_E + rb * np.sin(az)[None, :]
        n = VIEW_N + rb * np.cos(az)[None, :]
        col = (e - west) / CELL - 0.5
        row = (north - n) / CELL - 0.5
        hgt = sample(z, col, row)
        sh = sample(shade, col, row)
        tan_el = (hgt - eye - rb * rb / (2 * R_EFF)) / rb
        y = cy - f * tan_el / np.cos(daz)[None, :]           # rectilinear, no pitch
        Y[i:i + 256] = np.where(np.isnan(y), np.inf, y)
        hz = 1 - np.exp(-rb / 38000.0)
        V[i:i + 256] = np.nan_to_num(sh) * (1 - hz) + 0.035 * hz
    cm = np.minimum.accumulate(Y, axis=0)                   # topmost row reached by the nearest j samples
    rows = np.arange(H, dtype=np.float32) + 0.5
    img = np.zeros((H, W), np.float32)
    cover = np.zeros((H, W), np.float32)
    dist = np.full((H, W), np.inf, np.float32)
    for c in range(W):
        idx = np.searchsorted(-cm[:, c], -rows, side="left")   # first sample whose ray reaches row
        hit = idx < len(rs)
        ii = np.minimum(idx, len(rs) - 1)
        img[:, c] = np.where(hit, V[ii, c], 0)
        cover[:, c] = hit
        dist[:, c] = np.where(hit, rs[ii], np.inf)
    ymin = cm[-1]
    meta = {"k": k, "W": W, "H": H, "f_px": f, "cy": cy, "ridge_top_px": float(np.min(ymin[np.isfinite(ymin)]))}
    return img, cover, dist, meta


def sky(H: int, W: int, cy: float) -> np.ndarray:
    y = (np.arange(H, dtype=np.float32) + 0.5)[:, None]
    t = np.clip((cy - y) / cy, 0, 1)                         # 0 at eye level, 1 at the top
    v = 0.004 + 0.045 * np.exp(-t * 6.0)                     # moonlit air thickest at the horizon
    return np.broadcast_to(v, (H, W)).astype(np.float32)


def main() -> None:
    tiles = Path(sys.argv[1])
    k = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 2
    z, west, north, dl, sk = mosaic(tiles)
    print("mosaic", z.shape, np.nanmin(z), np.nanmax(z))
    img, cover, dist, meta = render(z, west, north, dl, sk, k)
    s = sky(meta["H"], meta["W"], meta["cy"])
    out = img * cover + s * (1 - cover)
    OUT.mkdir(parents=True, exist_ok=True)
    np.save(OUT.parent.parent.parent / "output" / "posters" / "w47" / "anim" / "terrain_lin.npy", out)
    meta.update({"view_lv95": [VIEW_E, VIEW_N], "ground_m": GROUND, "eye_m": EYE, "az0": AZ0, "hfov": HFOV,
                 "horizon_frac": HORIZON, "cell_m": CELL, "moon": [MOON_AZ, MOON_ALT]})
    (OUT / "terrain_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(meta)


if __name__ == "__main__":
    main()
