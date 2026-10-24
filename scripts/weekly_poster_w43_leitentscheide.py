"""Week 43 (16-22 October 2026), German: the week's new leading decisions.

After Josef Müller-Brockmann's purely typographic posters for Zurich ("Der Film", 1960; the
Tonhalle "musica viva" series): black ground, one grotesk, nothing but type, set on a strict
two-column grid. The week's figure is cut by the top and left edges of the sheet; below it the
13 BGE that reached the corpus this week stand as a wall, by part of the official collection:
III (civil law, Lausanne) and V (social insurance law, Luzern). Each line is the citation string
the API returns (citation_string_de, never built here) and the Regeste's first key word phrase,
verbatim (scripts/weekly_poster_w43_data.json, from get_regeste).

    design(week, lang) -> str      # a complete 1080x1350 HTML page
"""
from __future__ import annotations

import weekly_poster as wp
from weekly_poster_designs import esc, page

PART = {"III": ("III", "Zivilrecht", "Lausanne"), "V": ("V", "Sozialversicherungsrecht", "Luzern")}
MONTH_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
            "Oktober", "November", "Dezember"]
RED = "#ff2a1f"


def design(week: dict, lang: str = "de") -> str:
    n = lambda v: wp.fmt(int(v), "de")  # noqa: E731
    bc, bk = week["by_canton"], week["by_court"]
    fed = bc.get(wp.FEDERAL, 0)
    vd, lu = bk.get("bger@VD", 0), bk.get("bger@LU", 0)
    bge = week["bge"]
    a, b = week["from"], week["to"]

    sections = []
    for part, (num, area, seat) in PART.items():
        rows = [e for e in bge if e["part"] == part]
        if not rows:
            continue
        items = "".join(f'<li><b>{esc(e["de"])}</b><span>{esc(e["topic"].split(";")[0])}</span></li>' for e in rows)
        sections.append(f'<section><h3><span>{num}</span><span>{area}, {seat}</span><span>{len(rows)}</span></h3><ol>{items}</ol></section>')

    body = f"""
<div class="big">{n(week['added'])}</div>
<div class="side"><p class="l1">neue<br>Entscheide.</p></div>
<p class="wk">Woche {week['week_no']} · {a.day}.–{b.day}. {MONTH_DE[b.month - 1]} {b.year}</p>
<p class="l2">Bundesgericht {n(vd + lu)} (Lausanne {n(vd)}, Luzern {n(lu)}) · Bundesverwaltungsgericht {n(bk.get('bvger', 0))} · Bundesstrafgericht {n(bk.get('bstger', 0))} · Kantone {n(week['added'] - fed)}</p>
<h2>{len(bge)} neue Leitentscheide</h2>
<div class="wall">{''.join(sections)}</div>
<footer><b>opencaselaw.ch</b><span>Stichworte wörtlich aus den Regesten.</span></footer>"""

    css = f"""
body{{background:#000;color:#fff;font-family:'Inter Tight','Helvetica Neue',Helvetica,sans-serif}}
.big{{position:absolute;left:-34px;top:-118px;font-size:520px;font-weight:800;line-height:1;letter-spacing:-.075em;color:{RED}}}
.side{{position:absolute;left:760px;right:52px;top:52px}}
.wk{{position:absolute;right:52px;top:344px;font-size:16px;font-weight:600;color:#fff}}
.l1{{font-size:50px;font-weight:800;line-height:.98;margin-top:178px;letter-spacing:-.035em}}
.l2{{position:absolute;left:52px;right:52px;top:372px;font-size:15px;color:#9a9a9a;border-top:2px solid #fff;padding-top:9px}}
h2{{position:absolute;left:52px;top:418px;font-size:26px;font-weight:800;color:{RED};letter-spacing:-.015em}}
.wall{{position:absolute;left:52px;right:52px;top:466px}}
section+section{{margin-top:14px}}
h3{{display:grid;grid-template-columns:56px 1fr auto;font-size:13px;font-weight:600;color:#9a9a9a;letter-spacing:.07em;text-transform:uppercase;
  border-bottom:1px solid #3a3a3a;padding-bottom:5px}}
h3 span:first-child{{color:#fff;font-weight:800}}
ol{{list-style:none}}
li{{display:grid;grid-template-columns:480px 1fr;align-items:center;column-gap:22px;padding:4px 0 5px;border-bottom:1px solid #222}}
li b{{font-size:44px;font-weight:800;letter-spacing:-.035em;line-height:1;white-space:nowrap}}
li span{{font-size:15px;line-height:1.22;color:#c2c2c2}}
footer{{position:absolute;left:52px;right:52px;bottom:34px;display:flex;justify-content:space-between;align-items:baseline;font-size:13px;color:#8c8c8c}}
footer b{{font-size:22px;font-weight:800;color:#fff}}"""
    return page("family=Inter+Tight:wght@400;600;800", css, body, "de")
