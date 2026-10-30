"""Week 44 (23-29 October 2026), Italian: the week as one divisionist painting.

Giovanni Segantini's "Mezzogiorno sulle Alpi" (High Noon in the Alps, 1891, Segantini Museum,
St. Moritz; public domain, Google Art Project scan on Wikimedia Commons) rebuilt from exactly as
many dots as there are new decisions: 34 rows of 34 and 33 on a staggered grid, 1,139 in all. Each
dot takes the colour of its patch of the painting. The 54 dots nearest the shepherdess keep it in
full and the full size, one for each decision written in Italian; the others are smaller and
drained towards grey. Segantini
painted light by laying pure colours side by side; here the same division counts.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import weekly_poster as wp
from weekly_poster_designs import esc, page

ASSET = Path(__file__).with_name("weekly_poster_w44_assets") / "segantini_mezzogiorno.jpg"
MONTH_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
            "ottobre", "novembre", "dicembre"]
ROWS, COLS = 34, 34      # 34 x 34 - 17 = 1,139: every second row has one dot fewer
DX, DY = 1080 / COLS, 31.0
R = 14.9
CROP = (0, 28 / 773, 1, 738 / 773)     # of the painting, as fractions
# the shepherdess, as fractions of the painting
FIGURE = [(x / 720, y / 773) for x, y in
          [(345, 178), (400, 178), (407, 205), (395, 232), (422, 250), (432, 300), (450, 320), (472, 400), (494, 500),
           (492, 556), (440, 572), (396, 560), (365, 480), (358, 368), (345, 335), (300, 312), (325, 255), (356, 232),
           (350, 205)]]
BG, SKY = "#12110d", "#4f86c8"


def dots() -> list[tuple[float, float]]:
    pts = []
    for r in range(ROWS):
        n = COLS - (r % 2)
        x0 = DX / 2 + (r % 2) * DX / 2
        for c in range(n):
            pts.append((x0 + c * DX, DY / 2 + r * DY))
    return pts


def design(week: dict, lang: str = "it") -> str:
    a, b = week["from"], week["to"]
    n_it = week["languages"]["it"]
    pts = dots()
    assert len(pts) == week["added"]
    field_h = ROWS * DY

    art = Image.open(ASSET).convert("RGB")
    w, h = art.size
    box = (0, int(CROP[1] * h), w, int(CROP[3] * h))
    crop = art.crop(box)
    sx, sy = crop.width / 1080, crop.height / field_h
    px = np.asarray(crop.resize((1080, int(field_h)), Image.LANCZOS), dtype=np.float32)

    fig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(fig).polygon([(x * w, y * h) for x, y in FIGURE], fill=255)
    fig = np.asarray(fig.crop(box).resize((1080, int(field_h))), dtype=np.float32) / 255

    def patch(a_, x, y):
        x0, x1 = max(int(x - R), 0), min(int(x + R), 1080)
        y0, y1 = max(int(y - R), 0), min(int(y + R), int(field_h))
        return a_[y0:y1, x0:x1]

    overlap = [float(patch(fig, x, y).mean()) for x, y in pts]
    chosen = set(sorted(range(len(pts)), key=lambda i: -overlap[i])[:n_it])
    circles = []
    for i, (x, y) in enumerate(pts):
        c = patch(px, x, y).reshape(-1, 3).mean(0)
        if i in chosen:
            mean = c.mean()
            c = np.clip(mean + (c - mean) * 1.5, 0, 255) * 1.12      # the figure, a little brighter than life
            r = R
        else:
            grey = c.dot([.3, .59, .11])
            c = (grey + (c - grey) * .85) * .94
            r = R * .78
        hexc = "#%02x%02x%02x" % tuple(int(min(v, 255)) for v in c)
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{hexc}"/>')

    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">{''.join(circles)}</svg>
<div class="n">{esc(wp.fmt(week["added"], "it"))}</div>
<div class="t">nuove decisioni</div>
<div class="i"><b>{n_it}</b> in italiano</div>
<div class="d">Settimana {week["week_no"]}, {a.day}–{b.day} {MONTH_IT[b.month - 1]} {b.year}</div>
<div class="k">Un punto, una decisione. A colori quelle in italiano.</div>
<div class="c">Giovanni Segantini, Mezzogiorno sulle Alpi, 1891</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:{BG};color:#efe9da;font-family:'Fraunces',serif}}
.n{{position:absolute;left:30px;top:1072px;font:500 190px/1 'Fraunces',serif;letter-spacing:-.03em;font-variation-settings:'opsz' 144,'SOFT' 0}}
.t{{position:absolute;left:36px;top:1262px;font:400 34px/1 'Fraunces',serif}}
.i{{position:absolute;left:560px;top:1092px;font:400 46px/1 'Fraunces',serif;color:{SKY}}}
.i b{{font-weight:600;font-size:98px;letter-spacing:-.02em;vertical-align:-.04em;font-variation-settings:'opsz' 24}}
.d{{position:absolute;left:562px;top:1206px;font:400 21px/1 'Fraunces',serif}}
.k{{position:absolute;left:562px;top:1238px;font:400 21px/1 'Fraunces',serif}}
.c{{position:absolute;left:562px;top:1282px;font:italic 400 16px/1 'Fraunces',serif;opacity:.8}}
.u{{position:absolute;right:36px;top:1208px;font:500 19px/1 'Fraunces',serif}}
"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400", css, body, lang)
