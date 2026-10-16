"""Week 42 (9-15 October 2026), English: a piste map.

The week drawn as a Pistenplan. Every court is a run: it ends in the valley and starts as
high up the mountain as it had new decisions this week (one scale for all runs, so run
length = count). The mountains are drawn to the runs, nothing else. Run colour is the
week against a usual week (median of the previous eight weeks): blue below it, red about
it, black well above it. Ticino (55 against a usual 12) is the one black run. Numbers in
the piste-map key at the foot.

Thresholds (difficulty): blue if n < usual - max(5, usual/4); black if n > usual +
max(5, usual/2); red otherwise. The federal runs are coloured by the federal total, the
only usual figure the harness carries for them.

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import math

import weekly_poster as wp
from weekly_poster_designs import AREAS_EN, esc, page

NAME = dict(AREAS_EN)
W, H = wp.W, wp.H

BASE_Y = 902          # the valley floor: every run ends here
PX_PER = 3.3          # vertical pixels per decision
ANGLE = 67            # slope of a run, degrees from horizontal
PITCH = 32            # horizontal distance between neighbouring runs
LIFT = 36             # a summit rises this far above its run's number plate
COS = math.cos(math.radians(ANGLE)) / math.sin(math.radians(ANGLE))  # horizontal per vertical

COLOUR = {"blue": "#1b5fc4", "red": "#d1232b", "black": "#17191c"}
LEGEND = [("blue", "quieter than usual"), ("red", "about a usual week"), ("black", "far busier than usual")]


def difficulty(n: float, usual: float) -> str:
    if n < usual - max(5, usual / 4):
        return "blue"
    if n > usual + max(5, usual / 2):
        return "black"
    return "red"


def _runs(week: dict) -> list[dict]:
    """The runs: federal courts split by court, cantons with new decisions, each with its colour."""
    bc, bk, uc = week["by_canton"], week["by_court"], week["usual_canton"]
    fed = bc.get(wp.FEDERAL, 0)
    vd, lu, bvger, bstger = (bk.get(k, 0) for k in ("bger@VD", "bger@LU", "bvger", "bstger"))
    other = fed - vd - lu - bvger - bstger
    assert other >= 0
    fed_col = difficulty(fed, uc.get(wp.FEDERAL, 0))
    runs = [
        {"code": "bger@VD", "name": "Federal Supreme Court, Lausanne", "short": "Supreme Court Lausanne", "n": vd, "col": fed_col},
        {"code": "bger@LU", "name": "Federal Supreme Court, Lucerne", "n": lu, "col": fed_col},
        {"code": "bvger", "name": "Federal Administrative Court", "short": "Federal Admin. Court", "n": bvger, "col": fed_col},
        {"code": "bstger", "name": "Federal Criminal Court", "n": bstger, "col": fed_col},
        {"code": "fed", "name": "Other federal bodies", "n": other, "col": fed_col},
    ]
    for c, n in bc.items():
        if c != wp.FEDERAL and n > 0:
            runs.append({"code": c, "name": NAME[c], "n": n, "col": difficulty(n, uc.get(c, 0))})
    runs = [r for r in runs if r["n"] > 0]
    assert sum(r["n"] for r in runs) == week["added"]
    for i, r in enumerate(sorted(runs, key=lambda r: (-r["n"], r["name"])), 1):
        r["no"] = i
    return runs


# The panorama, left to right. One range, two summits. The west massif carries Romandie and
# the federal courts, the east massif the German-speaking cantons; Ticino's black run comes
# off the west summit's right face. Runs on one face are parallel, PITCH apart, so they never
# cross; the short runs (fewer than ten decisions) stand in the valleys, TINY apart.
WEST_VALLEY = ["JU", "NE"]                              # tiny, left of the west massif
WEST_FACE = ["GE", "bvger", "bger@VD", "VD", "FR"]      # summit first, all leaning down-left
WEST_RIGHT = ["TI", "bger@LU", "fed", "bstger"]         # the west summit's right face, down-right
MID_VALLEY = ["SO", "BL"]                               # tiny, between the massifs
EAST_NEST = ["AG", "BE", "BS"]                          # down-left, nested under the east summit
EAST_SUMMIT = "ZH"                                      # leaning down-right
EAST_RIGHT = ["SG", "ZG", "SZ", "GR", "UR"]             # the east summit's right face
QUIET = ["VS", "LU", "OW", "NW", "SH", "TG", "AR", "AI", "GL"]  # usually silent; drawn in the east valley if present
TINY = 40
CONE = 1.0            # slope of a mountain face (vertical per horizontal)


def _layout(runs: list[dict]) -> list[dict]:
    by = {r["code"]: r for r in runs}
    h = lambda c: by[c]["n"] * PX_PER  # noqa: E731
    x = 0
    for c in [c for c in WEST_VALLEY if c in by]:
        by[c].update(face=-1, base=x)
        x += TINY
    face = [c for c in WEST_FACE if c in by]
    for i, c in enumerate(reversed(face)):             # FR ... GE, left to right
        by[c].update(face=-1, base=x + PITCH * i)
    summit = by[face[0]]
    x = summit["base"]
    right = [c for c in WEST_RIGHT if c in by]
    x += 2 * h(right[0]) * COS + PITCH                 # first right-face run clears the summit run's line
    for i, c in enumerate(right):
        by[c].update(face=1, base=x + PITCH * i)
    x = by[right[-1]]["base"] + TINY
    for c in [c for c in MID_VALLEY if c in by]:
        by[c].update(face=-1, base=x)
        x += TINY
    nest = [c for c in EAST_NEST if c in by]           # AG, BE, BS: BS leftmost
    for i, c in enumerate(reversed(nest)):
        by[c].update(face=-1, base=x + PITCH * i)
    tall = nest[0]
    xs = by[tall]["base"] - (h(EAST_SUMMIT) - 2 * h(tall)) * COS + PITCH   # the summit's start x, so AG clears its line
    by[EAST_SUMMIT].update(face=1, base=xs + h(EAST_SUMMIT) * COS)
    x = by[EAST_SUMMIT]["base"] + PITCH
    for i, c in enumerate([c for c in EAST_RIGHT if c in by]):
        by[c].update(face=1, base=x + PITCH * i)
        x_last = x + PITCH * i
    x = x_last + TINY
    for c in [c for c in QUIET if c in by]:
        by[c].update(face=1, base=x)
        x += TINY
    for r in runs:
        r["h"] = r["n"] * PX_PER
    span_l = min(r["base"] - max(0, -r["face"]) * 0 for r in runs)
    span_r = max(r["base"] - r["face"] * r["h"] * COS if r["face"] < 0 else r["base"] for r in runs)
    shift = (W - (span_r - span_l)) / 2 - span_l      # centre the range
    for r in runs:
        r["base"] += shift
        r["top"] = (r["base"] - r["face"] * r["h"] * COS, BASE_Y - r["h"])
    return runs


def _run_path(r: dict) -> str:
    """The run as a gentle S down the slope, drawn left to right so its label reads the right way up."""
    (tx, ty), (bx, byy) = r["top"], (r["base"], BASE_Y)
    p0, p3 = ((bx, byy), (tx, ty)) if r["face"] < 0 else ((tx, ty), (bx, byy))
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    length = math.hypot(dx, dy)
    s = min(8, length / 14)  # the wiggle, kept small so the drawn length stays the count
    nx, ny = -dy / length * s, dx / length * s
    p1 = (p0[0] + dx * .35 + nx, p0[1] + dy * .35 + ny)
    p2 = (p0[0] + dx * .65 - nx, p0[1] + dy * .65 - ny)
    return f"M{p0[0]:.1f},{p0[1]:.1f} C{p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f} {p3[0]:.1f},{p3[1]:.1f}"


def _mountains(runs: list[dict]) -> str:
    """The range: one faceted pyramid over the start of every run of ten or more decisions.

    Each pyramid rises LIFT above its run's number plate; its left face is lit, its right face
    in shade; the tallest carry rock at the summit. Taller pyramids are drawn first, so the
    lower ones stand in front: foothills before the main summits.
    """
    out = []
    cones = []
    for i, r in enumerate(sorted((r for r in runs if r["n"] >= 10), key=lambda r: -r["h"])):
        px, py = r["top"][0], r["top"][1] - LIFT
        sl = CONE * (0.9 + 0.08 * (i % 4))              # a little variety in the faces
        sr = CONE * (1.1 - 0.07 * (i % 3))
        cones.append((px, py, BASE_Y - py, sl, sr))

    def covered(px, py):  # the apex lies inside a taller pyramid
        return any(py > BASE_Y - (hh - (px - cx) * (sl if px < cx else sr)) for cx, cy, hh, sl, sr in cones
                   if cy < py and (hh - abs(px - cx) * (sl if px < cx else sr)) > 0)

    for px, py, hh, sl, sr in cones:
        lx, rx = px - hh / sl, px + hh / sr
        out.append(f'<polygon class="snow" points="{lx:.0f},{BASE_Y + 1} {px:.0f},{py:.0f} {px:.0f},{BASE_Y + 1}"/>'
                   f'<polygon class="shade" points="{px:.0f},{py:.0f} {rx:.0f},{BASE_Y + 1} {px:.0f},{BASE_Y + 1}"/>')
        if hh >= 300 and not covered(px, py):          # rock only on the summits that make the skyline
            d = hh * .16
            out.append(f'<polygon class="rock" points="{px:.0f},{py:.0f} {px - d / sl * .9:.0f},{py + d * .9:.0f} '
                       f'{px - d * .1:.0f},{py + d * .7:.0f} {px + d / sr * 1.25:.0f},{py + d * 1.25:.0f}"/>'
                       f'<polygon class="rock2" points="{px:.0f},{py:.0f} {px + d / sr * 1.25:.0f},{py + d * 1.25:.0f} '
                       f'{px + d / sr * .55:.0f},{py + d * .55:.0f}"/>')
    return "".join(out)


def design(week: dict, lang: str = "en") -> str:
    f = lambda n: wp.fmt(n, lang)  # noqa: E731
    a, b = week["from"], week["to"]
    runs = _layout(_runs(week))
    by = {r["code"]: r for r in runs}

    # Mountains.
    mountains = _mountains(runs)

    # Runs, labels and number plates.
    paths, plates, labels = [], [], []
    for r in sorted(runs, key=lambda r: -r["n"]):
        d = _run_path(r)
        col = COLOUR[r["col"]]
        paths.append(f'<path id="r{r["no"]}" d="{d}" class="halo"/><path d="{d}" stroke="{col}" class="run"/>')
        tx, ty = r["top"]
        plates.append(f'<g transform="translate({tx:.1f},{ty - 19:.1f})"><circle r="14" fill="{col}" class="plate"/>'
                      f'<text class="no">{r["no"]}</text></g>')
        length = r["h"] / math.sin(math.radians(ANGLE))
        text = f'{r.get("short", r["name"])}  {f(r["n"])}'
        if r["n"] >= 50 and 7.9 * len(text) < 0.6 * length:  # long runs only; the label covers at most 60%
            tp = f'<textPath href="#r{r["no"]}" startOffset="50%" text-anchor="middle">{esc(text)}</textPath>'
            labels.append(f'<text class="lbl halo">{tp}</text><text class="lbl">{tp}</text>')

    # Key.
    order = sorted(runs, key=lambda r: r["no"])
    cols, per = 3, -(-len(order) // 3)
    key_cols = "".join(
        '<div class="col">' + "".join(
            f'<div class="row"><span class="pl" style="background:{COLOUR[r["col"]]}">{r["no"]}</span>'
            f'<span class="nm">{esc(r["name"])}</span><span class="ct">{f(r["n"])}</span></div>'
            for r in order[i * per:(i + 1) * per]) + "</div>"
        for i in range(cols))
    legend = "".join(f'<span class="lg"><i style="background:{COLOUR[k]}"></i>{t}</span>' for k, t in LEGEND)
    ti = by["TI"]

    body = f"""
