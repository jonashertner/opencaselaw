"""Second set of weekly poster designs, one per site language (the library rotates weekly).

  de  board      Ankünfte: a split-flap arrivals board, one row per court.
  fr  rose       La rose de la semaine: a Nightingale coxcomb, one petal per night.
  it  mosaic     Mosaico: Switzerland laid in tesserae, one per decision.
  rm  sgraffito  Sgraffito: the week as Engadin facade ornament, one band per night.
  en  forecast   The Case-Law Forecast: the week read out like the shipping forecast.

Each takes (delta, cur, lang) like the designs in weekly_poster_editions.py.
"""
from __future__ import annotations

import html
import math
import random
import re
from datetime import timedelta

import weekly_poster as wp

W, H = wp.W, wp.H
FED = wp.FEDERAL


def esc(s) -> str:
    return html.escape(str(s))


def region_of(code: str) -> str:
    return "ch" if code == FED else "fr" if code in wp.FRENCH else "it" if code in wp.ITALIAN else "de"


def page(fonts: str, css: str, body: str, lang: str) -> str:
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?{fonts}&display=swap" rel="stylesheet">
<style>*{{box-sizing:border-box;margin:0;padding:0}}html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{position:relative;font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased}}
svg{{position:absolute;inset:0}}{css}</style></head><body>{body}</body></html>"""


def first_day(delta: dict):
    """The first day of the week shown: the baseline is the previous day's last snapshot."""
    return delta["from"] + timedelta(days=1)


REFILED_NOTE = {
    "de": "{k}: {n} bereits erfasste Entscheide wurden dem Gericht zugeordnet, das sie gefällt hat; nicht als neu gezählt.",
    "fr": "{k}\u202f: {n} décisions déjà publiées ont été rattachées au tribunal qui les a rendues\u202f; non comptées comme nouvelles.",
    "it": "{k}: {n} decisioni già presenti sono state riattribuite al tribunale che le ha pronunciate; non contate come nuove.",
    "rm": "{k}: {n} decisiuns gia registradas èn vegnidas attribuidas a la dretgira che las ha prendidas.",
    "en": "{k}: {n} rulings already in the corpus were re-filed under the court that decided them; not counted as new.",
}


def refiled_note(delta: dict, lang: str) -> str:
    """One sentence per canton whose re-filed rulings the weekly rule left out (see weekly_delta)."""
    return " ".join(REFILED_NOTE[lang].format(k=k, n=wp.fmt(n, lang)) for k, n in sorted((delta.get("refiled") or {}).items()))


def nights(delta: dict) -> list:
    return sorted(delta["by_day"])


def week_no(delta: dict) -> int:
    return delta["to"].isocalendar().week


# ── de: Ankünfte ──────────────────────────────────────────────────────────────

FEDERAL_DE = {"bger": "Bundesgericht", "bge": "BGE amtliche Sammlung", "bvger": "Bundesverwaltungsgericht",
              "bstger": "Bundesstrafgericht", "bpatger": "Bundespatentgericht", "ta_sst": "Schweizer Sportgericht",
              "edoeb": "EDÖB", "weko": "WEKO", "finma": "FINMA", "finma_versicherungsrecht": "FINMA Versicherung",
              "ubi": "UBI", "elcom": "ElCom", "postcom": "PostCom", "comcom": "ComCom", "esbk": "ESBK",
              "bazg": "BAZG", "emark": "EMARK", "hudoc_ch": "EGMR (Schweiz)", "bge_egmr": "EGMR in der BGE",
              "ch_bundesrat": "Bundesrat", "mkg": "Militärkassationsgericht"}
WORDS = {"gerichte": "Gerichte", "zuerich": "Zürich", "buelach": "Bülach", "zivilstraf": "Zivil- und Strafgericht",
         "omni": "Gerichte", "findinfo": "Gerichte", "bvd": "Bau- und Verkehrsdirektion"}
WEEKDAY_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
CANTON_DE = {"AG": "Aargau", "AI": "Appenzell I.Rh.", "AR": "Appenzell A.Rh.", "BE": "Bern", "BL": "Basel-Landschaft",
             "BS": "Basel-Stadt", "FR": "Freiburg", "GE": "Genf", "GL": "Glarus", "GR": "Graubünden", "JU": "Jura",
             "LU": "Luzern", "NE": "Neuenburg", "NW": "Nidwalden", "OW": "Obwalden", "SG": "St. Gallen",
             "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz", "TG": "Thurgau", "TI": "Tessin", "UR": "Uri",
             "VD": "Waadt", "VS": "Wallis", "ZG": "Zug", "ZH": "Zürich"}
CELLS = (8, 26, 2, 5)  # columns of the board, in flaps


def court_name_de(code: str) -> str:
    base, _, seat = code.partition("@")
    if base in FEDERAL_DE:
        name = FEDERAL_DE[base]
        return name + {"LU": " Luzern", "VD": " Lausanne"}.get(seat, "")
    m = re.match(r"^([a-z]{2})_(.+)$", base)
    if not m:
        return base
    if m.group(2) in ("gerichte", "omni", "findinfo", "publikationen"):  # a canton's courts as one source
        return CANTON_DE.get(m.group(1).upper(), m.group(1).upper())
    words = [WORDS.get(w, w.capitalize()) for w in m.group(2).split("_")]
    return " ".join(words)


def flaps(text: str, cells: int, align: str = "left") -> str:
    text = text[:cells]
    text = text.rjust(cells) if align == "right" else text.ljust(cells)
    return "".join(f'<i>{esc(ch) if ch != " " else "&nbsp;"}</i>' for ch in text)


# Swiss station-board grammar (blue field, Ankunft / Von / Gleis, category badges, a "via" line,
# yellow notices). Deliberately without the SBB logo, the station clock and the SBB typeface.
SEAT_CITY = {"AG": "Aarau", "AI": "Appenzell", "AR": "Trogen", "BE": "Bern", "BL": "Liestal", "BS": "Basel",
             "FR": "Fribourg", "GE": "Genève", "GL": "Glarus", "GR": "Chur", "JU": "Delémont", "LU": "Luzern",
             "NE": "Neuchâtel", "NW": "Stans", "OW": "Sarnen", "SG": "St. Gallen", "SH": "Schaffhausen",
             "SO": "Solothurn", "SZ": "Schwyz", "TG": "Frauenfeld", "TI": "Lugano", "UR": "Altdorf",
             "VD": "Lausanne", "VS": "Sion", "ZG": "Zug", "ZH": "Zürich"}
FED_CITY = {"ta_sst": "Schweiz", "bger": "Lausanne", "bger@VD": "Lausanne", "bger@LU": "Luzern", "bge": "Lausanne", "bvger": "St. Gallen",
            "bstger": "Bellinzona", "bpatger": "St. Gallen"}
BADGE = {"bger": "BGer", "bge": "BGE", "bvger": "BVGer", "bstger": "BStGer", "bpatger": "BPatGer", "ta_sst": "SST",
         "obergericht": "OG", "verwaltungsgericht": "VG", "sozialversicherungsgericht": "SVG", "handelsgericht": "HG",
         "bezirksgericht": "BG", "kantonsgericht": "KG", "strafgericht": "SG", "zivilgericht": "ZG",
         "versicherungsgericht": "VsG", "steuerrekursgericht": "StRG", "appellationsgericht": "AppG",
         "arbeitsgericht": "AG", "mietgericht": "MG", "zivilstraf": "OG"}


