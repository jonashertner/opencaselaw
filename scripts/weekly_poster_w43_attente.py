"""Week 43 (16-22 October 2026), French: the wait, one line per decision.

Every new decision of the week is one hairline, drawn from the day it was handed down to the
end of the week it was published (22 October, the right edge). Sorted by age, the lines form a
single shape: the newest at the top are short ticks at the right, the oldest at the bottom
reach back to January 2025. Court holidays (art. 46 LTF) stand behind as vertical bands; three
blue rules mark the shares older than six weeks, four months and ten months when they arrived. The figure is set
in the same hairlines, after the grid-built lettering of Wim Crouwel's "Vormgevers" (Stedelijk, 1968).

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

from datetime import date, timedelta

import weekly_poster as wp
from weekly_poster_designs import page

MONTH_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
            "octobre", "novembre", "décembre"]
MON_SHORT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
INK, BLUE, GRID = "#000000", "#1537ff", "#e6e6e6"
XL, XR, YT, YB = 60, 1020, 70, 1222


def ferias(year: int) -> list[tuple[date, date]]:
    """Art. 46 al. 1 LTF: 7 days before to 7 days after Easter, 15 July-15 August, 18 December-2 January."""
    a = year % 19; b, c = divmod(year, 100); d, e = divmod(b, 4); f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30; i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    easter = date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)
    return [(easter - timedelta(days=7), easter + timedelta(days=7)),
            (date(year, 7, 15), date(year, 8, 15)), (date(year, 12, 18), date(year + 1, 1, 2)),
            (date(year - 1, 12, 18), date(year, 1, 2))]


def design(week: dict, lang: str = "fr") -> str:
    n = lambda v: wp.fmt(int(v), "fr")  # noqa: E731
    end, a = week["to"], week["from"]
    # one line per decision, from its decision date to the day it entered the corpus, sorted by wait
    rows = sorted(((date.fromisoformat(d), date.fromisoformat(s)) for d, s in week["arrivals"]),
                  key=lambda t: ((t[1] - t[0]).days, t[0]))
    lags = [(s - d).days for d, s in rows]
    total = len(lags)
    first = date(end.year - 1, 1, 1)                     # the axis starts on 1 January of last year
    older = [d for d, _ in rows if d < first]
    span = (end - first).days
    px = lambda lag: max(XL, XR - lag / span * (XR - XL))  # noqa: E731  lag = days before the end of the week
    pitch = (YB - YT) / total

    path = "".join(f"M{px((end - d).days):.1f} {YT + i * pitch + pitch / 2:.2f}H{px((end - s).days):.1f}"
                   for i, (d, s) in enumerate(rows))

    bands, ticks = [], []
    for y in range(first.year, end.year + 1):
        for s, e in ferias(y):
            s, e = max(s, date(first.year, first.month, 1)), min(e, end)
            if s <= e:
                bands.append(f'<rect x="{px((end - s).days):.1f}" y="{YT - 8}" width="{(e - s).days / span * (XR - XL):.1f}" '
                             f'height="{YB - YT + 16}" fill="url(#hatch)"/>')
    d = date(first.year, first.month, 1)
    while d <= end:
        x = px((end - d).days)
        ticks.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{YT - 8}" y2="{YB + 8}" stroke="{GRID}" stroke-width="1"/>')
        label = MON_SHORT[d.month - 1] + (f" {d.year}" if d.month == 1 else "")
        if d.month in (1, 4, 7, 10):
            ticks.append(f'<text x="{x + 3:.1f}" y="{YB + 26}" class="mt">{label}</text>')
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)

    marks = []
    for days, text in ((42, "plus de six semaines"), (122, "plus de quatre mois"), (304, "plus de dix mois")):
        k = sum(1 for l in lags if l <= days)
        share = round((total - k) / total * 100)
        y = YT + k * pitch
        x = px((end - rows[min(k, total - 1)][0]).days)
        marks.append(f'<line x1="{XL}" x2="{x:.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{BLUE}" stroke-width="2"/>'
                     f'<text x="{XL}" y="{y - 10:.1f}" class="fq">{share}&#8239;%</text>'
                     f'<text x="{XL + 92}" y="{y - 11:.1f}" class="ft">{text}</text>')

    k42 = sum(1 for l in lags if l <= 42)
    note_y = YT + k42 * pitch + 22
    figure_txt = wp.fmt(week["added"], "fr")
    old = min(d for d, _ in rows)
    oldest = (f"← {n(len(older))} plus anciennes, la première du {old.day} {MONTH_FR[old.month - 1]} {old.year}" if older
              else f"la plus ancienne&#8239;: {old.day} {MONTH_FR[old.month - 1]} {old.year}")
    body = f"""
<svg width="1080" height="1350" viewBox="0 0 1080 1350">
<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect width="5" height="5" fill="#fff"/><line x1="0" y1="0" x2="0" y2="5" stroke="#d6d6d6" stroke-width="2"/></pattern>
<pattern id="lines" width="8" height="{pitch * 2:.3f}" patternUnits="userSpaceOnUse"><rect width="8" height="{pitch * 1.25:.3f}" fill="{INK}"/></pattern></defs>
{''.join(bands)}{''.join(ticks)}
<path d="{path}" stroke="{INK}" stroke-width="{max(pitch * .62, .8):.2f}" fill="none"/>
<text x="{XL - 10}" y="420" class="fig" fill="url(#lines)">{figure_txt}</text>
<text x="{XL + 4}" y="{YB - 46}" class="old">{oldest}</text>
{''.join(marks)}
<text x="{XR}" y="{YT - 18}" text-anchor="end" class="mt pub">entrée dans le corpus ↓</text>
</svg>
<p class="hd">Semaine {week['week_no']} · du {a.day} au {end.day} {MONTH_FR[end.month - 1]} {end.year}</p>
<p class="lede">nouvelles décisions. Chacune est un trait, du jour où elle a été rendue au jour où elle est entrée dans le corpus.</p>
<p class="note">Bandes hachurées&#8239;: féries judiciaires (art. 46 LTF).</p>
<footer><b>opencaselaw.ch</b><span>La jurisprudence suisse, libre et gratuite</span></footer>"""

    css = f"""
body{{background:#fff;color:{INK};font-family:'Space Grotesk','Helvetica Neue',sans-serif}}
.hd{{position:absolute;left:{XL}px;top:62px;font-size:19px;font-weight:600}}
.lede{{position:absolute;left:{XL}px;width:560px;top:444px;font-size:26px;line-height:1.18;font-weight:500}}
.fig{{font-family:'Inter Tight',sans-serif;font-size:410px;font-weight:800;letter-spacing:-.06em}}
.old{{font-size:14px;font-weight:600;fill:{INK};paint-order:stroke;stroke:#fff;stroke-width:5px;stroke-linejoin:round}}
.fq{{font-size:34px;font-weight:700;fill:{BLUE}}}.ft{{font-size:17px;font-weight:600;fill:{BLUE}}}
.mt{{font-size:13px;fill:#777}}.pub{{fill:{INK};font-weight:700;font-size:15px}}
.note{{position:absolute;left:{XL}px;width:440px;top:{note_y:.0f}px;font-size:14px;line-height:1.4;color:#555}}
footer{{position:absolute;left:{XL}px;right:60px;bottom:36px;display:flex;justify-content:space-between;align-items:baseline;
  border-top:3px solid {INK};padding-top:10px;font-size:17px}}
footer b{{font-size:28px;font-weight:700}}"""
    return page("family=Space+Grotesk:wght@400;500;600;700&family=Inter+Tight:wght@800", css, body, "fr")
