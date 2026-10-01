"""Versioned, additive contracts for the public research interfaces.

These models describe existing server results; they never invent citations,
normalise evidence, or remove unrecognised fields. MCP validates the original
dictionaries against them and advertises them as outputSchema. The curated
research OpenAPI document publishes them for the seven research routes; the
live routes' own OpenAPI entries are left exactly as they were (the Copilot
Studio subset depends on their hand-typed schemas).
"""
from __future__ import annotations

import copy
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

CONTRACT_VERSION = "1.0.0"


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    ignored_arguments: list[str] | None = Field(default=None, description="MCP parameters that were ignored; they did not affect the returned result.")


class ResearchError(ResearchModel):
    error: str
    message: str | None = None
    available_e_numbers: list[str] | None = None


class CitationFields(ResearchModel):
    citation_string_de: str | None = None
    citation_string_fr: str | None = None
    citation_string_it: str | None = None
    canonical_url: str | None = None
    markdown_link: str | None = None
    rule_statement: str | None = None


class DecisionRecord(CitationFields):
    decision_id: str
    docket_number: str | None = None
    court: str | None = None
    canton: str | None = None
    language: str | None = None
    decision_date: str | None = None
    publication_date: str | None = None
    date_is_estimated: bool | None = None
    title: str | None = None
    regeste: str | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    content_hash: str | None = None
    scraped_at: str | None = None
    snippet: str | None = None
    pinpoint: dict[str, Any] | None = None
    citation_count: int | None = None
    is_leading_case: bool | None = None
    joined_dockets: list[str] | None = Field(default=None, description="Secondary dockets of a consolidated proceeding stored under this decision; a reference by any of them names this record. Absent when there are none.")
    canonical_decision_id: str | None = Field(default=None, description="The canonical decision_id when the representation manifest is loaded: equals decision_id unless this record is a duplicate representation of another stored decision. decision_id itself is never changed.")
    is_canonical: bool | None = Field(default=None, description="Whether this record is the canonical representation (canonical_decision_id == decision_id). Absent when the manifest is not loaded.")


class SearchDecisionsResponse(ResearchModel):
    total: int = Field(description="Match count; see total_is_lower_bound before treating it as exact.")
    total_is_lower_bound: bool
    results: list[DecisionRecord]
    returned: int
    limit: int
    offset: int
    has_more: bool = Field(description="Whether another page is retrievable. False does not prove a relevance-ranked search enumerated the corpus.")
    next_offset: int | None
    result_set_id: str | None = None
    query_condensed: bool | None = None
    condensed_terms: list[str] | None = None
    note: str | None = None
    degraded: bool | None = Field(default=None, description="True when the search hit its time budget and skipped ranking strategies; the page is a degraded ranking, not the best matches.")
    decisions: list[dict[str, Any]] | None = Field(default=None, description="Legacy MCP widget records, retained alongside results.")


class DecisionResponse(DecisionRecord):
    full_text: str | None = Field(default=None, description="Stored decision text; omitted when full_text=false. MCP caps it at 200,000 characters and explicitly provides truncation metadata and a full REST URL when capped.")
    full_text_total_chars: int | None = None
    full_text_returned_chars: int | None = None
    full_text_truncated: bool | None = None
    full_text_url: str | None = Field(default=None, description="REST URL for the complete stored text when the MCP response is truncated; content_hash remains the stored source hash.")
    recency_note: str | None = None


class PassagePart(ResearchModel):
    e_number: str
    text: str


class ErwaegungResponse(CitationFields):
    decision_id: str
    e_number: str
    text: str = Field(description="Existing source text response; cross-references can contain Markdown links.")
    depth: int | None = None
    parent_e_number: str | None = None
    siblings: list[str] | None = None
    court: str | None = None
    language: str | None = None
    composed_of: list[str] | None = None
    parts: list[PassagePart] | None = None


class LawArticle(ResearchModel):
    article_num: str
    heading: str | None = None
    text: str | None = Field(default=None, description="Article text; a whole-law table of contents can omit it.")
    text_status: str | None = Field(default=None, description="ok, heading_only or empty for a historical PDF edition: heading_only/empty means no article text was recovered.")
    xml: str | None = None
    section: str | None = None
    section_heading: str | None = None


