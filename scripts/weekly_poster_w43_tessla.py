"""Week 43 (16-22 October 2026), Rumantsch: the week cut like a tally stick (tessla).

After Herbert Matter's photomontage posters for the Swiss tourist office (1934-36): a
photograph pushed to an extreme close-up and set at an angle, one flat Swiss red, heavy white
type. The photograph is a real Alpine tally stick, a double tessel from Alp Blümatt (Turtmann
VS), 1893 (Sandstein, Wikimedia Commons, CC BY 3.0), its carved notches and burned house mark
filling the sheet. Over it the week is cut in white the same way, exactly: a cross for every
hundred, a long notch for every ten, a short notch for every one (7 + 7 + 1 = 771). On the red
band below, each canton with its coat of arms (public domain) and its own cuts. Nearly wordless.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import base64
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import page

ASSETS = Path(__file__).with_name("weekly_poster_w43_assets")
RED, WHITE = "#d52b1e", "#ffffff"


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def cuts(n: int) -> list[str]:
    h, rest = divmod(int(n), 100)
    t, o = divmod(rest, 10)
    return ["x"] * h + ["t"] * t + ["o"] * o


def cut_paths(n: int, x: float, y: float, h: float, unit: float) -> tuple[list[str], float]:
    """Path data for the cuts of n at (x, y), h tall, and the x after the last cut."""
    out, prev = [], None
    for c in cuts(n):
        if prev and c != prev:
            x += unit * .8
        if c == "x":
            w = unit * 1.3
            out.append(f'<path d="M{x} {y} L{x + w} {y + h} M{x + w} {y} L{x} {y + h}"/>')
            x += w + unit * .7
        elif c == "t":
            out.append(f'<path d="M{x} {y} L{x} {y + h}"/>')
            x += unit * .75
        else:
            out.append(f'<path d="M{x} {y} L{x} {y + h * .5}"/>')
            x += unit * .6
        prev = c
    return [p[len('<path d="'):-len('"/>')] for p in out], x


def tally(n: int, x: float, y: float, h: float, unit: float, stroke: float) -> tuple[str, float]:
    """White cuts, for the red band."""
    paths, end = cut_paths(n, x, y, h, unit)
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return f'<g stroke="{WHITE}" stroke-width="{stroke}" stroke-linecap="square" fill="none">{body}</g>', end


def carved(n: int, x: float, y: float, h: float, unit: float, stroke: float) -> str:
    """The same cuts as grooves in the photographed wood: a dark V with a lit edge on one side."""
    paths, _ = cut_paths(n, x, y, h, unit)
    d = " ".join(paths)
    return (f'<g fill="none" stroke-linecap="round">'
            f'<path d="{d}" stroke="#f7e6c4" stroke-width="{stroke * 1.25}" opacity=".55" transform="translate(2.5 2)"/>'
            f'<path d="{d}" stroke="#1c0e04" stroke-width="{stroke}" opacity=".88" style="mix-blend-mode:multiply"/>'
            f'<path d="{d}" stroke="#000" stroke-width="{stroke * .35}" opacity=".5"/></g>')


def design(week: dict, lang: str = "rm") -> str:
    bc = week["by_canton"]
    a, b = week["from"], week["to"]
    photo = b64(ASSETS / "tessla.jpg")
    arms = {p.stem: b64(p) for p in (ASSETS / "wappen").glob("*.svg")}

    big = carved(week["added"], 70, 456, 180, 44, 15)

    order = sorted(bc, key=lambda k: (-bc[k], k))
    cols, cw, rh, x0, y0 = 4, 240, 84, 60, 846
    cells = []
    for i, k in enumerate(order):
        r, c = divmod(i, cols)
        x, y = x0 + c * cw, y0 + r * rh
        mark = arms.get("CH" if k == wp.FEDERAL else k)
        cells.append(f'<image href="data:image/svg+xml;base64,{mark}" x="{x}" y="{y}" width="34" height="40" preserveAspectRatio="xMidYMid meet"/>')
        svg, end = tally(bc[k], x + 48, y + 4, 32, 13, 3.2)
        cells.append(svg + f'<text x="{x + 48}" y="{y + 64}" class="cn">{wp.fmt(bc[k], "rm")}</text>')

    body = f"""
<div class="photo"></div>
<div class="band"></div>
<svg width="1080" height="1350" viewBox="0 0 1080 1350">{big}{''.join(cells)}</svg>
<div class="num">{wp.fmt(week['added'], 'rm')}</div>
<div class="wk"><b>{week['week_no']}</b><span>{a.day}.–{b.day}.{b.month}.{b.year}</span></div>
<p class="credit">Tessla, Alp Blümatt (VS), 1893 · Fotografia: Sandstein, CC BY 3.0 · Vopnas: domena publica</p>
<footer>opencaselaw.ch</footer>"""

    css = f"""
body{{background:#111;color:{WHITE};font-family:'Barlow Condensed','Arial Narrow',sans-serif}}
.photo{{position:absolute;left:-140px;top:-120px;width:1360px;height:1000px;transform:rotate(-7deg);
  background:url(data:image/jpeg;base64,{photo}) 52% 38%/178% auto no-repeat;filter:contrast(1.18) brightness(.8)}}
.band{{position:absolute;left:0;top:0;width:1080px;height:1350px;background:{RED};clip-path:polygon(0 790px,1080px 700px,1080px 1350px,0 1350px)}}
.num{{position:absolute;left:44px;top:6px;font-size:400px;font-weight:800;line-height:1;letter-spacing:-.02em;color:{WHITE};
  text-shadow:0 0 40px rgba(0,0,0,.35)}}
.wk{{position:absolute;right:60px;top:52px;text-align:right;text-shadow:0 0 24px rgba(0,0,0,.5)}}
.wk b{{display:block;font-size:150px;line-height:.85;font-weight:600}}
.wk span{{font-size:26px;letter-spacing:.06em}}
.cn{{font-size:20px;font-weight:700;fill:#ffd9d4}}
.credit{{position:absolute;left:60px;bottom:40px;font-size:13px;color:#ffd9d4;letter-spacing:.02em}}
footer{{position:absolute;right:60px;bottom:32px;font-size:30px;font-weight:800}}"""
    return page("family=Barlow+Condensed:wght@300;600;700;800", css, body, "rm")
