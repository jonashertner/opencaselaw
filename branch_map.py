"""branch_map — coarse legal-branch classification (backlog P1.1).

``branch`` ∈ {zivil, straf, oeffentlich, sozialversicherung} or None.

Shared by build_fts5 (decisions.db column), export_parquet (parquet column)
and — later — the MCP search filter, so all three doors agree. Derivation
order, most to least specific, NULL over guess:

  1. COURT_BRANCH        — single-branch courts (court code is sufficient)
  2. DOCKET_RULES        — per-court docket/series codes with fixed meaning
                            (BGer division prefixes, BGE volume romans,
                            GE/VD chamber series, ZH-OGer / BE registers)
  3. chamber-name keywords — official chamber names are reliable labels

Composite courts without a safe rule stay None until their vocabulary is
verified (extended together with the proceeding_type dictionary, P1.3).
"""
from __future__ import annotations

import re

ZIVIL = "zivil"
STRAF = "straf"
OEFF = "oeffentlich"
SOZ = "sozialversicherung"

# ── 1. single-branch courts ─────────────────────────────────────────────
COURT_BRANCH = {
    # sozialversicherung
    "zh_sozialversicherungsgericht": SOZ,
    "bs_sozialversicherungsgericht": SOZ,
    "sg_versicherungsgericht": SOZ,
    "ag_versicherungsgericht": SOZ,
    # straf
    "bstger": STRAF,
    "mkg": STRAF,
    "ag_strafgericht": STRAF,
    "zg_strafgericht": STRAF,
    # zivil
    "bpatger": ZIVIL,
    "zh_handelsgericht": ZIVIL,
    "ag_handelsgericht": ZIVIL,
    "sg_handelsgericht": ZIVIL,
    "ag_zivilgericht": ZIVIL,
    "zh_mietgericht": ZIVIL,
    "zh_arbeitsgericht": ZIVIL,
    # oeffentlich — administrative courts and recourse bodies
    "bvger": OEFF,
    "zh_verwaltungsgericht": OEFF,
    "be_verwaltungsgericht": OEFF,
    "sg_verwaltungsgericht": OEFF,
    "ag_verwaltungsgericht": OEFF,
    "sz_verwaltungsgericht": OEFF,
    "zg_verwaltungsgericht": OEFF,
    "zg_regierungsrat": OEFF,          # GVP: Regierungsrat + Landammann as Beschwerdeinstanz
    "zg_datenschutzstelle": OEFF,
    "ne_jurisprudence_adm": OEFF,
    "ch_vb": OEFF,                      # Verwaltungsbehörden des Bundes
    "ch_bundesrat": OEFF,
    "zh_baurekursgericht": OEFF,
    "zh_steuerrekursgericht": OEFF,
    "be_steuerrekurs": OEFF,
    "bs_steuerrekurskommission": OEFF,
    "bs_personalrekurskommission": OEFF,
    "sg_verwaltungsrekurskommission": OEFF,
    "ag_spezialverwaltungsgericht": OEFF,
    "ag_regierungsrat": OEFF,
    "ag_departement_bvu": OEFF,
    "ag_departement_vi": OEFF,
    "ag_departement_gs": OEFF,
    "ag_departement_bks": OEFF,
    "ag_baugesetzgebung": OEFF,
    "ag_justizgericht": OEFF,
    "be_direktionen": OEFF,
    "be_bvd": OEFF,
    "emark": OEFF,                      # Asylrekurskommission
    # attorney-supervision (disciplinary/administrative)
    "ag_anwaltskommission": OEFF,
    "ag_aufsichtskommission": OEFF,
    "zg_anwaltsaufsicht": OEFF,
    "be_anwaltsaufsicht": OEFF,
    "tg_anwaltskommission": OEFF,
    "sav_kantone": OEFF,
    "sav_international": OEFF,
    # federal regulators
    "edoeb": OEFF, "finma": OEFF, "finma_versicherungsrecht": OEFF,
    "weko": OEFF, "elcom": OEFF, "comcom": OEFF, "postcom": OEFF,
    "ubi": OEFF, "eschk": OEFF, "esbk": OEFF, "estv": OEFF,
    "bazg": OEFF, "rab": OEFF, "preisueberwacher": OEFF,
    # human-rights (Konventionsrecht — public law)
    "ecthr_chamber": OEFF, "ecthr_committee": OEFF,
    "ecthr_grand_chamber": OEFF, "hudoc_ch": OEFF, "bge_egmr": OEFF,
}

