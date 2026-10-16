"""Week 42 (9-15 October 2026), Italian edition: *Foglio di francobolli*.

A sheet of postage stamps. One stamp per canton that published new decisions this week and
one per federal court; the face value of each stamp is the week's count. Ticino, with more
than four times its usual week, gets the large special issue. The sheet is ordered by face
value, largest first; the ink says the level (red = cantonal courts, blue = federal).

    design(week, lang) -> complete 1080x1350 HTML page
"""
from __future__ import annotations

import html

import weekly_poster as wp
from weekly_poster_designs import page

W, H = wp.W, wp.H

CANTON_IT = {
    "AG": "Argovia", "AI": "Appenzello Interno", "AR": "Appenzello Esterno", "BE": "Berna",
    "BL": "Basilea Campagna", "BS": "Basilea Città", "FR": "Friburgo", "GE": "Ginevra", "GL": "Glarona",
    "GR": "Grigioni", "JU": "Giura", "LU": "Lucerna", "NE": "Neuchâtel", "NW": "Nidvaldo", "OW": "Obvaldo",
    "SG": "San Gallo", "SH": "Sciaffusa", "SO": "Soletta", "SZ": "Svitto", "TG": "Turgovia", "TI": "Ticino",
    "UR": "Uri", "VD": "Vaud", "VS": "Vallese", "ZG": "Zugo", "ZH": "Zurigo",
}

# The federal side, split by court and seat. (code on the stamp, caption lines, by_court keys)
FEDERAL_STAMPS = [
    ("TF", ("Tribunale federale", "Losanna"), ("bger@VD",)),
    ("TF", ("Tribunale federale", "Lucerna"), ("bger@LU",)),
    ("TAF", ("Tribunale amministrativo federale", "San Gallo"), ("bvger",)),
    ("TPF", ("Tribunale penale federale", "Bellinzona"), ("bstger",)),
]
OTHER_FEDERAL = ("CH", ("Altre autorità federali", "e Corte EDU"))

PAPER, DESK = "#f5efe2", "#2a2724"
RED, BLUE = "#c3202f", "#1e3561"

# Sheet geometry
COLS = ROWS = 5
SHEET_X, SHEET_Y = 48, 214
SELVAGE_X, SELVAGE_TOP, SELVAGE_BOTTOM = 32, 46, 70
CELL_W, CELL_H = 184, 190
PERF = 7.2  # hole diameter


def esc(s) -> str:
    return html.escape(str(s))


def stamps(week: dict) -> tuple[list[dict], dict]:
    """All stamps but the special issue, by face value; and the special issue itself."""
    by_canton, by_court = week["by_canton"], week["by_court"]
    out = []
    for code, n in by_canton.items():
        if code == wp.FEDERAL or code == "TI" or not n:
            continue
        out.append({"code": code, "caption": (CANTON_IT[code],), "n": n, "ink": RED})
    fed_total = by_canton.get(wp.FEDERAL, 0)
    for code, caption, keys in FEDERAL_STAMPS:
        n = sum(by_court.get(k, 0) for k in keys)
        if n:
            out.append({"code": code, "caption": caption, "n": n, "ink": BLUE})
        fed_total -= n
    if fed_total:
        out.append({"code": OTHER_FEDERAL[0], "caption": OTHER_FEDERAL[1], "n": fed_total, "ink": BLUE})
    out.sort(key=lambda s: (-s["n"], s["code"]))
    special = {"code": "TI", "caption": (CANTON_IT["TI"],), "n": by_canton.get("TI", 0),
               "usual": week["usual_canton"].get("TI", 0)}
    return out, special


def place(n_small: int) -> list[tuple[int, int]]:
    """Grid cells (col, row) for the small stamps in reading order, flowing around the 2x2 special issue
    at the top right."""
    cells = []
    for r in range(ROWS):
        for c in range(COLS):
            if r < 2 and c >= COLS - 2:
                continue
            cells.append((c, r))
    if n_small > len(cells):
        raise ValueError(f"{n_small} stamps do not fit the {COLS}x{ROWS} sheet")
    return cells[:n_small]


def perforations(rects: list[tuple[float, float, float, float]]) -> str:
    """Holes along the four edges of every stamp (shared edges overdraw in the same places)."""
    holes = set()
    for x, y, w, h in rects:
        nx, ny = round(w / 11.5), round(h / 11.5)
        for i in range(nx + 1):
            px = x + w * i / nx
            holes.add((round(px, 1), round(y, 1)))
            holes.add((round(px, 1), round(y + h, 1)))
        for j in range(ny + 1):
            py = y + h * j / ny
            holes.add((round(x, 1), round(py, 1)))
            holes.add((round(x + w, 1), round(py, 1)))
    return "".join(f'<circle cx="{cx}" cy="{cy}" r="{PERF / 2}"/>' for cx, cy in sorted(holes))