<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9cbbdc"/><stop offset=".55" stop-color="#cfdfee"/><stop offset="1" stop-color="#eef3f8"/></linearGradient>
<linearGradient id="valley" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e9eff5"/><stop offset="1" stop-color="#f7f9fb"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="url(#sky)"/>
<polygon class="far" points="-20,{BASE_Y} 60,640 170,590 260,640 330,560 420,620 520,540 640,610 720,520 820,590 900,530 980,600 1100,560 1100,{BASE_Y}"/>
<polygon class="far2" points="-20,{BASE_Y} 100,700 230,660 350,720 470,650 560,700 700,640 790,690 900,630 1010,690 1100,660 1100,{BASE_Y}"/>
<rect x="0" y="{BASE_Y}" width="{W}" height="{H - BASE_Y}" fill="url(#valley)"/>
{mountains}
<line x1="0" y1="{BASE_Y}" x2="{W}" y2="{BASE_Y}" stroke="#b8c7d6" stroke-width="1"/>
{''.join(paths)}{''.join(labels)}{''.join(plates)}
</svg>
<div class="head">
<div class="eyebrow">OpenCaseLaw &middot; Piste map of the week</div>
<div class="big">{f(week['added'])}<span> new decisions</span></div>
<div class="when">Week {week['week_no']} &middot; {a.day}&ndash;{b.day} {b.strftime('%B %Y')}</div>
<div class="how">Every run is a court. Its length: the court's new decisions this week.
Its colour: the week against a usual one.</div>
</div>
<div class="key">
<div class="legend">{legend}<span class="lg note">usual = median of the previous eight weeks; blue below ¾ of it, black above 1½ × it</span></div>
<div class="cols">{key_cols}</div>
</div>
<div class="foot"><span>opencaselaw.ch</span><span>{f(week['total'])} decisions, 1875 to date &middot; open data</span></div>
"""
    assert ti["col"] == "black"
    css = f"""
