"""
Legal reference extraction for Swiss case law text.

Extracts:
- Statute references (e.g., "Art. 8 EMRK", "Art. 34 Abs. 2 BV")
- Case citations (BGE and docket-like references)
- Prior instance references (appeal chain tracking)
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_ARTICLE_MARKER = r"(?:Art\.?|Artikel)"
_PARAGRAPH_MARKER = r"(?:Abs\.?|Absatz|al\.?|alin(?:ea)?\.?|cpv\.?|co\.?|para\.?)"
_ORDINAL_SUFFIX = r"(?:bis|ter|quater|quinquies|sexies)"
_ARTICLE_TOKEN = rf"\d+(?:\s*{_ORDINAL_SUFFIX}|[a-z](?![a-z]))?"
_PARAGRAPH_TOKEN = rf"\d+(?:\s*{_ORDINAL_SUFFIX}|[a-z](?![a-z]))?"
# Qualifiers that can appear between paragraph and law code
_FOLLOWING_MARKER = r"(?:ff|ss|segg)\.?"  # "and following" markers
_SUB_MARKER = r"(?:Ziff(?:er)?|lit|Bst|Buchst|S|Satz|ch|let|n)"
_SUB_TOKEN = r"(?:\d+|[a-z])"
_INVALID_LAW_CODES = {
    # ── Statute structural markers ──
    "AL", "ABS", "ABSATZ", "ALIN", "ALINEA", "CPV", "PARA",
    "BIS", "TER", "QUATER", "QUINQUIES", "SEXIES",
    "FF", "SS", "SEGG", "ZIFF", "ZIFFER", "LIT", "BST", "BUCHST", "SATZ",
    # ── German articles, prepositions, conjunctions ──
    "AB", "AM", "AN", "AUS", "BEI", "BZW", "DA", "DAS", "DEM", "DEN",
    "DER", "DES", "DIE", "DIES", "DURCH", "EIN", "EINE", "EINEM",
    "EINEN", "EINER", "EINES", "ER", "ES", "GEGEN", "HA", "IM", "IN",
    "IST", "JE", "MIT", "NACH", "NEBEN", "NICHT", "NOCH", "NUR",
    "ODER", "OHNE", "SICH", "SIE", "SIND", "SOWIE", "UM", "UND",
    "UNTER", "VOM", "VON", "VOR", "WAR", "WIE", "WIRD", "ZU",
    "ZUM", "ZUR", "ZWISCHEN",
    # ── French articles, prepositions, conjunctions ──
    "AU", "AUX", "AVEC", "CE", "CES", "CETTE", "COMME", "DANS",
    "DE", "DU", "EN", "EST", "ET", "IL", "LA", "LE", "LES",
    "MAIS", "OU", "PAR", "PEUT", "POUR", "QUE", "QUI", "SE",
    "SONT", "SUR", "UN", "UNE",
    # ── Italian articles, prepositions ──
    "CHE", "CON", "CUI", "DAL", "DEI", "DEL", "DELL", "DELLA",
    "DELLE", "DELLO", "DI", "FRA", "GLI", "NEL", "NELL", "NELLA",
    "NON", "PER", "SUL", "TRA", "UNA", "UNO",
    # ── Ordinal / structural words ──
    "ART", "CUM", "DRITTER", "ERSTER", "LETT", "LET", "LETTRE",
    "LITT", "NAPR", "PHR", "PRIMA", "RZ", "SECONDA", "ZWEITER",
    # ── Common abbreviations that are not law codes ──
    "AD", "AGB", "BI", "CH", "NE", "NI", "NO", "OF", "QU", "RE", "SI",
}

STATUTE_PATTERN = re.compile(
    rf"""
    \b{_ARTICLE_MARKER}\s*
    (?P<article>{_ARTICLE_TOKEN})\s*
    (?:{_PARAGRAPH_MARKER}\s*(?P<paragraph>{_PARAGRAPH_TOKEN}))?\s*
    (?:{_FOLLOWING_MARKER}\s+)?
    (?:{_SUB_MARKER}\.?\s*{_SUB_TOKEN}\s+)?
    (?P<law>[A-Z][A-Z0-9]{{1,11}}(?:/[A-Z0-9]{{2,6}})?)
    \b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)

# BGE = German, ATF = French, DTF = Italian — the same series, one prefix
# per language. Until 2026-08 only the literal "BGE" was recognised, so
# "ATF 143 III 666" survived solely via the bare-form docket pattern below
# (target_type='docket', prefix discarded): decisions since 2024 yielded
# 345,146 bge-typed tokens from DE sources but only 283 from FR. The
# normalised form stays "BGE vol DIV page" — one canonical key per target,
# whichever language cited it.
BGE_PATTERN = re.compile(
    r"\b(?:BGE|ATF|DTF)\s+(?P<vol>\d{1,3})\s+(?P<div>[IVX]{1,4})\s+(?P<page>\d{1,4})\b",
    flags=re.IGNORECASE,
)

