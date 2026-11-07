"""
Swiss Bankers Association — Schweizerische Bankiervereinigung (swissbanking.ch)
================================================================================

LICENCE / PERMISSION STATUS (2026-11-06): the SBVg is a PRIVATE industry
association (Verein under Art. 60 ZGB), not a federal or cantonal authority.
Its Richtlinien, Vereinbarungen and the other texts here are its own
copyrighted publications — several are FINMA-recognised as supervisory
minimum standards (which makes them binding on the whole industry, not just
SBVg members, but does not change who holds the copyright). Jonas Hertner
emailed the SBVg on 2026-11-06 asking permission to index this material for
OpenCaseLaw. Jonas decided on 2026-11-07 to publish it while the request is
pending (as for bankingombudsman.py): the texts are served unchanged,
attributed to the SBVg and linked to its site, outside the corpus's CC0
licence. If the association sets conditions or asks for removal, drop the
source from ENABLED_SCRAPERS, delete output/practice/sbvg.jsonl and rebuild
practice.db.

The live document library (JS-rendered, no static links)
----------------------------------------------------------
swissbanking.ch/de/services/downloads (the site's general "Downloads"
catalogue, filterable by tag) renders via a React bundle
(Internezzo.Downloads/Js/App/static/js/downloads.js) that calls back to the
SAME page URL with `?ajaxDestination[identifier]=downloadItems&
ajaxDestination[tag]=<uuid>&ajaxDestination[limit]=...` (a Neos CMS "ajax
destination" pattern — found by reading the bundle: it builds the request
with axios, not a separate REST API). That endpoint consistently answered
"Unknown AJAX destination" (HTTP 404) for every variant tried on 2026-11-06
(with/without a prior-page session cookie, with/without Referer and
X-Requested-With headers, with and without the full default parameter set
the component sends on first load) — it appears to require request state
that only exists inside Neos' own server-side Fusion rendering of that
exact page view, which a plain HTTP client cannot reproduce. This module
therefore does NOT use that endpoint.

What it uses instead: the site's TOPIC pages (under /de/themen/...,
/fr/themes/..., /it/temi/..., /en/topics/...) are ordinary server-rendered
HTML and link the current self-regulation PDFs directly, each in its own
`div.content__assetList--asset` block or as a plain in-text link — both
carry the file's clean name as the anchor's `title` attribute (or, failing
that, its visible text). `parse_pdf_links()` reads every such link to a
`_Resources/Persistent/.../*.pdf` file on each page. Five pages are scraped,
each in DE/FR/IT/EN (found via that page's own `<link rel="alternate"
hreflang>` tags, the same WPML-style mechanism used by bankingombudsman.py's
case collection — Neos evidently uses it too):

  selbstregulierung      regulierung-compliance/selbstregulierung — the
                         current VSB/CDB text + the "Übersicht über die
                         Richtlinien der SBVg" index PDF (an authoritative,
                         dated table of all 17 current/superseded
                         Richtlinien — used below as the enumeration
                         fallback for the items no page links directly).
  geldwaeschereibekaempfung  regulierung-compliance/geldwaeschereibekaempfung
                         — VSB 27 (in force 2027-01-01) + its track-changes
                         companion (skipped — a redline of the clean VSB27
                         text, same exclusion rab_rs.py makes) + its
                         Kommentar; VSB 20 (in force until then) + its
                         Kommentar; ~20 "Leading Cases" case-digest PDFs of
                         the VSB Aufsichtskommission (one per half-year,
                         2017 H1 – 2026 H1) plus a 2017-2026 compilation
                         (skipped — sums the half-years, same exclusion
                         ehra.py makes for its "(alle)" compilation); ~12
                         Tätigkeitsberichte of that commission (1977-2021,
                         in multi-year volumes).
  hypothekarmarktregulierung  regulierung-compliance/hypothekarmarktregulierung
                         — the two FINMA-recognised mortgage-lending
                         Richtlinien (Mindestanforderungen; Prüfung/
                         Bewertung/Abwicklung grundpfandgesicherter Kredite).
  sustainable-finance/selbstregulierungen-im-bereich-sustainable-finance —
                         the ESG-Präferenzen/Greenwashing Richtlinie
                         (2023 and 2025 editions — SBVg renamed it between
                         editions, so they are NOT grouped under one
                         doc_number; see below), its FAQ, its "Übersicht
                         Änderungen", the Energieeffizienz-Hypotheken
                         Richtlinie, a Onepager, and an implementation
                         study ("Wie Banken ... umsetzen").
  blockchain_leitfaden   a single statements-und-medienmitteilungen page:
                         "Leitfaden der SBVg zur Eröffnung von Firmenkonti
                         für DLT-Unternehmen" (2019, DE/FR; the IT/EN pages
                         only relist the DE file — see below).

A translation that does not exist is not a 404: a language page simply
relists an earlier language's PDF under its own anchor text (confirmed for
several Leading Cases/Tätigkeitsberichte years on the IT and EN pages,
which fall back to the FR or DE file respectively). Pages are read in a
fixed order (de, fr, it, en) and a URL already yielded under an earlier
language is never yielded again, so the row keeps the language it was
actually published in.

Known gap (say so, per the task): the "Übersicht" PDF lists 17 Richtlinien
status
 as of 01.09.2023. Of those, this module finds a direct public PDF for the
VSB/CDB, the two hypothecary Richtlinien, and (via the ESG topic page) the
ESG-Präferenzen and Energieeffizienz Richtlinien — six of seventeen. The
other eleven (Narilo/nachrichtenlose Vermögenswerte, Vermögensverwaltungs-
aufträge, Finanzanalyse-Unabhängigkeit, Treuhandanlagen, Business Continuity
Management, Falschgeld, Länderrisiko, and three repealed ones: strukturierte
Produkte, Protokollierungspflicht Art. 24 KAG, Effektenhändler-
Verhaltensregeln, Notes ausländischer Schuldner) have no discoverable public
link on swissbanking.ch as of 2026-11-06 (the dedicated member portal at
/de/vsb-dokumente requires an institutional login and is out of scope for a
public scraper). FINMA's own enumeration page
(finma.ch/de/dokumentation/selbstregulierung/) was checked as the task
suggested; it explains the recognition mechanism but links no per-document
table, so it did not help close this gap. The "Übersicht" PDF text itself
IS indexed (doc_type "bericht") so a reader at least sees the full table.

doc_type: "richtlinie" for the VSB/CDB text and the Hypothek/ESG/
Energieeffizienz Richtlinien (the task's suggested type, reused rather than
adding "vereinbarung" to the enum — see the module's final report for the
call); "rechtsprechung" for the Leading Cases (anonymised disciplinary
precedent of the VSB's own supervisory commission — the same use of the
type as the banking ombudsman's case collection and BSV's AHI-Praxis);
"bericht" for the Tätigkeitsberichte, the Übersicht table and the
implementation study; "faq" for the ESG FAQ (already a registered value,
used elsewhere by ESA); "merkblatt" for the Onepager and the blockchain
Leitfaden (practical guidance notes, not binding rules).
"""
from __future__ import annotations

