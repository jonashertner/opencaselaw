"""proceeding_map — fine proceeding-type classification (backlog #56 / P0.1).

``proceeding_type`` = a slug from the LegalStats wishlist §0 taxonomy
(Fedlex-anchored: ZPO 219/243/248/308/319, SchKG 17/80/271, BGG 72/78/82/
113/120/121, StPO 393/398/410, VwVG 44), ``procedural_code`` = the code
family (zpo | schkg | bgg | og | stpo | vwvg | vrg_<kt> | atsg).

Same construction discipline as branch_map: register/docket codes with
fixed, documented meanings only — NULL over guess. Composite courts
without a verified vocabulary stay None until researched.

Sources of the mappings:
- BGer: the BGG-era register letter IS the proceeding (A=Beschwerde,
  B=Beschwerde in Strafsachen (1B) , D=subsidiäre Verfassungsbeschwerde,
  E=Klage, F=Revision, G=Erläuterung/Berichtigung); OG-era letters and the
  EVG single-letter registers get og_/evg_ slugs.
- Cantonal register codes verified via chamber names / wishlist / the
  2026-07-02 feedback loop (VD ML = mainlevée, GE DCSO = surveillance
  SchKG, ZH RT = Rechtsöffnung ...).
"""
from __future__ import annotations

import re

# ── BGer ────────────────────────────────────────────────────────────────
_BGG_DIV_BRANCH = {"1": "oeff", "2": "oeff", "4": "zivil", "5": "zivil",
                   "6": "straf", "7": "straf", "8": "soz", "9": "soz"}
_BGG_LETTER = {
    # letter -> (slug-template by division-branch, procedural_code)
    "A": ({"oeff": "bgg_beschwerde_oeff", "zivil": "bgg_beschwerde_zivil",
           "straf": "bgg_beschwerde_straf", "soz": "bgg_beschwerde_soz"}, "bgg"),
    "B": ({"oeff": "bgg_beschwerde_straf",   # 1B: strafprozessuale Zwangsmassnahmen
           "zivil": "bgg_beschwerde_zivil",
           "straf": "bgg_beschwerde_straf", "soz": "bgg_beschwerde_soz"}, "bgg"),
    "C": ({"oeff": "bgg_beschwerde_oeff", "zivil": "bgg_beschwerde_zivil",
           "straf": "bgg_beschwerde_straf", "soz": "bgg_beschwerde_soz"}, "bgg"),
    "D": (dict.fromkeys(("oeff", "zivil", "straf", "soz"),
                        "bgg_verfassungsbeschwerde"), "bgg"),
    "E": (dict.fromkeys(("oeff", "zivil", "straf", "soz"), "bgg_klage"), "bgg"),
    "F": (dict.fromkeys(("oeff", "zivil", "straf", "soz"), "bgg_revision"), "bgg"),
    "G": (dict.fromkeys(("oeff", "zivil", "straf", "soz"),
                        "bgg_erlaeuterung"), "bgg"),
}
# OG-era (pre-2007) register letters, dot format ("2A.123/2005")
_OG_LETTER = {
    "A": ("og_verwaltungsgerichtsbeschwerde", "og"),
    "P": ("og_staatsrechtliche_beschwerde", "og"),
    "C": ("og_berufung", "og"),
    "S": ("og_nichtigkeitsbeschwerde", "og"),
}
_BGER_RE = re.compile(r"^(\d)([A-Z])([._ ])\d+/(\d{4})$")
_EVG_RE = re.compile(r"^[IUCHKBPM][\s_]?\d+/\d{2,4}$")


def _bger(docket: str):
    if _EVG_RE.match(docket):
        return "evg_verwaltungsgerichtsbeschwerde", "og"
    m = _BGER_RE.match(docket)
    if not m:
        return None, None
    div, letter, sep, year = m.groups()
    branch = _BGG_DIV_BRANCH.get(div)
    if not branch:
        return None, None
    # dot separator = OG era; BGG register letters otherwise
    if sep == "." or int(year) < 2007:
        slug = _OG_LETTER.get(letter)
        return slug if slug else (None, None)
    entry = _BGG_LETTER.get(letter)
    if not entry:
        return None, None
    return entry[0].get(branch), entry[1]