DOCKET_PATTERNS = [
    # 1A.122/2005, 2C_37/2016, D-7414/2015
    re.compile(r"\b[A-Z0-9]{1,4}[._-]\d{1,6}[/_]\d{4}\b"),
    # VB.2018.00411, RR.2012.25
    re.compile(r"\b[A-Z]{1,6}\.\d{4}\.\d{1,6}\b"),
    # 151 I 62 (internal BGE style references without explicit "BGE")
    re.compile(r"\b\d{1,3}\s+[IVX]{1,4}\s+\d{1,4}\b"),
]


@dataclass(frozen=True)
class StatuteReference:
    raw: str
    law_code: str
    article: str
    paragraph: str | None
    normalized: str


@dataclass(frozen=True)
class CaseCitation:
    raw: str
    citation_type: str  # bge | docket
    normalized: str


def extract_references(text: str) -> dict[str, list[dict]]:
    statutes = [asdict(s) for s in extract_statute_references(text)]
    citations = [asdict(c) for c in extract_case_citations(text)]
    return {
        "statutes": statutes,
        "citations": citations,
    }


def extract_statute_references(text: str) -> list[StatuteReference]:
    if not text:
        return []

    refs: list[StatuteReference] = []
    seen: set[str] = set()

    for match in STATUTE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        article = re.sub(r"\s+", "", (match.group("article") or "").lower())
        paragraph_raw = match.group("paragraph")
        paragraph = re.sub(r"\s+", "", paragraph_raw.lower()) if paragraph_raw else None
        law_raw = match.group("law")
        # Require the matched text to look like a legal abbreviation, not a
        # regular word.  Lowercase words (der, des, in, ihrer) and long
        # title-case words (Oder, Della, Ihrer) are filtered out.
        # Short title-case (Cst, Abs) are allowed — the blocklist catches
        # the false positives among those.
        n_upper = sum(1 for c in law_raw if c.isupper())
        if n_upper == 0:
            continue
        if n_upper == 1 and len(law_raw) > 3:
            continue
        law_code = law_raw.upper()
        if law_code in _INVALID_LAW_CODES:
            continue
        normalized = _normalize_statute(article=article, paragraph=paragraph, law_code=law_code)

        if normalized in seen:
            continue
        seen.add(normalized)
        refs.append(
            StatuteReference(
                raw=raw,
                law_code=law_code,
                article=article,
                paragraph=paragraph,
                normalized=normalized,
            )
        )
    return refs


