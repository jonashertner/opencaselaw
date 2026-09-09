"""SEO-optimized HTML pages for individual court decisions.

Serves lightweight HTML pages with:
- Schema.org LegalCase markup
- Open Graph tags
- Regeste, metadata, statute references
- Links to PDF/source
- Canonical URLs

Mounted at /entscheid/{decision_id} on the Starlette app.
"""
from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import urllib.parse
from pathlib import Path

import ecthr_docket

logger = logging.getLogger("swiss-caselaw-mcp")

DATA_DIR = Path(os.environ.get("SWISS_CASELAW_DIR", str(Path.home() / ".swiss-caselaw")))
BASE_URL = os.environ.get("SWISS_CASELAW_BASE_URL", "https://mcp.opencaselaw.ch")


# ── Text reflow ───────────────────────────────────────────────────────
#
# Source corpus text is hard-wrapped at PDF column width (60–80 chars)
# and additionally wraps inline citations like "BGE 129 III 209" or
# "Art. 27 ZGB" onto their own lines. Rendering those `\n`s with
# CSS `white-space: pre-wrap` produces the jagged "citation dangling
# mid-sentence" look the user reported.
#
# Fix: split on blank lines into logical paragraphs, then within each
# paragraph collapse single newlines + whitespace runs into single
# spaces. List items (1. / a) / – ) keep their hard break so dispositiv
# orders and enumerations stay legible.
#
# This mirrors the helpers in exports.py (used by DOCX / PDF) so HTML,
# DOCX and PDF outputs all flow identically.

_LIST_MARKER_RE = re.compile(r"^([0-9]+[.)]|[a-z][.)]|[-*\u2013\u2022])\s")

# Punctuation-spacing fixes for text that has been re-joined from
# PDF-extracted lines (citation-broken inline text). Applied AFTER
# newline-as-space collapse so we tighten artefacts like "( art." and
# "L' art" without disturbing intentional whitespace inside the prose.
#
# Conservative: only the patterns that are *always* wrong in legal
# DE/FR/IT typography. Skipped intentionally:
#   * `:` — French typography uses a thin space before, hard to
#     distinguish from the join artefact without a NBSP signal.
#   * `.` — risk of breaking abbreviations like "Bd. " or "S. ".
_PUNCT_OPEN_PAREN_RE = re.compile(r"\(\s+")
_PUNCT_CLOSE_PAREN_RE = re.compile(r"\s+\)")
_PUNCT_BEFORE_SEMI_COMMA_RE = re.compile(r"\s+([;,])")
# Apostrophe elision (FR / IT): "L' art" → "L'art". Allow-list of
# known elision prefixes — keeps the rule from gluing English closing
# quotes (e.g. `She said 'Hello' and waved` would otherwise become
# `Hello'and waved`). Longer alternatives ordered first so the regex
# matches greedily.
_PUNCT_ELISION_RE = re.compile(
    r"\b("
    r"jusqu|lorsqu|puisqu|presqu|quoiqu"     # FR compound w/ qu'
    r"|dell|nell|sull|dall|all"              # IT compound w/ ll'
    r"|gli"                                  # IT
    r"|qu|ch"                                # FR / IT digraph
    r"|[ldnscmtjv]"                          # FR / IT mono-letter
    r")(['\u2019])\s+([a-zA-Z\u00C0-\u017F])",
    re.IGNORECASE,
)


def _tighten_punctuation_spacing(text: str) -> str:
    if not text:
        return text
    text = _PUNCT_OPEN_PAREN_RE.sub("(", text)
    text = _PUNCT_CLOSE_PAREN_RE.sub(")", text)
    text = _PUNCT_BEFORE_SEMI_COMMA_RE.sub(r"\1", text)
    text = _PUNCT_ELISION_RE.sub(r"\1\2\3", text)
    return text


