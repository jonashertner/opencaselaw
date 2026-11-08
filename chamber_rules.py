"""The deciding chamber of a cantonal ruling, read from the court's own text.

entscheidsuche sets a chamber on every document through its court hierarchy;
we left it empty on ~110k rows of courts that have chambers (Fribourg, the
Bern Verwaltungsgericht, Basel-Landschaft, the frozen Vaud findinfo rows,
Nidwalden, Uri, Appenzell Ausserrhoden, the Zürich Baurekursgericht, the Bern
directorates). These helpers read it from what the court itself publishes, in
this order:

1. the ruling's own header: the line under "Arrêt du 9 juin 2026" (FR), the
   line under "Verwaltungsgericht des Kantons Bern" (BE), "Entscheid des
   Kantonsgerichts Basel-Landschaft, Abteilung Strafrecht" (BL), the block
   under "TRIBUNAL CANTONAL" (VD). Only the head of the decision text is read:
   past the Rubrum a chamber name is one the reasons mention (the decision
   under appeal, a related case), not the one that decided. Same principle as
   scrapers.bger.chamber_from_text (86ba0c80).
2. where the header says nothing (a metadata-only row, an extraction that lost
   the first page): the court's own docket or file-name scheme, but only where
   that scheme maps one-to-one onto a chamber AND that mapping was confirmed
   against the headers of the court's own rulings (FR "601 2025 200" -> Ie
   Cour administrative; BL "2013-05-23_sv_9" -> Abteilung
   Sozialversicherungsrecht; NW "ZA 19 13" -> Zivilabteilung).
3. otherwise None. A gap beats a guess.

Never from entscheidsuche: rows ingested from there carry a synthetic
Kopfzeile ("Vaud Tribunal cantonal Cour d'appel civile 10.05.2011 ...") and,
when they had no text, a Meta block ("Basel-Land Kantonsgericht / Abteilung
Strafrecht"). Both name entscheidsuche's own chamber guess, which is wrong
for whole series (vd_findinfo "Décision / 2011 / 200": Kopfzeile "Cour
d'appel civile", header "CHAMBRE DES RECOURS PENALE"). own_text() removes
them before anything is read.

Names are the court's own, in the language of the ruling where the court
uses two (FR "IIe Cour administrative" / "II. Verwaltungsgerichtshof", as the
BGer divisions are stored). For a court code that bundles several courts
(bl_gerichte, nw_gerichte, ur_gerichte, ar_gerichte) the value names the
court too ("Kantonsgericht, Abteilung Strafrecht"), the way bl_gerichte and
ar_gerichte already store "Kantonsgericht" / "Obergericht".

derive() is used by the scrapers for new rows and by
scripts/backfill_cantonal_chambers.py for the rows we hold, so both write the
same chamber for the same document.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

# entscheidsuche Kopfzeile: one line per language, each with the date
# ("Bern Verwaltungsgericht 29.07.2025 100 2025 8").
_DATE_DMY = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")


def own_text(text: str | None) -> str:
    """The text with entscheidsuche's Kopfzeile and Meta block removed.

    Kopfzeile: up to four leading lines, each carrying a dd.mm.yyyy date.
    Meta block (only on rows ingested without a text): a run of two or more
    lines that repeat the Kopfzeile's leading words without the date
    ("Basel-Land Kantonsgericht", "Bâle-Campagne Kantonsgericht", ...),
    possibly followed by entscheidsuche's chamber line. Everything from the
    Meta block on is entscheidsuche's, so it is cut.
    """
    if not text:
        return ""
    lines = text.split("\n")
    i = 0
    heads: list[str] = []
    while i < len(lines) and i < 4 and _DATE_DMY.search(lines[i]):
        first = lines[i].strip().split(" ")[0]
        if first:
            heads.append(first)
        i += 1
    if not heads:
        return text
    rest = lines[i:]
    heads_set = set(heads)
    run_start = None
    run = 0
    for j, ln in enumerate(rest):
        s = ln.strip()
        if s and s.split(" ")[0] in heads_set and not _DATE_DMY.search(s) and len(s) < 120:
            if run == 0:
                run_start = j
            run += 1
            if run >= 2:
                rest = rest[:run_start]
                break
        elif s:
            run = 0
    return "\n".join(rest)


def _fold(s: str) -> str:
    """Upper-case, accents and every non-letter/digit removed.

    "COUR DES ASSU RANCES SOCIALES", "Cour des assurances\nsociales" and
    "Cour d’appel" vs "Cour d'appel" compare equal; OCR splits words and
    headers wrap across lines.
    """
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s.upper() if ch.isalnum() and not unicodedata.combining(ch))


@dataclass(frozen=True)
class Name:
    """One chamber: the names to store (by ruling language) and the header
    spellings that identify it (folded with _fold before comparing)."""
    key: str
    names: dict[str, str]
    spellings: tuple[str, ...] = ()

    def folded(self) -> list[tuple[str, str]]:
        """(folded spelling, language) pairs: the stored names themselves plus
        the extra spellings (which carry their language as 'xx:' prefix)."""
        out = [(_fold(v), lang) for lang, v in self.names.items()]
        for sp in self.spellings:
            lang, _, txt = sp.partition(":")
            out.append((_fold(txt), lang))
        return out

    def name(self, lang: str | None) -> str:
        if lang and lang in self.names:
            return self.names[lang]
        return next(iter(self.names.values()))


def _earliest(window: str, table: list[Name]) -> tuple[Name, str] | None:
    """The chamber named earliest in the window (folded compare); on a tie the
    longer spelling wins ("CHAMBREDESRECOURSPENALE" over "CHAMBREDESRECOURS")."""
    fw = _fold(window)
    best: tuple[int, int, Name, str] | None = None
    for nm in table:
        for sp, lang in nm.folded():
            if not sp:
                continue
            pos = fw.find(sp)
            if pos < 0:
                continue
            cand = (pos, -len(sp), nm, lang)
            if best is None or cand[:2] < best[:2]:
                best = cand
    return (best[2], best[3]) if best else None


def _cut(text: str, stop: re.Pattern | None, limit: int) -> str:
    w = text[:limit]
    if stop is not None:
        m = stop.search(w)
        if m:
            w = w[: m.start()]
    return w


@dataclass
class Result:
    chamber: str | None
    how: str | None = None          # "header" | "docket" | "url" | None
    key: str | None = None          # the chamber's table key (for audits)
    header_key: str | None = None   # what the header alone said
    docket_key: str | None = None   # what the docket alone said
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fribourg — Tribunal cantonal (publicationtc.fr.ch, Tribuna)
# ---------------------------------------------------------------------------
# The line under "Arrêt du ..." / "Urteil vom ..." names the Cour. The docket
# prefix is the Cour's own number (measured on 14,858 served rows,
# 2026-11-08: every prefix's headers name one Cour, see the runbook).

FR_TABLE = [
    Name("101", {"fr": "Ie Cour d'appel civil", "de": "I. Zivilappellationshof"}),
    Name("102", {"fr": "IIe Cour d'appel civil", "de": "II. Zivilappellationshof"}),
    Name("104", {"fr": "Cour de modération", "de": "Moderationshof"}),
    Name("105", {"fr": "Chambre des poursuites et faillites",
                 "de": "Schuldbetreibungs- und Konkurskammer"}),
    Name("106", {"fr": "Cour de protection de l'enfant et de l'adulte",
                 "de": "Kindes- und Erwachsenenschutzhof"}),
    Name("106t", {"fr": "Chambre des tutelles"}),
    Name("501", {"fr": "Cour d'appel pénal", "de": "Strafappellationshof"}),
    Name("502", {"fr": "Chambre pénale", "de": "Strafkammer"}),
    Name("601", {"fr": "Ie Cour administrative", "de": "I. Verwaltungsgerichtshof"}),
    Name("602", {"fr": "IIe Cour administrative", "de": "II. Verwaltungsgerichtshof"}),
    Name("603", {"fr": "IIIe Cour administrative", "de": "III. Verwaltungsgerichtshof"}),
    Name("604", {"fr": "Cour fiscale", "de": "Steuergerichtshof"},
         ("de:Steuergerichtshofs",)),
    Name("605", {"fr": "Ie Cour des assurances sociales", "de": "I. Sozialversicherungsgerichtshof"}),
    Name("608", {"fr": "IIe Cour des assurances sociales", "de": "II. Sozialversicherungsgerichtshof"}),
    Name("605u", {"fr": "Cour des assurances sociales", "de": "Sozialversicherungsgerichtshof"}),
]
_FR_BY_KEY = {n.key: n for n in FR_TABLE}
# The Cour's number in the docket ("602 2025 13"); year bounds where the Cour
# changed: the Cour de protection replaced the Chambre des tutelles with the
# 2013 adult-protection law (first header 2014, last "Chambre des tutelles"
# 2012); the Cour des assurances sociales split in two in 2011 (last undivided
# header 2010; 608 opens in 2013). 607 = the President of the Cour fiscale.
FR_DOCKET = re.compile(r"^(\d{3}) (\d{4}) \d+")
FR_DOCKET_KEY = {
    "101": ("101", 0), "102": ("102", 0), "104": ("104", 0), "105": ("105", 0),
    "106": ("106", 2013), "501": ("501", 0), "502": ("502", 0), "601": ("601", 0),
    "602": ("602", 0), "603": ("603", 0), "604": ("604", 0), "605": ("605", 2011),
    "607": ("604", 0), "608": ("608", 2013),
}
_FR_STOP = re.compile(r"\n\s*(?:Composition|Besetzung|Parties|Parteien)\b")


def fr_gerichte(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    hit = _earliest(_cut(body, _FR_STOP, 1800), FR_TABLE)
    hkey = hit[0].key if hit else None
    dkey = None
    m = FR_DOCKET.match(docket or "")
    if m and m.group(1) in FR_DOCKET_KEY:
        key, since = FR_DOCKET_KEY[m.group(1)]
        if int(m.group(2)) >= since:
            dkey = key
    if hit:
        return Result(hit[0].name(hit[1]), "header", hkey, hkey, dkey)
    if dkey:
        lang = "de" if language == "de" else "fr"
        return Result(_FR_BY_KEY[dkey].name(lang), "docket", dkey, None, dkey)
    return Result(None, None, None, None, None)


# ---------------------------------------------------------------------------
# Bern — Verwaltungsgericht (vg-urteile.apps.be.ch, Tribuna)
# ---------------------------------------------------------------------------
# "Verwaltungsgericht des Kantons Bern / Sozialversicherungsrechtliche
# Abteilung / Urteil vom ...". Three Abteilungen: the verwaltungsrechtliche
# (dockets 100 ...), the sozialversicherungsrechtliche (200 ...) and the
# Abteilung für französischsprachige Geschäfte, which decides every French
# case under either docket series (Cour des affaires de langue française).

BE_VG_TABLE = [
    Name("vra", {"de": "Verwaltungsrechtliche Abteilung"}),
    Name("sva", {"de": "Sozialversicherungsrechtliche Abteilung"}),
    Name("fr", {"fr": "Cour des affaires de langue française",
                "de": "Abteilung für französischsprachige Geschäfte"},
         ("fr:Section des affaires de langue française",
          "de:Abteilung für französischsprachige Geschäfte")),
]
_BE_VG_BY_KEY = {n.key: n for n in BE_VG_TABLE}
_BE_VG_STOP = re.compile(r"\n\s*(?:Urteil|Entscheid|Verfügung|Beschluss|Jugement|Décision|Arrêt)\s+(?:vom|du)\b")
BE_VG_DOCKET = re.compile(r"^(100|200) \d{4} \d+")


def be_verwaltungsgericht(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    hit = _earliest(_cut(body, _BE_VG_STOP, 900), BE_VG_TABLE)
    hkey = hit[0].key if hit else None
    dkey = None
    m = BE_VG_DOCKET.match(docket or "")
    if m:
        # The stored language tag is wrong on ~9 % of the French rulings
        # (107 of ~1,200 tagged "de", 2026-11-08); the text's own words decide.
        if len(body.strip()) >= 80:
            from models import detect_language
            language = detect_language(body)
        if language == "fr":
            dkey = "fr"
        elif language == "de":
            dkey = "vra" if m.group(1) == "100" else "sva"
    if hit:
        return Result(hit[0].name(hit[1] if hkey == "fr" else None), "header", hkey, hkey, dkey)
    if dkey:
        return Result(_BE_VG_BY_KEY[dkey].name(language), "docket", dkey, None, dkey)
    return Result(None, None, None, None, None)


# ---------------------------------------------------------------------------
# Basel-Landschaft — Kantonsgericht, Steuer- und Enteignungsgericht,
# Zwangsmassnahmengericht (bl.swisslex.ch and baselland.ch)
# ---------------------------------------------------------------------------
# Header: "Entscheid des Kantonsgerichts Basel-Landschaft, Abteilung
# Sozialversicherungsrecht vom 12. Februar 2015 (720 14 312)". The court's
# own PDF file names carry the Abteilung ("2013-05-23_sv_9.pdf": sv, vv, zr,
# sr; "stger" the Steuergericht, "entger" the Enteignungsgericht), and the
# baselland.ch path names the court (/steuergericht/, /enteignungsgericht/).

BL_KG = "Kantonsgericht"
BL_TABLE = [
    Name("zr", {"de": f"{BL_KG}, Abteilung Zivilrecht"}, ("de:Abteilung Zivilrecht",)),
    Name("sr", {"de": f"{BL_KG}, Abteilung Strafrecht"}, ("de:Abteilung Strafrecht",)),
    Name("vv", {"de": f"{BL_KG}, Abteilung Verfassungs- und Verwaltungsrecht"},
         ("de:Abteilung Verfassungs- und Verwaltungsrecht",)),
    Name("sv", {"de": f"{BL_KG}, Abteilung Sozialversicherungsrecht"},
         ("de:Abteilung Sozialversicherungsrecht",)),
]
_BL_BY_KEY = {n.key: n for n in BL_TABLE}
_BL_SLUG = re.compile(r"\d{4}[-_]\d{2}[-_]\d{2}[-_](zr|sr|vv|sv|stger|entger|entg)(?:[-_.]|$)", re.I)
_BL_PATH = re.compile(r"/rechtsprechung/(steuergericht|enteignungsgericht|zwangsmassnahmengericht)/")
_BL_COURTS = {"stger": "Steuergericht", "steuergericht": "Steuergericht",
              "entger": "Enteignungsgericht", "entg": "Enteignungsgericht",
              "enteignungsgericht": "Enteignungsgericht",
              "zwangsmassnahmengericht": "Zwangsmassnahmengericht"}
# Header must say whose Abteilung it is: "Kantonsgericht(s) Basel-Landschaft,
# Abteilung X" (swisslex) or a first line "Abteilung X" under the court.
_BL_HEADER = re.compile(
    r"Kantonsgerichts?\s+(?:Basel-Landschaft|Basel-Land|BL)\s*,?\s*(Abteilung\s+[^\n]{5,60}?)\s+(?:vom|\(|\n)",
    re.I)
# Kantonsgericht docket series (swisslex "720 14 312"): measured against the
# swisslex headers on 2026-11-08 (runbook). Only series whose headers name a
# single Abteilung are listed.
# Threshold: at least 20 header-labelled rows and 98 % naming one Abteilung
# (731: 58 sv / 3 zr, left out).
BL_SERIES: dict[str, str] = {
    "400": "zr", "410": "zr", "420": "zr", "430": "zr",
    "460": "sr", "470": "sr",
    "710": "sv", "715": "sv", "720": "sv", "725": "sv", "730": "sv", "735": "sv",
    "740": "sv", "745": "sv", "750": "sv", "760": "sv",
    "810": "vv",
    "650": "entger",  # 148 of 152 headers: Abteilung Enteignungsgericht, 4 none
}
# "Entscheid des Steuer- und Enteignungsgerichts Basel-Landschaft, Abteilung
# Enteignungsgericht vom ...": stored as the Abteilung's court name, the way
# the scraper's court_description already stores it.
_BL_SE_HEADER = re.compile(
    r"(?:Steuer-\s*und\s+Enteignungsgerichts?\s+(?:Basel-Landschaft|Basel-Land|BL)\s*,?\s*Abteilung\s+"
    r"(Steuergericht|Enteignungsgericht)"
    r"|Entscheid\s+des\s+(Steuergericht|Enteignungsgericht)s\s+(?:Basel-Landschaft|Basel-Land|BL)\b)",
    re.I)


def bl_gerichte(docket: str, text: str, language: str | None, source_url: str = "", **_) -> Result:
    body = own_text(text)
    hkey = None
    m = _BL_HEADER.search(body[:700])
    if m:
        hit = _earliest(m.group(1), BL_TABLE)
        if hit:
            hkey = hit[0].key
    dkey = None
    dhow = "docket"
    s = _BL_SLUG.search(docket or "") or _BL_SLUG.search(unquote(source_url or ""))
    if s:
        dkey = s.group(1).lower()
    else:
        p = _BL_PATH.search(source_url or "")
        if p:
            dkey, dhow = p.group(1), "url"
        else:
            ser = re.match(r"^(\d{3}) (?:\d{2}|\d{4}) \d+", docket or "")
            if ser and ser.group(1) in BL_SERIES:
                dkey = BL_SERIES[ser.group(1)]
    if not hkey:
        se = _BL_SE_HEADER.search(body[:700])
        if se:
            hkey = (se.group(1) or se.group(2)).lower()
            hkey = "stger" if hkey == "steuergericht" else "entger"
    if hkey in _BL_BY_KEY:
        return Result(_BL_BY_KEY[hkey].name("de"), "header", hkey, hkey, dkey)
    if hkey:
        return Result(_BL_COURTS[hkey], "header", hkey, hkey, dkey)
    if dkey in _BL_BY_KEY:
        return Result(_BL_BY_KEY[dkey].name("de"), "docket", dkey, None, dkey)
    if dkey in _BL_COURTS:
        return Result(_BL_COURTS[dkey], dhow, dkey, None, dkey)
    return Result(None, None, None, None, dkey)


# ---------------------------------------------------------------------------
# Vaud — Tribunal cantonal, findinfo-tc.vd.ch (frozen es_vd_findinfo rows)
# ---------------------------------------------------------------------------
# Header block: "TRIBUNAL / CANTONAL / 160 / PE08.012590-LML / CHAMBRE / DES
# RECOURS PENALE / ____ / Séance du 10 mai 2011". The findinfo docket
# ("Arrêt / 2011 / 200") carries no chamber, so there is no fallback.

VD_TABLE = [Name(k, {"fr": k}) for k in (
    "Cour des assurances sociales",
    "Chambre des recours pénale",
    "Chambre des recours civile",
    "Chambre des recours",
    "Cour des poursuites et faillites",
    "Chambre des poursuites et faillites",
    "Cour d'appel pénale",
    "Cour d'appel civile",
    "Chambre des curatelles",
    "Chambre des tutelles",
    "Cour civile",
    "Cour de cassation pénale",
    "Cour administrative",
    "Chambre des avocats",
    "Tribunal d'accusation",
    "Cour de droit administratif et public",
    "Chambre des révisions civiles et pénales",
    "Cour plénière",
    "Chambre patrimoniale cantonale",
    "Chambre des notaires",
)]
_VD_ANCHOR = re.compile(r"TRIBUNAL\s+C\s*ANTONA|Tribunal\s+cantonal")
_VD_STOP = re.compile(r"_{12,}|\n\s*(?:Présidence|Composition|Séance|Présidente?\s*:)")


def vd_findinfo(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    a = _VD_ANCHOR.search(body[:1500])
    if not a:
        return Result(None)
    window = _cut(body[a.end():], _VD_STOP, 400)
    hit = _earliest(window, VD_TABLE)
    if hit:
        return Result(hit[0].name("fr"), "header", hit[0].key, hit[0].key, None)
    return Result(None)


# ---------------------------------------------------------------------------
# Nidwalden — Obergericht and Verwaltungsgericht (www.nw.ch)
# ---------------------------------------------------------------------------
# Header: "GERICHTE / OBERGERICHT / ... / ZA 24 19 / Urteil vom 22. Mai 2025 /
# Zivilabteilung / Besetzung". The docket's letters are the Abteilung's.

NW_TABLE = [
    Name("ZA", {"de": "Obergericht, Zivilabteilung"}, ("de:Zivilabteilung",)),
    Name("SA", {"de": "Obergericht, Strafabteilung"}, ("de:Strafabteilung",)),
    Name("BAZ", {"de": "Obergericht, Beschwerdeabteilung in Zivilsachen"},
         ("de:Beschwerdeabteilung in Zivilsachen",)),
    Name("BAS", {"de": "Obergericht, Beschwerdeabteilung in Strafsachen"},
         ("de:Beschwerdeabteilung in Strafsachen",)),
    Name("SV", {"de": "Verwaltungsgericht, Sozialversicherungsabteilung"},
         ("de:Sozialversicherungsabteilung",)),
    Name("VA", {"de": "Verwaltungsgericht, Verwaltungsabteilung"}, ("de:Verwaltungsabteilung",)),
    Name("ST", {"de": "Verwaltungsgericht, Steuerabteilung"}, ("de:Steuerabteilung",)),
]
_NW_BY_KEY = {n.key: n for n in NW_TABLE}
_NW_STOP = re.compile(r"\n\s*(?:Besetzung|Verfahrensbeteiligte)\b")
_NW_DOCKET = re.compile(r"^(ZA|SA|BAZ|BAS|SV|VA|ST) \d{2} \d+")


# The Obergericht sitting as Verfassungsgericht ("VG 17 1 / Entscheid vom ...
# / Verfassungsgericht"): named only when the header says so.
_NW_VERF = re.compile(r"\n\s*Verfassungsgericht\s*\n")


def nw_gerichte(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    window = _cut(body, _NW_STOP, 700)
    hit = _earliest(window, NW_TABLE)
    hkey = hit[0].key if hit else None
    m = _NW_DOCKET.match(docket or "")
    dkey = m.group(1) if m else None
    if not dkey:  # the publication id stands in for the docket on some rows
        m2 = re.search(r"\n\s*(ZA|SA|BAZ|BAS|SV|VA|ST) \d{2} \d+\b", body[:500])
        dkey = m2.group(1) if m2 else None
    if not hkey and _NW_VERF.search(window) and re.search(r"OBERGERICHT", window[:200]):
        return Result("Obergericht, Verfassungsgericht", "header", "VG", "VG", dkey)
    if hkey:
        return Result(_NW_BY_KEY[hkey].name("de"), "header", hkey, hkey, dkey)
    if dkey:
        return Result(_NW_BY_KEY[dkey].name("de"), "docket", dkey, None, dkey)
    return Result(None)


# ---------------------------------------------------------------------------
# Uri — Obergericht (www.ur.ch)
# ---------------------------------------------------------------------------
# Header: "OBERGERICHT / Zivilrechtliche Abteilung / ____ / OG Z 25 19". The
# docket letter is the Abteilung's: OG V (Verwaltungsrechtliche), OG Z
# (Zivilrechtliche), OG S (Strafrechtliche). Other series (BI, SK, AK, the
# Rechenschaftsbericht extracts "94/95 13") get a chamber only from a header.

UR_TABLE = [
    Name("V", {"de": "Obergericht, Verwaltungsrechtliche Abteilung"},
         ("de:Verwaltungsrechtliche Abteilung", "de:Verwaltungsrechtlichen Abteilung")),
    Name("Z", {"de": "Obergericht, Zivilrechtliche Abteilung"},
         ("de:Zivilrechtliche Abteilung", "de:Zivilrechtlichen Abteilung")),
    Name("S", {"de": "Obergericht, Strafrechtliche Abteilung"},
         ("de:Strafrechtliche Abteilung", "de:Strafrechtlichen Abteilung")),
    Name("BI", {"de": "Obergericht, Strafprozessuale Beschwerdeinstanz"},
         ("de:Strafprozessuale Beschwerdeinstanz",)),
]
_UR_BY_KEY = {n.key: n for n in UR_TABLE}
_UR_STOP = re.compile(r"\n\s*(?:Besetzung|Verfahrensbeteiligte)\b")
# OG BI is filled from a header only: its headers were not checked against
# enough rulings to vouch for the series.
_UR_DOCKET = re.compile(r"(?:^|[_ ])OG ([VZS]) \d{2} \d+")


def ur_gerichte(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    window = _cut(body, _UR_STOP, 500)
    # The header is the court's: "OBERGERICHT" must open it.
    hit = _earliest(window, UR_TABLE) if re.search(r"OBERGERICHT|Obergericht", window[:200]) else None
    hkey = hit[0].key if hit else None
    m = _UR_DOCKET.search(docket or "")
    dkey = m.group(1) if m else None
    if hkey:
        return Result(_UR_BY_KEY[hkey].name("de"), "header", hkey, hkey, dkey)
    if dkey:
        return Result(_UR_BY_KEY[dkey].name("de"), "docket", dkey, None, dkey)
    return Result(None)


# ---------------------------------------------------------------------------
# Appenzell Ausserrhoden — Obergericht (rechtsprechung.ar.ch)
# ---------------------------------------------------------------------------
# Header: "Obergericht Appenzell Ausserrhoden / 3. Abteilung / Zirkular-Urteil
# vom ..." (the parties come first in the portal's HTML). Docket "O3V-19-40":
# the digit after O is the Abteilung. The GVP yearbook entries ("OG ARGVP
# 2011 3563") name only the authority, which the portal's argvpBehoerde field
# states; the scraper already stores it as "Obergericht" / "Kantonsgericht".

# Folded (see _fold): "Kantonsge richt App enzell Ausserrhoden 1. Abteilung"
# is how the PDF text of some Kantonsgericht rulings reads.
_AR_HEADER = re.compile(r"(OBERGERICHT|KANTONSGERICHT)(?:VON)?APPENZELLAUSSERRHODEN([1-5])ABTEILUNG")
_AR_DOCKET = re.compile(r"(?:^|\s)([OK])([1-5])[ZVSE]?-\d{2}-\d+")
_AR_COURT = {"O": "Obergericht", "K": "Kantonsgericht",
             "OBERGERICHT": "Obergericht", "KANTONSGERICHT": "Kantonsgericht"}
_AR_AUTH = re.compile(r"^(OG|KG) ")
_AR_AUTH_NAME = {"OG": "Obergericht", "KG": "Kantonsgericht"}


def ar_gerichte(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    m = _AR_HEADER.search(_fold(body[:2500]))
    hkey = f"{_AR_COURT[m.group(1)]}, {m.group(2)}. Abteilung" if m else None
    d = _AR_DOCKET.search(docket or "")
    dkey = f"{_AR_COURT[d.group(1)]}, {d.group(2)}. Abteilung" if d else None
    # A GVP entry on an Obergericht ruling quotes the Kantonsgericht
    # Abteilung it reviewed ("OG O1Z-18-5 ARGVP 2018 3728"): a header naming
    # another court than the one the docket states is not this ruling's.
    a = _AR_AUTH.match(docket or "")
    auth = _AR_AUTH_NAME[a.group(1)] if a else (_AR_COURT[d.group(1)] if d else None)
    if hkey and auth and not hkey.startswith(auth + ","):
        hkey = None
    if hkey:
        return Result(hkey, "header", hkey, hkey, dkey)
    if dkey:
        return Result(dkey, "docket", dkey, None, dkey)
    a = _AR_AUTH.match(docket or "")
    if a:
        return Result(_AR_AUTH_NAME[a.group(1)], "docket", a.group(1), None, a.group(1))
    return Result(None)


# ---------------------------------------------------------------------------
# Zürich — Baurekursgericht (baurekursgericht-zh.ch)
# ---------------------------------------------------------------------------
# Header: "Baurekursgericht / des Kantons Zürich / 1. Abteilung / G.-Nr.
# R1S.2017.05031 / BRGE I Nr. 0095/2017". The roman numeral of the BRGE/BRKE
# number is the Abteilung's.

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}
_ZH_BRG_HEADER = re.compile(r"Baurekurs(?:gericht|kommission)\s+des\s+Kantons\s+Zürich\s*\n\s*([1-4])\.\s*Abteilung", re.I)
_ZH_BRG_DOCKET = re.compile(r"^BR[GK]E (IV|III|II|I) Nrn?\.")


# The Geschäftsnummer carries the Abteilung too ("G.-Nr. R1S.2017.05031",
# "im Verfahren G.-Nr. R4.2010.00132" in the BEZ extracts of
# Präsidialverfügungen).
_ZH_BRG_GNR = re.compile(r"G\.-Nr\.\s*R([1-4])[A-Z]?\.\d{4}\.")


def zh_baurekursgericht(docket: str, text: str, language: str | None, **_) -> Result:
    body = own_text(text)
    m = _ZH_BRG_HEADER.search(body[:600])
    hkey = m.group(1) if m else None
    d = _ZH_BRG_DOCKET.match(docket or "")
    dkey = str(_ROMAN[d.group(1)]) if d else None
    if not dkey:
        g = _ZH_BRG_GNR.search(body[:400])
        dkey = g.group(1) if g else None
    if hkey:
        return Result(f"{hkey}. Abteilung", "header", hkey, hkey, dkey)
    if dkey:
        return Result(f"{dkey}. Abteilung", "docket", dkey, None, dkey)
    return Result(None)


# ---------------------------------------------------------------------------
# Bern — directorates (frozen es_be_weitere rows; be_direktionen live)
# ---------------------------------------------------------------------------
# be_weitere bundles the rulings of several directorates; the chamber is the
# directorate, by its official abbreviation, as be_direktionen._chamber
# stores it (and as 45 held rows already carry "GEF"). Read from the docket
# ("2023.GSI.846", "JGK 32.13-15.36") or the directorate's own host
# (www.gsi.be.ch, www.gef.be.ch).

_BE_DIR = ("GSI", "GEF", "JGK", "DIJ", "BKD", "ERZ", "POM", "SID", "FIN", "WEU", "VOL", "BVE", "BVD", "STA")
_BE_DIR_DOCKET = re.compile(r"(?:^|[.\s])(" + "|".join(_BE_DIR) + r")(?:[.\s\xa0]|$)")
_BE_DIR_HOST = re.compile(r"https?://www\.(?:[a-z_]+\.)?(" + "|".join(d.lower() for d in _BE_DIR) + r")\.be\.ch/")


def be_weitere(docket: str, text: str, language: str | None, source_url: str = "", **_) -> Result:
    d = _BE_DIR_DOCKET.search(docket or "")
    if d:
        return Result(d.group(1), "docket", d.group(1), None, d.group(1))
    h = _BE_DIR_HOST.search(source_url or "")
    if h:
        return Result(h.group(1).upper(), "url", h.group(1).upper(), None, h.group(1).upper())
    return Result(None)


# ---------------------------------------------------------------------------

RULES = {
    "fr_gerichte": fr_gerichte,
    "be_verwaltungsgericht": be_verwaltungsgericht,
    "bl_gerichte": bl_gerichte,
    "vd_findinfo": vd_findinfo,
    "nw_gerichte": nw_gerichte,
    "ur_gerichte": ur_gerichte,
    "ar_gerichte": ar_gerichte,
    "zh_baurekursgericht": zh_baurekursgericht,
    "be_weitere": be_weitere,
}

# Courts checked and left out: a single deciding body, whose rulings and
# portal name no chamber (runbooks/cantonal_chambers_2026-11.md). The
# Kassationsgericht ZH ("Kassationsgericht des Kantons Zürich / Kass.-Nr."),
# the BVD's Rechtsamt, the Steuerrekurskommission and the Obergericht SH
# (its rulings name only the composition: Einzelrichter, Dreierkammer).
SINGLE_BODY = ("zh_kassationsgericht", "be_bvd", "be_steuerrekurs", "sh_obergericht", "sh_gerichte")


# Header and docket keys that name the same chamber under two names (the
# Cour des assurances sociales before its 2011 split; the Chambre des
# tutelles the Cour de protection replaced).
_EQUIVALENT = {
    "fr_gerichte": ({"605", "605u"}, {"608", "605u"}, {"106", "106t"}),
    "bl_gerichte": ({"entger", "entg", "enteignungsgericht"}, {"stger", "steuergericht"}),
}


def _compact(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKD", s).upper())


def docket_in_head(docket: str, text: str, window: int = 1500) -> bool:
    """Whether the ruling's head prints the row's own docket ("605 2012 204";
    BE VG prints "200 23 618 IV" for 200 2023 618). Punctuation and spacing
    are ignored."""
    d = _compact(docket or "")
    if len(d) < 4:
        return False
    head = _compact(own_text(text)[:window])
    if d in head:
        return True
    short = _compact(re.sub(r"\b(?:19|20)(\d{2})\b", r"\1", docket or "", count=1))
    return short != d and len(short) >= 4 and short in head


def _agree(court: str, a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in s and b in s for s in _EQUIVALENT.get(court, ()))


def derive(court: str, *, docket: str | None, text: str | None,
           language: str | None = None, source_url: str | None = None) -> Result:
    """The chamber for one ruling of `court`, or Result(None) (see module doc).

    When the header and the docket both name a chamber and they differ, the
    header wins if the head prints the row's own docket; otherwise the
    result is Result(None, how="conflict"): on every such row checked
    (2026-11-08) the stored text was another ruling's (fr_gerichte "106 2018
    45" holds the text of "608 2018 5"; be_verwaltungsgericht "100 2023 306"
    the text of "200 23 618"), so the header describes the wrong document and
    the docket alone is not enough to vouch for it. Those rows are reported,
    not filled.
    """
    fn = RULES.get(court)
    if fn is None:
        return Result(None)
    r = fn(docket=docket or "", text=text or "", language=language,
           source_url=source_url or "")
    if r.header_key and r.docket_key and not _agree(court, r.header_key, r.docket_key):
        if docket_in_head(docket or "", text or ""):
            # The text is the row's own: the header is the authority, and the
            # docket series was not one-to-one for this ruling (FR 605 2012
            # 204: the IIe Cour des assurances sociales sat under 605 dockets
            # before the 608 series opened in 2013).
            r.detail["docket_overruled"] = r.docket_key
            return r
        return Result(None, "conflict", None, r.header_key, r.docket_key)
    return r


def chamber_for(court: str, *, docket: str | None, text: str | None,
                language: str | None = None, source_url: str | None = None) -> str | None:
    """derive(...).chamber."""
    return derive(court, docket=docket, text=text, language=language,
                  source_url=source_url).chamber


def refine(existing: str | None, derived: str | None) -> str | None:
    """The chamber to store, given what the row (or the listing) already says.

    An existing value is kept, with one exception: a value that names only the
    court the derived chamber belongs to ("Kantonsgericht" -> "Kantonsgericht,
    Abteilung Strafrecht"; ar_gerichte "Obergericht" -> "Obergericht, 3.
    Abteilung") is completed, never contradicted."""
    e = (existing or "").strip()
    if not e:
        return derived or None
    if derived and derived.startswith(e + ", "):
        return derived
    return existing


def chamber_or(existing: str | None, court: str, *, docket: str | None, text: str | None,
               language: str | None = None, source_url: str | None = None) -> str | None:
    """The scrapers' entry point: refine(existing, chamber_for(...))."""
    return refine(existing, chamber_for(court, docket=docket, text=text, language=language,
                                        source_url=source_url))