def extract_case_citations(text: str) -> list[CaseCitation]:
    if not text:
        return []

    refs: list[CaseCitation] = []
    seen: set[str] = set()

    # BGE citations, e.g., "BGE 147 I 268"
    for match in BGE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        normalized = f"BGE {match.group('vol')} {match.group('div').upper()} {match.group('page')}"
        if normalized in seen:
            continue
        seen.add(normalized)
        refs.append(CaseCitation(raw=raw, citation_type="bge", normalized=normalized))

    for pattern in DOCKET_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0).strip()
            if pattern is DOCKET_PATTERNS[-1]:
                # Avoid double-counting BGE/ATF/DTF refs as docket-style refs.
                prefix = text[max(0, match.start() - 8):match.start()]
                if re.search(r"\b(?:BGE|ATF|DTF)\s*$", prefix, flags=re.IGNORECASE):
                    continue
            normalized = _normalize_docket(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            refs.append(CaseCitation(raw=raw, citation_type="docket", normalized=normalized))

    return refs


def _normalize_statute(*, article: str, paragraph: str | None, law_code: str) -> str:
    if paragraph:
        return f"ART.{article}.ABS.{paragraph.lower()}.{law_code.upper()}"
    return f"ART.{article}.{law_code.upper()}"


def _normalize_docket(text: str) -> str:
    # Preserve BGE-like spacing while normalizing punctuation/case.
    compact = re.sub(r"\s+", " ", text.strip().upper())
    if re.match(r"^\d{1,3}\s+[IVX]{1,4}\s+\d{1,4}$", compact):
        return compact

    normalized = text.strip().upper()
    normalized = normalized.replace("-", "_").replace(".", "_").replace("/", "_")
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


# ---------------------------------------------------------------------------
# Prior instance extraction (appeal chain tracking)
# ---------------------------------------------------------------------------

# Structural markers to locate the header section
_GEGENSTAND_RE = re.compile(
    r"\b(?:Gegenstand|Objet|Oggetto)\b", re.IGNORECASE
)
# The heading form: the word alone on its line, optionally with a colon.
_GEGENSTAND_LINE_RE = re.compile(
    r"^[ \t]*(?:Gegenstand|Objet|Oggetto)[ \t]*:?[ \t]*$", re.IGNORECASE | re.MULTILINE
)
# How far into the text a bare (non-heading) "Gegenstand" may start and still
# be taken as the heading. BGer headers put it within ~1,500 chars.
_GEGENSTAND_MAX_START = 6000
_BODY_START_RE = re.compile(
    r"\b(?:Erwägung(?:en)?|Sachverhalt|Considérant|Faits|Considerando|Fatti"
    r"|Visto|In\s+Erwägung)\s*:",
    re.IGNORECASE,
)

# Appeal keyword + preposition → text → (docket) in parentheses
# Matches across all three languages:
#   DE: Beschwerde gegen den Entscheid des ... vom 13. Nov 2025 (SBK.2025.285).
#   FR: recours contre l'arrêt de la ... du 6 août 2024 (A/1168/2024).
#   IT: ricorso contro la sentenza del ... del 31 marzo 2025 (35.2024.77).
_PRIOR_INSTANCE_RE = re.compile(
    r"\b(?:Beschwerde|Berufung|Rekurs|Einsprache|recours|appel|ricorso)"
    r"\s+(?:gegen|contre|contro)\b"
    r"[^(]{10,500}?"  # court name, decision type, date (non-greedy, no backtrack through parens)
    r"\(([^)]{3,100})\)",  # docket in parentheses
    re.IGNORECASE | re.DOTALL,
)

# Broader docket pattern for parenthetical content — accepts formats
# not covered by DOCKET_PATTERNS (e.g. "35.2024.77", "A/1168/2024")
_PAREN_DOCKET_RE = re.compile(
    r"[A-Z0-9]{1,6}[./_-]\d{2,6}[./_-]\d{2,6}"
    r"(?:\s*[-–]\s*[A-Z0-9]{1,6}[./_-]\d{2,6}[./_-]\d{2,6})?",
    re.IGNORECASE,
)


_SACHVERHALT_RE = re.compile(
    r"\b(?:Sachverhalt|Faits|Fatti)\s*:?",
    re.IGNORECASE,
)
_ERWAEGUNGEN_RE = re.compile(
    r"\b(?:Erw[aä]gung(?:en)?|Consid[eé]rant|Considerando|In\s+Erw[aä]gung)"
    r"\s*:?",
    re.IGNORECASE,
)

# Narrative prior-instance pattern in Sachverhalt body:
# "mit Urteil/Entscheid vom ... wies das Obergericht/Kantonsgericht ..."
# "par arrêt du ... la Cour/le Tribunal ..."
# "con sentenza del ... il Tribunale ..."
# Captures a docket in trailing parentheses (if present).
_NARRATIVE_PRIOR_RE = re.compile(
    r"(?:mit\s+(?:Urteil|Entscheid|Beschluss)"
    r"|par\s+(?:arr[eê]t|d[eé]cision|jugement)"
    r"|con\s+(?:sentenza|decisione|decreto))"
    r"\s+(?:vom|du|del)\s+"
    r"[^(]{5,300}?"
    r"\(([^)]{3,100})\)",
    re.IGNORECASE | re.DOTALL,
)


def extract_prior_instance(text: str | None) -> list[str]:
    """Extract prior instance docket number(s) from a decision's text.

    Swiss court decisions (especially BGer) include a formulaic header identifying
    the prior instance, e.g.:
        Beschwerde gegen den Entscheid des Obergerichts vom 13.11.2025 (SBK.2025.285).
        recours contre l'arrêt de la Cour de justice du 6 août 2024 (ATA/917/2024).
        ricorso contro la sentenza del Tribunale del 31 marzo 2025 (35.2024.77).

    Also checks the Sachverhalt body for narrative prior-instance mentions:
        mit Urteil vom 14. Juli 2005 wies das Appellationsgericht ... ab (No. 123/2005).
        par arrêt du 6 août 2024, la Cour d'appel ... (A/1168/2024).

    Returns normalized docket references of the prior instance(s).
    """
    if not text:
        return []

    dockets: list[str] = []
    seen: set[str] = set()

    def _add(paren_content: str) -> None:
        for docket in _extract_dockets_from_paren(paren_content):
            if docket and docket not in seen:
                seen.add(docket)
                dockets.append(docket)

    # Pass 1: formulaic header (Gegenstand → Sachverhalt region)
    header = _extract_header_section(text)
    for match in _PRIOR_INSTANCE_RE.finditer(header):
        _add(match.group(1).strip())

    # Pass 2: narrative patterns in the Sachverhalt body
    # Extract the Sachverhalt section (between Sachverhalt: and Erwägungen:)
    if not dockets:
        sv_start = _SACHVERHALT_RE.search(text)
        if sv_start:
            ew = _ERWAEGUNGEN_RE.search(text, pos=sv_start.end())
            sv_end = ew.start() if ew else min(sv_start.end() + 5000, len(text))
            sachverhalt = text[sv_start.end():sv_end]
            for match in _NARRATIVE_PRIOR_RE.finditer(sachverhalt):
                _add(match.group(1).strip())

    # Pass 3: also try the formulaic pattern in the Sachverhalt body
    # (some decisions have "Beschwerde gegen ..." buried in the Sachverhalt)
    if not dockets:
        sv_start = _SACHVERHALT_RE.search(text)
        if sv_start:
            ew = _ERWAEGUNGEN_RE.search(text, pos=sv_start.end())
            sv_end = ew.start() if ew else min(sv_start.end() + 5000, len(text))
            sachverhalt = text[sv_start.end():sv_end]
            for match in _PRIOR_INSTANCE_RE.finditer(sachverhalt):
                _add(match.group(1).strip())

    return dockets


def _extract_header_section(text: str) -> str:
    """Extract the header section where the prior instance is declared.

    Looks for text between 'Gegenstand/Objet/Oggetto' and the first body
    marker (Erwägung/Sachverhalt/etc.). Falls back to the first 2000 chars.

    The heading is the word on a line of its own (BGer: "Gegenstand\\n").
    The bare word also occurs in the reasoning ("Gegenstand des Verfahrens"),
    and a cantonal judgment without a Gegenstand heading used to lose its real
    header to that mention: ZH Obergericht LA240031 took its "header" from
    char 33,883 and never saw "Berufung gegen ... (AN220047-L)" on line 15.
    A bare-word match therefore only counts near the top, where a flattened
    text (no line breaks) still carries it.
    """
    gegenstand = _GEGENSTAND_LINE_RE.search(text) or _GEGENSTAND_RE.search(
        text, 0, _GEGENSTAND_MAX_START
    )
    if not gegenstand:
        return text[:2000]

    start = gegenstand.start()
    body = _BODY_START_RE.search(text, pos=start + 10)
    end = body.start() if body else min(start + 2000, len(text))
    return text[start:end]


_ZH_SPLIT_SUFFIX_RE = re.compile(r"(?<=\b[A-Z]{2}\d{6})-\s+(?=[A-Z](?![A-Za-z]))")


def _extract_dockets_from_paren(content: str) -> list[str]:
    """Extract and normalize docket references from parenthetical content.

    Handles single dockets ('SBK.2025.285'), multiple dockets separated
    by ' - ' ('A/1168/2024 AIDSO - ATA/917/2024'), comma ('A/1168/2024,
    ATA/917/2024'), or semicolon, and filters out redacted content ('N (...)').
    """
    if not content or content.strip() in ("...", "…"):
        return []

    # A ZH docket broken at its hyphen by the PDF line wrap: "(AN220047- L)".
    content = _ZH_SPLIT_SUFFIX_RE.sub("-", content)

    # Split on spaced dash/en-dash, comma, or semicolon for multi-docket
    # parentheticals like "A/1168/2024 AIDSO - ATA/917/2024" or
    # "A/1168/2024, ATA/917/2024" or "A/1168/2024; ATA/917/2024"
    parts = re.split(r"\s+[-–]\s+|[,;]\s*", content)

    results: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Try existing DOCKET_PATTERNS — findall to capture multiple dockets
        # within a single part (e.g. "4A_648/2024 AIDSO ATA/917/2024")
        found = False
        for pattern in DOCKET_PATTERNS[:2]:  # skip BGE-style pattern
            matches = pattern.findall(part)
            if matches:
                for match_val in matches:
                    # findall returns group(0) for patterns without groups,
                    # or the group content for patterns with groups
                    raw = match_val if isinstance(match_val, str) else match_val[0]
                    normalized = _normalize_docket(raw)
                    if normalized:
                        results.append(normalized)
                        found = True
                break  # don't try the next pattern if this one matched
        if found:
            continue

        # Try broader pattern for formats like "35.2024.77"
        m = _PAREN_DOCKET_RE.search(part)
        if m:
            normalized = _normalize_docket(m.group(0))
            if normalized and len(normalized) >= 5:
                results.append(normalized)
                continue

        # Fallback: if part is short and contains both letters and digits,
        # treat the whole thing as a docket (handles ZH-style "FP240022-L")
        if (
            5 <= len(part) <= 40
            and any(c.isdigit() for c in part)
            and any(c.isalpha() for c in part)
            and " " not in part  # single token only
        ):
            normalized = _normalize_docket(part)
            if normalized and len(normalized) >= 5:
                results.append(normalized)

    return results
