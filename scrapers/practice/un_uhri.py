"""
UN human-rights recommendations to Switzerland (Universal Human Rights Index)
=============================================================================

  https://uhri.ohchr.org/en/our-data-api
  https://uhri.ohchr.org/api/uhri/export-results/export-full-{en,fr}.json

OHCHR's Universal Human Rights Index annotates, paragraph by paragraph, every
country-specific recommendation and concern of the UN human-rights
mechanisms: the treaty bodies' concluding observations (CAT, CCPR, CESCR,
CERD, CEDAW, CRC, CRPD, CED, CMW), the Subcommittee on Prevention of
Torture's visit reports, the Universal Periodic Review (with the State's
position: supported / noted) and the special procedures' country-visit
reports. OHCHR offers the dataset "freely and without any restriction" and
publishes it as one daily JSON export per language (the "Our Data" page);
it is the only UN human-rights source whose terms allow bulk reuse —
juris.ohchr.org and tbinternet.ohchr.org disallow every robot, and the UN
Digital Library forbids automated downloads without the Library's approval.

Scope: Switzerland only — 27 documents, ~1,870 paragraphs (2026-10-23).
The export is global (278k paragraphs, ~400 MB per language), so the HTTP
response is parsed as it arrives and filtered — nothing is loaded whole or
written to disk.

One row per (paragraph, language):
  * en — every paragraph;
  * fr — where the French export carries a translation. For documents UHRI
    has not translated, the French export repeats the English text; those
    copies are dropped rather than labelled French.

UHRI's paragraphs from before ~2012 are its own digests, not the UN text
("w) the ratification of …", "c) …", "r) …", "> …" — welcomes / concern /
recommendation markers). They are kept, because they are the only
paragraph-level index of those reports, but tagged as summaries in the title
and topics so nobody quotes them as the committee's words.

doc_id is the UHRI annotation id (shared by both language exports), so a
corrected text re-appends under the same id (REVISION_FIELD="content_hash")
and build_practice_db upserts it.
"""
from __future__ import annotations

import html
import io
import json
import logging
import re
from typing import IO, Iterable, Iterator

from .base import PracticeScraper, sha256_hex

logger = logging.getLogger(__name__)

EXPORT_URL = "https://uhri.ohchr.org/api/uhri/export-results/export-full-{lang}.json"
COUNTRY = "Switzerland"          # as spelled in the English export
LANGUAGES = ("en", "fr")
# The full export held 278,244 paragraphs on 2026-10-23. Far fewer is a
# truncated or substituted file, not a dataset.
MIN_EXPORT_RECORDS = 100_000

# UHRI "Body" label (minus its "- " prefix) -> (authority, name en, name fr).
# The French export spells a few labels differently (EPU for UPR).
_TREATY_BODIES = {
    "CAT":   ("CAT",   "Committee against Torture", "Comité contre la torture"),
    "CCPR":  ("CCPR",  "Human Rights Committee", "Comité des droits de l'homme"),
    "CESCR": ("CESCR", "Committee on Economic, Social and Cultural Rights",
              "Comité des droits économiques, sociaux et culturels"),
    "CERD":  ("CERD",  "Committee on the Elimination of Racial Discrimination",
              "Comité pour l'élimination de la discrimination raciale"),
    "CEDAW": ("CEDAW", "Committee on the Elimination of Discrimination against Women",
              "Comité pour l'élimination de la discrimination à l'égard des femmes"),
    "CRC":   ("CRC",   "Committee on the Rights of the Child", "Comité des droits de l'enfant"),
    "CRC-OP-SC": ("CRC", "Committee on the Rights of the Child (Optional Protocol on the sale of children)",
                  "Comité des droits de l'enfant (Protocole facultatif concernant la vente d'enfants)"),
    "CRC-OP-AC": ("CRC", "Committee on the Rights of the Child (Optional Protocol on children in armed conflict)",
                  "Comité des droits de l'enfant (Protocole facultatif concernant les enfants dans les conflits armés)"),
    "CRPD":  ("CRPD",  "Committee on the Rights of Persons with Disabilities",
              "Comité des droits des personnes handicapées"),
    "CED":   ("CED",   "Committee on Enforced Disappearances", "Comité des disparitions forcées"),
    "CMW":   ("CMW",   "Committee on Migrant Workers", "Comité des travailleurs migrants"),
    "SPT":   ("SPT",   "Subcommittee on Prevention of Torture", "Sous-comité pour la prévention de la torture"),
    "UPR":   ("UPR",   "Universal Periodic Review", "Examen périodique universel"),
    "EPU":   ("UPR",   "Universal Periodic Review", "Examen périodique universel"),
}
# Anything else is a special-procedure mandate ("SR Racism", "WG Mercenaries",
# "IE Foreign debt"); its own label names it.
_SPECIAL_PROCEDURES = "SPECIAL PROCEDURES"

