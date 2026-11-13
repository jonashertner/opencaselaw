"""Week 46 (6 - 12 November 2026), Rumantsch: vopnas, a sheet of arms.

One coat of arms for each of the week's 1,132 new decisions: the arms of the canton whose court
gave it, the arms of the Confederation for the federal courts. They stand in rows like a woven
cloth, the largest group first: 308 Swiss crosses, 264 times Geneva, 164 times Vaud, down to one
Lucerne. No words beyond the figure and one line, as for every Rumantsch poster; the wording is
not proofread by a speaker.

The arms are the drawings on Wikimedia Commons ("Wappen <Kanton> matt.svg" and "Coat of Arms of
Switzerland (Pantone).svg"), all public domain as official arms; the files and their pages are
listed in weekly_poster_w46_assets/arms/SOURCES.json.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = 1080, 1350
ARMS = Path(__file__).with_name("weekly_poster_w46_assets") / "arms"
COLS = 36
X0, Y0, BW = 46.0, 262.0, 988.0
CELL_W, CELL_H = BW / COLS, 31.4
SHIELD_W, SHIELD_H = 22.6, 27.4
GROUND, INK = "#14120f", "#f3eee2"


def design(week: dict, lang: str) -> str:
    cn = week["by_canton"]
    order = sorted(cn.items(), key=lambda kv: (-kv[1], kv[0] != "CH", kv[0]))
    assert sum(n for _, n in order) == week["added"]
    cells, rules = [], []
    i = 0
    for code, n in order:
        svg = (ARMS / f"{code}.svg").read_bytes()
        rules.append(f".{code}{{background-image:url(data:image/svg+xml;base64,{base64.b64encode(svg).decode()})}}")
        for _ in range(n):
            x, y = X0 + (i % COLS) * CELL_W + (CELL_W - SHIELD_W) / 2, Y0 + (i // COLS) * CELL_H
            cells.append(f'<i class="{code}" style="left:{x:.1f}px;top:{y:.1f}px"></i>')
            i += 1
    assert i == week["added"]

    css = f"""
body{{background:{GROUND};color:{INK};font-family:'Fraunces',serif}}
.n{{position:absolute;left:40px;top:10px;font:500 212px/1 'Fraunces',serif;letter-spacing:-.03em;
   font-variation-settings:'opsz' 144,'SOFT' 0,'WONK' 0;font-variant-numeric:lining-nums}}
.t{{position:absolute;right:46px;top:84px;text-align:right;font:400 42px/1.1 'Fraunces',serif;font-variation-settings:'opsz' 60}}
.t b{{display:block;font:500 15px/1.5 'Archivo',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#a39c8c;margin-top:14px}}
.g i{{position:absolute;width:{SHIELD_W}px;height:{SHIELD_H}px;background-size:contain;background-repeat:no-repeat;background-position:center}}
{''.join(rules)}
.FR,.AG{{filter:drop-shadow(0 0 .7px rgba(243,238,226,.6))}}   /* black fields on the dark ground */
.k{{position:absolute;left:46px;top:1290px;font:italic 400 29px/1 'Fraunces',serif;font-variation-settings:'opsz' 36}}
.u{{position:absolute;right:46px;top:1295px;font:600 20px/1 'Archivo',sans-serif}}
"""
    body = f"""<div class="n">{wp.fmt(week['added'], lang)}</div>
<div class="t">novas decisiuns<b>Emna {week['week_no']} · {week['from'].day}–{week['to'].day} november {week['to'].year}</b></div>
<div class="g">{''.join(cells)}</div>
<div class="k">Mintga vopna è ina decisiun.</div>
<div class="u">opencaselaw.ch</div>"""
    return page("family=Fraunces:ital,opsz,wght,SOFT,WONK@0,9..144,300..900,0..100,0..1;1,9..144,300..900,0..100,0..1"
                "&family=Archivo:wght@500;600", css, body, lang)