# ── cantonal register codes (verified subset; ordered longest-first) ────
_CANTONAL = {
    "zh_obergericht": [
        ("LB", "zpo_berufung", "zpo"), ("LA", "zpo_berufung", "zpo"),
        ("LF", "zpo_berufung", "zpo"),
        ("RT", "schkg_rechtsoeffnung", "schkg"),
        ("PS", "schkg_aufsichtsbeschwerde", "schkg"),
        ("SB", "stpo_berufung", "stpo"), ("SU", "stpo_beschwerde", "stpo"),
    ],
    "ge_gerichte": [
        ("ATAS", "sozialversicherungsbeschwerde", "atsg"),
        ("ACJC", "zpo_berufung", "zpo"),
        ("AARP", "stpo_berufung", "stpo"),
        ("ACPR", "stpo_beschwerde", "stpo"),
        ("DCSO", "schkg_aufsichtsbeschwerde", "schkg"),
        ("JTAPI", "vwv_beschwerde_erstinstanz", "vrg_ge"),
        ("ATA", "vwv_beschwerde", "vrg_ge"),
        ("JTBL", "zpo_erstinstanz", "zpo"),
    ],
    "vd": [
        ("CASSO", "sozialversicherungsbeschwerde", "atsg"),
        ("CDAP", "vwv_beschwerde", "vrg_vd"),
        ("CACI", "zpo_berufung", "zpo"),
        ("CAPE", "stpo_berufung", "stpo"),
        ("CREC", "zpo_beschwerde", "zpo"),
        ("CPF", "schkg_beschwerde", "schkg"),
        ("ML", "schkg_rechtsoeffnung", "schkg"),
    ],
    "be_zivilstraf": [
        ("ZK", "zpo_berufung", "zpo"),
        ("ABS", "schkg_aufsichtsbeschwerde", "schkg"),
        ("KES", "zgb_kindes_erwachsenenschutz", "zpo"),
        ("SK", "stpo_berufung", "stpo"),
    ],
}
_VD_COURTS = {"vd_gerichte", "vd_findinfo", "vd_omni"}

