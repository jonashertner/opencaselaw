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
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, NamedTuple
from urllib.parse import urlparse

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


def discover_fga_botschaften(
    language: str = "de",
    timeout: int = 120,
) -> list[tuple[int, int, str, str]]:
    """Enumerate every Bundesblatt-published Botschaft via Fedlex SPARQL.

    Returns ``[(year, page, title), ...]`` for every FGA URI of
    typeDocument=23 (Botschaft) that has an Expression in the requested
    ``language``. The full SR vocabulary is post-2003; coverage gaps
    pre-2003 need the v0.5 amtsdruckschriften adapter.

    Why this beats amendment_refs as a candidate source:
    amendment_refs reflects what statute footnotes cite — predominantly
    Bundesbeschlüsse (the enacting decree), not the Botschaften that
    explain them. Querying typeDocument directly returns ~2,000 real
    Botschaften (DE) — an 18× expansion vs the 119 we got via
    amendment_refs.
    """
    if language not in LANG_URIS:
        raise ValueError(f"Unsupported language: {language!r}")

    query = f"""
    PREFIX jolux: <{JOLUX}>
    SELECT DISTINCT ?act ?title ?memPage WHERE {{
      ?act jolux:typeDocument <{BOTSCHAFT_TYPE_DOC_URI}> .
      ?act jolux:isRealizedBy ?expr .
      ?expr jolux:language <{LANG_URIS[language]}> .
      OPTIONAL {{ ?expr jolux:title ?title }}
      OPTIONAL {{ ?expr jolux:memorialPage ?memPage }}
      FILTER(strstarts(STR(?act), "https://fedlex.data.admin.ch/eli/fga/"))
    }}
    ORDER BY DESC(?act)
    """
    rows = sparql_query(query, timeout=timeout)
    out: list[tuple[int, int, str, str]] = []
    for r in rows:
        # bbl page = memorialPage (print/PDF edition); ELI segment is Fedlex's
        # internal fga index, not the page (issue #30).
        cand = _fga_candidate(_val(r, "act"), _val(r, "memPage"), _val(r, "title") or "")
        if cand is not None:
            out.append(cand)
    log.info(
        "discover_fga_botschaften(%s): %d Botschaften in Fedlex SPARQL",
        language, len(out),
    )
    return out


# ── Untyped historical Botschaften (1850-1999, BAR PDF scans) ───────

# Fedlex holds ~20,000 further FGA works whose title starts with
# "Botschaft" / "Message" / "Messaggio" but which carry NO typeDocument
# (the type vocabulary was applied from ~1999 on). Their only file is a
# PDF with an OCR text layer served by the Bundesarchiv's
# Amtsdruckschriften portal (``www.amtsdruckschriften.bar.admin.ch``),
# reached through the manifestation's jolux:isExemplifiedBy URL.
#
# The ELI of these works is ``eli/fga/<year>/<vol>_<pDE>_<pFR>_<pIT>``:
# the first segment is the Bundesblatt volume (BBl 1975 I 609 →
# ``1975/1_609__``), the following segments are the first page of the
# document in the German, French and Italian editions (empty when that
# edition has no expression). Verified 2026-09-09 on samples from 1850,
# 1935, 1955, 1975 and 1998 against ``jolux:memorialPage`` on the
# language expression, which is the authoritative page and is used first.
_FGA_HISTORICAL_ELI_RE = re.compile(
    r"/eli/fga/(?P<year>\d{4})/(?P<vol>\d+)_(?P<de>\d*)_(?P<fr>\d*)_?(?P<it>\d*)/?$"
)
_BAR_HOST = "www.amtsdruckschriften.bar.admin.ch"
_FEDLEX_FILE_HOST = "fedlex.data.admin.ch"
_BOTSCHAFT_TITLE_PREFIX = {"de": "botschaft", "fr": "message", "it": "messaggio"}


class UntypedBotschaft(NamedTuple):
    """One historical (untyped) Botschaft candidate.

    The first six fields are the contract named in the sprint brief
    ``(year, page, title, eli_uri, file_url, file_host)``; the rest carry
    the Fedlex metadata the ingest stores verbatim (citation string from
    ``dct:identifier``, publication date, volume).
    """
    year: int
    page: int
    title: str
    eli_uri: str
    file_url: str
    file_host: str          # 'bar' | 'fedlex' | other netloc
    # "BBl 1975 I 609" / "FF 1998 I 205": Fedlex's dct:identifier, else its
    # jolux:historicalLegalId (both carry the same string on the language
    # expression; probed 2026-09-09). None when Fedlex has neither — never
    # assembled from year / volume / page (R1-R3); the parts stay separate.
    citation: str | None
    publication_date: str | None
    volume: int | None
    language: str


def parse_historical_fga_eli(eli_uri: str) -> dict | None:
    """Split ``eli/fga/1975/1_609__`` into year, volume and per-language pages.

    Returns ``{"year": 1975, "volume": 1, "pages": {"de": 609, "fr": None,
    "it": None}}`` or ``None`` when the URI does not follow the historical
    pattern (modern ELIs such as ``eli/fga/2011/1325`` have no volume).
    """
    m = _FGA_HISTORICAL_ELI_RE.search(eli_uri or "")
    if not m:
        return None
    pages = {}
    for lang in ("de", "fr", "it"):
        seg = m.group(lang)
        pages[lang] = int(seg) if seg else None
    return {"year": int(m.group("year")), "volume": int(m.group("vol")), "pages": pages}


