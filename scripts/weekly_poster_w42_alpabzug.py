#!/usr/bin/env python3
"""Week 42 poster, Romansh edition: the week's courts come down from the alp.

A procession of cows on a switchback road. One cow per canton with new decisions in the window
(CH split into the federal courts by seat), ordered by count: the biggest bells lead, so the
procession reads as a sorted bar chart. The area of a cow's bell is proportional to its count
(height ~ sqrt(n)); the cow itself grows a little with the count too. The lead cow wears the
flowers of the Alpkönigin. Labels: the canton code (or federal court abbreviation) and the number.

Nothing about removed rows, nights or builds is shown; the counts are the harness's by_canton /
by_court figures and must add up to week["added"].
"""
from __future__ import annotations

import math

import weekly_poster as wp
from weekly_poster_designs import esc, page

W, H = wp.W, wp.H

MONTH_RM = ["schaner", "favrer", "mars", "avrigl", "matg", "zercladur", "fanadur", "avust", "settember",
            "october", "november", "december"]

T = {
    "kicker": "OpenCaseLaw",
    "title": "Davent da l’alp",
    "headline": "Emna {w} · dals {a} fin ils {b} {m} {y}",
    "big": "novas decisiuns",
    "explain": "Mintga vatga è in chantun u ina dretgira federala. La grondezza da sia s-chella: sias novas decisiuns questa emna.",
    "total": "{t} decisiuns dapi il 1875",
}

# Federal side, split by court (and the Federal Supreme Court by seat); the rest of CH is "autras".
FEDERAL = [("bger@VD", "TF Losanna"), ("bvger", "TAF"), ("bger@LU", "TF Lucerna"), ("bstger", "TPF")]
OTHERS = "autras"

# palette
SKY0, SKY1 = "#d6e3ee", "#f4efe3"
BACK, FAR, FAR_SNOW, MID = "#b7c2ce", "#8798ad", "#f7f6f1", "#5f7f66"
MEADOW0, MEADOW1 = "#a9b96c", "#8ea557"
ROAD, ROAD_EDGE = "#dccfb4", "#b6a687"
COW = "#3a2a1f"
BELL, BELL_DARK, BELL_LIGHT = "#d4a53f", "#6a4712", "#f3dc8c"
STRAP, STRAP_DOT = "#b4302b", "#f6ead2"
INK, INK_SOFT = "#27221c", "#5a5248"


def herd(week: dict) -> list[tuple[str, int]]:
    """[(label, n)] sorted biggest first; the federal side split by court, cantons by code."""
    by_canton, by_court = week["by_canton"], week["by_court"]
    out = [(code, n) for code, n in by_canton.items() if code != wp.FEDERAL and n > 0]
    fed = 0
    for key, label in FEDERAL:
        n = by_court.get(key, 0)
        if n > 0:
            out.append((label, n))
        fed += n
    rest = by_canton.get(wp.FEDERAL, 0) - fed
    if rest > 0:
        out.append((OTHERS, rest))
    out.sort(key=lambda c: (-c[1], c[0]))
    assert sum(n for _, n in out) == week["added"], "the herd must add up to the week"
    return out


# ── drawing ──────────────────────────────────────────────────────────────────────────────────────

