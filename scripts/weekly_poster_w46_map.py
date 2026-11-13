#!/usr/bin/env python3
"""Week 46 (6 - 12 November 2026): the animated map, one self-contained HTML page.

The German poster set in motion. Over Switzerland's relief the fog rises from the lowest ground
(Lago Maggiore, 193 m) to 1,132 metres above sea level while the figure counts from 0 to 1,132:
one metre for each of the week's new decisions. The section from Basel to Monte Rosa fills with
it. Under the pointer the page reads the height of the ground and says how far it lies under or
above the fog.

Real: the heights (DHM25/200 m, © swisstopo) and the border (swissBOUNDARIES3D). Drawn: the light
from the north-west, the fog's thinning towards its shore, the shadows on it. The page draws the
map on the GPU from one texture that holds each point's height and shading.

    python3 scripts/weekly_poster_w46_map.py            # -> output/posters/w46/map.html
    python3 scripts/weekly_poster_w46_map.py --site     # also docs/posters/2026-w46/map/ (archive step)
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).parent
OUT = HERE.parent / "output" / "posters" / "w46" / "map.html"
SITE = HERE.parent / "docs" / "posters" / "2026-w46" / "map" / "index.html"
TEX_W = 1400                            # texture width in pixels


def geometry() -> tuple[dict, list, np.ndarray]:
    """Cantons, lakes and the national border as one ring (km), from weekly_poster_cantons.json."""
    data = json.loads((HERE / "weekly_poster_cantons.json").read_text(encoding="utf-8"))
    lakes = [np.array(r) for lk in data["lakes"] for r in lk["rings"]]
    chains = [np.array(c) for c in data["border"]]
    chains = [c for c in chains if np.abs(c[0] - c[-1]).sum() > 1e-6]      # an enclave's own ring stays out
    ring = chains.pop(0)
    while chains:                                                          # swisstopo's chains, end to end
        end = ring[-1]
        _, i, flip = min((min(np.hypot(*(c[0] - end)), np.hypot(*(c[-1] - end))), i,
                          np.hypot(*(c[-1] - end)) < np.hypot(*(c[0] - end))) for i, c in enumerate(chains))
        c = chains.pop(i)
        ring = np.vstack([ring, c[::-1] if flip else c])
    x, y = ring[:, 0], ring[:, 1]
    area = abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) / 2
    assert 41000 < area < 41600, area                                      # Switzerland: 41,285 km2
    return data["cantons"], lakes, ring


def build() -> dict:
    import weekly_poster_w46_nebelmeer as nb

    v = json.loads((HERE / "weekly_poster_w46_data.json").read_text(encoding="utf-8"))["verified"]
    z1, meta = nb.heights()
    inside = z1 > 0
    shade = nb.shade(z1 - 1, inside, meta["cell"] * 1000)
    w, h = TEX_W, round(TEX_W * z1.shape[0] / z1.shape[1])
    small = lambda a: np.asarray(Image.fromarray(a.astype(np.float32)).resize((w, h), Image.BILINEAR))  # noqa: E731
    cover = small(inside)
    zs = small(np.where(inside, z1 - 1, 0)) / np.maximum(cover, 1e-6)      # heights averaged over Swiss ground only
    ss = small(np.where(inside, shade, 0)) / np.maximum(cover, 1e-6)
    ins = cover > 0.5
    zq = np.where(ins, np.clip(np.round(zs), 0, 65000) + 1, 0).astype(np.uint16)
    rgb = np.stack([(zq >> 8).astype(np.uint8), (zq & 255).astype(np.uint8),
                    np.where(ins, np.clip(ss * 255, 0, 255), 0).astype(np.uint8)], axis=-1)
    png = io.BytesIO()
    Image.fromarray(rgb).save(png, "PNG", optimize=True)

    km, z = nb.profile()
    last = int(np.nonzero(z > 0)[0][-1])
    km, z = km[:last + 1], z[:last + 1]
    total = v["added"]
    under = float((inside & (z1 - 1 < total)).sum() / inside.sum())
    (e0, n0), (e1, n1) = nb.PROFILE
    fx = lambda e: (e - meta["e0"]) / (z1.shape[1] * meta["cell"])          # noqa: E731  (0..1 across the map)
    fy = lambda n: (meta["n1"] - n) / (z1.shape[0] * meta["cell"])          # noqa: E731
    return {"total": total, "week": 46, "tw": w, "th": h, "tex": base64.b64encode(png.getvalue()).decode(),
            "lowest": meta["lowest"], "under": round(under * 100),
            "km": round(float(km[-1]), 2), "prof": [round(float(a), 1) for a in z[::3]],
            "line": [round(fx(e0), 4), round(fy(n0), 4), round(fx(e1), 4), round(fy(n1), 4)],
            "box": [meta["e0"], meta["n1"], round(z1.shape[1] * meta["cell"], 1), round(z1.shape[0] * meta["cell"], 1)]}


def main() -> None:
    d = build()
    page = (HERE / "weekly_poster_w46_map.html").read_text(encoding="utf-8").replace(
        "/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    head = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Week 46 of Swiss case law as an animated map: over Switzerland's relief the fog rises to 1,132 metres, one metre for each new decision.">
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(head + page + "\n</html>\n", encoding="utf-8")
    if "--site" in sys.argv:                 # the site copy belongs to the archive step
        # the site loads nothing from third parties: its copy carries the two faces itself
        # (Latin subsets as distributed by Google Fonts, SIL Open Font License, see assets/fonts/)
        fonts = HERE / "weekly_poster_w46_assets" / "fonts"
        face = lambda family, weight, name: (  # noqa: E731
            f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{base64.b64encode((fonts / name).read_bytes()).decode()}) format('woff2')}}")
        links = [ln for ln in page.splitlines() if "fonts.googleapis.com" in ln]
        assert len(links) == 2, links
        own = page
        for ln in links:
            own = own.replace(ln + "\n", "")
        own = own.replace("<style>\n", "<style>\n" + face("Instrument Serif", "400", "instrument-serif-latin.woff2") + "\n"
                          + face("Inter Tight", "400 600", "inter-tight-latin.woff2") + "\n", 1)
        assert "googleapis" not in own and "gstatic" not in own
        SITE.parent.mkdir(parents=True, exist_ok=True)
        SITE.write_text(head + '<link rel="canonical" href="https://opencaselaw.ch/posters/2026-w46/map/">\n'
                        '<meta property="og:title" content="Nebelmeer"><meta property="og:type" content="website">\n'
                        '<meta property="og:description" content="Swiss case law as an animated map: the fog rises to 1,132 metres, one metre for each new decision of the week.">\n'
                        '<meta property="og:url" content="https://opencaselaw.ch/posters/2026-w46/map/">\n'
                        '<meta property="og:image" content="https://opencaselaw.ch/posters/2026-w46/de-nebelmeer.webp">\n'
                        + own + "\n</html>\n", encoding="utf-8")
    print(OUT, len(page), "bytes")


if __name__ == "__main__":
    main()
