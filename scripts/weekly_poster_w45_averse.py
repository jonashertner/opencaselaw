"""Week 45 (30 October - 5 November 2026), French: the week as a downpour.

Félix Vallotton, born in Lausanne, drew "L'Averse" in 1894: a couple fighting one umbrella in a
city shower. The print (Bibliothèque nationale de France scan on Wikimedia Commons, CC0) is cleaned
down to its flat masses by scripts/weekly_poster_w45_assets/clean_vallotton.py, which also takes
out the rain Vallotton drew. The rain on the poster is new: exactly one stroke per decision of the
week, 905, spread evenly and cut into the picture the way a woodcut does it, black on the paper,
paper on the black. The two labels copy the print's own boxed lettering ("L'AVERSE." and "FV.94").

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import weekly_poster as wp
from weekly_poster_designs import esc, page

ASSET = Path(__file__).with_name("weekly_poster_w45_assets") / "vallotton_averse_bw.png"
X0, Y0, BOXW = 44, 64, 992          # the picture on the poster, in CSS px
SCALE = 2                           # the bitmap is drawn at the poster's 2x render scale
LENGTH, WIDTH = 30.0, 2.3           # one rain stroke, CSS px
ANGLE = math.radians(68)            # from the horizontal, falling to the right as in the print
PAPER, INK = "#ebe2cd", "#16130f"
MONTH_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"]


def spread(n: int, w: float, h: float, seed: int = 45, tries: int = 14) -> np.ndarray:
    """n points spread evenly over a w x h box (Mitchell's best candidate): each new point is the
    candidate farthest from the points already placed."""
    rng = np.random.default_rng(seed)
    pts = np.empty((n, 2))
    pts[0] = rng.random(2) * (w, h)
    for i in range(1, n):
        cand = rng.random((tries, 2)) * (w, h)
        d = ((cand[:, None, :] - pts[None, :i, :]) ** 2).sum(-1).min(1)
        pts[i] = cand[d.argmax()]
    return pts


def picture(n: int) -> tuple[str, int]:
    """The print with n rain strokes cut into it, as a PNG data URI, and its height in CSS px."""
    src = Image.open(ASSET).convert("L")
    boxh = round(BOXW * src.height / src.width)
    W, H = BOXW * SCALE, boxh * SCALE
    base = np.asarray(src.resize((W, H), Image.LANCZOS)) > 127          # True = paper
    over = 4                                                              # supersampling for the strokes
    mask = Image.new("L", (W * over, H * over), 0)
    draw = ImageDraw.Draw(mask)
    dx, dy = math.cos(ANGLE) * LENGTH / 2, math.sin(ANGLE) * LENGTH / 2
    pad = LENGTH / 2
    for x, y in spread(n, BOXW - 2 * pad, boxh - 2 * pad) + pad:
        f = SCALE * over
        draw.line([((x - dx) * f, (y - dy) * f), ((x + dx) * f, (y + dy) * f)], fill=255,
                  width=round(WIDTH * f))
    m = np.asarray(mask.resize((W, H), Image.LANCZOS)).astype(np.float64) / 255
    ink, paper = np.array(Image.new("RGB", (1, 1), INK))[0, 0], np.array(Image.new("RGB", (1, 1), PAPER))[0, 0]
    tone = np.where(base[..., None], paper, ink).astype(np.float64)    # the print
    flip = np.where(base[..., None], ink, paper).astype(np.float64)    # the stroke cuts the other colour
    rgb = tone * (1 - m[..., None]) + flip * m[..., None]
    buf = io.BytesIO()
    Image.fromarray(rgb.round().astype(np.uint8)).save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), boxh


def design(week: dict, lang: str = "fr") -> str:
    a, b = week["from"], week["to"]
    uri, boxh = picture(week["added"])
    bottom = Y0 + boxh
    span = (f"du {a.day}{'ᵉʳ' if a.day == 1 else ''} {MONTH_FR[a.month - 1]} au "
            f"{b.day}{'ᵉʳ' if b.day == 1 else ''} {MONTH_FR[b.month - 1]} {b.year}")
    body = f"""
<div class="pic" style="top:{Y0}px;height:{boxh}px"><img src="{uri}" width="{BOXW}" height="{boxh}"></div>
<div class="lab l" style="top:{bottom - 130}px"><b>{esc(wp.fmt(week["added"], "fr"))}</b><span>NOUVELLES<br>DÉCISIONS.</span></div>
<div class="lab r" style="top:{bottom - 66}px">S.{week["week_no"]}</div>
<div class="cap" style="top:{bottom + 26}px">Semaine {week["week_no"]}, {span}.<br>Chaque trait de pluie est une nouvelle décision.</div>
<div class="src" style="top:{bottom + 30}px">opencaselaw.ch<br><i>D’après Félix Vallotton, L’Averse, 1894</i></div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Oswald',sans-serif}}
.pic{{position:absolute;left:{X0}px;width:{BOXW}px;outline:7px solid {INK};outline-offset:0}}
.pic img{{display:block}}
.lab{{position:absolute;background:{PAPER};border:5px solid {INK};font-family:'Oswald',sans-serif;font-weight:600;
  text-transform:uppercase;line-height:1;color:{INK}}}
.lab.l{{left:{X0}px;height:130px;display:flex;align-items:center;gap:18px;padding:0 26px 0 18px;border-left:0;border-bottom:0}}
.lab.l b{{font-size:124px;font-weight:700;letter-spacing:-.01em;margin-top:-10px}}
.lab.l span{{font-size:36px;letter-spacing:.03em;line-height:1.05}}
.lab.r{{right:{1080 - X0 - BOXW}px;height:66px;padding:0 16px;display:flex;align-items:center;font-size:40px;letter-spacing:.04em;
  border-right:0;border-bottom:0}}
.cap{{position:absolute;left:{X0}px;font:400 24px/1.4 'Oswald',sans-serif;letter-spacing:.01em}}
.src{{position:absolute;right:{X0}px;text-align:right;font:500 24px/1.4 'Oswald',sans-serif}}
.src i{{font:italic 400 17px/1.6 'EB Garamond',serif}}
"""
    return page("family=Oswald:wght@400;500;600;700&family=EB+Garamond:ital@1", css, body, lang)