def _flow_paragraph(text: str) -> str:
    """Collapse a chunk of corpus text into a single re-flowable
    paragraph: newlines + runs of whitespace become single spaces."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = _tighten_punctuation_spacing(text)
    return text.strip()


def _split_paragraphs(text: str) -> list[str]:
    """Split text into logical paragraphs.

    * Blank lines (``\\n\\s*\\n``) → paragraph boundary.
    * Within a paragraph, lines that begin with a list marker stay as
      their own paragraph (preserves dispositiv-style enumerations).
    * Other single newlines collapse into spaces (joins PDF column
      wraps and dangling citations).

    HUDOC fallback: ECHR decisions ingested via HUDOC have many
    single-newlines but ZERO blank-line breaks (their source HTML's
    block boundaries collapse to ``\\n`` under BeautifulSoup's
    ``separator='\\n'``, never doubled). When we detect that pattern
    — many ``\\n`` and no ``\\n\\n`` — we treat each ``\\n`` as a
    paragraph boundary so the rendered page isn't one giant wall of
    text. This kicks in only when the heuristic strongly applies, so
    courts whose source HAS proper blank-line paragraph structure are
    unaffected.
    """
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # HUDOC fallback: many single newlines, no blank lines → each line
    # IS a paragraph. Threshold of 30 protects against short single-
    # paragraph regestes / dispositivs.
    #
    # 2026-05-09 hardening: also gate on line-length distribution.
    # PDF-extracted BGE Erwägungen (e.g. bge_BGE_133_III_121 E. 3.1)
    # have 30+ newlines and 0 blank breaks too, but the newlines split
    # mid-sentence around legal citations like "art. 394 ss CO" — those
    # short citation-only lines should INLINE into the surrounding
    # prose, not become standalone paragraphs. Real HUDOC paragraphs
    # are legal-prose lines that average well over 30 characters; a
    # majority-of-short-lines pattern indicates citation-broken inline
    # text, in which case we fall through to the regular path that
    # collapses single newlines into spaces.
    n_newlines = text.count("\n")
    n_blank_breaks = len(re.findall(r"\n\s*\n", text))
    if n_newlines >= 30 and n_blank_breaks == 0:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        short_ratio = (
            sum(1 for ln in lines if len(ln) < 30) / len(lines)
            if lines else 0.0
        )
        if lines and short_ratio < 0.30:
            return [_tighten_punctuation_spacing(ln) for ln in lines]
        # else: fall through — the regular newline-as-space collapse
        # below produces correctly-flowed prose.

    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n+", text.strip()):
        if not block.strip():
            continue
        items: list[str] = []
        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if not items:
                items.append(line)
                continue
            if _LIST_MARKER_RE.match(line):
                items.append(line)
            else:
                items[-1] = re.sub(r" {2,}", " ", items[-1].rstrip() + " " + line)
        paragraphs.extend(_tighten_punctuation_spacing(p) for p in items)
    return paragraphs


_REGESTE_SPLIT_RE = re.compile(r"(?=\b(?:Regeste|Regesto)\b)", re.IGNORECASE)


def _split_regeste(regeste: str) -> list[str]:
    """Split a multilingual regeste into per-language paragraphs.
    Falls back to a single flowed paragraph when no language headers
    are detectable (most cantonal decisions)."""
    if not regeste or not regeste.strip():
        return []
    parts = [p for p in _REGESTE_SPLIT_RE.split(regeste) if p and p.strip()]
    if len(parts) <= 1:
        return [_flow_paragraph(regeste)]
    return [_flow_paragraph(p) for p in parts if _flow_paragraph(p)]


def _paragraphs_html(text: str, *, classname: str | None = None) -> str:
    """Render `text` as a sequence of <p> elements after reflow.
    Returns the empty string for empty input."""
    paras = _split_paragraphs(text)
    if not paras:
        return ""
    cls = f' class="{classname}"' if classname else ""
    return "\n".join(f"<p{cls}>{_esc(p)}</p>" for p in paras)

# Court display names (subset — full list in mcp_server.py)
_COURT_NAMES = {
    "bger": "Bundesgericht", "bge": "Bundesgericht (BGE)",
    "bge_historical": "Bundesgericht (historisch)",
    "ecthr_chamber": "EGMR (Kammer)",
    "ecthr_grand_chamber": "EGMR (Grosse Kammer)",
    "ecthr_committee": "EGMR (Ausschuss)",
    "bvger": "Bundesverwaltungsgericht", "bstger": "Bundesstrafgericht",
    "bpatger": "Bundespatentgericht",
    "ag_gerichte": "Aargau", "ai_gerichte": "Appenzell I.Rh.",
    "ar_gerichte": "Appenzell A.Rh.", "be_verwaltungsgericht": "Bern VerwG",
    "be_zivilstraf": "Bern OG", "bl_gerichte": "Basel-Landschaft",
    "bs_appellationsgericht": "Basel-Stadt", "fr_gerichte": "Freiburg",
    "ge_gerichte": "Genf", "gl_gerichte": "Glarus", "gr_gerichte": "Graubünden",
    "ju_gerichte": "Jura", "lu_gerichte": "Luzern", "ne_gerichte": "Neuenburg",
    "nw_gerichte": "Nidwalden", "ow_gerichte": "Obwalden",
    "sg_gerichte": "St. Gallen", "sh_gerichte": "Schaffhausen",
    "so_gerichte": "Solothurn", "sz_gerichte": "Schwyz",
    "tg_gerichte": "Thurgau", "ti_gerichte": "Ticino",
    "ur_gerichte": "Uri", "vd_gerichte": "Waadt", "vd_findinfo": "Waadt",
    "vs_gerichte": "Wallis", "zg_obergericht": "Zug OG",
    "zh_obergericht": "Zürich OG", "zh_verwaltungsgericht": "Zürich VerwG",
    "zh_sozialversicherungsgericht": "Zürich SozVersG",
}


def _get_db():
    db_path = DATA_DIR / "decisions.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_structure_db():
    """Open the decision-structure sidecar DB if available, else None."""
    db_path = Path(os.environ.get(
        "SWISS_CASELAW_STRUCTURE_DB", str(DATA_DIR / "decision_structure.db")
    ))
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


_E_NUMBER_LEVEL_RE = re.compile(r"^(\d*)([A-Za-z]*)$")


def _e_number_sort_key(e_number: str) -> tuple:
    """Sort '1.10' after '1.9' (numeric, not lexicographic) and keep
    lettered Erwägungen in document order: '2' < '2a' < '2a/aa' <
    '2a/bb' < '2b', and 'aa' < 'ab' < 'bb'.

    Every level is a (number, letters) pair, so keys always compare.
    The old int-or-str mix raised TypeError as soon as the structure
    sidecar started carrying lettered numbers ('2a', '2aa', '3c/aa';
    2026-09-06 incident). Levels split on '.' and on '/' (the extractor
    writes a double-letter run under a letter as '3c/aa')."""
    out = []
    for level in re.split(r"[./]", str(e_number or "")):
        m = _E_NUMBER_LEVEL_RE.match(level)
        if level and m:
            out.append((int(m.group(1)) if m.group(1) else 0, m.group(2).lower()))
        else:
            out.append((0, level.lower()))
    return tuple(out)


def _fetch_structure(decision_id: str) -> dict | None:
    """Return {'row': structure_row, 'paragraphs': [..]} or None."""
    sconn = _get_structure_db()
    if sconn is None:
        return None
    try:
        row = sconn.execute(
            "SELECT * FROM structure WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if not row:
            return None
        paragraphs = sconn.execute(
            "SELECT e_number, depth, parent, text FROM erwaegungen_paragraph "
            "WHERE decision_id = ? ORDER BY depth, e_number",
            (decision_id,),
        ).fetchall()
        paragraphs = sorted(
            [dict(p) for p in paragraphs],
            key=lambda p: _e_number_sort_key(p["e_number"]),
        )
        return {"row": dict(row), "paragraphs": paragraphs}
    finally:
        sconn.close()


_REFERENCE_DB_PATH = DATA_DIR / "reference_graph.db"
_STATUTE_RE = re.compile(r"^ART\.([^.]+)(?:\.ABS\.([^.]+))?\.(.+)$")


def _get_reference_db():
    """Open reference_graph.db read-only. Returns None if unavailable
    (the page falls back to the no-citations layout in that case)."""
    if not _REFERENCE_DB_PATH.exists():
        return None
    try:
        return sqlite3.connect(
            f"file:{_REFERENCE_DB_PATH}?mode=ro&immutable=1",
            uri=True, timeout=30,
        )
    except Exception:
        return None


def _decision_id_variants(did: str) -> list[str]:
    """BGE-style ids appear with and without the leading court prefix in the
    citation graph; try both so the join hits regardless of the storage form."""
    variants = {did}
    if did.startswith("bge_"):
        variants.add(did[4:])  # "bge_BGE_..." → "BGE_..."
    if did.startswith("BGE_"):
        variants.add("bge_" + did)
    return list(variants)


def _parse_statute_id(sid: str) -> tuple[str, str, str] | None:
    """Parse 'ART.20.ABS.2.OR' → ('OR', '20', 'Art. 20 Abs. 2 OR').
    Returns None if the shape is unrecognised."""
    if not sid:
        return None
    m = _STATUTE_RE.match(sid)
    if not m:
        return None
    art, para, abbr = m.group(1), m.group(2), m.group(3)
    label = f"Art. {art}"
    if para:
        label += f" Abs. {para}"
    label += f" {abbr}"
    return (abbr, art, label)


def _fetch_citations(decision_id: str, *, limit: int = 10) -> dict:
    """Pull the citation-graph context for a decision: outbound case
    citations, inbound case citations, statute references."""
    out: list[tuple[str, str, int]] = []
    inb: list[tuple[str, int]] = []
    statutes: list[tuple[str, str, str, int]] = []
    conn = _get_reference_db()
    if conn is None:
        return {"out": out, "in": inb, "statutes": statutes}
    try:
        ids = _decision_id_variants(decision_id)
        ph = ",".join("?" * len(ids))
        for row in conn.execute(
            f"SELECT target_decision_id, MIN(target_ref) ref, COUNT(*) c "
            f"FROM citation_targets "
            f"WHERE source_decision_id IN ({ph}) AND target_decision_id IS NOT NULL "
            f"GROUP BY target_decision_id ORDER BY c DESC LIMIT ?",
            (*ids, limit),
        ).fetchall():
            out.append((row[0], row[1] or row[0], row[2]))
        for row in conn.execute(
            f"SELECT source_decision_id, COUNT(*) c "
            f"FROM citation_targets "
            f"WHERE target_decision_id IN ({ph}) "
            f"GROUP BY source_decision_id ORDER BY c DESC LIMIT ?",
            (*ids, limit),
        ).fetchall():
            inb.append((row[0], row[1]))
        for row in conn.execute(
            f"SELECT statute_id, mention_count FROM decision_statutes "
            f"WHERE decision_id IN ({ph}) "
            f"ORDER BY mention_count DESC LIMIT ?",
            (*ids, limit),
        ).fetchall():
            parsed = _parse_statute_id(row[0])
            if parsed:
                statutes.append((parsed[0], parsed[1], parsed[2], row[1]))
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return {"out": out, "in": inb, "statutes": statutes}


def _esc(text: str | None) -> str:
    return html.escape(text or "", quote=True)


def _short_court_label(court_code: str) -> str:
    """Compact court display name for citation lists. Falls back to the
    underscore-stripped code when not in _COURT_NAMES."""
    if not court_code:
        return ""
    if court_code in _COURT_NAMES:
        return _COURT_NAMES[court_code]
    return court_code.replace("_", " ").upper()


_COURT_SHORT = {
    "bger": "BGer", "bvger": "BVGer", "bstger": "BStGer", "bpatger": "BPatGer",
    "bge": "BGE", "bge_historical": "BGE", "bge_egmr": "EGMR",
    "ch_bundesrat": "BR", "mkg": "MKG", "hudoc_ch": "EGMR",
    "ecthr_chamber": "EGMR", "ecthr_grand_chamber": "EGMR (GK)",
    "ecthr_committee": "EGMR",
    "finma": "FINMA", "finma_versicherungsrecht": "FINMA-VR", "weko": "WEKO",
    "edoeb": "EDÖB", "ubi": "UBI", "elcom": "ElCom", "postcom": "PostCom",
    "comcom": "ComCom", "ta_sst": "TA-SST", "emark": "EMARK",
}


def _short_decision_label(did: str) -> str:
    """Render a decision_id as a short, human-readable citation.

    Examples:
      bge_BGE_143_III_480 → 'BGE 143 III 480'
      BGE_143_III_480     → 'BGE 143 III 480'
      bger_4A_140_2023    → 'BGer 4A_140/2023'
      zh_obergericht_NP190024 → 'ZH Obergericht NP190024'
    """
    if not did:
        return ""
    # BGE forms first (highest precision)
    if did.startswith("bge_BGE_"):
        rest = did[len("bge_BGE_"):]
        return "BGE " + rest.replace("_", " ")
    if did.startswith("BGE_"):
        return "BGE " + did[4:].replace("_", " ")
    # Try the longest court-prefix match.
    for prefix in sorted(_COURT_SHORT, key=len, reverse=True):
        if did.startswith(prefix + "_"):
            docket = did[len(prefix) + 1:]
            # bger 4A_140_2023 → 4A_140/2023 (slash before the 4-digit year)
            if "_" in docket and docket.rsplit("_", 1)[-1].isdigit() and len(docket.rsplit("_", 1)[-1]) == 4:
                head, year = docket.rsplit("_", 1)
                docket = head + "/" + year
            return f"{_COURT_SHORT[prefix]} {docket}"
    # Cantonal pattern: "{canton}_{court_word}_{rest}" → "{CANTON} {Court} {rest}"
    parts = did.split("_", 2)
    if len(parts) == 3 and len(parts[0]) == 2 and parts[0].isalpha():
        canton = parts[0].upper()
        court_word = parts[1].replace("gerichte", "Gerichte").title()
        return f"{canton} {court_word} {parts[2]}"
    return did.replace("_", " ")


def _render_citation_section(citations: dict, language: str) -> str:
    """Bottom-of-page citation context: outbound, inbound, statutes.
    All three lists are optional — only the populated columns render."""
    out, inb, statutes = citations["out"], citations["in"], citations["statutes"]
    if not (out or inb or statutes):
        return ""

    labels = {
        "de": ("Verweise & Kontext", "Zitiert (Out)", "Zitiert von (In)", "Gesetzesartikel", "Vorinstanzen / verwandte Entscheide"),
        "fr": ("Références & contexte", "Cite", "Cité par", "Articles de loi", "Décisions liées"),
        "it": ("Riferimenti & contesto", "Cita", "Citato da", "Articoli di legge", "Decisioni correlate"),
    }
    lab = labels.get(language, labels["de"])

    parts = ['<aside class="citations" id="citations">']
    parts.append(f'<h2>{_esc(lab[0])}</h2>')
    parts.append('<div class="citations-grid">')

    if out:
        parts.append(f'<div class="cit-col"><h3>{_esc(lab[1])} <span class="count">({len(out)})</span></h3><ul>')
        for did, ref, cnt in out:
            label = ref if ref else _short_decision_label(did)
            parts.append(
                f'<li><a href="/entscheid/{_esc(did)}">{_esc(label)}</a>'
                + (f' <span class="muted">×{cnt}</span>' if cnt > 1 else '')
                + '</li>'
            )
        parts.append('</ul></div>')

    if inb:
        parts.append(f'<div class="cit-col"><h3>{_esc(lab[2])} <span class="count">({len(inb)})</span></h3><ul>')
        for did, cnt in inb:
            parts.append(
                f'<li><a href="/entscheid/{_esc(did)}">{_esc(_short_decision_label(did))}</a>'
                + (f' <span class="muted">×{cnt}</span>' if cnt > 1 else '')
                + '</li>'
            )
        parts.append('</ul></div>')

    if statutes:
        parts.append(f'<div class="cit-col"><h3>{_esc(lab[3])}</h3><ul>')
        seen_labels = set()
        for abbr, art, label, cnt in statutes:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            parts.append(
                f'<li><a href="/laws/?law={_esc(abbr)}&art={_esc(art)}">{_esc(label)}</a>'
                + (f' <span class="muted">×{cnt}</span>' if cnt > 1 else '')
                + '</li>'
            )
        parts.append('</ul></div>')

    parts.append('</div></aside>')
    return "\n".join(parts)


def _truncate(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def render_decision_page(
    decision_id: str,
    *,
    highlight: str | None = None,
    e_focus: str | None = None,
) -> tuple[str, int, str | None]:
    """Render an HTML page for a single decision.

    Returns ``(html, status_code, redirect_location)``. ``redirect_location``
    is non-None only for the 301 docket-redirect case below; the caller must
    send it as the ``Location`` header instead of ``html`` when present.

    Optional ``highlight``: a verbatim substring that, if found in the
    Erwägung whose ``e_number`` matches ``e_focus``, is wrapped in
    ``<mark>…</mark>`` so the lawyer landing on the page sees the exact
    sentence that triggered the citation. Used by the
    find_relevant_erwaegung MCP tool's display_url so chat → browser
    handoff lands on the highlighted span, not just the right anchor.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()

        if row:
            return _render_decision(row, highlight=highlight, e_focus=e_focus), 200, None

        # P1.4: no LIKE %...% substring fallback here any more — that let
        # /entscheid/1 resolve to an unrelated decision with HTTP 200 via
        # nondeterministic substring matching. An EXACT docket_number match
        # is safe to redirect on ONLY if it is unique; zero or multiple
        # matches both 404 (the caller decoded the path segment already, so
        # docket_number values containing '/' or spaces compare correctly
        # here).
        docket_matches = conn.execute(
            "SELECT decision_id FROM decisions WHERE docket_number = ? LIMIT 2",
            (decision_id,),
        ).fetchall()
        if len(docket_matches) == 1:
            canonical_id = docket_matches[0]["decision_id"]
            location = "/entscheid/" + urllib.parse.quote(str(canonical_id), safe="")
            return "", 301, location

        return _render_404(decision_id), 404, None
    finally:
        conn.close()


