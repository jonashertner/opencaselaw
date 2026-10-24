"""
UN treaty bodies — decisions and Views on individual communications
===================================================================

  https://juris.ohchr.org/casedetails/{case_id}/en-US      case page (metadata + file links)
  https://docstore.ohchr.org/SelfServices/FilesHandler.ashx?enc=…   the document itself

Every decision the UN human-rights treaty bodies have adopted on an
individual communication, against every State party: Human Rights Committee
(CCPR), Committee against Torture (CAT), CERD, CEDAW, CRC, CRPD, CESCR and
CED. One court code per committee (untb_ccpr, untb_cat, …) so
search_decisions(court=…) can filter by committee, as with the ECtHR
formations. Scope decided by Jonas 2026-10-24: all communications, not only
those against Switzerland — a committee's reading of its treaty carries the
same weight in Swiss proceedings whichever State was the party.

Access. OHCHR's jurisprudence database (juris.ohchr.org) is the only index
of these cases; its robots.txt disallows all agents, and the UN Digital
Library's terms ask for approval before automated downloads. Permission was
requested on 2026-10-23 (library-ny@un.org, permissions@un.org, cc the UHRI
team); Jonas decided on 2026-10-24 to ingest meanwhile, at the rate and
under the identification that request promised: one request every five
seconds, an identifying user agent with a contact address, and a stop on
the first sign of a block (a 403/429 is an error, never retried around).
docstore.ohchr.org, which serves the files, has no robots.txt.

Discovery. Case ids are sequential integers (1 … 4173 on 2026-10-24, with
~365 holes). Every id up to the highest one seen plus PROBE_WINDOW is
probed once; an id that answers with the database's error page ("Oops...
error occured") is gap-cached and re-probed weekly (ScraperState.GAP_TTL_DAYS).
Which case ids are done is kept in a sidecar (state/untb.juris_ids.tsv,
case_id TAB decision_id), because the decision_id is minted from the UN
symbol, which only the case page reveals.

Document. French where OHCHR has a French text, else English (the ECtHR
rule: French is a Swiss official language, English the fallback). Per
language the Word-exported HTML, then DOCX, then the PDF's text layer;
image-only PDFs (most decisions before ~1995) go through Tesseract OCR,
which the production host has and a dev machine may not — a None there is
gap-cached and retried.

decision_id = untb_<committee>_<symbol>, docket_number = the UN symbol
('CAT/C/51/D/426/2010', unique per document — an admissibility decision and
the later Views on the same communication are two documents),
docket_number_2 = the communication number(s) ('426/2010').
"""
from __future__ import annotations

import html as html_lib
import io
import logging
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, extract_citations, make_decision_id

logger = logging.getLogger(__name__)

CASE_URL = "https://juris.ohchr.org/casedetails/{case_id}/en-US"
# Highest case id observed on 2026-10-24 (CCPR/C/143/D/4367/2023). Discovery
# starts from here on a fresh state so the first run covers the whole range.
SEED_MAX_CASE_ID = 4173
PROBE_WINDOW = 150

# Symbol prefix -> (court code, committee acronym). Order matters: "CAT/OP"
# (the Subcommittee on Prevention of Torture) is not a complaint body.
SYMBOL_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("CCPR/", "untb_ccpr", "CCPR"),
    ("CAT/C/", "untb_cat", "CAT"),
    ("CERD/", "untb_cerd", "CERD"),
    ("CEDAW/", "untb_cedaw", "CEDAW"),
    ("CRC/", "untb_crc", "CRC"),
    ("CRPD/", "untb_crpd", "CRPD"),
    ("E/C.12/", "untb_cescr", "CESCR"),
    ("CED/", "untb_ced", "CED"),
)
UNTB_COURTS = frozenset(court for _, court, _ in SYMBOL_PREFIXES)

