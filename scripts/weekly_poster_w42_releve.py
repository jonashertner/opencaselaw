"""Week 42 poster, French edition: a relevé de compte, in the Geneva private-banking manner.

The account is «Jurisprudence suisse». The statement lists one credit line per court or canton for the
week (largest first), subtotals for the Confederation and the cantons, the week's total movements and the
balance at the close of the period. The grammar of a statement (account, period, reference, unit) is kept
only where it carries data: the reference is the ISO week, the unit is the decision. No fees, no debits,
no bank. The one comparison allowed is a footnote on Ticino against its usual week.

    design(week, "fr") -> a complete 1080x1350 HTML page
"""
from __future__ import annotations

from datetime import date

import weekly_poster as wp
from weekly_poster_designs import esc, page

FED = wp.FEDERAL
NNBSP = " "  # espace fine insécable, before ; : ? !

MONTH_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"]
CANTON_FR = {"GE": "Genève", "ZH": "Zurich", "VD": "Vaud", "TI": "Tessin", "AG": "Argovie", "BE": "Berne",
             "FR": "Fribourg", "BS": "Bâle-Ville", "BL": "Bâle-Campagne", "SG": "Saint-Gall", "ZG": "Zoug",
             "SZ": "Schwyz", "GR": "Grisons", "JU": "Jura", "NE": "Neuchâtel", "SO": "Soleure", "UR": "Uri",
             "LU": "Lucerne", "TG": "Thurgovie", "VS": "Valais", "SH": "Schaffhouse", "AR": "Appenzell Rh.-Ext.",
             "AI": "Appenzell Rh.-Int.", "GL": "Glaris", "OW": "Obwald", "NW": "Nidwald"}

# The federal side, as a reader names the courts; code = the statement's reference column.
FEDERAL_LINES = (
    ("TF", "Tribunal fédéral, Lausanne", ("bger@VD",)),
    ("TF", "Tribunal fédéral, Lucerne", ("bger@LU",)),
    ("TAF", "Tribunal administratif fédéral", ("bvger",)),
    ("TPF", "Tribunal pénal fédéral", ("bstger",)),
)


def day_fr(d: date, with_year: bool = True) -> str:
    n = "1er" if d.day == 1 else str(d.day)
    return f"{n} {MONTH_FR[d.month - 1]}" + (f" {d.year}" if with_year else "")


def period_fr(a: date, b: date) -> str:
    if a.month == b.month and a.year == b.year:
        first = "1er" if a.day == 1 else str(a.day)
        return f"du {first} au {day_fr(b)}"
    return f"du {day_fr(a, with_year=a.year != b.year)} au {day_fr(b)}"


def federal_lines(week: dict) -> list[tuple[str, str, int]]:
    """The Confederation's credit lines; the remainder covers the other federal sources in by_canton['CH']."""
    by_court = week["by_court"]
    lines = [(code, name, sum(by_court.get(k, 0) for k in keys)) for code, name, keys in FEDERAL_LINES]
    rest = week["by_canton"][FED] - sum(n for _, _, n in lines)
    assert rest >= 0, "federal court lines exceed the federal total"
    lines.append(("", "Autres instances fédérales, CourEDH, Tribunal du sport", rest))
    assert sum(n for _, _, n in lines) == week["by_canton"][FED]
    return [ln for ln in lines if ln[2] > 0]


