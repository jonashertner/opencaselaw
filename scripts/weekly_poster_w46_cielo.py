"""Week 46 (6 - 12 November 2026), Italian: Giacometti's starry sky in as many touches as decisions.

Augusto Giacometti (Stampa 1877 - Zurich 1947) painted "Sternenhimmel" in 1917: a round night sky
laid on in thick touches of blue, with white stars and clouds of carmine. Here the picture is set
again in exactly 1,132 touches, one for each new decision. Weighted stippling gathers them where the
painting has detail and lets them spread where it is calm; each takes the colour of the patch it
stands for and is as large as that patch. The carmine clouds hold exactly 27 touches, one for each
decision written in Italian, kept a little brighter than the rest.

The painting is in the public domain (the painter died in 1947); the reproduction is the file
"Starry Sky Augusto Giacometti (1917).jpg" on Wikimedia Commons, 743 pixels wide, which is enough
to take colours from, since every touch is drawn anew.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page
from weekly_poster_w44_earth import nearest

ASSET = Path(__file__).with_name("weekly_poster_w46_assets") / "giacometti_sternenhimmel.jpg"
DISC = (371.5, 375.5, 362.0)      # the tondo in the reproduction: centre and radius in pixels
SIZE = 760                        # cells a side of the stippling grid
ROUNDS = 26
DETAIL = 2.2                      # how strongly detail attracts touches
FILL = .5                         # touch radius against the side of its patch
CX, CY, RAD = 540.0, 522.0, 492.0  # the tondo on the sheet
GROUND, INK, CARMINE = "#07080f", "#efe9da", "#e2484d"


def design(week: dict, lang: str = "it") -> str:
    a, b = week["from"], week["to"]
    n, n_it = week["added"], week["languages"]["it"]
    art = Image.open(ASSET).convert("RGB")
    cx, cy, r = DISC
    crop = art.crop((round(cx - r), round(cy - r), round(cx + r), round(cy + r))).resize((SIZE, SIZE), Image.LANCZOS)
    px = np.asarray(crop, dtype=np.float64)
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    disc = ((xs - SIZE / 2 + .5) ** 2 + (ys - SIZE / 2 + .5) ** 2 < (SIZE / 2 - 2) ** 2).astype(np.float64)
    grey = np.asarray(crop.convert("L").filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
    gy, gx = np.gradient(grey)
    edge = Image.fromarray(np.hypot(gx, gy))
    edge = np.asarray(edge.resize((SIZE // 12, SIZE // 12), Image.BOX).resize((SIZE, SIZE), Image.BICUBIC), dtype=np.float64)
    weight = (1 + DETAIL * (np.clip(edge, 0, None) / np.percentile(edge, 95)) ** 1.4) * disc

    # the carmine clouds: clearly red pixels, closed into patches
    R, G, B = px[..., 0], px[..., 1], px[..., 2]
    red = ((R > 95) & (R > 1.5 * G) & (R > 1.2 * B)).astype(np.uint8) * 255
    red = np.asarray(Image.fromarray(red).filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(5)), dtype=np.float64) / 255 * disc
    assert 0.01 < red.sum() / disc.sum() < 0.08, red.sum() / disc.sum()
    inside, outside = (weight * red).sum(), (weight * (1 - red)).sum()
    share = n_it / n
    weight = weight * (1 - red) + weight * red * (share * outside / (1 - share)) / inside   # exactly its share

    rng = np.random.default_rng(46)
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
    d = disc.ravel()
    count = np.maximum(np.bincount(owner, d, n), 1)
    rgb = np.stack([np.bincount(owner, px[..., c].ravel() * d, n) / count for c in range(3)], axis=1)
    on_red = np.bincount(owner, red.ravel(), n) / count
    chosen = set(np.argsort(-on_red)[:n_it].tolist())
    s = 2 * RAD / SIZE
    circles = []
    for i in np.argsort(-count):      # the large touches first, the fine ones on top
        x, y = CX - RAD + float(pts[i, 0]) * s, CY - RAD + float(pts[i, 1]) * s
        rr = FILL * (count[i] * s * s) ** .5 * 1.06
        c = rgb[i]
        mean = c.mean()
        if i in chosen:               # a touch of the carmine clouds: pulled towards the cloud's own red
            c = c * .45 + np.array([226, 72, 77.0]) * .55
        else:
            c = (mean + (c - mean) * 1.75) * 1.28 + 4
        col = "#%02x%02x%02x" % tuple(int(min(max(v, 0), 255)) for v in c)
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rr:.1f}" fill="{col}"/>')
    assert len(circles) == n and len(chosen) == n_it

    month = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
             "novembre", "dicembre"][b.month - 1]
    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">{''.join(circles)}</svg>
<div class="n">{esc(wp.fmt(n, "it"))}</div>
<div class="t">nuove decisioni</div>
<div class="i"><b>{n_it}</b> in italiano</div>
<div class="d">Settimana {week["week_no"]}, {a.day}–{b.day} {month} {b.year}</div>
<div class="k">Un tocco, una decisione. In carminio quelle in italiano.</div>
<div class="c">Augusto Giacometti, Sternenhimmel (Cielo stellato), 1917</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:{GROUND};color:{INK};font-family:'Fraunces',serif}}
.n{{position:absolute;left:34px;top:1062px;font:500 196px/1 'Fraunces',serif;letter-spacing:-.03em;font-variation-settings:'opsz' 144,'SOFT' 0;
   font-variant-numeric:lining-nums}}
.t{{position:absolute;left:42px;top:1268px;font:400 34px/1 'Fraunces',serif}}
.i{{position:absolute;left:580px;top:1088px;font:400 46px/1 'Fraunces',serif;color:{CARMINE}}}
.i b{{font-weight:600;font-size:98px;letter-spacing:-.02em;vertical-align:-.04em;font-variation-settings:'opsz' 24;font-variant-numeric:lining-nums}}
.d{{position:absolute;left:582px;top:1204px;font:400 21px/1 'Fraunces',serif}}
.k{{position:absolute;left:582px;top:1238px;font:400 18.5px/1 'Fraunces',serif}}
.c{{position:absolute;left:582px;top:1270px;font:italic 400 16px/1 'Fraunces',serif;opacity:.8}}
.u{{position:absolute;left:582px;top:1306px;font:600 20px/1 'Archivo',sans-serif}}
"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;0,9..144,600;1,9..144,400&family=Archivo:wght@600", css, body, lang)
