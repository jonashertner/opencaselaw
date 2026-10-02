#!/usr/bin/env python3
"""The weekly poster as five editions: one design per language of the site, same week, same numbers.

    .venv/bin/python scripts/weekly_poster_editions.py            # all five, into output/posters/
    .venv/bin/python scripts/weekly_poster_editions.py de it      # a selection
    .venv/bin/python scripts/weekly_poster_editions.py --archive  # and publish into docs/posters/ (Friday job)

  de  topo      Where did the week happen?    A relief map: every court seat a summit.
  fr  sediment  How deep is the record?       One line per year since 1875.
  it  score     When did the week arrive?     Canton by night, overprinted circles.
  rm  fields    Whose week was it?            Four colour fields, one per language region.
  en  receipt   What exactly did we get?      Every court, itemised. Price: CHF 0.00.

Data and counting rule: weekly_poster.py. Border and lakes: weekly_poster_geo.json.
"""
from __future__ import annotations

import html
import math
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_poster as wp  # noqa: E402

W, H = wp.W, wp.H
FED = wp.FEDERAL
LANG = "en"

# Cantonal capitals and federal court seats, Swiss grid (LV03, km east / km north).
SEATS = {
    "ZH": ("Zürich", 683, 248), "BE": ("Bern", 600, 200), "LU": ("Luzern", 666, 211),
    "UR": ("Altdorf", 692, 193), "SZ": ("Schwyz", 692, 208), "OW": ("Sarnen", 661, 194),
    "NW": ("Stans", 670, 201), "GL": ("Glarus", 724, 211), "ZG": ("Zug", 681, 225),
    "FR": ("Fribourg", 579, 184), "SO": ("Solothurn", 607, 228), "BS": ("Basel", 611, 267),
    "BL": ("Liestal", 622, 259), "SH": ("Schaffhausen", 690, 283), "AR": ("Herisau", 739, 250),
    "AI": ("Appenzell", 749, 244), "SG": ("St. Gallen", 746, 254), "GR": ("Chur", 759, 191),
    "AG": ("Aarau", 646, 249), "TG": ("Frauenfeld", 710, 268), "TI": ("Bellinzona", 722, 117),
    "VD": ("Lausanne", 538, 152), "VS": ("Sion", 594, 120), "NE": ("Neuchâtel", 561, 205),
    "GE": ("Genève", 500, 118), "JU": ("Delémont", 593, 246),
}
FEDERAL_SEAT = {"bger": "VD", "bge": "VD", "bvger": "SG", "bpatger": "SG", "bstger": "TI"}  # others: Bern
COURT_NAMES = {"bger": "BGer", "bvger": "BVGer", "bstger": "BStGer", "bpatger": "BPatGer", "bge": "BGE",
               "edoeb": "EDÖB"}
REGIONS = {
    "de": ("Deutschschweiz", [k for k in wp.TILES if k not in wp.FRENCH | wp.ITALIAN]),
    "fr": ("Suisse romande", sorted(wp.FRENCH)),
    "it": ("Svizzera italiana", sorted(wp.ITALIAN)),
    "ch": ("Bund", [FED]),
}


def n(v: int, lang: str = LANG) -> str:
    return wp.fmt(v, lang)


def esc(s: str) -> str:
    return html.escape(s)


WEEK = {"en": "Week", "de": "Woche", "fr": "Semaine", "it": "Settimana", "rm": "Emna"}


def meta(delta: dict, lang: str = LANG) -> tuple[str, str]:
    iso = delta["to"].isocalendar()
    return (f"{WEEK[lang]} {iso.week}, {iso.year}",
            f"{wp.fmt_day(delta['from'], lang)}\u2013{wp.fmt_day(delta['to'], lang)}")


def week_hue(delta: dict) -> int:
    """A hue per ISO week: 53 posters walk once around the colour wheel."""
    return round(delta["to"].isocalendar().week * 360 / 53) % 360


