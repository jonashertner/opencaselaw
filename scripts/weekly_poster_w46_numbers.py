"""Week 46 (6 - 12 November 2026), English: the week in its own case numbers.

The sheet is one block of type: the case number of every decision that arrived this week, each
exactly once, 1,132 of them, federal bodies first, then the cantons in alphabetical order. The
block is set in a fixed-width face, so every character sits in a cell of a grid; the cells that
fall inside the figure "1,132" are printed reversed, paper on red, the rest grey on paper. Nothing is drawn: the
figure is made of the decisions it counts.

The case numbers are the courts' own (weekly_poster_w46_assets/dockets.json, the 1,132 rows of the
week's verified count). The data protection commissioner publishes no case numbers; its five
documents stand as "EDÖB" with their date (two of one day as /1 and /2). The figure's shape is the face Anton (SIL Open Font
License), stretched to the sheet.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ASSETS = Path(__file__).with_name("weekly_poster_w46_assets")
X0, Y0, BW, BH = 40.0, 38.0, 1000.0, 1232.0          # the block of type
ADV = 0.6                                            # JetBrains Mono: advance in em
SEP = " · "
PAPER, INK, RED = "#f2eee4", "#191817", "#e3311d"


def numbers() -> list[str]:
    out = []
    for docket, court, _, _ in json.loads((ASSETS / "dockets.json").read_text(encoding="utf-8")):
        if court == "edoeb":
            y, m, d = re.search(r"(\d{4})-(\d{2})-(\d{2})$", docket).groups()
            docket = f"EDÖB {int(d)}.{int(m)}.{y}"
        out.append(" ".join(docket.split()))
    seen: dict[str, int] = {}
    for i, it in enumerate(out):                      # two EDÖB rulings of one day: /1 and /2
        if out.count(it) > 1:
            seen[it] = seen.get(it, 0) + 1
            out[i] = f"{it}/{seen[it]}"
    return out


def lines(items: list[str], cols: int) -> list[str]:
    """Fill lines of `cols` characters with whole case numbers and space them out to the full width."""
    rows, cur = [], []
    for it in items:
        if cur and len(SEP.join(cur + [it])) > cols:
            rows.append(cur)
            cur = []
        cur.append(it)
    rows.append(cur)
    out = []
    for i, r in enumerate(rows):
        if i == len(rows) - 1 or len(r) == 1:
            out.append(SEP.join(r).ljust(cols))
            continue
        spare = cols - len(SEP.join(r))
        gaps = len(r) - 1
        s = ""
        for k, it in enumerate(r[:-1]):
            extra = spare // gaps + (1 if k < spare % gaps else 0)
            left = extra // 2
            s += it + " " * (1 + left) + "·" + " " * (1 + extra - left)
        out.append(s + r[-1])
    assert all(len(o) == cols for o in out)
    return out


def figure(text: str, cols: int, rows: int) -> np.ndarray:
    """Which cells of the grid lie inside the figure: the word in Anton, stretched to the block."""
    font = ImageFont.truetype(str(ASSETS / "Anton-Regular.ttf"), 600)
    box = font.getbbox(text)
    img = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(img).text((-box[0], -box[1]), text, font=font, fill=255)
    k = 6
    pad_c, pad_r = 2, 2                                        # a margin of light type round the figure
    inner = img.resize(((cols - 2 * pad_c) * k, (rows - 2 * pad_r) * k), Image.LANCZOS)
    full = Image.new("L", (cols * k, rows * k), 0)
    full.paste(inner, (pad_c * k, pad_r * k))
    a = np.asarray(full, dtype=float).reshape(rows, k, cols, k).mean(axis=(1, 3)) / 255
    return a > 0.5


def design(week: dict, lang: str) -> str:
    items = numbers()
    assert len(items) == week["added"] == len(set(items)), (len(items), len(set(items)))
    # the number of columns for which the lines' height comes closest to 1.3 em
    best = None
    for cols in range(120, 220):
        n = len(lines(items, cols))
        fs = BW / (ADV * cols)
        ratio = BH / n / fs
        if best is None or abs(ratio - 1.3) < abs(best[0] - 1.3):
            best = (ratio, cols, n, fs)
    _, cols, n, fs = best
    text = lines(items, cols)
    inside = figure(wp.fmt(week["added"], lang), cols, n)
    lh = BH / n
    rows = []
    for j, line in enumerate(text):
        out, k = [], 0
        while k < cols:
            e = k
            while e < cols and inside[j, e] == inside[j, k]:
                e += 1
            seg = esc(line[k:e])
            out.append(f"<b>{seg}</b>" if inside[j, k] else seg)
            k = e
        rows.append(f'<div style="top:{j * lh:.2f}px">{"".join(out)}</div>')

    css = f"""
body{{background:{PAPER};color:{INK};font-family:'JetBrains Mono',monospace}}
.b{{position:absolute;left:{X0}px;top:{Y0}px;width:{BW}px;height:{BH}px;font-size:{fs:.4f}px;line-height:{lh:.3f}px;
   font-weight:400;font-variant-ligatures:none;font-feature-settings:"liga" 0,"calt" 0;color:#6b665c}}
.b div{{position:absolute;left:0;white-space:pre}}
.b b{{font-weight:700;color:{PAPER};background:{RED};padding:{max(0.0, (lh - fs * 1.32) / 2) + 0.3:.2f}px 0}}
.k{{position:absolute;left:{X0}px;top:1293px;font:500 16.5px/1.25 'JetBrains Mono',monospace;letter-spacing:-.01em}}
.k i{{font-style:normal;color:#6f6a60;font-weight:400}}
.u{{position:absolute;right:40px;top:1293px;font:700 16.5px/1.25 'JetBrains Mono',monospace}}
"""
    body = f"""<div class="b">{''.join(rows)}</div>
<div class="k">{wp.fmt(week['added'], lang)} new decisions · week {week['week_no']} · {week['from'].day}–{week['to'].day} November {week['to'].year}<br>
<i>Every case number of the week, printed once.</i></div>
<div class="u">opencaselaw.ch</div>"""
    return page("family=JetBrains+Mono:wght@300;400;500;700;800", css, body, lang)