import datetime
import logging
import re
import unicodedata
from typing import Iterator
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify

logger = logging.getLogger(__name__)

BASE = "https://www.swissbanking.ch"

# Each group: a stable key + its DE/FR/IT/EN URL (found via hreflang on the
# DE page — see module docstring) + the classifier to apply to its tiles.
PAGE_GROUPS = (
    ("selbstregulierung", {
        "de": f"{BASE}/de/themen/regulierung-compliance/selbstregulierung",
        "fr": f"{BASE}/fr/themes/reglementation-et-compliance/autoregulation",
        "it": f"{BASE}/it/temi/regolamentazione-e-conformita/autodisciplina",
        "en": f"{BASE}/en/topics/regulation-and-compliance/self-regulation",
    }),
    ("geldwaeschereibekaempfung", {
        "de": f"{BASE}/de/themen/regulierung-compliance/geldwaeschereibekaempfung",
        "fr": f"{BASE}/fr/themes/reglementation-et-compliance/lutte-contre-le-blanchiment-d-argent",
        "it": f"{BASE}/it/temi/regolamentazione-e-conformita/lotta-al-riciclaggio-di-denaro",
        "en": f"{BASE}/en/topics/regulation-and-compliance/the-fight-against-money-laundering",
    }),
    ("hypothekarmarktregulierung", {
        "de": f"{BASE}/de/themen/regulierung-compliance/hypothekarmarktregulierung",
        "fr": f"{BASE}/fr/themes/reglementation-et-compliance/marche-hypothecaire",
        "it": f"{BASE}/it/temi/regolamentazione-e-conformita/regolamentazione-del-mercato-ipotecario",
        "en": f"{BASE}/en/topics/regulation-and-compliance/mortgage-market-regulation",
    }),
    ("esg_selbstregulierung", {
        "de": f"{BASE}/de/themen/sustainable-finance/selbstregulierungen-im-bereich-sustainable-finance",
        "fr": f"{BASE}/fr/themes/sustainable-finance/autoregulations-en-matiere-de-finance-durable",
        "it": f"{BASE}/it/temi/finanza-sostenibile/autodisciplina-nell-ambito-della-sustainable-finance",
        "en": f"{BASE}/en/topics/sustainable-finance/self-regulation-on-sustainable-finance",
    }),
    ("blockchain_leitfaden", {
        "de": f"{BASE}/de/medien-politik/statements-und-medienmitteilungen/"
              "eroeffnung-von-firmenkonti-fuer-blockchain-unternehmen-bankiervereinigung-"
              "publiziert-leitfaden-fuer-ihre-mitglieder",
        "fr": f"{BASE}/fr/medias-politique/communiques-de-presse/"
              "eroeffnung-von-firmenkonti-fuer-blockchain-unternehmen-bankiervereinigung-"
              "publiziert-leitfaden-fuer-ihre-mitglieder",
        "en": f"{BASE}/en/media-politics/press-releases/"
              "opening-corporate-accounts-for-blockchain-companies-swiss-bankers-"
              "association-publishes-guidelines-for-its-members",
    }),
)