# German / French / Italian names of each treaty, appended to the regeste:
# the only lexical bridge a DE or IT query has to a French or English
# decision (same device as scrapers/hudoc._ecthr_convention_keywords).
_TREATY_KEYWORDS = {
    "CCPR": "UNO-Pakt II, Internationaler Pakt über bürgerliche und politische Rechte; "
            "Pacte international relatif aux droits civils et politiques; Patto II",
    "CAT": "UNO-Antifolterkonvention, Übereinkommen gegen Folter; "
           "Convention contre la torture; Convenzione contro la tortura",
    "CERD": "Rassendiskriminierungsübereinkommen; Convention sur l'élimination de la "
            "discrimination raciale; Convenzione sull'eliminazione della discriminazione razziale",
    "CEDAW": "Frauenrechtskonvention, Übereinkommen zur Beseitigung jeder Form von "
             "Diskriminierung der Frau; Convention sur l'élimination de la discrimination "
             "à l'égard des femmes",
    "CRC": "Kinderrechtskonvention, Übereinkommen über die Rechte des Kindes; "
           "Convention relative aux droits de l'enfant; Convenzione sui diritti del fanciullo",
    "CRPD": "Behindertenrechtskonvention, Übereinkommen über die Rechte von Menschen mit "
            "Behinderungen; Convention relative aux droits des personnes handicapées",
    "CESCR": "UNO-Pakt I, Internationaler Pakt über wirtschaftliche, soziale und kulturelle "
             "Rechte; Pacte international relatif aux droits économiques, sociaux et culturels",
    "CED": "Übereinkommen zum Schutz aller Personen vor dem Verschwindenlassen; "
           "Convention pour la protection de toutes les personnes contre les disparitions forcées",
}

LEGAL_AREA = "UNO-Menschenrechtsausschüsse / Organes conventionnels de l'ONU"

# juris "Type of decision" -> outcome, only where the type itself says it.
# A merits decision can go either way; the text decides, not the type.
_OUTCOME = {
    "inadmissibility decision": "Nichteintreten",
    "discontinuance decision": "Abschreibung",
}

_ERROR_PAGE = "Oops... error occured"
_LANG_ROW = {"French": "fr", "English": "en"}
_FORMAT_ORDER = ("html", "docx", "pdf")
MIN_TEXT_CHARS = 400      # a discontinuance decision is a few paragraphs

_FUNCTION_WORDS = {
    "en": {"the", "of", "and", "to", "that", "is", "for", "with", "by", "its", "has", "was", "which"},
    "fr": {"le", "la", "les", "des", "du", "et", "est", "que", "qui", "dans", "pour", "sur", "une",
           "par", "aux", "au", "à", "ont", "été"},
}


# ───────────────────────────────────────────── case page