def design(week: dict, lang: str = "fr") -> str:
    f = lambda n: wp.fmt(int(n), lang)  # noqa: E731
    a, b = week["from"], week["to"]
    added, total = int(week["added"]), int(week["total"])

    fed = federal_lines(week)
    fed_total = week["by_canton"][FED]
    cantons = sorted(((k, n) for k, n in week["by_canton"].items() if k != FED and n > 0), key=lambda kv: (-kv[1], kv[0]))
    cant_total = sum(n for _, n in cantons)
    assert fed_total + cant_total == added, f"{fed_total} + {cant_total} != {added}"

    # The only comparison shown: Ticino against the median of the previous eight weeks.
    ti_now = week["by_canton"].get("TI", 0)
    ti_usual = week["usual_canton"].get("TI")
    note_key = "TI" if ti_now and ti_usual else None

    def row(code: str, label: str, n: int, mark: str = "") -> str:
        return (f'<div class="r"><span class="c">{esc(code)}</span><span class="l">{esc(label)}{mark}</span>'
                f'<span class="n">{f(n)}</span></div>')

    def section(title: str) -> str:
        return f'<div class="sec"><span>{esc(title)}</span><span>Crédit</span></div>'

    def subtotal(label: str, n: int) -> str:
        return f'<div class="r sub"><span class="c"></span><span class="l">{esc(label)}</span><span class="n">{f(n)}</span></div>'

    rows = [section("Confédération")]
    rows += [row(c, name, n) for c, name, n in fed]
    rows.append(subtotal("Sous-total Confédération", fed_total))
    rows.append(section("Cantons"))
    for k, n in cantons:
        mark = '<sup>1</sup>' if k == note_key else ""
        rows.append(row(k, CANTON_FR.get(k, k), n, mark))
    rows.append(subtotal("Sous-total cantons", cant_total))

    note = ""
    if note_key:
        note = (f'<p class="note"><sup>1</sup> Tessin{NNBSP}: {f(ti_now)} décisions cette semaine contre {f(round(ti_usual))} '
                f'une semaine habituelle (médiane des huit semaines précédentes).</p>')

    body = f"""
<div class="sheet">
  <header>
    <div class="who">
      <b>OpenCaseLaw</b>
      <span>jurisprudence suisse en libre accès</span>
    </div>
    <div class="meta">
      <span>Compte</span><span>Jurisprudence suisse</span>
      <span>Période</span><span>{period_fr(a, b)}</span>
      <span>Référence</span><span>{a.year}-S{week['week_no']:02d}</span>
      <span>Unité</span><span>décisions publiées</span>
    </div>
  </header>
  <h1>Relevé de compte</h1>

  <div class="sum">
    <div class="mv">
      <span class="k">Total des mouvements de la semaine</span>
      <span class="big">{f(added)}</span>
      <span class="u">nouvelles décisions</span>
    </div>
    <div class="bal">
      <div><span class="k">Solde au {day_fr(b)}</span><span class="v">{f(total)}</span><span class="u">décisions, de 1875 à ce jour</span></div>
      <div class="split"><span><i>{f(fed_total)}</i> Confédération</span><span><i>{f(cant_total)}</i> cantons</span></div>
    </div>
  </div>

  <div class="tbl">
    {''.join(rows)}
    <div class="r tot"><span class="c"></span><span class="l">Total des mouvements de la semaine</span><span class="n">{f(added)}</span></div>
  </div>
  {note}

  <footer>
    <span><b>opencaselaw.ch</b></span>
    <span>Données en libre accès (CC0)</span>
  </footer>
</div>"""

    css = """
body{background:#dcdad4;font-family:'Inter',Helvetica,Arial,sans-serif;color:#17191c;font-feature-settings:'tnum' 1,'cv11' 1}
.sheet{position:absolute;left:48px;top:36px;width:984px;height:1278px;background:#fff;padding:44px 60px 0;
  box-shadow:0 1px 2px rgba(0,0,0,.08),0 14px 34px rgba(30,30,25,.14)}
header{display:flex;justify-content:space-between;align-items:flex-start}
.who b{display:block;font-size:22px;font-weight:600;letter-spacing:-.01em}
.who span{display:block;font-size:14px;color:#6a6e74;margin-top:5px}
.meta{display:grid;grid-template-columns:auto auto;column-gap:22px;row-gap:3px;font-size:14px;line-height:1.35}
.meta span:nth-child(odd){color:#6a6e74}
.meta span:nth-child(even){text-align:right;font-weight:500}
h1{font-size:34px;font-weight:400;letter-spacing:-.02em;margin-top:18px;padding-bottom:12px;border-bottom:1.5px solid #17191c}
.sum{display:grid;grid-template-columns:1fr 372px;border-bottom:1px solid #17191c}
.mv{padding:14px 0 12px;border-right:1px solid #cfd2d6}
.bal{padding:14px 0 12px 34px;display:flex;flex-direction:column;justify-content:space-between}
.k{display:block;font-size:13.5px;letter-spacing:.09em;text-transform:uppercase;color:#6a6e74}
.big{display:block;font-size:164px;line-height:.88;font-weight:300;letter-spacing:-.05em;margin:10px 0 0 -7px}
.mv .u{display:block;font-size:17px;color:#3c4046;margin-top:8px}
.bal .v{display:block;font-size:52px;font-weight:400;letter-spacing:-.025em;line-height:1.05;margin-top:12px}
.bal .u{display:block;font-size:15px;color:#3c4046;margin-top:6px}
.split{display:flex;gap:28px;font-size:15px;color:#3c4046;padding-top:12px;border-top:1px solid #e1e3e6}
.split i{font-style:normal;font-weight:600;color:#17191c;font-size:20px;margin-right:6px}
.tbl{margin-top:4px;font-size:16px}
.sec{display:flex;justify-content:space-between;font-size:12.5px;letter-spacing:.09em;text-transform:uppercase;color:#6a6e74;
  padding:12px 0 5px;border-bottom:1px solid #17191c}
.r{display:grid;grid-template-columns:62px 1fr 120px;align-items:baseline;padding:3px 0;border-bottom:1px solid #e6e8ea;line-height:1.2}
.r .c{font-size:13px;letter-spacing:.06em;color:#6a6e74}
.r .n{text-align:right}
.r sup{font-size:11px;color:#6a6e74;margin-left:1px}
.r.sub{font-weight:500;border-bottom:0;padding-top:5px}
.r.sub .l{color:#3c4046;font-weight:400}
.r.tot{margin-top:10px;border-top:1.5px solid #17191c;border-bottom:0;padding-top:9px;font-weight:600;font-size:18px}
.note{font-size:14px;line-height:1.4;color:#3c4046;margin-top:14px}
.note sup{font-size:10px;margin-right:2px}
footer{position:absolute;left:60px;right:60px;bottom:30px;display:flex;justify-content:space-between;align-items:baseline;gap:30px;
  border-top:1px solid #cfd2d6;padding-top:10px;font-size:13.5px;color:#6a6e74}
footer b{font-size:18px;font-weight:600;color:#17191c}"""
    fonts = "family=Inter:wght@300;400;500;600"
    return page(fonts, css, body, lang)
