"""Week 43 (16-22 October 2026), Italian: the week on Justice's scales.

After Max Huber's posters for the Monza autodrome (1948-68): transparent colour printed over
image and type, one dynamic diagonal. Parmigianino's pen drawing of Justice holding scales
(1503-40; The Metropolitan Museum of Art, 62.120.3, CC0) fills the sheet; over it two discs are
printed in transparent red and blue, their areas exactly this week's new decisions (771) and a
usual week (1,040, the median of the previous eight). They hang from one beam, and the heavier
sinks; the beam's angle is the relative difference.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import math
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

ASSET = Path(__file__).with_name("weekly_poster_w43_assets") / "parmigianino.jpg"
NAME_IT = {"CH": "Tribunali federali", "GE": "Ginevra", "ZH": "Zurigo", "VD": "Vaud", "AG": "Argovia",
           "BS": "Basilea Città", "BE": "Berna", "BL": "Basilea Campagna", "SO": "Soletta", "NE": "Neuchâtel",
           "SG": "San Gallo", "TI": "Ticino"}
MONTH_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
            "ottobre", "novembre", "dicembre"]
INK, RED, BLUE, YELLOW = "#111111", "#e8352b", "#1f5bd8", "#ffd200"
MAX_TILT = 22.0
HAND = (0.887, 0.278)   # Justice's fingertips on the sheet, as fractions of its width and height
ART_H = 1500
ART_W = ART_H * 1374 / 1908
AT = (566, 286)          # where her fingertips sit on the poster
K = 7.4          # px of radius per sqrt(decision): area is proportional to the count


def num(v: float) -> str:
    return wp.fmt(int(v), "it") if float(v).is_integer() else f"{v:.1f}".replace(".", ",")


def design(week: dict, lang: str = "it") -> str:
    now, usual = week["added"], week["usual_total"]
    bc, uc = week["by_canton"], week["usual_canton"]
    a, b = week["from"], week["to"]
    img = base64.b64encode(ASSET.read_bytes()).decode()

    r1, r2 = K * math.sqrt(now), K * math.sqrt(usual)
    tilt = max(-MAX_TILT, min(MAX_TILT, MAX_TILT * 2 * (usual - now) / (now + usual)))
    cx, cy, half = AT[0], AT[1] + 96, 262
    art_x, art_y = AT[0] - HAND[0] * ART_W, AT[1] - HAND[1] * ART_H
    t = math.radians(tilt)
    lx, ly = cx - half * math.cos(t), cy - half * math.sin(t)
    rx, ry = cx + half * math.cos(t), cy + half * math.sin(t)
    d1y, d2y = ly + 120 + r1, ry + 120 + r2

    rows = [k for k in ("CH", "GE", "ZH", "VD", "AG", "BS", "BE", "BL", "SO", "NE", "SG", "TI") if k in bc]
    SHORT = {"CH": "Federali", "BS": "Basilea Città", "BL": "Basilea Camp."}
    pairs = []
    for i, k in enumerate(rows):
        x = 60 + i * 80 + 40
        y = 1118
        ra, rb = 1.55 * math.sqrt(bc[k]), 1.55 * math.sqrt(uc.get(k, 0))
        pairs.append(f'<circle cx="{x - ra * .45:.1f}" cy="{y}" r="{ra:.1f}" fill="{RED}" style="mix-blend-mode:multiply"/>'
                     f'<circle cx="{x + rb * .45:.1f}" cy="{y}" r="{rb:.1f}" fill="{BLUE}" style="mix-blend-mode:multiply"/>'
                     f'<text x="{x}" y="{y + 44}" text-anchor="middle" class="pk">{esc(SHORT.get(k, NAME_IT[k]))}</text>'
                     f'<text x="{x}" y="{y + 62}" text-anchor="middle" class="pv"><tspan class="pr">{num(bc[k])}</tspan> '
                     f'<tspan class="pb">{num(uc.get(k, 0))}</tspan></text>')

    body = f"""