_STATUS_IN_FORCE = ("in Kraft",)
_STATUS_SUPERSEDED = ("aufgehoben",)
# VSB 27 takes effect 2027-01-01 (announced 2026-10-30); VSB 20 is current
# until then. Computed from today's date rather than hardcoded so this does
# not go stale the moment 2027 arrives — but it IS computed once at import
# time, so a long-running process needs a restart (or re-import) after
# 2027-01-01 to see VSB 27 as the in-force edition. Re-check this constant
# if the VSB's effective date changes again.
_VSB_CURRENT_EDITION = "20" if datetime.date.today() < datetime.date(2027, 1, 1) else "27"

_KORREKTURMODUS_RE = re.compile(r"Korrekturmodus|track.?changes|avec modifications", re.I)
_KOMMENTAR_RE = re.compile(r"Kommentar|Commentaire|Commento|Commentary", re.I)
_LEADING_CASES_RE = re.compile(r"Leading\s*Cases", re.I)
_TAETIGKEITSBERICHT_RE = re.compile(
    r"T[äa]tigkeitsbericht|Rapport d.activit[ée]s?|Geschäftsbericht", re.I)
_VSB_TOKEN_RE = re.compile(
    r"\bVSB\b|\bCDB\b|Standesregeln|Sorgfaltspflicht der Banken|"
    r"diligence des banques|diligenza delle banche|due diligence", re.I)
