"""Week 46 (6 - 12 November 2026), French: feuilles.

A November ground of fallen leaves: 1,132 of them, one for each new decision (a "feuille" is a
leaf and a sheet of paper). Golden leaves are decisions written in French (573), russet ones in
German (532), green ones in Italian (27). They lie thin where they are still falling and thick
where they have gathered; where a leaf lands is not data.

The leaves are real. Each is the print of one dried leaf from Constantin von Ettingshausen, "Die
Blatt-Skelete der Dikotyledonen" (Vienna 1861, public domain): a Naturselbstdruck, the leaf itself
pressed into the printing plate, so that the paper shows its venation line for line. 214 whole
leaves were cut from the scanned book by weekly_poster_w46_assets/build_leaves.py; each is used
five or six times here, turned and tinted. Only the tint is ours.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H, S = 1080, 1350, 2
ASSETS = Path(__file__).with_name("weekly_poster_w46_assets")
PAPER, INK = (240, 233, 216), "#1c1712"
TINT = {"fr": (214, 160, 24), "de": (178, 66, 30), "it": (72, 128, 62)}
FIELD = (46, 300, 1034, 1206)                     # where leaves may lie (css px)
KEEP_CLEAR = [(40, 0, 1080, 292)]                 # the head of the sheet stays free for the type
LEAF = 58.0                                       # mean length of a leaf, css px


def density(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """How thickly the leaves lie: a few still falling at the top, a deep layer further down."""
    t = np.clip((y - FIELD[1]) / (FIELD[3] - FIELD[1]), 0, 1)
    return 0.16 + 0.84 * np.clip((t - 0.05) / 0.5, 0, 1) ** 1.5


def places(n: int, rng: np.random.Generator) -> np.ndarray:
    """n places, spread so that no leaf lies exactly on another (best of several tries each time)."""
    pts = np.zeros((n, 2))
    for i in range(n):
        cand = []
        while len(cand) < 14:
            x, y = rng.uniform(FIELD[0], FIELD[2]), rng.uniform(FIELD[1], FIELD[3])
            if rng.random() < density(np.array(x), np.array(y)) and not any(a <= x <= c and b <= y <= d for a, b, c, d in KEEP_CLEAR):
                cand.append((x, y))
        cand = np.array(cand)
        if i == 0:
            pts[0] = cand[0]
            continue
        d = np.sqrt(((cand[:, None, :] - pts[None, :i, :]) ** 2).sum(2)).min(1)
        pts[i] = cand[np.argmax(d * np.sqrt(density(cand[:, 0], cand[:, 1])))]
    return pts


def ground(week: dict) -> Image.Image:
    meta = json.loads((ASSETS / "leaves.json").read_text())["leaves"]
    sheet = Image.open(ASSETS / "leaves.png").convert("RGB")
    n = week["added"]
    langs = [lg for lg in ("fr", "de", "it") for _ in range(week["languages"].get(lg, 0))]
    assert len(langs) == n
    rng = np.random.default_rng(46)
    rng.shuffle(langs)
    pts = places(n, rng)
    depth = pts[:, 1] + rng.normal(0, 60, n)                      # roughly from the top down, the heap on top;
    depth += np.array([1e6 if lg == "it" else 0 for lg in langs])  # the few Italian ones last, so none is buried
    order = np.argsort(depth)
    canvas = Image.new("RGBA", (W * S, H * S), PAPER + (255,))
    kinds = rng.permutation(n) % len(meta)
    for i in order:
        x0, y0, w, h, _ = meta[kinds[i]]
        tile = np.asarray(sheet.crop((x0, y0, x0 + w, y0 + h)), dtype=np.float32) / 255
        veins, body = tile[..., 0], tile[..., 1]
        c = np.array(TINT[langs[i]], dtype=np.float32) * rng.uniform(0.9, 1.08)
        light = c * 0.3 + np.array(PAPER, dtype=np.float32) * 0.7                     # the veins: the print, left pale
        rgb = c[None, None, :] * (1 - veins[..., None]) + light[None, None, :] * veins[..., None]
        leaf = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), body * 255]).astype(np.uint8), "RGBA")
        size = LEAF * S * rng.uniform(0.78, 1.25) / max(w, h)
        leaf = leaf.resize((max(2, round(w * size)), max(2, round(h * size))), Image.LANCZOS)
        leaf = leaf.rotate(rng.uniform(0, 360), resample=Image.BICUBIC, expand=True)
        px, py = round(pts[i, 0] * S - leaf.width / 2), round(pts[i, 1] * S - leaf.height / 2)
        shadow = Image.new("RGBA", leaf.size, (40, 28, 12, 0))
        shadow.putalpha(leaf.getchannel("A").filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.34)))
        canvas.alpha_composite(shadow, (max(0, px + 2), max(0, py + 3)), (max(0, -px - 2), max(0, -py - 3)))
        canvas.alpha_composite(leaf, (max(0, px), max(0, py)), (max(0, -px), max(0, -py)))
    return canvas.convert("RGB")


def design(week: dict, lang: str) -> str:
    data = io.BytesIO()
    ground(week).save(data, "JPEG", quality=93, subsampling=0)
    lg = week["languages"]
    hexc = {k: "#%02x%02x%02x" % v for k, v in TINT.items()}
    css = f"""