def design(week: dict, lang: str = "it") -> str:
    small, special = stamps(week)
    f = lambda n: wp.fmt(n, lang)  # noqa: E731
    grid_x, grid_y = SHEET_X + SELVAGE_X, SHEET_Y + SELVAGE_TOP
    sheet_w = 2 * SELVAGE_X + COLS * CELL_W
    sheet_h = SELVAGE_TOP + ROWS * CELL_H + SELVAGE_BOTTOM

    rects, divs = [], []
    for s, (c, r) in zip(small, place(len(small))):
        x, y = grid_x + c * CELL_W, grid_y + r * CELL_H
        rects.append((x, y, CELL_W, CELL_H))
        cap = "<br>".join(esc(t) for t in s["caption"])
        divs.append(
            f'<div class="st" style="left:{x}px;top:{y}px;width:{CELL_W}px;height:{CELL_H}px;color:{s["ink"]}">'
            f'<div class="fr"><span class="hv">Helvetia</span><span class="cd">{esc(s["code"])}</span>'
            f'<span class="cp">{cap}</span><span class="fv">{f(s["n"])}</span></div></div>')

    # The special issue: 2x2 at the top right, printed in solid ink.
    x, y = grid_x + (COLS - 2) * CELL_W, grid_y
    w, h = 2 * CELL_W, 2 * CELL_H
    rects.append((x, y, w, h))
    times = special["n"] / special["usual"] if special["usual"] else 0
    assert times > 4, "the special issue claims 'more than four times a usual week'"
    divs.append(
        f'<div class="st sp" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">'
        f'<div class="fr"><span class="hv">Helvetia</span><span class="em">Emissione speciale</span>'
        f'<span class="cd">{esc(special["code"])}</span>'
        f'<p class="why"><b>{esc(special["caption"][0])}</b>: {f(special["n"])} nuove decisioni, più di quattro volte '
        f'una settimana abituale ({f(int(special["usual"]))}, mediana delle otto settimane precedenti).</p>'
        f'<span class="fv">{f(special["n"])}</span></div></div>')

    silent = [CANTON_IT[k] for k in sorted(wp.TILES) if not week["by_canton"].get(k)]
    a, b = week["from"], week["to"]
    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>Settimana {week['week_no']} · dal {a.day} al {b.day} ottobre {b.year}</span></div>
<h1>{f(week['added'])} <i>nuove decisioni</i></h1>
<p class="sub">Un foglio di francobolli: uno per ogni cantone e tribunale federale con decisioni nuove questa settimana. Il valore facciale è il numero di decisioni; in ordine di valore.</p>
<div class="sheet" style="left:{SHEET_X}px;top:{SHEET_Y}px;width:{sheet_w}px;height:{sheet_h}px">
  <div class="sv top"><span><i class="k r"></i>tribunali cantonali</span><span><i class="k b"></i>tribunali e autorità federali</span><span>{f(week['added'])} decisioni · {len(small) + 1} francobolli</span></div>
  <div class="sv bot">Senza nuove decisioni questa settimana: {esc(", ".join(silent))}.</div>
</div>
{''.join(divs)}
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}"><g fill="{DESK}">{perforations(rects)}</g></svg>
<div class="ft"><b>opencaselaw.ch</b><span>{f(week['total'])} decisioni dal 1875, da consultare, citare e scaricare liberamente</span></div>"""
    css = f"""
body{{background:{DESK};color:{PAPER};font-family:'Inter Tight',Helvetica,Arial,sans-serif}}
.hd{{position:absolute;left:48px;right:48px;top:40px;display:flex;justify-content:space-between;font-size:21px;font-weight:500;letter-spacing:.01em;color:#cbc3b3}}
h1{{position:absolute;left:46px;top:76px;font-family:'Fraunces',Georgia,serif;font-weight:500;font-size:80px;line-height:1;letter-spacing:-.025em;color:{PAPER};font-variation-settings:'opsz' 144}}
h1 i{{font-style:italic;font-weight:400}}
.sub{{position:absolute;left:664px;right:48px;top:84px;font-size:18px;line-height:1.36;color:#cbc3b3}}
.sheet{{position:absolute;background:{PAPER};border-radius:2px;box-shadow:0 18px 50px rgba(0,0,0,.45)}}
.sv{{position:absolute;left:{SELVAGE_X}px;right:{SELVAGE_X}px;font-size:16px;color:#6e675b;letter-spacing:.01em}}
.sv.top{{top:14px;display:flex;justify-content:space-between}}
.sv.bot{{bottom:0;height:{SELVAGE_BOTTOM}px;display:flex;align-items:center;font-size:16px;line-height:1.3}}
.k{{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:8px;vertical-align:-1px}}
.k.r{{background:{RED}}}.k.b{{background:{BLUE}}}
.st{{position:absolute;padding:13px}}
.fr{{position:relative;width:100%;height:100%;border:1.5px solid currentColor;}}
.hv{{position:absolute;left:11px;top:9px;font-size:11px;font-weight:600;letter-spacing:.18em;text-transform:uppercase}}
.cd{{position:absolute;left:0;right:0;top:46px;text-align:center;font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:70px;line-height:1;letter-spacing:-.03em;font-variation-settings:'opsz' 144}}
.cp{{position:absolute;left:11px;right:11px;bottom:9px;font-size:12px;line-height:1.2;font-weight:500}}
.fv{{position:absolute;right:10px;top:6px;font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:34px;line-height:1;letter-spacing:-.02em;font-variation-settings:'opsz' 144}}
.st.sp{{color:{PAPER}}}
.st.sp .fr{{background:{RED};border-color:{RED}}}
.st.sp .hv{{left:18px;top:16px;font-size:13px}}
.st.sp .em{{position:absolute;right:18px;top:16px;font-size:13px;font-weight:600;letter-spacing:.18em;text-transform:uppercase}}
.st.sp .cd{{top:60px;font-size:170px}}
.st.sp .why{{position:absolute;left:18px;right:18px;bottom:16px;width:230px;font-size:16px;line-height:1.3;font-weight:500}}
.st.sp .why b{{font-weight:700}}
.st.sp .fv{{right:18px;top:auto;bottom:10px;font-size:84px}}
.ft{{position:absolute;left:48px;right:48px;bottom:30px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;color:#cbc3b3;border-top:1px solid #4a443d;padding-top:12px}}
.ft b{{font-family:'Fraunces',Georgia,serif;font-size:32px;font-weight:500;color:{PAPER}}}"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,400..700;1,9..144,400..700&family=Inter+Tight:wght@400;500;600;700",
                css, body, lang)