# ── single-proceeding courts (court identity is sufficient) ─────────────
COURT_PROCEEDING = {
    "bvger": ("vwvg_beschwerde", "vwvg"),
    "emark": ("vwvg_beschwerde", "vwvg"),          # Asylrekurskommission
    "zh_sozialversicherungsgericht": ("sozialversicherungsbeschwerde", "atsg"),
    "sg_versicherungsgericht": ("sozialversicherungsbeschwerde", "atsg"),
    "bs_sozialversicherungsgericht": ("sozialversicherungsbeschwerde", "atsg"),
    "ag_versicherungsgericht": ("sozialversicherungsbeschwerde", "atsg"),
    "zh_verwaltungsgericht": ("vwv_beschwerde", "vrg_zh"),
    "be_verwaltungsgericht": ("vwv_beschwerde", "vrg_be"),
    "sg_verwaltungsgericht": ("vwv_beschwerde", "vrg_sg"),
    "ag_verwaltungsgericht": ("vwv_beschwerde", "vrg_ag"),
    "sz_verwaltungsgericht": ("vwv_beschwerde", "vrg_sz"),
    "zg_verwaltungsgericht": ("vwv_beschwerde", "vrg_zg"),
    "zg_regierungsrat": ("vwv_beschwerde", "vrg_zg"),
    "ne_jurisprudence_adm": ("vwv_beschwerde", "vrg_ne"),
    "zh_baurekursgericht": ("vwv_rekurs", "vrg_zh"),
    "zh_steuerrekursgericht": ("vwv_rekurs", "vrg_zh"),
    "be_steuerrekurs": ("vwv_rekurs", "vrg_be"),
    "bs_steuerrekurskommission": ("vwv_rekurs", "vrg_bs"),
    "bs_personalrekurskommission": ("vwv_rekurs", "vrg_bs"),
    "sg_verwaltungsrekurskommission": ("vwv_rekurs", "vrg_sg"),
    # Handelsgericht = einzige kantonale Instanz im ordentlichen Verfahren
    "zh_handelsgericht": ("zpo_ordentlich", "zpo"),
    "ag_handelsgericht": ("zpo_ordentlich", "zpo"),
    "sg_handelsgericht": ("zpo_ordentlich", "zpo"),
    "zh_mietgericht": ("zpo_erstinstanz", "zpo"),
    "zh_arbeitsgericht": ("zpo_erstinstanz", "zpo"),
    "bpatger": ("zpo_ordentlich", "zpo"),          # PatGG-Verfahren, zivil
    # uniform-by-nature sources
    "ch_vb": ("vwv_verwaltungspraxis", "vwvg"),    # VPB practice collection
    "ch_bundesrat": ("vwv_beschwerde", "vwvg"),
    "ecthr_chamber": ("emrk_individualbeschwerde", "emrk"),
    "ecthr_committee": ("emrk_individualbeschwerde", "emrk"),
    "ecthr_grand_chamber": ("emrk_individualbeschwerde", "emrk"),
    "hudoc_ch": ("emrk_individualbeschwerde", "emrk"),
    "bge_egmr": ("emrk_individualbeschwerde", "emrk"),
    "mkg": ("militaer_kassationsbeschwerde", "mstp"),
    "zh_kassationsgericht": ("kassationsbeschwerde", "zh_alt"),
    # regulators / supervisory authorities (Verfügungen & Aufsicht)
    "finma": ("aufsichtsverfuegung", "vwvg"),
    "finma_versicherungsrecht": ("aufsichtsverfuegung", "vwvg"),
    "weko": ("kartellverfahren", "kg"),
    "elcom": ("aufsichtsverfuegung", "vwvg"),
    "comcom": ("aufsichtsverfuegung", "vwvg"),
    "postcom": ("aufsichtsverfuegung", "vwvg"),
    "ubi": ("aufsichtsbeschwerde", "vwvg"),
    "edoeb": ("aufsichtsverfuegung", "vwvg"),
    "esbk": ("aufsichtsverfuegung", "vwvg"),
    "eschk": ("tarifgenehmigung", "urg"),
    "estv": ("steuerverfuegung", "vwvg"),
    "bazg": ("zollverfuegung", "vwvg"),
    "rab": ("aufsichtsverfuegung", "vwvg"),
    "preisueberwacher": ("preisempfehlung", "pueg"),
    # attorney supervision
    "be_anwaltsaufsicht": ("anwaltsaufsicht", "bgfa"),
    "ag_anwaltskommission": ("anwaltsaufsicht", "bgfa"),
    "ag_aufsichtskommission": ("anwaltsaufsicht", "bgfa"),
    "zg_anwaltsaufsicht": ("anwaltsaufsicht", "bgfa"),
    "tg_anwaltskommission": ("anwaltsaufsicht", "bgfa"),
    "sav_kantone": ("anwaltsaufsicht", "bgfa"),
    "sav_international": ("anwaltsaufsicht", "bgfa"),
    # directorate/department recourse decisions
    "be_direktionen": ("vwv_beschwerde", "vrg_be"),
    "be_bvd": ("vwv_beschwerde", "vrg_be"),
    "ag_regierungsrat": ("vwv_beschwerde", "vrg_ag"),
    "ag_departement_bvu": ("vwv_beschwerde", "vrg_ag"),
    "ag_departement_vi": ("vwv_beschwerde", "vrg_ag"),
    "ag_departement_gs": ("vwv_beschwerde", "vrg_ag"),
    "ag_departement_bks": ("vwv_beschwerde", "vrg_ag"),
}