body{{background:rgb{PAPER};color:{INK};font-family:'Bodoni Moda',serif}}
img{{position:absolute;left:0;top:0;width:{W}px;height:{H}px}}
.n{{position:absolute;left:46px;top:6px;font:500 236px/1 'Bodoni Moda',serif;letter-spacing:-.035em;font-variation-settings:'opsz' 96;
   font-variant-numeric:lining-nums}}
.t{{position:absolute;right:50px;top:84px;text-align:right;font:italic 400 46px/1.05 'Bodoni Moda',serif;font-variation-settings:'opsz' 48}}
.t b{{display:block;font:500 15.5px/1.5 'Archivo',sans-serif;font-style:normal;letter-spacing:.13em;text-transform:uppercase;color:#6d6352;margin-top:16px}}
.k{{position:absolute;left:50px;top:1262px;font:500 21px/1.3 'Archivo',sans-serif;letter-spacing:.005em}}
.k i{{display:inline-block;width:13px;height:13px;border-radius:50%;margin:0 8px 0 0;vertical-align:-1px}}
.k span{{margin-right:26px;white-space:nowrap}}
.c{{position:absolute;left:50px;top:1304px;font:400 13.5px/1.3 'Archivo',sans-serif;letter-spacing:.03em;color:#6d6352}}
.u{{position:absolute;right:50px;top:1298px;font:600 20px/1 'Archivo',sans-serif}}
"""
    body = f"""<img src="data:image/jpeg;base64,{base64.b64encode(data.getvalue()).decode()}" alt="">
<div class="n">{wp.fmt(week['added'], lang)}</div>
<div class="t">nouvelles décisions<b>Semaine {week['week_no']} · du {week['from'].day} au {week['to'].day} novembre {week['to'].year}</b></div>
<div class="k"><span>Une feuille par décision :</span><span><i style="background:{hexc['fr']}"></i>{lg['fr']} en français</span>
<span><i style="background:{hexc['de']}"></i>{lg['de']} en allemand</span><span><i style="background:{hexc['it']}"></i>{lg['it']} en italien</span></div>
<div class="c">Feuilles : C. von Ettingshausen, Die Blatt-Skelete der Dikotyledonen, Vienne 1861, impressions naturelles</div>
<div class="u">opencaselaw.ch</div>"""
    return page("family=Bodoni+Moda:ital,opsz,wght@0,6..96,400..700;1,6..96,400..700&family=Archivo:wght@400;500;600", css, body, lang)
