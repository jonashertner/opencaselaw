"""Fedlex Materialien discovery — SPARQL queries for Acts (Botschaft enactments)
and Consultations (Vernehmlassungen).

Phase 1 of the Materialien build (per memory note ``materialien_build_commitment``).
Extends the statute scraper without touching it — separate module so the
working statute pipeline stays unaffected during iteration.

Fedlex publishes Materialien across multiple ELI namespaces, all queryable
via the same SPARQL endpoint we already use for statutes:

    eli/oc/<orig_AS_ref>           — Act (Botschaft enactment publication)
    eli/dl/proj/<year>/<n>/cons_N  — Consultation (Vernehmlassung)
    eli/pudocc/...                 — PublicationProcess metadata

This module provides the discovery layer:

    discover_acts(sr_numbers=None)         → iterator of Act metadata dicts
    discover_consultations(sr_numbers=None)→ iterator of Consultation dicts
    fetch_manifestations(eli_uri)          → multilingual PDF/HTML/XML URLs

Output is JSONL (raw scrape store, append-only — same convention as the
``decisions/*.jsonl`` shards). The downstream build script
(``search_stack/build_materialien_db.py``, to be written in Phase 2)
ingests these JSONLs into the ``materialien_doc`` table and runs the
Akoma Ntoso / pdfplumber / FTS5 pipeline.

CLI:

    python -m scrapers.fedlex_materialien discover-acts \\
        --sr 220,210,311.0 --output output/raw/materialien/acts.jsonl

    python -m scrapers.fedlex_materialien discover-consultations \\
        --output output/raw/materialien/consultations.jsonl

    python -m scrapers.fedlex_materialien manifestations \\
        --eli https://fedlex.data.admin.ch/eli/oc/27/317_321_377
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fedlex_materialien")

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

# JOLUX is the Fedlex ontology (also used by Luxembourg and the EU
# Publications Office). Predicates we need:
JOLUX = "http://data.legilux.public.lu/resource/ontology/jolux#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
# Fedlex follows ELI conventions and reuses Dublin Core Terms for
# canonical identifiers — Expression-level dct:identifier carries the
# short citation string ("AS 2025 615" / "RO 2025 615" / "RU 2025 615")
# that lawyers use in briefs. NOT jolux:identifier (which exists on the
# Act/Work level but doesn't carry the AS/BBl reference string).
DCT = "http://purl.org/dc/terms/"

# Language URIs as used throughout Fedlex
LANG_URIS = {
    "de": "http://publications.europa.eu/resource/authority/language/DEU",
    "fr": "http://publications.europa.eu/resource/authority/language/FRA",
    "it": "http://publications.europa.eu/resource/authority/language/ITA",
}

REQUEST_DELAY = 0.3
DEFAULT_TIMEOUT = 120

session = requests.Session()
session.headers.update({
    "User-Agent": "OpenCaseLaw/1.0 (materialien scraper; +https://opencaselaw.ch)",
    "Accept": "application/sparql-results+json",
})


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class ActRealization:
    """One per-language Expression of an Act. Holds the per-language title
    and the memorial reference (the AS / BBl publication coordinates).

    Identifier note: ``identifier`` carries the canonical short citation
    string ("AS 2025 615" / "RO 2025 615" / "RU 2025 615") and is
    populated for ALL Acts (modern AND historical). For pre-1947 Acts
    the legacy memorial coordinates (memorial_year + memorial_page)
    also describe a unique location; for post-1947 Acts memorial_page
    is no longer used by Fedlex — the ``identifier`` field is the
    canonical reference.
    """
    language: str                       # 'de' | 'fr' | 'it'
    title: str | None = None            # e.g. "Bundesgesetz vom 30. März 1911 …"
    title_alternative: str | None = None  # e.g. "OR" / "CO"
    title_short: str | None = None      # e.g. "RLSV" / "OSITC"
    memorial_name: str | None = None    # e.g. "AS" / "RO" / "RU"
    memorial_year: str | None = None    # e.g. "27" (pre-1947 vol) or "2025" (calendar year)
    memorial_number: str | None = None  # rarely populated
    memorial_page: str | None = None    # populated for legacy Acts only
    identifier: str | None = None       # canonical citation: "AS 2025 615"
    pdf_url: str | None = None          # constructed from ELI URI


@dataclass
class ActRecord:
    """An Act = Botschaft-enactment Official Compilation entry.

    Maps to ``materialien_doc`` rows with kind='act'. Multiple Acts can
    target the same SR number (one per amendment cycle through history) —
    v0.2 currently only pulls the original ``basicAct``; per-amendment Acts
    will arrive in v0.3 via the ``amendment_refs`` table join.
    """
    eli_uri: str
    sr_number: str | None = None        # joined via the work URI
    work_uri: str | None = None         # eli/cc/... the consolidated law
    date_document: str | None = None
    date_entry_in_force: str | None = None
    publication_date: str | None = None
    process_type: str | None = None
    type_document: str | None = None
    legal_resource_genre: str | None = None
    is_part_of: str | None = None       # eli/collection/oc/<vol>/<...>
    realizations: list[ActRealization] = field(default_factory=list)


@dataclass
class ConsultationRealization:
    """Per-language Vernehmlassung event metadata."""
    language: str                        # 'de' | 'fr' | 'it'
    title: str | None = None
    description: str | None = None


@dataclass
class ConsultationRecord:
    """A Consultation = Vernehmlassung entry on a draft law.

    Language-independent fields are at the top level; per-language
    titles + descriptions live in `realizations`. Same dedup pattern
    as ActRecord (one record per ELI URI, list of per-language rows
    inside).
    """
    eli_uri: str
    sr_number: str | None = None
    impacts_work_uri: str | None = None  # the law it concerns
    status: str | None = None
    consultation_id: str | None = None
    realizations: list[ConsultationRealization] = field(default_factory=list)


@dataclass
class Manifestation:
    """One downloadable file for a given expression+format+language."""
    file_url: str
    format: str        # 'pdf' | 'html' | 'xml' (Akoma Ntoso) | 'epub' | etc.
    language: str      # 'de' | 'fr' | 'it'


# ── SPARQL execution ────────────────────────────────────────────────


def sparql_query(query: str, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """POST to the Fedlex SPARQL endpoint, return result bindings.

    POST (not GET) because the endpoint returns 400 for GETs longer than
    ~4-6 kB — same defensive pattern used in scrapers/fedlex.py.
    """
    resp = session.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def _val(binding: dict, key: str) -> str | None:
    """Safely extract a SPARQL binding value."""
    cell = binding.get(key)
    return cell["value"] if cell else None


# ── Acts discovery (eli/oc/... namespace) ────────────────────────────


def _canonical_act_pdf_url(act_eli_uri: str, language: str) -> str:
    """Construct the canonical Fedlex PDF URL for an Act in a given language.

    Fedlex serves Acts at a content-negotiation URL pattern; the archival
    Type-A PDF (signed, long-term-preservation) is what citizens get from
    the federal-law search. We don't ask SPARQL for these — the URL is
    fully derivable from the ELI URI.

    Example:
      eli/oc/27/317_321_377  +  'de'
      → https://www.fedlex.admin.ch/eli/oc/27/317_321_377/de/pdf-a
    """
    # Strip any language suffix the caller may have included
    base = act_eli_uri.rstrip("/")
    if base.endswith(f"/{language}"):
        base = base[: -len(f"/{language}")]
    base_user = base.replace(
        "fedlex.data.admin.ch", "www.fedlex.admin.ch", 1
    )
    return f"{base_user}/{language}/pdf-a"


def discover_acts(sr_numbers: list[str] | None = None) -> Iterator[ActRecord]:
    """Yield ActRecord entries for every Botschaft-enactment Act
    referenced by the given SR numbers (or ALL classified compilation
    laws if sr_numbers is None).

    Acts are reached via the basicAct relation from a ConsolidationAbstract:

        ?work jolux:basicAct ?act .

    Per-amendment Acts (each revision had its own message) are NOT yet
    pulled here — the basicAct only points to the ORIGINAL act. v0.3 will
    add amendment-level Acts via the existing ``materialien.amendment_refs``
    table (which already has 83,958 BBl/AS pointers per article).

    Implementation note: per-language metadata (title, memorial coords)
    lives on the Act's Expressions, not the Act itself. Per the Fedlex
    JOLUX schema, titles are language-dependent so they hang off the
    Expression resource. We aggregate them under one ActRecord with a
    list of ActRealization rows so the JSONL output is one-Act-per-line.
    """
    sr_filter = ""
    if sr_numbers:
        sr_list = ", ".join(f'"{sr}"' for sr in sr_numbers)
        sr_filter = f"FILTER(?srNumber IN ({sr_list}))"

    # Single query that joins Acts with ALL their Expression metadata so
    # we don't have to do N+1 round-trips. GROUP_CONCAT collapses the
    # per-language rows into one Act row; we re-split client-side.
    query = f"""
    PREFIX jolux: <{JOLUX}>
    PREFIX dct: <{DCT}>
    SELECT
        ?work ?srNumber ?act ?dateDoc ?dateEif
        ?publicationDate ?processType ?typeDoc ?genre ?isPartOf
        ?expr ?lang ?title ?titleAlt ?titleShort ?identifier
        ?memName ?memYear ?memNumber ?memPage
    WHERE {{
      ?work a jolux:ConsolidationAbstract .
      ?work jolux:historicalLegalId ?srNumber .
      ?work jolux:basicAct ?act .
      {sr_filter}
      OPTIONAL {{ ?act jolux:dateDocument ?dateDoc }}
      OPTIONAL {{ ?act jolux:dateEntryInForce ?dateEif }}
      OPTIONAL {{ ?act jolux:publicationDate ?publicationDate }}
      OPTIONAL {{ ?act jolux:processType ?processType }}
      OPTIONAL {{ ?act jolux:typeDocument ?typeDoc }}
      OPTIONAL {{ ?act jolux:legalResourceGenre ?genre }}
      OPTIONAL {{ ?act jolux:isPartOf ?isPartOf }}
      OPTIONAL {{
        ?act jolux:isRealizedBy ?expr .
        OPTIONAL {{ ?expr jolux:language ?lang }}
        OPTIONAL {{ ?expr jolux:title ?title }}
        OPTIONAL {{ ?expr jolux:titleAlternative ?titleAlt }}
        OPTIONAL {{ ?expr jolux:titleShort ?titleShort }}
        OPTIONAL {{ ?expr dct:identifier ?identifier }}
        OPTIONAL {{ ?expr jolux:memorialName ?memName }}
        OPTIONAL {{ ?expr jolux:memorialYear ?memYear }}
        OPTIONAL {{ ?expr jolux:memorialNumber ?memNumber }}
        OPTIONAL {{ ?expr jolux:memorialPage ?memPage }}
      }}
    }}
    """
    rows = sparql_query(query, timeout=600)

    # Group by Act ELI URI — multiple rows per Act (one per Expression).
    by_act: dict[str, ActRecord] = {}
    for r in rows:
        act_uri = _val(r, "act")
        if not act_uri:
            continue
        if act_uri not in by_act:
            by_act[act_uri] = ActRecord(
                eli_uri=act_uri,
                sr_number=_val(r, "srNumber"),
                work_uri=_val(r, "work"),
                date_document=_val(r, "dateDoc"),
                date_entry_in_force=_val(r, "dateEif"),
                publication_date=_val(r, "publicationDate"),
                process_type=_val(r, "processType"),
                type_document=_val(r, "typeDoc"),
                legal_resource_genre=_val(r, "genre"),
                is_part_of=_val(r, "isPartOf"),
            )
        # Collect per-Expression realization
        lang_uri = _val(r, "lang") or ""
        lang_code = next(
            (k for k, v in LANG_URIS.items() if v == lang_uri),
            lang_uri.rsplit("/", 1)[-1].lower()[:3] if lang_uri else "",
        )
        if not lang_code:
            continue
        # Dedup on (act_uri, language) — same expression may appear once
        existing_langs = {rz.language for rz in by_act[act_uri].realizations}
        if lang_code in existing_langs:
            continue
        by_act[act_uri].realizations.append(ActRealization(
            language=lang_code,
            title=_val(r, "title"),
            title_alternative=_val(r, "titleAlt"),
            title_short=_val(r, "titleShort"),
            identifier=_val(r, "identifier"),
            memorial_name=_val(r, "memName"),
            memorial_year=_val(r, "memYear"),
            memorial_number=_val(r, "memNumber"),
            memorial_page=_val(r, "memPage"),
            pdf_url=_canonical_act_pdf_url(act_uri, lang_code),
        ))

    log.info(
        "Discovered %d Acts via SPARQL (%d Expression rows aggregated)",
        len(by_act), len(rows),
    )
    yield from by_act.values()


# ── Consultations discovery (eli/dl/proj/... namespace) ─────────────


def _detect_language(text: str | None) -> str | None:
    """Heuristic language guess for Consultation titles/descriptions.

    Fedlex returns multiple title rows per Consultation but does NOT tag
    them with a language URI on the Consultation itself (unlike Acts,
    where the Expression layer is explicitly per-language). So we detect
    DE / FR / IT from text content. Crude but reliable for legal-domain
    German, French, Italian — they have very different stopword sets.
    """
    if not text:
        return None
    sample = text[:300].lower()
    de_score = sum(1 for w in (" der ", " die ", " und ", " für ", " ist ",
                                " des ", " mit ", " ein ", " einer ", " im ",
                                "ung ", "lich ") if w in sample)
    fr_score = sum(1 for w in (" le ", " la ", " les ", " de ", " des ",
                                " et ", " pour ", " une ", " sur ", " est ",
                                "ent ", "tion ") if w in sample)
    it_score = sum(1 for w in (" il ", " la ", " le ", " di ", " del ",
                                " della ", " per ", " un ", " una ", " sui ",
                                "zione", "mento") if w in sample)
    scores = [("de", de_score), ("fr", fr_score), ("it", it_score)]
    best = max(scores, key=lambda x: x[1])
    return best[0] if best[1] >= 2 else None


def discover_consultations(sr_numbers: list[str] | None = None) -> Iterator[ConsultationRecord]:
    """Yield ConsultationRecord entries for Vernehmlassungen.

    Consultations are linked to the law they concern via:

        ?cons jolux:foreseenImpactToLegalResource ?work .

    Where ?work is an eli/cc/... ConsolidationAbstract. We optionally
    join through to its SR number for filtering.

    v0.3: deduplicates rows by ELI URI and aggregates per-language titles
    + descriptions into ConsultationRecord.realizations — same pattern as
    discover_acts. Earlier versions returned one record per row, which
    yielded ~9× duplication (eg. 288 SPARQL rows / 32 unique consultations
    for OR alone) because Fedlex returns one row per (title, description)
    Cartesian product over the multilingual events.
    """
    sr_filter = ""
    if sr_numbers:
        sr_list = ", ".join(f'"{sr}"' for sr in sr_numbers)
        sr_filter = f"""
        ?work jolux:historicalLegalId ?srNumber .
        FILTER(?srNumber IN ({sr_list}))
        """
    else:
        sr_filter = "OPTIONAL { ?work jolux:historicalLegalId ?srNumber }"

    query = f"""
    PREFIX jolux: <{JOLUX}>
    SELECT DISTINCT
        ?cons ?title ?description ?status ?eventId ?work ?srNumber
    WHERE {{
      ?cons a jolux:Consultation .
      ?cons jolux:foreseenImpactToLegalResource ?work .
      {sr_filter}
      OPTIONAL {{ ?cons jolux:eventTitle ?title }}
      OPTIONAL {{ ?cons jolux:eventDescription ?description }}
      OPTIONAL {{ ?cons jolux:consultationStatus ?status }}
      OPTIONAL {{ ?cons jolux:eventId ?eventId }}
    }}
    """
    rows = sparql_query(query, timeout=600)

    # Aggregate: one ConsultationRecord per ELI URI, with realizations
    # inside. Same dedup pattern as discover_acts.
    by_cons: dict[str, ConsultationRecord] = {}
    seen_titles: dict[tuple[str, str], None] = {}  # (eli_uri, title)
    for r in rows:
        eli_uri = _val(r, "cons")
        if not eli_uri:
            continue
        if eli_uri not in by_cons:
            by_cons[eli_uri] = ConsultationRecord(
                eli_uri=eli_uri,
                sr_number=_val(r, "srNumber"),
                impacts_work_uri=_val(r, "work"),
                status=_val(r, "status"),
                consultation_id=_val(r, "eventId"),
            )
        title = _val(r, "title")
        description = _val(r, "description")
        if not title:
            continue
        # Dedup on (eli_uri, title) — Fedlex returns one row per
        # (title × description) cross-product.
        key = (eli_uri, title)
        if key in seen_titles:
            continue
        seen_titles[key] = None
        lang = _detect_language(title) or _detect_language(description)
        if lang is None:
            continue
        existing_langs = {rz.language for rz in by_cons[eli_uri].realizations}
        if lang in existing_langs:
            continue
        by_cons[eli_uri].realizations.append(ConsultationRealization(
            language=lang,
            title=title,
            description=description,
        ))

    log.info(
        "Discovered %d Consultations via SPARQL (%d raw rows aggregated)",
        len(by_cons), len(rows),
    )
    yield from by_cons.values()


def discover_amendment_acts(
    years: list[int] | None = None,
    memorial_names: list[str] | None = None,
) -> Iterator[ActRecord]:
    """Discover ALL Acts in the Fedlex Official Compilation, not just the
    basicActs. These include amendment Acts — every revision of a federal
    law had its own Botschaft/AS publication, and they live in the same
    eli/oc/ namespace as the original enactment.

    Strategy: walk the Expression layer (where memorial coordinates live)
    rather than the Act layer (which has fewer attributes). Filter by
    memorial_year so callers can scope by amendment date range.

    NOTE: this returns Acts WITHOUT the SR-number link. The connection
    "this amendment Act modified law SR X" comes from the existing
    materialien.amendment_refs table — Phase 2's build script does that
    cross-join. We don't try to resolve it here because no JOLUX predicate
    exposes it directly.

    Args:
        years: optional list of memorial years to filter on. Default: all.
        memorial_names: subset of ['AS', 'RO', 'RU']. Default: all three —
            so each Act gets DE/FR/IT realizations. v0.3 defaulted to
            'AS' only and missed FR + IT (~2/3 of per-language metadata).
    """
    if memorial_names is None:
        memorial_names = ["AS", "RO", "RU"]
    # Note on the year filter: ?memYear is a typed literal (xsd:gYear) in
    # Fedlex's RDF, so a plain ``?memYear IN ("2024")`` returns 0 rows.
    # STR(?memYear) coerces to a plain string for the comparison.
    year_filter = ""
    if years:
        year_list = ", ".join(f'"{y}"' for y in years)
        year_filter = f"FILTER(STR(?memYear) IN ({year_list}))"

    mem_list = ", ".join(f'"{m}"' for m in memorial_names)
    mem_filter = f"FILTER(?memName IN ({mem_list}))"

    # Note on the resource-type filter: ``?act a jolux:Act`` over-filtered
    # in production (some amendment publications carry only the inferred
    # type). The strstarts on the eli/oc/ namespace is restrictive enough.
    query = f"""
    PREFIX jolux: <{JOLUX}>
    PREFIX dct: <{DCT}>
    SELECT
        ?act ?dateDoc ?dateEif ?publicationDate ?processType
        ?typeDoc ?genre ?isPartOf
        ?expr ?lang ?title ?titleAlt ?titleShort ?identifier
        ?memName ?memYear ?memNumber ?memPage
    WHERE {{
      ?expr jolux:memorialName ?memName .
      {mem_filter}
      ?expr jolux:memorialYear ?memYear .
      {year_filter}
      OPTIONAL {{ ?expr jolux:memorialNumber ?memNumber }}
      OPTIONAL {{ ?expr jolux:memorialPage ?memPage }}
      ?act jolux:isRealizedBy ?expr .
      OPTIONAL {{ ?expr jolux:language ?lang }}
      OPTIONAL {{ ?expr jolux:title ?title }}
      OPTIONAL {{ ?expr jolux:titleAlternative ?titleAlt }}
      OPTIONAL {{ ?expr jolux:titleShort ?titleShort }}
      OPTIONAL {{ ?expr dct:identifier ?identifier }}
      OPTIONAL {{ ?act jolux:dateDocument ?dateDoc }}
      OPTIONAL {{ ?act jolux:dateEntryInForce ?dateEif }}
      OPTIONAL {{ ?act jolux:publicationDate ?publicationDate }}
      OPTIONAL {{ ?act jolux:processType ?processType }}
      OPTIONAL {{ ?act jolux:typeDocument ?typeDoc }}
      OPTIONAL {{ ?act jolux:legalResourceGenre ?genre }}
      OPTIONAL {{ ?act jolux:isPartOf ?isPartOf }}
      FILTER(strstarts(str(?act), "https://fedlex.data.admin.ch/eli/oc/"))
    }}
    """
    rows = sparql_query(query, timeout=600)

    by_act: dict[str, ActRecord] = {}
    for r in rows:
        act_uri = _val(r, "act")
        if not act_uri:
            continue
        if act_uri not in by_act:
            by_act[act_uri] = ActRecord(
                eli_uri=act_uri,
                sr_number=None,        # NOT joined — see docstring
                work_uri=None,
                date_document=_val(r, "dateDoc"),
                date_entry_in_force=_val(r, "dateEif"),
                publication_date=_val(r, "publicationDate"),
                process_type=_val(r, "processType"),
                type_document=_val(r, "typeDoc"),
                legal_resource_genre=_val(r, "genre"),
                is_part_of=_val(r, "isPartOf"),
            )
        lang_uri = _val(r, "lang") or ""
        lang_code = next(
            (k for k, v in LANG_URIS.items() if v == lang_uri),
            lang_uri.rsplit("/", 1)[-1].lower()[:3] if lang_uri else "",
        )
        if not lang_code:
            continue
        existing_langs = {rz.language for rz in by_act[act_uri].realizations}
        if lang_code in existing_langs:
            continue
        by_act[act_uri].realizations.append(ActRealization(
            language=lang_code,
            title=_val(r, "title"),
            title_alternative=_val(r, "titleAlt"),
            title_short=_val(r, "titleShort"),
            identifier=_val(r, "identifier"),
            memorial_name=_val(r, "memName"),
            memorial_year=_val(r, "memYear"),
            memorial_number=_val(r, "memNumber"),
            memorial_page=_val(r, "memPage"),
            pdf_url=_canonical_act_pdf_url(act_uri, lang_code),
        ))

    log.info(
        "Discovered %d amendment Acts via SPARQL (%d Expression rows; "
        "memorial_names=%s years=%s)",
        len(by_act), len(rows), memorial_names, years or "all",
    )
    yield from by_act.values()


# ── Direct Botschaft discovery (typeDocument=23) ────────────────────


# Fedlex internal vocabulary — ID 23 in the resource-type taxonomy
# encodes a Botschaft / Message / Messaggio (Federal Council Message
# accompanying a draft law). Other IDs are noise for our purposes:
# 8 = Bundesbeschluss (decree), 33 = Mitteilung (notice), etc.
BOTSCHAFT_TYPE_DOC_URI = (
    "https://fedlex.data.admin.ch/vocabulary/resource-type/23"
)

# The Bundesblatt Materialien types we ingest, keyed by Fedlex's numeric
# resource-type id. The slug is what ``botschaft_documents.doc_type`` stores
# (stable, ASCII, never shown raw to users — the labels below are). Counts
# are DE works with a file on fedlex.data.admin.ch, probed 2026-09-09:
#   23 Botschaft des Bundesrates               2,055
#   24 Bericht des Bundesrates                   196
#   25 Stellungnahme des Bundesrates             531
#   30 Bericht parlamentarische Kommission       754
#   38 Erlassentwurf (2021+ only)                623
#   39 Vorentwurf Erlass                           0  (no FGA work carries it)
#   40 Eröffnung des Vernehmlassungsverfahrens 1,420
# NOT ingested: 33 Beschlüsse der Behörde (44,615 notices), ordinances,
# decrees — noise for legislative-history research.
FGA_DOC_TYPES: dict[int, str] = {
    23: "botschaft",
    24: "bericht_bundesrat",
    25: "stellungnahme_bundesrat",
    30: "bericht_kommission",
    38: "erlassentwurf",
    39: "vorentwurf_erlass",
    40: "vernehmlassung_eroeffnung",
}
DEFAULT_DOC_TYPE = "botschaft"

# skos:prefLabel of each resource-type in Fedlex's own vocabulary (queried
# 2026-09-09). These are the only type labels ever shown to a user (R3:
# names come from Fedlex, never synthesised).
FGA_DOC_TYPE_LABELS: dict[str, dict[str, str]] = {
    "botschaft": {
        "de": "Botschaft des Bundesrates",
        "fr": "Message du Conseil fédéral",
        "it": "Messaggio del Consiglio federale",
    },
    "bericht_bundesrat": {
        "de": "Bericht des Bundesrates",
        "fr": "Rapport du Conseil fédéral",
        "it": "Rapporto del Consiglio federale",
    },
    "stellungnahme_bundesrat": {
        "de": "Stellungnahme des Bundesrates",
        "fr": "Avis du Conseil fédéral",
        "it": "Parere del Consiglio federale",
    },
    "bericht_kommission": {
        "de": "Bericht parlamentarische Kommission",
        "fr": "Rapport d'une Commission parlementaire",
        "it": "Rapporto di una Commissione parlamentare",
    },
    "erlassentwurf": {
        "de": "Erlassentwurf",
        "fr": "Projet d'acte",
        "it": "Progetto di atto",
    },
    "vorentwurf_erlass": {
        "de": "Vorentwurf Erlass",
        "fr": "Avant-projet d'acte",
        "it": "Avamprogetto di atto",
    },
    "vernehmlassung_eroeffnung": {
        "de": "Eröffnung des Vernehmlassungsverfahrens",
        "fr": "Ouverture de la procédure de consultation",
        "it": "Avvio della procedura di consultazione",
    },
}


def fga_type_uri(type_id: int) -> str:
    return f"https://fedlex.data.admin.ch/vocabulary/resource-type/{int(type_id)}"


def parse_doc_types(spec: str | None) -> tuple[int, ...]:
    """``"23,24, 30"`` → ``(23, 24, 30)``. Unknown ids raise ValueError so a
    typo in the systemd unit fails loudly instead of ingesting nothing."""
    if not spec or not spec.strip():
        return (23,)
    out: list[int] = []
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        tid = int(tok)
        if tid not in FGA_DOC_TYPES:
            raise ValueError(
                f"Unknown Fedlex document type {tid}; known: "
                f"{sorted(FGA_DOC_TYPES)}"
            )
        if tid not in out:
            out.append(tid)
    return tuple(out) or (23,)


def _fga_candidate(
    act_uri: str | None, mem_page: str | None, title: str
) -> tuple[int, int, str, str] | None:
    """Map a Fedlex ``fga`` act URI + its (optional) ``jolux:memorialPage`` to a
    Botschaft candidate ``(year, citation_page, eli_uri, title)``.

    Issue #30: the citable Bundesblatt page is ``memorialPage`` — the page in the
    print/PDF gazette edition. The trailing ELI segment is Fedlex's internal
    ``fga`` index (a sequence number, sometimes a composite like
    ``1_9194_8542_8123``), NOT the page. When Fedlex has no ``memorialPage``
    (post-2022 Bundesblatt) the segment doubles as the document number, so it is
    the citation. The full ``act_uri`` is preserved for fetching. Returns
    ``None`` if neither source yields a usable integer page.
    """
    parts = (act_uri or "").rstrip("/").rsplit("/", 2)
    if len(parts) < 3:
        return None
    try:
        year = int(parts[-2])
    except ValueError:
        return None
    seg = parts[-1]
    if mem_page is not None:
        try:
            page = int(mem_page)
        except (TypeError, ValueError):
            return None
    elif seg.isdigit():
        # No memorialPage (post-2022 Bundesblatt): the segment is the doc number.
        # ``.isdigit()`` rejects composite ELI segments (e.g. "1_9194_8542_8123"),
        # which int() would otherwise mis-parse via underscore digit grouping.
        page = int(seg)
    else:
        return None
    return (year, page, act_uri, title)


def _clean_title(title: str | None) -> str | None:
    """Fedlex titles verbatim, whitespace only normalised (they carry a
    trailing blank and occasional line breaks). Never rewritten."""
    if not title:
        return None
    t = " ".join(title.split())
    return t or None


def _clean_date(value: str | None) -> str | None:
    """``jolux:dateDocument`` / ``jolux:publicationDate`` are xsd:date
    ("2011-09-07"); keep the date part only, drop a time suffix if any."""
    if not value:
        return None
    v = value.strip()
    return v[:10] if len(v) >= 10 and v[4] == "-" and v[7] == "-" else (v or None)


def _fga_meta_query(
    language: str,
    values_clause: str | None = None,
    type_ids: tuple[int, ...] = (23,),
) -> str:
    """The one SPARQL shape used by discovery and by the backfill.

    Verified on Fedlex 2026-09-09 for eli/fga/2011/1325, 2013/823 and
    2025/960: the Work carries ``jolux:dateDocument`` (the date the
    message is "vom", e.g. 2011-09-07) and ``jolux:publicationDate`` (the
    Bundesblatt issue date, 2011-10-25); the language Expression carries
    ``jolux:title`` and ``jolux:memorialPage``.

    Discovery binds ``?typeDoc`` from a ``VALUES`` list of the requested
    resource types (one round-trip for all of them). The backfill
    (``values_clause``) restricts to the given works and has no typeDocument
    filter at all: the rows already are Botschaften, and some pre-1999 works
    lack the type.
    """
    if values_clause:
        select = "?act ?title ?memPage ?dateDoc ?pubDate"
        subject = f"VALUES ?act {{ {values_clause} }}"
    else:
        select = "?act ?typeDoc ?title ?memPage ?dateDoc ?pubDate"
        values = " ".join(f"<{fga_type_uri(t)}>" for t in type_ids)
        subject = (
            f"VALUES ?typeDoc {{ {values} }}\n"
            "      ?act jolux:typeDocument ?typeDoc .\n"
            '      FILTER(strstarts(STR(?act), "https://fedlex.data.admin.ch/eli/fga/"))'
        )
    return f"""
    PREFIX jolux: <{JOLUX}>
    SELECT DISTINCT {select} WHERE {{
      {subject}
      ?act jolux:isRealizedBy ?expr .
      ?expr jolux:language <{LANG_URIS[language]}> .
      OPTIONAL {{ ?expr jolux:title ?title }}
      OPTIONAL {{ ?expr jolux:memorialPage ?memPage }}
      OPTIONAL {{ ?act jolux:dateDocument ?dateDoc }}
      OPTIONAL {{ ?act jolux:publicationDate ?pubDate }}
    }}
    ORDER BY DESC(?act)
    """


_META_KEYS = ("title", "date_document", "publication_date")


def _fga_meta_from_binding(r: dict) -> dict | None:
    """One discovery row → ``{"year", "page", "eli_uri", "title",
    "date_document", "publication_date"}`` (no type), or None when the URI
    is not a citable FGA work."""
    cand = _fga_candidate(_val(r, "act"), _val(r, "memPage"), _val(r, "title") or "")
    if cand is None:
        return None
    year, page, eli_uri, title = cand
    return {
        "year": year,
        "page": page,
        "eli_uri": eli_uri,
        "title": _clean_title(title),
        "date_document": _clean_date(_val(r, "dateDoc")),
        "publication_date": _clean_date(_val(r, "pubDate")),
    }


def discover_fga_documents(
    language: str = "de",
    doc_types: tuple[int, ...] | list[int] = (23,),
    timeout: int = 120,
) -> list[dict]:
    """Enumerate Bundesblatt publications of the given Fedlex resource types,
    with their metadata.

    One SPARQL round-trip for all requested types (a ``VALUES`` clause), so
    the nightly unit pays the same latency for seven types as for one.
    Returns one dict per FGA work that has an Expression in ``language``::

        {"year": 2011, "page": 7705,
         "eli_uri": "https://fedlex.data.admin.ch/eli/fga/2011/1325",
         "title": "Botschaft …", "doc_type": "botschaft", "type_id": 23,
         "date_document": "2011-09-07", "publication_date": "2011-10-25"}

    ``page`` is the citable memorialPage (issue #30, see ``_fga_candidate``);
    ``title`` is the language-specific ``jolux:title`` verbatim (whitespace
    normalised, None when Fedlex has none); ``date_document`` is
    ``jolux:dateDocument`` (the date the message is dated),
    ``publication_date`` is ``jolux:publicationDate`` (the Bundesblatt
    issue) — either may be None on old records. One row per work: a work
    tagged with several requested types is reported once, under the lowest
    id (Botschaft wins), and a second title/date binding for the same ELI
    only fills fields the first left empty.
    """
    if language not in LANG_URIS:
        raise ValueError(f"Unsupported language: {language!r}")
    type_ids = tuple(dict.fromkeys(int(t) for t in doc_types)) or (23,)
    unknown = [t for t in type_ids if t not in FGA_DOC_TYPES]
    if unknown:
        raise ValueError(f"Unknown Fedlex document type(s): {unknown}")

    rows = sparql_query(_fga_meta_query(language, type_ids=type_ids), timeout=timeout)
    by_uri: dict[str, dict] = {}
    for r in rows:
        type_uri = _val(r, "typeDoc")
        if type_uri is None and len(type_ids) == 1:
            # Single-type query: the binding is implied (and older fixtures
            # / callers never selected it).
            type_id = type_ids[0]
        else:
            try:
                type_id = int((type_uri or "").rsplit("/", 1)[-1])
            except ValueError:
                continue
        slug = FGA_DOC_TYPES.get(type_id)
        if slug is None:
            continue
        meta = _fga_meta_from_binding(r)
        if meta is None:
            continue
        prev = by_uri.get(meta["eli_uri"])
        if prev is None:
            meta["doc_type"], meta["type_id"] = slug, type_id
            by_uri[meta["eli_uri"]] = meta
            continue
        if type_id < prev["type_id"]:
            prev["doc_type"], prev["type_id"] = slug, type_id
        for k in _META_KEYS:
            if not prev[k] and meta[k]:
                prev[k] = meta[k]
    out = list(by_uri.values())
    per_type: dict[str, int] = {}
    for d in out:
        per_type[d["doc_type"]] = per_type.get(d["doc_type"], 0) + 1
    log.info(
        "discover_fga_documents(%s, types=%s): %d works in Fedlex SPARQL (%s)",
        language, ",".join(str(t) for t in type_ids), len(out),
        ", ".join(f"{k}={v}" for k, v in sorted(per_type.items())) or "none",
    )
    return out


def discover_fga_botschaften_meta(
    language: str = "de",
    timeout: int = 120,
) -> list[dict]:
    """Every Bundesblatt-published Botschaft (typeDocument=23) with its
    metadata — :func:`discover_fga_documents` for type 23 only. Each dict
    carries ``year, page, eli_uri, title, date_document, publication_date``
    (plus ``doc_type='botschaft'`` / ``type_id=23``)."""
    return discover_fga_documents(language=language, doc_types=(23,), timeout=timeout)


def discover_fga_botschaften(
    language: str = "de",
    timeout: int = 120,
) -> list[tuple[int, int, str, str]]:
    """Enumerate every Bundesblatt-published Botschaft via Fedlex SPARQL.

    Returns ``[(year, page, eli_uri, title), ...]`` for every FGA URI of
    typeDocument=23 (Botschaft) that has an Expression in the requested
    ``language``. Kept as the type-23 tuple view over
    :func:`discover_fga_documents` so existing callers keep their shape.
    The full SR vocabulary is post-2003; coverage gaps pre-2003 are the
    untyped historical works (:func:`discover_fga_botschaften_untyped`).

    Why this beats amendment_refs as a candidate source:
    amendment_refs reflects what statute footnotes cite — predominantly
    Bundesbeschlüsse (the enacting decree), not the Botschaften that
    explain them. Querying typeDocument directly returns ~2,000 real
    Botschaften (DE) — an 18× expansion vs the 119 we got via
    amendment_refs.
    """
    docs = discover_fga_documents(language=language, doc_types=(23,), timeout=timeout)
    out = [(d["year"], d["page"], d["eli_uri"], d["title"] or "") for d in docs]
    log.info(
        "discover_fga_botschaften(%s): %d Botschaften in Fedlex SPARQL",
        language, len(out),
    )
    return out


def fetch_fga_metadata(
    eli_uris: list[str],
    language: str = "de",
    timeout: int = 120,
) -> dict[str, dict]:
    """Title + dates for a batch of FGA Works, keyed by ELI URI.

    One SPARQL round trip per call (``VALUES`` list; callers batch ~50).
    Returns ``{eli_uri: {"title", "date_document", "publication_date"}}``
    for every URI Fedlex answered; URIs without an Expression in
    ``language`` are absent from the result.
    """
    if language not in LANG_URIS:
        raise ValueError(f"Unsupported language: {language!r}")
    uris = [u for u in dict.fromkeys(eli_uris) if u]
    if not uris:
        return {}
    values = " ".join(f"<{u}>" for u in uris)
    rows = sparql_query(_fga_meta_query(language, values_clause=values), timeout=timeout)
    out: dict[str, dict] = {}
    for r in rows:
        eli = _val(r, "act")
        if not eli:
            continue
        meta = {
            "title": _clean_title(_val(r, "title")),
            "date_document": _clean_date(_val(r, "dateDoc")),
            "publication_date": _clean_date(_val(r, "pubDate")),
        }
        cur = out.get(eli)
        if cur is None:
            out[eli] = meta
        else:
            for k, v in meta.items():
                if not cur[k] and v:
                    cur[k] = v
    return out


# ── Manifestations (downloadable files) ─────────────────────────────


def fetch_manifestations(eli_uri: str) -> list[Manifestation]:
    """For one Act / Consultation / any ELI URI, return the multilingual
    file URLs across all formats Fedlex publishes.

    Fedlex's FRBR layering: Work → Expression (per language) →
    Manifestation (per format) → Item (the actual file URL).
    """
    query = f"""
    PREFIX jolux: <{JOLUX}>
    SELECT DISTINCT ?fmt ?lang ?file WHERE {{
      <{eli_uri}> jolux:isRealizedBy ?expr .
      ?expr jolux:isEmbodiedBy ?manif .
      OPTIONAL {{ ?expr jolux:language ?lang . }}
      OPTIONAL {{ ?manif jolux:userFormat ?fmt . }}
      OPTIONAL {{ ?manif jolux:isExemplifiedBy ?file . }}
    }}
    """
    rows = sparql_query(query, timeout=60)
    out: list[Manifestation] = []
    for r in rows:
        file_url = _val(r, "file")
        if not file_url:
            continue
        fmt_uri = _val(r, "fmt") or ""
        lang_uri = _val(r, "lang") or ""
        # Translate URIs to short codes
        fmt = fmt_uri.rsplit("/", 1)[-1].lower() if fmt_uri else "unknown"
        lang_code = next(
            (k for k, v in LANG_URIS.items() if v == lang_uri),
            lang_uri.rsplit("/", 1)[-1].lower()[:3],
        )
        out.append(Manifestation(file_url=file_url, format=fmt, language=lang_code))
    return out


# ── JSONL output ─────────────────────────────────────────────────────


def write_jsonl(records: Iterator, path: Path) -> int:
    """Write dataclass records as one-JSON-per-line. Returns count.

    ActRecord.realizations is a list of ActRealization dataclasses;
    asdict() recurses into them so the JSONL line carries full per-
    language metadata (title, memorial coords, pdf_url) inline.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fedlex Materialien discovery (Phase 1)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_acts = sub.add_parser("discover-acts", help="SPARQL → Act metadata JSONL")
    p_acts.add_argument(
        "--sr", default=None,
        help="Comma-separated SR numbers (e.g. 220,210,311.0). Default: all laws.",
    )
    p_acts.add_argument(
        "--output", default="output/raw/materialien/acts.jsonl",
        help="Output JSONL path",
    )

    p_cons = sub.add_parser(
        "discover-consultations", help="SPARQL → Consultation metadata JSONL",
    )
    p_cons.add_argument("--sr", default=None)
    p_cons.add_argument(
        "--output", default="output/raw/materialien/consultations.jsonl",
    )

    p_amend = sub.add_parser(
        "discover-amendment-acts",
        help="SPARQL → ALL Acts in eli/oc/ namespace (incl. amendments) with metadata",
    )
    p_amend.add_argument(
        "--years", default=None,
        help="Comma-separated memorial years to filter on (e.g. 2020,2021,2022). Default: all.",
    )
    p_amend.add_argument(
        "--memorial-names", default="AS,RO,RU",
        help="Comma-separated subset of {AS, RO, RU} (default: all three) — "
             "the memorial-language code of the Expression. v0.4 default "
             "captures DE+FR+IT realizations per Act; restrict to one to "
             "speed the SPARQL query at the cost of 2/3 of the metadata.",
    )
    p_amend.add_argument(
        "--output", default="output/raw/materialien/amendment_acts.jsonl",
    )

    p_man = sub.add_parser(
        "manifestations", help="Print downloadable file URLs for one ELI URI",
    )
    p_man.add_argument("--eli", required=True, help="Fully-qualified ELI URI")

    args = parser.parse_args(argv)

    if args.cmd == "discover-acts":
        sr_list = [s.strip() for s in args.sr.split(",")] if args.sr else None
        n = write_jsonl(discover_acts(sr_list), Path(args.output))
        log.info("Wrote %d Acts → %s", n, args.output)
        return 0

    if args.cmd == "discover-consultations":
        sr_list = [s.strip() for s in args.sr.split(",")] if args.sr else None
        n = write_jsonl(discover_consultations(sr_list), Path(args.output))
        log.info("Wrote %d Consultations → %s", n, args.output)
        return 0

    if args.cmd == "discover-amendment-acts":
        years_list = (
            [int(y.strip()) for y in args.years.split(",")] if args.years else None
        )
        mem_list = [m.strip() for m in args.memorial_names.split(",") if m.strip()]
        n = write_jsonl(
            discover_amendment_acts(years=years_list, memorial_names=mem_list),
            Path(args.output),
        )
        log.info("Wrote %d amendment Acts → %s", n, args.output)
        return 0

    if args.cmd == "manifestations":
        manifs = fetch_manifestations(args.eli)
        for m in manifs:
            print(f"  [{m.language} / {m.format:6s}] {m.file_url}")
        log.info("Found %d manifestations for %s", len(manifs), args.eli)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