_EDITION27_RE = re.compile(r"\b27\b")
_EDITION20_RE = re.compile(r"\b20\b|2020")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# No trailing \b after "1\." / "2\.": a boundary needs a word/non-word
# transition, and "1." is always followed by a space, so \b1\.\b never
# matches — silently missing every "1. Halbjahr"/"2. Halbjahr" title.
_HALF1_RE = re.compile(r"\b1\.|\b1er\b|\bprimo\b", re.I)
_HALF2_RE = re.compile(r"\b2\.|\b2i[eè]me\b|\bsecondo\b", re.I)
_EDITION_YEAR_SUFFIX_RE = re.compile(r"\s*\((\d{4})\)\s*$")
# Titles that talk ABOUT a Richtlinie/Directive rather than naming one (an
# implementation study) — checked before the generic Richtlinien/Directives/
# Guidelines match below, which would otherwise fire on the word alone.
_ANALYSIS_RE = re.compile(r"^(?:Wie|Comment|How)\b", re.I)
# "FAQ" and "Onepager" are the same loanword in all four languages, so they
# make a reliable language-invariant signal on their own.
_FAQ_RE = re.compile(r"\bFAQ\b|\bQ&A\b", re.I)
_ONEPAGER_RE = re.compile(r"\bOnepager\b", re.I)
# The exact title of the one authoritative index table (found on the
# selbstregulierung page) — intentionally narrow so it does not also catch
# the unrelated ESG "list of changes" document below, which happens to
# contain the same two words ("Übersicht"/"Richtlinien").
_RICHTLINIEN_INDEX_RE = re.compile(
    r"Übersicht\s+über\s+die\s+Richtlinien\s+der\s+SBVg|"
    r"Aper[çc]u\s+des\s+[Dd]irectives\s+de\s+l.ASB|"
    r"Elenco\s+delle\s+direttive", re.I)
_ESG_CHANGELOG_RE = re.compile(
    r"(?:Änderungen|modifications|modifiche|changes)\s+"
    r"(?:Richtlinien|Directives|Direttive|Guidelines)|"
    r"(?:Richtlinien|Directives|Direttive|Guidelines).{0,15}"
    r"(?:Änderungen|modifications|modifiche|changes)", re.I)
_ESG_STUDY_RE = re.compile(
    r"ESG.{0,15}(?:umsetzen|mettent en œuvre|implement)|"
    r"(?:Wie|Comment|How)\b.{0,80}ESG", re.I)
# Language-invariant fingerprints for the Richtlinien/Directives whose
# titles are fully translated (no shared word survives across DE/FR/IT/EN),
# so doc_number is minted from the SUBJECT instead of the title text —
# otherwise each language would mint its own doc_number for what is one
# document. Order matters: ESG must be checked before the plain mortgage
# fingerprint since the ESG guideline's own title also contains no
# mortgage/hypothek word, but the energy-efficiency one contains BOTH
# "energy" and "mortgage" words.
_ESG_ADVICE_RE = re.compile(r"\bESG\b", re.I)
_ENERGY_MORTGAGE_RE = re.compile(
    r"(?:Energieeffizienz|[ée]nerg[ée]tique|efficienza energetica|energy efficiency)", re.I)
_MIN_REQ_MORTGAGE_RE = re.compile(
    r"Mindestanforderungen.{0,20}Hypothekarfinanzierungen|"
    r"exigences minimales.{0,20}financements hypoth[ée]caires|"
    r"requisiti minimi.{0,20}finanziamenti ipotecari", re.I)
_PLEDGE_CREDIT_RE = re.compile(
    r"grundpfandgesicherter\s+Kredite|gage immobilier|pegno immobiliare", re.I)
_MULTIYEAR_COMPILATION_RE = re.compile(
    r"\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b")  # e.g. "2017 - 2026" / "2017 – 2026"

