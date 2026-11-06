"""Week 45 (30 October - 5 November 2026), Rumantsch: the week as a cross-stitch sampler, second state.

The first state's stag (weekly_poster_w45_kreuzstich.py) read as a dragon at poster size. This one
takes Johann Sibmacher's eagle from the same pattern book, "Schön Neues Modelbuch" (Nürnberg 1597),
read cell by cell from the Bibliothèque nationale de France copy
(scripts/weekly_poster_w45_assets/sibmacher_eagle.txt). The print fills some cells solid and some
with a pattern; here the solid ones are worked in red and the patterned ones in a dark thread.
Sibmacher's book is from Nürnberg; no line from it to Grisons embroidery is claimed or checked. Every red cross is one decision: the eagle's red, the week's figure
stitched above it and the ground line below it make 905 together. Almost no words, as for every
Rumantsch edition.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import random
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import esc, page

EAGLE = Path(__file__).with_name("weekly_poster_w45_assets") / "sibmacher_eagle.txt"
CELL = 14.6
LINEN, THREAD, THREAD_HI, INK = "#f3eee2", "#a8222b", "#c63a3f", "#2a2522"
DARK, DARK_HI = "#1f1b19", "#38322e"
DIGITS = {  # a sampler alphabet, seven by nine
    "0": [".#####.", "##...##", "##...##", "##...##", "##...##", "##...##", "##...##", "##...##", ".#####."],
    "1": ["...##..", "..###..", ".####..", "...##..", "...##..", "...##..", "...##..", "...##..", ".######"],
    "2": [".#####.", "##...##", ".....##", "....##.", "...##..", "..##...", ".##....", "##.....", "#######"],
    "3": [".#####.", "##...##", ".....##", ".....##", "..####.", ".....##", ".....##", "##...##", ".#####."],
    "4": ["....##.", "...###.", "..####.", ".##.##.", "##..##.", "#######", "....##.", "....##.", "....##."],
    "5": ["#######", "##.....", "##.....", "######.", ".....##", ".....##", ".....##", "##...##", ".#####."],
    "6": [".#####.", "##...##", "##.....", "######.", "##...##", "##...##", "##...##", "##...##", ".#####."],
    "7": ["#######", ".....##", "....##.", "....##.", "...##..", "...##..", "..##...", "..##...", "..##..."],
    "8": [".#####.", "##...##", "##...##", "##...##", ".#####.", "##...##", "##...##", "##...##", ".#####."],
    "9": [".#####.", "##...##", "##...##", "##...##", ".######", ".....##", ".....##", "##...##", ".#####."],
}
MONTH_RM = ["schaner", "favrer", "mars", "avrigl", "matg", "zercladur", "fanadur", "avust", "settember",
            "october", "november", "december"]


def pattern(text: str) -> list[str]:
    rows = ["" for _ in range(9)]
    for i, ch in enumerate(text):
        for r in range(9):
            rows[r] += ("." * 2 if i else "") + DIGITS[ch][r]
    return rows


def ground(n: int) -> list[str]:
    """The red stitches left over after eagle and numerals: one ground line under the eagle, as the
    bands of the pattern book have."""
    if n < 0:
        raise SystemExit(f"eagle and numerals already take more than the week: {n}")
    return ["#" * n] if n else []


def stitches(rows: list[str], x0: float, y0: float, rng: random.Random, mark: str = "#") -> list[str]:
    out = []
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch != mark:
                continue
            x, y = x0 + c * CELL, y0 + r * CELL
            i, j = CELL * .14, lambda: rng.uniform(-.5, .5)
            out.append(f'<path class="u" d="M{x + i + j():.1f},{y + CELL - i + j():.1f}L{x + CELL - i + j():.1f},{y + i + j():.1f}"/>'
                       f'<path class="o" d="M{x + i + j():.1f},{y + i + j():.1f}L{x + CELL - i + j():.1f},{y + CELL - i + j():.1f}"/>')
    return out


def count(rows: list[str], mark: str = "#") -> int:
    return sum(line.count(mark) for line in rows)


def design(week: dict, lang: str = "rm") -> str:
    a, b = week["from"], week["to"]
    eagle = [ln for ln in EAGLE.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith(";")]
    figure = pattern(str(week["added"]))
    line = ground(week["added"] - count(eagle, "X") - count(figure))
    assert count(eagle, "X") + count(figure) + count(line) == week["added"]

    rng = random.Random(45)
    snap = lambda w: round((1080 - w) / 2 / CELL) * CELL  # noqa: E731  (centre on the weave)
    top = 9 * CELL
    red = stitches(figure, snap(len(figure[0]) * CELL), top, rng)
    ey = top + (9 + 3) * CELL
    ex = snap(len(eagle[0]) * CELL)
    dark = stitches(eagle, ex, ey, rng, "o")
    red += stitches(eagle, ex, ey, rng, "X")
    ly = ey + (len(eagle) + 1) * CELL
    if line:
        red += stitches(line, snap(len(line[0]) * CELL), ly, rng)
    foot = ly + CELL

    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
  <defs>
    <pattern id="weave" width="{CELL}" height="{CELL}" patternUnits="userSpaceOnUse">
      <path d="M0,0H{CELL}M0,0V{CELL}" stroke="#d9d0bd" stroke-width=".6"/>
      <circle cx="0" cy="0" r="1" fill="#cfc5b0"/>
    </pattern>
  </defs>
  <rect width="1080" height="1350" fill="{LINEN}"/>
  <rect x="0" y="0" width="1080" height="1350" fill="url(#weave)" opacity=".75"/>
  <g stroke-linecap="round" class="dk">{''.join(dark)}</g>
  <g stroke-linecap="round">{''.join(red)}</g>
</svg>
<div class="k" style="top:{foot + 46:.0f}px">Mintga crusch cotschna è ina decisiun.</div>
<div class="d" style="top:{foot + 46:.0f}px">Emna {week["week_no"]} · {a.day} {MONTH_RM[a.month - 1]} – {b.day} {MONTH_RM[b.month - 1]} {b.year}<br>opencaselaw.ch</div>
<div class="s">Evla suenter Johann Sibmacher, 1597</div>
"""
    css = f"""
body{{background:{LINEN};color:{INK};font-family:'Cormorant Garamond',serif}}
svg{{position:absolute;left:0;top:0}}
.u{{stroke:{THREAD};stroke-width:3.6}}
.o{{stroke:{THREAD_HI};stroke-width:3.6}}
.dk .u{{stroke:{DARK}}}
.dk .o{{stroke:{DARK_HI}}}
.k{{position:absolute;left:64px;font:italic 500 27px/1.3 'Cormorant Garamond',serif}}
.d{{position:absolute;right:64px;text-align:right;font:500 22px/1.4 'Cormorant Garamond',serif;letter-spacing:.02em}}
.s{{position:absolute;left:64px;bottom:44px;font:italic 400 17px/1 'Cormorant Garamond',serif;color:#7a6f63}}
"""
    return page("family=Cormorant+Garamond:ital,wght@0,400;0,500;1,400;1,500", css, body, lang)