def bell(cx: float, top: float, h: float) -> str:
    """A Treichel hanging from (cx, top): crown, flaring body, rim, clapper. Area ~ h^2."""
    w = h * 1.0
    body = (f"M{cx - .19 * w:.1f},{top:.1f} C{cx - .19 * w:.1f},{top + .12 * h:.1f} {cx - .46 * w:.1f},{top + .5 * h:.1f} "
            f"{cx - .5 * w:.1f},{top + .84 * h:.1f} L{cx - .5 * w:.1f},{top + h:.1f} L{cx + .5 * w:.1f},{top + h:.1f} "
            f"L{cx + .5 * w:.1f},{top + .84 * h:.1f} C{cx + .46 * w:.1f},{top + .5 * h:.1f} {cx + .19 * w:.1f},{top + .12 * h:.1f} "
            f"{cx + .19 * w:.1f},{top:.1f} Z")
    sw = max(1.2, h * 0.045)
    return (f'<path d="{body}" fill="{BELL}" stroke="{BELL_DARK}" stroke-width="{sw:.1f}" stroke-linejoin="round"/>'
            f'<path d="M{cx - .5 * w:.1f},{top + .84 * h:.1f} L{cx + .5 * w:.1f},{top + .84 * h:.1f}" stroke="{BELL_DARK}" stroke-width="{sw:.1f}"/>'
            f'<path d="M{cx - .13 * w:.1f},{top + .16 * h:.1f} C{cx - .2 * w:.1f},{top + .35 * h:.1f} {cx - .3 * w:.1f},{top + .55 * h:.1f} {cx - .34 * w:.1f},{top + .72 * h:.1f}" '
            f'fill="none" stroke="{BELL_LIGHT}" stroke-width="{max(1.0, h * .06):.1f}" stroke-linecap="round" opacity=".85"/>'
            f'<circle cx="{cx:.1f}" cy="{top + h + h * .05:.1f}" r="{max(1.5, h * .075):.1f}" fill="{BELL_DARK}"/>')


def cow(x: float, ground: float, s: float, flip: bool, n: int, bell_h: float, lead: bool) -> str:
    """A cow walking right (flipped: left), feet at `ground`, tail at local x=0. Local units: length 131, height 90."""
    tf = f"translate({x:.1f} {ground:.1f}) scale({-s if flip else s:.3f} {s:.3f})" + (" translate(-128 0)" if flip else "")
    body = ("M12,-82 C30,-88 60,-88 78,-84 C86,-82 92,-78 98,-74 C106,-71 116,-64 123,-55 C127,-50 127,-42 122,-39 "
            "C117,-36 113,-39 110,-43 C106,-50 101,-57 96,-60 C92,-56 88,-47 86,-40 C83,-37 79,-38 74,-40 "
            "C60,-42 40,-42 26,-42 C15,-44 9,-52 9,-60 C9,-72 10,-78 12,-82 Z")
    udder = '<ellipse cx="50" cy="-40" rx="10" ry="6"/>'
    legs = ("M16,-48 L26,-48 L22,-2 L12,-2 Z M30,-48 L40,-48 L40,-2 L30,-2 Z "
            "M64,-48 L74,-48 L74,-2 L64,-2 Z M76,-48 L86,-48 L92,-2 L82,-2 Z")
    tail = "M12,-80 C3,-70 1,-56 5,-42"
    horns = "M97,-75 C95,-82 98,-89 104,-90 M103,-73 C106,-81 112,-84 117,-82"
    ear = '<ellipse cx="93" cy="-70" rx="6" ry="3.2" transform="rotate(-30 93 -70)"/>'
    # a wide strap around the neck, the bell hanging from its lowest point
    strap = (f'<path d="M85,-83 C90,-72 93,-62 93,-52" fill="none" stroke="{STRAP}" stroke-width="9" stroke-linecap="round"/>'
             f'<g fill="{STRAP_DOT}"><circle cx="88" cy="-76" r="1.6"/><circle cx="91.4" cy="-67" r="1.6"/><circle cx="92.8" cy="-58" r="1.6"/></g>')
    flowers = ""
    if lead:
        flowers = ('<g><circle cx="102" cy="-95" r="5.4" fill="#d9403a"/><circle cx="111" cy="-98" r="5" fill="#f3efe4"/>'
                   '<circle cx="93" cy="-92" r="4.6" fill="#f3efe4"/><circle cx="119" cy="-92" r="4.4" fill="#d9403a"/>'
                   '<circle cx="106" cy="-87" r="3.4" fill="#e6b72f"/><circle cx="87" cy="-86" r="3.6" fill="#e6b72f"/>'
                   '<path d="M97,-99 l-4,-6 M114,-103 l3,-6" stroke="#3d7f4e" stroke-width="2" stroke-linecap="round"/></g>')
    # the bell is drawn unscaled by the cow's size (its height encodes the count), in the outer frame
    bx = x + (128 - 93) * s if flip else x + 93 * s
    bt = ground - 52 * s
    shadow = f'<ellipse cx="{x + 66 * s:.1f}" cy="{ground + 1:.1f}" rx="{62 * s:.1f}" ry="{5 * s:.1f}" fill="#4a3a28" opacity=".22"/>'
    return (shadow + f'<g transform="{tf}" fill="{COW}" stroke="{COW}" stroke-linejoin="round" stroke-linecap="round">'
            f'<path d="{legs}" stroke-width="2"/><path d="{tail}" fill="none" stroke-width="4"/>'
            f'<path d="{body}" stroke-width="2"/>{udder}<path d="{horns}" fill="none" stroke-width="3"/>{ear}{strap}{flowers}</g>'
            + bell(bx, bt, bell_h))