def page(fonts: str, css: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?{fonts}&display=swap" rel="stylesheet">
<style>*{{box-sizing:border-box;margin:0;padding:0}}html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{position:relative;font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased}}
svg{{position:absolute;inset:0}}{css}</style></head><body>{body}</body></html>"""


# ── 1. topo ───────────────────────────────────────────────────────────────────

def contour_segments(field, level: float) -> list[tuple[float, float, float, float]]:
    """Marching squares: line segments (in grid units) where `field` crosses `level`."""
    import numpy as np

    a, b, c, d = field[:-1, :-1], field[:-1, 1:], field[1:, 1:], field[1:, :-1]  # tl tr br bl
    case = (a >= level) * 8 + (b >= level) * 4 + (c >= level) * 2 + (d >= level) * 1
    out = []
    for j, i in zip(*np.nonzero((case > 0) & (case < 15))):
        va, vb, vc, vd = a[j, i], b[j, i], c[j, i], d[j, i]
        t = lambda p, q: (level - p) / (q - p)
        edge = {
            "t": lambda: (i + t(va, vb), j), "r": lambda: (i + 1, j + t(vb, vc)),
            "b": lambda: (i + t(vd, vc), j + 1), "l": lambda: (i, j + t(va, vd)),
        }
        pairs = {1: ["lb"], 2: ["br"], 3: ["lr"], 4: ["tr"], 5: ["tl", "br"], 6: ["tb"], 7: ["tl"],
                 8: ["tl"], 9: ["tb"], 10: ["tr", "lb"], 11: ["tr"], 12: ["lr"], 13: ["br"], 14: ["lb"]}
        for p in pairs[int(case[j, i])]:
            (x1, y1), (x2, y2) = edge[p[0]](), edge[p[1]]()
            out.append((x1, y1, x2, y2))
    return out


def seat_heights(delta: dict) -> dict[str, int]:
    """New decisions per seat: cantonal courts at the capital, federal courts where they sit."""
    seats: dict[str, int] = {}
    for (court, canton), v in delta["by_key"].items():
        seat = FEDERAL_SEAT.get(court, "BE") if canton == FED else canton
        seats[seat] = seats.get(seat, 0) + v
    return seats


# label placement per seat: (dx, dy, anchor), tuned for the crowded centre and north-east
LABEL_AT = {
    "OW": (-9, 5, "end"), "NW": (9, 5, "start"), "LU": (-9, -6, "end"), "ZG": (9, 14, "start"),
    "SZ": (9, 6, "start"), "UR": (9, 14, "start"), "AR": (-9, 12, "end"), "AI": (9, 18, "start"),
    "SG": (9, -6, "start"), "BL": (9, 5, "start"), "BS": (-9, -4, "end"), "SO": (-9, 5, "end"),
    "AG": (9, 16, "start"), "ZH": (9, -6, "start"), "TG": (9, 5, "start"), "SH": (9, 5, "start"),
    "GE": (9, -8, "start"), "VD": (9, -8, "start"), "FR": (9, 14, "start"), "BE": (-9, 5, "end"),
    "JU": (-9, 5, "end"), "NE": (-9, 5, "end"), "GL": (9, 5, "start"),
}


TOPO = {
    "en": {
        "title": ("Topography of a week", "in Swiss case law"), "sheet": "Sheet",
        "sub": "{n} court decisions entered the open corpus. Every summit is a court seat, and its height is the number of new decisions.",
        "key": "Contour interval: every line doubles the count. Highest summit this week: {place}, {v}. Federal courts stand at their seats: the Federal Supreme Court in Lausanne, the Federal Administrative Court in St. Gallen, the Federal Criminal Court in Bellinzona, other federal authorities in Bern. Grey points: no new decisions this week.",
        "foot": "{t} decisions since 1875, free to search, cite and download",
    },
    "de": {
        "title": ("Topografie einer Woche", "Schweizer Rechtsprechung"), "sheet": "Blatt",
        "sub": "{n} Entscheide sind neu im offenen Korpus. Jeder Gipfel ist ein Gerichtssitz, seine Höhe die Zahl der neuen Entscheide.",
        "key": "Äquidistanz: Jede Höhenlinie verdoppelt die Zahl. Höchster Gipfel der Woche: {place}, {v}. Die Gerichte des Bundes stehen an ihrem Sitz: das Bundesgericht in Lausanne, das Bundesverwaltungsgericht in St. Gallen, das Bundesstrafgericht in Bellinzona, die übrigen Bundesbehörden in Bern. Graue Punkte: keine neuen Entscheide in dieser Woche.",
        "foot": "{t} Entscheide seit 1875, frei durchsuchbar, zitierbar und herunterladbar",
    },
    "fr": {
        "title": ("Topographie d’une semaine", "de jurisprudence suisse"), "sheet": "Feuille",
        "sub": "{n} décisions ont rejoint le corpus ouvert. Chaque sommet est le siège d’un tribunal, sa hauteur le nombre de nouvelles décisions.",
        "key": "Équidistance: chaque courbe de niveau double le nombre. Plus haut sommet de la semaine: {place}, {v}. Les tribunaux fédéraux figurent à leur siège: le Tribunal fédéral à Lausanne, le Tribunal administratif fédéral à Saint-Gall, le Tribunal pénal fédéral à Bellinzone, les autres autorités fédérales à Berne. Points gris: aucune nouvelle décision cette semaine.",
        "foot": "{t} décisions depuis 1875, libres à consulter, citer et télécharger",
    },
    "it": {
        "title": ("Topografia di una settimana", "di giurisprudenza svizzera"), "sheet": "Foglio",
        "sub": "{n} decisioni sono entrate nel corpus aperto. Ogni vetta è la sede di un tribunale, la sua altezza il numero di nuove decisioni.",
        "key": "Equidistanza: ogni curva di livello raddoppia il numero. Vetta più alta della settimana: {place}, {v}. I tribunali federali figurano nella loro sede: il Tribunale federale a Losanna, il Tribunale amministrativo federale a San Gallo, il Tribunale penale federale a Bellinzona, le altre autorità federali a Berna. Punti grigi: nessuna nuova decisione questa settimana.",
        "foot": "{t} decisioni dal 1875, da consultare, citare e scaricare liberamente",
    },
}


def relief_png(field) -> str:
    """Imhof-style shaded relief of the data surface, as a base64 PNG (light from the north-west)."""
    import base64
    import io

    import numpy as np
    from PIL import Image

    z = np.log2(1 + 2 * field)  # one unit of height per contour line
    gy, gx = np.gradient(z * 15.0)
    norm = np.sqrt(gx**2 + gy**2 + 1)
    shade = (gx * 0.5 + gy * 0.5 + 0.7071) / norm  # n = (-gx, -gy, 1), light = (-.5, -.5, .707)
    d = shade - 0.7071
    lit = np.clip(d / 0.29, 0, 1)[..., None] * 0.95
    dark = np.clip(-d / 0.55, 0, 1)[..., None] * 0.8
    height = (z / max(z.max(), 1e-9))[..., None] * 0.55
    rgb = np.full(z.shape + (3,), 255.0)
    rgb += (np.array([247, 229, 190]) - rgb) * height  # hypsometric tint: warmer with height
    rgb += (np.array([255, 246, 214]) - rgb) * lit  # sunlit slopes
    rgb += (np.array([92, 104, 150]) - rgb) * dark  # blue-violet shadow slopes
    buf = io.BytesIO()
    Image.fromarray(rgb.clip(0, 255).astype("uint8")).save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def design_topo(delta: dict, cur: dict, lang: str = "en") -> str:
    import json

    import numpy as np

    t = TOPO[lang]
    num = lambda v: wp.fmt(v, lang)
    iso = delta["to"].isocalendar()
    span = f"{wp.fmt_day(delta['from'], lang)}–{wp.fmt_day(delta['to'], lang)}"
    span += f" {delta['to'].year}" if lang == "en" else str(delta["to"].year)
    geo = json.loads((Path(__file__).with_name("weekly_poster_geo.json")).read_text())

    x0, y0, mw = 40, 428, 1000
    e0, e1, n0, n1 = 476, 842, 68, 302
    s = mw / (e1 - e0)
    mh = round((n1 - n0) * s)
    px = lambda e: x0 + (e - e0) * s
    py = lambda nn: y0 + (n1 - nn) * s
    ring = lambda pts: "M" + "L".join(f"{px(e):.1f} {py(nn):.1f}" for e, nn in pts) + "Z"

    heights = seat_heights(delta)
    sigma = 8.5 * s
    gx, gy = np.meshgrid(np.arange(mw + 1), np.arange(mh + 1))
    field = np.zeros_like(gx, dtype=float)
    for seat, v in heights.items():
        _, e, nn = SEATS[seat]
        field += v * np.exp(-((gx - (px(e) - x0)) ** 2 + (gy - (py(nn) - y0)) ** 2) / (2 * sigma**2))

    step = 3
    coarse = field[::step, ::step]
    lines = []
    for k in range(12):
        level = 0.5 * 2**k
        if level > coarse.max():
            break
        d = "".join(f"M{x0 + a * step:.1f} {y0 + b * step:.1f}L{x0 + c * step:.1f} {y0 + e * step:.1f}"
                    for a, b, c, e in contour_segments(coarse, level))
        index = k % 4 == 3  # 4, 64: the heavier index contours
        lines.append(f'<path d="{d}" fill="none" stroke="#9c4f1f" stroke-width="{1.9 if index else 0.8}" '
                     f'stroke-linecap="round"/>')

    labels = []
    for code, (name, e, nn) in SEATS.items():
        v = heights.get(code, 0)
        x, y = px(e), py(nn)
        dx, dy, anchor = LABEL_AT.get(code, (9, 5, "start"))
        if v:
            labels.append(f'<path d="M{x - 4.5:.1f} {y + 3.5:.1f}h9l-4.5 -8z" fill="#111"/>')
            labels.append(f'<text x="{x + dx:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}" class="pl">'
                          f'{esc(name)} <tspan class="el">{num(v)}</tspan></text>')
        else:
            labels.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.9" fill="#6f6f6f"/>')
            labels.append(f'<text x="{x + dx:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}" class="pl z">{esc(name)}</text>')

    ticks = []
    for e in range(500, e1, 50):
        ticks.append(f'<path d="M{px(e):.1f} {y0}v-7M{px(e):.1f} {y0 + mh}v7" stroke="#111" stroke-width="1"/>'
                     f'<text x="{px(e):.1f}" y="{y0 - 12}" text-anchor="middle" class="tk">{2000 + e}</text>')
    for nn in range(100, n1, 50):
        ticks.append(f'<path d="M{x0} {py(nn):.1f}h-7M{x0 + mw} {py(nn):.1f}h7" stroke="#111" stroke-width="1"/>'
                     f'<text x="{x0 - 11}" y="{py(nn):.1f}" text-anchor="middle" class="tk" '
                     f'transform="rotate(-90 {x0 - 11} {py(nn):.1f})">{1000 + nn}</text>')
    bar = 50 * s
    top = max(heights.items(), key=lambda kv: kv[1])
    border = ring(geo["border"])
    lakes = "".join(ring(l) for l in geo["lakes"])
    img = f'<image href="data:image/png;base64,{relief_png(field)}" x="{x0}" y="{y0}" width="{mw + 1}" height="{mh + 1}"'
    size = min(112, 1000 / (max(map(len, t["title"])) * 0.385))

    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>{t['sheet']} {iso.week} &nbsp; {esc(span)}</span></div>
<h1 style="font-size:{size:.0f}px">{esc(t['title'][0])}<br>{esc(t['title'][1])}</h1>
<p class="sub">{esc(t['sub'].format(n=num(delta['added'])))}</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs><clipPath id="ch"><path d="{border}"/></clipPath><clipPath id="fr"><rect x="{x0}" y="{y0}" width="{mw}" height="{mh}"/></clipPath></defs>
<g clip-path="url(#fr)">
{img} opacity=".42"/>
<g clip-path="url(#ch)">{img}/></g>
<path d="{lakes}" fill="#cfe2f1" stroke="#5f97c4" stroke-width=".8"/>
<path d="{border}" fill="none" stroke="#c2185b" stroke-opacity=".22" stroke-width="7" stroke-linejoin="round"/>
<path d="{border}" fill="none" stroke="#111" stroke-width="1.1" stroke-linejoin="round"/>
{''.join(lines)}
</g>
<rect x="{x0}" y="{y0}" width="{mw}" height="{mh}" fill="none" stroke="#111" stroke-width="1.4"/>
{''.join(ticks)}{''.join(labels)}
<path d="M{x0 + mw - bar:.1f} {y0 + mh + 38}h{bar:.1f}M{x0 + mw - bar:.1f} {y0 + mh + 32}v12M{x0 + mw} {y0 + mh + 32}v12" stroke="#111" stroke-width="1.4"/>
<text x="{x0 + mw - bar - 10:.1f}" y="{y0 + mh + 43}" class="tk" text-anchor="end" style="font-size:15px">50 km</text>
</svg>
<p class="key" style="top:{y0 + mh + 26}px">{esc(t['key'].format(place=SEATS[top[0]][0], v=num(top[1])))}</p>
<div class="ft"><b>opencaselaw.ch</b><span>{esc(t['foot'].format(t=num(cur['total'])))}</span></div>"""
    css = """
body{background:#fff;color:#111;font-family:'IBM Plex Sans Condensed','Helvetica Neue',sans-serif}
.hd{position:absolute;left:40px;right:40px;top:34px;display:flex;justify-content:space-between;font-size:20px;font-weight:500}
h1{position:absolute;left:40px;top:80px;width:1000px;font-family:'Instrument Serif',Georgia,serif;font-weight:400;line-height:.9;letter-spacing:-.015em;white-space:nowrap}
.sub{position:absolute;left:40px;top:306px;width:900px;font-size:21px;line-height:1.3}
.pl{font-family:'Instrument Serif',Georgia,serif;font-style:italic;font-size:21px;fill:#111;paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round}
.pl.z{fill:#6f6f6f;font-size:17px;stroke-width:3px}
.el{font-family:'IBM Plex Sans Condensed',sans-serif;font-style:normal;font-weight:600;font-size:17px}
.tk{font-family:'IBM Plex Sans Condensed',sans-serif;font-size:13px;fill:#111}
.key{position:absolute;left:40px;width:770px;font-size:17px;line-height:1.36}
.ft{position:absolute;left:40px;right:40px;bottom:36px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;border-top:1.4px solid #111;padding-top:14px}
.ft b{font-family:'Instrument Serif',serif;font-weight:400;font-size:34px}"""
    doc = page("family=Instrument+Serif:ital@0;1&family=IBM+Plex+Sans+Condensed:wght@400;500;600", css, body)
    return doc.replace('<html lang="en">', f'<html lang="{lang}">', 1)


