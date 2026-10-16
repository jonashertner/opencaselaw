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
    sup = '<span class="sup">1</span>'

    def row(code: str, label: str, n: int, mark: str = "", last: bool = False) -> str:
        return (f'<div class="r{" last" if last else ""}"><span class="c">{esc(code)}</span><span class="l">{esc(label)}{mark}</span>'
                f'<span class="n">{f(n)}</span></div>')

    def section(title: str) -> str:
        return f'<div class="sec"><span>{esc(title)}</span><span>Crédit</span></div>'

    def subtotal(label: str, n: int) -> str:
        return f'<div class="r sub"><span class="c"></span><span class="l">{esc(label)}</span><span class="n">{f(n)}</span></div>'

    rows = [section("Confédération")]
    rows += [row(c, name, n, last=i == len(fed) - 1) for i, (c, name, n) in enumerate(fed)]
    rows.append(subtotal("Sous-total Confédération", fed_total))
    rows.append(section("Cantons"))
    for i, (k, n) in enumerate(cantons):
        rows.append(row(k, CANTON_FR.get(k, k), n, sup if k == note_key else "", last=i == len(cantons) - 1))
    rows.append(subtotal("Sous-total cantons", cant_total))

    note = ""
    if note_key:
        note = (f'<p class="note">{sup} Tessin{NNBSP}: {f(ti_now)} décisions cette semaine contre {f(round(ti_usual))} '
                f'une semaine habituelle (médiane des huit semaines précédentes).</p>')

    meta = (("Compte", "Jurisprudence suisse"), ("Période", period_fr(a, b)),
            ("Référence", f"{a.year}-S{week['week_no']:02d}"), ("Unité", "décisions publiées"))
    meta_html = "".join(f'<span class="ml">{k}{NNBSP}:</span><span class="mv">{esc(v)}</span>' for k, v in meta)

    body = f"""
<div class="sheet">
  <header>
    <div class="who"><b>OpenCaseLaw</b><h1>Relevé de compte</h1></div>
    <div class="meta">{meta_html}</div>
  </header>

  <div class="sum">
    <span class="k">Total des mouvements de la semaine</span>
    <span class="k r2">Solde au {day_fr(b)}</span>
    <span class="big">{f(added)}</span>
    <span class="v r2">{f(total)}</span>
    <span class="u">nouvelles décisions</span>
    <span class="u r2">décisions, de 1875 à ce jour</span>
    <div class="split r2"><span>Confédération <b>{f(fed_total)}</b></span><span>Cantons <b>{f(cant_total)}</b></span></div>
  </div>

  <div class="tbl">
    {''.join(rows)}
    <div class="r tot"><span class="c"></span><span class="l">Total des mouvements de la semaine</span><span class="n">{f(added)}</span></div>
  </div>
  {note}

  <footer>
    <span><b>opencaselaw.ch</b></span>
    <span>Jurisprudence suisse en libre accès · données CC0</span>
  </footer>
</div>"""

    css = """
:root{--ink:#17191c;--grey:#6b6f75;--mid:#3b3f45;--hair:#cfd2d6;--w:864px;--num:120px;--right:372px}
body{background:#dcdad4;font-family:'Inter',Helvetica,Arial,sans-serif;color:var(--ink);font-feature-settings:'tnum' 1,'cv11' 1}
.sheet{position:absolute;left:48px;top:48px;width:984px;height:1254px;background:#fff;padding:46px 60px 0;
  box-shadow:0 0.5px 1.5px rgba(0,0,0,.07),0 6px 16px rgba(30,30,25,.09)}
header{display:flex;justify-content:space-between;align-items:flex-end;padding-bottom:16px;border-bottom:1px solid var(--ink)}
.who b{display:block;font-size:15px;font-weight:600;letter-spacing:.02em}
h1{font-size:36px;font-weight:400;letter-spacing:-.02em;line-height:1.1;margin-top:12px}
.meta{display:grid;grid-template-columns:104px auto;row-gap:3px;font-size:14.5px;line-height:1.3;align-self:flex-start}
.ml{color:var(--grey)}
.mv{color:var(--ink)}
.sum{position:relative;display:grid;grid-template-columns:1fr var(--right);align-items:baseline;padding:14px 0 12px;border-bottom:1px solid var(--ink)}
.sum::before{content:'';position:absolute;top:0;bottom:0;right:var(--right);width:0.5px;background:var(--hair)}
.sum .r2{grid-column:2;padding-left:34px}
.k{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--grey)}
.big{font-size:164px;line-height:.78;font-weight:300;letter-spacing:-.05em;margin:12px 0 0 -7px}
.v{font-size:54px;font-weight:400;letter-spacing:-.025em;line-height:.8}
.u{font-size:16.5px;color:var(--mid);margin-top:8px}
.split{display:flex;justify-content:space-between;align-items:baseline;margin-top:12px;padding-top:7px;border-top:0.5px solid var(--hair);font-size:16px;color:var(--mid)}
.split b{font-weight:600;color:var(--ink);font-size:17.5px;margin-left:4px}
.tbl{margin-top:6px;font-size:17.5px}
.sec{display:flex;justify-content:space-between;font-size:12.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--grey);
  padding:11px 0 4px;border-bottom:0.5px solid var(--ink)}
.r{display:grid;grid-template-columns:62px 1fr var(--num);align-items:baseline;padding:2.5px 0;border-bottom:0.5px solid var(--hair);line-height:1.2}
.r .c{font-size:12.5px;letter-spacing:.1em;color:var(--grey);text-transform:uppercase}
.r .n{text-align:right}
.r.last{border-bottom:0}
.r.sub{border-top:0.5px solid var(--ink);border-bottom:0;padding-top:4px}
.r.sub .l{color:var(--mid)}
.r.sub .n{font-weight:500}
.r.tot{margin-top:10px;border-top:3px double var(--ink);border-bottom:0;padding-top:10px;font-weight:600;font-size:18.5px}
.sup{font-size:62%;line-height:0;position:relative;top:-.6em;margin-left:1px;color:var(--grey)}
.note{font-size:14px;line-height:1.4;color:var(--mid);margin-top:11px}
.note .sup{margin:0 2px 0 0}
footer{position:absolute;left:60px;right:60px;bottom:30px;display:flex;justify-content:space-between;align-items:baseline;gap:30px;
  border-top:0.5px solid var(--hair);padding-top:10px;font-size:13.5px;color:var(--grey)}
footer b{font-size:17px;font-weight:600;color:var(--ink)}"""
    fonts = "family=Inter:wght@300;400;500;600"
    return page(fonts, css, body, lang)
