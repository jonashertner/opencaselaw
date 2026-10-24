"""Week 43 (16-22 October 2026), English: every decision a figure.

After the Isotype picture statistics of Otto Neurath and Gerd Arntz (Vienna, 1930), taken to
its limit: one figure for one decision. Last week's 806 set the field; this week fills 771 of
it, and the 35 left empty are the change, drawn as outlines. Colour is the court: federal
courts red (the 13 new leading decisions black), then the cantonal courts by language region:
French-speaking green, German-speaking blue, Italian-speaking ochre. Rows of thirty, counted
in tens, so any group can be counted by eye.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import weekly_poster as wp
from weekly_poster_designs import page

INK, RED, GREEN, BLUE, OCHRE, GHOST = "#161616", "#d2302a", "#2e8b57", "#2457a6", "#d9a21b", "#bdbdbd"
DOC = ('<symbol id="doc" viewBox="0 0 20 26"><path d="M0 0H13L20 7V26H0Z"/>'
       '<path d="M13 0V7H20" fill="#fff" fill-opacity=".6"/></symbol>'
       '<symbol id="ghost" viewBox="0 0 20 26"><path d="M1 1H12.6L19 7.4V25H1Z" fill="none" stroke-width="1.6"/></symbol>')
PER_ROW, PITCH, GAP, ROW_H, W, H = 30, 27, 10, 28.6, 18.5, 24
X0, Y0 = 60, 322


def f(n: int) -> str:
    return wp.fmt(int(n), "en")


def design(week: dict, lang: str = "en") -> str:
    bc, bk = week["by_canton"], week["by_court"]
    added, prev = week["added"], week["prev_total"]
    a, b = week["from"], week["to"]

    bge = bk.get("bge@VD", 0) + bk.get("bge@LU", 0)
    fed = bc.get(wp.FEDERAL, 0)
    fr = sum(v for k, v in bc.items() if k in wp.FRENCH)
    it = sum(v for k, v in bc.items() if k in wp.ITALIAN)
    de = added - fed - fr - it
    groups = [("Federal", fed - bge, RED, "doc", True), ("of them leading decisions", bge, INK, "doc", False),
              ("French-speaking cantons", fr, GREEN, "doc", True), ("German-speaking cantons", de, BLUE, "doc", True),
              ("Italian-speaking canton", it, OCHRE, "doc", True), (f"{f(prev)} last week", max(prev - added, 0), GHOST, "ghost", False)]

    marks, labels, i, ghost_at = [], [], 0, None
    for name, n, col, sym, new_row in groups:
        if new_row and i % PER_ROW:
            i += PER_ROW - i % PER_ROW
        start = i
        for _ in range(n):
            r, c = divmod(i, PER_ROW)
            x = X0 + c * PITCH + (c // 10) * GAP
            y = Y0 + r * ROW_H
            attr = f'fill="{col}"' if sym == "doc" else f'stroke="{col}"'
            marks.append(f'<use href="#{sym}" x="{x}" y="{y}" width="{W}" height="{H}" {attr}/>')
            i += 1
        if n and sym == "ghost":
            r, c = divmod(i - 1, PER_ROW)
            ghost_at = (X0 + c * PITCH + (c // 10) * GAP + W + 14, Y0 + r * ROW_H + 21, n)
        elif n:
            labels.append((start // PER_ROW, name, fed if name == "Federal" else n, col, sym))

    lx = X0 + PER_ROW * PITCH + 2 * GAP + 14
    lab_svg, last_row = [], -2
    for row, name, n, col, sym in labels:
        row = max(row, last_row + 2)
        y = Y0 + row * ROW_H
        last_row = row
        lab_svg.append(f'<text x="{lx}" y="{y + 20}" class="ln" style="fill:{col}">{f(n)}</text>'
                       f'<text x="{lx}" y="{y + 40}" class="lt">{name}</text>')

    rows = -(-i // PER_ROW)
    body = f"""
<header><b>The Swiss Case-Law Index</b><span>Week {week['week_no']} · {a.day}–{b.day} {b.strftime('%B %Y')}</span></header>
<div class="big">{f(added)}</div>
<p class="lede">new decisions this week.<br><b>One figure each.</b></p>
<svg width="1080" height="1350" viewBox="0 0 1080 1350"><defs>{DOC}</defs>
{''.join(marks)}{''.join(lab_svg)}

</svg>
<p class="note" style="top:{Y0 + rows * ROW_H + 14}px"><b>Outlines: {f(prev - added)} fewer than week 42 ({f(prev)}).</b> Rows of thirty, in tens.
Federal: Supreme Court {f(bk.get('bger@VD', 0) + bk.get('bger@LU', 0))} and {f(bge)} leading decisions, Administrative Court {f(bk.get('bvger', 0))}, Criminal Court {f(bk.get('bstger', 0))}, federal authorities {f(fed - bge - sum(bk.get(k, 0) for k in ('bger@VD', 'bger@LU', 'bvger', 'bstger')))}. Cantons by majority language.</p>
<footer><b>opencaselaw.ch</b><span>Swiss case law, free to search and cite</span></footer>"""

    css = f"""
body{{background:#fff;color:{INK};font-family:'Jost','Futura',sans-serif}}
header{{position:absolute;left:60px;right:60px;top:40px;display:flex;justify-content:space-between;align-items:baseline;
  border-bottom:3px solid {INK};padding-bottom:9px}}
header b{{font-size:30px;font-weight:700}}header span{{font-size:18px;font-weight:500}}
.big{{position:absolute;left:52px;top:88px;font-size:228px;font-weight:700;line-height:1;letter-spacing:-.035em}}
.lede{{position:absolute;left:520px;top:186px;font-size:34px;line-height:1.15;font-weight:500}}
.lede b{{font-weight:700}}
.gl{{font-size:17px;font-weight:600;fill:#777}}
.ln{{font-size:30px;font-weight:700}}.lt{{font-size:13.5px;font-weight:500;fill:#444}}
.note{{position:absolute;left:60px;right:60px;font-size:14.5px;line-height:1.4;color:#555}}
.note b{{color:{INK};font-weight:600}}
footer{{position:absolute;left:60px;right:60px;bottom:38px;display:flex;justify-content:space-between;align-items:baseline;
  border-top:3px solid {INK};padding-top:10px;font-size:18px}}
footer b{{font-size:30px;font-weight:700}}"""
    return page("family=Jost:wght@400;500;600;700", css, body, "en")
