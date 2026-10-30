"""Week 44 (23-29 October 2026), French: the week as windows onto the lake, second state.

Closer to the Gray Studios sheets: one flat blue with a coarse visible dither, painted fragments and
almost no words. Only the cantons with a hundred decisions or more are named, and the federal courts,
which are no canton, leave the map for one line of text.


Each canton keeps its real outline, scaled so that its area is exactly its new decisions this week;
the federal courts take the outline of the country. All of them are windows onto one painting,
Ferdinand Hodler's "Der Genfersee von Chexbres aus" (Lake Geneva from Chexbres, around 1905, public
domain; digitisation on Wikimedia Commons), which sits still behind the sheet while the cantons move
over it. Around them a flat cobalt with a dithered edge and a printed grain, after the Gray Studios
posters. A canton with no decision has no window.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page

HERE = Path(__file__).parent
ASSET = HERE / "weekly_poster_w44_assets" / "hodler_chexbres.jpg"
CANTONS = HERE / "weekly_poster_cantons.json"
GEO = HERE / "weekly_poster_geo.json"
NAME = {"CH": "Tribunaux fédéraux", "GE": "Genève", "VD": "Vaud", "ZH": "Zurich", "BE": "Berne", "AG": "Argovie",
        "GR": "Grisons", "ZG": "Zoug", "SO": "Soleure", "NE": "Neuchâtel", "BS": "Bâle-Ville", "SZ": "Schwytz",
        "JU": "Jura", "TI": "Tessin", "FR": "Fribourg", "UR": "Uri", "BL": "Bâle-Campagne", "SG": "Saint-Gall",
        "VS": "Valais", "GL": "Glaris"}
MONTH_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"]
K = 300.0          # px² of poster per decision: area is proportional to the count
BOX = (44, 372, 1036, 1306)
HOME = {"GE": (0.13, .90), "VD": (.30, .50), "NE": (.10, .28), "JU": (.16, .06), "FR": (.34, .32), "BE": (.50, .42),
        "VS": (.50, .90), "SO": (.34, .10), "BS": (.45, .02), "BL": (.53, .10), "AG": (.60, .10), "ZH": (.78, .16),
        "ZG": (.70, .46), "SZ": (.76, .58), "UR": (.68, .66), "GL": (.86, .52), "SG": (.93, .08), "GR": (.88, .78),
        "TI": (.66, .90)}
BAYER = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
                  [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                  [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], dtype=np.float32) / 64


def outlines() -> dict[str, list[list[list[float]]]]:
    """Polygons per code as lists of rings; the first ring of each polygon is its outer edge."""
    cantons = json.loads(CANTONS.read_text(encoding="utf-8"))["cantons"]
    out = {k: v for k, v in cantons.items()}
    border = json.loads(GEO.read_text(encoding="utf-8"))["border"]
    out["CH"] = [border] if border and isinstance(border[0][0], (list, tuple)) else [[border]]
    return out


def ring_area(r) -> float:
    return abs(sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r)))) / 2


def shape(polys, n: int) -> tuple[list, float, float]:
    """The polygons scaled to area K*n, north up, centred on (0, 0); returns them with their size."""
    area = sum(ring_area(p[0]) - sum(ring_area(h) for h in p[1:]) for p in polys)
    s = math.sqrt(K * n / area)
    pts = [q for p in polys for q in p[0]]
    x0, x1 = min(q[0] for q in pts), max(q[0] for q in pts)
    y0, y1 = min(q[1] for q in pts), max(q[1] for q in pts)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    scaled = [[[((q[0] - cx) * s, (cy - q[1]) * s) for q in ring] for ring in p] for p in polys]
    return scaled, (x1 - x0) * s, (y1 - y0) * s


def layout(items: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Start each outline near home and push overlapping boxes apart, leaving room for its label."""
    (l, t, r, b) = BOX
    pos = {k: (l + HOME[k][0] * (r - l), t + HOME[k][1] * (b - t)) for k in items}
    keys = sorted(items, key=lambda k: -items[k][0] * items[k][1])
    for _ in range(1400):
        for i, a in enumerate(keys):
            for c in keys[i + 1:]:
                wa, ha = items[a]
                wb, hb = items[c]
                dx, dy = pos[c][0] - pos[a][0], pos[c][1] - pos[a][1]
                ox = (wa + wb) / 2 + 26 - abs(dx)
                oy = (ha + hb) / 2 + 40 - abs(dy)
                if ox > 0 and oy > 0:
                    # the larger window moves less
                    w = (wb * hb) / (wa * ha + wb * hb)
                    if ox < oy:
                        s = ox * (1 if dx >= 0 else -1)
                        pos[a] = (pos[a][0] - s * w, pos[a][1])
                        pos[c] = (pos[c][0] + s * (1 - w), pos[c][1])
                    else:
                        s = oy * (1 if dy >= 0 else -1)
                        pos[a] = (pos[a][0], pos[a][1] - s * w)
                        pos[c] = (pos[c][0], pos[c][1] + s * (1 - w))
        for k in keys:
            w, h = items[k]
            pos[k] = (min(max(pos[k][0], l + w / 2), r - w / 2), min(max(pos[k][1], t + h / 2), b - h / 2 - 22))
    return pos