# Court codes inside multi-court cantonal sources, named as each decision's own header names the court
# (checked 2026-10-09 against one recent decision per code). Codes not listed fold into the canton row.
CHAMBER_NAMES = {
    "ge_gerichte": {
        "ACJC": ("ACJC", "Cour de justice, Chambre civile"), "ATAS": ("ATAS", "Cour de justice, Chambre des assurances sociales"),
        "ATA": ("ATA", "Cour de justice, Chambre administrative"), "ACST": ("ACST", "Cour de justice, Chambre constitutionnelle"),
        "ACPR": ("ACPR", "Cour de justice, Chambre pénale de recours"),
        "AARP": ("AARP", "Cour de justice, Chambre pénale d'appel et de révision"),
        "DCSO": ("DCSO", "Cour de justice, Chambre de surveillance des offices des poursuites et faillites"),
        "DAS": ("DAS", "Cour de justice, Chambre de surveillance"), "DAAJ": ("DAAJ", "Cour de justice, Assistance judiciaire"),
        "JTAPI": ("JTAPI", "Tribunal administratif de première instance"),
        "DITAI": ("JTAPI", "Tribunal administratif de première instance"),
        "JTDP": ("JTDP", "Tribunal de police"), "JTCO": ("JTCO", "Tribunal correctionnel"), "JTCR": ("JTCR", "Tribunal criminel"),
    },
    "vd_gerichte": {
        "CASSO": ("CASSO", "Cour des assurances sociales"), "CREP": ("CREP", "Chambre des recours pénale"),
        "CACI": ("CACI", "Cour d'appel civile"), "CAPE": ("CAPE", "Cour d'appel pénale"),
        "CPF": ("CPF", "Cour des poursuites et faillites"), "CREC": ("CREC", "Chambre des recours civile"),
        "CCUR": ("CCUR", "Chambre des curatelles"), "CA": ("CA", "Cour administrative"), "CCIV": ("CCIV", "Cour civile"),
        "TARB": ("TARB", "Tribunal arbitral des assurances"), "CAVO": ("CAVO", "Chambre des avocats"),
        "TPRAC": ("TPRAC", "Tribunal de prud'hommes de l'administration cantonale"),
    },
    "ne_gerichte": {
        "CDP": ("CDP", "Cour de droit public"), "ARMP": ("ARMP", "Autorité de recours en matière pénale"),
        "ARMC": ("ARMC", "Autorité de recours en matière civile"), "CACIV": ("CACIV", "Cour d'appel civile"),
        "CMPEA": ("CMPEA", "Cour des mesures de protection de l'enfant et de l'adulte"), "CPEN": ("CPEN", "Cour pénale"),
        "CCIV": ("CCIV", "Cour civile"),
    },
    "ju_gerichte": {
        "ADM": ("ADM", "Cour administrative"), "ASS": ("ASS", "Cour des assurances"), "CC": ("CC", "Cour civile"),
        "CPF": ("CPF", "Cour des poursuites et faillites"), "CP": ("CP", "Cour pénale"),
        "CPR": ("CPR", "Chambre pénale des recours"), "CON": ("CST", "Cour constitutionnelle"),
        "TPI": ("TPI", "Tribunal de première instance"), "CIV": ("TPI", "Tribunal de première instance"),
        "CA": ("TPI", "Tribunal de première instance"),
    },
    "so_gerichte": {
        "Verwaltungsgericht": ("VG", "Verwaltungsgericht"), "VWBES": ("VG", "Verwaltungsgericht"),
        "Versicherungsgericht": ("VsG", "Versicherungsgericht"), "VSBES": ("VsG", "Versicherungsgericht"),
        "VSGES": ("VsG", "Versicherungsgericht"),
        "Zivilkammer": ("OG", "Obergericht, Zivilkammer"), "ZKBES": ("OG", "Obergericht, Zivilkammer"),
        "ZKBER": ("OG", "Obergericht, Zivilkammer"), "ZKERL": ("OG", "Obergericht, Zivilkammer"),
        "ZKEIV": ("OG", "Obergericht, Zivilkammer"), "OGBES": ("OG", "Obergericht, Zivilkammer"),
        "Obergericht": ("OG", "Obergericht"), "Strafkammer": ("OG", "Obergericht, Strafkammer"),
        "Beschwerdekammer": ("OG", "Obergericht, Beschwerdekammer"), "BKBES": ("OG", "Obergericht, Beschwerdekammer"),
        "Schuldbetreibungs- und Konkurskammer": ("OG", "Obergericht, Schuldbetreibungs- und Konkurskammer"),
        "SCBES": ("OG", "Obergericht, Schuldbetreibungs- und Konkurskammer"),
        "Steuergericht": ("StG", "Steuergericht"),
    },
}


def fold_chambers(by_court: dict) -> dict:
    """Merge court-code rows that name the same court; unknown codes go back to the canton row."""
    out: dict = {}
    for court, n in by_court.items():
        base, _, code = court.partition("#")
        if code:
            named = CHAMBER_NAMES.get(base, {}).get(code)
            key = f"{base}#{named[1]}" if named else base
        else:
            key = court
        out[key] = out.get(key, 0) + n
    return out


def board_row(court: str, canton: str) -> tuple[str, str, str, bool]:
    """(badge, city, via line, federal) for one court."""
    if "#" in court:  # a named court inside a multi-court cantonal source
        base, _, name = court.partition("#")
        badge = next((b for b, nm in CHAMBER_NAMES.get(base, {}).values() if nm == name), canton)
        return badge, SEAT_CITY.get(canton, canton), f"{name}, Kanton {CANTON_DE.get(canton, canton)}", False
    base, _, seat = court.partition("@")
    if canton == FED:
        city = FED_CITY.get(court, FED_CITY.get(base, "Bern"))
        via = {"bger@LU": "Bundesgericht, sozialrechtliche Abteilungen", "bger@VD": "Bundesgericht"}.get(court, court_name_de(court))
        return BADGE.get(base, base.upper()[:5]), city, via, True
    parts = base.split("_", 1)[1].split("_") if "_" in base else [base]
    city = SEAT_CITY.get(canton, canton)
    if parts[0] == "bezirksgericht" and len(parts) > 1:
        city = WORDS.get(parts[1], parts[1].capitalize())
    badge = BADGE.get(parts[0], canton)
    name = court_name_de(court)
    via = (f"Gerichte des Kantons {CANTON_DE.get(canton, canton)}" if name == CANTON_DE.get(canton)
           else f"{name}, Kanton {CANTON_DE.get(canton, canton)}")
    return badge, city, via, False


def design_board(delta: dict, cur: dict, lang: str = "de", prev: dict | None = None) -> str:  # prev: unused
    canton_of = {c["court"]: c["canton"] for c in cur["by_court"]}
    rows = []
    for court, n in sorted(fold_chambers(delta["by_court"]).items(), key=lambda kv: (-kv[1], kv[0])):
        canton = canton_of.get(court.partition("@")[0].partition("#")[0], "CH")
        rows.append((*board_row(court, canton), n))
    top, rest = rows[:13], rows[13:]

    lines = []
    for badge, city, via, fed, n in top:
        lines.append(f'<div class="r"><div class="cat{" fed" if fed else ""}">{esc(badge)}</div>'
                     f'<div class="dest"><b>{esc(city)}</b><span>{esc(via)}</span></div>'
                     f'<div class="n">{wp.fmt(n, "de")}</div></div>')
    if rest:
        lines.append(f'<div class="r more"><div></div><div class="dest"><b>{len(rest)} weitere Gerichte</b>'
                     f'<span>in {len({r[1] for r in rest})} weiteren Orten</span></div>'
                     f'<div class="n">{wp.fmt(sum(r[4] for r in rest), "de")}</div></div>')
    cantons = len([k for k in delta["by_canton"] if k != FED])
    split = any("@" in c for c in delta["by_court"])
    first = rows[0]
    share = first[4] / max(1, delta["added"])
    part = next((w for f, w in ((.5, "die Hälfte"), (1 / 3, "ein Drittel"), (.25, "ein Viertel"), (.2, "ein Fünftel"),
                                (.1, "ein Zehntel")) if share >= f), None)
    badge, city, via, fed, _ = first
    if via.startswith("Gerichte des Kantons "):
        lead = "von den " + via.replace("Gerichte", "Gerichten", 1)
    elif fed:
        lead = f"vom {via.split(',')[0]} in {city}"
    else:
        lead = f"vom {via.split(',')[0]} {city}"  # e.g. "vom Obergericht Zürich"
    note = (f"{wp.fmt(delta['added'], 'de')} neue Entscheide aus {cantons} Kantonen und vom Bund."
            + (f" Mehr als {part} davon stammt {lead}." if part else ""))
    a, b = first_day(delta), delta["to"]
    body = f"""
<div class="top"><div class="title">Ankunft <i>Arrivée</i> <i>Arrivo</i></div>
  <div class="meta"><b>Woche {week_no(delta)}</b><span>{a.day}.{a.month}.–{b.day}.{b.month}.{b.year}</span></div></div>
<div class="th"><span></span><span>Von</span><span>Entscheide</span></div>
<div class="rows">{''.join(lines)}</div>
<div class="note"><b>Information</b><span>{esc(note)}</span></div>
<div class="ft"><b>opencaselaw.ch</b><span>{wp.fmt(cur['total'], 'de')} Entscheide seit 1875, frei durchsuchbar, zitierbar und herunterladbar</span></div>"""
    css = """
body{background:#1c3a78;color:#fff;font-family:'Source Sans 3','Frutiger','Helvetica Neue',sans-serif}
.top{position:absolute;left:0;right:0;top:0;height:132px;background:#16306a;display:flex;justify-content:space-between;align-items:center;padding:0 48px}
.title{font-size:66px;font-weight:700;letter-spacing:-.01em}
.title i{font-style:normal;font-weight:400;font-size:30px;color:#b9c6e4;margin-left:14px;letter-spacing:0}
.meta{display:flex;flex-direction:column;align-items:flex-end;line-height:1.15}
.meta b{font-size:34px;font-weight:700}.meta span{font-size:23px;color:#d5ddf0}
.th,.r{display:grid;grid-template-columns:108px 1fr 170px;column-gap:22px;align-items:center}
.th{position:absolute;left:48px;right:48px;top:152px;font-size:19px;font-weight:600;color:#b9c6e4}
.th span:nth-child(3),.r .n{text-align:right}
.rows{position:absolute;left:48px;right:48px;top:186px}
.r{height:69px;border-top:1px solid #34539a}
.cat{justify-self:start;font-size:21px;font-weight:700;line-height:1;padding:6px 8px 5px;border:2px solid #fff;border-radius:4px;min-width:62px;text-align:center}
.cat.fed{background:#fff;color:#1c3a78}
.dest{display:flex;flex-direction:column;line-height:1.1;overflow:hidden;white-space:nowrap}
.dest b{font-size:34px;font-weight:700;letter-spacing:-.01em}
.dest span{font-size:18px;color:#c7d2ec;margin-top:3px}
.was{font-size:26px;font-weight:600;color:#9fb0d6}
.was.na{color:#6f84b8}
.r .n{font-size:38px;font-weight:700}
.r.more .dest b{font-weight:600;color:#dfe5f3}
.note{position:absolute;left:0;right:0;top:1178px;height:76px;background:#16306a;display:flex;align-items:center;gap:22px;padding:0 48px;font-size:21px;line-height:1.25}
.note b{flex:none;background:#ffde14;color:#16306a;font-weight:700;font-size:19px;padding:5px 10px;border-radius:3px}
.note span{color:#ffde14}
.ft{position:absolute;left:48px;right:48px;bottom:26px;display:flex;justify-content:space-between;align-items:baseline;font-size:18px;color:#c7d2ec}
.ft b{font-size:30px;font-weight:700;color:#fff}"""
    return page("family=Source+Sans+3:wght@400;600;700", css, body, lang)