# ── 2. sediment ───────────────────────────────────────────────────────────────

SEDIMENT = {
    "en": {"h1": "{y} years of Swiss case law, one line per year.",
           "sub": "Line length is the number of decisions decided that year and openly available today, {t} in all.",
           "wk": "{n} arrived this week; the coloured line is {last} so far.",
           "foot": "Free to search, cite and download."},
    "fr": {"h1": "{y} ans de jurisprudence suisse, une ligne par année.",
           "sub": "La longueur d\u2019une ligne est le nombre de décisions rendues cette année-là et librement accessibles aujourd\u2019hui, {t} en tout.",
           "wk": "{n} sont arrivées cette semaine; la ligne colorée est {last} à ce jour.",
           "foot": "Libres à consulter, citer et télécharger."},
}


def design_sediment(delta: dict, cur: dict, lang: str = "en") -> str:
    week, span = meta(delta, lang)
    t = SEDIMENT[lang]
    n = lambda v: wp.fmt(v, lang)  # noqa: E731
    accent = f"oklch(0.82 0.19 {week_hue(delta)})"
    last = delta["to"].year
    years = [y for y in range(1875, last + 1)]
    by = {int(k): v for k, v in cur["by_year"].items()}
    top_y, bottom_y = 344, 1250
    pitch = (bottom_y - top_y) / len(years)
    cx, full = 624, 820
    vmax = max(by.get(y, 0) for y in years)
    bars, marks = [], []
    for i, y in enumerate(years):
        w = max(1.6, by.get(y, 0) / vmax * full)
        yy = bottom_y - (i + 1) * pitch
        bars.append(f'<rect x="{cx - w / 2:.1f}" y="{yy:.2f}" width="{w:.1f}" height="{pitch * 0.72:.2f}" '
                    f'fill="{accent if y == last else "#fff"}"/>')
        if (y % 25 == 0 and last - y > 4) or y in (1875, last):
            marks.append(f'<text x="40" y="{yy + pitch * 0.36 + 5:.1f}" class="yr">{y}</text>'
                         f'<text x="188" y="{yy + pitch * 0.36 + 5:.1f}" class="ct" text-anchor="end">{n(by.get(y, 0))}</text>')
    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>{esc(week)} &nbsp; {esc(span)}</span></div>