def classify_file_host(file_url: str | None) -> str:
    """'bar' for the Bundesarchiv portal, 'fedlex' for the Fedlex file
    store, otherwise the bare netloc (or '' when there is no URL)."""
    if not file_url:
        return ""
    netloc = urlparse(file_url).netloc.lower()
    if netloc == _BAR_HOST:
        return "bar"
    if netloc == _FEDLEX_FILE_HOST or netloc.endswith(".fedlex.admin.ch"):
        return "fedlex"
    return netloc


def _untyped_candidate(row: dict, language: str) -> UntypedBotschaft | None:
    """Map one SPARQL row of the untyped query to a candidate, or None."""
    act = _val(row, "act")
    title = (_val(row, "title") or "").strip()
    if not act or not title:
        return None
    if not title.lower().startswith(_BOTSCHAFT_TITLE_PREFIX[language]):
        return None
    parsed = parse_historical_fga_eli(act)
    year: int | None = parsed["year"] if parsed else None
    page: int | None = None
    mem_page = _val(row, "memPage")
    if mem_page is not None:
        try:
            page = int(mem_page)
        except (TypeError, ValueError):
            page = None
    if page is None and parsed:
        page = parsed["pages"].get(language)
    if year is None:
        # No volume pattern: fall back to the plain ``eli/fga/<year>/<seg>`` shape.
        parts = act.rstrip("/").rsplit("/", 2)
        if len(parts) == 3 and parts[-2].isdigit():
            year = int(parts[-2])
            if page is None and parts[-1].isdigit():
                page = int(parts[-1])
    if year is None or page is None:
        return None
    file_url = _val(row, "file")
    if not file_url:
        return None
    volume = parsed["volume"] if parsed else None
    citation = (_val(row, "ident") or _val(row, "hid") or "").strip() or None
    return UntypedBotschaft(
        year=year, page=page, title=title, eli_uri=act, file_url=file_url,
        file_host=classify_file_host(file_url), citation=citation,
        publication_date=_val(row, "pubDate"), volume=volume, language=language,
    )


def discover_fga_botschaften_untyped(
    language: str = "de",
    timeout: int = 600,
) -> list[UntypedBotschaft]:
    """Enumerate the historical Botschaften that carry no typeDocument.

    FGA works with an expression in ``language`` whose title starts with
    'Botschaft' / 'Message' / 'Messaggio' (case-insensitive) and which do
    NOT have typeDocument=23 — the complement of
    :func:`discover_fga_botschaften`. Measured 2026-09-09: 8,767 (DE) /
    8,921 (FR) / 2,362 (IT) works, 1850-1999, every one of them a single
    ``pdf-a`` manifestation on the Bundesarchiv host (one FR file on
    Fedlex's own store).

    One candidate per work: when Fedlex returns several rows for a work
    (several titles or files) the first row carrying a file wins. Rows
    without a file URL are dropped (nothing to fetch). The citation string
    is ``dct:identifier`` or ``jolux:historicalLegalId`` on the expression;
    a work with neither is returned with ``citation=None`` (the ingest
    records it, never builds one) and counted in the log line.
    """
    if language not in LANG_URIS:
        raise ValueError(f"Unsupported language: {language!r}")
    prefix = _BOTSCHAFT_TITLE_PREFIX[language]
    query = f"""
    PREFIX jolux: <{JOLUX}>
    PREFIX dct: <{DCT}>
    SELECT DISTINCT ?act ?title ?memPage ?ident ?hid ?pubDate ?file WHERE {{
      ?act jolux:isRealizedBy ?expr .
      ?expr jolux:language <{LANG_URIS[language]}> .
      ?expr jolux:title ?title .
      FILTER(STRSTARTS(LCASE(STR(?title)), "{prefix}"))
      FILTER(strstarts(STR(?act), "https://fedlex.data.admin.ch/eli/fga/"))
      FILTER NOT EXISTS {{ ?act jolux:typeDocument <{BOTSCHAFT_TYPE_DOC_URI}> }}
      OPTIONAL {{ ?expr jolux:memorialPage ?memPage }}
      OPTIONAL {{ ?expr dct:identifier ?ident }}
      OPTIONAL {{ ?expr jolux:historicalLegalId ?hid }}
      OPTIONAL {{ ?act jolux:publicationDate ?pubDate }}
      OPTIONAL {{ ?expr jolux:isEmbodiedBy ?manif . ?manif jolux:isExemplifiedBy ?file }}
    }}
    ORDER BY ?act
    """
    rows = sparql_query(query, timeout=timeout)
    by_act: dict[str, UntypedBotschaft] = {}
    dropped = 0
    for r in rows:
        cand = _untyped_candidate(r, language)
        if cand is None:
            dropped += 1
            continue
        if cand.eli_uri in by_act:
            continue
        by_act[cand.eli_uri] = cand
    out = list(by_act.values())
    hosts: dict[str, int] = {}
    for c in out:
        hosts[c.file_host] = hosts.get(c.file_host, 0) + 1
    no_citation = sum(1 for c in out if not c.citation)
    log.info(
        "discover_fga_botschaften_untyped(%s): %d works (%d rows, %d dropped, "
        "%d without a Fedlex citation) hosts=%s",
        language, len(out), len(rows), dropped, no_citation, hosts,
    )
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