def _apply_highlight(escaped_text: str, highlight: str) -> str:
    """Wrap the first verbatim occurrence of ``highlight`` in ``escaped_text``
    with ``<mark>…</mark>``. Both inputs are HTML-escaped already; the wrapper
    tags are injected raw. Case-sensitive on the raw substring (lawyers cite
    verbatim quotes), but normalises whitespace runs so PDF column wrapping
    in the corpus doesn't defeat the match.
    """
    if not highlight or not escaped_text:
        return escaped_text
    needle = _esc(highlight.strip())
    if not needle:
        return escaped_text
    # Whitespace-tolerant match: replace runs of whitespace in BOTH the
    # haystack and needle with a single space for matching, but rewrite
    # the original positions in the haystack — so we don't lose layout.
    norm_re = re.compile(r"\s+")
    flat_hay = norm_re.sub(" ", escaped_text)
    flat_needle = norm_re.sub(" ", needle)
    idx = flat_hay.find(flat_needle)
    if idx < 0:
        return escaped_text
    # Re-find the same position in the un-flattened haystack by walking
    # both strings in lockstep.
    j = 0  # cursor in escaped_text
    flat_cursor = 0
    start = end = -1
    while j < len(escaped_text):
        ch = escaped_text[j]
        flat_ch = " " if ch.isspace() else ch
        # advance the flat cursor only when we emit a non-collapsed char
        if not (ch.isspace() and j > 0 and escaped_text[j - 1].isspace()):
            if flat_cursor == idx and start < 0:
                start = j
            flat_cursor += 1
            if flat_cursor == idx + len(flat_needle):
                end = j + 1
                break
        j += 1
    if start < 0 or end < 0:
        return escaped_text
    return escaped_text[:start] + "<mark>" + escaped_text[start:end] + "</mark>" + escaped_text[end:]


