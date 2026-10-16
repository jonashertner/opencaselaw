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

# The federal side, split by court and seat: (code on the stamp, caption lines, by_court keys).
FEDERAL_STAMPS = [
    ("TF", ("Tribunale federale", "Losanna"), ("bger@VD",)),
    ("TF", ("Tribunale federale", "Lucerna"), ("bger@LU",)),
    ("TAF", ("Tribunale amministrativo", "federale, San Gallo"), ("bvger",)),
    ("TPF", ("Tribunale penale", "federale, Bellinzona"), ("bstger",)),
]
OTHER_FEDERAL = ("CH", ("Altre autorità federali", "e Corte EDU"))

# Inks and paper: one red, one blue, one cream.
PAPER, DESK = "#f5efe2", "#35312c"
RED, BLUE = "#c3202f", "#1e3561"

# Sheet geometry. The perforation pitch divides the cell exactly, so every edge carries
# whole holes at a regular pitch and the holes of neighbouring stamps coincide.
COLS = ROWS = 5
PITCH, HOLE = 12, 7.0
CELL = 15 * PITCH                      # 180 px, square stamps
SELVAGE = 40                           # equal on all four sides
SHEET_W = 2 * SELVAGE + COLS * CELL    # 980
SHEET_H = 2 * SELVAGE + ROWS * CELL    # 980
SHEET_X, SHEET_Y = (W - SHEET_W) // 2, 236
PAD = 10                               # from the perforation line to the printed frame


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
    """Grid cells (col, row) for the small stamps in reading order, flowing around the 2x2 special
    issue at the top right."""
    cells = [(c, r) for r in range(ROWS) for c in range(COLS) if not (r < 2 and c >= COLS - 2)]
    if n_small > len(cells):
        raise ValueError(f"{n_small} stamps do not fit the {COLS}x{ROWS} sheet")
    return cells[:n_small]


def perforations(rects: list[tuple[int, int, int, int]]) -> str:
    """Whole holes at a fixed pitch along the four edges of every stamp; a corner hole is shared."""
    holes = set()
    for x, y, w, h in rects:
        for i in range(0, w + 1, PITCH):
            holes.add((x + i, y))
            holes.add((x + i, y + h))
        for j in range(0, h + 1, PITCH):
            holes.add((x, y + j))
            holes.add((x + w, y + j))
    return "".join(f'<circle cx="{cx}" cy="{cy}" r="{HOLE / 2}"/>' for cx, cy in sorted(holes))


