"""Week 44 (23-29 October 2026), Italian: Segantini in as many dots as there are decisions.

Giovanni Segantini's "Mezzogiorno sulle Alpi" (High Noon in the Alps, 1891, Segantini Museum, St. Moritz;
public domain, Google Art Project scan on Wikimedia Commons) in exactly 1,139 dots on a dark ground,
as in the first state, but the dots no longer stand on a grid. Weighted stippling gathers them where
the picture has detail, the sheep, the birch, the mountains, and lets them spread where it is calm,
the way a painter's touch does; each takes the colour of the patch it stands for and is as large as
that patch. The shepherdess holds exactly 54 dots, one for each decision written in Italian, kept at
full colour while the rest of the meadow stays a little quieter.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page
from weekly_poster_w44_divisionismo import BG, CROP, FIGURE, MONTH_IT, SKY
from weekly_poster_w44_earth import nearest

ASSET = Path(__file__).with_name("weekly_poster_w44_assets") / "segantini_mezzogiorno.jpg"
FIELD_W, FIELD_H = 1080, 1054
SIZE = 700            # cells a side of the stippling grid
ROUNDS = 22
DETAIL = 2.6          # how strongly detail attracts dots against the calm of the picture
FILL = .47            # dot radius against the side of its patch: just short of touching
QUIET = .8            # saturation of everything but the figure


def design(week: dict, lang: str = "it") -> str:
    a, b = week["from"], week["to"]
    n, n_it = week["added"], week["languages"]["it"]
    art = Image.open(ASSET).convert("RGB")
    w, h = art.size
    box = (0, int(CROP[1] * h), w, int(CROP[3] * h))
    crop = art.crop(box).resize((SIZE, SIZE), Image.LANCZOS)
    px = np.asarray(crop, dtype=np.float64)
    grey = np.asarray(crop.convert("L").filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
    gy, gx = np.gradient(grey)
    edge = Image.fromarray(np.hypot(gx, gy))
    edge = np.asarray(edge.resize((SIZE // 12, SIZE // 12), Image.BOX).resize((SIZE, SIZE), Image.BICUBIC), dtype=np.float64)
    weight = 1 + DETAIL * (edge / np.percentile(edge, 95)) ** 1.4

    fig = Image.new("L", (w, h), 0)
    ImageDraw.Draw(fig).polygon([(x * w, y * h) for x, y in FIGURE], fill=255)
    fig = np.asarray(fig.crop(box).resize((SIZE, SIZE)), dtype=np.float64) / 255
    # the figure gets exactly its share: 54 of 1,139 dots
    inside, outside = (weight * fig).sum(), (weight * (1 - fig)).sum()
    share = n_it / n
    weight = weight * (1 - fig) + weight * fig * (share * outside / (1 - share)) / inside

    rng = np.random.default_rng(44)
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    ys, xs = ys.ravel(), xs.ravel()
    wt = weight.ravel()
    pts = np.stack([xs, ys], axis=1).astype(np.float32)[rng.choice(len(xs), n, replace=False, p=wt / wt.sum())]
    for _ in range(ROUNDS):
        owner = nearest(pts, SIZE).ravel()
        mass = np.bincount(owner, wt, n)
        sx, sy = np.bincount(owner, wt * xs, n), np.bincount(owner, wt * ys, n)
        ok = mass > 0
        pts[ok, 0], pts[ok, 1] = sx[ok] / mass[ok], sy[ok] / mass[ok]

    owner = nearest(pts, SIZE).ravel()
    count = np.maximum(np.bincount(owner, minlength=n), 1)
    rgb = np.stack([np.bincount(owner, px[..., c].ravel(), n) / count for c in range(3)], axis=1)
    on_fig = np.bincount(owner, fig.ravel(), n) / count
    chosen = set(np.argsort(-on_fig)[:n_it].tolist())
    sx, sy = FIELD_W / SIZE, FIELD_H / SIZE
    circles = []
    for i in np.argsort(-count):      # the large dots first, the fine ones on top
        x, y = float(pts[i, 0]) * sx, float(pts[i, 1]) * sy
        r = FILL * (count[i] * sx * sy) ** .5 * 1.05
        c = rgb[i]
        mean = c.mean()
        c = mean + (c - mean) * (1.45 if i in chosen else QUIET * 1.25)
        c = c * (1.12 if i in chosen else .96)
        col = "#%02x%02x%02x" % tuple(int(min(max(v, 0), 255)) for v in c)
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{col}"/>')

    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">{''.join(circles)}</svg>
<div class="n">{esc(wp.fmt(n, "it"))}</div>
<div class="t">nuove decisioni</div>
<div class="i"><b>{n_it}</b> in italiano</div>
<div class="d">Settimana {week["week_no"]}, {a.day}–{b.day} {MONTH_IT[b.month - 1]} {b.year}</div>
<div class="k">Un punto, una decisione. A colori pieni quelle in italiano.</div>
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
.k{{position:absolute;left:562px;top:1238px;font:400 18.5px/1 'Fraunces',serif}}
.c{{position:absolute;left:562px;top:1282px;font:italic 400 16px/1 'Fraunces',serif;opacity:.8}}
.u{{position:absolute;right:36px;top:1208px;font:500 19px/1 'Fraunces',serif}}
"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400", css, body, lang)