_POSITION = {
    "en": {"Supported": "supported", "Noted": "noted", "Supported/Noted": "partly supported"},
    "fr": {"Accepté": "acceptée", "Noté": "notée", "Accepté/Noté": "partiellement acceptée"},
}

# UHRI digest markers: w) welcomes, c) concern, r) recommendation, n) note,
# and "> " bullets on the oldest special-procedure entries.
_DIGEST = re.compile(r"^\s*(?:[a-z]\)\s|>\s)")
# Paragraph number at the start of a line: "6.The Committee", "8.\tThe",
# "39.1 Intensify", "123.65. Ensure", "39.1Redoubler" (UPR recommendations).
_UPR_NUM = re.compile(r"(?m)^\s*(\d{1,3}\.\d{1,3})\.?\s*(?=[^\d\s.])")
_PARA_NUM = re.compile(r"(?m)^\s*(\d{1,3})\.(?!\d)\s*(?=\S)")
# Function words for the one sanity check on the exports' language labels:
# a few SPT paragraphs sit in the English export in French (the report's
# original language).
_FUNCTION_WORDS = {
    "en": {"the", "of", "and", "to", "in", "that", "is", "for", "with", "on", "by", "its", "be", "are",
           "has", "have", "was", "were", "which", "this", "an"},
    "fr": {"le", "la", "les", "de", "des", "du", "et", "est", "que", "qui", "dans", "pour", "sur",
           "une", "un", "par", "aux", "au", "à", "ont", "ne", "pas", "ce", "cette", "été", "son", "ses"},
}
_HEADING = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.S | re.I)
_BREAK = re.compile(r"<br\s*/?>|</p>|</h[1-6]>|</li>|</div>", re.I)
_TAG = re.compile(r"<[^>]+>")


# ───────────────────────────────────────────── streaming JSON

def iter_json_array(fp: IO[str], chunk_size: int = 1 << 20) -> Iterator[dict]:
    """Yield the objects of a top-level JSON array without loading it whole."""
    decoder = json.JSONDecoder()
    buf, pos = "", 0
    eof = False

    def more() -> bool:
        nonlocal buf, pos, eof
        data = fp.read(chunk_size)
        if not data:
            eof = True
            return False
        buf, pos = buf[pos:] + data, 0
        return True

    while True:                                   # the opening bracket
        while pos < len(buf) and buf[pos].isspace():
            pos += 1
        if pos < len(buf):
            break
        if not more():
            return
    if buf[pos] != "[":
        raise ValueError(f"not a JSON array (starts with {buf[pos:pos + 40]!r})")
    pos += 1
    while True:
        while pos < len(buf) and (buf[pos].isspace() or buf[pos] == ","):
            pos += 1
        if pos >= len(buf):
            if not more():
                raise ValueError("JSON array truncated (no closing bracket)")
            continue
        if buf[pos] == "]":
            return
        try:
            obj, end = decoder.raw_decode(buf, pos)
        except json.JSONDecodeError:
            if eof or not more():
                raise ValueError("JSON array truncated mid-object")
            continue
        pos = end
        yield obj


# ───────────────────────────────────────────── record → stub

def _label(value: str | None) -> str:
    return (value or "").strip().lstrip("-").strip()


def _symbol(raw: str) -> str:
    """'A/HRC/45/15/ADD.1' -> 'A/HRC/45/15/Add.1' (the UN's own spelling)."""
    return re.sub(r"/ADD\.(\d+)", r"/Add.\1", (raw or "").strip())


def _space_label(line: str) -> str:
    """'D.Dissemination and follow-up' -> 'D. Dissemination and follow-up'
    (section labels lost their tab the same way paragraph numbers did)."""
    return re.sub(r"^([A-Z]|[IVX]{1,4})\.(?=[A-ZÀ-Ý])", r"\1. ", line)