class LawResponse(ResearchModel):
    sr_number: str
    title: str | None = None
    abbreviation: str | None = None
    canton: str | None = None
    level: str | None = None
    language: str | None = None
    consolidation_date: str | None = None
    snapshot_date: str | None = None
    version: str | None = None
    as_of: str | None = None
    work_uri: str | None = None
    work_entry_in_force: str | None = None
    work_no_longer_in_force: str | None = None
    fedlex_snapshot_uri: str | None = None
    text_source: str | None = None
    verbatim_quotation: str | None = None
    text_status: str | None = Field(default=None, description="ok, heading_only or empty (historical PDF editions); heading_only/empty means no article text was recovered.")
    source_url: str | None = None
    source_label: str | None = None
    articles: list[LawArticle] | None = None
    pending_changes: list[dict[str, Any]] | None = None


class CitationEdge(ResearchModel):
    source_decision_id: str | None = None
    target_decision_id: str | None = None
    target_ref: str | None = None
    confidence_score: float | None = None
    mention_count: int | None = None
    docket_number: str | None = None
    court: str | None = None
    decision_date: str | None = None


class CitationsResponse(ResearchModel):
    decision_id: str
    direction: str
    limit: int
    offset: int
    incoming: list[CitationEdge] | None = None
    outgoing: list[CitationEdge] | None = None
    incoming_total: int | None = None
    outgoing_total: int | None = None
    incoming_returned: int | None = None
    outgoing_returned: int | None = None
    incoming_has_more: bool | None = None
    outgoing_has_more: bool | None = None
    next_offset: int | None = None


class CiteResponse(CitationFields):
    exists: bool = Field(description="False is a completed lookup with no resolved reference, not a server error.")
    decision_id: str | None = None
    queried: str | None = None
    resolved_id: str | None = None
    citation_string: str | None = None
    close_matches: list[DecisionRecord] | None = None
    joined_dockets: list[str] | None = Field(default=None, description="Secondary dockets of the resolved consolidated proceeding; absent when there are none.")
    canonical_decision_id: str | None = Field(default=None, description="Canonical decision_id of the resolved record when the representation manifest is loaded; decision_id is never changed.")
    is_canonical: bool | None = Field(default=None, description="Whether the resolved record is the canonical representation; absent when the manifest is not loaded.")


class LookupHit(DecisionRecord):
    citation: str | None = Field(default=None, description="Server-formatted citation label of the hit.")
    url: str | None = Field(default=None, description="Public decision page of the hit.")


class LookupResponse(ResearchModel):
    query: str
    is_case_number: bool
    exact: bool | None = Field(default=None, description="True when only decisions carrying the reference as their own docket or BGE label were returned (exact=true).")
    total: int = Field(description="Returned hits, not an uncapped count; inspect all hits for ambiguity and treat a full page conservatively.")
    results: list[LookupHit]
    hint: str | None = None
    match: str | None = Field(default=None, description="Without exact=true: 'exact' when the hits carry the reference as their own docket or BGE label, 'search' when nothing matched exactly and the hits are near or related numbers.")
    partial: bool | None = Field(default=None, description="True when the fallback search was cut short under load: the hits are incomplete, never proof of absence. With no hits the endpoint answers 503 instead.")


RESEARCH_MODELS: dict[str, type[ResearchModel]] = {
    "search_decisions": SearchDecisionsResponse,
    "get_decision": DecisionResponse,
    "get_erwaegung": ErwaegungResponse,
    "get_law": LawResponse,
    "find_citations": CitationsResponse,
    "cite": CiteResponse,
    "lookup": LookupResponse,
}

RESEARCH_PATHS = {
    "search_decisions": "/decisions",
    "get_decision": "/decisions/{decision_id}",
    "get_erwaegung": "/erwaegung/{decision_id}/{e_number}",
    "get_law": "/laws/{abbreviation}",
    "find_citations": "/citations/{decision_id}",
    "cite": "/cite",
    "lookup": "/lookup",
}

_NOT_FOUND = {
    "get_decision": "Decision not found.",
    "get_law": "Requested XML representation unavailable (format=xml).",
}


@cache
def response_type(operation: str):
    return RESEARCH_MODELS[operation] | ResearchError


@cache
def output_schema(operation: str) -> dict:
    # MCP requires an object at the root. The union documents both the
    # existing success payload and handled errors without weakening either.
    schema = TypeAdapter(response_type(operation)).json_schema()
    return {"type": "object", **schema}


def validate_payload(operation: str, payload: dict) -> None:
    """Validate without serialising models (which could coerce or add fields)."""
    model = ResearchError if payload.get("error") else RESEARCH_MODELS[operation]
    model.model_validate(payload, strict=True)


