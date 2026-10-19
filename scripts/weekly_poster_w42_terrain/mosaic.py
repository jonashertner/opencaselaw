#!/usr/bin/env python3
"""swissALTI3D tiles in dem/ -> one heightfield at 4 m (mosaic.npy + mosaic.json with LV95 extent)."""
import json, re, sys
from pathlib import Path
import numpy as np, tifffile
HERE = Path(__file__).resolve().parent
DEM = HERE / (sys.argv[1] if len(sys.argv) > 1 else "dem")
NAME = sys.argv[2] if len(sys.argv) > 2 else "mosaic"
tiles = {}
for p in DEM.glob("*.tif"):
    m = re.match(r"swissalti3d_(\d{4})_(\d{4})-(\d{4})_2_", p.name)
    year, e, n = int(m[1]), int(m[2]), int(m[3])
    if (e, n) not in tiles or tiles[(e, n)][0] < year:
        tiles[(e, n)] = (year, p)
es = sorted({e for e, n in tiles}); ns = sorted({n for e, n in tiles})
e0, e1, n0, n1 = es[0], es[-1] + 1, ns[0], ns[-1] + 1
R = 4  # m
W_, H_ = (e1 - e0) * 1000 // R, (n1 - n0) * 1000 // R
z = np.full((H_, W_), np.nan, np.float32)
for (e, n), (year, p) in tiles.items():
    a = tifffile.imread(p).astype(np.float32)          # 500 x 500 at 2 m, row 0 = north edge
    a[a < -1000] = np.nan
    a = a.reshape(250, 2, 250, 2).mean(axis=(1, 3))
    r0 = (n1 - (n + 1)) * 1000 // R; c0 = (e - e0) * 1000 // R
    z[r0:r0 + 250, c0:c0 + 250] = a
print("tiles", len(tiles), "years", sorted({y for y, _ in tiles.values()}), "extent E", e0, e1, "N", n0, n1, "shape", z.shape, "nan", np.isnan(z).mean())
np.save(HERE / f"{NAME}.npy", z)
json.dump({"e0": e0 * 1000, "n1": n1 * 1000, "res": R, "shape": z.shape}, open(HERE / f"{NAME}.json", "w"))
# local maxima above a threshold: the summits
from scipy import ndimage as ndi
zz = np.nan_to_num(z, nan=0)
mx = ndi.maximum_filter(zz, size=151)
peaks = np.argwhere((zz == mx) & (zz > np.percentile(zz, 99.3)))
for r, c in sorted(peaks, key=lambda rc: -zz[rc[0], rc[1]])[:12]:
    print(f"peak {zz[r, c]:.0f} m at E {e0 * 1000 + c * R:.0f} N {n1 * 1000 - r * R:.0f}")