# ── text-derived proceeding (EXPERIMENTAL — measured, NOT wired) ────────
# Measured 2026-07-02 against register-code ground truth (4k random rows):
# Rechtsmittel-family precision 83.0%, incremental yield on unmapped rows
# only 12.1%. The failure modes are structural for bag-of-words on the
# head: the text names the SUBJECT matter ("Rechtsöffnung" in a BGer
# Beschwerde about a Rechtsöffnung case) or the LOWER instance's remedy.
# Per NULL-over-guess this tier is deliberately NOT called by
# derive_proceeding; a future variant must anchor on the "Gegenstand:" /
# object-of-proceedings line and re-validate to >95% before wiring.
_TEXT_CLAMP = re.compile(
    r"(Sachverhalt|Erwägung|Aus den Erwägungen|Faits|consid[ée]rant|"
    r"Fatti|in fatto|Considerando|Vu\s*:)", re.I)

# ordered most-specific-first: (regex, {branch: (slug, code)} or fixed)
_TEXT_RULES = [
    (re.compile(r"\bRechts[öo]ffnung|\bmainlev[ée]e|\brigetto\s+dell", re.I),
     {"*": ("schkg_rechtsoeffnung", "schkg")}),
    (re.compile(r"\bBerufung\b|\bappel\b|\bappello\b|\bappellazione\b", re.I),
     {"zivil": ("zpo_berufung", "zpo"), "straf": ("stpo_berufung", "stpo")}),
    (re.compile(r"\bReklamation\b|\breclamo\b", re.I),
     {"zivil": ("zpo_beschwerde", "zpo")}),
    (re.compile(r"\bRevisionsgesuch\b|\bRevision\b|\br[ée]vision\b|\brevisione\b", re.I),
     {"zivil": ("zpo_revision", "zpo"), "straf": ("stpo_revision", "stpo"),
      "oeffentlich": ("vwv_revision", "vwvg"),
      "sozialversicherung": ("atsg_revision", "atsg")}),
    (re.compile(r"\bRekurs\b", re.I),
     {"oeffentlich": ("vwv_rekurs", "vrg"), "zivil": ("zpo_rekurs_alt", "zpo_alt")}),
    (re.compile(r"\bEinsprache\b|\bopposition\b|\bopposizione\b", re.I),
     {"straf": ("stpo_einsprache", "stpo"), "oeffentlich": ("vwv_einsprache", "vwvg")}),
    (re.compile(r"\bBeschwerde\b|\brecours\b|\bricorso\b", re.I),
     {"zivil": ("zpo_beschwerde", "zpo"), "straf": ("stpo_beschwerde", "stpo"),
      "oeffentlich": ("vwv_beschwerde", "vwvg"),
      "sozialversicherung": ("sozialversicherungsbeschwerde", "atsg")}),
    (re.compile(r"\bKlage\b|\bdemande\b|\bazione\b", re.I),
     {"zivil": ("zpo_klage", "zpo")}),
]


def derive_proceeding_from_text(full_text, branch):
    """Rechtsmittel word from the clamped head + coarse branch. None-safe."""
    if not full_text:
        return None, None
    head = full_text[:2000]
    m = _TEXT_CLAMP.search(head)
    if m:
        head = head[:m.start()]
    for rx, table in _TEXT_RULES:
        if rx.search(head):
            hit = table.get(branch or "") or table.get("*")
            if hit:
                slug, code = hit
                # canton-generic vrg gets no canton suffix from text alone
                return slug, code
    return None, None


def derive_proceeding(court, chamber=None, docket_number=None):
    """(proceeding_type, procedural_code) or (None, None). NULL over guess."""
    if not court:
        return None, None
    fixed = COURT_PROCEEDING.get(court)
    if fixed:
        return fixed
    dk = (docket_number or "").strip()
    if court == "bger" and dk:
        return _bger(dk)
    table = _CANTONAL.get("vd" if court in _VD_COURTS else court)
    if table:
        for cand in (dk, (chamber or "").strip()):
            if not cand:
                continue
            for prefix, slug, code in table:
                if cand.startswith(prefix):
                    return slug, code
    return None, None
