#!/usr/bin/env python3
"""Switzerland's heights for the week-46 Nebelmeer poster and map.

Input: DHM200.asc from swisstopo's free height model DHM25/200 m
(https://data.geo.admin.ch/ch.swisstopo.digitales-hoehenmodell_25/data.zip; terms: free use,
the source must be named: "© swisstopo"). The grid is 1926 x 1201 cells of 200 m in LV03.

Output: dhm200_ch.png, 16-bit greyscale, height in metres + 1, 0 outside Switzerland; cut to the
national border (swissBOUNDARIES3D, as in weekly_poster_cantons.json) and cropped to its box.
dhm200_ch.json holds the km position of the crop.

  python3 build_relief.py DHM200.asc
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
E0, N1, CELL = 479.9, 302.1, 0.2          # km: west edge, north edge, cell size


def main(src: str) -> None:
    import weekly_poster_w46_map as m     # the border ring, joined
    txt = Path(src).read_text().split("\n", 6)[6]
    z = np.array(txt.split(), dtype=np.float32).reshape(1201, 1926)
    _, _, border = m.geometry()
    mask = Image.new("L", (z.shape[1], z.shape[0]), 0)
    ImageDraw.Draw(mask).polygon([((x - E0) / CELL, (N1 - y) / CELL) for x, y in border], fill=1)
    inside = (np.asarray(mask) > 0) & (z > -9000)
    rows, cols = np.nonzero(inside.any(1))[0], np.nonzero(inside.any(0))[0]
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    out = np.where(inside, np.round(z) + 1, 0).astype(np.uint16)[r0:r1, c0:c1]
    Image.fromarray(out).save(HERE / "dhm200_ch.png", optimize=True)
    meta = {"e0": round(E0 + c0 * CELL, 1), "n1": round(N1 - r0 * CELL, 1), "cell": CELL,
            "lowest": int(out[out > 0].min() - 1), "highest": int(out.max() - 1),
            "source": "swisstopo DHM25/200 m", "attribution": "© swisstopo"}
    (HERE / "dhm200_ch.json").write_text(json.dumps(meta) + "\n")
    print(out.shape, meta, (HERE / "dhm200_ch.png").stat().st_size)


if __name__ == "__main__":
    main(sys.argv[1])