def _to_openapi_30(node) -> None:
    """Rewrite JSON Schema 2020-12 idioms that OpenAPI 3.0.3 rejects, in place.

    Mirrors the server's own sanitiser: a null branch becomes `nullable`,
    type arrays collapse, `const` becomes `enum`, boolean exclusive bounds
    and multi-example lists are dropped to their 3.0 forms.
    """
    if isinstance(node, dict):
        for combiner in ("anyOf", "oneOf"):
            branches = node.get(combiner)
            if isinstance(branches, list):
                non_null = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
                if len(non_null) != len(branches):
                    if len(non_null) == 1:
                        del node[combiner]
                        for key, value in non_null[0].items():
                            node.setdefault(key, value)
                    else:
                        node[combiner] = non_null
                    node["nullable"] = True
        kind = node.get("type")
        if isinstance(kind, list):
            non_null = [k for k in kind if k != "null"]
            if len(non_null) == 1:
                node["type"] = non_null[0]
            else:
                node.pop("type", None)
            if "null" in kind:
                node["nullable"] = True
        for key in ("exclusiveMinimum", "exclusiveMaximum"):
            if isinstance(node.get(key), bool):
                node.pop(key)
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if isinstance(node.get("examples"), list) and node["examples"]:
            node.setdefault("example", node["examples"][0])
            node.pop("examples")
        for value in list(node.values()):
            _to_openapi_30(value)
    elif isinstance(node, list):
        for value in node:
            _to_openapi_30(value)


@cache
def _openapi_response(operation: str) -> tuple[dict, dict]:
    """(components, 200 schema) for one operation, in OpenAPI 3.0.3 form."""
    schema = TypeAdapter(response_type(operation)).json_schema(
        ref_template="#/components/schemas/{model}", mode="serialization")
    components = schema.pop("$defs", {})
    _to_openapi_30(schema)
    _to_openapi_30(components)
    return components, schema


def research_openapi(full: dict) -> dict:
    """Select only public research routes and attach the typed contracts.

    The live application document is not modified: schemas are injected into
    this curated copy only, so /api/openapi.json and the Copilot subset keep
    their existing response documentation.
    """
    spec = copy.deepcopy(full)
    spec["info"] = {
        **spec["info"],
        "title": "OpenCaseLaw Research API",
        "version": CONTRACT_VERSION,
        "description": "Read-only research operations. Schemas preserve additive metadata. Match counts and exhausted pages do not guarantee corpus completeness.",
    }
    spec["paths"] = {}
    capabilities = {}
    typed_components: dict = {}
    for name, path in RESEARCH_PATHS.items():
        route = copy.deepcopy(full["paths"][path])
        operation = route["get"]
        operation["x-opencaselaw-operation"] = name
        components, schema = _openapi_response(name)
        typed_components.update(copy.deepcopy(components))
        responses = operation.setdefault("responses", {})
        success = copy.deepcopy(responses.get("200") or {})
        success.setdefault("description", "Successful Response")
        success["content"] = {"application/json": {"schema": copy.deepcopy(schema)}}
        if name == "get_law":
            success["content"]["application/xml"] = {"schema": {"type": "string"}}
        responses["200"] = success
        if name in _NOT_FOUND:
            responses.setdefault("404", {
                "description": _NOT_FOUND[name],
                "content": {"application/json": {"schema": {
                    "type": "object", "properties": {"detail": {"type": "string"}}, "required": ["detail"]}}},
            })
        spec["paths"][path] = {"get": operation}
        capabilities[name] = {
            "method": "GET", "path": path,
            "operation_id": operation["operationId"],
            "response_model": RESEARCH_MODELS[name].__name__,
            "mcp_tool": name if name != "lookup" else None,
        }
    spec["x-opencaselaw-contract-version"] = CONTRACT_VERSION
    spec["x-opencaselaw-capabilities"] = capabilities
    # The full document also contains paid/admin request schemas. Retain
    # only components reachable from these seven GET operations, resolving
    # references against the application components plus the typed ones.
    available: dict = copy.deepcopy(full.get("components", {}))
    available.setdefault("schemas", {}).update(typed_components)
    components: dict = {}
    seen: set[str] = set()

    def collect(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref and ref not in seen:
                if not ref.startswith("#/components/"):
                    raise ValueError(f"Non-local research schema reference: {ref}")
                seen.add(ref)
                _, _, category, name = ref.split("/", 3)
                component = copy.deepcopy(available[category][name])
                components.setdefault(category, {})[name] = component
                collect(component)
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(spec["paths"])
    spec["components"] = components
    spec.pop("tags", None)
    return spec