<h1>{esc(t['h1'].format(y=last - 1875))}</h1>
<p class="sub">{esc(t['sub'].format(t=n(cur['total'])))}
<b style="color:{accent}">{esc(t['wk'].format(n=n(delta['added']), last=last))}</b></p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">{''.join(bars)}{''.join(marks)}</svg>
<div class="ft"><b>opencaselaw.ch</b><span>{esc(t['foot'])}</span></div>"""
    css = f"""
body{{background:#000;color:#fff;font-family:'Bricolage Grotesque','Helvetica Neue',sans-serif}}
.hd{{position:absolute;left:40px;right:40px;top:34px;display:flex;justify-content:space-between;font-size:20px;font-weight:500}}
h1{{position:absolute;left:40px;top:82px;width:1000px;font-weight:800;font-size:{66 if lang == 'en' else 61}px;line-height:.98;letter-spacing:-.03em}}
.sub{{position:absolute;left:40px;top:226px;width:960px;font-size:21px;line-height:1.3;font-weight:400}}
.sub b{{font-weight:600}}
.yr{{font-family:'Bricolage Grotesque',sans-serif;font-size:19px;font-weight:700;fill:#fff}}
.ct{{font-family:'Bricolage Grotesque',sans-serif;font-size:17px;font-weight:400;fill:#9a9a9a}}
.ft{{position:absolute;left:40px;right:40px;bottom:34px;display:flex;justify-content:space-between;align-items:baseline;font-size:20px}}
.ft b{{font-weight:800;font-size:28px;letter-spacing:-.02em}}"""
    return page("family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,500;12..96,600;12..96,700;12..96,800", css, body)


# ── 3. score ──────────────────────────────────────────────────────────────────

INK = {"de": "#0078BF", "fr": "#FF48B0", "it": "#FF6C2F", "ch": "#00A95C"}  # risograph inks


def region_of(code: str) -> str:
    return "ch" if code == FED else "fr" if code in wp.FRENCH else "it" if code in wp.ITALIAN else "de"


SCORE = {
    "en": {"h1": "How the week arrived", "fed": "Federal", "nb": "no build",
           "sub": "{n} court decisions, by canton and by the night our build picked them up. Circle area is the count. Nothing new from {silent}.",
           "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
           "regions": {"ch": "Federal", "de": "German-speaking", "fr": "French-speaking", "it": "Italian-speaking"},
           "foot": "{t} decisions since 1875, free to search, cite and download"},
    "it": {"h1": "Partitura di una settimana", "fed": "Federale", "nb": "non eseguito",
           "sub": "{n} decisioni, per cantone e per la notte in cui il nostro aggiornamento le ha raccolte. L\u2019area del cerchio è il numero. Nessuna novità da {silent}.",
           "days": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
           "regions": {"ch": "Confederazione", "de": "Svizzera tedesca", "fr": "Svizzera romanda", "it": "Svizzera italiana"},
           "foot": "{t} decisioni dal 1875, da consultare, citare e scaricare liberamente"},
}


def design_score(delta: dict, cur: dict, lang: str = "en") -> str:
    week, span = meta(delta, lang)
    t = SCORE[lang]
    n = lambda v: wp.fmt(v, lang)  # noqa: E731
    first = delta["from"].date() + timedelta(days=1)
    days = [first + timedelta(days=i) for i in range((delta["to"].date() - first).days + 1)]
    rows = sorted((k for k in [FED, *wp.TILES] if delta["by_canton"].get(k)), key=lambda k: (-delta["by_canton"][k], k))
    silent = sorted(k for k in wp.TILES if not delta["by_canton"].get(k))
    top_y = 366
    pitch = min(52.0, (1200 - top_y) / max(1, len(rows) - 1))
    col0, colw = 262, (W - 40 - 262) / len(days)
    vmax = max((v for d in delta["by_day"].values() for v in d.values()), default=1)
    rmax = pitch * 1.25
    staff, marks, heads = [], [], []
    for r, code in enumerate(rows):
        y = top_y + r * pitch
        total = delta["by_canton"].get(code, 0)
        staff.append(f'<path d="M{col0 - 20} {y:.1f}H{W - 40}" stroke="#111" stroke-width="{0.9 if total else 0.35}"/>')
        staff.append(f'<text x="40" y="{y + 6:.1f}" class="rl{"" if total else " z"}">{t["fed"] if code == FED else code}</text>')
        if total:
            staff.append(f'<text x="{col0 - 34}" y="{y + 6:.1f}" class="rn" text-anchor="end">{n(total)}</text>')
        for c, day in enumerate(days):
            v = delta["by_day"].get(day, {}).get(code, 0)
            if v:
                marks.append(f'<circle cx="{col0 + (c + 0.5) * colw:.1f}" cy="{y:.1f}" r="{rmax * math.sqrt(v / vmax):.1f}" '
                             f'fill="{INK[region_of(code)]}" style="mix-blend-mode:multiply"/>')
    for c, day in enumerate(days):
        x = col0 + (c + 0.5) * colw
        missing = day not in delta["by_day"]
        heads.append(f'<text x="{x:.1f}" y="{top_y - 98}" class="dh" text-anchor="middle">{t["days"][day.weekday()]} {day.day}</text>')
        tot = sum(delta["by_day"].get(day, {}).values())
        heads.append(f'<text x="{x:.1f}" y="{top_y - 76}" class="dn" text-anchor="middle">'
                     f'{t["nb"] if missing else n(tot)}</text>')
    legend = "".join(f'<span><i style="background:{INK[k]}"></i>{esc(t["regions"][k])}</span>' for k in ("ch", "de", "fr", "it"))
    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>{esc(week)} &nbsp; {esc(span)}</span></div>
<h1 style="font-size:{min(59, 59 * 20 / len(t['h1'])):.0f}px">{esc(t['h1'])}</h1>
<p class="sub">{esc(t['sub'].format(n=n(delta['added']), silent=" ".join(silent)))}</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">{''.join(heads)}{''.join(staff)}<g style="isolation:isolate">{''.join(marks)}</g></svg>
<div class="lg">{legend}</div>
<div class="ft"><b>opencaselaw.ch</b><span>{esc(t['foot'].format(t=n(cur['total'])))}</span></div>"""
    css = """
body{background:#fff;color:#111;font-family:'DM Sans','Helvetica Neue',sans-serif}
.hd{position:absolute;left:40px;right:40px;top:34px;display:flex;justify-content:space-between;font-size:20px;font-weight:500}
h1{position:absolute;left:40px;top:80px;font-family:'Syne',sans-serif;font-weight:800;line-height:1;letter-spacing:-.045em;white-space:nowrap}
.sub{position:absolute;left:40px;top:164px;width:940px;font-size:21px;line-height:1.3}
.rl{font-family:'Syne',sans-serif;font-weight:800;font-size:19px;fill:#111}.rl.z{fill:#a3a3a3;font-weight:500}
.rn{font-family:'DM Sans',sans-serif;font-size:17px;fill:#111}
.dh{font-family:'Syne',sans-serif;font-weight:700;font-size:19px;fill:#111}
.dn{font-family:'DM Sans',sans-serif;font-size:16px;fill:#6b6b6b}
.lg{position:absolute;left:40px;right:40px;top:1252px;display:flex;gap:22px;font-size:16.5px;white-space:nowrap}.lg .z{margin-left:auto;color:#6b6b6b}
.lg i{display:inline-block;width:15px;height:15px;border-radius:50%;margin-right:7px;vertical-align:-2px}
.ft{position:absolute;left:40px;right:40px;bottom:30px;display:flex;justify-content:space-between;align-items:baseline;font-size:18px}
.ft b{font-family:'Syne',sans-serif;font-weight:800;font-size:24px}"""
    return page("family=Syne:wght@500;700;800&family=DM+Sans:wght@400;500", css, body)


# ── 4. fields ─────────────────────────────────────────────────────────────────

FIELD = {"de": ("#0E4D3A", "#fff"), "fr": ("#F7B8CE", "#111"), "ch": ("#FF5B1F", "#111"), "it": ("#111", "#fff")}


FIELDS = {
    "en": {"names": {k: v[0] for k, v in REGIONS.items()}, "share": "{p} % of the week",
           "say": "{n} court decisions joined the open corpus of Swiss case law this week. Each field is as tall as its share."},
    "rm": {"names": {"de": "Svizra tudestga", "fr": "Svizra franzosa", "it": "Svizra taliana", "ch": "Confederaziun"},
           "share": "{p} % da l\u2019emna",
           "say": "{n} novas decisiuns da dretgiras en il corpus avert questa emna. Mintga champ è uschè aut sco sia part."},
}


def design_fields(delta: dict, cur: dict, lang: str = "en") -> str:
    week, span = meta(delta, lang)
    t = FIELDS[lang]
    n = lambda v: wp.fmt(v, lang)  # noqa: E731
    sums = {k: sum(delta["by_canton"].get(c, 0) for c in codes) for k, (_, codes) in REGIONS.items()}
    order = sorted((k for k in sums if sums[k]), key=lambda k: -sums[k])
    total = sum(sums.values())
    blocks, y = [], 0.0
    for i, k in enumerate(order):
        h = H * sums[k] / total
        bg, fg = FIELD[k]
        name = t["names"][k]
        if k == "ch":
            parts = sorted(((COURT_NAMES.get(c, c.split("_")[0].upper()), v) for (c, canton), v in delta["by_key"].items() if canton == FED),
                           key=lambda kv: -kv[1])
        else:
            parts = sorted(((c, delta["by_canton"].get(c, 0)) for c in REGIONS[k][1]), key=lambda kv: -kv[1])
        detail = " &ensp; ".join(f"{esc(c)} {n(v)}" for c, v in parts if v)
        head = 112 if i == 0 else 0  # the first field also carries the masthead
        if h - head >= 150:
            size = min(150, (h - head) * 0.42, 700 / (len(name) * 0.43))  # Anton is ~0.43 em per letter
            inner = (f'<div class="nm" style="font-size:{size:.0f}px;top:{head + 20}px">{esc(name)}</div>'
                     f'<div class="ct" style="font-size:{size:.0f}px;top:{head + 20}px">{n(sums[k])}</div>'
                     f'<div class="dt">{detail}<br>{esc(t["share"].format(p=round(sums[k] / total * 100)))}</div>')
        else:
            size = max(20, h * 0.62)
            inner = (f'<div class="nm" style="font-size:{size:.0f}px;top:{(h - size * 1.1) / 2:.0f}px">{esc(name)}</div>'
                     f'<div class="ct" style="font-size:{size:.0f}px;top:{(h - size * 1.1) / 2:.0f}px">{n(sums[k])}</div>')
        blocks.append(f'<section style="top:{y:.1f}px;height:{h + 0.5:.1f}px;background:{bg};color:{fg}">{inner}</section>')
        y += h
    fg0 = FIELD[order[0]][1]
    body = f"""{''.join(blocks)}
<div class="hd" style="color:{fg0}"><b>opencaselaw.ch</b><span>{esc(week)} &nbsp; {esc(span)}</span></div>
<p class="say" style="color:{fg0}">{esc(t['say'].format(n=n(delta['added'])))}</p>"""
    css = """
body{background:#111;font-family:'DM Sans','Helvetica Neue',sans-serif}
section{position:absolute;left:0;right:0;overflow:hidden}
.nm,.ct{position:absolute;font-family:'Anton','Arial Narrow',sans-serif;line-height:1.1;letter-spacing:-.005em;white-space:nowrap}
.nm{left:36px}.ct{right:36px}
.dt{position:absolute;left:38px;right:38px;bottom:24px;font-size:21px;line-height:1.4;font-weight:500}
.hd{position:absolute;left:38px;right:38px;top:30px;display:flex;justify-content:space-between;font-size:21px;font-weight:500}
.hd b{font-weight:700}
.say{position:absolute;left:38px;top:62px;width:1000px;font-size:21px;line-height:1.3}"""
    return page("family=Anton&family=DM+Sans:wght@400;500;700", css, body)


# ── 5. receipt ────────────────────────────────────────────────────────────────

def design_receipt(delta: dict, cur: dict) -> str:
    week, span = meta(delta)
    iso = delta["to"].isocalendar()
    ground = f"oklch(0.72 0.17 {week_hue(delta)})"
    items = sorted(delta["by_court"].items(), key=lambda kv: (-kv[1], kv[0]))
    room, min_lh = 690, 19.5
    keep = len(items) if room / len(items) >= min_lh else int(room / min_lh) - 1
    rest = items[keep:]
    lh = min(21.0, room / (keep + bool(rest)))
    cols = 44

    def row(label: str, value: str, fill: str = ".") -> str:
        return esc(label) + " " + fill * max(2, cols - len(label) - len(value) - 2) + " " + esc(value)

    lines = [row(c, n(v)) for c, v in items[:keep]]
    if rest:
        lines.append(row(f"{len(rest)} further courts", n(sum(v for _, v in rest))))
    code = f"{iso.year}W{iso.week:02d}{delta['added']:05d}"
    bars, x = [], 0.0
    for ch in code:  # the barcode spells the week and the total, one stripe pair per character
        w = 2 + (ord(ch) % 5) * 1.6
        bars.append(f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="54" fill="#111"/>')
        x += w + 2 + (ord(ch) % 3) * 1.8
        bars.append(f'<rect x="{x:.1f}" y="0" width="1.6" height="54" fill="#111"/>')
        x += 4.6
    teeth = ",".join(f"{100 - i * 100 / 30:.2f}% {100 if i % 2 else 98.7}%" for i in range(31))
    body = f"""
<div class="paper" style="clip-path:polygon(0 0,100% 0,{teeth})">
<div class="mast">OpenCaseLaw</div>
<pre class="c">Swiss case law, open to everyone
opencaselaw.ch

{esc(week)}   {esc(span)}
{'-' * cols}
</pre><pre>{row('COURT', 'NEW', ' ')}
{'-' * cols}</pre>
<pre style="line-height:{lh:.1f}px">{chr(10).join(lines)}</pre>
<pre>{'-' * cols}
<b>{row('TOTAL NEW DECISIONS', n(delta['added']), ' ')}</b>
{row('removed in clean-up', '-' + n(delta['removed']), ' ')}
{row('now in the corpus', n(cur['total']), ' ')}
{'-' * cols}
<b>{row('PRICE', 'CHF 0.00', ' ')}</b>
{row('licence', 'CC0, public domain', ' ')}
{'-' * cols}</pre>
<svg class="bc" width="{x:.0f}" height="54" viewBox="0 0 {x:.0f} 54">{''.join(bars)}</svg>
<pre class="c">{code}

Thank you for reading the law.</pre>
</div>"""
    css = f"""
body{{background:{ground};font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;color:#111}}
.paper{{position:absolute;left:230px;top:-30px;width:620px;height:1352px;background:#fbfaf6;padding:86px 0 0 74px;
  transform:rotate(-2.2deg);transform-origin:50% 50%;filter:drop-shadow(0 18px 26px rgba(0,0,0,.28))}}
.mast{{font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:44px;letter-spacing:-.03em;width:472px;text-align:center;margin-bottom:6px}}
pre{{font-family:inherit;font-size:17.5px;line-height:21px;white-space:pre}}
pre.c{{text-align:center;width:472px}}
pre b{{font-weight:700}}
.bc{{position:static;display:block;margin:14px 0 8px;width:472px}}"""
    return page("family=IBM+Plex+Mono:wght@400;500;700", css, body)


# One design per language of the site, same week, same numbers.
EDITION = {
    "de": ("topo", design_topo),  # the Landeskarte
    "fr": ("sediment", design_sediment),
    "it": ("score", design_score),  # la partitura
    "rm": ("fields", design_fields),
    "en": ("receipt", lambda delta, cur, lang: design_receipt(delta, cur)),
}
CAPTION = {
    "de": "Woche {w} in der Schweizer Rechtsprechung, als Karte gezeichnet: {n} Entscheide sind zwischen dem {a} und dem {b} neu bei OpenCaseLaw dazugekommen. Jeder Gipfel ist ein Gerichtssitz, seine Höhe die Zahl der neuen Entscheide.\n\nDer Korpus umfasst jetzt {t} Entscheide zurück bis 1875, frei durchsuchbar, zitierbar und herunterladbar: opencaselaw.ch",
    "fr": "La semaine {w} de la jurisprudence suisse: {n} décisions ont rejoint OpenCaseLaw entre le {a} et le {b}. Elles s\u2019ajoutent à 151 ans de jurisprudence, une ligne par année.\n\nLe corpus compte désormais {t} décisions depuis 1875, libres à consulter, citer et télécharger: opencaselaw.ch",
    "it": "La settimana {w} della giurisprudenza svizzera, scritta come una partitura: {n} decisioni sono entrate in OpenCaseLaw tra il {a} e il {b}, cantone per cantone e notte per notte.\n\nIl corpus conta ora {t} decisioni dal 1875, da consultare, citare e scaricare liberamente: opencaselaw.ch",
    "rm": "Emna {w}: {n} novas decisiuns da dretgiras en il corpus avert dad OpenCaseLaw, dals {a} fin ils {b}.\n\nIl corpus cumpiglia ussa {t} decisiuns dapi il 1875: opencaselaw.ch",
    "en": "Week {w} in Swiss case law, itemised: {n} decisions entered OpenCaseLaw between {a} and {b}. Price: CHF 0.00.\n\nThe corpus now holds {t} decisions back to 1875, free to search, cite and download: opencaselaw.ch",
}


ARCHIVE = wp.REPO / "docs" / "posters"  # served at opencaselaw.ch/posters/
SCHEMA = "opencaselaw.posters.v1"


def update_manifest(manifest: dict, week: dict) -> dict:
    """The manifest with `week` added, or replacing the entry of the same ISO week; newest first."""
    weeks = [w for w in manifest.get("weeks", []) if w["week"] != week["week"]] + [week]
    return {"schema": SCHEMA, "weeks": sorted(weeks, key=lambda w: w["week"], reverse=True)}


def archive_week(delta: dict, rendered: dict[str, tuple[str, Path, str]], archive: Path = ARCHIVE) -> dict:
    """Copy this week's posters into the site archive as WebP and record them in index.json.

    `rendered` maps language -> (design, png path, caption). The same bytes go to latest/<lang>.webp,
    a stable address for the README; identical files cost git nothing.
    """
    import json

    from PIL import Image

    iso = delta["to"].isocalendar()
    slug = f"{iso.year}-w{iso.week:02d}"
    (archive / slug).mkdir(parents=True, exist_ok=True)
    (archive / "latest").mkdir(exist_ok=True)
    posters = []
    for lang, (design, png, caption) in rendered.items():
        rel = f"{slug}/{lang}-{design}.webp"
        full = Image.open(png).convert("RGB")
        full.save(archive / rel, "WEBP", quality=84, method=6)
        (archive / "latest" / f"{lang}.webp").write_bytes((archive / rel).read_bytes())
        # small copy for the homepage hero, which shows all five at once
        full.resize((360, 450), Image.LANCZOS).save(archive / "latest" / f"{lang}-s.webp", "WEBP", quality=80, method=6)
        posters.append({"lang": lang, "design": design, "file": rel, "caption": caption})
    week = {
        "week": f"{iso.year}-W{iso.week:02d}", "from": delta["from"].date().isoformat(),
        "to": delta["to"].date().isoformat(), "added": delta["added"], "posters": posters,
    }
    index = archive / "index.json"
    manifest = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
    manifest = update_manifest(manifest, week)
    index.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return week


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("langs", nargs="*", choices=[*EDITION, []], help="languages to render (default: all five)")
    ap.add_argument("--end", type=date.fromisoformat, help="last day of the window (default: newest snapshot)")
    ap.add_argument("--ref", default="origin/main", help="git ref carrying the nightly stats.json commits")
    ap.add_argument("--archive", action="store_true",
                    help="also publish into docs/posters/ (WebP + index.json); refuses to run on fallback fonts")
    args = ap.parse_args(argv)
    if args.archive and args.langs and set(args.langs) != set(EDITION):
        ap.error("--archive publishes a whole week: render all five languages")

    delta, cur = wp.load_week(args.ref, args.end)
    iso = delta["to"].isocalendar()
    out = wp.REPO / "output" / "posters"
    out.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for lang in args.langs or list(EDITION):
        name, fn = EDITION[lang]
        doc = fn(delta, cur, lang)
        path = out / f"opencaselaw-{iso.year}-w{iso.week:02d}-{lang}-{name}"
        path.with_suffix(".html").write_text(doc, encoding="utf-8")
        caption = CAPTION[lang].format(
            w=iso.week, n=wp.fmt(delta["added"], lang), a=wp.fmt_day(delta["from"], lang),
            b=wp.fmt_day(delta["to"], lang), t=wp.fmt(cur["total"], lang)).replace("..", ".")  # "1.10." + full stop
        path.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
        wp.render_png(doc, path.with_suffix(".png"), require_fonts=args.archive)
        rendered[lang] = (name, path.with_suffix(".png"), caption)
        print(f"wrote {path}.png")
    if args.archive:
        week = archive_week(delta, rendered)
        print(f"archived {week['week']} ({week['added']} decisions) in {ARCHIVE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
