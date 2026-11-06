#!/usr/bin/env python3
"""Week 45 (30 October - 5 November 2026): the interactive map, one self-contained HTML page.

The English poster made live, in the spirit of week 44's Gravitad: Switzerland split along its
language border, the French side set a little apart, and in each language's area exactly as many
dots as there are decisions in that language, evenly spaced. 905 dots this week, every decision
dated 2026 for the year, and the country flows from one to the other. Pointing at a canton lights
the dots that stand in it and lists its courts. It runs on the GPU (one draw call for the dots,
one for the ground); the lens under the pointer is the Sphere texture of week 44
(https://textures.dev/s/sphere, by Textures, licence "free to use").

    python3 scripts/weekly_poster_w45_map.py     # -> output/posters/w45/map.html + docs/posters/2026-w45/map/
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_poster_w44_gravitad as grav  # noqa: E402  (court seats in km)
import weekly_poster_w44_map as w44  # noqa: E402  (canton names, court names, seats)
import weekly_poster_w45_roestigraben as rg  # noqa: E402  (language areas, the ditch)

HERE = Path(__file__).parent
OUT = HERE.parent / "output" / "posters" / "w45" / "map.html"
SITE = HERE.parent / "docs" / "posters" / "2026-w45" / "map" / "index.html"
E0, N0, SCALE = 466.0, 298.0, 2.66      # km at the map's top left, map units per km
BOX = (1000, 612)                       # map units
SHIFT = np.array(rg.GRABEN)             # the French side stands apart by this much (map units)
RASTER = 6                              # cells per km of the language and canton rasters
PICK = 4                                # map units per cell of the pointer lookup
GROUND = 0.62                           # km between the ground's tint points
SEAT_KM = 15
LANGS = ["de", "fr", "it"]
CODES = w44.CODES


def km_to_map(km: np.ndarray) -> np.ndarray:
    return np.stack([(km[:, 0] - E0) * SCALE, (N0 - km[:, 1]) * SCALE], axis=1)


def rasters() -> tuple[np.ndarray, np.ndarray, tuple]:
    """Language (0 none, 1 de, 2 fr, 3 it) and canton (index + 1) of every raster cell, drawn with
    PIL polygons: cantons by official language, the bilingual districts over them, Valais German
    from the Leuk district east, lakes cut out."""
    cantons, overlays, lakes = rg.regions()
    x0, y0, x1, y1 = 480.0, 70.0, 840.0, 300.0
    w, h = int((x1 - x0) * RASTER), int((y1 - y0) * RASTER)
    to = lambda ring: [((x - x0) * RASTER, (y1 - y) * RASTER) for x, y in ring]  # noqa: E731  (north up)
    lang, cant = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    dl, dc = ImageDraw.Draw(lang), ImageDraw.Draw(cant)
    for i, code in enumerate(CODES):
        for poly in cantons[code]:
            code_lang = 1 + LANGS.index(rg.CANTON_LANG.get(code, "de"))
            dl.polygon(to(poly[0]), fill=code_lang)
            dc.polygon(to(poly[0]), fill=i + 1)
            for hole in poly[1:]:
                dl.polygon(to(hole), fill=0)
                dc.polygon(to(hole), fill=0)
    L = np.asarray(lang).copy()
    leuk = Image.new("1", (w, h), 0)
    for p in overlays.pop("leuk"):
        ImageDraw.Draw(leuk).polygon(to(p[0]), fill=1)
    for lg, polys in overlays.items():
        m = Image.new("1", (w, h), 0)
        for p in polys:
            ImageDraw.Draw(m).polygon(to(p[0]), fill=1)
        L[np.asarray(m) & (L > 0)] = 1 + LANGS.index(lg)
    C = np.asarray(cant).copy()
    vs = C == CODES.index("VS") + 1
    east = (np.arange(w)[None, :] / RASTER + x0) > rg.UPPER_VALAIS
    # the simplified district outlines leave hairline gaps between them: close them
    leuk = leuk.convert("L").filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    german = east | (np.asarray(leuk) > 0)
    L[vs & german], L[vs & ~german] = 1, 2
    # specks of French or Italian a few cells wide, left where outlines meet, go to German
    for code in (2, 3):
        mask = Image.fromarray(((L == code) * 255).astype(np.uint8))
        opened = np.asarray(mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))) > 0
        L[(L == code) & ~opened & (L > 0)] = 1
    lk = Image.new("1", (w, h), 0)
    for ring in lakes:
        ImageDraw.Draw(lk).polygon(to(ring), fill=1)
    L[np.asarray(lk)] = 0
    C[np.asarray(lk)] = 0
    return L, C, (x0, y1)


def sample(R: np.ndarray, origin: tuple, km: np.ndarray) -> np.ndarray:
    x0, y1 = origin
    i = np.clip(((km[:, 0] - x0) * RASTER).astype(int), 0, R.shape[1] - 1)
    j = np.clip(((y1 - km[:, 1]) * RASTER).astype(int), 0, R.shape[0] - 1)
    return R[j, i]


def lattice(L: np.ndarray, origin: tuple, code: int, n: int) -> np.ndarray:
    """Exactly n points on a square lattice inside one language's area (raster lookups)."""
    x0, y1 = origin
    xs, ys = (x0, x0 + L.shape[1] / RASTER), (y1 - L.shape[0] / RASTER, y1)
    area = (L == code).sum() / RASTER ** 2
    base = (area / n) ** .5
    best = None
    for p in np.linspace(base * .97, base * 1.03, 120):
        for ox in np.linspace(0, p, 6, endpoint=False):
            for oy in np.linspace(0, p, 6, endpoint=False):
                gx, gy = np.meshgrid(np.arange(xs[0] + ox, xs[1], p), np.arange(ys[0] + oy, ys[1], p))
                pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
                keep = pts[sample(L, origin, pts) == code]
                if len(keep) == n:
                    return keep
                if len(keep) > n and (best is None or len(keep) < len(best)):
                    best = keep
    return best[np.linspace(0, len(best) - 1, n).astype(int)]