def _render_decision(
    row: sqlite3.Row,
    *,
    highlight: str | None = None,
    e_focus: str | None = None,
) -> str:
    did = row["decision_id"]
    court = row["court"] or ""
    court_name = _COURT_NAMES.get(court, court.replace("_", " ").title())
    canton = row["canton"] or ""
    # ECtHR dockets carry a _yyyymmdd key suffix that must never reach a page
    # title, an og:title or the Schema.org LegalCase name.
    docket = ecthr_docket.display_docket(row["court"], row["docket_number"]) or did
    date = row["decision_date"] or ""
    language = row["language"] or "de"
    title = row["title"] or ""
    regeste = row["regeste"] or ""
    full_text = row["full_text"] or ""
    source_url = row["source_url"] or ""
    pdf_url = row["pdf_url"] or ""

    # Clean regeste for meta description
    clean_regeste = re.sub(r"<[^>]+>", " ", regeste)  # strip HTML tags
    clean_regeste = re.sub(r"^Regeste\s*\n?\s*", "", clean_regeste)  # strip "Regeste" prefix
    clean_regeste = re.sub(r"^Regesto\s*\n?\s*", "", clean_regeste)
    meta_desc = _truncate(re.sub(r"\s+", " ", clean_regeste).strip(), 160)

    # Page title
    if title:
        page_title = f"{docket}, {_truncate(title, 60)} | OpenCaseLaw"
    else:
        page_title = f"{docket}, {court_name} | OpenCaseLaw"

    canonical = f"{BASE_URL}/entscheid/{did}"

    # Mint both identifiers for the Schema.org LegalCase block:
    #   - cli:ch (Swiss-native, primary): captures cantonal sovereignty,
    #     trilingualism, pinpoint and version as first-class axes.
    #   - ECLI (European projection): deterministic many-to-one
    #     projection used for cross-border interop. See cli_ch.py.
    try:
        from cli_ch import mint_cli_ch
        cli_ch_uri = mint_cli_ch(
            decision_id=did,
            court=court,
            docket_number=docket,
        )
    except Exception:
        cli_ch_uri = None
    try:
        from ecli import mint_ecli
        ecli_uri = mint_ecli(
            decision_id=did,
            court=court,
            docket_number=docket,
            decision_date=date,
            language=language,
        )
    except Exception:
        ecli_uri = None

    # Schema.org LegalCase — enriched 2026-05-21 with identifier (ECLI),
    # citation (case-citation graph), and legislationCited (statute graph).
    # These three fields are what turns a Schema.org LegalCase from "page
    # title + date" into a machine-readable legal record. The arrays are
    # capped (100 / 100) so the per-decision payload stays well under
    # 100 KB even for the most citation-heavy BGE.
    schema_json = {
        "@context": "https://schema.org",
        "@type": "LegalCase",
        "name": docket,
        "description": _truncate(regeste, 300),
        "datePublished": date,
        "inLanguage": language,
        "court": {
            "@type": "GovernmentOrganization",
            "name": court_name,
            "address": {
                "@type": "PostalAddress",
                "addressCountry": "CH",
            },
        },
        "url": canonical,
        "isPartOf": {
            "@type": "Dataset",
            "name": "Swiss Case Law Open Dataset",
            "url": "https://opencaselaw.ch",
        },
    }
    identifiers = []
    if cli_ch_uri:
        identifiers.append({
            "@type": "PropertyValue",
            "propertyID": "cli:ch",
            "value": cli_ch_uri,
        })
    if ecli_uri:
        identifiers.append({
            "@type": "PropertyValue",
            "propertyID": "ECLI",
            "value": ecli_uri,
        })
    if identifiers:
        schema_json["identifier"] = identifiers if len(identifiers) > 1 else identifiers[0]
    if source_url:
        schema_json["sameAs"] = source_url

    # Enrich with outbound case + statute citations from the reference
    # graph. _fetch_citations returns {"out": [(target_id, ref, count)],
    # "in": [...], "statutes": [(law_code, article, version, count)]}.
    # We cap at 100 each so the per-decision payload stays under 100 KB
    # even on citation-heavy BGEs.
    try:
        _citations = _fetch_citations(did)
    except Exception:
        _citations = None
    if _citations:
        out_cases = _citations.get("out") or []
        if out_cases:
            schema_json["citation"] = [
                {
                    "@type": "LegalCase",
                    "name": (ref or target_id),
                    "url": f"{BASE_URL}/entscheid/{target_id}",
                }
                for (target_id, ref, _count) in out_cases[:100]
                if target_id
            ]
        cited_statutes = _citations.get("statutes") or []
        if cited_statutes:
            schema_json["legislationCited"] = [
                {
                    "@type": "Legislation",
                    "name": f"Art. {article} {law_code}".strip(),
                }
                for (law_code, article, _version, _count) in cited_statutes[:100]
                if law_code and article
            ]

    import json
    schema_str = json.dumps(schema_json, ensure_ascii=False)

    # Language labels
    lang_labels = {"de": "Deutsch", "fr": "Français", "it": "Italiano", "rm": "Rumantsch"}
    lang_label = lang_labels.get(language, language)

    # Regeste paragraphs — multilingual headers split per-language, then
    # each language is reflowed (collapses PDF column line-wraps).
    regeste_html = ""
    if regeste:
        for para in _split_regeste(regeste):
            regeste_html += f"<p>{_esc(para)}</p>\n"

    # Structured fields (Sachverhalt / Erwägungen / Dispositiv) from sidecar DB
    structure = _fetch_structure(did)
    structured_html = ""
    if structure and structure["row"]:
        srow = structure["row"]
        sachverhalt = srow.get("sachverhalt") or ""
        dispositiv = srow.get("dispositiv") or ""
        paragraphs = structure["paragraphs"]
        try:
            import json as _json
            disp_orders = _json.loads(srow["dispositiv_orders"]) if srow.get("dispositiv_orders") else []
        except Exception:
            disp_orders = []

        parts = ['<section class="structured">']

        if sachverhalt:
            parts.append('<details><summary><strong>Sachverhalt</strong></summary>')
            parts.append(f'<div class="section-body">{_paragraphs_html(sachverhalt)}</div></details>')

        if paragraphs:
            parts.append('<details open><summary><strong>Erwägungen</strong> '
                         f'<span class="count">({len(paragraphs)} Absätze)</span></summary>')
            parts.append('<div class="erwaegungen">')
            # If we received a ?highlight=… query, render a banner above
            # the Erwägungen section so the lawyer sees WHY the page was
            # opened with a highlight (came from chat / Word add-in).
            if highlight and e_focus:
                parts.append(
                    f'<div class="match-banner" role="status">'
                    f'<span class="match-banner-icon" aria-hidden="true">★</span> '
                    f'Treffer in <a href="#e-{_esc(e_focus.replace(".", "-"))}">'
                    f'E. {_esc(e_focus)}</a> — markierter Satz unten hervorgehoben.'
                    f'</div>'
                )
            for p in paragraphs:
                indent_px = (p["depth"] - 1) * 16
                e_label = f"E. {p['e_number']}"
                anchor = f"e-{p['e_number'].replace('.', '-')}"
                # Each Erwägung is paragraph-flowed; the E-number anchor
                # sits inline with the first paragraph for compact scanning.
                e_paras = _split_paragraphs(p["text"])
                if not e_paras:
                    continue
                # Apply the ?highlight= substring only to the focused
                # Erwägung — never elsewhere — so the same string showing up
                # in another paragraph (boilerplate, repeated tests) doesn't
                # also get marked.
                is_focus = bool(e_focus and p["e_number"] == e_focus)
                first_esc = _esc(e_paras[0])
                if is_focus and highlight:
                    first_esc = _apply_highlight(first_esc, highlight)
                first_html = (
                    f'<a class="e-num" href="#{anchor}">{_esc(e_label)}</a> '
                    f'{first_esc}'
                )
                rest_paras = []
                for par in e_paras[1:]:
                    par_esc = _esc(par)
                    if is_focus and highlight:
                        par_esc = _apply_highlight(par_esc, highlight)
                    rest_paras.append(f'<p>{par_esc}</p>')
                rest_html = "".join(rest_paras)
                focus_class = " erw-focus" if is_focus and highlight else ""
                parts.append(
                    f'<div class="erw{focus_class}" id="{anchor}" style="margin-left:{indent_px}px">'
                    f'<p class="e-text">{first_html}</p>{rest_html}</div>'
                )
            parts.append('</div></details>')

        if dispositiv:
            parts.append('<details open><summary><strong>Dispositiv</strong></summary>')
            if disp_orders:
                parts.append('<ol class="dispositiv-orders">')
                for order in disp_orders:
                    parts.append(f'<li>{_esc(order)}</li>')
                parts.append('</ol>')
            else:
                parts.append(f'<div class="section-body">{_paragraphs_html(dispositiv)}</div>')
            parts.append('</details>')

        parts.append('</section>')
        structured_html = "\n".join(parts)

    # Full decision text — collapsed by default when structured view is available
    text_excerpt = ""
    if full_text:
        is_open = "" if structure else " open"
        text_excerpt = f"""
        <details{is_open}>
            <summary>Volltext (verifizierbarer Originaltext)</summary>
            <div class="fulltext">{_paragraphs_html(full_text)}</div>
        </details>"""

    # Citation graph context — outbound + inbound + statute references.
    citations = _fetch_citations(did)
    citations_html = _render_citation_section(citations, language)

    # Localised section labels
    breadcrumb_labels = {"de": "Startseite", "fr": "Accueil", "it": "Home"}
    sources_labels = {"de": "Quelle", "fr": "Source", "it": "Fonte"}
    pdf_labels = {"de": "Original-PDF", "fr": "PDF original", "it": "PDF originale"}
    export_labels = {"de": "Export", "fr": "Export", "it": "Esporta"}
    home_label = breadcrumb_labels.get(language, "Home")
    source_label = sources_labels.get(language, "Source")
    pdf_label = pdf_labels.get(language, "Original PDF")
    export_label = export_labels.get(language, "Export")

    return f"""<!DOCTYPE html>
<html lang="{_esc(language)}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="google-site-verification" content="5eTv5mgNKw8M8vENzS4KPG4aJKYm_zKZJhL3TbQpOGs">
<title>{_esc(page_title)}</title>
<meta name="description" content="{_esc(meta_desc)}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{_esc(canonical)}">
<meta property="og:type" content="article">
<meta property="og:title" content="{_esc(docket)}, {_esc(court_name)}">
<meta property="og:description" content="{_esc(meta_desc)}">
<meta property="og:url" content="{_esc(canonical)}">
<meta property="og:site_name" content="OpenCaseLaw.ch">
<meta property="og:locale" content="{_esc(language)}_CH">
<meta property="og:image" content="https://opencaselaw.ch/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="OpenCaseLaw — Swiss legal research">
<meta property="article:published_time" content="{_esc(date)}T00:00:00Z">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_esc(docket)}, {_esc(court_name)}">
<meta name="twitter:description" content="{_esc(meta_desc)}">
<meta name="twitter:image" content="https://opencaselaw.ch/og-image.png">
<meta name="twitter:image:alt" content="OpenCaseLaw — Swiss legal research">
<script type="application/ld+json">{schema_str}</script>
<link rel="stylesheet" href="https://opencaselaw.ch/static/css/design-system.css?v=21">
<link rel="icon" type="image/svg+xml" href="https://opencaselaw.ch/favicon.svg">
<style>
    /* /entscheid/{{id}} v2 — site chrome wears the shared design system,
       the decision body keeps Times serif for legal authenticity. The
       Times scope is bound to .decision-body so every reference, citation
       link, nav and footer stays in IBM Plex (consistent with /search,
       /courts, /laws etc.). */

    main.decision {{
        max-width: var(--max-w-prose);
        padding-top: var(--s-6);
        padding-bottom: var(--s-7);
    }}

    .breadcrumb {{
        font-size: var(--t-sm);
        color: var(--text-3);
        margin-bottom: var(--s-5);
    }}
    .breadcrumb a {{ color: var(--text-2); text-decoration: none; }}
    .breadcrumb a:hover {{ color: var(--text); text-decoration: underline; }}
    .breadcrumb .sep {{ margin: 0 var(--s-2); color: var(--text-3); }}

    .decision-head {{
        margin: 0 0 var(--s-6);
        padding-bottom: var(--s-5);
        border-bottom: 1px solid var(--border);
    }}
    .decision-docket {{
        font-family: var(--f-mono);
        font-size: clamp(var(--t-xl), 4vw, var(--t-2xl));
        font-weight: 500;
        letter-spacing: -0.01em;
        line-height: 1.15;
        margin: 0 0 var(--s-2);
        color: var(--text);
    }}
    .decision-subtitle {{
        font-size: var(--t-base);
        color: var(--text-2);
        line-height: 1.45;
        margin: 0 0 var(--s-4);
        max-width: 60ch;
    }}
    .decision-meta {{
        font-size: var(--t-sm);
        color: var(--text-2);
        margin: 0 0 var(--s-4);
        display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s-3);
    }}
    .decision-meta strong {{ color: var(--text); font-weight: 500; }}
    .decision-meta .sep {{ color: var(--text-3); }}
    .decision-meta .canton {{
        font-family: var(--f-mono); font-size: var(--t-xs);
        padding: 1px 8px; border-radius: var(--radius-pill);
        background: var(--bg-soft); color: var(--text-2);
    }}
    .decision-actions {{
        display: flex; flex-wrap: wrap; gap: var(--s-3);
        align-items: baseline; font-size: var(--t-sm);
        margin-top: var(--s-3);
    }}
    .decision-actions a {{
        color: var(--text); text-decoration: none;
        padding-bottom: 1px;
        border-bottom: 1px dotted var(--text-3);
    }}
    .decision-actions a:hover {{ border-color: var(--text); }}
    .decision-actions .label {{
        color: var(--text-3); font-size: var(--t-xs);
        text-transform: uppercase; letter-spacing: 0.06em;
        font-weight: 500;
    }}
    .decision-actions .group {{
        display: inline-flex; gap: var(--s-3); align-items: baseline;
    }}
    .decision-actions .vsep {{
        width: 1px; height: 14px; background: var(--border);
        align-self: center;
    }}

    /* ── DECISION BODY — Times serif scope only ───────────────────── */
    .decision-body {{
        font-family: var(--f-serif);
        font-size: 16px;
        line-height: 1.6;
        color: var(--text);
    }}
    .decision-body h2 {{
        font-family: var(--f-sans);
        font-size: var(--t-lg);
        font-weight: 500;
        color: var(--text);
        margin: var(--s-6) 0 var(--s-3);
        line-height: 1.2;
    }}
    .decision-body details {{ margin: var(--s-5) 0; }}
    .decision-body details summary {{
        cursor: pointer;
        font-family: var(--f-sans);
        font-weight: 500;
        font-size: var(--t-base);
        color: var(--text);
        padding: var(--s-2) 0;
        border-bottom: 1px solid var(--border);
        list-style: none;
    }}
    .decision-body details summary::-webkit-details-marker {{ display: none; }}
    .decision-body details summary::after {{
        content: '+'; float: right;
        font-family: var(--f-mono); color: var(--text-2);
    }}
    .decision-body details[open] summary::after {{ content: '−'; }}
    .decision-body details summary .count {{
        font-family: var(--f-mono); font-weight: 400;
        font-size: var(--t-xs); color: var(--text-3);
        margin-left: var(--s-2);
    }}
    .decision-body .regeste {{ margin: 0 0 var(--s-5); }}
    .decision-body .regeste p {{ margin: 0 0 var(--s-3); }}

    .decision-body .section-body {{ margin: var(--s-3) 0 0; }}
    .decision-body .section-body p,
    .decision-body .erw p,
    .decision-body .fulltext p {{
        margin: 0 0 var(--s-3);
        line-height: 1.6;
        text-align: justify;
        hyphens: auto;
    }}
    .decision-body .section-body p:last-child,
    .decision-body .erw p:last-child,
    .decision-body .fulltext p:last-child {{ margin-bottom: 0; }}
    .decision-body .erwaegungen {{ margin: var(--s-3) 0 0; }}
    .decision-body .erw {{ padding: var(--s-3) 0; border-bottom: 1px dashed var(--border); }}
    .decision-body .erw:last-child {{ border-bottom: none; }}
    .decision-body .erw .e-num {{
        font-family: var(--f-mono);
        font-weight: 500;
        margin-right: var(--s-2);
        color: var(--accent);
        text-decoration: none;
    }}
    .decision-body .erw .e-num:hover {{ text-decoration: underline; }}

    /* Highlight from ?highlight=&e=… (find_relevant_erwaegung handoff). */
    .decision-body .erw-focus {{
        background: color-mix(in srgb, var(--accent) 5%, transparent);
        border-left: 3px solid var(--accent);
        padding-left: var(--s-3);
        margin-left: 0 !important;
        scroll-margin-top: 80px;
    }}
    .decision-body .erw-focus .e-num {{ color: var(--accent); font-weight: 600; }}
    .decision-body .erw mark {{
        background: color-mix(in srgb, var(--accent) 22%, transparent);
        color: var(--text);
        padding: 1px 2px;
        border-radius: 2px;
        font-weight: 500;
    }}
    .decision-body .match-banner {{
        margin: var(--s-4) 0 var(--s-5);
        padding: var(--s-3) var(--s-4);
        background: color-mix(in srgb, var(--accent) 8%, transparent);
        border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
        border-radius: 8px;
        font-family: var(--f-sans);
        font-size: 14px;
        color: var(--text);
        display: flex;
        align-items: center;
        gap: var(--s-2);
    }}
    .decision-body .match-banner-icon {{
        color: var(--accent);
        font-size: 16px;
        flex-shrink: 0;
    }}
    .decision-body .match-banner a {{
        color: var(--accent);
        text-decoration: none;
        font-weight: 500;
    }}
    .decision-body .match-banner a:hover {{ text-decoration: underline; }}
    .decision-body .dispositiv-orders {{
        margin: var(--s-3) 0 0 var(--s-5);
        padding: 0; list-style: decimal;
    }}
    .decision-body .dispositiv-orders li {{ margin: var(--s-3) 0; padding-left: 0.4em; }}
    .decision-body .fulltext {{ font-size: 15px; line-height: 1.6; margin: var(--s-3) 0 0; }}

    /* ── Citation context — back to Plex sans ──────────────────────── */
    .citations {{
        margin-top: var(--s-7);
        padding-top: var(--s-5);
        border-top: 1px solid var(--border);
        font-family: var(--f-sans);
    }}
    .citations h2 {{
        font-size: var(--t-lg); font-weight: 500;
        color: var(--text); margin: 0 0 var(--s-4);
    }}
    .citations-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: var(--s-5);
    }}
    .cit-col h3 {{
        font-size: var(--t-xs); font-weight: 500;
        text-transform: uppercase; letter-spacing: 0.06em;
        color: var(--text-3); margin: 0 0 var(--s-2);
        padding-bottom: var(--s-2);
        border-bottom: 1px solid var(--border);
    }}
    .cit-col h3 .count {{ color: var(--text-3); font-weight: 400; }}
    .cit-col ul {{
        list-style: none; padding: 0; margin: 0;
        font-size: var(--t-sm); line-height: 1.7;
    }}
    .cit-col li {{ margin: 0; padding: 2px 0; }}
    .cit-col a {{
        color: var(--text); text-decoration: none;
        border-bottom: 1px dotted transparent;
    }}
    .cit-col a:hover {{ border-color: var(--text-2); }}
    .cit-col .muted {{ color: var(--text-3); font-family: var(--f-mono); font-size: var(--t-xs); }}

    /* ── Mobile ────────────────────────────────────────────────────── */
    @media (max-width: 720px) {{
        main.decision {{ padding-top: var(--s-4); }}
        .decision-actions {{ flex-direction: column; align-items: flex-start; gap: var(--s-2); }}
        .decision-actions .vsep {{ display: none; }}
    }}

    /* ── Print: drop the chrome, keep the law ──────────────────────── */
    @media print {{
        header.site, footer.site, .breadcrumb, .decision-actions,
        .citations, details > summary {{ display: none !important; }}
        main.decision {{ max-width: none; padding: 0; }}
        details, details[open] {{ display: block; }}
        details > *:not(summary) {{ display: block !important; }}
        .decision-body {{ font-size: 11pt; line-height: 1.5; }}
        .decision-body .erw, .decision-body p {{ page-break-inside: avoid; }}
    }}
</style>
</head>
<body>

<a class="skip-link" href="#main">Skip to content</a>

<header class="site">
  <div class="container row">
    <a class="brand" href="https://opencaselaw.ch/">
      <span class="logo">+</span> <span class="wordmark">opencaselaw.ch</span>
    </a>
    <nav aria-label="Primary">
      <a href="https://opencaselaw.ch/search/" data-nav-key="search">Search</a>
      <a href="https://opencaselaw.ch/courts/" data-nav-key="courts">Courts</a>
      <a href="https://opencaselaw.ch/laws/" data-nav-key="laws">Laws</a>
      <a href="https://opencaselaw.ch/word/" data-nav-key="word">Word</a>
      <a href="https://opencaselaw.ch/mcp/" data-nav-key="mcp">MCP</a>
      <a href="https://opencaselaw.ch/api/" data-nav-key="api">API</a>
      <a href="https://opencaselaw.ch/quality.html" data-nav-key="quality">Quality</a>
      <span class="lang-sw" role="group" aria-label="Language">
        <a href="https://opencaselaw.ch/?lang=de" data-l="de">DE</a>
        <a href="https://opencaselaw.ch/?lang=fr" data-l="fr">FR</a>
        <a href="https://opencaselaw.ch/?lang=it" data-l="it">IT</a>
        <a href="https://opencaselaw.ch/?lang=rm" data-l="rm">RM</a>
        <a href="https://opencaselaw.ch/?lang=en" data-l="en">EN</a>
      </span>
    </nav>
    <button class="nav-toggle" id="nav-toggle" aria-label="Menu" aria-expanded="false" aria-controls="primary-nav">
      <svg class="icon-menu" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
      <svg class="icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M6 18L18 6"/></svg>
    </button>
  </div>
</header>

<main class="container decision" id="main">
  <p class="breadcrumb">
    <a href="https://opencaselaw.ch/">{_esc(home_label)}</a>
    <span class="sep">/</span>
    <a href="https://opencaselaw.ch/courts/">{_esc(court_name)}</a>
    <span class="sep">/</span>
    <span>{_esc(docket)}</span>
  </p>

  <header class="decision-head">
    <h1 class="decision-docket">{_esc(docket)}</h1>
    {f'<p class="decision-subtitle">{_esc(title)}</p>' if title else ''}
    <div class="decision-meta">
      <span><strong>{_esc(court_name)}</strong></span>
      <span class="sep">·</span>
      <span>{_esc(date)}</span>
      <span class="sep">·</span>
      <span>{_esc(lang_label)}</span>
      {f'<span class="canton">{_esc(canton)}</span>' if canton else ''}
    </div>
    <div class="decision-actions">
      <span class="group">
        <span class="label">{_esc(source_label)}</span>
        {f'<a href="{_esc(source_url)}" rel="noopener" target="_blank">Original</a>' if source_url else ''}
        {f'<a href="{_esc(pdf_url)}" rel="noopener" target="_blank">{_esc(pdf_label)}</a>' if pdf_url else ''}
      </span>
      <span class="vsep"></span>
      <span class="group">
        <span class="label">{_esc(export_label)}</span>
        <a href="/api/decisions/{_esc(did)}/export.docx" rel="nofollow">Word</a>
        <a href="/api/decisions/{_esc(did)}/export.pdf" rel="nofollow">PDF</a>
        <a href="/api/decisions/{_esc(did)}/export.bib" rel="nofollow">BibTeX</a>
        <a href="/api/decisions/{_esc(did)}/export.ris" rel="nofollow">RIS</a>
      </span>
    </div>
  </header>

  <article class="decision-body">
    {f'<div class="regeste">{regeste_html}</div>' if regeste_html else ''}
    {structured_html}
    {text_excerpt}
  </article>

  {citations_html}
</main>

<footer class="site">
  <div class="container row">
    <div>
      <a class="brand" href="https://opencaselaw.ch/"><span class="logo">+</span> <span class="wordmark">opencaselaw.ch</span></a>
      <p style="margin-top: var(--s-3); max-width: 32ch; font-size: var(--t-sm); color: var(--text-2);">
        Open Swiss case law. Dataset packaging CC0 · Code MIT. Public-good infrastructure.
      </p>
    </div>
    <div>
      <h4 data-i18n="footer.data">Data</h4>
      <ul>
        <li><a href="https://opencaselaw.ch/entscheide/">Decisions</a></li>
        <li><a href="https://opencaselaw.ch/laws/">Laws</a></li>
        <li><a href="https://opencaselaw.ch/quality.html">Quality</a></li>
        <li><a href="https://opencaselaw.ch/coverage/">Coverage</a></li>
      </ul>
    </div>
    <div>
      <h4 data-i18n="footer.access">Access</h4>
      <ul>
        <li><a href="https://opencaselaw.ch/word/">Word add-in</a></li>
        <li><a href="https://opencaselaw.ch/mcp/">MCP server</a></li>
        <li><a href="https://huggingface.co/datasets/voilaj/swiss-caselaw" rel="noopener">HuggingFace</a></li>
        <li><a href="https://github.com/jonashertner/opencaselaw" rel="noopener">GitHub</a></li>
      </ul>
    </div>
    <div>
      <h4 data-i18n="footer.about">About</h4>
      <ul>
        <li><a href="https://opencaselaw.ch/ueber/">Mission</a></li>
        <li><a href="https://opencaselaw.ch/paper/">Research</a></li>
        <li><a href="https://opencaselaw.ch/datenschutz/">Privacy</a></li>
        <li><a href="https://opencaselaw.ch/governance-and-removal-policy.md">Governance</a></li>
      </ul>
    </div>
  </div>
  <div class="container" style="margin-top: var(--s-5); font-size: var(--t-xs); color: var(--text-3);">
    © 2026 Jonas Hertner / OpenCaseLaw · Corpus CC0 · Code MIT
  </div>
</footer>

<script>
(function () {{
  /* Canonical lookup by data-nav-key — fixes RM-stickiness where
     textContent='Tschertga' could not be looked up after first click. */
  var NAV_I18N = {{
    search:  {{ de: 'Suche',     fr: 'Recherche', it: 'Ricerca',  rm: 'Tschertga', en: 'Search' }},
    courts:  {{ de: 'Gerichte',  fr: 'Tribunaux', it: 'Tribunali', rm: 'Dretgiras', en: 'Courts' }},
    laws:    {{ de: 'Gesetze',   fr: 'Lois',      it: 'Leggi',     rm: 'Leschas',   en: 'Laws' }},
    word:    {{ de: 'Word',      fr: 'Word',      it: 'Word',      rm: 'Word',      en: 'Word' }},
    mcp:     {{ de: 'MCP',       fr: 'MCP',       it: 'MCP',       rm: 'MCP',       en: 'MCP' }},
    api:     {{ de: 'API',       fr: 'API',       it: 'API',       rm: 'API',       en: 'API' }},
    quality: {{ de: 'Qualitaet', fr: 'Qualite',   it: 'Qualita',   rm: 'Qualitad',  en: 'Quality' }}
  }};
  var I18N_FOOTER = {{
    'footer.product':  {{ de: 'Produkt',     fr: 'Produit',     it: 'Prodotto',        rm: 'Product',     en: 'Product' }},
    'footer.access':   {{ de: 'Zugang',      fr: 'Accès',       it: 'Accesso',         rm: 'Access',      en: 'Access' }},
    'footer.data':     {{ de: 'Daten',       fr: 'Donnees',     it: 'Dati',            rm: 'Datas',       en: 'Data' }},
    'footer.about':    {{ de: 'Ueber',       fr: 'A propos',    it: 'Chi siamo',       rm: 'Davart',      en: 'About' }},
    'footer.search':   {{ de: 'Suche',       fr: 'Recherche',   it: 'Ricerca',         rm: 'Tschertga',   en: 'Search' }},
    'footer.courts':   {{ de: 'Gerichte',    fr: 'Tribunaux',   it: 'Tribunali',       rm: 'Dretgiras',   en: 'Courts' }},
    'footer.laws':     {{ de: 'Gesetze',     fr: 'Lois',        it: 'Leggi',           rm: 'Leschas',     en: 'Laws' }},
    'footer.word':     {{ de: 'Word-Add-in', fr: 'Word add-in', it: 'Componente Word', rm: 'Add-in Word', en: 'Word add-in' }},
    'footer.mcp':      {{ de: 'MCP',         fr: 'MCP',         it: 'MCP',             rm: 'MCP',         en: 'MCP' }},
    'footer.api':      {{ de: 'API',         fr: 'API',         it: 'API',             rm: 'API',         en: 'API' }},
    'footer.paper':    {{ de: 'Paper',       fr: 'Article',     it: 'Articolo',        rm: 'Artitgel',    en: 'Paper' }},
    'footer.quality':  {{ de: 'Qualitaet',   fr: 'Qualite',     it: 'Qualita',         rm: 'Qualitad',    en: 'Quality' }},
    'footer.coverage': {{ de: 'Abdeckung',   fr: 'Couverture',  it: 'Copertura',       rm: 'Cuvrida',     en: 'Coverage' }}
  }};
  function applyNavLang(lang) {{
    document.querySelectorAll('header.site nav > a[data-nav-key]').forEach(function (a) {{
      var dict = NAV_I18N[a.dataset.navKey];
      if (dict && dict[lang]) a.textContent = dict[lang];
    }});
    document.querySelectorAll('[data-i18n]').forEach(function (el) {{
      var key = el.getAttribute('data-i18n');
      var dict = (window.I18N_PAGE && window.I18N_PAGE[key]) || I18N_FOOTER[key];
      if (dict && dict[lang]) el.textContent = dict[lang];
    }});
    document.documentElement.setAttribute('lang', lang);
  }}
  var qs = (location.search.match(/[?&]lang=([a-z]{{2}})/) || [])[1];
  var stored = null;
  try {{ stored = localStorage.getItem('lang'); }} catch (_) {{}}
  var html = (document.documentElement.lang || '').slice(0, 2);
  var lang = qs || stored || html || 'de';
  if (qs) {{ try {{ localStorage.setItem('lang', qs); }} catch (_) {{}} }}
  applyNavLang(lang);
  document.querySelectorAll('.lang-sw [data-l]').forEach(function (el) {{
    el.classList.toggle('on', el.dataset.l === lang);
    el.addEventListener('click', function (e) {{
      e.preventDefault();
      var l = el.dataset.l;
      try {{ localStorage.setItem('lang', l); }} catch (_) {{}}
      document.querySelectorAll('.lang-sw [data-l]').forEach(function (b) {{
        b.classList.toggle('on', b.dataset.l === l);
      }});
      applyNavLang(l);
    }});
  }});
}})();
</script>
<script>
(function () {{
  var btn = document.getElementById('nav-toggle');
  if (!btn) return;
  var nav = document.querySelector('header.site nav');
  function setOpen(open) {{
    document.body.classList.toggle('nav-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }}
  btn.addEventListener('click', function () {{ setOpen(!document.body.classList.contains('nav-open')); }});
  if (nav) nav.querySelectorAll('a').forEach(function (a) {{ a.addEventListener('click', function () {{ setOpen(false); }}); }});
  document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape') setOpen(false); }});
}})();
</script>
</body>
</html>"""