def plate(shapes: dict, pos: dict) -> str:
    """Cobalt, painting through the windows, dithered edges, grain: one JPEG at twice poster size."""
    W, H, F = 2160, 2700, 2
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    for k, polys in shapes.items():
        px, py = pos[k]
        for p in polys:
            d.polygon([((x + px) * F, (y + py) * F) for x, y in p[0]], fill=255)
            for hole in p[1:]:
                d.polygon([((x + px) * F, (y + py) * F) for x, y in hole], fill=0)
    soft = np.asarray(mask.filter(ImageFilter.GaussianBlur(5)), dtype=np.float32) / 255
    cell = 4
    yy, xx = np.mgrid[0:H, 0:W]
    bayer = BAYER[(yy // cell) % 8, (xx // cell) % 8]
    window = soft > bayer * .92 + .04

    art = Image.open(ASSET).convert("RGB")
    s = max(W / art.width, H / art.height)
    art = art.resize((round(art.width * s), round(art.height * s)), Image.LANCZOS)
    ox = int((art.width - W) * .58)
    paint = np.asarray(art.crop((ox, 0, ox + W, H)), dtype=np.float32)

    top, low = np.array([33, 112, 224], np.float32), np.array([12, 62, 168], np.float32)
    t = (yy / H) + 0.0
    blue_mix = (t > bayer * .86 + .07)[..., None]
    field = top * (1 - blue_mix) + low * blue_mix
    out = np.where(window[..., None], paint, field)
    rng = np.random.default_rng(44)
    grain = rng.normal(0, 7.5, (H, W, 1)).astype(np.float32)
    out = np.clip(out + grain, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(out, "RGB").save(buf, "JPEG", quality=84)
    return base64.b64encode(buf.getvalue()).decode()


def design(week: dict, lang: str = "fr") -> str:
    a, b = week["from"], week["to"]
    bc = {k: v for k, v in week["by_canton"].items() if v and k != "CH"}
    federal = week["by_canton"]["CH"]
    src = outlines()
    shapes, sizes = {}, {}
    for k, n in bc.items():
        shapes[k], w, h = shape(src[k], n)
        sizes[k] = (w, h)
    pos = layout(sizes)
    img = plate(shapes, pos)
    labels = []
    for k, n in bc.items():
        if n < 100:     # only the largest are named; the shapes say the rest
            continue
        x, y = pos[k]
        w, h = sizes[k]
        big = n >= 100
        # Vaud's label stands beside it: right below lies Geneva's exclave Céligny, not Vaud
        at = f"left:{x - w * .34:.0f}px;top:{y + h * .22:.0f}px" if k == "VD" else f"left:{x:.0f}px;top:{y + h / 2 + 8:.0f}px"
        labels.append(f'<div class="lb{" big" if big else ""}" style="{at}">{esc(NAME[k])} <b>{wp.fmt(n, "fr")}</b></div>')
    body = f"""
<div class="bg" style="background-image:url(data:image/jpeg;base64,{img})"></div>
<div class="n">{esc(wp.fmt(week["added"], "fr"))}</div>
<div class="t">nouvelles décisions</div>
<div class="d">Semaine {week["week_no"]}<br>{a.day}–{b.day} {MONTH_FR[b.month - 1]} {b.year}</div>
<div class="k">L’aire de chaque canton est celle de ses<br>décisions. {federal} autres viennent des tribunaux fédéraux.</div>
<div class="u">opencaselaw.ch</div>
<div class="c">Ferdinand Hodler, Le Léman vu de Chexbres, vers 1905</div>
{''.join(labels)}
"""
    css = """
body{background:#1860cd;color:#fff;font-family:'Inter Tight',sans-serif}
.bg{position:absolute;inset:0;background-size:cover}
.n{position:absolute;left:40px;top:34px;font:600 212px/1 'Inter Tight',sans-serif;letter-spacing:-.045em}
.t{position:absolute;left:48px;top:254px;font:500 42px/1 'Inter Tight',sans-serif;letter-spacing:-.015em}
.d{position:absolute;right:48px;top:62px;text-align:right;font:500 25px/1.28 'Inter Tight',sans-serif}
.u{position:absolute;right:48px;top:264px;font:500 25px/1 'Inter Tight',sans-serif}
.k{position:absolute;right:48px;top:146px;text-align:right;font:500 18px/1.3 'Inter Tight',sans-serif}
.c{position:absolute;right:48px;bottom:26px;font:500 14px/1 'Inter Tight',sans-serif;opacity:.85}
.lb{position:absolute;transform:translateX(-50%);white-space:nowrap;font:500 16px/1 'Inter Tight',sans-serif}
.lb b{font-weight:700}
.lb.big{font-size:20px}
"""
    return page("family=Inter+Tight:wght@500;600;700", css, body, lang)