def b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def i16(p: np.ndarray) -> np.ndarray:
    return np.round(p * 8).astype(np.int16)


def build() -> dict:
    v = json.loads((HERE / "weekly_poster_w45_data.json").read_text(encoding="utf-8"))["verified"]
    yr = json.loads((HERE / "weekly_poster_w45_year.json").read_text(encoding="utf-8"))
    L, C, origin = rasters()

    week_km, year_km, week_lang, year_lang, order = [], [], [], [], []
    base = 0
    for k, lg in enumerate(LANGS):
        w = lattice(L, origin, k + 1, v["languages"].get(lg, 0)) if v["languages"].get(lg) else np.zeros((0, 2))
        y = lattice(L, origin, k + 1, yr["languages"][lg])
        assert len(w) == v["languages"].get(lg, 0) and len(y) == yr["languages"][lg]
        # each of this week's dots takes over the nearest free dot of the year, so none has to jump far
        free = np.ones(len(y), bool)
        for p in w:
            d = ((y - p) ** 2).sum(1)
            d[~free] = 1e12
            i = int(d.argmin())
            free[i] = False
            order.append(base + i)
            week_km.append(p)
        year_km.append(y)
        year_lang.append(np.full(len(y), k))
        base += len(y)
    year_km = np.concatenate(year_km)
    lang_of = np.concatenate(year_lang)
    a = km_to_map(year_km)
    a[order] = km_to_map(np.array(week_km))
    b = km_to_map(year_km)
    fr = lang_of == 1
    a[fr] += SHIFT
    b[fr] += SHIFT
    in_week = np.zeros(len(year_km), np.uint8)
    in_week[order] = 255

    idx = sample(C, origin, year_km).astype(np.int16) - 1
    idx[idx < 0] = 26
    seat = np.zeros(len(year_km), np.uint8)
    for at, _, _ in w44.SEATS:
        s = np.array(grav.SEAT[at], dtype=float)
        seat[((year_km - s) ** 2).sum(1) < SEAT_KM ** 2] = 128
    wave = np.clip(b[:, 0] / BOX[0] * 255, 0, 255).astype(np.uint8)
    info = np.stack([idx.astype(np.uint8), in_week, seat + lang_of.astype(np.uint8), wave], axis=1).astype(np.uint8)

    # the ground: the three language areas as a fine field of tint points
    gx, gy = np.meshgrid(np.arange(origin[0], origin[0] + L.shape[1] / RASTER, GROUND),
                         np.arange(origin[1] - L.shape[0] / RASTER, origin[1], GROUND))
    g = np.stack([gx.ravel(), gy.ravel()], axis=1)
    gl_ = sample(L, origin, g)
    g, gl_ = g[gl_ > 0], gl_[gl_ > 0] - 1
    gp = km_to_map(g)
    gp[gl_ == 1] += SHIFT

    # which canton lies under the pointer: look up the unshifted place, or, for the French side,
    # the place the shift came from
    cols, rows = BOX[0] // PICK, BOX[1] // PICK
    cx, cy = np.meshgrid((np.arange(cols) + .5) * PICK, (np.arange(rows) + .5) * PICK)
    m = np.stack([cx.ravel(), cy.ravel()], axis=1)
    to_km = lambda q: np.stack([q[:, 0] / SCALE + E0, N0 - q[:, 1] / SCALE], axis=1)  # noqa: E731
    k1, k2 = to_km(m), to_km(m - SHIFT)
    l1, l2 = sample(L, origin, k1), sample(L, origin, k2)
    c1, c2 = sample(C, origin, k1), sample(C, origin, k2)
    pick = np.where((l1 > 0) & (l1 != 2), c1, np.where(l2 == 2, c2, 0)).astype(np.uint8)
    at = {}
    for i, code in enumerate(CODES):
        hit = m[pick == i + 1]
        if len(hit):
            mid = np.median(hit, axis=0)
            at[code] = [round(float(mid[0]), 1), round(float(hit[:, 1].min()), 1)]

    def by_canton(by_court: dict) -> dict:
        out: dict[str, list] = {}
        for k, n in by_court.items():
            c = k.split("_")[0].upper()
            if c in w44.NAMES and c != "CH":
                out.setdefault(c, []).append([w44.COURTS.get(k) or w44.label(k), n])
        for r in out.values():
            r.sort(key=lambda t: -t[1])
        return out

    def seats(by_court: dict, ch: int) -> list:
        out, used = [], 0
        for _, courts, name in w44.SEATS:
            n = ch - used if courts is None else sum(by_court.get(c, 0) for c in courts)
            used += n
            out.append([name, n])
        return out

    def view(d: dict) -> dict:
        return {"total": d.get("added", d.get("total")), "counts": d["by_canton"], "courts": by_canton(d["by_court"]),
                "seats": seats(d["by_court"], d["by_canton"].get("CH", 0)), "langs": [d["languages"].get(lg, 0) for lg in LANGS]}

    return {"w": BOX[0], "h": BOX[1], "codes": CODES, "names": w44.NAMES, "n": len(year_km),
            "a": b64(i16(a)), "b": b64(i16(b)), "info": b64(info), "gp": b64(i16(gp)), "gl": b64(gl_.astype(np.uint8)),
            "gn": len(gp), "gs": GROUND * SCALE, "pick": PICK, "cols": cols, "rows": rows, "pa": b64(pick.reshape(rows, cols)),
            "at": at, "week": view(v), "year": view(yr)}


def main() -> None:
    d = build()
    page = (HERE / "weekly_poster_w45_map.html").read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    SITE.parent.mkdir(parents=True, exist_ok=True)
    head = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Week 45 of Swiss case law as a live map: one dot per decision, in the area of its language, {d['week']['total']:,} this week and {d['year']['total']:,} this year.">
<link rel="canonical" href="https://opencaselaw.ch/posters/2026-w45/map/">
<meta property="og:title" content="Röstigraben"><meta property="og:type" content="website">
<meta property="og:description" content="Swiss case law as a live map, one dot per decision, by language.">
<meta property="og:url" content="https://opencaselaw.ch/posters/2026-w45/map/">
<meta property="og:image" content="https://opencaselaw.ch/posters/2026-w45/en-roestigraben.webp">
"""
    SITE.write_text(head + page.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "") + "\n</html>\n", encoding="utf-8")
    print(OUT, len(page), "bytes,", d["n"], "dots,", d["gn"], "ground points")


if __name__ == "__main__":
    main()