# ── 2. per-court docket rules ───────────────────────────────────────────
_BGER_DIVISION = {"1": OEFF, "2": OEFF, "4": ZIVIL, "5": ZIVIL,
                  "6": STRAF, "7": STRAF, "8": SOZ, "9": SOZ}
# separator variants all occur in the corpus: 5A_1008/2025, 2A.123/2005
# (pre-2007 dot), 1C 146/2025 (space, ~45k rows)
_BGER_RE = re.compile(r"^(\d)[A-Z][._ ]\d+/\d{4}$")
# pre-2007 EVG single-letter registers (I/U/C/H/K/B/P/M) — all social law;
# space AND underscore separators occur (I 123/04, I_350/1999)
_EVG_RE = re.compile(r"^[IUCHKBPM][\s_]?\d+/\d{2,4}$")
# BGE volume citations: "150 II 1", "BGE 150 II 1", historical "84_II_437"
_BGE_RE = re.compile(r"^(?:BGE[\s_]+)?\d+[\s_]+(IV|III|II|Ia|Ib|I|V)(?:[\s_]|$)")
_BGE_ROMAN = {"I": OEFF, "Ia": OEFF, "Ib": OEFF, "II": OEFF,
              "III": ZIVIL, "IV": STRAF, "V": SOZ}
# GE Cour de justice series (ordered: longest prefix first)
_GE_SERIES = [("ATAS", SOZ), ("ACJC", ZIVIL), ("AARP", STRAF),
              ("ACPR", STRAF), ("DCSO", ZIVIL), ("JTAPI", OEFF),
              ("ACST", OEFF), ("CAPH", ZIVIL), ("ATA", OEFF),
              ("JTBL", ZIVIL),   # Tribunal des baux et loyers
              ("DAS/", ZIVIL)]   # Chambre de surveillance (protection adulte/enfant)
# VD Tribunal cantonal chamber codes (registers "AI " = assurance-invalidité,
# "ML " = mainlevée carry a trailing space to avoid prefix collisions)
_VD_SERIES = [("CASSO", SOZ), ("CDAP", OEFF), ("CACI", ZIVIL),
              ("CAPE", STRAF), ("CREC", ZIVIL), ("CPF", ZIVIL),
              ("AI ", SOZ), ("ML ", ZIVIL)]
# GR Kantonsgericht/Verwaltungsgericht series (safe subset; single letters
# and the PKG/PVG Praxis volumes are mixed and stay NULL)
_GR_SERIES = [("ZK", ZIVIL), ("ZR", ZIVIL), ("KSK", ZIVIL), ("SBK", ZIVIL),
              ("SK", STRAF), ("VR", OEFF)]
# ZH Obergericht register codes (safe subset)
_ZH_OG = {"LA": ZIVIL, "LB": ZIVIL, "LF": ZIVIL, "PS": ZIVIL, "RT": ZIVIL,
          "SB": STRAF, "SU": STRAF}
_ZH_OG_RE = re.compile(r"^([A-Z]{2})\d")


def _bger_rule(docket: str):
    m = _BGER_RE.match(docket)
    if m:
        return _BGER_DIVISION.get(m.group(1))
    if _EVG_RE.match(docket):
        return SOZ
    return None


def _bge_rule(docket: str):
    m = _BGE_RE.match(docket)
    return _BGE_ROMAN.get(m.group(1)) if m else None


def _series_rule(series):
    def rule(docket: str):
        for prefix, branch in series:
            if docket.startswith(prefix):
                return branch
        return None
    return rule


def _zh_og_rule(docket: str):
    m = _ZH_OG_RE.match(docket)
    return _ZH_OG.get(m.group(1)) if m else None


def _be_zivilstraf_rule(docket: str):
    # zivil registers per the LegalStats 2026-07-02 feedback diff (their
    # chamber dictionary found 603 zivil rows vs our ZK-only 403): ABS =
    # Aufsichtsbehörde SchKG, KES = Kindes-/Erwachsenenschutz, HG =
    # Handelsgericht, CIV = French-form civil register.
    for prefix in ("ZK", "ABS", "KES", "HG", "CIV"):
        if docket.startswith(prefix):
            return ZIVIL
    if docket.startswith("SK"):
        return STRAF
    return None