def design(week: dict, lang: str = "it") -> str:
    small, special = stamps(week)
    f = lambda n: wp.fmt(n, lang)  # noqa: E731
    grid_x, grid_y = SHEET_X + SELVAGE, SHEET_Y + SELVAGE

    rects, divs = [], []
    for s, (c, r) in zip(small, place(len(small))):
        x, y = grid_x + c * CELL, grid_y + r * CELL
        rects.append((x, y, CELL, CELL))
        cap = "<br>".join(esc(t) for t in s["caption"])
        divs.append(
            f'<div class="st" style="left:{x}px;top:{y}px;width:{CELL}px;height:{CELL}px;color:{s["ink"]}">'
            f'<div class="fr"><div class="bar"><span class="hv">Helvetia</span><span class="fv">{f(s["n"])}</span></div>'
            f'<div class="cd"><span>{esc(s["code"])}</span></div><div class="cp">{cap}</div></div></div>')

    # The special issue: 2x2 at the top right, printed in solid ink.
    x, y = grid_x + (COLS - 2) * CELL, grid_y
    rects.append((x, y, 2 * CELL, 2 * CELL))
    times = special["n"] / special["usual"] if special["usual"] else 0
    assert times > 4, "the special issue claims 'more than four times a usual week'"
    divs.append(
        f'<div class="st sp" style="left:{x}px;top:{y}px;width:{2 * CELL}px;height:{2 * CELL}px">'
        f'<div class="fr"><div class="bar"><span class="hv">Helvetia</span><span class="hv">Emissione speciale</span></div>'
        f'<div class="cd"><span>{esc(special["code"])}</span></div>'
        f'<div class="foot"><p class="why"><b>{esc(special["caption"][0])}</b>: {f(special["n"])} nuove decisioni, '
        f'più di quattro volte una settimana abituale ({f(int(special["usual"]))}, mediana delle otto settimane '
        f'precedenti).</p><span class="fv">{f(special["n"])}</span></div></div></div>')

    silent = [CANTON_IT[k] for k in sorted(wp.TILES) if not week["by_canton"].get(k)]
    a, b = week["from"], week["to"]
    body = f"""
<div class="hd">OpenCaseLaw · Settimana {week['week_no']} · dal {a.day} al {b.day} ottobre {b.year}</div>
<div class="lead"><h1>{f(week['added'])} <i>nuove decisioni</i></h1>
<p class="sub">Un foglio di francobolli: uno per ogni cantone e tribunale federale con decisioni nuove questa settimana. Il valore facciale è il numero di decisioni; in ordine di valore.</p></div>
<div class="sheet" style="left:{SHEET_X}px;top:{SHEET_Y}px;width:{SHEET_W}px;height:{SHEET_H}px">
  <div class="sv top"><span><i class="k r"></i>tribunali cantonali</span><span><i class="k b"></i>tribunali e autorità federali</span><span>{f(week['added'])} decisioni · {len(small) + 1} francobolli</span></div>
  <div class="sv bot">Senza nuove decisioni questa settimana: {esc(", ".join(silent))}.</div>
</div>
{''.join(divs)}
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}"><g fill="{DESK}">{perforations(rects)}</g></svg>
<div class="ft"><b>opencaselaw.ch</b><span>{f(week['total'])} decisioni dal 1875, da consultare, citare e scaricare liberamente</span></div>"""
    fr = "'Fraunces',Georgia,serif"
    css = f"""
body{{background:{DESK};color:{PAPER};font-family:'Inter Tight',Helvetica,Arial,sans-serif}}
.hd{{position:absolute;left:{SHEET_X}px;top:40px;font-size:20px;line-height:26px;font-weight:500;color:#d2cabb}}
.lead{{position:absolute;left:{SHEET_X}px;right:{SHEET_X}px;top:92px;display:flex;justify-content:space-between;align-items:flex-end}}
h1{{font-family:{fr};font-weight:500;font-size:78px;line-height:78px;letter-spacing:-.025em;color:{PAPER};font-variation-settings:'opsz' 144;white-space:nowrap}}
h1 i{{font-style:italic;font-weight:400}}
.sub{{width:372px;font-size:17px;line-height:26px;color:#d2cabb;margin-bottom:12px}}
.sheet{{position:absolute;background:{PAPER};border-radius:2px;box-shadow:0 24px 60px rgba(0,0,0,.42),0 2px 6px rgba(0,0,0,.25)}}
.sv{{position:absolute;left:{SELVAGE}px;right:{SELVAGE}px;height:{SELVAGE}px;display:flex;align-items:center;font-size:14.5px;line-height:20px;color:#6e675b;white-space:nowrap}}
.sv.top{{top:0;justify-content:space-between}}
.sv.bot{{bottom:0}}
.k{{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:8px;vertical-align:-1px}}
.k.r{{background:{RED}}}.k.b{{background:{BLUE}}}
.st{{position:absolute;padding:{PAD}px}}
.fr{{position:relative;width:100%;height:100%;border:1.5px solid currentColor}}
.bar{{position:absolute;left:10px;right:10px;top:7px;display:flex;justify-content:space-between;align-items:baseline}}
.hv{{font-size:11px;line-height:14px;font-weight:600;letter-spacing:.18em;text-transform:uppercase}}
.fv{{font-family:{fr};font-weight:600;font-size:34px;line-height:34px;letter-spacing:-.02em;font-variation-settings:'opsz' 144}}
.cd{{position:absolute;left:0;right:0;top:42px;bottom:46px;display:flex;justify-content:center}}
.cd span{{font-family:{fr};font-weight:600;font-size:70px;line-height:70px;letter-spacing:-.03em;font-variation-settings:'opsz' 144;position:relative;top:2px}}
.cp{{position:absolute;left:10px;bottom:8px;font-size:13px;line-height:16px;font-weight:500;white-space:nowrap}}
.st.sp{{color:{PAPER}}}
.st.sp .fr{{background:{RED};border-color:{RED}}}
.st.sp .bar{{left:20px;right:20px;top:17px}}
.st.sp .hv{{font-size:13px;line-height:16px}}
.st.sp .cd{{top:60px;bottom:120px}}
.st.sp .cd span{{font-size:176px;line-height:176px;top:-26px}}
.st.sp .foot{{position:absolute;left:20px;right:20px;bottom:18px;display:flex;justify-content:space-between;align-items:flex-end}}
.st.sp .why{{width:224px;font-size:16px;line-height:21px;font-weight:500}}
.st.sp .why b{{font-weight:700}}
.st.sp .fv{{font-size:92px;line-height:92px;position:relative;top:6px}}
.ft{{position:absolute;left:{SHEET_X}px;right:{SHEET_X}px;bottom:30px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;color:#d2cabb;border-top:1px solid #57514a;padding-top:12px}}
.ft b{{font-family:{fr};font-size:32px;font-weight:500;color:{PAPER}}}"""
    return page("family=Fraunces:ital,opsz,wght@0,9..144,400..700;1,9..144,400..700&family=Inter+Tight:wght@400;500;600;700",
                css, body, lang)