def mountains() -> str:
    far = ("M0,520 L70,480 L140,505 L215,445 L265,470 L320,460 L395,500 L470,448 L530,476 L590,428 L650,466 "
           "L715,452 L790,500 L860,454 L930,484 L1000,462 L1080,500 L1080,620 L0,620 Z")
    snow = ("M215,445 L197,460 L208,457 L218,464 L228,459 L237,463 Z M590,428 L572,446 L585,442 L593,451 L603,444 L612,449 Z "
            "M860,454 L845,469 L856,465 L864,472 L873,466 L880,470 Z M470,448 L458,461 L467,458 L474,464 L484,460 Z")
    mid = ("M0,560 L90,515 L170,548 L250,495 L340,540 L420,512 L520,556 L610,508 L700,546 L780,520 L870,556 "
           "L950,522 L1080,560 L1080,660 L0,660 Z")
    back = ("M0,500 L60,452 L130,478 L200,436 L270,470 L350,420 L430,462 L500,432 L560,458 L640,402 L720,450 "
            "L800,418 L870,452 L950,412 L1020,446 L1080,420 L1080,620 L0,620 Z")
    back_snow = ("M350,420 L336,434 L346,431 L352,437 L361,432 L368,436 Z M640,402 L626,418 L636,414 L643,421 L652,415 L660,420 Z "
                 "M950,412 L938,425 L947,422 L953,428 L961,423 L968,427 Z")
    return (f'<path d="{back}" fill="{BACK}"/><path d="{back_snow}" fill="{FAR_SNOW}"/>'
            f'<path d="{far}" fill="{FAR}"/><path d="{snow}" fill="{FAR_SNOW}"/><path d="{mid}" fill="{MID}"/>')