# ── fr: La rose de la semaine ─────────────────────────────────────────────────

ROSE = {"ch": ("Confédération", "#3b3b3b"), "de": ("Suisse alémanique", "#9db1c6"),
        "fr": ("Suisse romande", "#e9a088"), "it": ("Suisse italienne", "#d6b25e")}
WEEKDAY_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def wedge(cx, cy, r0, r1, a0, a1) -> str:
    """Annular sector between radii r0 < r1 and angles a0 < a1 (radians, 0 = up, clockwise)."""
    p = lambda r, a: (cx + r * math.sin(a), cy - r * math.cos(a))  # noqa: E731
    large = 1 if a1 - a0 > math.pi else 0
    (x0, y0), (x1, y1) = p(r1, a0), p(r1, a1)
    if r0 <= 0.01:
        return f"M{cx:.1f} {cy:.1f}L{x0:.1f} {y0:.1f}A{r1:.1f} {r1:.1f} 0 {large} 1 {x1:.1f} {y1:.1f}Z"
    (x2, y2), (x3, y3) = p(r0, a1), p(r0, a0)
    return (f"M{x0:.1f} {y0:.1f}A{r1:.1f} {r1:.1f} 0 {large} 1 {x1:.1f} {y1:.1f}"
            f"L{x2:.1f} {y2:.1f}A{r0:.1f} {r0:.1f} 0 {large} 0 {x3:.1f} {y3:.1f}Z")


MONTH_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
            "novembre", "décembre"]


def design_rose(delta: dict, cur: dict, lang: str = "fr", usual: dict | None = None) -> str:
    """La rose des écarts: each canton's week against its usual week, one petal per canton by geography.

    The circle is a usual week. A petal reaching beyond it is a busier week than usual, a short one a
    quieter week; the scale is logarithmic, so twice and half the usual stand equally far from the circle.
    """
    import weekly_poster_editions as ed

    usual = usual or wp.usual_week(delta)
    now = lambda k: delta["by_canton"].get(k, 0)  # noqa: E731
    typ = lambda k: usual["by_canton"].get(k, 0)  # noqa: E731
    mid_e, mid_n = 660, 185
    bearing = {k: math.atan2(ed.SEATS[k][1] - mid_e, ed.SEATS[k][2] - mid_n) % (2 * math.pi) for k in wp.TILES}
    order = sorted(wp.TILES, key=bearing.get)
    cx, cy, R0, RU = 540, 770, 92, 250  # centre disc, usual-week circle
    span = RU - R0  # one doubling = span / 3

    def rr(n, u):
        if not n:
            return None
        q = n / u if u else 8
        return R0 + span * (1 + math.log2(max(1 / 8, min(8.0, q))) / 3)

    step = 2 * math.pi / len(order)
    petals, labels, ticks, placed = [], [], [], []
    reach = [(rr(now(k), typ(k)) or 0) if typ(k) or not now(k) else RU + 26 for k in order]
    for i, k in enumerate(order):
        a0, a1 = i * step + .012, (i + 1) * step - .012
        am = (a0 + a1) / 2
        r = rr(now(k), typ(k))
        colour = ROSE[region_of(k)][1]
        if r and typ(k):
            petals.append(f'<path d="{wedge(cx, cy, R0 + 4, r, a0, a1)}" fill="{colour}"/>')
        elif r:  # nothing in a usual week: no ratio to draw, so a short hatched petal marks it as new
            r = RU + 26
            petals.append(f'<path d="{wedge(cx, cy, R0 + 4, r, a0, a1)}" fill="url(#new)"/>')
        q = now(k) / typ(k) if typ(k) else None
        tag = "nouveau" if not typ(k) and now(k) else ("—" if not now(k) else
              (f"×{q:.1f}".replace(".", ",") if q >= 1 else f"÷{1 / q:.1f}".replace(".", ",")))
        anchor = "middle" if abs(math.sin(am)) < .35 else ("start" if math.sin(am) > 0 else "end")
        cls = "kl" if now(k) else "kl z"
        w, h = (118, 46) if now(k) else (34, 24)
        rl = max(reach[i - 1], reach[i], reach[(i + 1) % len(order)], RU) + 24
        while True:
            lx, ly = cx + rl * math.sin(am), cy - rl * math.cos(am)
            x0b = lx - (w / 2 if anchor == "middle" else w if anchor == "end" else 0)
            box = (x0b, ly - 22, x0b + w, ly - 22 + h)
            hit_petal = any(abs(math.atan2(px_ - cx, -(py_ - cy)) % (2 * math.pi) - am) < 0 for px_, py_ in ())
            if not any(box[0] < q_[2] and q_[0] < box[2] and box[1] < q_[3] and q_[1] < box[3] for q_ in placed):
                break
            rl += 10
        placed.append(box)
        labels.append(f'<text x="{lx:.1f}" y="{ly + 2:.1f}" text-anchor="{anchor}" class="{cls}">{k}</text>'
                      + (f'<text x="{lx:.1f}" y="{ly + 22:.1f}" text-anchor="{anchor}" class="kn">{wp.fmt(now(k), "fr")} · {tag}</text>' if now(k) else ""))
    for mult, txt in ((2, "×2"), (4, "×4"), (.5, "÷2")):
        rad_ = R0 + span * (1 + math.log2(mult) / 3)
        ticks.append(f'<circle cx="{cx}" cy="{cy}" r="{rad_:.1f}" fill="none" stroke="#b9b2a4" stroke-width=".7" stroke-dasharray="1.5 4"/>'
                     f'<text x="{cx + 4}" y="{cy - rad_ - 4:.1f}" class="gr">{txt}</text>')
    ring = (f'<circle cx="{cx}" cy="{cy}" r="{RU}" fill="none" stroke="#24221e" stroke-width="1.3"/>'
            "")
    fn, fu = now(FED), typ(FED)
    fq = fn / fu if fu else 1
    core = (f'<circle cx="{cx}" cy="{cy}" r="{R0}" fill="{ROSE["ch"][1]}"/>'
            f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" class="cn">{wp.fmt(fn, "fr")}</text>'
            f'<text x="{cx}" y="{cy + 22}" text-anchor="middle" class="cl">tribunaux fédéraux</text>'
            f'<text x="{cx}" y="{cy + 42}" text-anchor="middle" class="cl w">habituel {wp.fmt(round(fu), "fr")}</text>')
    busy = max((k for k in order if now(k) and typ(k)), key=lambda k: now(k) / typ(k))
    refiled = delta.get("refiled") or {}
    quiet = min((k for k in order if typ(k) >= 5 and k not in refiled), key=lambda k: now(k) / typ(k))
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for key, (n, c) in ROSE.items() if key != "ch")
    a, b = first_day(delta), delta["to"]
    period = (f"du {a.day} au {b.day} {MONTH_FR[b.month - 1]} {b.year}" if a.month == b.month
              else f"du {a.day} {MONTH_FR[a.month - 1]} au {b.day} {MONTH_FR[b.month - 1]} {b.year}")
    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>Semaine {week_no(delta)}</span></div>
<h1><span class="l1">La rose</span><span class="l2">des cantons</span></h1>
<p class="sub">{wp.fmt(delta['added'], 'fr')} décisions entrées dans le corpus ouvert {period}, pour {wp.fmt(round(usual['added']), 'fr')} une semaine habituelle. Chaque pétale est un canton, orienté là où il se trouve&#8239;; au-delà du cercle, une semaine plus chargée que d’habitude, en deçà, plus calme.</p>
<p class="hl"><b>{busy}</b> {wp.fmt(now(busy), 'fr')} décisions, {f"{now(busy) / typ(busy):.1f}".replace(".", ",")} fois l’habitude. <b>{quiet}</b> {wp.fmt(now(quiet), 'fr')}, pour {wp.fmt(round(typ(quiet)), 'fr')} d’habitude.</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs><pattern id="new" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
<rect width="7" height="7" fill="#dfe6ee"/><line x1="0" y1="0" x2="0" y2="7" stroke="#9db1c6" stroke-width="3"/></pattern></defs>{''.join(ticks)}
<g stroke="#24221e" stroke-width=".8" stroke-linejoin="round">{''.join(petals)}</g>{ring}{core}{''.join(labels)}</svg>
<div class="lg">{legend}<span><i class="ring"></i>semaine habituelle</span></div>
<p class="key">Semaine habituelle&#8239;: médiane des huit semaines précédentes. Échelle logarithmique. Hachuré&#8239;: aucune décision d’habitude. {esc(refiled_note(delta, "fr"))} D’après Florence Nightingale, 1858.</p>
<div class="ft"><b>opencaselaw.ch</b><span>{wp.fmt(cur['total'], 'fr')} décisions depuis 1875, libres à consulter, citer et télécharger</span></div>"""
    css = """
