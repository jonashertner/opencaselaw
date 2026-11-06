"""Week 45 (30 October - 5 November 2026), Italian: the week built as a Botta drum.

Mario Botta's buildings in Ticino (Mogno, Stabio, Monte Tamaro) are drums of stone laid in bands,
pale marble alternating with grey granite. Here the drum is built of the week: one stone per new
decision, 905 visible stones, and one band of courses per canton, the largest at the base. A band
holds about eighteen stones per course across the visible half of the drum, so its height is its
count. A vertical cut runs up the middle of the drum, as at Botta's Casa Rotonda in Stabio, and
the courses split around it; a canton with a single decision is a single long stone, so the one
or two of them at the top close the cut as lintels. Ticino's band is laid in brick. Only the
building type is borrowed, no drawing of a real building.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import math

import weekly_poster as wp
from weekly_poster_designs import esc, page

CX, R = 470.0, 330.0           # drum axis and radius, px
BASE = 1150.0                  # foot of the drum
COURSE = 15.4                  # course height
PER = 18                       # stones per course across the visible half
JOINT = 1.6
SLOT = 26.0                    # the cut up the middle
LIGHT = math.radians(-40)      # light from the front left
PAPER, INK = "#efebe3", "#1d1c1a"
MARBLE, GRANITE, BRICK = (236, 232, 222), (122, 124, 126), (176, 82, 52)
MONTH_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
            "novembre", "dicembre"]
NAME_IT = {"CH": "Tribunali federali", "ZH": "Zurigo", "BE": "Berna", "LU": "Lucerna", "UR": "Uri", "SZ": "Svitto",
           "OW": "Obvaldo", "NW": "Nidvaldo", "GL": "Glarona", "ZG": "Zugo", "FR": "Friburgo", "SO": "Soletta",
           "BS": "Basilea Città", "BL": "Basilea Campagna", "SH": "Sciaffusa", "AR": "Appenzello Esterno",
           "AI": "Appenzello Interno", "SG": "San Gallo", "GR": "Grigioni", "AG": "Argovia", "TG": "Turgovia",
           "TI": "Ticino", "VD": "Vaud", "VS": "Vallese", "NE": "Neuchâtel", "GE": "Ginevra", "JU": "Giura"}


def shade(rgb: tuple, theta: float) -> str:
    lit = 0.6 + 0.4 * max(0.0, math.cos(theta - LIGHT)) - 0.1 * (1 - math.cos(theta))
    return "#" + "".join(f"{min(255, round(c * lit)):02x}" for c in rgb)


def x_of(theta: float, side: int) -> float:
    """Where a point of the drum's face lands; side -1/+1 squeezes it to the left/right of the cut,
    side 0 (a lintel over the cut) leaves it."""
    x = R * math.sin(theta)
    if side:
        x = side * SLOT / 2 + x * (R - SLOT / 2) / R
    return CX + x


def runs(m: int) -> list[tuple[int, float, float]]:
    """The stones of a course of m: (side, from, to) in angle, split around the cut."""
    if m == 1:
        return [(0, -math.pi / 2, math.pi / 2)]
    left, right = (m + 1) // 2, m // 2
    out = [(-1, -math.pi / 2 + math.pi / 2 * s / left, -math.pi / 2 + math.pi / 2 * (s + 1) / left) for s in range(left)]
    out += [(1, math.pi / 2 * s / right, math.pi / 2 * (s + 1) / right) for s in range(right)]
    return out


def design(week: dict, lang: str = "it") -> str:
    a, b = week["from"], week["to"]
    bands = sorted(((n, k) for k, n in week["by_canton"].items() if n), key=lambda t: (-t[0], t[1]))
    stones, labels, y, total = [], [], BASE, 0
    slot_top = BASE
    for i, (n, k) in enumerate(bands):
        courses = max(1, round(n / PER))
        rgb = BRICK if k == "TI" else (MARBLE if i % 2 == 0 else GRANITE)
        top = y - courses * COURSE
        for c in range(courses):
            m = (c + 1) * n // courses - c * n // courses   # stones in this course, the odd ones spread
            y1 = y - c * COURSE
            if m > 1:
                slot_top = y1 - COURSE
            for side, t0, t1 in runs(m):
                x0, x1 = x_of(t0, side), x_of(t1, side)
                # a joint of constant width on the face, thinner where the stone turns away
                j = JOINT * 0.5
                if x1 - x0 <= 2 * j + .4:
                    j = max(0.0, (x1 - x0 - .4) / 2)
                stones.append(f'<rect x="{x0 + j:.2f}" y="{y1 - COURSE + JOINT / 2:.2f}" width="{x1 - x0 - 2 * j:.2f}" '
                              f'height="{COURSE - JOINT:.2f}" fill="{shade(rgb, (t0 + t1) / 2)}"/>')
                total += 1
        mid = (y + top) / 2
        labels.append((mid, k, n))
        y = top
    assert total == week["added"]
    crown = y

    # labels on the right, pushed apart where bands are thin
    gap, placed = 17.0, []
    for mid, k, n in labels:                # from the base upwards
        ly = min(mid, placed[-1][0] - gap) if placed else mid
        placed.append((ly, mid, k, n))
    tags, lines = [], []
    lx = CX + R + 34
    for ly, mid, k, n in placed:
        lines.append(f'<polyline points="{CX + R + 6:.1f},{mid:.1f} {lx - 10:.1f},{mid:.1f} {lx - 4:.1f},{ly:.1f}" />')
        tags.append(f'<text x="{lx:.1f}" y="{ly + 4.5:.1f}">{esc(NAME_IT[k])} <tspan class="v">{esc(wp.fmt(n, "it"))}</tspan></text>')

    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
  <defs>
    <linearGradient id="sh" x1="0" x2="1">
      <stop offset="0" stop-color="#000" stop-opacity=".0"/><stop offset=".72" stop-color="#000" stop-opacity=".0"/>
      <stop offset="1" stop-color="#000" stop-opacity=".14"/>
    </linearGradient>
    <linearGradient id="cut" x1="0" x2="1">
      <stop offset="0" stop-color="#121211"/><stop offset=".55" stop-color="#2b2a27"/>
      <stop offset=".8" stop-color="#4a4843"/><stop offset="1" stop-color="#1c1b19"/>
    </linearGradient>
  </defs>
  <ellipse cx="{CX}" cy="{BASE + 4}" rx="{R + 40}" ry="16" fill="#000" opacity=".08"/>
  <rect x="{CX - R}" y="{crown}" width="{2 * R}" height="{BASE - crown}" fill="#3a3936"/>
  {''.join(stones)}
  <rect x="{CX - R}" y="{crown}" width="{2 * R}" height="{BASE - crown}" fill="url(#sh)"/>
  <rect x="{CX - SLOT / 2}" y="{slot_top}" width="{SLOT}" height="{BASE - slot_top}" fill="url(#cut)"/>
  <rect x="{CX - R - 6}" y="{crown - 12}" width="{2 * R + 12}" height="12" fill="#d9d4c9"/>
  <rect x="{CX - R - 6}" y="{crown - 1}" width="{2 * R + 12}" height="2" fill="#000" opacity=".25"/>
  <line x1="40" y1="{BASE}" x2="1040" y2="{BASE}" stroke="{INK}" stroke-width="2"/>
  <g fill="none" stroke="{INK}" stroke-width="1" opacity=".55">{''.join(lines)}</g>
  <g class="tg">{''.join(tags)}</g>
</svg>
<div class="n">{esc(wp.fmt(week["added"], "it"))}</div>
<div class="t">nuove decisioni</div>
<div class="d">Settimana {week["week_no"]}<br>{a.day} {MONTH_IT[a.month - 1]} – {b.day} {MONTH_IT[b.month - 1]} {b.year}</div>
<div class="k">Una pietra per decisione,<br>una fascia per cantone.</div>
<div class="u">opencaselaw.ch</div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Barlow',sans-serif}}
svg{{position:absolute;left:0;top:0}}
.tg text{{font:500 14px 'Barlow',sans-serif;fill:{INK}}}
.tg .v{{font-weight:700}}
.n{{position:absolute;left:52px;top:46px;font:700 176px/1 'Barlow Condensed',sans-serif;letter-spacing:-.01em}}
.t{{position:absolute;left:340px;top:76px;font:500 46px/1 'Barlow Condensed',sans-serif}}
.d{{position:absolute;left:342px;top:136px;font:400 25px/1.3 'Barlow',sans-serif}}
.k{{position:absolute;left:58px;top:{BASE + 26:.0f}px;font:400 23px/1.35 'Barlow',sans-serif}}
.u{{position:absolute;right:52px;top:{BASE + 26:.0f}px;font:600 23px/1.35 'Barlow',sans-serif}}
"""
    return page("family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;700", css, body, lang)