def design(week: dict, lang: str = "rm") -> str:
    lang = "rm"  # the Romansh edition; the harness passes the language through
    a, b = week["from"], week["to"]
    cows = herd(week)
    top = max(n for _, n in cows)
    left, right = 110, 970
    grounds = [728, 938, 1148]                     # the three legs of the road, far to near
    rows = [cows[14:][::-1], cows[7:14], cows[:7][::-1]]   # back of the procession first
    flips = [False, True, False]                   # walking direction per leg: →, ←, →
    out, labels = [], []
    for ground, row, flip in zip(grounds, rows, flips):
        k = len(row)
        pitch = (right - left) / max(1, k - 1)
        for i, (label, n) in enumerate(row):
            cx = left + i * pitch
            s = 0.88 + 0.18 * math.sqrt(n / top)
            bell_h = 9 + 36 * math.sqrt(n / top)
            x = cx - 66 * s
            out.append(cow(x, ground, s, flip, n, bell_h, lead=(n == top)))
            labels.append(f'<text x="{cx:.1f}" y="{ground + 34}" class="num">{wp.fmt(n, lang)}</text>'
                          f'<text x="{cx:.1f}" y="{ground + 55}" class="code">{esc(label)}</text>')
    road = (f"M-30,610 C30,650 50,{grounds[0]} {left - 20},{grounds[0]} L{right + 20},{grounds[0]} "
            f"C{right + 110},{grounds[0]} {right + 110},{grounds[1]} {right + 20},{grounds[1]} L{left - 20},{grounds[1]} "
            f"C{left - 110},{grounds[1]} {left - 110},{grounds[2]} {left - 20},{grounds[2]} L{right + 20},{grounds[2]} "
            f"C{right + 60},{grounds[2]} {right + 90},{grounds[2] + 30} {W + 30},{grounds[2] + 70}")
    m = MONTH_RM[b.month - 1]
    month = ("d’" if m[0] in "aeiou" else "da ") + m
    body = f"""
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>
 <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{SKY0}"/><stop offset="1" stop-color="{SKY1}"/></linearGradient>
 <linearGradient id="meadow" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{MEADOW0}"/><stop offset="1" stop-color="{MEADOW1}"/></linearGradient>
</defs>
<rect width="{W}" height="{H}" fill="url(#sky)"/>
{mountains()}
<rect y="610" width="{W}" height="{H - 610}" fill="url(#meadow)"/>
<path d="M0,626 C200,590 420,640 620,606 C800,576 960,620 1080,596 L1080,660 L0,660 Z" fill="{MEADOW0}"/>
<path d="{road}" fill="none" stroke="{ROAD_EDGE}" stroke-width="40" stroke-linecap="round"/>
<path d="{road}" fill="none" stroke="{ROAD}" stroke-width="34" stroke-linecap="round"/>
{''.join(out)}
<g text-anchor="middle">{''.join(labels)}</g>
</svg>
<div class="hd">
 <div class="k">{T["kicker"]}</div>
 <div class="t">{T["title"]}</div>
 <div class="h">{T["headline"].format(w=week["week_no"], a=a.day, b=b.day, m=month, y=b.year)}</div>
 <div class="n"><b>{wp.fmt(week["added"], lang)}</b> <span>{T["big"]}</span></div>
 <div class="x">{T["explain"]}</div>
</div>
<div class="ft"><b>opencaselaw.ch</b><span>{T["total"].format(t=wp.fmt(week["total"], lang))}</span></div>"""
    css = f"""
body{{background:{SKY1};color:{INK};font-family:'Inter',system-ui,sans-serif}}
.hd{{position:absolute;left:60px;right:60px;top:52px;text-align:center}}
.k{{font-size:15px;font-weight:600;letter-spacing:.24em;text-transform:uppercase;color:{INK_SOFT}}}
.t{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:58px;line-height:1.05;margin-top:8px;letter-spacing:-.01em}}
.h{{font-size:23px;font-weight:500;margin-top:10px;color:{INK_SOFT}}}
.n{{margin-top:2px;line-height:1}}
.n b{{font-family:'Fraunces',Georgia,serif;font-weight:700;font-size:150px;letter-spacing:-.04em;color:{INK}}}
.n span{{font-family:'Fraunces',Georgia,serif;font-weight:500;font-size:44px;letter-spacing:-.01em;margin-left:6px}}
.x{{font-family:'Fraunces',Georgia,serif;font-style:italic;font-size:19.5px;margin:10px auto 0;max-width:760px;text-wrap:balance;color:{INK_SOFT}}}
.num{{font-family:'Fraunces',Georgia,serif;font-weight:700;font-size:28px;fill:{INK}}}
.code{{font-size:13.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;fill:{INK}}}
.ft{{position:absolute;left:60px;right:60px;bottom:38px;display:flex;justify-content:space-between;align-items:baseline;font-size:18px;border-top:1px solid rgba(39,34,28,.35);padding-top:12px;color:{INK}}}
.ft b{{font-family:'Fraunces',Georgia,serif;font-weight:600;font-size:26px}}"""
    return page("family=Fraunces:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Inter:wght@400;500;600;700", css, body, lang)
