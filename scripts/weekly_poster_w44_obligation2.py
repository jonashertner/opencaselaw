"""Week 44 (23-29 October 2026), German: the week as a bond certificate, wholly engraved.

The second state: one ink and one technique throughout. The face value is cut in the same wave lines
as the vignette, the amount is also written out in words as on any security, the vignette thins out
into the paper, and the address is the paying agent.


An old Swiss bearer bond, the kind with a sheet of coupons to cut off: an "Obligation", which is
also the word the Obligationenrecht is named for. The face value is the week's new decisions. The
sheet below holds one coupon for the Confederation and one for each canton, in the constitution's
order, with this week's number on it; a canton with nothing has a blank coupon. It is a fictional
security: no issuer, no bank, no currency. Bearer bonds are payable to whoever holds them, and so
is this one.

The vignette is the Federal Supreme Court at Mon Repos, Lausanne, engraved from a photograph
released under CC0 by its author (Wikimedia Commons, "Tribunal Fédéral à Lausanne depuis le Parc de
Mon-Repos, Lausanne, avril 2019") with scripts/weekly_poster_w44_engrave.py.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image

import weekly_poster as wp
from weekly_poster_designs import esc, page

ASSET = Path(__file__).with_name("weekly_poster_w44_assets") / "court_engraved_oval.png"
INK, PAPER = "#16352c", "#ebe4d0"
ORDER = ["CH", "ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS", "BL", "SH", "AR", "AI",
         "SG", "GR", "AG", "TG", "TI", "VD", "VS", "NE", "GE", "JU"]
MONTH_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
            "November", "Dezember"]
M = 38           # frame inset
COLS, ROWS = 9, 3
SHEET_TOP, SHEET_H = 952, 350


def band(x0: float, y0: float, x1: float, y1: float, waves: int, amp: float) -> str:
    """Two interlaced sine lines along one side of the frame: the guilloche."""
    n = waves * 12
    ln = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
    paths = []
    for phase in (0, math.pi):
        pts = []
        for i in range(n + 1):
            t = i / n
            off = amp * math.sin(t * waves * 2 * math.pi + phase)
            pts.append(f"{x0 + ux * ln * t - uy * off:.1f},{y0 + uy * ln * t + ux * off:.1f}")
        paths.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{INK}" stroke-width=".8"/>')
    return "".join(paths)


def inked() -> str:
    """The engraving is a greyscale map of where the ink goes; print it in the poster's ink."""
    alpha = Image.open(ASSET).convert("L")
    plate = Image.new("RGBA", alpha.size, INK)
    plate.putalpha(alpha)
    buf = io.BytesIO()
    plate.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def design(week: dict, lang: str = "de") -> str:
    a, b = week["from"], week["to"]
    bc = week["by_canton"]
    img = inked()

    frame = (band(M, M, 1080 - M, M, 92, 4) + band(M, 1350 - M, 1080 - M, 1350 - M, 92, 4)
             + band(M, M, M, 1350 - M, 116, 4) + band(1080 - M, M, 1080 - M, 1350 - M, 116, 4))

    x0, x1 = M + 14, 1080 - M - 14
    cw = (x1 - x0) / COLS
    ch = SHEET_H / ROWS
    cells = []
    for i, code in enumerate(ORDER):
        c, r = i % COLS, i // COLS
        x, y = x0 + c * cw, SHEET_TOP + r * ch
        n = bc.get(code, 0)
        label = "Bund" if code == "CH" else code
        num = f'<text x="{x + cw / 2:.1f}" y="{y + ch * .72:.1f}" text-anchor="middle" class="cn">{wp.fmt(n, "de")}</text>' if n else ""
        cells.append(f'<text x="{x + 10:.1f}" y="{y + 22:.1f}" class="cl">{label}</text>{num}')
    rules = []
    for c in range(1, COLS):
        rules.append(f'<line x1="{x0 + c * cw:.1f}" y1="{SHEET_TOP}" x2="{x0 + c * cw:.1f}" y2="{SHEET_TOP + SHEET_H}"/>')
    for r in range(0, ROWS + 1):
        rules.append(f'<line x1="{x0}" y1="{SHEET_TOP + r * ch:.1f}" x2="{x1}" y2="{SHEET_TOP + r * ch:.1f}"/>')

    body = f"""
<div class="vig" style="background-image:url(data:image/png;base64,{img})"></div>
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
  {frame}
  <g stroke="{INK}" stroke-width="1.1" stroke-dasharray="1.5 5" stroke-linecap="round">{''.join(rules)}</g>
  {''.join(cells)}
  <defs><pattern id="w" width="26" height="7.2" patternUnits="userSpaceOnUse"><path d="M0 2.2 C6.5 .4 6.5 .4 13 2.2 S19.5 4 26 2.2 V5.4 C19.5 7.2 19.5 7.2 13 5.4 S6.5 3.6 0 5.4Z" fill="{INK}"/></pattern></defs>
  <text x="540" y="874" text-anchor="middle" class="fv" fill="url(#w)" stroke="{INK}" stroke-width="1.4">{esc(wp.fmt(week["added"], "de"))}</text>
</svg>
<div class="head">Obligation</div>
<div class="sub">Schweizer Rechtsprechung, Woche {week["week_no"]}, {a.day}.–{b.day}. {MONTH_DE[b.month - 1]} {b.year}</div>
<div class="words">Eintausendeinhundertneununddreissig neue Entscheide</div>
<div class="line">Zahlbar an jedermann, kostenlos. Zahlstelle: opencaselaw.ch</div>
"""
    css = f"""
body{{background:{PAPER};color:{INK};font-family:'Libre Caslon Text',serif}}
.vig{{position:absolute;left:{M + 14}px;top:190px;width:{1080 - 2 * M - 28}px;height:572px;background-size:cover;background-position:center}}
.head{{position:absolute;left:0;right:0;top:50px;text-align:center;font:400 96px/1 'UnifrakturCook',serif}}
.sub{{position:absolute;left:0;right:0;top:160px;text-align:center;font:400 19px/1 'Libre Caslon Text',serif;letter-spacing:.02em}}
.fv{{font:700 196px 'Bodoni Moda',serif;letter-spacing:-.01em;font-variant-numeric:lining-nums;font-variation-settings:'opsz' 28}}
.words{{position:absolute;left:0;right:0;top:884px;text-align:center;font:italic 400 25px/1 'Libre Caslon Text',serif}}
.line{{position:absolute;left:0;right:0;top:917px;text-align:center;font:400 16px/1 'Libre Caslon Text',serif}}
.line b{{font-weight:700}}
.cl{{font:700 15px 'Libre Caslon Text',serif;fill:{INK}}}
.cn{{font:700 44px 'Bodoni Moda',serif;fill:{INK};font-variant-numeric:lining-nums;font-variation-settings:'opsz' 6}}
"""
    fonts = "family=Bodoni+Moda:opsz,wght@6..96,700&family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400&family=UnifrakturCook:wght@700"
    return page(fonts, css, body, lang)
