"""Week 42 poster, German: the Abstimmungskarte.

Switzerland's cantons shaded like a federal vote-result map, but the variable is this
week's new decisions per canton against a usual week (the median of the eight weeks
before). The federal courts get their own box, as the national result does on a vote map.

Canton shapes, lakes and the national border: scripts/weekly_poster_cantons.json
(swisstopo swissBOUNDARIES3D and the federal VEC25 lake layer via geo.admin.ch,
simplified; attribution "© swisstopo" on the poster). Lakes are the Swiss part only,
like the cantons; the border is the cantons' unshared edges. No runtime fetch except fonts.
"""
from __future__ import annotations

import json
from pathlib import Path

import weekly_poster as wp
from weekly_poster_designs import CANTON_DE, esc, page

W, H = wp.W, wp.H
FED = wp.FEDERAL

# Diverging scale: "mehr" in blue, "weniger" in ochre. Deliberately not a Ja/Nein palette.
MORE = ("#a9c8e6", "#5b93cc", "#1d4f91")
LESS = ("#f3cfa6", "#e0975a", "#a84f12")
SAME = "#d9d9d4"
FEW = "#ffffff"  # hatched: under three decisions, this week and usually
INK, SOFT, RULE = "#1b1b1b", "#595955", "#b9b9b4"
LAKE = "#cfe0ef"

# Map frame: the country at 1016 px width, LV03-style km in, poster px out.
E0, E1, N0, N1 = 485.0, 834.0, 75.0, 296.0
MAP_X, MAP_Y, MAP_W = 32, 322, 1016
SC = MAP_W / (E1 - E0)
MAP_H = (N1 - N0) * SC

# Label anchors (LV03 km), hand-placed where the automatic centroid lands badly.
ANCHOR = {
    "ZH": (688, 253), "BE": (612, 182), "LU": (652, 214), "UR": (691, 183), "SZ": (701, 212),
    "OW": (654, 189), "NW": (673, 201), "GL": (726, 206), "ZG": (683, 224), "FR": (577, 176),
    "SO": (606, 229), "BS": (611, 269), "BL": (626, 254), "SH": (687, 284), "AR": (736, 250),
    "AI": (750, 242), "SG": (761, 226), "GR": (765, 177), "AG": (655, 253), "TG": (722, 273),
    "TI": (708, 118), "VD": (540, 157), "VS": (618, 114), "NE": (552, 208), "GE": (500, 116),
    "JU": (585, 246),
}
# Labels that cannot sit inside their canton: pulled out on a 45-degree leader of one length.
LEADER_KM = 11.0
OUTSIDE = {"BS": (-1, 1), "NE": (-1, 1), "GE": (1, -1)}  # direction (east, north)


def px(e: float) -> float:
    return MAP_X + (e - E0) * SC


def py(n: float) -> float:
    return MAP_Y + (N1 - n) * SC


def ring_path(pts) -> str:
    return "M" + "L".join(f"{px(e):.1f} {py(n):.1f}" for e, n in pts) + "Z"


def _lum(h: str) -> float:
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def white_type(fill: str) -> bool:
    """White labels only where they reach WCAG 4.5:1 on the fill; dark ink everywhere else."""
    return 1.05 / (_lum(fill) + 0.05) >= 4.5


def klass(now: float, usual: float) -> tuple[str, str]:
    """(colour, legend key) for one canton or the federal courts."""
    if now < 3 and usual < 3:
        return FEW, "few"
    if usual == 0:
        return MORE[2], "m3"
    r = now / usual
    if r > 2:
        return MORE[2], "m3"
    if r > 1.5:
        return MORE[1], "m2"
    if r > 1.25:
        return MORE[0], "m1"
    if r >= 0.8:
        return SAME, "same"
    if r > 2 / 3:
        return LESS[0], "l1"
    if r > 0.5:
        return LESS[1], "l2"
    return LESS[2], "l3"