def clean_text(raw: str) -> tuple[str, str]:
    """UHRI's HTML-ish paragraph -> (plain text, last heading)."""
    raw = raw or ""
    headings = [html.unescape(_TAG.sub("", h)).strip() for h in _HEADING.findall(raw)]
    heading = _space_label(re.sub(r"\s+", " ", headings[-1])) if headings else ""
    text = _BREAK.sub("\n", raw)
    text = html.unescape(_TAG.sub("", text))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    # "6.The Committee", "39.1Redoubler" — the source dropped the tab after
    # the number (UPR numbers first, so "39.1" is not read as paragraph 39).
    text = re.sub(r"(?m)^(\d{1,3}\.\d{1,3})(?=[^\d\s.])", r"\1 ", text)
    text = re.sub(r"(?m)^(\d{1,3})\.(?=[^\d\s])", r"\1. ", text)
    text = "\n".join(_space_label(ln) for ln in text.split("\n"))
    return text, heading


def paragraph_numbers(text: str) -> list[str]:
    """Paragraph numbers the text itself opens with: ['39.1'], ['9'], ['52', '53']."""
    upr = _UPR_NUM.findall(text)
    if upr:
        return upr[:1]
    # A run of consecutive numbers is several paragraphs (52, 53); a later
    # line that merely starts with a number is not.
    nums: list[str] = []
    for n in _PARA_NUM.findall(text):
        if nums and int(n) != int(nums[-1]) + 1:
            break
        nums.append(n)
    return nums


def _para_ref(nums: list[str], lang: str) -> str:
    if not nums:
        return ""
    one, many = ("para.", "paras.") if lang == "en" else ("par.", "par.")
    if len(nums) == 1:
        return f"{one} {nums[0]}"
    return f"{many} {nums[0]}–{nums[-1]}"


def _kind(symbol: str, body_key: str, lang: str) -> tuple[str, str]:
    """(doc_type, document description) from the symbol and body."""
    if body_key in ("UPR", "EPU"):
        return "upr_recommendation", (
            "Report of the Working Group on the Universal Periodic Review: Switzerland"
            if lang == "en" else
            "Rapport du Groupe de travail sur l'Examen périodique universel : Suisse")
    if "/CO/" in symbol:
        return "concluding_observations", (
            "Concluding observations on Switzerland" if lang == "en"
            else "Observations finales concernant la Suisse")
    if "/ROSP/" in symbol:
        return "visit_report", (
            "Report on the visit to Switzerland" if lang == "en"
            else "Rapport sur la visite en Suisse")
    return "visit_report", (
        "Report on the country visit to Switzerland" if lang == "en"
        else "Rapport sur la visite de pays en Suisse")


