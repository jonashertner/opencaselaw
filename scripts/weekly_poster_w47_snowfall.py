#!/usr/bin/env python3
"""Week 47 (13 - 19 November 2026): the film, one self-contained HTML page.

974 new decisions, 974 snow crystals photographed by Wilson A. Bentley. The film opens on one of
them, a stellar dendrite printed by Bentley and held by the Metropolitan Museum of Art, so close
that it fills the frame. The camera pulls back through the snowfall, then the plane of focus
travels out through the falling crystals, from the lens to the Bernina group under the moon. A
crystal is counted at the moment the camera's focus reaches it, and flares as it does; the counter
ends on 974 when the focus has passed the last one, and the mountains come sharp.

Real: the crystals (Bentley's photomicrographs: 24 prints at the Met, CC0, re-cut whole from the
scans; 13 Commons files cut for the Romansh poster, public domain), the mountains (swissALTI3D, the
view from the terrace of Muottas Muragl, see weekly_poster_w47_snowfall_terrain.py). Drawn: the
light, the motion of the fall, the lens (its depth of field and the iris closing as it racks).

    python3 scripts/weekly_poster_w47_snowfall.py            # -> output/posters/w47/snowfall.html
    python3 scripts/weekly_poster_w47_snowfall.py --site     # also docs/posters/2026-w47/film/ (archive step)
"""
from __future__ import annotations

import base64
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).parent
ASSETS = HERE / "weekly_poster_w47_assets" / "snowfall"
OUT = HERE.parent / "output" / "posters" / "w47" / "snowfall.html"
SITE = HERE.parent / "docs" / "posters" / "2026-w47" / "film" / "index.html"

# the frame and the lens, in css px of the 1080 x 1350 sheet; must match the terrain render
W, H = 1080.0, 1350.0
TERRAIN = json.loads((ASSETS / "terrain_meta.json").read_text(encoding="utf-8"))
F_PX = (W / 2) / math.tan(math.radians(TERRAIN["hfov"] / 2))
CX, CY = W / 2, H * TERRAIN["horizon_frac"]

# the camera (millimetres; the camera looks along +z, its final position is z = 0)
T_HOLD, T_PULL, T_RACK, T_INF, T_END = 1.6, 8.0, 13.4, 14.6, 20.0
D0, D1 = 5.5, 136.0            # camera to the first crystal: at the start, at the end of the pull
ZM = D1                        # the first crystal's depth from the final camera
F_END = 4200.0                 # the rack reaches this far before it goes to infinity
A0, A1 = 0.9, 0.14             # aperture diameter, mm: wide open on the first crystal, closed at the end
MACRO_SIZE = 3.6               # mm across
D_VIS = 22.0                   # nearer the lens than this a crystal is a faint blur, faded out, not yet counted


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def pull_ease(u: float) -> float:
    """The pull-back's ease: quintic (no jolt at either end), on u^0.8 so the settle is the longer half."""
    u = min(1.0, max(0.0, u)) ** 0.8
    return u * u * u * (u * (u * 6 - 15) + 10)


def cam_dist(t: float) -> float:
    """Camera to the first crystal."""
    return D0 * (D1 / D0) ** pull_ease((t - T_HOLD) / (T_PULL - T_HOLD))


def cam_z(t: float) -> float:
    return ZM - cam_dist(t)


# The camera backs away along its line of sight to the first crystal, the ray through the centre of
# the frame (the lens's eye level sits lower, at CY), so that crystal stays fixed at the centre from
# the first frame to the last: one move, no drift. The final camera is the origin.
RY = (CY - H / 2) / F_PX           # rise of that ray per mm of depth
YM = D1 * RY                       # the first crystal's height above the final camera


def cam_y(t: float) -> float:
    return YM - cam_dist(t) * RY


def inv_focus(t: float) -> float:
    """1 / focus distance (1/mm), from the camera."""
    if t <= T_PULL:
        return 1.0 / cam_dist(t)
    if t <= T_RACK:
        return 1.0 / (D1 * (F_END / D1) ** ease((t - T_PULL) / (T_RACK - T_PULL)))
    return (1.0 / F_END) * (1 - ease((t - T_RACK) / (T_INF - T_RACK)))


def focus_time(depth: float) -> float:
    """When the rack (after the pull) reaches a crystal at this distance from the final camera."""
    lo, hi = T_PULL, T_RACK
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1.0 / inv_focus(mid) < depth:
            lo = mid
        else:
            hi = mid
    return hi