LEGEND = [("l3", LESS[2], "unter ½"), ("l2", LESS[1], "½–⅔"), ("l1", LESS[0], "⅔–⅘"),
          ("same", SAME, "etwa gleich"), ("m1", MORE[0], "1¼–1½×"), ("m2", MORE[1], "1½–2×"),
          ("m3", MORE[2], "über 2×")]
SW_W, SW_GAP = 118, 8  # legend swatch width and gap


def usual_fmt(v: float, lang: str) -> str:
    return wp.fmt(int(v), lang) if float(v).is_integer() else wp.fmt(int(v), lang) + ",5"


def design(week: dict, lang: str = "de") -> str:
    here = Path(__file__).resolve().parent
    data = json.loads((here / "weekly_poster_cantons.json").read_text(encoding="utf-8"))
    cantons, lakes_data = data["cantons"], data["lakes"]
    now = lambda k: week["by_canton"].get(k, 0)  # noqa: E731
    typ = lambda k: week["usual_canton"].get(k, 0)  # noqa: E731

    shapes, labels = [], []
    for code in sorted(cantons, key=lambda k: -max(len(r) for p in cantons[k] for r in p)):
        colour, key = klass(now(code), typ(code))
        d = "".join(ring_path(r) for poly in cantons[code] for r in poly)
        fill = f'fill="{colour}"' if key != "few" else 'fill="url(#hatch)"'
        shapes.append(f'<path d="{d}" {fill} fill-rule="evenodd" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>')
    for code in cantons:
        colour, key = klass(now(code), typ(code))
        e, n = ANCHOR[code]
        x, y = px(e), py(n)
        n_ = now(code)
        if code in OUTSIDE:
            de, dn = OUTSIDE[code]
            lx, ly = px(e + de * LEADER_KM), py(n + dn * LEADER_KM)
            anchor = "start" if de > 0 else "end"
            labels.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" stroke="{SOFT}" stroke-width="1"/>'
                          f'<text x="{lx + (4 if de > 0 else -4):.1f}" y="{ly:.1f}" class="lb" text-anchor="{anchor}" dy="{-2 if dn > 0 else 24}">'
                          f'<tspan class="code">{code}</tspan><tspan class="num" dx="5">{wp.fmt(n_, lang)}</tspan></text>')
            continue
        if n_:
            labels.append(f'<text x="{x:.1f}" y="{y:.1f}" class="lb{" w" if white_type(colour) else ""}" text-anchor="middle">'
                          f'<tspan class="code" x="{x:.1f}" dy="-9">{code}</tspan>'
                          f'<tspan class="num" x="{x:.1f}" dy="27">{wp.fmt(n_, lang)}</tspan></text>')
        else:
            labels.append(f'<text x="{x:.1f}" y="{y:.1f}" class="lb none" text-anchor="middle" dy="6">{code}</text>')

    lakes = "".join(ring_path(r) for l in lakes_data for r in l["rings"])
    border = "".join("M" + "L".join(f"{px(e):.1f} {py(n):.1f}" for e, n in ch) for ch in data["border"])

    # Federal courts: the "Bund" box, like the national result on a vote map.
    fn, fu = now(FED), typ(FED)
    fc = lambda c: week["by_court"].get(c, 0)  # noqa: E731
    bger_vd, bger_lu, bvger = fc("bger@VD"), fc("bger@LU"), fc("bvger")
    rest = fn - bger_vd - bger_lu - bvger
    f_colour, f_key = klass(fn, fu)
    fed_rows = [("Bundesgericht, Lausanne", bger_vd), ("Bundesgericht, Luzern", bger_lu),
                ("Bundesverwaltungsgericht", bvger), ("Übrige Bundesinstanzen", rest)]
    fed_html = "".join(f'<div class="fr"><span>{esc(n)}</span><b>{wp.fmt(v, lang)}</b></div>' for n, v in fed_rows)

    # Table: every canton with new decisions, this week against a usual week, three equal columns.
    rows = sorted((k for k in cantons if now(k)), key=lambda k: (-now(k), k))
    cells = []
    for k in rows:
        colour, key = klass(now(k), typ(k))
        sw = f'<i style="background:{colour}{";border:1px solid #c4c4bf" if key == "few" else ""}"></i>'
        cells.append(f'<div class="tr">{sw}<span class="tn">{esc(CANTON_DE[k])}</span>'
                     f'<b>{wp.fmt(now(k), lang)}</b><span class="tu">{usual_fmt(typ(k), lang)}</span></div>')
    per = -(-len(cells) // 3)
    th = f'<div class="th"><i></i><span>Kanton</span><span>Woche {week["week_no"]}</span><span>üblich</span></div>'
    columns = "".join(f'<div class="tc">{th}{"".join(cells[i:i + per])}</div>' for i in range(0, len(cells), per))
    silent = [k for k in wp.TILES if not now(k)]
    silent_html = ", ".join(CANTON_DE[k] for k in sorted(silent, key=lambda k: CANTON_DE[k]))

    legend = "".join(f'<div class="lg"><i style="background:{c}"></i><span>{t}</span></div>' for _, c, t in LEGEND)
    group_w = 3 * SW_W + 2 * SW_GAP
    a, b = week["from"], week["to"]
    months = {10: "Oktober"}
    body = f"""
<div class="bar"><span class="brand">OpenCaseLaw</span><span>Woche {week['week_no']} · {a.day}.–{b.day}. {months.get(b.month, b.strftime('%B'))} {b.year}</span></div>
<div class="hd"><div class="n">{wp.fmt(week['added'], lang)}</div>
<div class="t"><h1>neue<br>Entscheide</h1>
<p>Je Kanton verglichen mit einer üblichen Woche, dem Median der acht Wochen davor ({usual_fmt(week['usual_total'], lang)} Entscheide).</p></div></div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect width="5" height="5" fill="#fff"/><line x1="0" y1="0" x2="0" y2="5" stroke="#d4d4d0" stroke-width="1"/></pattern></defs>
{''.join(shapes)}
<path d="{lakes}" fill="{LAKE}" fill-rule="evenodd"/>
<path d="{border}" fill="none" stroke="#6c6c68" stroke-width="1.1" stroke-linejoin="round" stroke-linecap="round"/>
{''.join(labels)}
</svg>
<div class="fed" style="border-top-color:{f_colour}"><div class="fh"><span class="k">Bund</span><span class="s">Eidgenössische Gerichte und Behörden</span></div>
<div class="fn"><b>{wp.fmt(fn, lang)}</b><span>üblich {usual_fmt(fu, lang)}</span></div>{fed_html}</div>
<div class="leg"><span class="lh" style="left:0;width:{group_w}px">weniger als in einer üblichen Woche</span>
<span class="lh" style="left:{4 * (SW_W + SW_GAP)}px;width:{group_w}px">mehr als in einer üblichen Woche</span>
<div class="lrow">{legend}<div class="lg few"><i></i><span>unter 3, wie üblich</span></div></div></div>
<div class="tbl">{columns}</div>
<p class="note">Keine neuen Entscheide diese Woche aus {esc(silent_html)}.</p>
<div class="ft"><b>opencaselaw.ch</b><span>{wp.fmt(week['total'], lang)} Entscheide seit 1875, frei durchsuchbar, zitierbar und herunterladbar · Grenzen, Seen: © swisstopo, BAFU</span></div>"""
    css = f"""
body{{background:#fff;color:{INK};font-family:'Inter','Helvetica Neue',Arial,sans-serif;font-variant-numeric:lining-nums tabular-nums}}
.bar{{position:absolute;left:40px;right:40px;top:26px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;font-weight:500;color:{SOFT}}}
.bar .brand{{font-weight:700;color:{INK};letter-spacing:-.01em}}
.hd{{position:absolute;left:36px;width:700px;top:64px;display:flex;align-items:flex-start;gap:24px}}
.hd .n{{font-family:'Inter Tight','Inter',sans-serif;font-size:176px;font-weight:800;letter-spacing:-.05em;line-height:.86;color:{INK}}}
.hd .t{{padding-top:6px}}
h1{{font-family:'Inter Tight','Inter',sans-serif;font-size:50px;font-weight:700;letter-spacing:-.03em;line-height:.98}}
.hd p{{margin-top:12px;font-size:17px;line-height:1.35;color:{SOFT};max-width:340px}}
.lb{{fill:{INK}}}
.lb .code{{font-size:15px;font-weight:600;letter-spacing:.05em}}
.lb .num{{font-size:30px;font-weight:700;letter-spacing:-.02em}}
.lb.w{{fill:#fff}}
.lb.none{{fill:#8a8a86;font-size:15px;font-weight:600;letter-spacing:.05em}}
.fed{{position:absolute;right:40px;top:64px;width:290px;background:#fff;border-top:8px solid;box-shadow:0 1px 4px rgba(0,0,0,.18);padding:12px 16px 12px}}
.fh{{display:flex;flex-direction:column;line-height:1.15}}
.fh .k{{font-size:22px;font-weight:800;letter-spacing:-.01em}}.fh .s{{font-size:13px;color:{SOFT};margin-top:2px}}
.fn{{display:flex;align-items:baseline;gap:12px;margin:8px 0 8px}}
.fn b{{font-family:'Inter Tight','Inter',sans-serif;font-size:54px;font-weight:800;letter-spacing:-.04em;line-height:1}}
.fn span{{font-size:15px;font-weight:600;color:{SOFT}}}
.fr{{display:flex;justify-content:space-between;align-items:baseline;font-size:14.5px;border-top:1px solid #e6e6e2;padding:5px 0 4px;color:#2c2c2c}}
.fr b{{font-size:18px;font-weight:700}}
.leg{{position:absolute;left:40px;top:980px;height:70px}}
.lh{{position:absolute;top:0;font-size:14px;font-weight:600;color:{SOFT};white-space:nowrap}}
.lrow{{position:absolute;top:24px;left:0;display:flex;gap:{SW_GAP}px;align-items:flex-start}}
.lg{{width:{SW_W}px;display:flex;flex-direction:column;gap:5px}}
.lg i{{display:block;height:20px;border-radius:2px}}
.lg span{{font-size:14px;line-height:1.2;color:#2c2c2c;white-space:nowrap}}
.lg.few{{margin-left:{SW_GAP + 10}px;width:140px}}.lg.few i{{background:repeating-linear-gradient(45deg,#fff 0 2.5px,#d4d4d0 2.5px 3.5px);border:1px solid #c4c4bf}}
.tbl{{position:absolute;left:40px;right:40px;top:1066px;display:grid;grid-template-columns:repeat(3,1fr);column-gap:32px}}
.th,.tr{{display:grid;grid-template-columns:14px 1fr 64px 52px;align-items:center;column-gap:8px;height:26px}}
.th{{font-size:11.5px;font-weight:600;color:{SOFT};text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid {RULE};height:24px;align-items:end;padding-bottom:5px;box-sizing:content-box}}
.th span{{white-space:nowrap}}.th span:nth-child(n+3){{text-align:right}}
.tr{{font-size:15px;border-bottom:1px solid #ececea}}
.tr i{{display:block;width:12px;height:12px;border-radius:2px}}
.tr b{{font-size:15px;font-weight:700;text-align:right}}
.tr .tu{{color:{SOFT};text-align:right;font-size:14px}}
.note{{position:absolute;left:40px;right:40px;top:1262px;font-size:14px;color:{SOFT};line-height:1.35}}
.ft{{position:absolute;left:40px;right:40px;bottom:22px;display:flex;justify-content:space-between;align-items:baseline;font-size:14px;color:{SOFT};border-top:1px solid {RULE};padding-top:8px}}
.ft b{{font-size:22px;font-weight:700;color:{INK}}}"""
    fonts = "family=Inter:wght@400;500;600;700;800&family=Inter+Tight:wght@700;800"
    return page(fonts, css, body, lang)