def in_other_language(text: str, lang: str) -> bool:
    """True when `text` is clearly not in `lang` (the other export language)."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    own = sum(w in _FUNCTION_WORDS[lang] for w in words)
    other = sum(w in _FUNCTION_WORDS["fr" if lang == "en" else "en"] for w in words)
    # Two foreign function words and none of our own already settles a
    # one-line footnote ("Certains détenus … ont passé six mois … un régime").
    return (other >= 2 and own == 0) or (other >= 3 and other > 2 * own)


def record_to_stub(rec: dict, lang: str) -> dict | None:
    symbol = _symbol(rec.get("Symbol"))
    text, heading = clean_text(rec.get("Text"))
    if not symbol or not text or in_other_language(text, lang):
        return None
    body_label = _label(rec.get("Body"))
    tb = _TREATY_BODIES.get(body_label)
    if tb:
        authority, name_en, name_fr = tb
        authority = f"UN-{authority}"
        body_name = name_en if lang == "en" else name_fr
    else:
        authority = f"UN-{_SPECIAL_PROCEDURES}"
        body_name = (f"Special procedures ({body_label})" if lang == "en"
                     else f"Procédures spéciales ({body_label})")
    doc_type, doc_desc = _kind(symbol, body_label, lang)
    digest = bool(_DIGEST.match(text))
    nums = [] if digest else paragraph_numbers(text)
    ref = _para_ref(nums, lang)
    doc_number = f"{symbol}, {ref}" if ref else symbol

    annotation = _label(rec.get("AnnotationType"))
    positions = [_label(p) for p in rec.get("UprPositions") or []]
    recommending = [_label(c) for c in rec.get("UprRecommendingCountry") or []]
    cycle = _label(rec.get("UprCycle"))

    if doc_type == "upr_recommendation":
        # "… recommendation 39.1 (Ghana), noted" — the recommending State in
        # brackets, as the UPR report itself prints it.
        pos = ", ".join(_POSITION[lang].get(p, p) for p in positions)
        cyc = f", {cycle.lower()}" if cycle else ""
        title = (f"Universal Periodic Review of Switzerland{cyc}: recommendation" if lang == "en"
                 else f"Examen périodique universel de la Suisse{cyc} : recommandation")
        if nums:
            title += f" {nums[0]}"
        if recommending:
            title += f" ({', '.join(recommending)})"
        if pos:
            title += f", {pos}"
    else:
        title = f"{body_name}{':' if lang == 'en' else ' :'} {doc_desc} ({symbol})"
        if ref:
            title += f", {ref}"
        if heading:
            title += f" — {heading}"
    if digest:
        title += (" [UHRI summary, not the verbatim UN text]" if lang == "en"
                  else " [résumé UHRI, pas le texte original de l'ONU]")

    topics: list[str] = [body_label]
    if annotation:
        topics.append(annotation)
    topics += [f"UPR position: {p}" if lang == "en" else f"Position EPU : {p}" for p in positions]
    topics += [f"Recommending State: {c}" if lang == "en" else f"État recommandant : {c}"
               for c in recommending]
    if cycle:
        topics.append(cycle)
    topics += [_label(t) for t in rec.get("Themes") or []]
    topics += [_label(p) for p in rec.get("AffectedPersons") or []]
    if digest:
        topics.append("UHRI summary (not verbatim)" if lang == "en" else "Résumé UHRI (non textuel)")
    topics = list(dict.fromkeys(t for t in topics if t))

    url = f"https://docs.un.org/{lang}/{symbol}"
    return {
        "annotation_id": rec["AnnotationId"],
        "doc_number": doc_number,
        "title": title,
        "date": (rec.get("PublicationDate") or "")[:10],
        "language": lang,
        "doc_type": doc_type,
        "issuing_authority": authority,
        "url": url,
        "pdf_url": url,
        "body_text": text,
        "topics": topics,
        "content_hash": sha256_hex(text),
    }


def build_stubs(en_records: Iterable[dict], fr_records: Iterable[dict] | None,
                country: str = COUNTRY) -> list[dict]:
    """English rows for every paragraph on `country`; French rows where the
    French export holds an actual translation. French is matched by
    annotation id, never by the localised country name ("Suisse")."""
    stubs: list[dict] = []
    en_text: dict[str, str] = {}
    for rec in en_records:
        if country not in (rec.get("Countries") or []):
            continue
        aid = rec.get("AnnotationId")
        if not aid:
            continue
        en_text[aid] = rec.get("Text") or ""
        stub = record_to_stub(rec, "en")
        if stub:
            stubs.append(stub)
    for rec in fr_records or ():
        aid = rec.get("AnnotationId")
        if aid not in en_text or (rec.get("Text") or "") == en_text[aid]:
            continue                                   # not ours / untranslated copy
        stub = record_to_stub(rec, "fr")
        if stub:
            stubs.append(stub)
    return stubs


# ───────────────────────────────────────────── scraper

class UnUhriScraper(PracticeScraper):
    SOURCE_KEY = "un_uhri"
    ISSUING_AUTHORITY = "UN"          # per row: UN-CAT, UN-CCPR, UN-UPR, …
    DEFAULT_DOC_TYPE = "concluding_observations"
    REVISION_FIELD = "content_hash"
    EXPORT_TIMEOUT = 600

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['annotation_id']}_{stub['language']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["issuing_authority"]
        return doc

    def _open_export(self, lang: str) -> IO[str]:
        url = EXPORT_URL.format(lang=lang)
        self._rate_limit()
        r = self.session.get(url, stream=True, timeout=self.EXPORT_TIMEOUT)
        r.raise_for_status()
        r.raw.decode_content = True
        return io.TextIOWrapper(r.raw, encoding="utf-8")

    def _records(self, lang: str) -> Iterator[dict]:
        n = 0
        with self._open_export(lang) as f:
            for rec in iter_json_array(f):
                n += 1
                yield rec
        if n < MIN_EXPORT_RECORDS:
            raise RuntimeError(f"[un_uhri] {lang} export holds only {n:,} records — not the full dataset")
        logger.info("[un_uhri] %s export: %d records", lang, n)

    def discover_documents(self) -> Iterator[dict]:
        # English first, in full: the French pass keys on its annotation ids.
        stubs = build_stubs(self._records("en"), self._records("fr"))
        if not stubs:
            # A renamed field or country label would otherwise read as
            # "nothing new" forever (scraper-health silent-success rule).
            raise RuntimeError(f"[un_uhri] no {COUNTRY} paragraphs in the UHRI export — schema changed?")
        langs = {lang: sum(s["language"] == lang for s in stubs) for lang in LANGUAGES}
        logger.info("[un_uhri] %d paragraphs on %s: %s", len(stubs), COUNTRY, langs)
        yield from stubs