body{{background:#9cbbdc;font-family:'Barlow',Helvetica,Arial,sans-serif;color:#13233a}}
.far{{fill:#b7cce2}}.far2{{fill:#c9dae9}}
.snow{{fill:#f9fbfd}}.shade{{fill:#d4dfeb}}.rock{{fill:#8793a1}}.rock2{{fill:#6a7584}}
.halo{{fill:none;stroke:#fff;stroke-width:14;stroke-linecap:round;opacity:.85}}
.run{{fill:none;stroke-width:8;stroke-linecap:round}}
.plate{{stroke:#fff;stroke-width:2.5}}
.no{{font-size:15px;font-weight:700;fill:#fff;text-anchor:middle;dominant-baseline:central}}
.lbl{{font-size:14.5px;font-weight:700;fill:#13233a;letter-spacing:.02em}}
.lbl.halo{{fill:none;stroke:#fff;stroke-width:8px;stroke-linejoin:round}}
.lbl textPath{{dominant-baseline:central}}
.head{{position:absolute;left:64px;top:58px;width:700px}}
.eyebrow{{font-size:19px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#1d3c62}}
.big{{font-size:118px;font-weight:800;line-height:1;letter-spacing:-.02em;margin-top:14px}}
.big span{{font-size:46px;font-weight:600;letter-spacing:0;margin-left:14px}}
.when{{font-size:30px;font-weight:600;margin-top:10px}}
.how{{font-size:20px;line-height:1.3;margin-top:14px;max-width:560px;color:#1d3c62}}
.key{{position:absolute;left:40px;right:40px;top:938px;height:330px;background:#fff;border:1.5px solid #b8c7d6;padding:16px 24px 12px}}
.legend{{display:flex;gap:24px;align-items:center;font-size:18px;font-weight:600;padding-bottom:10px;border-bottom:1px solid #d6dee7;margin-bottom:10px}}
.lg{{display:flex;align-items:center;gap:9px;white-space:nowrap}}
.lg i{{display:inline-block;width:34px;height:8px;border-radius:4px}}
.lg.note{{margin-left:auto;font-weight:400;font-size:14.5px;color:#4d5c70;max-width:360px;line-height:1.25;white-space:normal}}
.cols{{display:flex;gap:26px}}
.col{{min-width:0}}.col:nth-child(1){{flex:1.5}}.col:nth-child(2){{flex:1.42}}.col:nth-child(3){{flex:1}}
.row{{display:flex;align-items:center;gap:9px;height:28px;font-size:17.5px}}
.pl{{flex:none;width:24px;height:24px;border-radius:50%;color:#fff;font-weight:700;font-size:13px;display:flex;align-items:center;justify-content:center}}
.nm{{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ct{{flex:none;font-weight:700;min-width:34px;text-align:right}}
.foot{{position:absolute;left:40px;right:40px;bottom:40px;display:flex;justify-content:space-between;font-size:21px;font-weight:600;color:#13233a}}
.foot span:last-child{{font-weight:500}}"""
    return page("family=Barlow:wght@400;500;600;700;800", css, body, lang)
