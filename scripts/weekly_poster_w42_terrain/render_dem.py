#!/usr/bin/env python3
"""Painted panorama from a real heightfield (swissALTI3D, 4 m mosaic).

Camera: pinhole without roll, horizon row V0 (a shifted lens: vertical lines stay vertical, as in
the painted panoramas). Each image column is a vertical plane; the terrain is ray-marched along it
(floating-horizon algorithm). Horizontal focal length FH, vertical FV (FV/FH = vertical exaggeration).

    venv/bin/python render_dem.py <config.json>    -> terrain.png (2x poster), terrain_meta.npz

The meta carries, at poster resolution (1080 x 940): the skyline row per column, elevation and the
screen-space fall-line direction per pixel, for the run refit.
"""
from __future__ import annotations

import json, math, sys
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from PIL import Image

HERE = Path(__file__).resolve().parent
W, SCENE_H = 1080, 940          # poster coordinates
SS = 2                          # supersampling (both axes) on top of the 2x poster render
OUT_SCALE = 2


def load_mosaic(name="mosaic"):
    z = np.load(HERE / f"{name}.npy")
    meta = json.load(open(HERE / f"{name}.json"))
    return np.nan_to_num(z, nan=float(np.nanmin(z))), meta


def to_grid(E, N, meta):
    """LV95 -> fractional (row, col) in the mosaic."""
    r = (meta["n1"] - N) / meta["res"]
    c = (E - meta["e0"]) / meta["res"]
    return r, c


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def fbm(shape, rng, octaves=5, base=4.0):
    """Cheap value noise in [0,1] via upsampled random grids."""
    out = np.zeros(shape, np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        n = (int(base * 2 ** o) + 2, int(base * 2 ** o * shape[1] / shape[0]) + 2)
        g = rng.random(n).astype(np.float32)
        out += amp * ndi.zoom(g, (shape[0] / n[0], shape[1] / n[1]), order=3)[: shape[0], : shape[1]]
        tot += amp
        amp *= .5
    return out / tot


class Grid:
    """A heightfield with its derived fields (normals, slope, shadow, materials)."""

    def __init__(self, name, cfg, rng, S, vex, sun_el, fine=True):
        self.z, self.meta = load_mosaic(name)
        z, res = self.z, self.meta["res"]
        self.res = res
        sig = cfg.get("shade_sigma", 3.0) if fine else .8
        self.zs = ndi.gaussian_filter(z, 1.0 if fine else .8)        # geometry (skyline, occlusion)
        self.zsh = ndi.gaussian_filter(z, sig)                         # shading (painted, not every bump)
        gy, gx = np.gradient(self.zsh, res)
        self.gN, self.gE = -gy, gx
        gy2, gx2 = np.gradient(ndi.gaussian_filter(z, 1.5 if fine else .8), res)
        self.slope = np.degrees(np.arctan(np.hypot(gx2, gy2))).astype(np.float32)
        self.slope_s = ndi.gaussian_filter(self.slope, 4.0 if fine else .8)
        nrm_v = np.stack([-self.gE * vex, -self.gN * vex, np.ones_like(z)], -1)
        nrm_v /= np.linalg.norm(nrm_v, axis=-1, keepdims=True)
        self.lambert = np.clip(nrm_v @ S, 0, 1).astype(np.float32)
        # cast shadows: march toward the sun on a coarser grid
        k = 2 if fine else 1
        zc = self.zs[::k, ::k]
        step = res * k
        hz = np.zeros_like(zc)
        dE, dN = S[0], S[1]
        nn = math.hypot(dE, dN); dE, dN = dE / nn, dN / nn
        rows, cols = np.indices(zc.shape)
        for i in range(1, 160 if fine else 90):
            d = i * step * (1 + i * .02)
            rr = rows - d * dN / step
            cc = cols + d * dE / step
            zz = ndi.map_coordinates(zc, [rr, cc], order=1, mode="nearest")
            hz = np.maximum(hz, (zz - zc) / d)
        shadow_c = smoothstep(math.tan(sun_el) - .04, math.tan(sun_el) + .06, hz)
        sh = ndi.zoom(ndi.gaussian_filter(shadow_c, 1.0), k, order=1)
        self.shadow = np.zeros_like(z); hh, ww = min(sh.shape[0], z.shape[0]), min(sh.shape[1], z.shape[1])
        self.shadow[:hh, :ww] = sh[:hh, :ww]
        # materials
        treeline = cfg.get("treeline", 2150) + 70 * (fbm(z.shape, rng, 4, 3) - .5) * 2
        rock = smoothstep(cfg.get("rock_lo", 40), cfg.get("rock_hi", 52), self.slope_s)
        rock = np.maximum(rock, smoothstep(62, 72, self.slope))
        rock = ndi.gaussian_filter(rock, 1.5 if fine else .5)
        hard = rock > .5
        if fine:   # drop small specks: rock reads as coherent masses on the cliffs
            lab, n = ndi.label(hard)
            sizes = ndi.sum(hard, lab, index=np.arange(1, n + 1))
            keep = np.zeros(n + 1, bool); keep[1:] = sizes >= cfg.get("rock_min_cells", 250)
            hard = keep[lab]
            hard = ndi.binary_closing(hard, iterations=2)
        self.rock = smoothstep(.35, .65, ndi.gaussian_filter(hard.astype(np.float32), 1.2)).astype(np.float32)
        forest = smoothstep(treeline + 60, treeline - 120, z) * smoothstep(42, 30, self.slope)
        forest *= smoothstep(.25, .55, fbm(z.shape, rng, 5, 6))
        forest *= smoothstep(cfg.get("valley_floor", 1700) - 80, cfg.get("valley_floor", 1700) + 60, z)
        self.forest = forest.astype(np.float32)
        self.tree_tex = fbm(z.shape, rng, 2, 160 if fine else 24)       # tree tops: fine, crisp
        self.ledge = fbm(z.shape, rng, 4, 40 if fine else 8)

    def coords(self, E, N):
        r = (self.meta["n1"] - N) / self.res
        c = (E - self.meta["e0"]) / self.res
        inside = (r > 1) & (r < self.z.shape[0] - 2) & (c > 1) & (c < self.z.shape[1] - 2)
        return r, c, inside


def render(cfg: dict):
    rng = np.random.default_rng(cfg.get("seed", 7))
    # ── camera ──
    cE, cN, cz = cfg["cam"]
    az = math.radians(cfg["azimuth"])         # bearing of the view direction, clockwise from north
    F = np.array([math.sin(az), math.cos(az)])
    R = np.array([math.cos(az), -math.sin(az)])
    FH, FV, V0, U0 = cfg["fh"], cfg["fv"], cfg["v0"], cfg.get("u0", W / 2)
    vex = FV / FH
    # ── sun (toward the sun), from the upper left of the view ──
    sun_rel = math.radians(cfg.get("sun_rel", -62))   # relative to the view direction, negative = left
    sun_el = math.radians(cfg.get("sun_el", 38))
    sun_az = az + math.pi - sun_rel                   # behind-left of the camera for negative sun_rel
    S = np.array([math.cos(sun_el) * math.sin(sun_az), math.cos(sun_el) * math.cos(sun_az), math.sin(sun_el)])
    fine = Grid(cfg.get("mosaic", "mosaic"), cfg, rng, S, vex, sun_el, fine=True)
    far = Grid(cfg["far"], cfg, rng, S, vex, sun_el, fine=False) if cfg.get("far") else None
    print("grids ready")

    # ── ray march ──
    cols_px = np.arange(W * OUT_SCALE * SS) / (OUT_SCALE * SS) + .5 / (OUT_SCALE * SS)
    tan_u = (cols_px - U0) / FH
    ncol = cols_px.size
    ts = []
    t = cfg.get("t_min", 400.0)
    while t < cfg.get("t_max", 16000.0):
        ts.append(t)
        t += min(max(t / 700, 2.5), 30)
    ts = np.array(ts, np.float32)
    nstep = ts.size
    print("columns", ncol, "steps", nstep)
    rows_out = SCENE_H * OUT_SCALE * SS
    # attributes per (step, column)
    E = cE + ts[:, None] * (F[0] + tan_u[None, :] * R[0])
    N = cN + ts[:, None] * (F[1] + tan_u[None, :] * R[1])
    r1, c1, in1 = fine.coords(E, N)
    if far is not None:
        r2, c2, in2 = far.coords(E, N)
        inside = in1 | in2
    else:
        inside = in1

    def samp(name):
        a = ndi.map_coordinates(getattr(fine, name), [r1, c1], order=1, mode="nearest").astype(np.float32)
        if far is not None:
            b = ndi.map_coordinates(getattr(far, name), [r2, c2], order=1, mode="nearest").astype(np.float32)
            a = np.where(in1, a, b)
        return a
    zq = samp("zs")
    v = V0 - FV * (zq - cz) / ts[:, None]                 # screen row (poster units)
    v = np.where(inside, v, 1e9)
    vpx = v * OUT_SCALE * SS
    # floating horizon: the visible sample for each pixel row is the first step whose cummin <= row
    cm = np.minimum.accumulate(vpx, axis=0)
    img_idx = np.empty((rows_out, ncol), np.int32)
    rows_px = np.arange(rows_out, dtype=np.float32) + .5
    for j in range(ncol):
        col = cm[:, j]
        # first step index where col <= row  (col is non-increasing): searchsorted on -col
        img_idx[:, j] = np.searchsorted(-col, -rows_px, side="left")
    sky = img_idx >= nstep
    img_idx = np.minimum(img_idx, nstep - 1)

    # ── shade the samples (painted: banded light, cool shadow faces, warm lit snow) ──
    lam = samp("lambert"); sh = samp("shadow"); rk = samp("rock"); fo = samp("forest"); tt = samp("tree_tex"); sl = samp("slope")
    light = np.clip((lam - cfg.get("key_lo", .25)) / (1 - cfg.get("key_lo", .25)), 0, 1) * (1 - .92 * sh)

    def bands(x, n, soft=.35, keep=.3):
        q = np.floor(x * n) / n + smoothstep(.5 - soft / 2, .5 + soft / 2, (x * n) % 1) / n
        return (1 - keep) * q + keep * x

    key = bands(np.clip(light, 0, 1), cfg.get("bands", 5))
    snow_lit = np.array([254, 252, 247]) / 255
    snow_mid = np.array([224, 232, 243]) / 255
    snow_sh = np.array([108, 138, 190]) / 255
    rock_lit = np.array([150, 138, 126]) / 255
    rock_mid = np.array([88, 94, 106]) / 255
    rock_sh = np.array([38, 46, 62]) / 255
    for_lit = np.array([78, 122, 84]) / 255
    for_sh = np.array([24, 48, 36]) / 255

    def ramp(lo, mid, hi, k):
        k = k[..., None]
        return np.where(k < .5, lo + (mid - lo) * (k / .5), mid + (hi - mid) * ((k - .5) / .5))

    snow = ramp(snow_sh, snow_mid, snow_lit, key)
    snow = snow * (1 + .03 * smoothstep(.8, 1, key))[..., None] * np.array([1, .995, .98])
    rockc = ramp(rock_sh, rock_mid, rock_lit, bands(np.clip(light * .95, 0, 1), 4))
    ftex = smoothstep(.35, .65, tt)
    forc = ramp(for_sh, (for_lit + for_sh) / 2, for_lit, np.clip(key * (.55 + .7 * ftex), 0, 1))
    forc = forc * (1 - .25 * smoothstep(.55, .9, tt))[..., None]           # dark gaps between crowns
    rk_eff = rk * smoothstep(.15, .6, samp("ledge") * .5 + .5 * smoothstep(42, 60, sl))
    colr = snow * (1 - rk_eff[..., None]) + rockc * rk_eff[..., None]
    colr = colr * (1 - fo[..., None]) + forc * fo[..., None]
    # snow surface: a faint fall-line streakiness from the micro relief (couloirs), kept subtle
    micro = np.clip((samp("zs") - samp("zsh")) / 6.0, -1, 1)
    colr = colr * (1 + (.06 + .22 * rk_eff) * micro * (1 - fo))[..., None]
    # aerial perspective
    haze_col = np.array([200, 214, 232]) / 255
    t0 = cfg.get("haze_t0", 5500.0)
    hz_f = 1 - np.exp(-np.maximum(ts[:, None] - t0, 0) / cfg.get("haze_km", 5000.0))
    hz_f = hz_f * (1 - .25 * smoothstep(2600, 4200, zq))
    colr = colr * (1 - hz_f[..., None]) + haze_col * hz_f[..., None]

    # gather pixels
    rgb = np.empty((rows_out, ncol, 3), np.float32)
    jj = np.arange(ncol)[None, :].repeat(rows_out, 0)
    rgb[:] = colr[img_idx, jj]
    zpix = zq[img_idx, jj]
    fo_pix = fo[img_idx, jj]
    if fo_pix.max() > 0:
        crowns = rng.random(rgb.shape[:2]).astype(np.float32)
        crowns = ndi.gaussian_filter(crowns, 1.6)
        crowns = (crowns - crowns.mean()) / (crowns.std() + 1e-6)
        tips = smoothstep(.6, 1.6, crowns)                                 # bright crown tips
        gaps = smoothstep(-.6, -1.8, crowns)                               # dark gaps
        mod = 1 + .22 * tips - .35 * gaps
        rgb = rgb * (1 - fo_pix[..., None]) + rgb * mod[..., None] * fo_pix[..., None]
    rgb = np.where(sky[..., None], 0.0, rgb)
    alpha = (~sky).astype(np.float32)
    # downsample
    def down(a, f):
        h, w = a.shape[0] // f, a.shape[1] // f
        return a[: h * f, : w * f].reshape(h, f, w, f, *a.shape[2:]).mean(axis=(1, 3))
    a_out = down(alpha, SS)
    out = down(rgb, SS) / np.maximum(a_out, 1e-3)[..., None]            # un-premultiply at the skyline
    out = ndi.gaussian_filter(out, (cfg.get("soft_px", 1.2), cfg.get("soft_px", 1.2), 0))
    rgba = np.dstack([np.clip(out, 0, 1), np.clip(a_out, 0, 1)])
    img = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")
    img.save(HERE / "terrain.png")

    # ── meta at poster resolution ──
    f = OUT_SCALE * SS
    sky_p = down(sky.astype(np.float32), f) > .5
    skyline = np.array([int(np.argmax(~sky_p[:, j])) if (~sky_p[:, j]).any() else SCENE_H for j in range(W)])
    z_p = down(zpix, f)
    idx_p = img_idx[::f, ::f]
    # fall-line direction in screen space: project P and P + eps * downhill
    gE_s, gN_s = samp("gE"), samp("gN")
    stepl = 30.0
    dE_ = -gE_s / (np.hypot(gE_s, gN_s) + 1e-6) * stepl
    dN_ = -gN_s / (np.hypot(gE_s, gN_s) + 1e-6) * stepl
    dz_ = -(np.hypot(gE_s, gN_s)) * stepl
    E2, N2, z2 = E + dE_, N + dN_, zq + dz_
    t2 = (E2 - cE) * F[0] + (N2 - cN) * F[1]
    u2 = U0 + FH * ((E2 - cE) * R[0] + (N2 - cN) * R[1]) / t2
    v2 = V0 - FV * (z2 - cz) / t2
    u1 = U0 + FH * tan_u[None, :].repeat(nstep, 0)
    du, dv = u2 - u1, v2 - v
    nn = np.hypot(du, dv) + 1e-6
    fall = np.stack([du / nn, dv / nn], -1)
    jj_p = np.arange(0, ncol, f)[None, :].repeat(idx_p.shape[0], 0)
    fall_p = fall[idx_p, jj_p]
    E_p, N_p = E[idx_p, jj_p], N[idx_p, jj_p]
    fall_p[sky_p] = 0
    fall_p[..., 1] = np.abs(fall_p[..., 1])
    fx = ndi.gaussian_filter(fall_p[..., 0], 9); fy = ndi.gaussian_filter(fall_p[..., 1], 9)
    nn = np.hypot(fx, fy) + 1e-9
    summ = []
    for (sE, sN, sz) in cfg.get("summits", []):
        tt_ = (sE - cE) * F[0] + (sN - cN) * F[1]
        uu = U0 + FH * ((sE - cE) * R[0] + (sN - cN) * R[1]) / tt_
        vv = V0 - FV * (sz - cz) / tt_
        col = int(round(uu))
        summ.append((uu, skyline[col] if 0 <= col < W else vv))
        print(f"summit {sz} m -> u {uu:.1f} v {vv:.1f} (skyline there {skyline[col] if 0 <= col < W else None})")
    np.savez_compressed(HERE / "terrain_meta.npz", skyline=skyline, z=z_p, fall=fall_p, fx=(fx / nn).astype(np.float32),
                        fy=(fy / nn).astype(np.float32), E=E_p, N=N_p, sky=sky_p, summits=np.array(summ, np.float32),
                        slope=sl[idx_p, jj_p], rock=rk_eff[idx_p, jj_p], forest=fo[idx_p, jj_p])
    np.savez_compressed(HERE / "terrain_runs.npz", skyline=skyline, fx=(fx / nn).astype(np.float16), fy=(fy / nn).astype(np.float16),
                        sky=sky_p, summits=np.array(summ, np.float32))
    print("skyline min row", skyline.min(), "at col", int(skyline.argmin()))
    return img


if __name__ == "__main__":
    cfg = json.load(open(sys.argv[1]))
    render(cfg)