DOCKET_RULES = {
    "bger": _bger_rule,
    "bge": _bge_rule,
    "ge_gerichte": _series_rule(_GE_SERIES),
    "vd_gerichte": _series_rule(_VD_SERIES),
    "vd_findinfo": _series_rule(_VD_SERIES),
    "vd_omni": _series_rule(_VD_SERIES),
    "zh_obergericht": _zh_og_rule,
    "be_zivilstraf": _be_zivilstraf_rule,
    "gr_gerichte": _series_rule(_GR_SERIES),
}

# ── 3. official chamber-name keywords ───────────────────────────────────
_KW = [
    (re.compile(r"sozialversicherung|assurances?\s+sociales|assicurazioni\s+sociali|versicherungsgericht|sozialrecht", re.I), SOZ),
    (re.compile(r"straf|p[ée]nal|penale", re.I), STRAF),
    (re.compile(r"verwaltungs|öffentlich|oeffentlich|administrati[fv]|amministrativ|droit\s+public|abgaben|steuerrekurs", re.I), OEFF),
    (re.compile(r"zivil(?!stand)|civil", re.I), ZIVIL),
]


def _chamber_rule(chamber: str):
    for rx, branch in _KW:
        if rx.search(chamber):
            return branch
    return None


# ── chamber code from the docket (backlog P1.2) ────────────────────────
# Where the portal supplied no chamber, the docket's register/series code IS
# the chamber signal (wishlist P1.7: "copy the parsed docket code into it").
# Only structurally unambiguous shapes; document-type words are never codes.
_DOC_TYPE_TOKENS = {"ARRET", "ARRÊT", "JUG", "URTEIL", "ENTSCHEID", "BGE",
                    "DEC", "DECISION", "SENTENZA", "VPB"}
_REGISTER_SHAPE_RE = re.compile(
    r"^([A-Za-z]{1,8}\d{0,2})[ ._/-](?:18|19|20)\d\d[ ._/-]\d")  # WBE.2020.195 / SR2 2025 84
_SERIES_SLASH_RE = re.compile(r"^([A-Z]{2,6})/\d")               # ATAS/1001/2007
_BGER_CODE_RE = re.compile(r"^(\d[A-Z])[._ ]\d+/\d{4}$")         # 5A_1008/2025 -> 5A
_VD_LOOSE_RES = (
    re.compile(r"^([A-Z]{1,6}) / (?:18|19|20)\d\d"),             # HC / 2010 / 123
    re.compile(r"^([A-Z]{1,6}) \d+/\d{2}\b"),                    # AI 123/09
    re.compile(r"^([A-Z]{2,6}) [A-Z]{2}\."),                     # CDAP GE.2021.0001
)
_VD_COURTS = {"vd_gerichte", "vd_findinfo", "vd_omni"}


def docket_chamber_code(court, docket_number):
    """Register/series code from the docket, or None. Used to fill an empty
    chamber field (P1.2); never overwrites portal-supplied chambers."""
    if not docket_number:
        return None
    dk = docket_number.strip()
    if court == "bger":
        m = _BGER_CODE_RE.match(dk)
        if m:
            return m.group(1)
        if _EVG_RE.match(dk):
            return dk[0]
        return None
    if court == "bge":
        m = _BGE_RE.match(dk)
        return m.group(1) if m else None
    m = _REGISTER_SHAPE_RE.match(dk) or _SERIES_SLASH_RE.match(dk)
    if not m and court in _VD_COURTS:
        for rx in _VD_LOOSE_RES:
            m = rx.match(dk)
            if m:
                break
    if not m:
        return None
    code = m.group(1)
    if code.upper() in _DOC_TYPE_TOKENS:
        return None
    return code


def derive_branch(court, chamber=None, docket_number=None):
    """Coarse branch for one decision, or None. NULL over guess."""
    if not court:
        return None
    b = COURT_BRANCH.get(court)
    if b:
        return b
    rule = DOCKET_RULES.get(court)
    if rule:
        # Some portals put the series code in the chamber field instead of
        # (or as well as) the docket (VD: chamber="CACI"), so try both.
        for candidate in (docket_number, chamber):
            if candidate:
                b = rule(candidate.strip())
                if b:
                    return b
    if chamber:
        b = _chamber_rule(chamber)
        if b:
            return b
    return None