body{background:#faf8f3;color:#24221e;font-family:'Cormorant Garamond',Georgia,serif}
.hd{position:absolute;left:52px;right:52px;top:38px;display:flex;justify-content:space-between;font-size:21px;font-weight:600;letter-spacing:.02em}
h1{position:absolute;left:48px;top:62px;font-weight:500;font-style:italic;line-height:.82;letter-spacing:-.02em}
h1 .l1{display:block;font-size:150px}
h1 .l2{display:block;font-size:66px;margin:6px 0 0 6px;letter-spacing:-.01em}
.sub{position:absolute;left:560px;right:52px;top:96px;font-size:20.5px;line-height:1.3;font-weight:500}
.hl{position:absolute;left:52px;right:52px;top:300px;font-size:22px;font-style:italic;color:#5d584f}
.hl b{font-style:normal;font-weight:700;color:#24221e}
.kl{font-family:'Cormorant Garamond',serif;font-size:27px;font-weight:700;fill:#24221e}
.kl.z{fill:#b5ae9f;font-size:20px;font-weight:600}
.kn{font-family:'Cormorant Garamond',serif;font-size:17px;font-style:italic;font-weight:500;fill:#5d584f}
.gr{font-family:'Cormorant Garamond',serif;font-size:14px;font-style:italic;fill:#9c968a}
.gu{font-family:'Cormorant Garamond',serif;font-size:16px;font-style:italic;font-weight:600;fill:#24221e}
.cn{font-family:'Cormorant Garamond',serif;font-size:42px;font-weight:600;fill:#faf8f3}
.cl{font-family:'Cormorant Garamond',serif;font-size:16px;font-style:italic;fill:#e9e4d8}
.cl.w{font-size:15px;fill:#bdb6a8}
.lg{position:absolute;left:52px;right:52px;top:1192px;display:flex;justify-content:center;gap:44px;font-size:22px;font-weight:600}
.lg i{display:inline-block;width:15px;height:15px;margin-right:9px;vertical-align:-1px;outline:.9px solid #2b2925}
.lg i.ring{outline:none;border:1.4px solid #24221e;border-radius:50%;width:16px;height:16px}
.key{position:absolute;left:52px;right:52px;top:1228px;font-size:15.5px;line-height:1.25;font-style:italic;color:#5d584f;text-align:center}
.ft{position:absolute;left:52px;right:52px;bottom:24px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;border-top:1px solid #bdb6a8;padding-top:9px}
.ft b{font-size:30px;font-weight:600}"""
    return page("family=Cormorant+Garamond:ital,wght@0,500;0,600;0,700;1,500;1,600", css, body, lang)


# ── it: Mosaico ───────────────────────────────────────────────────────────────

TESSERA = {"ch": ("Confederazione", "#d4ab3a"), "de": ("Svizzera tedesca", "#bf5a3c"),
           "fr": ("Svizzera romanda", "#5f9178"), "it": ("Svizzera italiana", "#ece4d2")}
GROUND = "#24304d"


def inside(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    hit, j = False, len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


FED_SEAT_NAME = {"VD": "Losanna", "LU": "Lucerna", "SG": "San Gallo", "TI": "Bellinzona", "BE": "Berna"}


def design_mosaic(delta: dict, cur: dict, lang: str = "it") -> str:
    """A mosaic cartogram: each canton (and each federal seat) a square panel of tesserae, one per decision,
    set where it lies in the country and pushed apart until no two touch."""
    import json
    from pathlib import Path

    import weekly_poster_editions as ed

    geo = json.loads(Path(__file__).with_name("weekly_poster_geo.json").read_text())
    x0, y0, mw = 100, 450, 880
    e0, e1, n0, n1 = 476, 842, 68, 302
    sc = mw / (e1 - e0)
    mh = (n1 - n0) * sc
    px = lambda e: x0 + (e - e0) * sc  # noqa: E731
    py = lambda nn: y0 + (n1 - nn) * sc  # noqa: E731
    groups = []  # (label, colour key, n, home x, home y)
    for k in wp.TILES:
        n = delta["by_canton"].get(k, 0)
        if n:
            _, e, nn = ed.SEATS[k]
            groups.append([k, region_of(k), n, px(e), py(nn)])
    fed = {}
    for (court, canton), v in delta["by_key"].items():
        if canton == FED:
            seat = court.split("@")[1] if "@" in court else ed.FEDERAL_SEAT.get(court, "BE")
            fed[seat] = fed.get(seat, 0) + v
    for seat, n in fed.items():
        _, e, nn = ed.SEATS[seat]
        groups.append([FED_SEAT_NAME[seat], "ch", n, px(e), py(nn) + 1])
    t = 11.5  # one tessera
    for g in groups:
        side = math.ceil(math.sqrt(g[2]))
        g += [side * t, side]  # panel width, tiles per row
    lab = 26  # room under each panel for its label
    pos = [[g[3], g[4]] for g in groups]
    gap = 10
    half = lambda g: (max(g[5], 9 * (len(g[0]) + 4)) / 2 + gap / 2, (g[5] + lab) / 2 + gap / 2)  # noqa: E731
    for it in range(900):  # push overlapping panels (with their labels) apart, pull each gently home
        moved = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                (wi, hi), (wj, hj) = half(groups[i]), half(groups[j])
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                ox, oy = wi + wj - abs(dx), hi + hj - abs(dy)
                if ox > 0 and oy > 0:
                    moved = True
                    mi, mj = groups[i][2], groups[j][2]  # the larger panel yields less
                    fi, fj = mj / (mi + mj), mi / (mi + mj)
                    if ox < oy:
                        sgn = 1 if dx >= 0 else -1
                        pos[i][0] -= sgn * ox * fi
                        pos[j][0] += sgn * ox * fj
                    else:
                        sgn = 1 if dy >= 0 else -1
                        pos[i][1] -= sgn * oy * fi
                        pos[j][1] += sgn * oy * fj
        for i, g in enumerate(groups):
            pull = .02 + .06 * min(1, g[2] / 300)  # big panels stay near home
            pos[i][0] += (g[3] - pos[i][0]) * pull
            pos[i][1] += (g[4] - pos[i][1]) * pull
            hw, hh = half(g)
            pos[i][0] = min(max(pos[i][0], 46 + hw), W - 46 - hw)
            pos[i][1] = min(max(pos[i][1], 330 + hh), 1165 - hh)
        if not moved and it > 50:
            break
    for it in range(400):  # final pass: no pull home, equal shares, until nothing touches
        moved = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                (wi, hi), (wj, hj) = half(groups[i]), half(groups[j])
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                ox, oy = wi + wj - abs(dx), hi + hj - abs(dy)
                if ox > 0 and oy > 0:
                    moved = True
                    k = 0 if ox < oy else 1
                    d = (ox if k == 0 else oy) / 2 + .5
                    sgn = 1 if (dx if k == 0 else dy) >= 0 else -1
                    pos[i][k] -= sgn * d
                    pos[j][k] += sgn * d
        for i, g in enumerate(groups):
            hw, hh = half(g)
            pos[i][0] = min(max(pos[i][0], 46 + hw), W - 46 - hw)
            pos[i][1] = min(max(pos[i][1], 330 + hh), 1165 - hh)
        if not moved:
            break
    pos = [[x, y - lab / 2] for x, y in pos]  # centre of the tile square, label below
    rnd = random.Random(week_no(delta))
    tiles, labels = [], []
    for (label, reg, n, hx, hy, w, per), (cx, cy) in zip(groups, pos):
        left, top = cx - w / 2, cy - w / 2
        colour = TESSERA[reg][1]
        for i in range(n):
            r_, c_ = divmod(i, per)
            tx, ty = left + c_ * t + t / 2, top + (per - 1 - r_) * t + t / 2  # filled from the bottom
            q = t * .84
            rot = rnd.uniform(-7, 7)
            shade = 1 + rnd.uniform(-.1, .1)
            tiles.append(f'<rect x="{tx - q / 2:.1f}" y="{ty - q / 2:.1f}" width="{q:.1f}" height="{q:.1f}" rx="1.4" '
                         f'fill="{colour}" style="filter:brightness({shade:.2f})" transform="rotate({rot:.1f} {tx:.1f} {ty:.1f})"/>')
        big = n >= 40
        labels.append(f'<text x="{cx:.1f}" y="{top + w + (21 if big else 17):.1f}" text-anchor="middle" class="{"gl" if big else "gl s"}">'
                      f'{esc(label)} <tspan class="gn">{wp.fmt(n, "it")}</tspan></text>')
    outline = "M" + "L".join(f"{px(e):.1f} {py(nn):.1f}" for e, nn in geo["border"]) + "Z"
    silent = " ".join(k for k in wp.TILES if not delta["by_canton"].get(k) and k not in (delta.get("refiled") or {}))
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for n, c in TESSERA.values())
    a, b = first_day(delta), delta["to"]
    body = f"""
<div class="hd"><span>OpenCaseLaw</span><span>Settimana {week_no(delta)} &nbsp; {wp.fmt_day(a, 'it')}–{wp.fmt_day(b, 'it')}{b.year}</span></div>
<h1><span>Mosaico</span><i>della settimana</i></h1>
<p class="sub">{wp.fmt(delta['added'], 'it')} decisioni entrate nel corpus aperto. Ogni tessera è una decisione&#8239;; ogni cantone un riquadro di tessere, posato dove si trova il cantone. I tribunali federali stanno nella loro sede.</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<path d="{outline}" fill="#161f38" stroke="#e3bd4f" stroke-width="1.6" stroke-dasharray="3 4" stroke-linejoin="round" opacity=".9"/>
{''.join(tiles)}{''.join(labels)}</svg>
<div class="lg">{legend}</div>
<p class="key">Nessuna decisione questa settimana: {esc(silent)}. {esc(refiled_note(delta, "it"))}</p>
<div class="ft"><b>opencaselaw.ch</b><span>{wp.fmt(cur['total'], 'it')} decisioni dal 1875, da consultare, citare e scaricare liberamente</span></div>"""
    css = """
body{background:#0f1628;color:#f1ead9;font-family:'Bodoni Moda',Didot,serif}
.hd{position:absolute;left:46px;right:46px;top:36px;display:flex;justify-content:space-between;font-size:20px;font-weight:500;color:#cfc6b1}
h1{position:absolute;left:42px;top:70px;font-weight:500;line-height:.86}
h1 span{display:block;font-size:150px;letter-spacing:-.02em}
h1 i{display:block;font-size:52px;margin:4px 0 0 6px;color:#e3bd4f}
.sub{position:absolute;left:600px;right:46px;top:104px;font-size:21px;line-height:1.34;color:#ddd4bf}
.gl{font-family:'Bodoni Moda',serif;font-style:italic;font-size:22px;fill:#fff8e8}
.gl.s{font-size:16px;fill:#d9d1bf}
.gn{font-style:normal;font-weight:500}
.lg{position:absolute;left:46px;right:46px;top:1150px;display:flex;justify-content:space-between;font-size:21px}
.lg i{display:inline-block;width:17px;height:17px;border-radius:3px;margin-right:9px;vertical-align:-2px}
.key{position:absolute;left:46px;right:46px;top:1190px;font-size:16px;line-height:1.3;font-style:italic;color:#bfb7a3}
.ft{position:absolute;left:46px;right:46px;bottom:28px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;color:#cfc6b1;border-top:1px solid #39456a;padding-top:12px}
.ft b{font-size:32px;font-weight:500;color:#f1ead9}"""
    return page("family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,500;1,6..96,400;1,6..96,500", css, body, lang)


# ── rm: Sgraffito ─────────────────────────────────────────────────────────────

INK, LIME = "#4f5150", "#ecebe5"
WEEKDAY_RM = ["glindesdi", "mardi", "mesemna", "gievgia", "venderdi", "sonda", "dumengia"]
REGION_RM = {"ch": "Confederaziun", "de": "Svizra tudestga", "fr": "Svizra franzosa", "it": "Svizra taliana"}


def motif(kind: str, x: float, y: float, r: float, ink: str = INK) -> str:
    """One sgraffito unit, drawn as scratched lines: rosette, diamond, ring, tooth."""
    if kind == "ch":  # six-petal rosette from circles
        petals = "".join(f'<circle cx="{x + r / 2 * math.cos(k * math.pi / 3):.1f}" cy="{y + r / 2 * math.sin(k * math.pi / 3):.1f}" r="{r / 2:.1f}"/>'
                         for k in range(6))
        return f'<g fill="none" stroke="{ink}">{petals}<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/></g>'
    if kind == "de":
        return (f'<path d="M{x:.1f} {y - r:.1f}L{x + r:.1f} {y:.1f}L{x:.1f} {y + r:.1f}L{x - r:.1f} {y:.1f}Z'
                f'M{x:.1f} {y - r / 2:.1f}L{x + r / 2:.1f} {y:.1f}L{x:.1f} {y + r / 2:.1f}L{x - r / 2:.1f} {y:.1f}Z" fill="none" stroke="{ink}"/>')
    if kind == "fr":
        return (f'<g fill="none" stroke="{ink}"><circle cx="{x:.1f}" cy="{y:.1f}" r="{r * .9:.1f}"/></g>'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * .25:.1f}" fill="{ink}"/>')
    return f'<path d="M{x - r:.1f} {y + r * .8:.1f}L{x:.1f} {y - r * .8:.1f}L{x + r:.1f} {y + r * .8:.1f}Z" fill="{ink}"/>'


def _per_region(delta: dict, d) -> dict:
    per = {reg: 0 for reg in REGION_RM}
    for canton, v in delta["by_day"][d].items():
        per[region_of(canton)] += v
    return per


def quoins(x: float, top: float, bottom: float, side: str) -> str:
    """Painted corner stones down one edge, long and short in turn, as on Engadin houses."""
    out, y, k = [], top, 0
    while y < bottom - 10:
        h = min(66.0, bottom - y)
        w = 84 if k % 2 == 0 else 50
        x0 = x if side == "left" else x - w
        out.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{w}" height="{h - 6:.1f}" fill="{INK}"/>'
                   f'<rect x="{x0 + 5:.1f}" y="{y + 5:.1f}" width="{w - 10}" height="{h - 16:.1f}" fill="none" stroke="{LIME}" stroke-width="1.3"/>')
        y += h
        k += 1
    return "".join(out)


LIME2, SCRATCH, GHOST = "#ebe7de", "#55534d", "#b9b4a8"


def running_dog(x0: float, x1: float, y: float, h: float = 14.0, colour: str = SCRATCH) -> str:
    """A running-dog scroll border, the wave band of Engadin facades."""
    w = h * 1.6
    out = []
    x = x0
    while x + w <= x1 + .1:
        r = h * .32
        out.append(f"M{x:.1f} {y + h:.1f} C{x + w * .35:.1f} {y + h:.1f} {x + w * .32:.1f} {y:.1f} {x + w * .62:.1f} {y:.1f} "
                   f"A{r:.1f} {r:.1f} 0 1 1 {x + w * .62:.1f} {y + 2 * r:.1f}")
        x += w
    return (f'<path d="{"".join(out)}" fill="none" stroke="{colour}" stroke-width="1.5" stroke-linecap="round"/>'
            f'<line x1="{x0:.1f}" y1="{y + h + 5:.1f}" x2="{x - .1:.1f}" y2="{y + h + 5:.1f}" stroke="{colour}" stroke-width="1"/>')


def diamond_quoins(x: float, w: float, top: float, bottom: float) -> str:
    """Diamond-point corner stones painted down a facade edge, alternating long and short."""
    out, y, k = [], top, 0
    while y < bottom - 20:
        h = 54.0
        ww = w if k % 2 == 0 else w * .62
        xa = x if x < W / 2 else x - ww
        cx, cy = xa + ww / 2, y + h / 2
        out.append(f'<rect x="{xa:.1f}" y="{y:.1f}" width="{ww:.1f}" height="{h - 6:.1f}" fill="none" stroke="{SCRATCH}" stroke-width="1.3"/>'
                   f'<path d="M{xa:.1f} {y:.1f}L{cx:.1f} {cy - 3:.1f}L{xa + ww:.1f} {y:.1f}M{xa:.1f} {y + h - 6:.1f}L{cx:.1f} {cy - 3:.1f}L{xa + ww:.1f} {y + h - 6:.1f}" '
                   f'fill="none" stroke="{SCRATCH}" stroke-width=".9"/>'
                   f'<path d="M{xa:.1f} {y:.1f}L{cx:.1f} {cy - 3:.1f}L{xa:.1f} {y + h - 6:.1f}Z" fill="{SCRATCH}" opacity=".16"/>')
        y += h
        k += 1
    return "".join(out)


def design_sgraffito(delta: dict, cur: dict, lang: str = "rm", usual: dict | None = None) -> str:  # usual: unused
    unit, pitch, r = 10, 34.0, 12.0
    left, right = 330, 806
    per_row = int((right - left) / pitch)
    order = ("de", "fr", "ch", "it")
    now = {reg: sum(v for c, v in delta["by_canton"].items() if region_of(c) == reg) for reg in order}
    row_h = pitch * 1.06
    heights = [max(1, math.ceil(max(1, round(now[g] / unit)) / per_row)) * row_h + 22 for g in order]
    spacing = max(44.0, (1110 - 330 - sum(heights)) / len(order))  # friezes spread over the facade
    y, bands = 330.0, []
    for reg in order:
        k = round(now[reg] / unit)
        rows = max(1, math.ceil(max(k, 1) / per_row))
        h = rows * row_h + 22
        bands.append(running_dog(150, 930, y - 24, 12))
        for j in range(max(k, 1 if now[reg] else 0)):
            row, col = divmod(j, per_row)
            cx = left + pitch / 2 + col * pitch
            cy = y + 11 + row_h / 2 + row * row_h
            if j < k:
                bands.append(f'<g stroke-width="1.35">{motif(reg, cx, cy, r, SCRATCH)}</g>')
            elif j == 0 and now[reg]:
                bands.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="{SCRATCH}"/>')
        mid = y + h / 2
        bands.append(f'<text x="{left - 36}" y="{mid + 8:.1f}" text-anchor="end" class="rl">{esc(REGION_RM[reg])}</text>'
                     f'<text x="{right + 34}" y="{mid + 13:.1f}" class="dn">{wp.fmt(now[reg], "rm")}</text>')
        y += h + spacing
    bands.append(running_dog(150, 930, y - 24, 12))
    a, b = first_day(delta), delta["to"]
    legend_y = y + 20
    body = f"""
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" stroke-linecap="round">
<defs><filter id="lime"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" seed="4"/>
<feColorMatrix values="0 0 0 0 .33  0 0 0 0 .32  0 0 0 0 .29  0 0 0 .085 0"/></filter></defs>
<rect width="{W}" height="{H}" fill="{LIME2}"/><rect width="{W}" height="{H}" filter="url(#lime)"/>
{diamond_quoins(22, 92, 22, H - 20)}{diamond_quoins(W - 22, 92, 22, H - 20)}
<path d="M150 68 H930 M150 74 H930 M150 252 H930 M150 258 H930" stroke="{SCRATCH}" stroke-width="1"/>
<path d="M540 252 l-26 0 l26 22 l26 -22 z" fill="{LIME2}" stroke="{SCRATCH}" stroke-width="1"/>
<g transform="translate(540 262)" stroke="{SCRATCH}" stroke-width="1.1">{motif("ch", 0, 0, 7, SCRATCH)}</g>
{''.join(bands)}
</svg>
<div class="ins"><div class="t0">OpenCaseLaw</div><div class="t1">Sgraffito da l’emna</div>
<div class="t2">Emna {week_no(delta)} &nbsp; {wp.fmt_day(a, 'rm')}–{wp.fmt_day(b, 'rm')}{b.year} &nbsp; {wp.fmt(delta['added'], 'rm')} novas decisiuns en il corpus avert</div></div>
<div class="lg" style="top:{legend_y:.0f}px">
<span><svg width="28" height="28" viewBox="0 0 28 28" stroke-width="1.3">{motif("de", 14, 14, 10, SCRATCH)}</svg>10 decisiuns</span>
<span>• 1–4 decisiuns</span></div>
<div class="ft"><b>opencaselaw.ch</b><span>{wp.fmt(cur['total'], 'rm')} decisiuns dapi il 1875</span></div>"""
    css = f"""
body{{background:{LIME2};color:{SCRATCH};font-family:'EB Garamond',Georgia,serif}}
.ins{{position:absolute;left:150px;right:150px;top:84px;height:160px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
.t0{{font-family:'Cinzel',serif;font-size:16px;letter-spacing:.42em;margin-bottom:10px}}
.t1{{font-family:'Cinzel',serif;font-weight:500;font-size:60px;letter-spacing:.07em;line-height:1}}
.t2{{font-size:20px;font-style:italic;margin-top:14px}}
.rl{{font-family:'EB Garamond',serif;font-size:28px;font-style:italic;fill:{SCRATCH}}}
.dn{{font-family:'Cinzel',serif;font-size:36px;font-weight:500;fill:{SCRATCH}}}
.dd{{font-family:'EB Garamond',serif;font-size:18px;font-style:italic;fill:#7d7a72}}
.lg{{position:absolute;left:150px;right:150px;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:6px 30px;font-size:19px}}
.lg span{{display:flex;align-items:center;gap:8px}}
.lg svg{{position:static}}
.lg .u{{width:100%;justify-content:center;font-style:italic;font-size:17px;color:#7d7a72}}
.ft{{position:absolute;left:150px;right:150px;bottom:42px;display:flex;justify-content:space-between;align-items:baseline;font-size:19px;border-top:1px solid {SCRATCH};padding-top:10px}}
.ft b{{font-family:'Cinzel',serif;font-size:24px;font-weight:500;letter-spacing:.04em}}"""
    return page("family=Cinzel:wght@500;600&family=EB+Garamond:ital,wght@0,400;0,500;1,400", css, body, lang)


# ── en: The Case-Law Forecast ─────────────────────────────────────────────────

AREAS_EN = [("CH", "Federal courts"), ("GE", "Geneva"), ("VD", "Vaud"), ("VS", "Valais"), ("FR", "Fribourg"),
            ("NE", "Neuchâtel"), ("JU", "Jura"), ("BE", "Bern"), ("SO", "Solothurn"), ("BS", "Basel-Stadt"),
            ("BL", "Basel-Landschaft"), ("AG", "Aargau"), ("ZH", "Zurich"), ("SH", "Schaffhausen"),
            ("TG", "Thurgau"), ("SG", "St. Gallen"), ("AR", "Appenzell Ausserrhoden"), ("AI", "Appenzell Innerrhoden"),
            ("GL", "Glarus"), ("GR", "Grisons"), ("TI", "Ticino"), ("UR", "Uri"), ("SZ", "Schwyz"),
            ("OW", "Obwalden"), ("NW", "Nidwalden"), ("LU", "Lucerne"), ("ZG", "Zug")]


def trend(now: int, before: int) -> str:
    if not before:
        return f"{now}, new this week" if now else ""
    if not now:
        return f"quiet, after {before}"
    q = now / before
    word = ("rising sharply" if q >= 1.5 else "rising" if q >= 1.1 else "steady" if q > .9
            else "falling" if q > .5 else "falling sharply")
    return f"{now:,}, {word} from {before:,}"


# Weather regions as in Swiss forecasts; the weather of a region is its trend against last week.
REGIONS_WX = [("Federal courts", ["CH"]), ("Western Switzerland", ["GE", "VD", "NE", "FR", "JU"]),
              ("Valais", ["VS"]), ("Bern and Solothurn", ["BE", "SO"]),
              ("Northwestern Switzerland", ["BS", "BL", "AG"]), ("Zurich and Schaffhausen", ["ZH", "SH"]),
              ("Central Switzerland", ["LU", "ZG", "SZ", "UR", "OW", "NW"]),
              ("Eastern Switzerland", ["SG", "TG", "AR", "AI", "GL"]), ("Grisons", ["GR"]), ("Ticino", ["TI"])]
NAME_EN = dict(AREAS_EN)
WX = [("sun", "rising sharply", "up by half or more"), ("sunny", "rising", "up by a tenth or more"),
      ("cloud", "steady", "within a tenth"), ("rain", "falling", "down by a tenth or more"),
      ("storm", "falling sharply", "down by half or more"), ("fog", "quiet", "no new decisions")]


def weather(now: float, usual: float) -> str:
    """The weather of a region: this week against a typical week."""
    if not now:
        return "fog"
    if not usual:
        return "sun"
    q = now / usual
    return "sun" if q >= 1.5 else "sunny" if q >= 1.1 else "cloud" if q > .9 else "rain" if q > .5 else "storm"


def icon(kind: str, size: int = 64) -> str:
    """A weather symbol in the manner of the Swiss forecast pictograms."""
    sun = '<circle cx="24" cy="24" r="10" fill="#f2b100"/>' + "".join(
        f'<line x1="{24 + 14 * math.cos(k * math.pi / 4):.1f}" y1="{24 + 14 * math.sin(k * math.pi / 4):.1f}" '
        f'x2="{24 + 19 * math.cos(k * math.pi / 4):.1f}" y2="{24 + 19 * math.sin(k * math.pi / 4):.1f}" stroke="#f2b100" stroke-width="3.2" stroke-linecap="round"/>'
        for k in range(8))
    cloud = lambda dx, dy, c: (f'<g transform="translate({dx} {dy})" fill="{c}"><circle cx="18" cy="30" r="9"/>'  # noqa: E731
                               f'<circle cx="30" cy="24" r="12"/><circle cx="42" cy="31" r="8"/><rect x="18" y="29" width="25" height="10"/></g>')
    drops = lambda xs: "".join(f'<line x1="{x}" y1="44" x2="{x - 4}" y2="54" stroke="#2f7fd1" stroke-width="3" stroke-linecap="round"/>' for x in xs)  # noqa: E731
    body = {
        "sun": f'<g transform="translate(8 8)">{sun}</g>',
        "sunny": f'<g transform="translate(-2 -4) scale(.9)">{sun}</g>' + cloud(6, 12, "#9aa9b5"),
        "cloud": cloud(2, 4, "#8b99a6") + cloud(-6, 10, "#aab6c0"),
        "rain": cloud(2, 0, "#7d8c99") + drops((24, 34, 44)),
        "storm": cloud(2, 0, "#5f6d79") + '<path d="M34 40 L27 51 L33 51 L28 60 L41 47 L35 47 L39 40 Z" fill="#f2b100"/>' + drops((20, 48)),
        "fog": "".join(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#a7b2bb" stroke-width="3.4" stroke-linecap="round"/>'
                       for x0, x1, y in ((12, 52, 22), (8, 46, 32), (16, 56, 42))),
    }[kind]
    return f'<svg width="{size}" height="{size}" viewBox="0 0 64 64">{body}</svg>'


WX_MEANING = {"sun": "far busier than usual", "sunny": "busier than usual", "cloud": "a usual week",
              "rain": "quieter than usual", "storm": "far quieter than usual", "fog": "nothing new"}
# where each region's symbol stands on the map (Swiss grid, km)
REGION_AT = {"Western Switzerland": (548, 172), "Valais": (612, 116), "Bern and Solothurn": (602, 207),
             "Northwestern Switzerland": (626, 256), "Zurich and Schaffhausen": (690, 264),
             "Central Switzerland": (688, 194), "Eastern Switzerland": (742, 240), "Grisons": (768, 168),
             "Ticino": (712, 108)}
SHORT = {"Western Switzerland": "West", "Northwestern Switzerland": "Northwest", "Zurich and Schaffhausen": "Zurich",
         "Central Switzerland": "Centre", "Eastern Switzerland": "East", "Bern and Solothurn": "Bern"}
LAW_EN = {"311.0": "Swiss Criminal Code", "748.0": "Aviation Act", "142.20": "Foreign Nationals and Integration Act",
          "173.32": "Federal Administrative Court Act", "210": "Civil Code", "220": "Code of Obligations",
          "101": "Federal Constitution", "281.1": "Debt Enforcement and Bankruptcy Act", "272": "Civil Procedure Code",
          "312.0": "Criminal Procedure Code", "173.110": "Federal Supreme Court Act", "641.20": "VAT Act"}


FEDLEX_SPARQL = "https://fedlex.data.admin.ch/sparqlendpoint"


def law_outlook(until: str = "2027-03-01", timeout: int = 90) -> list[dict]:
    """Federal acts with a new consolidated version in force after today, from Fedlex: [{date, sr, title, abbr}]."""
    import json
    import urllib.parse
    import urllib.request

    q = f"""PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?date ?sr ?title ?abbr WHERE {{
  ?c jolux:isMemberOf ?work ; jolux:dateApplicability ?date .
  FILTER(?date > NOW() && ?date < "{until}"^^<http://www.w3.org/2001/XMLSchema#date>)
  ?work jolux:classifiedByTaxonomyEntry/<http://www.w3.org/2004/02/skos/core#notation> ?sr .
  ?work jolux:isRealizedBy ?e . ?e jolux:language <http://publications.europa.eu/resource/authority/language/DEU> ; jolux:title ?title .
  OPTIONAL {{ ?e jolux:titleShort ?abbr }}
}} ORDER BY ?date ?sr"""
    req = urllib.request.Request(FEDLEX_SPARQL + "?" + urllib.parse.urlencode({"query": q}),
                                 headers={"Accept": "application/sparql-results+json", "User-Agent": "opencaselaw-poster/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rows = json.load(r)["results"]["bindings"]
    seen = {}
    for b in rows:
        key = (b["date"]["value"], b["sr"]["value"])
        seen[key] = {"date": key[0], "sr": key[1], "title": b["title"]["value"], "abbr": b.get("abbr", {}).get("value", "")}
    return [v for v in seen.values() if not v["sr"].startswith("0.")]  # domestic law; treaties apart


# statutes a Swiss lawyer recognises at a glance, listed first when they change
KNOWN = ["BV", "ZGB", "OR", "StGB", "StPO", "ZPO", "SchKG", "BGG", "VwVG", "VGG", "DBG", "MWSTG", "AHVG", "IVG",
         "ELG", "BVG", "UVG", "KVG", "ATSG", "AIG", "AsylG", "DSG", "FINMAG", "RTVG", "StromVG", "PatG", "ParlG"]
ACT = re.compile(r"gesetz\b|gesetzbuch|bundesverfassung|prozessordnung", re.I)


def is_statute(o: dict) -> bool:
    return bool(ACT.search(o["title"])) and not o["title"].startswith("Verordnung") and not o["sr"].startswith("131.")


ISSUER = {"Schweizerischen Heilmittelinstituts": "Swissmedic"}
COUNTRY = {"Islamischen Republik Iran": "Iran", "Demokratischen Volksrepublik Korea": "Nordkorea"}


def short_name(o: dict) -> str:
    """How a Swiss lawyer would call the act: its short title, else the name in brackets, else its subject."""
    title = re.sub(r"<[^>]+>", "", o["title"]).replace("\u200b", "").strip()
    if o["abbr"]:
        return re.sub(r"<[^>]+>", "", o["abbr"])
    m = re.search(r"\(([^()]{2,32}?)(?:,[^()]*)?\)$", title)
    if m and not re.fullmatch(r"[A-Z][a-z]+( [A-Za-z]+)+|EFZ|EBA", m.group(1)):  # an English gloss or a diploma is not a name
        return m.group(1)
    m = re.match(r"(?:Verordnung|Reglement|Weisung)(?: (?:des|der) (.+?))?(?: vom [^,]*?\d{4})? "
                 r"(?:über|zur|zum|betreffend|für) (?:die |das |den |der |dem )?(.+)", title)
    if not m:
        return f"SR {o['sr']}"
    issuer, topic = m.group(1), re.sub(r"\s*\(.*$", "", m.group(2)).strip()
    issuer = ISSUER.get(issuer, issuer)
    g = re.match(r"(?:berufliche )?Grundbildung (.+?) (EFZ|EBA)\b", topic)
    if g:
        topic = f"Grundbildung {g.group(1)} {g.group(2)}"
    g = re.match(r"Massnahmen (?:gegenüber|im Zusammenhang mit) (?:der Situation in )?(?:der |den |bestimmten )?(.+)", topic)
    if g:
        target = COUNTRY.get(g.group(1), g.group(1))
        target = re.sub(r"^Personen.*?(Taliban|ISIL|Al-Kaida).*$", r"\1", target)
        topic = f"Massnahmen: {target}"
    if len(topic) > 46:
        cut = topic[:46].rsplit(" ", 1)[0]
        cut = re.sub(r"\s+(der|die|das|den|dem|des|von|und|im|in|an|für|bei|auf|zu)$", "", cut)
        topic = cut + "…"
    return topic[0].upper() + topic[1:] + (f" ({issuer})" if issuer else "")


LEVEL = [(100, 4, "#e2001a", "#fff"), (30, 3, "#f28c00", "#1b1b1b"), (5, 2, "#ffd500", "#1b1b1b"), (0, 1, "#cfe6b4", "#1b1b1b")]


def warn_level(n: int) -> tuple[int, str, str]:
    for floor, lvl, bg, fg in LEVEL:
        if n >= floor:
            return lvl, bg, fg
    return 1, LEVEL[-1][2], LEVEL[-1][3]


def design_forecast(delta: dict, cur: dict, lang: str = "en", usual: dict | None = None,
                    outlook: list[dict] | None = None) -> str:
    import json
    from datetime import date as _date
    from pathlib import Path

    usual = usual or wp.usual_week(delta)
    if outlook is None:
        try:
            outlook = law_outlook()
        except Exception:  # Fedlex unreachable: fall back to the list the nightly stats carry
            outlook = [{"date": u["in_force_date"], "sr": u["sr_number"], "title": u["title_de"], "abbr": ""}
                       for u in cur.get("upcoming_amendments") or []]
    now = lambda k: delta["by_canton"].get(k, 0)  # noqa: E731
    typ = lambda k: usual["by_canton"].get(k, 0)  # noqa: E731
    geo = json.loads(Path(__file__).with_name("weekly_poster_geo.json").read_text())
    x0, y0, mw = 300, 184, 680
    e0, e1, n0, n1 = 476, 842, 68, 302
    sc = mw / (e1 - e0)
    px = lambda e: x0 + (e - e0) * sc  # noqa: E731
    py = lambda nn: y0 + (n1 - nn) * sc  # noqa: E731
    ring = lambda pts: "M" + "L".join(f"{px(e):.1f} {py(nn):.1f}" for e, nn in pts) + "Z"  # noqa: E731
    stations = []
    for region, codes in REGIONS_WX:
        if codes == ["CH"]:
            continue
        rn, ru = sum(now(k) for k in codes), sum(typ(k) for k in codes)
        e, nn = REGION_AT[region]
        x, y = px(e), py(nn)
        if not rn and any(k in (delta.get("refiled") or {}) for k in codes):
            stations.append(f'<div class="st" style="left:{x - 34:.0f}px;top:{y - 34:.0f}px"><div class="sv rf"><b>*</b>'
                            f'<i>see note</i></div></div>'
                            f'<div class="sn" style="left:{x + 34:.0f}px;top:{y + 4:.0f}px">{esc(SHORT.get(region, region))}</div>')
            continue
        stations.append(f'<div class="st" style="left:{x - 34:.0f}px;top:{y - 34:.0f}px">{icon(weather(rn, ru), 68)}'
                        f'<div class="sv"><b>{rn:,}</b><i>{ru:,.0f}</i></div></div>'
                        f'<div class="sn" style="left:{x - 34:.0f}px;top:{y + 30:.0f}px">{esc(SHORT.get(region, region))}</div>')
    fn, fu = now(FED), typ(FED)
    fc = lambda c: delta["by_court"].get(c, 0)  # noqa: E731
    sc_ = fc("bger") + fc("bger@VD") + fc("bger@LU")
    fed = [("Federal Supreme Court, Lausanne", fc("bger@VD")), ("Federal Supreme Court, Lucerne", fc("bger@LU"))] \
        if fc("bger@VD") or fc("bger@LU") else [("Federal Supreme Court", sc_)]
    fed += [("Federal Administrative Court", fc("bvger")), ("Other federal bodies", fn - sc_ - fc("bvger"))]
    fed_cells = "".join(f'<div class="fc"><b>{v:,}</b><span>{esc(n)}</span></div>' for n, v in fed)
    # outlook: one column per date on which federal law changes, like the days of a local forecast
    by_date = {}
    for o in outlook:
        by_date.setdefault(o["date"], []).append(o)
    days = sorted(by_date)[:6]
    cols = []
    for d in days:
        items = by_date[d]
        acts = [o for o in items if is_statute(o)]
        ords = [o for o in items if not is_statute(o)]
        lvl, bg, fg = warn_level(len(items))
        dd = _date.fromisoformat(d)
        stat = sorted({short_name(o) for o in acts}, key=lambda a: (KNOWN.index(a) if a in KNOWN else 99, a.startswith("SR"), a))
        # short titles first, then descriptive names; a cut-off description last
        ordn = sorted({short_name(o) for o in ords}, key=lambda a: (a.startswith("SR"), "…" in a, len(a) > 24, a))
        cap_s, cap_o = 5, 4
        def listing(names, cap, word):
            shown = names[:cap]
            rest = len(names) - len(shown)
            return esc(", ".join(shown)) + (f" and {rest} more" if rest > 0 else "")
        cols.append(f'<div class="day"><div class="dw">{dd.strftime("%a")}</div><div class="dt">{dd.day} {dd.strftime("%b")}</div>'
                    f'<div class="lv" style="background:{bg};color:{fg}">{lvl}</div>'
                    f'<div class="dn"><b>{len(items)}</b> {"act" if len(items) == 1 else "acts"}</div>'
                    + (f'<div class="dk">{len(acts)} {"statute" if len(acts) == 1 else "statutes"}</div><div class="da">{listing(stat, cap_s, "")}</div>' if acts else "")
                    + (f'<div class="dk">{len(ords)} {"ordinance" if len(ords) == 1 else "ordinances"}</div><div class="do">{listing(ordn, cap_o, "")}</div>' if ords else "")
                    + '</div>')
    big = max(days, key=lambda d: len(by_date[d])) if days else None
    headline = ""
    if big:
        acts = [o for o in by_date[big] if is_statute(o)]
        bd = _date.fromisoformat(big)
        headline = (f"Heaviest day ahead: {bd.day} {bd.strftime('%B %Y')}, {len(by_date[big])} federal acts change, "
                    f"{len(acts)} of them statutes.")
    moved = sorted((k for k in wp.TILES if k not in (delta.get("refiled") or {})), key=lambda k: now(k) - typ(k))
    hi_k = moved[-1]
    synopsis = (f"{delta['added']:,} new decisions against {usual['added']:,.0f} in a usual week. "
                f"Sunny in {NAME_EN[hi_k]}, {now(hi_k):,} against a usual {typ(hi_k):,.0f}.")
    legend = "".join(f'<span>{icon(k, 36)}{esc(WX_MEANING[k])}</span>' for k in ("sun", "sunny", "cloud", "rain", "storm", "fog"))
    a, b = first_day(delta), delta["to"]
    body = f"""
<div class="bar"><span class="brand">OpenCaseLaw</span><span>Week {week_no(delta)} &nbsp; {a.day}–{b.day} {b.strftime('%B')} {b.year}</span></div>
<h1>Case-law weather</h1>
<p class="syn">{esc(synopsis)}</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<path d="{ring(geo['border'])}" fill="#eef1ec" stroke="#8c9a90" stroke-width="1.4" stroke-linejoin="round"/>
<path d="{''.join(ring(l) for l in geo['lakes'])}" fill="#c4dcef"/></svg>
{''.join(stations)}
<div class="mapkey"><span class="mk"><b>{now(hi_k):,}</b> <i>{typ(hi_k):,.0f}</i></span> new decisions this week, <i>in a usual week</i></div>
{f'<p class="rfn">* {esc(refiled_note(delta, "en").replace("ZH:", "Zurich:"))}</p>' if delta.get("refiled") else ""}
<div class="fed"><div class="fh">{icon(weather(fn, fu), 48)}<div><b>Federal courts {fn:,}</b><i>usual {fu:,.0f}</i></div></div>{fed_cells}</div>
<div class="ol"><div class="olh"><h2>Outlook: federal law coming into force</h2><span>Source: Fedlex</span></div>
<p class="olp">{esc(headline)}</p>
<div class="days">{''.join(cols)}</div>
<div class="lvk"><span>Warning level, by the number of federal acts changing that day:</span>
{''.join(f'<i style="background:{bg};color:{fg}">{lvl}</i>{t}' for (fl, lvl, bg, fg), t in zip(reversed(LEVEL), ("1–4", "5–29", "30–99", "100+")))}</div></div>
<div class="lg">{legend}</div>
<div class="ft"><b>opencaselaw.ch</b><span>Usual week: median of the eight weeks before. {wp.fmt(cur['total'], 'en')} decisions since 1875.</span></div>"""
    css = """
body{background:#fff;color:#1b1b1b;font-family:'Inter','Helvetica Neue',sans-serif;font-variant-numeric:normal}
.bar{position:absolute;left:0;right:0;top:0;height:52px;background:#1b1b1b;color:#fff;display:flex;justify-content:space-between;align-items:center;padding:0 40px;font-size:17px;font-weight:500}
.bar .brand{font-weight:700;letter-spacing:.01em}
h1{position:absolute;left:38px;top:72px;font-weight:700;font-size:58px;letter-spacing:-.035em;line-height:1}
.syn{position:absolute;left:40px;right:40px;top:142px;font-size:19px;line-height:1.4;color:#3c3c3c}
.st{position:absolute;display:flex;align-items:center;gap:0}
.st svg,.fed svg,.lg svg{position:static;flex:none}
.sv{display:flex;align-items:baseline;gap:5px;background:#fff;border-radius:4px;padding:1px 6px;box-shadow:0 1px 3px rgba(0,0,0,.18)}
.sv b{font-size:26px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.sv i{font-style:normal;font-size:15px;font-weight:600;color:#2a6fb5}
.sn{position:absolute;font-size:13px;font-weight:600;color:#4a4a4a}
.sv.rf{margin-left:68px}.sv.rf b{color:#2a6fb5}
.rfn{position:absolute;left:40px;width:250px;top:470px;font-size:13.5px;line-height:1.35;color:#3c3c3c}
.mapkey{position:absolute;left:40px;top:604px;font-size:14px;color:#4a4a4a;display:flex;align-items:center;gap:6px}
.mapkey .mk{background:#fff;border-radius:4px;padding:0 6px;box-shadow:0 1px 3px rgba(0,0,0,.18)}
.mapkey b{font-size:17px;color:#1b1b1b}.mapkey i{font-style:normal;font-weight:600;color:#2a6fb5}
.fed{position:absolute;left:40px;right:40px;top:632px;display:grid;grid-template-columns:260px repeat(4,1fr);column-gap:12px;align-items:center;border-top:1px solid #d9dcd8;border-bottom:1px solid #d9dcd8;padding:10px 0}
.fh{display:flex;align-items:center;gap:6px}.fh div{display:flex;flex-direction:column;line-height:1.15}
.fh b{font-size:19px;font-weight:700}.fh i{font-style:normal;font-size:13.5px;color:#2a6fb5;font-weight:600}
.fc{display:flex;flex-direction:column;line-height:1.15}.fc b{font-size:24px;font-weight:700}.fc span{font-size:13px;color:#4a4a4a}
.ol{position:absolute;left:40px;right:40px;top:728px;background:#f4f5f3;border-radius:10px;padding:18px 20px}
.olh{display:flex;justify-content:space-between;align-items:baseline}
.olh h2{font-size:25px;font-weight:700;letter-spacing:-.015em}.olh span{font-size:14px;color:#6a6a6a}
.olp{font-size:17.5px;margin:6px 0 14px;color:#2c2c2c}
.days{display:grid;grid-template-columns:repeat(6,1fr);column-gap:10px}
.day{background:#fff;border-radius:8px;padding:10px 8px 12px;display:flex;flex-direction:column;align-items:center;text-align:center}
.dw{font-size:14px;color:#6a6a6a;font-weight:600}.dt{font-size:20px;font-weight:700;margin-bottom:8px}
.lv{width:34px;height:34px;border-radius:50%;font-size:18px;font-weight:700;line-height:34px;margin-bottom:6px}
.dn{font-size:14px;color:#4a4a4a}.dn b{font-size:24px;color:#1b1b1b;font-weight:700}
.dk{font-size:11.5px;font-weight:600;color:#6a6a6a;margin-top:10px;text-transform:none;border-top:1px solid #e3e5e1;padding-top:6px;width:100%}
.da{font-size:13.5px;line-height:1.35;color:#1b1b1b;margin-top:3px;font-weight:700}
.do{font-size:13px;line-height:1.35;color:#2c2c2c;margin-top:3px;font-weight:500}
.lvk{display:flex;align-items:center;gap:8px;margin-top:12px;font-size:13.5px;color:#4a4a4a}
.lvk i{font-style:normal;width:22px;height:22px;border-radius:50%;text-align:center;line-height:22px;font-weight:700;font-size:12px;margin-left:8px}
.lg{position:absolute;left:40px;top:196px;width:240px;display:flex;flex-direction:column;gap:2px;font-size:14px;color:#3c3c3c}
.lg span{display:flex;align-items:center;gap:4px}
.ft{position:absolute;left:40px;right:40px;bottom:18px;display:flex;justify-content:space-between;align-items:baseline;font-size:14px;color:#6a6a6a;padding-top:6px}
.ft b{font-size:24px;font-weight:700;color:#1b1b1b}"""
    return page("family=Inter:wght@400;500;600;700", css, body, lang)


DESIGNS = {"de": ("board", design_board), "fr": ("rose", design_rose), "it": ("mosaic", design_mosaic),
           "rm": ("sgraffito", design_sgraffito), "en": ("forecast", design_forecast)}