def _render_404(decision_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Entscheid nicht gefunden | OpenCaseLaw</title>
<meta name="robots" content="noindex">
<meta name="google-site-verification" content="5eTv5mgNKw8M8vENzS4KPG4aJKYm_zKZJhL3TbQpOGs">
<link rel="stylesheet" href="https://opencaselaw.ch/static/css/design-system.css?v=21">
<link rel="icon" type="image/svg+xml" href="https://opencaselaw.ch/favicon.svg">
<style>
  main.notfound {{ max-width: 560px; padding-top: var(--s-7); padding-bottom: var(--s-7); }}
  main.notfound h1 {{ margin: 0 0 var(--s-3); }}
  main.notfound p {{ margin: 0 0 var(--s-4); color: var(--text-2); line-height: 1.6; }}
  main.notfound code {{ font-family: var(--f-mono); font-size: var(--t-sm); background: var(--bg-soft); padding: 2px 8px; border-radius: var(--radius-sm); color: var(--text); word-break: break-all; }}
</style>
</head>
<body>

<a class="skip-link" href="#main">Skip to content</a>

<header class="site">
  <div class="container row">
    <a class="brand" href="https://opencaselaw.ch/">
      <span class="logo">+</span> <span class="wordmark">opencaselaw.ch</span>
    </a>
    <nav aria-label="Primary">
      <a href="https://opencaselaw.ch/search/" data-nav-key="search">Search</a>
      <a href="https://opencaselaw.ch/courts/" data-nav-key="courts">Courts</a>
      <a href="https://opencaselaw.ch/laws/" data-nav-key="laws">Laws</a>
      <a href="https://opencaselaw.ch/word/" data-nav-key="word">Word</a>
      <a href="https://opencaselaw.ch/mcp/" data-nav-key="mcp">MCP</a>
      <a href="https://opencaselaw.ch/api/" data-nav-key="api">API</a>
      <a href="https://opencaselaw.ch/quality.html" data-nav-key="quality">Quality</a>
      <span class="lang-sw" role="group" aria-label="Language">
        <a href="https://opencaselaw.ch/?lang=de" data-l="de">DE</a>
        <a href="https://opencaselaw.ch/?lang=fr" data-l="fr">FR</a>
        <a href="https://opencaselaw.ch/?lang=it" data-l="it">IT</a>
        <a href="https://opencaselaw.ch/?lang=rm" data-l="rm">RM</a>
        <a href="https://opencaselaw.ch/?lang=en" data-l="en">EN</a>
      </span>
    </nav>
    <button class="nav-toggle" id="nav-toggle" aria-label="Menu" aria-expanded="false" aria-controls="primary-nav">
      <svg class="icon-menu" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
      <svg class="icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M6 18L18 6"/></svg>
    </button>
  </div>
</header>

<main class="container notfound">
  <p class="eyebrow">404</p>
  <h1>Entscheid nicht gefunden</h1>
  <p>Der Entscheid <code>{_esc(decision_id)}</code> wurde nicht gefunden. Vielleicht ist die ID veraltet oder enthält einen Tippfehler.</p>
  <p>
    <a href="https://opencaselaw.ch/search/" class="btn btn-primary">Suche &rarr;</a>
    <a href="https://opencaselaw.ch/courts/" class="btn" style="margin-left: var(--s-2);">Gerichte durchstöbern</a>
  </p>
</main>

<script>
(function () {{
  /* Canonical lookup by data-nav-key — fixes RM-stickiness where
     textContent='Tschertga' could not be looked up after first click. */
  var NAV_I18N = {{
    search:  {{ de: 'Suche',     fr: 'Recherche', it: 'Ricerca',  rm: 'Tschertga', en: 'Search' }},
    courts:  {{ de: 'Gerichte',  fr: 'Tribunaux', it: 'Tribunali', rm: 'Dretgiras', en: 'Courts' }},
    laws:    {{ de: 'Gesetze',   fr: 'Lois',      it: 'Leggi',     rm: 'Leschas',   en: 'Laws' }},
    word:    {{ de: 'Word',      fr: 'Word',      it: 'Word',      rm: 'Word',      en: 'Word' }},
    mcp:     {{ de: 'MCP',       fr: 'MCP',       it: 'MCP',       rm: 'MCP',       en: 'MCP' }},
    api:     {{ de: 'API',       fr: 'API',       it: 'API',       rm: 'API',       en: 'API' }},
    quality: {{ de: 'Qualitaet', fr: 'Qualite',   it: 'Qualita',   rm: 'Qualitad',  en: 'Quality' }}
  }};
  var I18N_FOOTER = {{
    'footer.product':  {{ de: 'Produkt',     fr: 'Produit',     it: 'Prodotto',        rm: 'Product',     en: 'Product' }},
    'footer.access':   {{ de: 'Zugang',      fr: 'Accès',       it: 'Accesso',         rm: 'Access',      en: 'Access' }},
    'footer.data':     {{ de: 'Daten',       fr: 'Donnees',     it: 'Dati',            rm: 'Datas',       en: 'Data' }},
    'footer.about':    {{ de: 'Ueber',       fr: 'A propos',    it: 'Chi siamo',       rm: 'Davart',      en: 'About' }},
    'footer.search':   {{ de: 'Suche',       fr: 'Recherche',   it: 'Ricerca',         rm: 'Tschertga',   en: 'Search' }},
    'footer.courts':   {{ de: 'Gerichte',    fr: 'Tribunaux',   it: 'Tribunali',       rm: 'Dretgiras',   en: 'Courts' }},
    'footer.laws':     {{ de: 'Gesetze',     fr: 'Lois',        it: 'Leggi',           rm: 'Leschas',     en: 'Laws' }},
    'footer.word':     {{ de: 'Word-Add-in', fr: 'Word add-in', it: 'Componente Word', rm: 'Add-in Word', en: 'Word add-in' }},
    'footer.mcp':      {{ de: 'MCP',         fr: 'MCP',         it: 'MCP',             rm: 'MCP',         en: 'MCP' }},
    'footer.api':      {{ de: 'API',         fr: 'API',         it: 'API',             rm: 'API',         en: 'API' }},
    'footer.paper':    {{ de: 'Paper',       fr: 'Article',     it: 'Articolo',        rm: 'Artitgel',    en: 'Paper' }},
    'footer.quality':  {{ de: 'Qualitaet',   fr: 'Qualite',     it: 'Qualita',         rm: 'Qualitad',    en: 'Quality' }},
    'footer.coverage': {{ de: 'Abdeckung',   fr: 'Couverture',  it: 'Copertura',       rm: 'Cuvrida',     en: 'Coverage' }}
  }};
  function applyNavLang(lang) {{
    document.querySelectorAll('header.site nav > a[data-nav-key]').forEach(function (a) {{
      var dict = NAV_I18N[a.dataset.navKey];
      if (dict && dict[lang]) a.textContent = dict[lang];
    }});
    document.querySelectorAll('[data-i18n]').forEach(function (el) {{
      var key = el.getAttribute('data-i18n');
      var dict = (window.I18N_PAGE && window.I18N_PAGE[key]) || I18N_FOOTER[key];
      if (dict && dict[lang]) el.textContent = dict[lang];
    }});
    document.documentElement.setAttribute('lang', lang);
  }}
  var qs = (location.search.match(/[?&]lang=([a-z]{{2}})/) || [])[1];
  var stored = null;
  try {{ stored = localStorage.getItem('lang'); }} catch (_) {{}}
  var html = (document.documentElement.lang || '').slice(0, 2);
  var lang = qs || stored || html || 'de';
  if (qs) {{ try {{ localStorage.setItem('lang', qs); }} catch (_) {{}} }}
  applyNavLang(lang);
  document.querySelectorAll('.lang-sw [data-l]').forEach(function (el) {{
    el.classList.toggle('on', el.dataset.l === lang);
    el.addEventListener('click', function (e) {{
      e.preventDefault();
      var l = el.dataset.l;
      try {{ localStorage.setItem('lang', l); }} catch (_) {{}}
      document.querySelectorAll('.lang-sw [data-l]').forEach(function (b) {{
        b.classList.toggle('on', b.dataset.l === l);
      }});
      applyNavLang(l);
    }});
  }});
}})();
</script>
<script>
(function () {{
  var btn = document.getElementById('nav-toggle');
  if (!btn) return;
  var nav = document.querySelector('header.site nav');
  function setOpen(open) {{
    document.body.classList.toggle('nav-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }}
  btn.addEventListener('click', function () {{ setOpen(!document.body.classList.contains('nav-open')); }});
  if (nav) nav.querySelectorAll('a').forEach(function (a) {{ a.addEventListener('click', function () {{ setOpen(false); }}); }});
  document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape') setOpen(false); }});
}})();
</script>
</body>
</html>"""


MAX_URLS_PER_SITEMAP = 40_000  # Google limit is 50K; stay under


def render_sitemap_index() -> str:
    """Generate sitemap index pointing to per-court chunk sitemaps."""
    conn = _get_db()
    try:
        courts = conn.execute(
            "SELECT court, COUNT(*) as n FROM decisions "
            "WHERE court IS NOT NULL GROUP BY court ORDER BY court"
        ).fetchall()
    finally:
        conn.close()

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for court, count in courts:
        chunks = (count // MAX_URLS_PER_SITEMAP) + 1
        for chunk in range(chunks):
            if chunks == 1:
                lines.append(f"  <sitemap><loc>{BASE_URL}/sitemap-{court}.xml</loc></sitemap>")
            else:
                lines.append(f"  <sitemap><loc>{BASE_URL}/sitemap-{court}-{chunk}.xml</loc></sitemap>")
    lines.append("</sitemapindex>")
    return "\n".join(lines)


def render_court_sitemap(court_and_chunk: str) -> str:
    """Generate sitemap for a single court's decisions (with optional chunk).

    court_and_chunk can be "bger" or "bger-0", "bger-1", etc.
    """
    # Parse court and chunk from path
    parts = court_and_chunk.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        court = parts[0]
        chunk = int(parts[1])
    else:
        court = court_and_chunk
        chunk = 0

    offset = chunk * MAX_URLS_PER_SITEMAP

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT decision_id, decision_date FROM decisions "
            "WHERE court = ? ORDER BY decision_date DESC "
            "LIMIT ? OFFSET ?",
            (court, MAX_URLS_PER_SITEMAP, offset),
        ).fetchall()
    finally:
        conn.close()

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for row in rows:
        did = row[0]
        lines.append(f"  <url><loc>{BASE_URL}/entscheid/{_esc(did)}</loc></url>")
    lines.append("</urlset>")
    return "\n".join(lines)
