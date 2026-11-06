"""Week 45 (30 October - 5 November 2026), German: a serial order of the week.

After the Zürich Concrete artists (Richard Paul Lohse's "Serielle Ordnungen"): one square per new
decision, 905 in all, laid out by rule and not by hand. The squares fill columns of thirty from the
top left, court by court in order of size, and every court gets the next colour of one spectrum.
So the field is the count: the steps between the colours are the figures, and the last column
holds what is left over. Only the method is borrowed, no painting.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import math

import weekly_poster as wp
from weekly_poster_designs import esc, page

ROWS = 30
CELL, GAP = 26.6, 2.0
LEFT, TOP = 96.0, 272.0
PAPER, INK = "#f4f2ec", "#141414"
MONTH_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
            "November", "Dezember"]
NAME_DE = {"CH": "Bundesgerichte", "ZH": "Zürich", "BE": "Bern", "LU": "Luzern", "UR": "Uri", "SZ": "Schwyz",
           "OW": "Obwalden", "NW": "Nidwalden", "GL": "Glarus", "ZG": "Zug", "FR": "Freiburg", "SO": "Solothurn",
           "BS": "Basel-Stadt", "BL": "Basel-Landschaft", "SH": "Schaffhausen", "AR": "Appenzell A.Rh.",
           "AI": "Appenzell I.Rh.", "SG": "St. Gallen", "GR": "Graubünden", "AG": "Aargau", "TG": "Thurgau",
           "TI": "Tessin", "VD": "Waadt", "VS": "Wallis", "NE": "Neuenburg", "GE": "Genf", "JU": "Jura"}


def oklch(l: float, c: float, h: float) -> str:
    """OKLCH to an sRGB hex string (clipped)."""
    a, b = c * math.cos(math.radians(h)), c * math.sin(math.radians(h))
    l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (l - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def gamma(v: float) -> int:
        v = min(1.0, max(0.0, v))
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return round(v * 255)
    return "#" + "".join(f"{gamma(v):02x}" for v in rgb)


def spectrum(n: int) -> list[str]:
    """n colours in one sweep, red through yellow and green to blue and violet. Lightness follows the
    hue a little, as pigment colours do, so the yellows stay yellow, and steps up and down from one
    colour to the next, so that two neighbouring bands never run together."""
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        h = 25 + 290 * t
        l = 0.58 + 0.2 * math.exp(-((h - 100) / 45) ** 2) + (0.07 if i % 2 else -0.07)
        out.append(oklch(l, 0.16, h))
    return out


def design(week: dict, lang: str = "de") -> str:
    a, b = week["from"], week["to"]
    groups = sorted(((n, k) for k, n in week["by_canton"].items() if n), key=lambda t: (-t[0], t[1]))
    colours = spectrum(len(groups))

    cells, legend = [], []
    i = 0
    for (n, k), col in zip(groups, colours):
        for _ in range(n):
            cx, cy = divmod(i, ROWS)
            x, y = LEFT + cx * (CELL + GAP), TOP + cy * (CELL + GAP)
            cells.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{CELL}" height="{CELL}" fill="{col}"/>')
            i += 1
        legend.append(f'<div class="l"><i style="background:{col}"></i><span>{esc(NAME_DE[k])}</span>'
                      f'<b>{esc(wp.fmt(n, "de"))}</b></div>')
    assert i == week["added"]
    rows = (len(legend) + 2) // 3
    cols = "".join(f'<div class="c">{"".join(legend[j * rows:(j + 1) * rows])}</div>' for j in range(3))

    body = f"""
<div class="n">{esc(wp.fmt(week["added"], "de"))}</div>
<div class="t">neue Entscheide in Woche {week["week_no"]}<br>{a.day}. {MONTH_DE[a.month - 1]} – {b.day}. {MONTH_DE[b.month - 1]} {b.year}</div>
<div class="r">Ein Quadrat pro Entscheid,<br>dreissig pro Spalte, nach Grösse</div>
<svg class="f" width="1080" height="1350" viewBox="0 0 1080 1350">{''.join(cells)}</svg>
<div class="lg">{cols}</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Inter Tight',sans-serif}}
.f{{position:absolute;left:0;top:0}}
.n{{position:absolute;left:88px;top:62px;font:800 168px/1 'Inter Tight',sans-serif;letter-spacing:-.045em}}
.t{{position:absolute;left:490px;top:86px;font:600 28px/1.25 'Inter Tight',sans-serif}}
.r{{position:absolute;left:490px;top:176px;font:400 19px/1.3 'Inter Tight',sans-serif;color:#555}}
.lg{{position:absolute;left:96px;top:1172px;width:890px;display:flex;gap:28px}}
.c{{flex:1}}
.l{{display:flex;align-items:center;font:500 17px/1.48 'Inter Tight',sans-serif}}
.l i{{width:13px;height:13px;margin-right:9px;flex:none}}
.l span{{flex:1}}
.l b{{font-weight:700;font-variant-numeric:tabular-nums}}
.u{{position:absolute;right:94px;top:62px;font:700 21px/1 'Inter Tight',sans-serif}}
"""
    return page("family=Inter+Tight:wght@400;500;600;700;800", css, body, lang)