<div class="art"></div>
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
<g style="mix-blend-mode:multiply">
<circle cx="{lx:.1f}" cy="{d1y:.1f}" r="{r1:.1f}" fill="{RED}"/>
<circle cx="{rx:.1f}" cy="{d2y:.1f}" r="{r2:.1f}" fill="{BLUE}"/>
</g>
<g stroke="{INK}" stroke-width="3" fill="none" stroke-linecap="round">
<line x1="{lx - 30:.1f}" y1="{ly - 30 * math.tan(t):.1f}" x2="{rx + 30:.1f}" y2="{ry + 30 * math.tan(t):.1f}" stroke-width="6"/>
<line x1="{cx}" y1="{AT[1]}" x2="{cx}" y2="{cy}" stroke-width="2.5"/>
<path d="M{lx:.1f} {ly:.1f}L{lx - r1 * .8:.1f} {d1y - r1 * .6:.1f}M{lx:.1f} {ly:.1f}L{lx + r1 * .8:.1f} {d1y - r1 * .6:.1f}" stroke-width="2"/>
<path d="M{rx:.1f} {ry:.1f}L{rx - r2 * .8:.1f} {d2y - r2 * .6:.1f}M{rx:.1f} {ry:.1f}L{rx + r2 * .8:.1f} {d2y - r2 * .6:.1f}" stroke-width="2"/>
</g>
<circle cx="{cx}" cy="{cy}" r="9" fill="{INK}"/>
<rect x="40" y="1080" width="1000" height="112" fill="#fff"/>
{''.join(pairs)}
<text x="{lx:.1f}" y="{d1y + 32:.1f}" text-anchor="middle" class="dn">{num(now)}</text>
<text x="{lx:.1f}" y="{d1y + 72:.1f}" text-anchor="middle" class="dl">questa settimana</text>
<text x="{rx:.1f}" y="{d2y + 30:.1f}" text-anchor="middle" class="un">{num(usual)}</text>
<text x="{rx:.1f}" y="{d2y + 68:.1f}" text-anchor="middle" class="dl">una settimana usuale</text>
</svg>
<header><span>Settimana {week['week_no']}</span><span>{a.day}–{b.day} {MONTH_IT[b.month - 1]} {b.year}</span></header>
<h1>nuove decisioni.<br>Il piatto più leggero sale.</h1>
<p class="note">Per la Confederazione e per ogni cantone: in rosso questa settimana, in blu una settimana usuale (mediana delle otto settimane precedenti). Area dei dischi proporzionale al numero. Parmigianino, <i>La Giustizia con la bilancia</i>, The Metropolitan Museum of Art (CC0).</p>
<footer><b>opencaselaw.ch</b><span>La giurisprudenza svizzera, libera e gratuita</span></footer>"""

    css = f"""
body{{background:#fff;color:{INK};font-family:'Archivo','Helvetica Neue',sans-serif}}
.art{{position:absolute;left:{art_x:.0f}px;top:{art_y:.0f}px;width:{ART_W:.0f}px;height:{ART_H}px;background:url(data:image/jpeg;base64,{img}) 0 0/100% 100%;
  filter:grayscale(1) brightness(1.28) contrast(1.85);-webkit-mask-image:linear-gradient(to right,#000 88%,transparent 98%)}}
header{{position:absolute;left:60px;right:60px;top:40px;display:flex;justify-content:space-between;font-size:18px;font-weight:700;
  letter-spacing:.14em;text-transform:uppercase}}
h1{{position:absolute;right:60px;top:84px;text-align:right;font-size:42px;line-height:1.05;font-weight:900;letter-spacing:-.02em}}
.dn{{font-size:118px;font-weight:900;fill:#fff;letter-spacing:-4px}}
.un{{font-size:112px;font-weight:900;fill:#fff;letter-spacing:-4px}}
.dl{{font-size:22px;font-weight:800;fill:#fff}}
.pk{{font-size:13px;font-weight:700;fill:{INK}}}.pv{{font-size:15px;font-weight:800}}.pr{{fill:{RED}}}.pb{{fill:{BLUE}}}
.note{{position:absolute;left:60px;right:60px;bottom:84px;font-size:12.5px;line-height:1.35;color:#444;background:#fff;padding:4px 10px;margin:0 -10px}}
footer{{position:absolute;left:60px;right:60px;bottom:36px;display:flex;justify-content:space-between;align-items:baseline;
  border-top:3px solid {INK};padding-top:10px;font-size:18px;background:#fff}}
footer b{{font-size:30px;font-weight:900}}"""
    return page("family=Archivo:wdth,wght@100..125,400..900", css, body, "it")