def position(c: dict, t: float) -> tuple[float, float, float]:
    x = c["x"] + c["sa"] * math.sin(c["sw"] * t + c["sp"])
    y = c["y"] - c["v"] * t
    return x, y, c["z"]


def screen(c: dict, t: float) -> tuple[float, float, float] | None:
    x, y, z = position(c, t)
    y -= cam_y(t)
    d = z - cam_z(t)
    if d < 1.0:
        return None
    return CX + F_PX * x / d, CY - F_PX * y / d, d


def counted(c: dict, t: float) -> bool:
    s = screen(c, t)
    return (s is not None and 0 <= s[0] <= W and 0 <= s[1] <= H and s[2] >= D_VIS
            and 1.0 / s[2] >= inv_focus(t) - 1e-12)


def make(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []
    tan_top, tan_bot, tan_side = CY / F_PX, (H - CY) / F_PX, CX / F_PX

    def base(z: float) -> dict:
        return {"z": z, "s": rng.uniform(1.4, 3.9), "k": rng.randrange(SPRITES),
                "r0": rng.uniform(0, 2 * math.pi), "rw": rng.choice((-1, 1)) * rng.uniform(0.08, 0.45),
                "ta": rng.uniform(0.15, 0.75), "tw": rng.uniform(0.35, 1.1), "tp": rng.uniform(0, 2 * math.pi),
                "ua": rng.uniform(0.15, 0.75), "uw": rng.uniform(0.35, 1.1), "up": rng.uniform(0, 2 * math.pi),
                "sw": rng.uniform(0.3, 0.9), "sp": rng.uniform(0, 2 * math.pi)}

    # the near crystals, between the final camera and the first crystal. Spread evenly over the final
    # frame, so as the camera backs away more of them come into view, about as the square of its
    # distance. They lie in a slab just in front of the first crystal's own plane: crystals nearer the
    # lens than that would pass through it on the axis and blot out the frame.
    for count, z0, z1 in ((120, ZM - 34.0, ZM - 0.6),):
        got = 0
        while got < count:
            z = rng.uniform(z0, z1)
            c = base(z)
            c["v"] = rng.uniform(0.5, 1.6)
            c["sa"] = 0.006 * z
            tc = T_PULL + 0.5                                 # the camera is at its final place
            u, w = rng.uniform(0.02, 0.98), rng.uniform(0.04, 0.97)
            d = z - cam_z(tc)
            c["x"] = (u * 2 - 1) * tan_side * d - c["sa"] * math.sin(c["sw"] * tc + c["sp"])
            c["y"] = (tan_top - w * (tan_top + tan_bot)) * d + c["v"] * tc
            if ok(c):
                out.append(c)
                got += 1

    # the rest: beyond the first crystal, counted when the rack reaches them
    lo, hi = ZM + 6.0, F_END * 0.97
    while len(out) < n - 1:
        z = lo * (hi / lo) ** (rng.random() ** 0.6)         # more of them far away, where they are fine haze
        c = base(z)
        c["v"] = rng.uniform(5.0, 11.0)
        c["sa"] = 0.006 * z
        tf = focus_time(z)                                                     # the camera is at 0 after the pull
        u, w = rng.uniform(0.02, 0.98), rng.uniform(0.03, 0.97)
        c["x"] = (u * 2 - 1) * tan_side * z - c["sa"] * math.sin(c["sw"] * tf + c["sp"])
        c["y"] = (tan_top - w * (tan_top + tan_bot)) * z + c["v"] * tf
        if ok(c):
            out.append(c)
    return out


def count_time(c: dict, dt: float = 1 / 120) -> float | None:
    """The moment the crystal is counted, on the film's own clock."""
    for i in range(int(T_END / dt) + 1):
        if counted(c, i * dt):
            return round(i * dt, 4)
    return None


def ok(c: dict) -> bool:
    c["tc"] = count_time(c)
    return c["tc"] is not None and T_HOLD <= c["tc"] <= T_RACK


SPRITES = json.loads((ASSETS / "textures.json").read_text(encoding="utf-8"))["atlas"]["n"]


def build(week: dict) -> dict:
    n = week["added"]
    cs = make(n, 47_974)
    assert len(cs) == n - 1, len(cs)
    tcs = sorted(c["tc"] for c in cs)
    assert all(T_HOLD <= t <= T_RACK for t in tcs)
    assert sum(1 for c in cs if c["tc"] <= T_RACK) + 1 == n            # every decision counted, once
    cs.sort(key=lambda c: c["z"], reverse=True)
    keys = ["x", "y", "z", "v", "sa", "sw", "sp", "s", "k", "r0", "rw", "ta", "tw", "tp", "ua", "uw", "up", "tc"]
    flat = [round(c[k], 4) if isinstance(c[k], float) else c[k] for c in cs for k in keys]
    tex = json.loads((ASSETS / "textures.json").read_text(encoding="utf-8"))
    b64 = lambda p: base64.b64encode((ASSETS / p).read_bytes()).decode()  # noqa: E731
    a, b = week["from"], week["to"]
    return {
        "n": n, "keys": keys, "c": flat,
        "cam": {"W": W, "H": H, "f": F_PX, "cx": CX, "cy": CY, "THOLD": T_HOLD, "TPULL": T_PULL, "TRACK": T_RACK,
                "TINF": T_INF, "TEND": T_END, "D0": D0, "D1": D1, "ZM": ZM, "FEND": F_END, "A0": A0, "A1": A1,
                "MS": MACRO_SIZE, "DVIS": D_VIS, "MY": YM, "RY": RY},
        "atlas": {"cols": tex["atlas"]["cols"], "rows": tex["atlas"]["rows"], "fit": tex["atlas"]["fit"] / tex["atlas"]["cell"]},
        "tsig": tex["terrain"]["levels_sigma_css"],
        "img": {"atlas": b64("atlas.png"), "far": b64("atlas_far.png"), "macro": b64("macro.jpg"),
                "t0": b64("terrain_0.jpg"), "t1": b64("terrain_1.jpg"), "t2": b64("terrain_2.jpg"), "t3": b64("terrain_3.jpg")},
        "week": {"no": week["week_no"], "line": f"Week {week['week_no']} · {a.day}–{b.day} November {b.year}"},
    }


def main() -> None:
    import poster_w47

    week = poster_w47.load()
    d = build(week)
    page = (HERE / "weekly_poster_w47_snowfall.html").read_text(encoding="utf-8").replace(
        "/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    head = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Week 47 of Swiss case law as a film: 974 snow crystals photographed by Wilson A. Bentley, one for each new decision, falling over the Bernina by moonlight.">
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(head + page + "\n</html>\n", encoding="utf-8")
    if "--site" in sys.argv:                 # the site copy belongs to the archive step
        # the site loads nothing from third parties: its copy carries the two faces itself (Latin
        # subsets as distributed by Google Fonts, SIL Open Font License, see assets/snowfall/fonts/)
        fonts = ASSETS / "fonts"
        face = lambda family, weight, name: (  # noqa: E731
            f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{base64.b64encode((fonts / name).read_bytes()).decode()}) format('woff2')}}")
        links = [ln for ln in page.splitlines() if "fonts.googleapis.com" in ln]
        assert len(links) == 2, links
        own = page
        for ln in links:
            own = own.replace(ln + "\n", "")
        own = own.replace("<style>\n", "<style>\n" + face("Fraunces", "300 700", "fraunces-latin.woff2") + "\n"
                          + face("Archivo", "500 600", "archivo-latin.woff2") + "\n", 1)
        assert "googleapis" not in own and "gstatic" not in own
        SITE.parent.mkdir(parents=True, exist_ok=True)
        url = "https://opencaselaw.ch/posters/2026-w47/film/"
        SITE.write_text(head + f'<link rel="canonical" href="{url}">\n'
                        '<meta property="og:title" content="974 crystals"><meta property="og:type" content="website">\n'
                        '<meta property="og:description" content="Swiss case law as a film: 974 snow crystals photographed by Wilson A. Bentley, one for each new decision of week 47, over the Bernina by moonlight.">\n'
                        f'<meta property="og:url" content="{url}">\n'
                        f'<meta property="og:image" content="{url}cover.webp">\n'
                        + own + "\n</html>\n", encoding="utf-8")
        print(SITE, SITE.stat().st_size, "bytes")
    tcs = sorted(d["c"][d["keys"].index("tc")::len(d["keys"])])
    print(OUT, OUT.stat().st_size, "bytes;", "counted by second:",
          [sum(1 for t in tcs if t <= s) + 1 for s in range(2, 15)])


if __name__ == "__main__":
    main()