def _field(text: str, label: str, nxt: str) -> str:
    m = re.search(re.escape(label) + r"\s*(.*?)\s*" + re.escape(nxt), text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_case_page(page_html: str) -> dict | None:
    """Metadata and per-language file links of one juris case page, or None
    for the database's error page (an unused case id)."""
    if _ERROR_PAGE in page_html or "Communication number(s):" not in page_html:
        return None
    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = html_lib.unescape(re.sub(r"\s+", " ", soup.get_text(" ")))
    # The record starts after the language switcher; its first two items are
    # the case title and the symbol.
    start = text.find("Русский")
    body = text[start + len("Русский"):] if start >= 0 else text
    head = body.split("Communication number(s):", 1)[0].strip()
    m = re.search(r"\s((?:CCPR|CAT|CERD|CEDAW|CRC|CRPD|CED|CMW|E/C\.12)/\S+)\s*$", head)
    symbol = m.group(1) if m else ""
    title = head[: m.start()].strip() if m else head

    links: dict[str, dict[str, str]] = {}
    for row in soup.select("tbody tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        lang = _LANG_ROW.get(cells[0].get_text(strip=True))
        if not lang:
            continue
        for a in row.find_all("a", href=True):
            img = a.find("img")
            fmt = Path(img["src"]).stem.lower() if img and img.get("src") else ""
            if fmt in _FORMAT_ORDER:
                links.setdefault(lang, {})[fmt] = a["href"]

    return {
        "title": title,
        "symbol": symbol,
        "communication_numbers": _field(body, "Communication number(s):", "Author:"),
        "author": _field(body, "Author:", "Type of decision:"),
        "decision_type": _field(body, "Type of decision:", "Session No"),
        "session": _field(body, "Session No", "Country"),
        "country": _field(body, "Country", "Submission date"),
        "submission_date": _parse_date(_field(body, "Submission date", "Date of decision")),
        "decision_date": _parse_date(_field(body, "Date of decision", "Comment:")),
        "substantive_issues": _field(body, "Substantive issues:", "Substantive articles:"),
        "substantive_articles": _field(body, "Substantive articles:", "Procedural issues:"),
        "procedural_issues": _field(body, "Procedural issues:", "Procedural articles:"),
        "procedural_articles": _field(body, "Procedural articles:", "Language"),
        "links": links,
    }


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def committee_for(symbol: str) -> tuple[str, str] | None:
    """(court code, committee acronym) for a UN symbol, None for anything else."""
    for prefix, court, acronym in SYMBOL_PREFIXES:
        if symbol.startswith(prefix):
            return court, acronym
    return None


# ───────────────────────────────────────────── document text

def html_text(data: bytes) -> str:
    """Text of OHCHR's Word-exported HTML (UTF-16 with BOM; bs4 sniffs it)."""
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def docx_text(data: bytes) -> str:
    """Paragraph text of a .docx, without python-docx."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        return ""
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        p = p.replace("<w:tab/>", "<w:t>\t</w:t>")
        paras.append("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)))
    return html_lib.unescape("\n".join(paras))


def pdf_text(data: bytes) -> str:
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    except Exception as e:  # noqa: BLE001 — a bad PDF is a missing text, not a crash
        logger.debug(f"[untb] PDF text layer unreadable: {e}")
        return ""


def clean_text(text: str) -> str:
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in (text or "").splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    # "9.2Le Comité", "10.À la lumière" — Word dropped the tab after the number.
    text = re.sub(r"(?m)^(\d{1,3}\.\d{1,2})(?=[^\d\s.])", r"\1 ", text)
    text = re.sub(r"(?m)^(\d{1,3})\.(?=[^\d\s])", r"\1. ", text)
    return text


def in_other_language(text: str, lang: str) -> bool:
    """True when `text` is clearly not in `lang` (OHCHR occasionally files the
    English document under the French row)."""
    words = re.findall(r"[a-zà-ÿ]+", text[:20000].lower())
    own = sum(w in _FUNCTION_WORDS[lang] for w in words)
    other = sum(w in _FUNCTION_WORDS["fr" if lang == "en" else "en"] for w in words)
    return other >= 20 and other > 2 * own


_NONE_FOUND = {"", "No issues found", "No articles found"}


def build_regeste(meta: dict, acronym: str) -> str:
    """juris's index terms (decision type, issues, articles) plus the treaty
    names in DE/FR/IT. The empty fields read 'No issues found' / 'No articles
    found' on the page and are dropped."""
    parts = [meta.get("decision_type") or ""]
    for key, label in (("substantive_issues", "Substantive issues"),
                       ("substantive_articles", "Articles"),
                       ("procedural_issues", "Procedural issues")):
        value = (meta.get(key) or "").strip()
        if value not in _NONE_FOUND:
            parts.append(f"{label}: {value}")
    parts.append(f"[{acronym}: {_TREATY_KEYWORDS[acronym]}]")
    return "; ".join(p for p in parts if p)


# ───────────────────────────────────────────── scraper

class UNTreatyBodyScraper(BaseScraper):
    """Decisions and Views of the UN treaty bodies on individual communications."""

    REQUEST_DELAY = 5.0        # the rate promised to the UN on 2026-10-23
    TIMEOUT = 90
    MAX_ERRORS = 10            # a block shows up as errors; stop early, never push through
    USER_AGENT = "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)"
    CACHE_NONE_AS_GAP = True
    # Unused ids and text-less scans come back None; ~365 holes + the probe
    # window on a full run.
    MAX_NONE_RETURNS = 1500

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sidecar_path = self.state_dir / "untb.juris_ids.tsv"
        self._done_case_ids: set[int] = set()
        if self.sidecar_path.exists():
            for line in self.sidecar_path.read_text(encoding="utf-8").splitlines():
                head = line.split("\t", 1)[0]
                if head.isdigit():
                    self._done_case_ids.add(int(head))
        self._case_of: dict[str, int] = {}

    @property
    def court_code(self) -> str:
        return "untb"

    @staticmethod
    def gap_id(case_id: int) -> str:
        return f"untb_juris_{case_id}"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        top = max([SEED_MAX_CASE_ID, *self._done_case_ids]) + PROBE_WINDOW
        pending = 0
        for case_id in range(1, top + 1):
            if case_id in self._done_case_ids or self.state.is_known(self.gap_id(case_id)):
                continue
            pending += 1
            yield {
                "case_id": case_id,
                "decision_id": self.gap_id(case_id),     # what a None return gap-caches
                "docket_number": f"juris {case_id}",
                "url": CASE_URL.format(case_id=case_id),
            }
        logger.info(f"[untb] {pending} case ids to probe (1..{top}, {len(self._done_case_ids)} done)")

    def _fetch_text(self, links: dict[str, dict[str, str]]) -> tuple[str, str, str] | None:
        """(text, language, format) of the best available rendition."""
        scans: list[tuple[str, bytes]] = []
        for lang in ("fr", "en"):
            for fmt in _FORMAT_ORDER:
                href = links.get(lang, {}).get(fmt)
                if not href:
                    continue
                data = self.get(href).content
                if fmt == "html":
                    text = html_text(data)
                elif fmt == "docx":
                    text = docx_text(data)
                else:
                    text = pdf_text(data)
                    if len(text.strip()) < MIN_TEXT_CHARS:
                        scans.append((lang, data))     # image-only: OCR, last resort
                        continue
                text = clean_text(text)
                if in_other_language(text, lang):
                    break       # mis-filed under this language; its other renditions are too
                if len(text) >= MIN_TEXT_CHARS:
                    return text, lang, fmt
        for lang, data in scans:
            from scrapers.pdf_ocr import ocr_pdf_bytes
            text = clean_text(ocr_pdf_bytes(data, lang="fra" if lang == "fr" else "eng"))
            if len(text) >= MIN_TEXT_CHARS:
                return text, lang, "pdf-ocr"
        return None

    def fetch_decision(self, stub: dict) -> Decision | None:
        case_id = stub["case_id"]
        meta = parse_case_page(self.get(stub["url"]).text)
        if meta is None:
            return None
        committee = committee_for(meta["symbol"])
        if committee is None:
            logger.warning(f"[untb] case {case_id}: unrecognised symbol {meta['symbol']!r}")
            return None
        court, acronym = committee
        got = self._fetch_text(meta["links"])
        if got is None:
            logger.info(f"[untb] case {case_id} ({meta['symbol']}): no usable text")
            return None
        text, lang, fmt = got

        decision_id = make_decision_id(court, meta["symbol"])
        self._case_of[decision_id] = case_id
        dtype = meta.get("decision_type") or None
        return Decision(
            decision_id=decision_id,
            court=court,
            canton="CH" if meta.get("country") == "Switzerland" else "UN",
            docket_number=meta["symbol"],
            docket_number_2=meta.get("communication_numbers") or None,
            decision_date=meta.get("decision_date"),
            language=lang,
            title=meta.get("title") or meta["symbol"],
            legal_area=LEGAL_AREA,
            regeste=build_regeste(meta, acronym),
            decision_type=dtype,
            outcome=_OUTCOME.get((dtype or "").lower()),
            full_text=text,
            source_url=stub["url"],
            pdf_url=f"https://docs.un.org/{lang}/{meta['symbol']}",
            external_id=f"juris:{case_id}",
            cited_decisions=extract_citations(text) if len(text) > 200 else [],
            scraped_at=datetime.now(timezone.utc),
        )

    def on_decision_persisted(self, decision: Decision) -> None:
        """run_scraper calls this after the JSONL write: only then is the case
        id done (a crash before it re-fetches the case, never loses it)."""
        case_id = self._case_of.pop(decision.decision_id, None)
        if case_id is None or case_id in self._done_case_ids:
            return
        self._done_case_ids.add(case_id)
        with open(self.sidecar_path, "a", encoding="utf-8") as f:
            f.write(f"{case_id}\t{decision.decision_id}\n")
