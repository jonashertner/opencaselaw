#!/usr/bin/env python3
"""Week 44 (23-29 October 2026): the interactive map, one self-contained HTML page.

The Rumantsch poster made live: Switzerland as an even field of red dots on graph paper, one dot per
decision, the sheet bent by the weight of each court. This week has 1,139 dots, this year 28,028, and
the country flows from one to the other. It runs on the GPU, so a frame costs the page almost
nothing. The lens under the pointer is the Sphere texture (https://textures.dev/s/sphere, by Textures,
licence "free to use"): its hill formula and spring constants, moved into a vertex shader.

    python3 scripts/weekly_poster_w44_map.py     # -> output/posters/w44/map.html
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_poster_w44_gravitad as grav  # noqa: E402  (the poster's seats, bend and lattice)

HERE = Path(__file__).parent
OUT = HERE.parent / "output" / "posters" / "w44" / "map.html"
SITE = HERE.parent / "docs" / "posters" / "2026-w44" / "map" / "index.html"
BOX = (1080, 700)          # the map's own units: the poster's pixels, cropped round the country
YSHIFT = 410               # poster y minus map y
SHEET = 9.0                # km between lines of the graph paper
SAMPLES = 130              # points along each line of the paper
PICK = 4                   # map units per cell of the lookup that says which canton is under the pointer
RASTER = 6                 # cells per km of the masks that sort dots into cantons
SEAT_KM = 15               # dots this near a federal seat light up with the federal courts

NAMES = {"AG": "Aargau", "AI": "Appenzell I.Rh.", "AR": "Appenzell A.Rh.", "BE": "Bern", "BL": "Basel-Landschaft",
         "BS": "Basel-Stadt", "FR": "Fribourg", "GE": "Geneva", "GL": "Glarus", "GR": "Graubünden", "JU": "Jura",
         "LU": "Lucerne", "NE": "Neuchâtel", "NW": "Nidwalden", "OW": "Obwalden", "SG": "St. Gallen",
         "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz", "TG": "Thurgau", "TI": "Ticino", "UR": "Uri",
         "VD": "Vaud", "VS": "Valais", "ZG": "Zug", "ZH": "Zurich", "CH": "Federal courts"}
# courts under their own names, in their own language
COURTS = {
    "ag_handelsgericht": "Handelsgericht", "ag_spezialverwaltungsgericht": "Spezialverwaltungsgericht",
    "ag_strafgericht": "Strafgericht", "ag_versicherungsgericht": "Versicherungsgericht",
    "ag_verwaltungsgericht": "Verwaltungsgericht", "ag_zivilgericht": "Zivilgericht",
    "ag_departement_bvu": "Departement BVU", "ag_departement_vi": "Departement VI",
    "be_direktionen": "Direktionen", "be_verwaltungsgericht": "Verwaltungsgericht",
    "be_zivilstraf": "Zivil- und Strafgerichte", "bl_gerichte": "Gerichte",
    "bs_appellationsgericht": "Appellationsgericht", "bs_sozialversicherungsgericht": "Sozialversicherungsgericht",
    "fr_gerichte": "Tribunaux / Gerichte", "ge_gerichte": "Tribunaux", "gl_gerichte": "Gerichte",
    "gr_gerichte": "Gerichte / Dretgiras / Tribunali", "ju_gerichte": "Tribunaux", "ne_gerichte": "Tribunaux",
    "ne_jurisprudence_adm": "Jurisprudence administrative", "so_gerichte": "Gerichte",
    "sg_versicherungsgericht": "Versicherungsgericht", "sg_verwaltungsrekurskommission": "Verwaltungsrekurskommission",
    "sz_gerichte": "Gerichte", "sz_verwaltungsgericht": "Verwaltungsgericht", "ti_gerichte": "Tribunali",
    "ur_gerichte": "Gerichte", "vd_gerichte": "Tribunal cantonal", "vd_omni": "Cour de droit administratif et public",
    "vs_gerichte": "Tribunaux / Gerichte", "zg_obergericht": "Obergericht", "zg_verwaltungsgericht": "Verwaltungsgericht",
    "zh_bezirksgericht_pfaeffikon": "Bezirksgericht Pfäffikon",
}
SEATS = [("VD", ["bger@VD", "bge"], "Federal Supreme Court, Lausanne"), ("LU", ["bger@LU"], "Federal Supreme Court, Lucerne"),
         ("SG", ["bvger"], "Federal Administrative Court, St. Gallen"), ("TI", ["bstger"], "Federal Criminal Court, Bellinzona"),
         ("BE", None, "Other federal bodies")]
CODES = sorted(c for c in NAMES if c != "CH")


def label(court: str) -> str:
    return " ".join(w.capitalize() for w in court.split("_")[1:]).replace("ue", "ü") or court


def px(km: np.ndarray, mass: list) -> np.ndarray:
    """A point on the sheet to map units, after the bend of that view."""
    p = grav.to_px(grav.warp(km, mass))
    return p - np.array([0, YSHIFT])


def b64(a: np.ndarray) -> str:
    return base64.b64encode(a.tobytes()).decode()


def i16(p: np.ndarray) -> np.ndarray:
    return np.round(p * 8).astype(np.int16)


def even(border: np.ndarray, n: int) -> np.ndarray:
    """Exactly n points on a square lattice inside the country, found on a raster for speed."""
    x0, y0 = border[:, 0].min(), border[:, 1].min()
    w, h = int((border[:, 0].max() - x0) * RASTER) + 2, int((border[:, 1].max() - y0) * RASTER) + 2
    img = Image.new("1", (w, h))
    ImageDraw.Draw(img).polygon([((x - x0) * RASTER, (y - y0) * RASTER) for x, y in border], fill=1)
    mask = np.asarray(img)
    area = mask.sum() / RASTER ** 2
    base = (area / n) ** .5
    best = None
    for p in np.linspace(base * .985, base * 1.015, 200):
        for ox in np.linspace(0, p, 7, endpoint=False):
            for oy in np.linspace(0, p, 7, endpoint=False):
                gx, gy = np.meshgrid(np.arange(x0 + ox, border[:, 0].max(), p), np.arange(y0 + oy, border[:, 1].max(), p))
                pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
                i = ((pts[:, 0] - x0) * RASTER).astype(int)
                j = ((pts[:, 1] - y0) * RASTER).astype(int)
                keep = pts[mask[j, i]]
                if len(keep) == n:
                    return keep
                if len(keep) > n and (best is None or len(keep) < len(best)):
                    best = keep
    return best[np.linspace(0, len(best) - 1, n).astype(int)]


def build() -> dict:
    data = json.loads((HERE / "weekly_poster_w44_data.json").read_text(encoding="utf-8"))
    v = data["verified"]
    yr = json.loads((HERE / "weekly_poster_w44_year.json").read_text(encoding="utf-8"))
    border = np.array(json.loads((HERE / "weekly_poster_geo.json").read_text(encoding="utf-8"))["border"], dtype=float)
    cantons = json.loads((HERE / "weekly_poster_cantons.json").read_text(encoding="utf-8"))["cantons"]
    mw, my = grav.masses(v), grav.masses(yr)

    week = even(border, v["added"])
    year = even(border, yr["total"])
    assert len(week) == v["added"] and len(year) == yr["total"]

    # each of this week's dots takes over the nearest of the year's, so that none has to jump
    d2 = ((year[None, :, :] - week[:, None, :]) ** 2).sum(2) if len(year) * len(week) < 4e7 else None
    order = np.full(len(week), -1)
    free = np.ones(len(year), bool)
    for i in range(len(week)):
        c = d2[i].copy()
        c[~free] = 1e12
        order[i] = int(c.argmin())
        free[order[i]] = False
    in_week = np.zeros(len(year), np.uint8)
    in_week[order] = 255
    a = px(year, mw)
    a[order] = px(week, mw)
    b = px(year, my)

    # which canton each dot stands in
    x0, y0 = border[:, 0].min() - 2, border[:, 1].min() - 2
    w, h = int((border[:, 0].max() - x0 + 4) * RASTER), int((border[:, 1].max() - y0 + 4) * RASTER)
    img = Image.new("L", (w, h), 255)
    dr = ImageDraw.Draw(img)
    for idx, code in enumerate(CODES):
        for part in cantons[code]:
            dr.polygon([((x - x0) * RASTER, (y - y0) * RASTER) for x, y in part[0]], fill=idx)
    grid = np.asarray(img)
    idx = grid[((year[:, 1] - y0) * RASTER).astype(int), ((year[:, 0] - x0) * RASTER).astype(int)].copy()
    idx[idx == 255] = 26
    seat = np.zeros(len(year), np.uint8)
    for at, _, _ in SEATS:
        s = np.array(grav.SEAT[at], dtype=float)
        seat[((year - s) ** 2).sum(1) < SEAT_KM ** 2] = 255
    # how late each dot moves when the views change: a soft wave from west to east
    wave = np.clip((b[:, 0] / BOX[0]) * 255, 0, 255).astype(np.uint8)
    info = np.stack([idx, in_week, seat, wave], axis=1).astype(np.uint8)

    # the sheet and the outline
    xs = np.arange(grav.ORIGIN[0] - 150, grav.ORIGIN[0] + 560, SHEET)
    ys = np.arange(grav.ORIGIN[1] - 330, grav.ORIGIN[1] + 330, SHEET)
    lines = [np.stack([np.full(SAMPLES, x), np.linspace(ys[0], ys[-1], SAMPLES)], axis=1) for x in xs]
    lines += [np.stack([np.linspace(xs[0], xs[-1], SAMPLES), np.full(SAMPLES, y)], axis=1) for y in ys]
    sheet = np.concatenate(lines)
    loop = np.concatenate([border, border[:1]])
    fade = np.where(grav.inside(sheet, border), 70, 255).astype(np.uint8)   # the paper shows less through the country

    # which canton lies under the pointer, in each view
    cols, rows = BOX[0] // PICK, BOX[1] // PICK

    def lookup(mass: list) -> tuple[np.ndarray, dict]:
        im = Image.new("L", (cols, rows), 0)
        dd = ImageDraw.Draw(im)
        at = {}
        for i, code in enumerate(CODES):
            pts = []
            for part in cantons[code]:
                q = px(np.array(part[0], dtype=float), mass)
                pts.append(q)
                dd.polygon([(x / PICK, y / PICK) for x, y in q], fill=i + 1)
            big = max(pts, key=len)
            at[code] = [round(float(big[:, 0].mean()), 1), round(float(big[:, 1].mean()), 1)]
        return np.asarray(im, dtype=np.uint8), at

    pick_w, at_w = lookup(mw)
    pick_y, at_y = lookup(my)

    def by_canton(by_court: dict) -> dict:
        out: dict[str, list] = {}
        for k, n in by_court.items():
            c = k.split("_")[0].upper()
            if c in NAMES and c != "CH":
                out.setdefault(c, []).append([COURTS.get(k) or label(k), n])
        for r in out.values():
            r.sort(key=lambda t: -t[1])
        return out

    def seats(by_court: dict, ch: int) -> list:
        rows_, used = [], 0
        for _, courts, name in SEATS:
            n = ch - used if courts is None else sum(by_court.get(c, 0) for c in courts)
            used += n
            rows_.append([name, n])
        return rows_

    return {"w": BOX[0], "h": BOX[1], "codes": CODES, "names": NAMES, "n": len(year), "line": SAMPLES, "lines": len(lines),
            "a": b64(i16(a)), "b": b64(i16(b)), "info": b64(info), "sa": b64(i16(px(sheet, mw))), "sb": b64(i16(px(sheet, my))), "fade": b64(fade),
            "oa": b64(i16(px(loop, mw))), "ob": b64(i16(px(loop, my))), "pick": PICK, "cols": cols, "rows": rows,
            "pa": b64(pick_w), "pb": b64(pick_y),
            "week": {"total": v["added"], "counts": v["by_canton"], "courts": by_canton(v["by_court"]), "seats": seats(v["by_court"], v["by_canton"]["CH"]), "at": at_w},
            "year": {"total": yr["total"], "counts": yr["by_canton"], "courts": by_canton(yr["by_court"]), "seats": seats(yr["by_court"], yr["by_canton"]["CH"]), "at": at_y}}


def main() -> None:
    d = build()
    page = (HERE / "weekly_poster_w44_map.html").read_text(encoding="utf-8").replace("/*DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    OUT.write_text(page, encoding="utf-8")
    SITE.parent.mkdir(parents=True, exist_ok=True)
    head = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Week 44 of Swiss case law as a live map: one dot per decision, 1,139 this week and 28,028 this year.">
<link rel="canonical" href="https://opencaselaw.ch/posters/2026-w44/map/">
<meta property="og:title" content="Gravitad"><meta property="og:type" content="website">
<meta property="og:description" content="Swiss case law as a live map, one dot per decision.">
<meta property="og:url" content="https://opencaselaw.ch/posters/2026-w44/map/">
<meta property="og:image" content="https://opencaselaw.ch/posters/2026-w44/rm-gravitad2.webp">
"""
    SITE.write_text(head + page.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "") + "\n</html>\n", encoding="utf-8")
    print(OUT, len(page), "bytes,", d["n"], "dots,", d["lines"], "lines")


if __name__ == "__main__":
    main()
