"""One docket -> decision_id resolver for the identifiers users actually type.

get_decision, cite, check_claim_support and find_citations all funnel through
mcp_server's lookup ladder (exact id, exact docket_number, joined-docket
alias, parsed reference, ECtHR application number, then a LIKE scan). The
ladder matches on the *stored* docket string, so it misses whenever the stored
form and the typed form differ in a way none of the earlier steps normalises —
and the LIKE scan that follows costs ~2 s on the 1M-row table and is gated off
for anything that does not look docket-like.

Measured on the production capture journal (2026-09-06..09): the misses are
bare cantonal dockets ('UH220412', 'PE.2026.0063', 'ATA/508/2014'), dotted
pre-2007 federal dockets ('4C.193/2000', '2P.83/2004') and their canonical ids
typed with the modern underscore ('bger_4C_194_2006'), EVG two-digit years
('H_302/03', 'P 33/96'), BVGer files ('B-822/2016', 'F-5046/2026'), ECtHR
application numbers ('17153/11', 'n° 22060/20'), percent-encoded canonical ids
('bge_127%20I%2038'), the ZH Verwaltungsgericht ids stored with a double
underscore ('zh_verwaltungsgericht__VB.2022.00753'), and the space form of our
own BGer citation strings (GitHub #76: '4A 123/2024' must round-trip to
'bger_4A_123_2024').

This module is pure (no DB, no I/O beyond one guarded read of
docs/coverage_notes.json). ``resolve_decision_ref`` turns a typed reference
into an ORDERED list of candidate decision_ids built from the id grammar each
scraper mints (``models.make_decision_id`` = court + '_' + docket with '/'
replaced by '_'; entscheidsuche rows replace every non-[A-Za-z0-9_.-] char with
'_'). The server tries them as primary-key lookups — a handful of indexed hits
instead of a table scan — and takes the first that exists. Because every
candidate is the exact stored id for that docket in that court, a hit can only
be the decision that carries the typed docket; the cite handler's identity
check still verifies that the decision carries the label the reference wrote.

Court inference is by docket grammar, verified against the ids in the corpus
(tests/test_decision_ref_resolver.py pins the observed shapes). Where a
grammar is shared by several courts (a dotted 'XX.YYYY.N' is BStGer, the
Aargau and St. Gallen courts, and — five-digit — the ZH administrative courts)
the candidates cover every court in a fixed preference order rather than
guessing one: a primary-key lookup is cheap, a wrong guess is not.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import reference_parser

# ── Coverage note for the honest not-found outcomes ──────────────────────

_FALLBACK_BGER_NOTE = (
    "Complete for published decisions from 2000 (verified against citation-gap "
    "oracles). Pre-2000 ordinary judgments were never put online by the court. "
    "Pre-2007 EVG social-insurance decisions (~15,215) are recoverable and queued."
)


def _load_bger_note() -> str:
    try:
        path = Path(__file__).resolve().parent / "docs" / "coverage_notes.json"
        note = json.loads(path.read_text(encoding="utf-8")).get("bger")
        return note if isinstance(note, str) and note.strip() else _FALLBACK_BGER_NOTE
    except Exception:  # noqa: BLE001 — a missing docs/ tree must not break serving
        return _FALLBACK_BGER_NOTE


BGER_COVERAGE_NOTE = _load_bger_note()

# The Federal Supreme Court put its ordinary judgments online from 2000; the
# EVG (social-insurance court, merged into the BGer in 2007) backlog is queued.
FIRST_ONLINE_YEAR_BGER = 2000
EVG_MERGER_YEAR = 2007

# ── Vocabularies (observed in corpus ids; see the tests) ─────────────────

FEDERAL_COURTS = ("bger", "bge", "bvger", "bstger", "bpatger")
CANTON_CODES = ("ag", "ai", "ar", "be", "bl", "bs", "fr", "ge", "gl", "gr", "ju", "lu",
                "ne", "nw", "ow", "sg", "sh", "so", "sz", "tg", "ti", "ur", "vd", "vs", "zg", "zh")

# EVG-era single-letter chambers (I = IV, U = UV, H = AHV, K = KV, C = ALV,
# B = BV, P = EL, M = MV). Stored as 'bger_I_538_99' with the two-digit year.
EVG_CHAMBERS = frozenset("BCHIKMPU")

# ZH Obergericht Geschäftsnummern: two letters + six digits ('UH220412'). The
# same grammar serves the Handelsgericht (HG/HE), the former Kassationsgericht
# (AC), the Bezirksgerichte and the Arbeits-/Mietgericht ('AH230041-L').
ZH_OBERGERICHT_PREFIXES = frozenset((
    "UH", "UE", "UK", "PS", "PF", "PC", "PE", "PD", "PP", "PQ", "PN", "LB", "LF", "LA",
    "LC", "LE", "LZ", "LY", "LG", "RT", "RA", "RB", "RE", "RV", "SB", "SU", "SE", "SF",
    "SM", "VB", "VO", "NG", "NQ", "NP", "NF", "NE", "NO", "OB", "CN", "SR",
))
ZH_HANDELSGERICHT_PREFIXES = frozenset(("HG", "HE"))
ZH_KASSATIONSGERICHT_PREFIXES = frozenset(("AC", "AA"))
ZH_SIX_DIGIT_COURTS = (
    "zh_obergericht", "zh_handelsgericht", "zh_kassationsgericht", "zh_gerichte",
    "zh_arbeitsgericht", "zh_mietgericht", "zh_bezirksgericht_zuerich",
    "zh_bezirksgericht_winterthur", "zh_bezirksgericht_uster",
    "zh_bezirksgericht_pfaeffikon", "zh_bezirksgericht_meilen", "zh_bezirksgericht_horgen",
    "zh_bezirksgericht_hinwil", "zh_bezirksgericht_dietikon", "zh_bezirksgericht_dielsdorf",
    "zh_bezirksgericht_buelach", "zh_bezirksgericht_andelfingen",
    "zh_bezirksgericht_affoltern",
)

# ZH Verwaltungsgericht ('VB.2025.00683', five-digit serial) and
# Sozialversicherungsgericht ('IV.2006.00678') chambers.
ZH_VERWALTUNGSGERICHT_PREFIXES = frozenset(("VB", "PB", "VR", "SB", "SR", "VK", "PK", "AN", "AB", "SV"))
ZH_SOZIALVERSICHERUNGSGERICHT_PREFIXES = frozenset((
    "IV", "UV", "KV", "BV", "ZL", "AL", "AK", "KK", "OH", "SR", "KA", "EL", "EO", "MV", "IB", "IZ", "AB",
))
ZH_ADMIN_SERIAL_WIDTH = 5

# Bundesstrafgericht collections ('SK.2023.12', 'BB.2023.1', 'CA.2023.1').
BSTGER_PREFIXES = frozenset((
    "SK", "SN", "BB", "BE", "BG", "BH", "BK", "BN", "BP", "BV", "CA", "CN", "CR",
    "RR", "RH", "RP", "SP", "TK", "AV", "BR",
))
# Other courts whose docket grammar is LETTERS.YYYY.N (1-4 digits).
DOTTED_SERIAL_COURTS = (
    "ag_verwaltungsgericht", "ag_strafgericht", "ag_zivilgericht", "ag_handelsgericht",
    "ag_versicherungsgericht", "ag_spezialverwaltungsgericht", "ag_gerichte",
    "ag_anwaltskommission", "ag_aufsichtskommission", "ag_justizgericht",
    "sg_kantonsgericht", "sg_gerichte", "sg_verwaltungsgericht", "sg_versicherungsgericht",
    "sg_handelsgericht", "sg_verwaltungsrekurskommission", "sg_publikationen",
    "bs_appellationsgericht", "bs_gerichte", "bl_gerichte",
)
# Vaud CDAP/CREP files ('PE.2026.0063', four-digit serial).
VD_DOTTED_COURTS = ("vd_gerichte", "vd_findinfo", "vd_omni")
VD_SERIAL_WIDTH = 4

# Geneva judgment and case numbers ('ACPR/635/2024', 'A/279/2011').
GE_PREFIXES = frozenset((
    "ACPR", "ACJC", "AARP", "ATA", "ATAS", "DAS", "DCSO", "JTPI", "JTAPI", "JTDP",
    "JTCO", "CAPH", "CAPJ", "AJP", "ACST", "DCBA", "PS", "AC", "A", "C", "P",
))
# Vaud FindInfo files are stored with spaces around the slashes ('HC / 2018 /
# 391' -> 'vd_findinfo_HC___2018___391').
VD_SLASH_COURTS = ("vd_findinfo", "vd_gerichte", "vd_omni")

# ── Shapes ────────────────────────────────────────────────────────────────

# A canonical id: court code (lowercase words joined by '_'), then '_' or '__'
# or a space, then the docket part (starting with a digit, an upper-case
# letter, or a BGE label). 'zh_verwaltungsgericht__VB.2022.00753',
# 'bge_151 I 3', 'bger 4A_123/2024', 'ch_vb_JAAC_60.2'.
_PREFIXED_RE = re.compile(r"^([a-z]{2,}(?:_[a-z]+)*)(?:_{1,2}| +)(?=[0-9A-Z])(.+)$")
_URL_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_PINPOINT_TAIL_RE = re.compile(
    r"\s*,?\s+(?:E|Erw|Erwägung|consid|cons|c)\.?\s+\d+(?:\.\d+)*[a-z]?(?:/[a-z]+)?\s*$",
    re.IGNORECASE,
)
_DATE_TAIL_RE = re.compile(
    r"[\s,;]*(?:vom|du|del|am|le|il|of|dal)\s+\d{1,2}(?:\.|er|re|º|°)?\s*[A-Za-zÀ-ÿ.]*\s*\d{2,4}\s*$",
    re.IGNORECASE,
)
_COURT_HEAD_RE = re.compile(
    r"^(?:(?:Urteil|Entscheid|Beschluss|Verfügung|arrêt|décision|jugement|sentenza|decisione|"
    r"Bundesgericht(?:s|es)?|BGer|TF|Tribunal\s+f[ée]d[ée]ral|Tribunale\s+federale|"
    r"Bundesverwaltungsgericht(?:s|es)?|BVGer|TAF|Tribunal\s+administratif\s+f[ée]d[ée]ral|"
    r"Bundesstrafgericht(?:s|es)?|BStGer|TPF|Tribunal\s+p[ée]nal\s+f[ée]d[ée]ral|"
    r"Bundespatentgericht(?:s|es)?|BPatGer|TFB|EGMR|ECtHR|CourEDH|CEDH|EVG|"
    r"Obergericht(?:s|es)?|OGer|Verwaltungsgericht(?:s|es)?|VGr|VGer|"
    r"Sozialversicherungsgericht(?:s|es)?|SVGer|Kantonsgericht(?:s|es)?|KGer|Handelsgericht(?:s|es)?|HGer|"
    r"Cour\s+de\s+justice|Tribunal\s+cantonal|Chambre|Cour|Kanton|canton|des|der|du|de|di|del|"
    r"AG|AI|AR|BE|BL|BS|FR|GE|GL|GR|JU|LU|NE|NW|OW|SG|SH|SO|SZ|TG|TI|UR|VD|VS|ZG|ZH)"
    r"(?:[\s,.:]+|$))+",
)

# Docket grammars, on the cleaned core. Separators are deliberately loose so
# that space, underscore, dot and slash spellings all reach the same ids.
_BGE_RE = re.compile(r"^(?:(?:BGE|ATF|DTF)[\s_]*)?(\d{1,3})[\s_]+([IVX]+)([ab]?)[\s_]+(\d{1,4})$", re.IGNORECASE)
_FEDERAL_RE = re.compile(r"^(\d{1,2}[A-Z]{1,2})[._ ]?(\d{1,5})[/_ ](\d{4})$")
_EVG_RE = re.compile(r"^([A-Z])[._ ]?(\d{1,5})[/_ ](\d{2}|\d{4})$")
_BVGER_RE = re.compile(r"^([A-F])[-_ ]?(\d{1,6})[/_ ](\d{4})$")
_BVGE_RE = re.compile(r"^BVGE[\s_]*(\d{4})[/_ ](\d{1,4})$", re.IGNORECASE)
_BPATGER_RE = re.compile(r"^([OS])[\s_]?(\d{4})[_ /.-](\d{1,3})$")
_DOTTED_RE = re.compile(r"^([A-Z]{1,5})[. _](\d{4})[. _](\d{1,6})$")
_ZH_SIX_RE = re.compile(r"^([A-Z]{2})[\s_]?(\d{6})([a-z]|-[A-Z](?:_U\d+)?)?$")
_SLASH_RE = re.compile(r"^([A-Z]{1,5})\s*/\s*(\d{1,6})\s*/\s*(\d{4})$")
_GE_SPACED_RE = re.compile(r"^([A-Z]{1,5})[ _](\d{1,6})[ _](\d{4})$")
_VD_SLASH_RE = re.compile(r"^([A-Z]{1,5})\s*/\s*((?:19|20)\d{2})\s*/\s*(\d{1,6})$")
_ZH_AGER_RE = re.compile(r"^AGer-Z\s*(\d{4})\s*Nr\.?\s*(\d{1,4})$", re.IGNORECASE)
_ECTHR_BARE_RE = re.compile(r"^(\d{1,6})/(\d{2})$")
_ECTHR_LABELLED_RE = re.compile(
    r"(?:\b(?:n[o°º]|no|nr|nos|req(?:uête)?|application)\.?\s*)(\d{1,6}/\d{2})(?![0-9/])",
    re.IGNORECASE,
)


def percent_decode(text: str | None) -> str:
    """Decode a URL-encoded reference ('bge_127%20I%2038'); other text unchanged."""
    s = (text or "").strip()
    if s and _URL_ENCODED_RE.search(s):
        try:
            return unquote(s).strip()
        except Exception:  # noqa: BLE001
            return s
    return s


def normalise_ref(text: str | None) -> str:
    """Comparison form of a typed reference: percent-decoded, unwrapped,
    whitespace-collapsed, trailing pinpoint and date phrase removed."""
    s = percent_decode(text)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip().strip("()[]«»\"'").strip(" ,;:")
    s = _PINPOINT_TAIL_RE.sub("", s)
    s = _DATE_TAIL_RE.sub("", s)
    return s.strip(" ,;:.")


def _dedupe(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _expand_year(y: str) -> str:
    if len(y) == 4:
        return y
    return ("20" if int(y) < 40 else "19") + y


def _bge_ids(vol: str, div: str, page: str) -> list[str]:
    return [f"bge_BGE_{vol}_{div}_{page}", f"bge_{vol}_{div}_{page}", f"bge_{vol} {div} {page}"]


# ── Grammar inference on a bare docket ───────────────────────────────────

def _grammar_candidates(core: str, courts: set[str] | None = None, canton: str | None = None) -> list[str]:
    """Candidate ids for a bare docket ``core`` by docket grammar. ``courts`` /
    ``canton`` (from the reference parser: 'BGer 4A_1/2020', 'Obergericht ZH
    LA210005') restrict the courts tried."""
    out: list[str] = []

    m = _BGE_RE.match(core)
    if m:
        out += _bge_ids(m.group(1), m.group(2).upper() + m.group(3).lower(), m.group(4))

    m = _FEDERAL_RE.match(core)
    if m:
        ch, n, y = m.group(1), m.group(2), m.group(3)
        out += [f"bger_{ch}_{n}_{y}", f"bger_{ch}.{n}_{y}"]

    m = _EVG_RE.match(core)
    if m and m.group(1) in EVG_CHAMBERS:
        ch, n, y = m.group(1), m.group(2), m.group(3)
        # Stored with the two-digit year as printed ('bger_I_538_99'); try the
        # four-digit spelling too in case a row was minted from a modern feed.
        years = [y, _expand_year(y)] if len(y) == 2 else [y[2:], y]
        out += [f"bger_{ch}_{n}_{yy}" for yy in years]

    m = _BVGER_RE.match(core)
    if m:
        out.append(f"bvger_{m.group(1)}-{m.group(2)}_{m.group(3)}")
    m = _BVGE_RE.match(core)
    if m:
        out += [f"bvger_BVGE {m.group(1)}_{m.group(2)}", f"bvger_BVGE_{m.group(1)}_{m.group(2)}"]

    m = _BPATGER_RE.match(core)
    if m:
        letter, y, n = m.group(1), m.group(2), m.group(3)
        out += _dedupe([f"bpatger_{letter}{y}_{int(n):03d}", f"bpatger_{letter}{y}_{n}"])

    m = _DOTTED_RE.match(core)
    if m:
        prefix, y, n = m.group(1), m.group(2), m.group(3)
        zh_n = n.zfill(ZH_ADMIN_SERIAL_WIDTH) if len(n) <= ZH_ADMIN_SERIAL_WIDTH else n
        vd_n = n.zfill(VD_SERIAL_WIDTH) if len(n) <= VD_SERIAL_WIDTH else n
        by_court: dict[str, list[str]] = {
            "bstger": [f"bstger_{prefix}.{y}.{n}"],
            "zh_verwaltungsgericht": [f"zh_verwaltungsgericht_{prefix}.{y}.{zh_n}",
                                      f"zh_verwaltungsgericht__{prefix}.{y}.{zh_n}"],
            "zh_sozialversicherungsgericht": [f"zh_sozialversicherungsgericht_{prefix}.{y}.{zh_n}"],
        }
        for court in DOTTED_SERIAL_COURTS:
            by_court[court] = [f"{court}_{prefix}.{y}.{n}"]
        for court in VD_DOTTED_COURTS:
            by_court[court] = [f"{court}_{prefix}.{y}.{vd_n}"]
        order: list[str] = []
        if len(n) == ZH_ADMIN_SERIAL_WIDTH or prefix in ZH_VERWALTUNGSGERICHT_PREFIXES | ZH_SOZIALVERSICHERUNGSGERICHT_PREFIXES:
            if prefix in ZH_SOZIALVERSICHERUNGSGERICHT_PREFIXES and prefix not in ZH_VERWALTUNGSGERICHT_PREFIXES:
                order += ["zh_sozialversicherungsgericht", "zh_verwaltungsgericht"]
            else:
                order += ["zh_verwaltungsgericht", "zh_sozialversicherungsgericht"]
        if prefix in BSTGER_PREFIXES:
            order.append("bstger")
        order += ["bstger", "zh_verwaltungsgericht", "zh_sozialversicherungsgericht"]
        order += list(DOTTED_SERIAL_COURTS) + list(VD_DOTTED_COURTS)
        for court in _dedupe(order):
            out += by_court[court]

    m = _ZH_SIX_RE.match(core)
    if m:
        prefix, digits, suffix = m.group(1), m.group(2), m.group(3) or ""
        docket = f"{prefix}{digits}{suffix}"
        order = []
        if prefix in ZH_HANDELSGERICHT_PREFIXES:
            order.append("zh_handelsgericht")
        elif prefix in ZH_KASSATIONSGERICHT_PREFIXES:
            order.append("zh_kassationsgericht")
        order += list(ZH_SIX_DIGIT_COURTS)
        out += [f"{court}_{docket}" for court in _dedupe(order)]

    m = _SLASH_RE.match(core) or _GE_SPACED_RE.match(core)
    if m:
        prefix, n, y = m.group(1), m.group(2), m.group(3)
        ge = [f"ge_gerichte_{prefix}_{n}_{y}"]
        vd = [f"{court}_{prefix}___{n}___{y}" for court in VD_SLASH_COURTS]
        vd += [f"{court}_{prefix}_{n}_{y}" for court in VD_SLASH_COURTS]
        out += ge + vd if prefix in GE_PREFIXES else vd + ge
    m = _VD_SLASH_RE.match(core)
    if m:
        # Vaud writes PREFIX / YEAR / N ('HC / 2018 / 391'), Geneva PREFIX/N/YEAR.
        prefix, y, n = m.group(1), m.group(2), m.group(3)
        out += [f"{court}_{prefix}___{y}___{n}" for court in VD_SLASH_COURTS]
        out += [f"{court}_{prefix}_{y}_{n}" for court in VD_SLASH_COURTS]

    m = _ZH_AGER_RE.match(core)
    if m:
        out.append(f"zh_arbeitsgericht_AGer-Z {m.group(1)} Nr. {int(m.group(2))}")

    m = _ECTHR_BARE_RE.match(core)
    if m:
        out.append(f"hudoc_ch_{m.group(1)}_{m.group(2)}")

    out = _dedupe(out)
    # A named court or canton is a restriction, never a hint: 'Obergericht ZH
    # LA210005' must not fall back to the other cantons' courts.
    if courts:
        out = [c for c in out if _court_of(c) in courts]
    if canton:
        out = [c for c in out if _court_of(c).startswith(canton.lower() + "_")]
    return out


def _court_of(decision_id: str) -> str:
    m = _PREFIXED_RE.match(decision_id)
    return m.group(1) if m else ""


# ── Canonical-id spelling variants ───────────────────────────────────────

def _prefixed_candidates(court: str, rest: str) -> list[str]:
    """Spelling variants of a court-prefixed id: separators (space / '_' / '/'),
    the ZH Verwaltungsgericht double underscore, federal dot-vs-underscore
    chambers, EVG two-digit years, and a BGE tuple filed under bger/bge."""
    rests = [rest, rest.replace("/", "_"), rest.replace(" ", "_"), rest.replace("/", "_").replace(" ", "_"),
             rest.replace("_", " ")]
    if "__" in rest:
        rests.append(re.sub(r"_{2,}", "_", rest))
    # No minted id carries a slash (make_decision_id and the entscheidsuche
    # normaliser both replace it), so a slash spelling is never a candidate.
    out = [f"{court}_{r}" for r in _dedupe(rests) if "/" not in r]
    if court == "zh_verwaltungsgericht":
        out += [f"{court}__{r}" for r in _dedupe(rests) if not r.startswith("_")]

    canton_code = court.split("_", 1)[0]
    if court in FEDERAL_COURTS or canton_code in CANTON_CODES:
        # 'bger_4C_194_2006' typed for a decision stored as 'bger_4C.194_2006',
        # 'zh_NG230021' (canton only) or 'zh_gerichte_MJ210045-L' (the row was
        # re-homed to zh_arbeitsgericht by the court-code repair) — reuse the
        # grammar, scoped to the court's canton. These come last: the direct
        # spelling variants above are tried first.
        canton = canton_code.upper() if canton_code in CANTON_CODES else None
        courts = {court} if court in FEDERAL_COURTS else None
        if court in ("bger", "bge"):
            courts = {"bger", "bge"}
        out += _grammar_candidates(_rest_as_docket(rest), courts, canton)
    return _dedupe(out)


def _rest_as_docket(rest: str) -> str:
    """The docket part of an id, in a spelling the grammars recognise: the last
    underscore becomes the number/year slash for federal shapes, all
    underscores become spaces for BGE tuples."""
    m = re.match(r"^(\d{1,2}[A-Z]{1,2}|[A-Z]|[A-F]-|[A-Z]{1,5})[._ -]?(\d{1,6})_(\d{2,4})$", rest)
    if m:
        head = m.group(1)
        if head.endswith("-"):
            return f"{head}{m.group(2)}/{m.group(3)}"
        sep = "." if "." in rest[: len(head) + 1] else "_"
        if re.fullmatch(r"[A-Z]{1,5}", head) and head not in EVG_CHAMBERS:
            return f"{head}/{m.group(2)}/{m.group(3)}"
        return f"{head}{sep}{m.group(2)}/{m.group(3)}"
    if re.match(r"^(?:BGE_)?\d{1,3}_[IVXab]+_\d{1,4}$", rest):
        return rest.replace("_", " ")
    return rest


# ── Public API ───────────────────────────────────────────────────────────

def resolve_decision_ref(text: str | None) -> list[str]:
    """Ordered candidate decision_ids for a typed reference. [] when the text
    is not a reference this resolver understands. Never includes the input
    itself: the caller has already tried it as an exact id."""
    raw = (text or "").strip()
    if not raw or len(raw) > 200:
        return []
    core = normalise_ref(raw)
    if not core:
        return []
    out: list[str] = []

    m = _PREFIXED_RE.match(core)
    if m:
        out += _prefixed_candidates(m.group(1), m.group(2).strip())
    else:
        bare = _COURT_HEAD_RE.sub("", core).strip(" ,;:") or core
        courts: set[str] = set()
        canton = None
        try:
            parsed = reference_parser.parse_reference(core)
            courts = {c for c in parsed.courts if c in FEDERAL_COURTS}
            canton = parsed.canton
            primary = parsed.primary_docket
        except Exception:  # noqa: BLE001 — the parser must never break resolution
            primary = None
        for candidate_text in _dedupe([bare, core, primary or ""]):
            out += _grammar_candidates(candidate_text, courts or None, canton)
        appno = application_number(core)
        if appno:
            out.append(f"hudoc_ch_{appno.replace('/', '_')}")
    return [c for c in _dedupe(out) if c != raw]


def application_number(text: str | None) -> str | None:
    """The ECtHR application number a reference names ('17153/11', 'n° 22060/20',
    'application no. 34812/15'), or None. Bare 'NNNNN/YY' only when the whole
    reference is that number, so a cantonal 'A/279/2011' is never read as one."""
    core = normalise_ref(text)
    m = _ECTHR_BARE_RE.match(core)
    if m:
        return core
    m = _ECTHR_LABELLED_RE.search(core)
    return m.group(1) if m else None


def unavailable_reason(text: str | None) -> dict | None:
    """Why a federal docket that resolves to nothing is absent, when the corpus
    coverage notes say so — a structured, honest outcome instead of a bare
    not-found. Two cases, both from docs/coverage_notes.json (bger):

      never_published_online  an ordinary BGer judgment (chamber '1A', '4C',
                              ...) dated before 2000: the court never put them
                              online, so no source can hold it.
      not_yet_ingested        an EVG social-insurance decision (single-letter
                              chamber I/U/H/K/C/B/P/M) before the 2007 merger:
                              recoverable, queued, not yet in the corpus.

    Returns None when the reference is not a federal docket of those shapes
    or is within the covered years (then the not-found stands on its own).
    """
    core = normalise_ref(text)
    if not core:
        return None
    m = _PREFIXED_RE.match(core)
    if m:
        if m.group(1) not in ("bger", "bge"):
            return None
        core = _rest_as_docket(m.group(2).strip())
    else:
        core = _COURT_HEAD_RE.sub("", core).strip(" ,;:") or core
    docket = core
    fed = _FEDERAL_RE.match(docket)
    if fed:
        year = int(fed.group(3))
        if year < FIRST_ONLINE_YEAR_BGER:
            return {
                "error_code": "never_published_online",
                "court": "bger",
                "docket": docket,
                "year": year,
                "reason": (
                    f"{docket} is a Federal Supreme Court judgment from {year}. The court "
                    f"put its ordinary judgments online only from {FIRST_ONLINE_YEAR_BGER}; "
                    "earlier ones exist solely on paper (or in BGE if they were published "
                    "there), so no open source — including this corpus — can serve the text."
                ),
                "coverage_note": BGER_COVERAGE_NOTE,
                "hint": (
                    "If the judgment was published in the official collection, search "
                    "BGE by subject with search_decisions (court='bge') and cite the BGE "
                    "reference instead. Do not cite the docket as if its text were verified."
                ),
            }
        return None
    evg = _EVG_RE.match(docket)
    if evg and evg.group(1) in EVG_CHAMBERS:
        year = int(_expand_year(evg.group(3)))
        if year < EVG_MERGER_YEAR:
            return {
                "error_code": "not_yet_ingested",
                "court": "bger",
                "docket": docket,
                "year": year,
                "reason": (
                    f"{docket} is an EVG (Eidgenössisches Versicherungsgericht) decision from "
                    f"{year}. The pre-{EVG_MERGER_YEAR} EVG backlog is only partly in the corpus; "
                    "this one is not indexed yet."
                ),
                "coverage_note": BGER_COVERAGE_NOTE,
                "hint": (
                    "The decision may exist on the court's site; do not treat this miss as "
                    "evidence that it does not exist, and do not quote it as verified."
                ),
            }
    return None