# A single out-of-scope consultation response (not a self-regulation text)
# that a generic short in-text link on the Geldwäsche page points to, under
# a too-short anchor text on some language pages ("presa di posizione") that
# would otherwise pass the length-based junk filter in parse_pdf_links().
_EXCLUDE_URL_RE = re.compile(r"Stellungnahme_SBVg_zum_TJPG", re.I)


def _clean(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def parse_pdf_links(html: str) -> list[dict]:
    """[{title, url}] for every link to a `_Resources/Persistent/*.pdf`
    file on a swissbanking.ch page. Prefers the anchor's `title` attribute
    (the clean document name, present on the structured
    `content__assetList` blocks and on most in-text links) over its visible
    text; when neither is present AND the text is short (a bare "VSB" or
    "PDF" inline mention — the real document is linked elsewhere on the
    page with its full name), the link is dropped rather than indexed under
    a useless title. The same PDF is sometimes linked twice with different
    text (an in-text mention plus the "real" tile); the better-titled
    occurrence wins regardless of which comes first in the page."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: dict[str, list[tuple[bool, int, str]]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if "_Resources/Persistent" not in href or not href.lower().endswith(".pdf"):
            continue
        title_attr = a.get("title")
        text = _clean(a.get_text())
        title = _clean(title_attr) if title_attr else text
        candidates.setdefault(href, []).append((bool(title_attr), len(title), title))
    out = []
    for url, cands in candidates.items():
        has_attr, length, title = max(cands, key=lambda c: (c[0], c[1]))
        if not has_attr and length < 15:
            continue
        if _EXCLUDE_URL_RE.search(url) or _EXCLUDE_URL_RE.search(title):
            continue
        out.append({"title": title, "url": url})
    return out


def _leading_cases_period(title: str) -> str:
    """Language-invariant key ("H1 2026", "H2 2024") so the DE/FR/IT/EN rows
    of the SAME half-year digest share one doc_number despite differently
    worded titles."""
    year_m = _YEAR_RE.search(title)
    year = year_m.group(0) if year_m else "????"
    half = "1" if _HALF1_RE.search(title) else ("2" if _HALF2_RE.search(title) else "?")
    return f"H{half} {year}"


def _report_year_range(title: str) -> str:
    """Language-invariant "1977-1980" key for a Tätigkeitsbericht title,
    from the distinct years printed in it (one row can print the same year
    twice, e.g. a single-year volume)."""
    years = []
    for m in _YEAR_RE.finditer(title):
        if m.group(0) not in years:
            years.append(m.group(0))
    return "-".join(years) if years else "????"


def classify_sbvg_pdf(title: str) -> dict | None:
    """{doc_number, doc_type, topics, year} for one parse_pdf_links() tile,
    or None to drop it (a track-changes redline or a multi-year Leading
    Cases compilation — both duplicate content already captured under the
    individual documents/periods they sum)."""
    title = _clean(title)
    if _KORREKTURMODUS_RE.search(title):
        return None
    if _LEADING_CASES_RE.search(title) and _MULTIYEAR_COMPILATION_RE.search(title):
        return None  # the "2017 - 2026" compilation

    if _LEADING_CASES_RE.search(title):
        period = _leading_cases_period(title)
        return {"doc_number": f"SBVg Leading Cases {period}", "doc_type": "rechtsprechung",
                "topics": ["VSB", "Leading Cases", "Aufsichtskommission"], "year": period.split()[-1]}

    if _TAETIGKEITSBERICHT_RE.search(title):
        years = _report_year_range(title)
        return {"doc_number": f"SBVg Tätigkeitsbericht Aufsichtskommission {years}",
                "doc_type": "bericht",
                "topics": ["VSB", "Tätigkeitsbericht", "Aufsichtskommission"],
                "year": years.split("-")[-1]}

    if _KOMMENTAR_RE.search(title) and _VSB_TOKEN_RE.search(title):
        edition = "27" if _EDITION27_RE.search(title) else (
            "20" if _EDITION20_RE.search(title) else "")
        status = (_STATUS_IN_FORCE if (edition == _VSB_CURRENT_EDITION or not edition)
                  else _STATUS_SUPERSEDED)
        return {"doc_number": "SBVg Kommentar zur VSB", "doc_type": "bericht",
                "topics": [t for t in ("VSB", f"VSB {edition}" if edition else "", *status) if t],
                "year": ""}

    if _VSB_TOKEN_RE.search(title):
        edition = "27" if _EDITION27_RE.search(title) else (
            "20" if _EDITION20_RE.search(title) else "")
        # An edition-less mention (seen on the EN overview page) is treated
        # as the one currently in force (see _VSB_CURRENT_EDITION above).
        status = (_STATUS_IN_FORCE if (edition == _VSB_CURRENT_EDITION or not edition)
                  else _STATUS_SUPERSEDED)
        return {"doc_number": "SBVg VSB", "doc_type": "richtlinie",
                "topics": [t for t in ("VSB", f"VSB {edition}" if edition else "", *status) if t],
                "year": ""}

    if _RICHTLINIEN_INDEX_RE.search(title):
        return {"doc_number": "SBVg Übersicht Richtlinien", "doc_type": "bericht",
                "topics": ["Selbstregulierung", "Übersicht"], "year": ""}

    if re.search(r"Leitfaden.{0,40}Firmenkonti|Guide pratique.{0,40}comptes", title, re.I):
        return {"doc_number": "SBVg Leitfaden Firmenkonti DLT-Unternehmen", "doc_type": "merkblatt",
                "topics": ["Blockchain", "DLT", "Firmenkonti"], "year": "2019"}

    year = _year_suffix(title)

    if _ESG_CHANGELOG_RE.search(title):
        # One document, DE/FR/IT/EN: titles share no translatable word, so
        # the doc_number is a hardcoded canonical label, not the title text.
        return {"doc_number": "SBVg Übersicht Änderungen ESG-Richtlinien", "doc_type": "bericht",
                "topics": ["ESG", "Sustainable Finance", "Übersicht"], "year": year or "2025"}

    if _ESG_STUDY_RE.search(title) or (_ANALYSIS_RE.search(title) and "ESG" in title.upper()):
        # The implementation study ("Wie Banken ... umsetzen" / "Comment les
        # banques ... mettent en œuvre" / "How banks ... implement"): about
        # the ESG Richtlinie, not itself one — checked before the generic
        # Richtlinien/Guidelines match below, which the word "ESG-Richtlinien"
        # /"directives ESG"/"ESG guidelines" in its own title would otherwise hit.
        return {"doc_number": "SBVg Studie ESG-Richtlinien Umsetzung", "doc_type": "bericht",
                "topics": ["ESG", "Sustainable Finance", "Studie"], "year": year}

    if _FAQ_RE.search(title):
        if _ESG_ADVICE_RE.search(title) or re.search(r"greenwashing", title, re.I):
            return {"doc_number": "SBVg Richtlinien ESG-Präferenzen FAQ", "doc_type": "faq",
                    "topics": ["ESG", "Sustainable Finance", "FAQ"], "year": year or "2026"}
        base = _EDITION_YEAR_SUFFIX_RE.sub("", title)
        return {"doc_number": f"SBVg {base}", "doc_type": "faq",
                "topics": ["FAQ"], "year": year}

    if _ONEPAGER_RE.search(title):
        return {"doc_number": "SBVg Onepager Sustainable Finance", "doc_type": "merkblatt",
                "topics": ["Sustainable Finance", "Onepager"], "year": year or "2025"}

    if _ESG_ADVICE_RE.search(title) or re.search(r"greenwashing", title, re.I):
        # The ESG-Präferenzen/Greenwashing Richtlinie itself (not its FAQ,
        # changelog, Onepager or the study about it — all matched above).
        # Fully-translated title, so the doc_number is a fixed label too;
        # the 2023 and 2025 editions keep SEPARATE numbers on purpose — the
        # SBVg renamed the guideline between editions (added "...und zur
        # Prävention von Greenwashing"), so collapsing them would claim an
        # edition history the association's own titles don't support.
        suffix = " (2025-)" if year and year >= "2024" else " (2023)" if year else ""
        return {"doc_number": f"SBVg Richtlinien ESG-Präferenzen Anlageberatung{suffix}",
                "doc_type": "richtlinie", "topics": ["ESG", "Sustainable Finance"], "year": year}

    if _ENERGY_MORTGAGE_RE.search(title):
        return {"doc_number": "SBVg Richtlinien Energieeffizienz Hypotheken", "doc_type": "richtlinie",
                "topics": ["Hypothekarmarkt", "Sustainable Finance"], "year": year or "2022"}

    if _MIN_REQ_MORTGAGE_RE.search(title):
        return {"doc_number": "SBVg Richtlinien Mindestanforderungen Hypothekarfinanzierungen",
                "doc_type": "richtlinie", "topics": ["Hypothekarmarkt"], "year": year or "2023"}

    if _PLEDGE_CREDIT_RE.search(title):
        return {"doc_number": "SBVg Richtlinien Grundpfandgesicherte Kredite",
                "doc_type": "richtlinie", "topics": ["Hypothekarmarkt"], "year": year or "2023"}

    if re.search(r"Richtlinien?|Directives?|Direttiv|Guidelines?", title, re.I):
        base = _EDITION_YEAR_SUFFIX_RE.sub("", title)
        return {"doc_number": f"SBVg {base}", "doc_type": "richtlinie",
                "topics": ["Selbstregulierung"], "year": year}

    # Anything else not matched above: indexed as a plain report rather
    # than dropped — better a generic doc_type than losing the document.
    base = _EDITION_YEAR_SUFFIX_RE.sub("", title)
    return {"doc_number": f"SBVg {base}", "doc_type": "bericht",
            "topics": ["Selbstregulierung"], "year": year}


def _year_suffix(title: str) -> str:
    m = _EDITION_YEAR_SUFFIX_RE.search(title)
    return m.group(1) if m else ""


class SbvgScraper(PracticeScraper):
    SOURCE_KEY = "sbvg"
    ISSUING_AUTHORITY = "SBVg"
    DEFAULT_DOC_TYPE = "richtlinie"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['sb_id']}"

    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if not doc["date"]:
            doc["date"] = first_date_iso(body_text[:2500])
        return doc

    def discover_documents(self) -> Iterator[dict]:
        # Global across ALL groups, not just within one: the VSB/CDB text is
        # linked identically from both the selbstregulierung and the
        # geldwaeschereibekaempfung page, and a per-group set let it through
        # twice (same content_hash, two doc_ids) when this was first tested.
        seen_urls: set[str] = set()
        for group_key, urls in PAGE_GROUPS:
            for lang in ("de", "fr", "it", "en"):
                url = urls.get(lang)
                if not url:
                    continue
                html = self._page(url)
                if html is None:
                    continue
                tiles = parse_pdf_links(html)
                logger.info("[%s] %s %s: %d pdf tiles", self.SOURCE_KEY, group_key, lang, len(tiles))
                for t in tiles:
                    if t["url"] in seen_urls:
                        continue  # already claimed under an earlier language or group
                    seen_urls.add(t["url"])
                    info = classify_sbvg_pdf(t["title"])
                    if info is None:
                        continue
                    key = slugify(unquote(t["url"].rsplit("/", 1)[-1]))[:60]
                    yield {
                        "pdf_url": t["url"],
                        "url": url,
                        "title": t["title"],
                        "doc_number": info["doc_number"],
                        "date": info["year"],
                        "language": lang,
                        "doc_type": info["doc_type"],
                        "topics": [*info["topics"], info["doc_number"]],
                        "sb_id": f"{group_key}_{key}_{lang}",
                    }
