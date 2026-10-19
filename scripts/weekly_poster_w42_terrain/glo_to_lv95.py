#!/usr/bin/env python3
"""Copernicus GLO-30 tile(s) -> coarse LV95 heightfield (30 m) for the far terrain: weiss_far.npy/json."""
import json, numpy as np, tifffile
from scipy import ndimage as ndi
tiles = {(46, 7): tifffile.imread("dem_glo/N46_E007.tif").astype(np.float32)}
print({k: v.shape for k, v in tiles.items()})
E0, E1, N0, N1, R = 2595000, 2650000, 1085000, 1125000, 30
Es = np.arange(E0, E1, R, dtype=np.float64); Ns = np.arange(N1, N0, -R, dtype=np.float64)
EE, NN = np.meshgrid(Es, Ns)
# LV95 -> WGS84 (swisstopo approximate formulas)
y = (EE - 2600000) / 1e6; x = (NN - 1200000) / 1e6
lon = 2.6779094 + 4.728982 * y + .791484 * y * x + .1306 * y * x**2 - .0436 * y**3
lat = 16.9023892 + 3.238272 * x - .270978 * y**2 - .002528 * x**2 - .0447 * y**2 * x - .0140 * x**3
lon, lat = lon * 100 / 36, lat * 100 / 36
out = np.full(EE.shape, np.nan, np.float32)
for (la0, lo0), a in tiles.items():
    h, w = a.shape
    rr = (la0 + 1 - lat) * (h - 1); cc = (lon - lo0) * (w - 1)   # GLO-30 pixel-is-point grid, 1° span
    m = (rr >= 0) & (rr <= h - 1) & (cc >= 0) & (cc <= w - 1)
    v = ndi.map_coordinates(a, [rr[m], cc[m]], order=1)
    out[m] = v
print("nan share", np.isnan(out).mean(), "max", np.nanmax(out))
np.save("weiss_far.npy", out)
json.dump({"e0": E0, "n1": N1, "res": R, "shape": out.shape}, open("weiss_far.json", "w"))
# sanity: the Weisshorn
r, c = (N1 - 1105568) / R, (2621452 - E0) / R
print("GLO at Weisshorn", out[int(r) - 2:int(r) + 3, int(c) - 2:int(c) + 3].max())
