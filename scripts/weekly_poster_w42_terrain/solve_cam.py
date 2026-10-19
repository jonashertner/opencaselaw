#!/usr/bin/env python3
"""Camera candidates for the Weisshorn-Bishorn panorama: for each camera position, the focal lengths
and horizon row that put the Weisshorn at (471, Y1) and the Bishorn at column 689, and what the
bottom sight line (poster row 900) hits in the Weisshorn column."""
import json, math, sys
import numpy as np
from scipy import ndimage as ndi
z = np.nan_to_num(np.load("weiss_far.npy"), nan=0); meta = json.load(open("weiss_far.json"))
res = meta["res"]
def zat(E, N):
    r, c = (meta["n1"] - N) / res, (E - meta["e0"]) / res
    return float(ndi.map_coordinates(z, [[r], [c]], order=1, mode="nearest")[0])
S1 = (2621452, 1105568, 4504); S2 = (2621352, 1107416, 4149)
Y1 = float(sys.argv[1]) if len(sys.argv) > 1 else 400
CAMS = [(2612600, 1109000, 2700, 1000), (2612000, 1108000, 2800, 1000), (2613000, 1107000, 2600, 1000), (2612500, 1106000, 2800, 900), (2611500, 1107500, 3000, 900), (2613200, 1108200, 2500, 1000)]
for cE, cN, cz, vz in CAMS:
    ground = zat(cE, cN)
    d1 = math.hypot(S1[0] - cE, S1[1] - cN); d2 = math.hypot(S2[0] - cE, S2[1] - cN)
    b1 = math.atan2(S1[0] - cE, S1[1] - cN); b2 = math.atan2(S2[0] - cE, S2[1] - cN)
    # view azimuth: Weisshorn at u=471, Bishorn at u=689 -> solve az, fh
    # u = 540 + fh*tan(b - az); two equations -> search az
    best = None
    for az in np.linspace(min(b1, b2) - .6, max(b1, b2) + .6, 2401):
        t1, t2 = math.tan(b1 - az), math.tan(b2 - az)
        if abs(t2 - t1) < 1e-6: continue
        fh = (391 - 609) / (t2 - t1)
        u0 = 609 - fh * t1
        err = abs(u0 - 540)
        if fh > 0 and (best is None or err < best[0]): best = (err, az, fh, u0)
    _, az, fh, u0 = best
    rel1, rel2 = (S1[2] - cz) / d1, (S2[2] - cz) / d2
    # fv, v0 from Weisshorn at Y1 and valley line (900) at altitude vz in the Weisshorn column
    fv = (900 - Y1) / (rel1 + (cz - vz) / d1)
    v0 = Y1 + fv * rel1
    y2 = v0 - fv * rel2
    # what does the bottom sight line hit in column 471? march
    hit = None
    F = (math.sin(az), math.cos(az))
    for t in np.arange(200, 16000, 10):
        E, N = cE + t * (F[0] + math.tan(b1 - az) * math.cos(az)), cN + t * (F[1] - math.tan(b1 - az) * math.sin(az))
        if not (meta["e0"] < E < meta["e0"] + z.shape[1] * res and meta["n1"] - z.shape[0] * res < N < meta["n1"]): continue
        zl = cz - (900 - v0) / fv * t
        if zat(E, N) > zl: hit = (t, zat(E, N)); break
    # first occluder: does near terrain rise above the camera's sight line to the summit?
    print(f"cam E{cE} N{cN} z{cz} (ground {ground:.0f}) az {math.degrees(az):.1f} fh {fh:.0f} fv {fv:.0f} vex {fv/fh:.2f} v0 {v0:.0f} | S1 y {Y1:.0f} d {d1:.0f} | S2 y {y2:.0f} d {d2:.0f} | ZH h {900-34-y2:.0f} of 430 | bottom hits {hit}")
