"""Abstract provider interface for LLM adapters."""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class ProviderMessage:
    role: str  # system | user | assistant | tool
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None
    name: str | None = None  # tool name for role=tool


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str


@dataclass
class ProviderResponse:
    """Non-streaming response from a provider."""
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = "stop"
    input_tokens: int | None = None
    output_tokens: int | None = None


MCP_TOOLS = [
    {
        "name": "search_decisions",
        "description": (
            "Search Swiss court decisions using full-text search. "
            "Supports keywords, phrases (in quotes), Boolean operators "
            "(AND, OR, NOT), and prefix matching (word*). "
            "Returns BM25-ranked results across ALL courts (BGE, BGer, cantonal) by default. "
            "Do NOT filter by court or language unless the user explicitly asks for a specific court. "
            "Broad unfiltered searches return the best mix of leading cases and cantonal decisions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "court": {"type": "string", "description": "Filter by court code"},
                "canton": {"type": "string", "description": "Filter by canton (CH, ZH, BE, GE, etc.)"},
                "language": {"type": "string", "description": "Filter by language: de, fr, it, rm", "enum": ["de", "fr", "it", "rm"]},
                "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "chamber": {"type": "string", "description": "Filter by chamber/division"},
                "limit": {"type": "integer", "description": "Max results to return. Use 100 for thorough research. Max 200.", "default": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_decision",
        "description": (
            "Fetch a single court decision with full text. "
            "Look up by decision_id or docket number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "Decision ID, docket number, or partial docket"},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "list_courts",
        "description": "List all available courts with decision counts.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_statistics",
        "description": "Get aggregate statistics about the dataset.",
        "parameters": {
            "type": "object",
            "properties": {
                "court": {"type": "string", "description": "Filter by court code"},
                "canton": {"type": "string", "description": "Filter by canton code"},
                "year": {"type": "integer", "description": "Filter by year"},
            },
        },
    },
    {
        "name": "draft_mock_decision",
        "description": (
            "Build a research-only mock decision outline from user facts. "
            "Combines relevant Swiss case law retrieval with statute references."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "facts": {"type": "string", "description": "Detailed facts of the case"},
                "question": {"type": "string", "description": "Optional legal question"},
                "preferred_language": {"type": "string", "enum": ["de", "fr", "it", "rm", "en"]},
                "deciding_court": {"type": "string", "description": "Hypothetical deciding court"},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["facts"],
        },
    },
    # ── Citation graph & jurisprudence ─────────────────────────────────
    {
        "name": "find_citations",
        "description": (
            "Given a decision_id, show what it cites and what cites it. "
            "Uses the reference graph (8.65M citation edges)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "Decision ID (e.g. bger_6B_1_2025)"},
                "direction": {"type": "string", "enum": ["both", "outgoing", "incoming"], "default": "both"},
                "min_confidence": {"type": "number", "description": "Resolved-citation confidence floor (0-1)", "default": 0.3},
                "limit": {"type": "integer", "description": "Max per direction (max 200)", "default": 50},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "find_appeal_chain",
        "description": (
            "Trace the appeal chain (Instanzenzug) for a decision: prior instances "
            "and subsequent appeals up to the Federal Supreme Court."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string", "description": "Decision ID"},
                "min_confidence": {"type": "number", "default": 0.3},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "find_leading_cases",
        "description": (
            "Find the most-cited decisions for a topic or statute. "
            "Authority ranking from the citation graph."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional topic query"},
                "law_code": {"type": "string", "description": "Optional law code (BV, OR, ZGB, EMRK, StGB)"},
                "article": {"type": "string", "description": "Optional article (requires law_code)"},
                "court": {"type": "string", "description": "Optional court filter"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "analyze_legal_trend",
        "description": (
            "Year-by-year decision counts for a statute or query. "
            "Shows how jurisprudence on a topic has evolved over time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional FTS query"},
                "law_code": {"type": "string", "description": "Optional law code"},
                "article": {"type": "string", "description": "Article (requires law_code)"},
                "court": {"type": "string", "description": "Optional court filter"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "get_case_brief",
        "description": (
            "Structured case brief for a Swiss court decision: regeste, Sachverhalt, "
            "key Erwägungen, Dispositiv, applicable statutes, citation authority."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "case": {"type": "string", "description": "BGE ref, decision_id, or docket number"},
            },
            "required": ["case"],
        },
    },
    {
        "name": "get_doctrine",
        "description": (
            "Statute text + leading cases + doctrinal timeline + Botschaft + scholarly "
            "commentary for a Swiss law article or legal concept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Statute ref ('Art. 41 OR') or legal concept"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_exam_question",
        "description": (
            "Generate a Swiss law exam question (Fallbearbeitung) from a real BGE. "
            "Returns fact pattern + hidden analysis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Legal area, statute, or concept"},
                "exclude_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "decision_ids already used in this session",
                },
            },
            "required": ["topic"],
        },
    },
    # ── Statute lookup (federal Fedlex + cantonal LexFind mirrors) ─────
    {
        "name": "get_law",
        "description": (
            "Authoritative lookup for the current text of any Swiss law article "
            "(federal or cantonal). Local Fedlex + LexFind mirrors."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sr_number": {"type": "string", "description": "SR number (e.g. 220 = OR)"},
                "abbreviation": {"type": "string", "description": "Federal law abbreviation (BV, OR, ZGB, …)"},
                "article": {"type": "string", "description": "Article number; omit for full article list"},
                "language": {"type": "string", "enum": ["de", "fr", "it"], "default": "de"},
                "canton": {"type": "string", "description": "Two-letter canton code or 'CH' for federal", "default": "CH"},
                "as_of": {"type": "string", "description": "Past date (YYYY-MM-DD or DD.MM.YYYY): the historical Fedlex edition in force on that date. Federal laws only; always report the returned snapshot_date."},
            },
        },
    },
    {
        "name": "search_laws",
        "description": (
            "Unified full-text search across every Swiss statute article — federal "
            "(Fedlex) and cantonal (LexFind, all 26 cantons). BM25 ranked, merged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS5 query"},
                "sr_number": {"type": "string", "description": "Restrict to one federal SR number"},
                "canton": {"type": "string", "description": "Two-letter canton code or 'CH'"},
                "jurisdiction": {"type": "string", "enum": ["all", "federal", "cantonal"], "default": "all"},
                "language": {"type": "string", "enum": ["de", "fr", "it"], "description": "Optional; omit to search all three languages"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_legislation",
        "description": (
            "Natural-language search across 33,000+ Swiss legislative texts via LexFind "
            "(federal + all 26 cantons). Set fetch_top_n_texts=1..3 for single-call answers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or keywords"},
                "canton": {"type": "string", "description": "Two-letter canton code or 'CH'"},
                "active_only": {"type": "boolean", "default": True},
                "search_in_content": {"type": "boolean", "default": False},
                "language": {"type": "string", "enum": ["de", "fr", "it"], "description": "Optional; omit to fan out across DE/FR/IT and merge results"},
                "limit": {"type": "integer", "default": 20},
                "fetch_top_n_texts": {"type": "integer", "description": "Inline parsed full-text for top N (max 10)", "default": 0},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_legislation",
        "description": (
            "Retrieve the full text + article list of a specific Swiss law by LexFind ID "
            "or SR/systematic number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lexfind_id": {"type": "integer", "description": "LexFind ID (from search_legislation)"},
                "systematic_number": {"type": "string", "description": "SR / systematic number"},
                "canton": {"type": "string", "default": "CH"},
                "include_versions": {"type": "boolean", "default": False},
                "language": {"type": "string", "enum": ["de", "fr", "it"], "default": "de"},
            },
        },
    },
    # ── Scholarly commentary (OnlineKommentar.ch) ─────────────────────
    {
        "name": "get_commentary",
        "description": (
            "Look up a legal commentary (OnlineKommentar.ch, CC-BY-4.0; for the BV also "
            "OpenLegalCommentary.ch, CC BY-SA 4.0, AI-generated) "
            "for a Swiss federal law article. Without article: lists available articles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "abbreviation": {"type": "string", "description": "Law abbreviation (OR, BV, ZGB, …)"},
                "sr_number": {"type": "string", "description": "SR number alternative to abbreviation"},
                "article": {"type": "string", "description": "Article number; omit to list available"},
                "language": {"type": "string", "default": "de"},
            },
        },
    },
    {
        "name": "search_commentaries",
        "description": "Full-text search across all OnlineKommentar.ch legal commentaries.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS5 query"},
                "abbreviation": {"type": "string", "description": "Filter by law abbreviation"},
                "language": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    # ── Materialien (legislative history / Botschaft) ─────────────────
    {
        "name": "get_materialien",
        "description": (
            "Look up preparatory materials (Botschaft, parliamentary debate) for a "
            "Swiss federal law article. Currently covers BGFA; BV/OR/StGB/ZGB rolling out."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "law_code": {"type": "string", "description": "Law abbreviation (BV, BGFA, OR, StGB)"},
                "article": {"type": "string", "description": "Article number; omit for all"},
            },
            "required": ["law_code"],
        },
    },
    {
        "name": "search_materialien",
        "description": (
            "Full-text search across all preparatory materials for Swiss federal laws "
            "(legislative intent, key arguments, design choices)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language or FTS5 query"},
                "law_code": {"type": "string", "description": "Filter by law abbreviation"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
]

SYSTEM_PROMPT = (
    "You are a senior legal research associate at a top-tier Swiss law firm. "
    "You have access to a comprehensive database of over 990,000 Swiss court decisions "
    "(1875–2026, 100 courts, 26 cantons, 4 languages: de/fr/it/rm). "
    "Your work product must meet the standards expected of a leading practitioner: "
    "precise, authoritative, and rigorously sourced.\n\n"

    "## LANGUAGE — MANDATORY\n"
    "You MUST reply in the SAME language the user writes in. "
    "German question → German answer. French → French. English → English. Italian → Italian. "
    "Stay in that language for the ENTIRE response — headings, analysis, citations, everything. "
    "Do NOT switch languages mid-reply. Do NOT default to English, Chinese, or any other language. "
    "This rule overrides all other instructions.\n\n"

    "## Court codes\n"
    "All court codes are **lowercase**. Common codes:\n"
    "- Federal: `bger` (BGer), `bge` (BGE Leitentscheide), `bvger` (BVGer), "
    "`bstger` (BStGer), `bpatger` (BPatGer)\n"
    "- Cantonal: `zh_gerichte`, `zh_vwg`, `zh_svg`, `be_verwaltungsgericht`, "
    "`be_zivilstraf`, `ge_gerichte`, `vd`, `sg_pub`, `ag`, `bs`, `bl_gerichte`, "
    "`gr`, `fr_gerichte`, `lu`, `so`, `tg`\n"
    "- Use `list_courts` to find the exact code for any court.\n"
    "- The database covers all years through 2026. Never assume a year range is missing.\n\n"

    "## Search behaviour — CRITICAL\n"
    "- ALWAYS call search_decisions before answering any legal question. "
    "Never rely on general knowledge alone.\n"
    "- ALWAYS set `limit` to at least 100 in every search_decisions call. "
    "This is mandatory — never use limit=10 or limit=20.\n"
    "- NEVER filter by language. Swiss law is multilingual — the leading case on a topic "
    "may be in any of the 4 languages. Always search across all languages.\n"
    "- NEVER filter by court unless the user explicitly asks for decisions from a specific court. "
    "Unfiltered searches return a mix of BGE Leitentscheide, BGer, BVGer, and cantonal decisions "
    "ranked by relevance — this is what produces the best research results.\n"
    "- Use targeted, specific queries. Do NOT set court, canton, or language filters "
    "unless the user explicitly requests a specific jurisdiction.\n"
    "- The search engine ranks by relevance — you will then select and cite only the "
    "5–15 most pertinent decisions from the results.\n"
    "- One precise search is better than many vague ones. If a search returns 0 results, "
    "simplify keywords or remove filters — at most twice, then report what you found.\n"
    "- Use lowercase court codes in filters (e.g. `court=bger`, not `court=BGer`).\n"
    "- Maximum 3 search rounds per user message.\n\n"

    "## Response format\n"
    "Do NOT narrate your process ('Let me search...', 'Ich suche jetzt...'). "
    "Call tools silently, then present your analysis. "
    "Structure every substantive answer as follows:\n\n"

    "### 1. Fragestellung\n"
    "One sentence restating the legal question.\n\n"

    "### 2. Rechtsprechungsübersicht\n"
    "From the search results, select the **most relevant and authoritative decisions** "
    "(typically 5–15 depending on how rich the case law is). Do NOT list every search hit — "
    "focus on leading cases, landmark rulings, and decisions that best illustrate the legal "
    "principles at stake. Present them in strict hierarchical order:\n"
    "- **Leitentscheide (BGE)** — if any published leading cases exist\n"
    "- **Bundesgericht (BGer)** — unpublished federal decisions\n"
    "- **Bundesverwaltungsgericht / Bundesstrafgericht** — if relevant\n"
    "- **Kantonale Gerichte** — cantonal court decisions\n\n"
    "For each decision cite:\n"
    "- Full docket number and date: e.g. **BGer 6B_123/2024** vom 15.01.2025\n"
    "- One-sentence summary of the holding (Leitsatz/ratio decidendi)\n"
    "- The key legal provision applied (e.g. Art. 271 OR, Art. 8 BV)\n\n"

    "### 3. Rechtliche Analyse\n"
    "Synthesise the legal principles, requirements, and tests the courts apply. "
    "Highlight points of consensus across courts. "
    "Flag any divergence, evolution over time, or open questions. "
    "Cite the specific decisions that support each point.\n\n"

    "### 4. Fazit & weiteres Vorgehen\n"
    "A concise conclusion answering the user's question. "
    "Then offer 1–2 concrete follow-up directions — phrased as searches in "
    "THIS database (name the exact query and any court/date filter, e.g. "
    "\"Suche: 'Anleihensbedingungen Auslegung', court=zh_handelsgericht, ab 2000\"), "
    "a deeper analysis of a specific decision, a related legal question, or "
    "narrowing by jurisdiction or time period. NEVER direct the user to "
    "Swisslex, Legalis, Weblaw, beck-online or any other commercial/paywalled "
    "database — not even as a 'you could also check' aside. This platform "
    "exists to make Swiss law openly accessible; if something is genuinely "
    "not in this corpus, say precisely what is missing and point to a FREE "
    "official source (Fedlex, LexFind, entscheidsuche.ch, the court's own "
    "portal) instead.\n\n"

    "Use the section headings above in the user's language "
    "(e.g. French: 1. Question juridique, 2. Aperçu de la jurisprudence, "
    "3. Analyse juridique, 4. Conclusion).\n\n"

    "## Quality standards\n"
    "- Open access only: never recommend or draft search strings for commercial "
    "legal databases (Swisslex, Legalis, Weblaw, beck-online, Juris). "
    "All further-research suggestions stay within this platform or free "
    "official sources.\n"
    "- Every factual claim must be backed by a specific decision from the database.\n"
    "- Be precise about jurisdictions (federal vs cantonal) and legal domains.\n"
    "- When summarising a full-text decision, extract the key holdings — never dump raw text.\n"
    "- Use proper legal terminology for the language you are writing in.\n"
    "- Maintain a professional, authoritative tone throughout.\n"
)


class ProviderBase(abc.ABC):
    """Abstract base for LLM provider adapters."""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[ProviderMessage],
        tools: list[dict] | None = None,
    ) -> ProviderResponse:
        """Send messages to the LLM and get a response (possibly with tool calls)."""
        ...

    async def chat_stream(
        self,
        messages: list[ProviderMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ProviderResponse]:
        """Yield ProviderResponse chunks with text deltas and/or tool_calls.

        Default implementation falls back to non-streaming chat().
        Subclasses should override for real token-by-token streaming.
        """
        resp = await self.chat(messages, tools=tools)
        yield resp

    @abc.abstractmethod
    def format_tool_result(self, result: ToolResult) -> ProviderMessage:
        """Format a tool result into the provider's expected message format."""
        ...
