"""CLI grammar, streaming-friendly output, and bounded research operations."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from . import render
from ._version import __version__
from .client import APIError, Client
from .references import normalise_pinpoint, parse_reference
from .workflows import (BREAKER_THRESHOLD, DEFAULT_JOBS, RANKED_MAX_RESULTS, ResolutionError, extract_docket,
                        fetch_passage, identify, identify_row, reference_key)

DEFAULT_BASE_URL = "https://mcp.opencaselaw.ch"
FORMATS = ("text", "json", "jsonl", "table", "csv", "md")
CONFIG_KEYS = ("base_url", "timeout", "retries", "format", "color", "language", "jobs", "cache", "local", "pack", "kind")


def config_path() -> str:
    return os.environ.get("OCL_CONFIG") or os.path.join(os.path.expanduser("~"), ".config", "ocl", "config")


def load_config(path: str | None = None) -> dict:
    """Defaults from ~/.config/ocl/config (key = value lines) and OCL_* variables.
    Precedence: command-line flag, then environment, then the file."""
    values: dict = {}
    try:
        text = Path(path or config_path()).read_text(encoding="utf-8")
    except OSError:
        text = ""
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip().lower().replace("-", "_")
        if not separator or key not in CONFIG_KEYS:
            raise ValueError(f"config line {number}: expected one of {', '.join(CONFIG_KEYS)} = value")
        values[key] = value.strip()
    for key in CONFIG_KEYS:
        env = os.environ.get("OCL_" + key.upper())
        if env is not None and env != "":
            values[key] = env
    try:
        if "timeout" in values:
            values["timeout"] = float(values["timeout"])
        if "retries" in values:
            values["retries"] = nonnegative_int(values["retries"])
        if "jobs" in values:
            values["jobs"] = positive_int(values["jobs"])
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise ValueError(f"config: {exc}") from exc
    for key, choices in (("format", FORMATS), ("color", ("auto", "always", "never")), ("language", ("de", "fr", "it")), ("kind", ("draft", "submission"))):
        if key in values and values[key] not in choices:
            raise ValueError(f"config: {key} must be one of {', '.join(choices)}")
    return values


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def confidence(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def _common():
    parser = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    parser.add_argument("--base-url", help=f"API origin (default: {DEFAULT_BASE_URL}; OCL_BASE_URL)")
    parser.add_argument("--timeout", type=float, help="per-request timeout in seconds (default: 30; OCL_TIMEOUT)")
    parser.add_argument("--retries", type=nonnegative_int, help="retries per request, 0-5 (default: 2; OCL_RETRIES)")
    parser.add_argument("--format", choices=list(FORMATS),
                        help="text (default at a terminal), json (default when piped), jsonl, table, csv or md (OCL_FORMAT)")
    parser.add_argument("--fields", help="comma-separated result fields for json/jsonl; reference, status, errors, notes and pagination are always retained")
    parser.add_argument("--color", choices=["auto", "always", "never"], help="colour in text output (default: auto; NO_COLOR and OCL_COLOR respected)")
    parser.add_argument("--jobs", type=positive_int, help=f"concurrent requests for batch commands, 1-8 (default: {DEFAULT_JOBS}; OCL_JOBS)")
    parser.add_argument("--verbose", action="store_true", help="log every request (URL, status, time) to stderr")
    parser.add_argument("--cache", metavar="DIR", help="cache responses in DIR, keyed by the server's database generation (OCL_CACHE)")
    parser.add_argument("--local", action="store_true",
                        help="offline: answer from the verification pack on this machine (OCL_LOCAL=1). Covers check, citations resolve, "
                             "cite, decisions passage/get, quotes check, bundles and doctor; federal statutes when statutes.sqlite sits next to the pack; no search or tools")
    parser.add_argument("--pack", metavar="PATH",
                        help="the pack file for --local (default: the one `ocl pack pull` stored in the user data directory; OCL_PACK)")
    return parser


_OFF = frozenset({"", "0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on", "default"})


def offline_defaults(cfg: dict) -> tuple[bool, str | None]:
    """(local mode on, pack path or None) from the config file and OCL_* variables.
    `pack` / OCL_PACK is a path. `local` / OCL_LOCAL is a switch (1, true, yes, on);
    a path given there, the 0.6/0.7 grammar, turns local mode on and names the pack."""
    local = str(cfg.get("local") or "").strip()
    pack = str(cfg.get("pack") or "").strip() or None
    if local.lower() in _OFF:
        return False, pack
    if local.lower() in _ON:
        return True, pack
    return True, pack or local


_FILTER_HELP = {
    "court": "court code, e.g. bge, bger, zh_obergericht (see /api/courts)",
    "canton": "canton code, e.g. ZH, BE, GE; CH for federal",
    "language": "de, fr, it or rm (en reaches ECtHR judgments only)",
    "date-from": "earliest decision date, YYYY-MM-DD",
    "date-to": "latest decision date, YYYY-MM-DD",
}


def _filters(parser):
    for name in ("court", "canton", "language", "date-from", "date-to"):
        parser.add_argument("--" + name, help=_FILTER_HELP[name])


def _input(parser, name, what):
    parser.add_argument(name, nargs="*", help=f"{what}; alternatively use --stdin or --input")
    parser.add_argument("--stdin", action="store_true", help="read one item per line, or JSONL records, from stdin")
    parser.add_argument("--input", metavar="FILE", help="read one item per line, or JSONL records, from this UTF-8 file")


_EXAMPLES = """examples:
  ocl decisions search 'Rachekündigung Art. 336 OR' --max-results 5
  ocl decisions passage bge_BGE_136_III_513 2.3
  ocl laws get OR --article 336
  ocl citations resolve 'BGE 136 III 513' '4A_747/2012'
  ocl bundle create 'Rachekündigung Art. 336 OR' --max-results 5 --passage 2 --law OR:336 --out evidence

At a terminal, results are shown as readable text; piped or with --format json/jsonl
they are JSON on stdout; table, csv and md are for pasting into documents. Messages go
to stderr. Defaults can live in ~/.config/ocl/config (key = value) or OCL_* variables;
`ocl completion zsh|bash|fish` prints a shell completion script.
Exit codes: 0 complete, 2 invalid input, 3 API or network failure, 4 partial or unresolved.
Agents: `ocl agent-guide` (contract, commands, rules), `ocl skills install --claude`, `ocl tool list`.
Guide: https://github.com/jonashertner/opencaselaw/blob/main/docs/research-cli.md
"""


def build_parser(config: dict | None = None):
    cfg = load_config() if config is None else config
    lang = cfg.get("language", "de")
    fmt = argparse.RawDescriptionHelpFormatter
    common = _common()
    parser = argparse.ArgumentParser(
        prog="ocl", parents=[_common()], formatter_class=fmt,
        description=("Research Swiss case law and statutes from the command line. Every command is a "
                     "read-only call to the public OpenCaseLaw API; identifiers, citation strings and "
                     "passage text come back from the service unchanged."),
        epilog=_EXAMPLES)
    parser.set_defaults(base_url=cfg.get("base_url", DEFAULT_BASE_URL), timeout=cfg.get("timeout", 30),
                        retries=cfg.get("retries", 2), format=cfg.get("format"), fields=None, cache=cfg.get("cache"),
                        local=offline_defaults(cfg)[0], pack=offline_defaults(cfg)[1],
                        color=cfg.get("color", "auto"), jobs=cfg.get("jobs", DEFAULT_JOBS), verbose=False)
    parser.add_argument("--version", action="version", version=f"ocl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    decisions = commands.add_parser("decisions", parents=[common], formatter_class=fmt,
                                    help="search decisions, fetch them, or fetch one exact Erwägung",
                                    description="Search court decisions, fetch full decisions, or fetch one numbered Erwägung verbatim.")
    actions = decisions.add_subparsers(dest="action", required=True, metavar="ACTION")
    search_parser = actions.add_parser(
        "search", parents=[common], formatter_class=fmt,
        help="find decisions by text and/or filters (bounded, with completeness metadata)",
        description=("Find decisions. A text query is ranked by relevance and sent as ONE request of "
                     "--max-results (at most 800). Without query text, the filters enumerate an exact set "
                     "page by page. The output keeps the service's counts (total, total_is_lower_bound, "
                     "has_more, next_offset) so you can see how complete the selection is."),
        epilog="example:\n  ocl decisions search 'Rachekündigung' --court bge --date-from 2015-01-01 --max-results 20 --format jsonl > hits.jsonl\n")
    search_parser.add_argument("query", nargs="?", default="", help="search text (FTS5 syntax: words, \"phrases\", AND/OR/NOT); omit to enumerate by filters only")
    _filters(search_parser)
    search_parser.add_argument("--chamber", help="chamber or division, substring match")
    search_parser.add_argument("--marked-for-publication", action="store_true", default=None, help="only BGer rulings flagged for the official BGE collection")
    search_parser.add_argument("--sort", choices=["relevance", "date_desc", "date_asc"], default="relevance", help="ordering (default: relevance)")
    search_parser.add_argument("--detail", choices=["full", "compact"], default="compact", help="record detail requested from the service (default: compact, which carries citation_string_de only; full adds regeste, snippet, pinpoint and the French/Italian citation strings)")
    search_parser.add_argument("--no-pinpoint", action="store_true", help="skip the per-result pinpoint lookup for full detail (faster)")
    search_parser.add_argument("--no-collapse", action="store_true", help="keep every row of a ruling the service stores under several ids (by default one row per canonical_decision_id is kept, the canonical record, and the others are counted in _client.duplicates_collapsed)")
    search_parser.add_argument("--max-results", type=positive_int, default=50, help="bound for the selection (default: 50; a text query allows at most 800)")
    search_parser.add_argument("--page-size", type=positive_int, default=50, help="results per request for filter-only enumeration, at most 800 (default: 50)")
    search_parser.add_argument("--offset", type=nonnegative_int, default=0, help="skip this many results first (default: 0)")

    get = actions.add_parser("get", parents=[common], formatter_class=fmt,
                             help="fetch full decisions by ID or docket, singly or in batch",
                             description=("Fetch full decisions. Accepts decision IDs (bge_BGE_136_III_513), dockets "
                                          "(4A_747/2012) or the JSONL rows written by `decisions search`."),
                             epilog="example:\n  ocl decisions search 'Rachekündigung' --max-results 5 --format jsonl | ocl decisions get --stdin --format jsonl > decisions.jsonl\n")
    _input(get, "ids", "decision IDs or dockets")
    get.add_argument("--no-full-text", action="store_true", help="metadata only, without the decision text")
    passage = actions.add_parser("passage", parents=[common], formatter_class=fmt,
                                 help="fetch one numbered Erwägung verbatim",
                                 description="Fetch the exact text of one numbered Erwägung (consid.), the citable unit in Swiss practice.",
                                 epilog="example:\n  ocl decisions passage bge_BGE_136_III_513 2.3\n")
    passage.add_argument("decision_id", help="decision ID or docket, e.g. bge_BGE_136_III_513 or 4A_747/2012")
    passage.add_argument("number", help="Erwägung number, e.g. 2, 2.3 or 3b (a lettered sub-number the index lacks returns its parent number with a note, exit 4)")

    laws = commands.add_parser("laws", parents=[common], formatter_class=fmt,
                               help="fetch statute text (federal and cantonal, current or historical)",
                               description="Fetch statute text from the Fedlex mirror (federal) or the cantonal mirrors.")
    law_actions = laws.add_subparsers(dest="action", required=True, metavar="ACTION")
    law = law_actions.add_parser("get", parents=[common], formatter_class=fmt,
                                 help="fetch one article, or a law's table of contents",
                                 description="Fetch one article (with --article) or the table of contents of a law.",
                                 epilog="examples:\n  ocl laws get OR --article 336\n  ocl laws get ZGB --article 8 --language fr\n  ocl laws get OR --article 41 --as-of 2015-01-01\n")
    law.add_argument("abbreviation", help="law abbreviation, e.g. OR, ZGB, StGB, BV (or _ with --sr-number)")
    law.add_argument("--article", help="article number, e.g. 41 or 41a")
    law.add_argument("--sr-number", help="SR number instead of an abbreviation, e.g. 220")
    law.add_argument("--as-of", help="historical federal edition in force on this date, YYYY-MM-DD")
    law.add_argument("--canton", default="CH", help="CH for federal law (default), or a canton code for cantonal law")
    law.add_argument("--language", choices=["de", "fr", "it"], default=lang, help="text language (default: de; OCL_LANGUAGE)")

    citations = commands.add_parser("citations", parents=[common], formatter_class=fmt,
                                    help="follow the citation graph, or check a list of references",
                                    description="Follow what a decision cites and what cites it, or check a list of references from a draft.")
    citation_actions = citations.add_subparsers(dest="action", required=True, metavar="ACTION")
    listing = citation_actions.add_parser("list", parents=[common], formatter_class=fmt,
                                          help="what a decision cites and what cites it (one page)",
                                          description="List citations of a decision from the reference graph, one explicitly paged response per direction.",
                                          epilog="example:\n  ocl citations list bge_BGE_140_III_86 --direction incoming --limit 20\n")
    listing.add_argument("decision_id", help="decision ID or docket")
    listing.add_argument("--direction", choices=["both", "incoming", "outgoing"], default="both", help="incoming = decisions citing it; outgoing = decisions it cites (default: both)")
    listing.add_argument("--limit", type=positive_int, default=50, help="per direction, at most 200 (default: 50)")
    listing.add_argument("--offset", type=nonnegative_int, default=0, help="pagination offset per direction (default: 0)")
    listing.add_argument("--min-confidence", type=confidence, default=0.3, help="minimum resolution confidence 0-1 (default: 0.3)")
    resolve = citation_actions.add_parser(
        "resolve", parents=[common], formatter_class=fmt,
        help="check references from a draft: found, missing, ambiguous; verify pinpoints",
        description=("Check a list of references as written in a draft. Each comes back as resolved, "
                     "pinpoint_unavailable, discrepancy, missing, ambiguous, unrecognized, resolution_incomplete "
                     "or error, with the service's evidence. The reference is parsed the way it is written: "
                     "BGE/ATF/DTF label, docket (4A_747/2012, 4A 747/2012, LA210005, C/11532/2013), court words, "
                     "date, page references and an inline pinpoint (E. 2.3, consid. 3b). The decision the service "
                     "proposes must carry the label written first in the reference (identity_check.method: "
                     "exact_canonical_id; exact_server_citation, the service's own string; exact_server_docket, the "
                     "decision's own docket; exact_server_joined_docket, a joined docket of a consolidated proceeding "
                     "the record lists; exact_candidate_label, a docket the lookup index knows in another form); "
                     "a docket the reference only mentions later is a cross-reference, not the citation. A docket carried by several "
                     "decisions at courts the reference does not rule out is ambiguous. An inline or input pinpoint is "
                     "retrieved and verified (pinpoint_status retrieved, parent_retrieved for a lettered sub-number "
                     "the index lacks, unavailable). A date or a docket written next to the label that contradicts "
                     "the record is a discrepancy. Nothing is ever substituted: close matches are listed for the author. "
                     "Existence is not legal support: the report never says a decision backs a proposition."),
        epilog="examples:\n  ocl citations resolve 'BGE 136 III 513' '4A_747/2012'\n  ocl citations resolve --input references.jsonl --format jsonl > resolution.jsonl\n\nreferences.jsonl lines look like {\"reference\": \"BGE 136 III 513\", \"pinpoint\": \"2.3\"}\n")
    _input(resolve, "references", "references such as 'BGE 136 III 513' or '4A_747/2012'")
    resolve.add_argument("--language", choices=["de", "fr", "it"], default=lang, help="language of the returned citation string (default: de; OCL_LANGUAGE)")

    check = commands.add_parser("check", parents=[common], formatter_class=fmt,
                                help="check every citation and quotation in a draft or a party submission (Word, PDF, Markdown, text) and write a report",
                                description=("Read a draft or a party submission (.docx including footnotes, .pdf page by page, .md, .txt, .html), find the citations "
                                             "and the quotations next to them, check each one against the corpus, and write a report you can open (HTML, or Markdown "
                                             "with a .md report name). Exit 4 when anything needs attention. Citations are read as written; the report shows the "
                                             "service's citation strings and the served wording. Several files, or a directory (its .pdf/.docx/.md/.html/.txt files, "
                                             "not recursive), are checked one after the other: each gets its report next to it and an index report lists them "
                                             "(exit 2 when a file could not be read, for instance a scanned PDF without a text layer). PDF reading needs the pypdf "
                                             "package (pip install opencaselaw-cli[pdf]) or pdftotext (poppler) on PATH."),
                                epilog=("examples:\n  ocl check memo.docx\n  ocl check memo.docx --report memo-check.md\n  ocl check memo.docx --format json --no-report\n"
                                        "  ocl check eingabe.pdf --kind submission\n  ocl check eingaben/ --kind submission --report eingaben/index.md\n"))
    check.add_argument("draft", nargs="+", metavar="FILE",
                       help="the draft or submission: .docx, .pdf, .md, .txt or .html; several files, or a directory (its checkable files, not recursive)")
    check.add_argument("--report", metavar="FILE", help="where to write the report (default: <draft>.check.html next to the draft; a .md name gives Markdown); "
                                                        "with several inputs, where to write the index (default: check-index.html in their common folder)")
    check.add_argument("--no-report", action="store_true", help="do not write a report file")
    check.add_argument("--language", choices=["de", "fr", "it", "en"], default=lang,
                       help="language of the report and of the returned citation strings (default: de; OCL_LANGUAGE; en: English report, German citation strings)")
    check.add_argument("--kind", choices=["draft", "submission"], default=cfg.get("kind", "draft"),
                       help="what the document is (default: draft; OCL_KIND). submission: the report is titled for the party's filing, its first line counts "
                            "citations not in the corpus, differing and not checked, and a missing row states in full that unpublished decisions and the "
                            "decision under appeal are expected to be absent")
    check.add_argument("--statutes", metavar="FILE", help="offline: statutes database for the statute checks (default: statutes.sqlite next to the pack; OCL_STATUTES)")
    check.add_argument("--canton", metavar="XX", help="canton for bare § references such as \"§ 18 VRG\" (OCL_CANTON)")

    quotes = commands.add_parser("quotes", parents=[common], formatter_class=fmt, help="check quotations against the served text",
                                 description="Quotations: does what the draft puts in quotation marks stand in the decision it is attributed to?")
    quote_actions = quotes.add_subparsers(dest="action", required=True)
    qcheck = quote_actions.add_parser(
        "check", parents=[common], formatter_class=fmt,
        help="verify quotations word for word against the passage or the decision text",
        description=("Identify each reference the way `citations resolve` does, then look for the quotation in the "
                     "cited Erwägung and, failing that, in the whole decision text. Typography, OCR line hyphenation, "
                     "whitespace and the service's link markup are folded for the comparison only. quote_status is "
                     "exact (verbatim), near (best match at 90% or better, with the differing spans and the served "
                     "wording) or not_found (best window and ratio reported). The served wording is never rewritten; "
                     "exit 4 unless every quotation is exact."),
        epilog="examples:\n  ocl quotes check 'BGE 136 III 513 E. 2.3' --quote 'le contrat de travail conclu pour une durée indéterminée'\n  ocl quotes check --input quotes.jsonl --format jsonl\n\nquotes.jsonl lines look like {\"reference\": \"BGE 136 III 513\", \"pinpoint\": \"2.3\", \"quote\": \"...\"}\n")
    _input(qcheck, "references", "one reference (with --quote) or none with --input/--stdin rows carrying a quote field")
    qcheck.add_argument("--quote", help="the quotation to check against the single reference given")
    qcheck.add_argument("--pinpoint", help="Erwägung number for the single reference, e.g. 2.3 (an inline 'E. 2.3' in the reference also works)")
    qcheck.add_argument("--language", choices=["de", "fr", "it"], default=lang, help="language of the returned citation string (default: de; OCL_LANGUAGE)")

    cite = commands.add_parser("cite", parents=[common], formatter_class=fmt,
                               help="get the canonical citation string for a reference",
                               description="Get the canonical Swiss citation string (DE/FR/IT) and link for a reference. A long-form reference (court, docket, date) is identified the way `citations resolve` does it. A pinpoint given with --pinpoint or written inline (BGE 136 III 513 E. 2.3) is checked against the structure index; a missing Erwägung is reported (pinpoint_exists=false, exit 4) and the decision-level string is returned rather than a string formatted as if the Erwägung existed.",
                               epilog="example:\n  ocl cite 'BGE 136 III 513' --pinpoint 2.3 --language fr\n")
    _input(cite, "references", "references such as 'BGE 136 III 513' or '4A_747/2012'")
    cite.add_argument("--pinpoint", help="Erwägung number to append, e.g. 2.3; its existence is verified unless --no-verify-pinpoint")
    cite.add_argument("--no-verify-pinpoint", action="store_true", help="format the pinpoint without checking that the Erwägung exists")
    cite.add_argument("--language", choices=["de", "fr", "it"], default=lang, help="primary language of citation_string (default: de; OCL_LANGUAGE)")

    bundle = commands.add_parser("bundle", parents=[common], formatter_class=fmt,
                                 help="save the evidence for a question into a folder you can keep or share",
                                 description="Save research evidence into a folder with a provenance manifest.")
    bundle_actions = bundle.add_subparsers(dest="action", required=True, metavar="ACTION")
    create = bundle_actions.add_parser(
        "create", parents=[common], formatter_class=fmt,
        help="run a search and save the decisions, passages and statutes it needs",
        description=("Run a search, then save each selected decision (JSON and plain text), the requested "
                     "Erwägungen and statute articles into --out. INDEX.md lists what was saved in plain "
                     "language; manifest.json records every request, timestamp, source link and file hash. "
                     "Nothing already saved is ever overwritten; use --resume to finish an interrupted run."),
        epilog="example:\n  ocl bundle create 'Rachekündigung Art. 336 OR' --max-results 10 --passage 2 --law OR:336 --out rachekuendigung-2026-09\n")
    create.add_argument("query", help="search text; an empty string with filters enumerates by filters only")
    create.add_argument("--out", required=True, metavar="DIR", help="new folder for the evidence (or an existing bundle with --resume)")
    create.add_argument("--max-results", type=positive_int, default=10, help="how many decisions to select (default: 10; a text query allows at most 800)")
    _filters(create)
    create.add_argument("--resume", action="store_true", help="continue an interrupted or partly failed bundle with the same options")
    create.add_argument("--passage", action="append", default=[], metavar="NUMBER", help="also save this Erwägung of every selected decision; repeatable")
    create.add_argument("--law", action="append", default=[], metavar="[CANTON/]ABBR:ARTICLE", help="also save this statute article, e.g. OR:336 or ZH/StG:1; repeatable")

    verify = bundle_actions.add_parser("verify", parents=[common], formatter_class=fmt,
                                       help="re-hash every saved file against the manifest",
                                       description="Check a bundle folder: every file listed in manifest.json is re-hashed; changed, missing and unlisted files are reported. No network.",
                                       epilog="example:\n  ocl bundle verify rachekuendigung-2026-09\n")
    verify.add_argument("bundle", help="bundle folder")
    diff = bundle_actions.add_parser("diff", parents=[common], formatter_class=fmt,
                                     help="what changed between two bundles of the same question",
                                     description="Compare two bundles: decisions added or removed, decisions whose served text changed, item statuses, request differences and the corpus generation. No network.",
                                     epilog="example:\n  ocl bundle diff rachekuendigung-2026-08 rachekuendigung-2026-09\n")
    diff.add_argument("old", help="earlier bundle folder")
    diff.add_argument("new", help="later bundle folder")
    add = bundle_actions.add_parser("add", parents=[common], formatter_class=fmt,
                                    help="add decisions found elsewhere to an existing bundle",
                                    description="Add decisions to an existing bundle, with the bundle's passages (and any extra --passage). Saved files are never overwritten.",
                                    epilog="example:\n  ocl bundle add rachekuendigung-2026-09 bge_BGE_140_III_86 --passage 2\n")
    add.add_argument("bundle", help="existing bundle folder")
    _input(add, "ids", "decision IDs to add")
    add.add_argument("--passage", action="append", default=[], metavar="NUMBER", help="also save this Erwägung of each added decision; repeatable")

    tool = commands.add_parser("tool", parents=[common], formatter_class=fmt, help="call any of the service's research tools",
                               description=("Every tool the service offers to agents (leading cases, relevant considerations, scholarship, "
                                            "commentaries, practice, materials, legislation changes, case briefs, claim support ...), called "
                                            "directly. Results come back as the tool's structured output; a tool-reported error is exit 4."),
                               epilog="examples:\n  ocl tool list\n  ocl tool schema find_leading_cases\n  ocl tool call find_leading_cases query='Rachekündigung' limit=5\n  ocl tool call get_regeste decision_id=bge_BGE_136_III_513 --format json\n")
    tool_actions = tool.add_subparsers(dest="action", required=True)
    tlist = tool_actions.add_parser("list", parents=[common], formatter_class=fmt, help="list the tools with a one-line description",
                                    description="Every tool the service advertises, with its required arguments and whether it returns structured output.")
    tlist.add_argument("--long", action="store_true", help="show full descriptions and required arguments")
    tschema = tool_actions.add_parser("schema", parents=[common], formatter_class=fmt, help="show one tool's input and output schema",
                                      description="The tool's description, input schema (arguments, types, required) and output schema when it has one.")
    tschema.add_argument("name", help="tool name, as in `ocl tool list`")
    tcall = tool_actions.add_parser("call", parents=[common], formatter_class=fmt, help="call one tool with key=value arguments",
                                    description=("Call a tool. Arguments are key=value pairs; a value that parses as JSON is typed "
                                                 "(limit=5, flag=true, ids='[\"a\"]'), anything else is a string. --args passes one JSON "
                                                 "object; --stdin/--input run one call per JSONL row (an object of arguments)."))
    tcall.add_argument("name", help="tool name")
    tcall.add_argument("pairs", nargs="*", metavar="key=value", help="tool arguments")
    tcall.add_argument("--args", dest="args_json", metavar="JSON", help="arguments as one JSON object")
    tcall.add_argument("--stdin", action="store_true", help="read one JSON object of arguments per line from stdin")
    tcall.add_argument("--input", metavar="FILE", help="read one JSON object of arguments per line from FILE")

    doctor = commands.add_parser("doctor", parents=[common], formatter_class=fmt, help="check the connection, the server and this client",
                                 description=("Reachability, server database generation and size, tool count, one timed citation lookup, cache state. "
                                              "Exit 3 when the service does not answer. With --local: the pack's path, size, schema, generation, "
                                              "counts and age (a warning past 14 days), the SQLite version, one timed lookup; nothing is sent."))

    skills = commands.add_parser("skills", parents=[common], formatter_class=fmt, help="agent skills bundled with the client",
                                 description="The procedures an agent follows with ocl (citation check, research, evidence bundle), shipped in the package.",
                                 epilog="examples:\n  ocl skills list\n  ocl skills show citation-check\n  ocl skills install --claude\n")
    skill_actions = skills.add_subparsers(dest="action", required=True)
    skill_actions.add_parser("list", parents=[common], formatter_class=fmt, help="list the bundled skills",
                             description="The skill files shipped in this package, with their one-line descriptions and paths.")
    sshow = skill_actions.add_parser("show", parents=[common], formatter_class=fmt, help="print one skill file",
                                     description="Print a bundled SKILL.md verbatim, to read or to pipe into another harness.")
    sshow.add_argument("name", help="skill name, as in `ocl skills list`")
    sinstall = skill_actions.add_parser("install", parents=[common], formatter_class=fmt, help="copy the skills into an agent's skills directory",
                                        description="Copy every bundled skill to <dir>/<name>/SKILL.md; existing files are kept unless --force.")
    sinstall.add_argument("--claude", action="store_true", help="install into ~/.claude/skills/<name>/SKILL.md (Claude Code)")
    sinstall.add_argument("--dir", metavar="DIR", help="install into DIR/<name>/SKILL.md")
    sinstall.add_argument("--force", action="store_true", help="overwrite existing files")

    pack = commands.add_parser("pack", parents=[common], formatter_class=fmt, help="the offline verification pack",
                               description=("A single SQLite file with decision metadata, the service's citation strings, docket aliases and every "
                                            "indexed Erwägung, so citation, pinpoint and quotation checks run on this machine only: nothing about a "
                                            "draft leaves it. Weekly snapshot published on the HuggingFace mirror (CC0). No full texts, no search."),
                               epilog=("examples:\n  ocl pack pull\n  ocl pack pull --url file://server/share/latest.sqlite.gz   # from a share (Windows)\n"
                                       "  ocl pack verify\n  ocl --local doctor\n  ocl --local check memo.docx\n"
                                       "  ocl --local citations resolve --input refs.jsonl --format jsonl\n"
                                       "  ocl --local --pack D:\\packs\\verification_pack.sqlite check memo.docx\n"))
    pack_actions = pack.add_subparsers(dest="action", required=True)
    ppull = pack_actions.add_parser("pull", parents=[common], formatter_class=fmt, help="download the latest pack (several GB), verify it, install it",
                                    description=("Download the verification pack gzip (resumable: an interrupted pull continues from the bytes already saved), "
                                                 "verify it against the published .sha256 checksum, then unpack and install it atomically; the verification is "
                                                 "recorded next to the pack for `ocl pack verify`. Default location: the user data directory (--to for another file)."))
    ppull.add_argument("--to", metavar="FILE", help="where to store the pack (default: --pack / OCL_PACK, else verification_pack.sqlite in the user data directory: "
                                                    "%%LOCALAPPDATA%%\\ocl on Windows, $XDG_DATA_HOME/ocl or ~/.local/share/ocl elsewhere)")
    ppull.add_argument("--url", help="alternative source of the .sqlite.gz: an https URL, a file:// URL (file://server/share/latest.sqlite.gz on Windows) "
                                     "or a path; its .sha256 sidecar is read from the same place")
    ppull.add_argument("--insecure", action="store_true", help="install even when no .sha256 checksum is published next to the pack "
                                                              "(without it the pull stops with exit 2; the pack is then recorded as unverified)")
    pinfo = pack_actions.add_parser("info", parents=[common], formatter_class=fmt, help="show the installed pack's generation, size and verification",
                                    description="The pack's schema version, build date, database generation, decision and paragraph counts, file size, "
                                                "and how it was verified when pulled (source, checksum).")
    pinfo.add_argument("--path", metavar="FILE", help="a pack file other than the default one")
    pverify = pack_actions.add_parser("verify", parents=[common], formatter_class=fmt, help="report how the installed pack was verified (exit 4 if it was not)",
                                      description=("The checksum the pull compared against the published .sha256, the source it came from, and the pack's "
                                                   "schema version, build date and counts; exit 4 when the pack was never verified (pulled with --insecure, "
                                                   "copied by hand, or changed since)."))
    pverify.add_argument("--path", metavar="FILE", help="a pack file other than the default one")

    commands.add_parser("agent-guide", parents=[common], formatter_class=fmt, help="print the agent guide (contract, commands, rules)",
                        description="The compact guide agents read first: install, JSON contract, exit codes, commands, statuses and the rules.")

    completion = commands.add_parser("completion", parents=[common], formatter_class=fmt,
                                     help="print a shell completion script",
                                     description="Print a completion script. Install with, for example: ocl completion zsh > ~/.zfunc/_ocl (zsh), or eval \"$(ocl completion bash)\" in ~/.bashrc, or ocl completion fish > ~/.config/fish/completions/ocl.fish.")
    completion.add_argument("shell", choices=["bash", "zsh", "fish"], help="shell")
    hidden = commands.add_parser("__complete", parents=[common], add_help=False,
                                 description="Internal: completion candidates for the shell scripts.")
    hidden.add_argument("words", nargs="*", help="command line words so far")
    return parser


_COMPLETION_SCRIPTS = {
    "bash": ('_ocl_complete() {\n  local IFS=$\'\\n\'\n  COMPREPLY=($(ocl __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null))\n}\n'
             'complete -o default -F _ocl_complete ocl\n'),
    "zsh": ('#compdef ocl\n_ocl() {\n  local -a candidates\n  candidates=("${(@f)$(ocl __complete -- "${words[@]:1:$((CURRENT-1))}" 2>/dev/null)}")\n'
            '  compadd -- "${candidates[@]}"\n}\ncompdef _ocl ocl\n'),
    "fish": ('complete -c ocl -f -a \'(ocl __complete -- (commandline -cpo)[2..] (commandline -ct) 2>/dev/null)\'\n'),
}


def complete(parser, words: list[str]) -> list[str]:
    """Candidates for the last (possibly empty) word, walking the parser tree."""
    partial = words[-1] if words else ""
    prior = words[:-1]
    current = parser
    previous_action = None
    used: set[str] = set()
    for word in prior:
        sub = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
        if previous_action is not None and previous_action.nargs is None and not word.startswith("-"):
            previous_action = None
            continue
        previous_action = None
        if sub is not None and word in sub.choices:
            current = sub.choices[word]
            used = set()
            continue
        if word.startswith("-"):
            used.add(word)
            action = next((a for a in current._actions if word in a.option_strings), None)
            if action is not None and action.nargs is None and not isinstance(action, argparse._StoreConstAction):
                previous_action = action
    if previous_action is not None and previous_action.choices:
        return sorted(str(c) for c in previous_action.choices if str(c).startswith(partial))
    if previous_action is not None:
        return []
    candidates: list[str] = []
    for action in current._actions:
        if isinstance(action, argparse._SubParsersAction):
            candidates += [c for c in action.choices if not c.startswith("__")]
        elif action.option_strings and action.help != argparse.SUPPRESS:
            candidates += [o for o in action.option_strings if o.startswith("--") and o not in used]
        elif not action.option_strings and action.choices:
            candidates += [str(c) for c in action.choices]
    return sorted(c for c in candidates if c.startswith(partial))


def read_inputs(args, field="decision_id") -> list[dict]:
    """Validate every input before making requests; metadata lines are ignored."""
    inputs = [{field: value} for value in getattr(args, "ids", getattr(args, "references", []))]
    sources = []
    if getattr(args, "stdin", False):
        sources.append(("stdin", sys.stdin.read().lstrip("\ufeff")))
    if getattr(args, "input", None):
        sources.append((args.input, Path(args.input).read_text(encoding="utf-8-sig")))
    for name, content in sources:
        for line_number, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if line[0] in '{["':
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{name}:{line_number}: invalid JSON") from exc
                if isinstance(row, str):
                    row = {field: row}
                if isinstance(row, dict) and row.get("_type") == "pagination":
                    continue
                if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f"{name}:{line_number}: expected a string or object containing {field}")
                if row.get("pinpoint") is not None and not isinstance(row["pinpoint"], str):
                    raise ValueError(f"{name}:{line_number}: pinpoint must be a string")
                inputs.append(row)
            else:
                inputs.append({field: line})
    if not inputs or any(not row[field].strip() for row in inputs):
        raise ValueError("provide at least one reference/ID, --stdin or --input FILE")
    return inputs


def search(client, params: dict, max_results: int, page_size: int = 50, collapse: bool = True) -> tuple[dict, int]:
    """Bounded retrieval that keeps every server page's metadata and partial results.

    A text query is ranked over one bounded candidate pool whose size depends
    on the requested window, so its pages are not composable: the selection
    is fetched as ONE request of ``max_results`` (server cap 800). Filter-only
    searches enumerate an exact, stably ordered set and are paged. Exhausting
    retrievable pages never proves corpus-wide exhaustiveness.

    With ``collapse`` (the default), rows the service links to one
    ``canonical_decision_id`` (the same ruling stored under several ids) are
    reduced to one row per ruling: the canonical record when the page holds
    it, at the group's first-seen rank. Nothing is rewritten; the dropped ids
    are listed under ``_client.collapsed_representations``.
    """
    if max_results <= 0 or not 1 <= page_size <= 800:
        raise ValueError("max-results must be positive; page-size must be between 1 and 800")
    ranked = bool((params.get("query") or "").strip())
    if ranked and max_results > RANKED_MAX_RESULTS:
        raise ValueError(
            f"a text query is ranked over one bounded candidate pool; --max-results is limited to "
            f"{RANKED_MAX_RESULTS} for text queries (filter-only searches without query text enumerate further)")
    start = params.get("offset", 0)
    offset = start
    results, pages, errors = [], [], []
    seen: set = set()
    groups: dict = {}  # canonical_decision_id -> {"index", "kept", "dropped"}
    duplicates = 0
    last = {}
    while len(results) < max_results:
        limit = max_results if ranked else min(page_size, max_results - len(results))
        try:
            page = client.get("/api/decisions", {**params, "limit": limit, "offset": offset})
            rows = page.get("results")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise APIError(None, "search response has no valid results array")
            if len(rows) > limit:
                raise APIError(None, "search response exceeds the requested page size")
            if not isinstance(page.get("has_more"), bool) or not isinstance(page.get("total_is_lower_bound"), bool):
                raise APIError(None, "search response lacks completeness metadata")
            metadata = {key: value for key, value in page.items() if key != "results"}
            pages.append(metadata)
            for row in rows:
                decision_id = row.get("decision_id")
                if isinstance(decision_id, str):
                    if decision_id in seen:
                        duplicates += 1
                        continue
                    seen.add(decision_id)
                canonical = row.get("canonical_decision_id") if collapse and isinstance(decision_id, str) else None
                if isinstance(canonical, str) and canonical:
                    group = groups.get(canonical)
                    if group is not None:
                        # The same ruling under another id: one row per ruling, the
                        # canonical record preferred, at the group's first-seen rank.
                        if decision_id == canonical and group["kept"] != canonical:
                            group["dropped"].append(group["kept"])
                            results[group["index"]] = row
                            group["kept"] = canonical
                        else:
                            group["dropped"].append(decision_id)
                        continue
                    groups[canonical] = {"index": len(results), "kept": decision_id, "dropped": []}
                results.append(row)
            last = metadata
            if ranked or not page["has_more"]:
                break
            next_offset = page.get("next_offset")
            if not rows or type(next_offset) is not int or next_offset != offset + len(rows):
                raise APIError(None, "search pagination did not advance consistently")
            offset = next_offset
        except APIError as exc:
            if not pages:
                raise
            errors.append({"offset": offset, **exc.to_dict()})
            break
    collapsed = [{"kept": g["kept"], "dropped": g["dropped"]} for g in groups.values() if g["dropped"]]
    envelope = {**last, "results": results, "returned": len(results), "offset": start,
                "limit": max_results, "total_is_lower_bound": any(p["total_is_lower_bound"] for p in pages),
                "_client": {"pages": pages, "errors": errors, "requests": getattr(client, "requests", None),
                            "ranked_single_request": ranked,
                            "duplicates_dropped": duplicates,
                            "duplicates_collapsed": sum(len(g["dropped"]) for g in collapsed),
                            "collapsed_representations": collapsed,
                            "max_results_reached": len(results) >= max_results,
                            "retrieval_complete": not errors and last.get("has_more") is False}}
    return envelope, 4 if errors else 0


_RESOLUTION_ROW_KEYS = ("checked_at", "legal_support_assessed", "reference")


def _project(row, fields):
    if not fields or not isinstance(row, dict):
        return row
    selected = {key: row[key] for key in fields if key in row}
    if all(key in row for key in _RESOLUTION_ROW_KEYS):
        # A resolution row: a real projection. What must survive is the
        # verdict and anything that qualifies it, never the evidence blocks.
        for key in ("reference", "status", "error", "reason", "note", "notes", "discrepancies",
                    "pinpoint_status", "pinpoint_note", "available_e_numbers", "input", "canonical_decision_id"):
            if key in row:
                selected[key] = row[key]
        return selected
    # Errors and uncertainty must survive convenience projections.
    for key in ("error", "errors", "status", "ambiguous", "candidates", "_client",
                "exists", "pinpoint_exists", "close_matches", "warning", "warnings",
                "note", "_note", "hint", "recency_note", "completeness", "manifest", "requested_e_number",
                "bundle", "scope", "provenance", "official_source_available",
                "pinpoint_status", "legal_support_assessed", "reason", "available_e_numbers", "identity_check"):
        if key in row:
            selected[key] = row[key]
    for key in row:
        if key.endswith(("_warning", "_warnings", "_note")):
            selected[key] = row[key]
    if row.get("status") == "ambiguous" and "lookup" in row:
        selected["lookup"] = row["lookup"]
    if "articles" in row:
        for key in ("sr_number", "abbreviation", "language", "canton", "version", "as_of",
                    "consolidation_date", "snapshot_date", "source_url", "source_label", "pdf_url",
                    "fedlex_snapshot_uri", "work_uri", "work_entry_in_force", "work_no_longer_in_force",
                    "work_in_force_status", "verbatim_status", "pending_changes"):
            if key in row:
                selected[key] = row[key]
    return selected


def output_format(args) -> str:
    fmt = getattr(args, "format", None)
    if fmt:
        return fmt
    try:
        return "text" if sys.stdout.isatty() else "json"
    except (AttributeError, ValueError):
        return "json"


def colour_enabled(args, stream) -> bool:
    choice = getattr(args, "color", "auto") or "auto"
    if choice == "never" or (choice == "auto" and (os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb")):
        return False
    if choice == "always":
        return True
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def emit(value, args):
    if isinstance(value, dict) and "_raw" in value:
        sys.stdout.write(value["_raw"] if value["_raw"].endswith("\n") or not value["_raw"] else value["_raw"] + "\n")
        return
    fmt = output_format(args)
    if fmt == "text":
        style = render.Style(colour_enabled(args, sys.stdout))
        print(render.render(value, args, style, render.terminal_width()))
        return
    if fmt == "table":
        print(render.render_table(value, args, render.terminal_width(maximum=200)))
        return
    if fmt == "csv":
        print(render.render_csv(value, args))
        return
    if fmt == "md":
        print(render.render_md(value, args, render.terminal_width(maximum=200)))
        return
    fields = [field.strip() for field in args.fields.split(",") if field.strip()] if args.fields else None
    collections = [key for key in ("results", "incoming", "outgoing")
                   if isinstance(value, dict) and isinstance(value.get(key), list)]
    if fmt == "jsonl" and collections:
        for key in collections:
            for row in value[key]:
                row = _project(row, fields)
                if key in {"incoming", "outgoing"}:
                    row = {**row, "_direction": key}
                print(json.dumps(row, ensure_ascii=False, allow_nan=False))
        metadata = {key: item for key, item in value.items() if key not in collections}
        print(json.dumps({"_type": "pagination", **metadata}, ensure_ascii=False, allow_nan=False))
    else:
        if collections:
            value = {**value, **{key: [_project(row, fields) for row in value[key]] for key in collections}}
        else:
            value = _project(value, fields)
        print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=None if fmt == "jsonl" else 2))


def _diagnostic(message: str, args=None) -> None:
    style = render.Style(colour_enabled(args, sys.stderr) if args is not None else False)
    print(style.yellow("ocl: ") + message, file=sys.stderr)


def pack_path(args) -> Path:
    """The pack file --local uses: --pack / OCL_PACK / config, else the pulled default."""
    from .local import default_pack_path
    given = getattr(args, "pack", None)
    return Path(given).expanduser() if given else default_pack_path()


def create_client(args):
    log = (lambda line: print("ocl: " + line, file=sys.stderr)) if getattr(args, "verbose", False) else None
    if getattr(args, "local", False):
        from .local import LocalClient
        if getattr(args, "statutes", None):
            os.environ["OCL_STATUTES"] = str(Path(args.statutes).expanduser())  # read by statutes.local_law
        return LocalClient(pack_path(args), log=log)
    if getattr(args, "pack", None):
        print("ocl: --pack given without --local; checking online (add --local to stay on this machine)", file=sys.stderr)  # a missing or unreadable pack is a ValueError: exit 2, no traceback
    return Client(base_url=args.base_url, timeout=args.timeout, retries=args.retries, log=log,
                  cache_dir=getattr(args, "cache", None) or None)


# A canonical id: lowercase court slug, underscore, label (bge_BGE_136_III_513, zh_obergericht_LA210005).
_ID_LIKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_\S")


def _decision_id(client, reference, language="de"):
    """The decision an id, docket, citation or long-form reference names; ResolutionError otherwise.
    A canonical id, or a bare token that is neither a docket nor a collection label, is used as is."""
    reference = reference.strip()
    parsed = parse_reference(reference)
    if _ID_LIKE.match(parsed.core) and "/" not in parsed.core:
        return parsed.core
    if "/" in reference or parsed.long_form or parsed.dockets or parsed.bge_label:
        return identify(client, reference, language)
    return reference


def _transport(error: dict) -> bool:
    """A failure a retry may fix: no HTTP answer, a server-side error, or a refusal that
    is about the client's standing (rate limit, authorisation) rather than the item."""
    status = error.get("status")
    if error.get("kind") == "resolution":
        return False
    return status is None or (isinstance(status, int) and (status >= 500 or status in (401, 403, 408, 429)))


def _one(args, client, kind, field, index, row):
    """Fetch one batch item; returns (result, None) or (None, error dict)."""
    try:
        if kind == "get":
            decision_id = _decision_id(client, row[field], getattr(args, "language", None) or "de")
            result = client.get("/api/decisions/" + quote(decision_id, safe=""), {"full_text": not args.no_full_text})
        else:
            reference = row[field].strip()
            parsed = parse_reference(reference)
            explicit = row["pinpoint"] if row.get("pinpoint") is not None else args.pinpoint
            try:
                pinpoint = normalise_pinpoint(explicit) if explicit else parsed.pinpoint
            except ValueError as exc:
                return None, {"index": index, field: row[field], "status": 400, "kind": "input", "message": str(exc)}
            language = args.language
            if _ID_LIKE.match(parsed.core) and "/" not in parsed.core:
                decision_id = parsed.core
                result = client.get("/api/cite", {"reference": decision_id, "language": language})
                if result.get("error"):
                    raise APIError(200, str(result["error"]))
            else:
                # Every other reference is identified the way `citations resolve`
                # does it: the decision must carry the label the author wrote. A
                # docket fragment the service matches by substring is never cited.
                try:
                    resolved = identify_row(client, reference, language)
                except ResolutionError as exc:
                    if exc.outcome == "missing" and isinstance(exc.row.get("citation"), dict):
                        result = dict(exc.row["citation"])
                        result.setdefault("exists", False)
                        result["reference_as_written"] = reference
                        return result, None
                    raise
                decision_id = resolved["decision_id"]
                result = dict(resolved["citation"])
                if result.get("decision_id") != decision_id:
                    result = client.get("/api/cite", {"reference": decision_id, "language": language})
                    if result.get("error"):
                        raise APIError(200, str(result["error"]))
                result["reference_as_written"] = reference
                result["resolved_decision_id"] = decision_id
                result["identity_check"] = resolved.get("identity_check")
                for key in ("query", "other_dockets", "canonical_decision_id"):
                    if resolved.get(key):
                        result[key] = resolved[key]
            if pinpoint and result.get("exists") is True and isinstance(result.get("decision_id"), str):
                result["pinpoint"] = pinpoint
                if parsed.pinpoint and not explicit:
                    result["pinpoint_source"] = "reference"
                if getattr(args, "no_verify_pinpoint", False):
                    formatted = client.get("/api/cite", {"reference": result["decision_id"], "pinpoint": pinpoint, "language": language})
                    if isinstance(formatted.get("citation_string"), str):
                        result["citation_string"] = formatted["citation_string"]
                    result["pinpoint_status"] = "not_checked"
                else:
                    # A formatted pinpoint must point at a passage that exists; a
                    # missing one leaves the decision-level string in place.
                    passage, status, error = fetch_passage(client, result["decision_id"], pinpoint)
                    if error is not None and error.status not in (200, 404):
                        raise error
                    result["pinpoint_exists"] = status == "retrieved"
                    result["pinpoint_status"] = status
                    if status == "retrieved":
                        formatted = client.get("/api/cite", {"reference": result["decision_id"], "pinpoint": pinpoint, "language": language})
                        if isinstance(formatted.get("citation_string"), str):
                            result["citation_string"] = formatted["citation_string"]
                    elif status == "parent_retrieved":
                        result["parent_e_number"] = passage.get("e_number")
                        result["pinpoint_note"] = (f"E. {pinpoint} is not indexed as such; E. {passage.get('e_number')} exists and "
                                                   "contains it. Locate the lettered part in that text before citing it")
                    else:
                        result["pinpoint_note"] = (f"E. {pinpoint} is not in the structure index of {result['decision_id']}; the "
                                                   "citation string is the decision-level one. Check the decision text before citing it")
                        response = getattr(error, "response", None)
                        if isinstance(response, dict) and response.get("available_e_numbers"):
                            result["available_e_numbers"] = response["available_e_numbers"]
        if result.get("error"):
            return None, {"index": index, field: row[field], "status": 200, "message": str(result["error"]), "response": result}
        return result, None
    except APIError as exc:
        return None, {"index": index, field: row[field], **exc.to_dict()}


def _batch(args, client, kind):
    field = "decision_id" if kind == "get" else "reference"
    inputs = read_inputs(args, field)
    jobs = max(1, min(int(getattr(args, "jobs", None) or DEFAULT_JOBS), 8))
    rows, errors = [], []
    consecutive_transport_failures = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for start in range(0, len(inputs), jobs):
            chunk = list(enumerate(inputs[start:start + jobs], start))
            for result, error in pool.map(lambda pair: _one(args, client, kind, field, pair[0], pair[1]), chunk):
                if error is not None:
                    errors.append(error)
                    consecutive_transport_failures = consecutive_transport_failures + 1 if _transport(error) else 0
                else:
                    rows.append(result)
                    consecutive_transport_failures = 0
            if consecutive_transport_failures >= BREAKER_THRESHOLD and start + jobs < len(inputs):
                for index, row in enumerate(inputs[start + jobs:], start + jobs):
                    errors.append({"index": index, field: row[field], "status": None,
                                   "message": f"skipped after {BREAKER_THRESHOLD} consecutive transport failures; rerun when the service is reachable"})
                break
    unresolved = any(row.get("exists") is False or row.get("pinpoint_exists") is False for row in rows)
    if len(inputs) == 1 and not errors:
        return rows[0], 4 if unresolved else 0
    # 3 only for failures a retry may fix; a decision or passage the service does
    # not have, or a reference that names no single decision, is 4.
    code = (3 if any(_transport(error) for error in errors) else 4) if errors else (4 if unresolved else 0)
    return {"results": rows, "errors": errors, "requested": len(inputs), "returned": len(rows),
            "requests": getattr(client, "requests", None)}, code


def _response(value):
    """A service answer that carries an error is 'not there' (4); transport failures raise APIError."""
    return value, 4 if value.get("error") else 0


def _package_text(relative: str) -> str:
    path = Path(__file__).parent / relative
    return path.read_text(encoding="utf-8")


def _typed(value: str):
    """A key=value argument: JSON when it parses (numbers, booleans, lists, objects), else the string."""
    try:
        return json.loads(value)
    except ValueError:
        return value


def _coerce(value: str, declared: dict | None):
    """A key=value argument typed by the tool's input schema: a declared string
    stays a string ("pinpoint=1", "article=336"); other declared types and
    undeclared keys take the JSON reading when the value parses."""
    kind = (declared or {}).get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    if kind == "string":
        return value
    typed = _typed(value)
    if kind in ("integer", "number", "boolean", "array", "object") and isinstance(typed, str):
        raise ValueError(f"{value!r} is not a valid {kind}")
    return typed


def _tool_argument_sets(args, properties: dict | None = None) -> list[dict]:
    sets = []
    if getattr(args, "args_json", None):
        parsed = json.loads(args.args_json)
        if not isinstance(parsed, dict):
            raise ValueError("--args must be a JSON object")
        sets.append(parsed)
    pairs = {}
    for pair in getattr(args, "pairs", None) or []:
        key, separator, value = pair.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"argument {pair!r} is not key=value")
        pairs[key.strip()] = _coerce(value, (properties or {}).get(key.strip()))
    if pairs:
        if sets:
            sets[0] = {**sets[0], **pairs}
        else:
            sets.append(pairs)
    sources = []
    if getattr(args, "stdin", False):
        sources.append(("stdin", sys.stdin.read()))
    if getattr(args, "input", None):
        sources.append((args.input, Path(args.input).read_text(encoding="utf-8-sig")))
    for name, content in sources:
        for number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{name}:{number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{name}:{number}: expected a JSON object of arguments")
            sets.append(row.get("arguments") if isinstance(row.get("arguments"), dict) else row)
    if not sets:
        sets.append({})
    return sets


def _tool_result(name: str, result: dict) -> dict:
    """The tool's structured output when present, else its text content; always says which tool answered."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        value = dict(structured)
    else:
        value = {"content": [c for c in result.get("content", []) if isinstance(c, dict)]}
        texts = [c.get("text") for c in value["content"] if c.get("type") == "text" and isinstance(c.get("text"), str)]
        if texts:
            value["text"] = "\n".join(texts)
    value["_tool"] = name
    if result.get("isError"):
        value["_is_error"] = True
        if "error" not in value:
            value["error"] = value.get("text") or f"{name} reported an error"
    return value


def tool_command(args, client):
    if args.action == "list":
        tools = client.mcp_tools()
        rows = []
        for tool in tools:
            schema = tool.get("inputSchema") or {}
            rows.append({"name": tool.get("name"), "description": (tool.get("description") or "").strip(),
                         "required": list(schema.get("required") or []), "arguments": sorted((schema.get("properties") or {}).keys()),
                         "structured_output": bool(tool.get("outputSchema"))})
        return {"tools": rows, "count": len(rows)}, 0
    if args.action == "schema":
        for tool in client.mcp_tools():
            if tool.get("name") == args.name:
                return {"name": tool.get("name"), "description": tool.get("description"), "inputSchema": tool.get("inputSchema"),
                        "outputSchema": tool.get("outputSchema")}, 0
        raise ValueError(f"unknown tool {args.name!r}; see `ocl tool list`")
    properties = None
    if getattr(args, "pairs", None):
        # type the key=value pairs by the tool's own schema (one tools/list, cached when a cache is on)
        try:
            for tool in client.mcp_tools():
                if tool.get("name") == args.name:
                    properties = (tool.get("inputSchema") or {}).get("properties") or {}
                    break
        except APIError:
            properties = None
    sets = _tool_argument_sets(args, properties)
    results, errors = [], []
    for index, arguments in enumerate(sets):
        try:
            value = client.tool_json(args.name, arguments)
            value.setdefault("_tool", args.name)
            if value.get("_is_error") and "error" not in value:
                value["error"] = value.get("text") or f"{args.name} reported an error"
            results.append(value)
        except APIError as exc:
            errors.append({"index": index, "arguments": arguments, **exc.to_dict()})
    if len(sets) == 1 and not errors:
        return results[0], 4 if results[0].get("_is_error") else 0
    if len(sets) == 1:
        return {"results": [], "errors": errors, "requested": 1, "returned": 0}, 3 if _transport(errors[0]) else 4
    failed = any(r.get("_is_error") for r in results)
    code = (3 if any(_transport(e) for e in errors) else 4) if errors else (4 if failed else 0)
    return {"results": results, "errors": errors, "requested": len(sets), "returned": len(results),
            "requests": getattr(client, "requests", None)}, code


def _pack_age_days(built_at) -> int | None:
    from datetime import datetime, timezone
    if not isinstance(built_at, str) or not built_at.strip():
        return None
    try:
        built = datetime.fromisoformat(built_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - built).total_seconds() // 86400))


PACK_STALE_DAYS = 14


def doctor_offline(args, client):
    """`ocl --local doctor`: the pack on this machine, nothing sent. Exit 0 when the
    pack opens and answers; a stale pack is a warning, not a failure."""
    import platform
    import sqlite3
    import time as _time
    report = {"client_version": __version__, "python": platform.python_version(), "platform": platform.platform(terse=True),
              "mode": "offline", "pack": str(client.pack_path), "ok": True}
    try:
        meta = client.meta
        report["pack_bytes"] = client.pack_path.stat().st_size
        for key in ("schema_version", "built_at", "db_generation"):
            report[key] = meta.get(key)
        for key in ("decisions", "paragraphs"):
            value = meta.get(key)
            report[key] = int(value) if isinstance(value, str) and value.isdigit() else value
        age = _pack_age_days(meta.get("built_at"))
        report["pack_age_days"] = age
        report["sqlite_version"] = sqlite3.sqlite_version
        warnings = []
        if age is None:
            warnings.append("the pack carries no readable built_at; its age is unknown")
        elif age > PACK_STALE_DAYS:
            warnings.append(f"the pack is {age} days old (weekly snapshots; run `ocl pack pull` for the current one)")
        started = _time.monotonic()
        cite = client.get("/api/cite", {"reference": "BGE 136 III 513", "language": "de"})
        report["cite_ms"] = round(1000 * (_time.monotonic() - started))
        report["cite_ok"] = cite.get("exists") is True and cite.get("decision_id") in _CITE_CHECK_IDS
        if not report["cite_ok"]:
            warnings.append("the reference check did not find BGE 136 III 513 in the pack; the pack may be partial")
        if warnings:
            report["warnings"] = warnings
    except (APIError, OSError, ValueError) as exc:
        report.update(ok=False, error=exc.to_dict() if isinstance(exc, APIError) else {"status": None, "message": str(exc)})
        return report, 3
    return report, 0


# BGE 136 III 513 is served as "bge_BGE_136_III_513" while the entscheidsuche
# rows are in the corpus and as the direct scraper's "bge_136 III 513" once
# they are retired; either answer means the reference resolved.
_CITE_CHECK_IDS = ("bge_BGE_136_III_513", "bge_136 III 513")


def doctor(args, client):
    import platform
    import time as _time
    if getattr(client, "offline", False):
        return doctor_offline(args, client)
    report = {"client_version": __version__, "python": platform.python_version(), "platform": platform.platform(terse=True),
              "base_url": getattr(client, "base_url", args.base_url),
              "cache_dir": str(client.cache_dir) if getattr(client, "cache_dir", None) else None, "ok": True}
    try:
        started = _time.monotonic()
        health = client.get("/health")
        report["health"] = {k: health.get(k) for k in ("status", "decisions", "db_generation") if k in health}
        report["health_ms"] = round(1000 * (_time.monotonic() - started))
        started = _time.monotonic()
        tools = client.mcp_tools()
        report["tools"] = {"count": len(tools), "structured_output": sum(1 for t in tools if t.get("outputSchema"))}
        report["tools_ms"] = round(1000 * (_time.monotonic() - started))
        started = _time.monotonic()
        cite = client.get("/api/cite", {"reference": "BGE 136 III 513", "language": "de"})
        report["cite_ms"] = round(1000 * (_time.monotonic() - started))
        report["cite_ok"] = cite.get("exists") is True and cite.get("decision_id") in _CITE_CHECK_IDS
        if not report["cite_ok"]:
            report["ok"] = False
            report["note"] = "the reference check did not return the expected decision"
    except APIError as exc:
        report.update(ok=False, error=exc.to_dict())
        return report, 3
    return report, 0 if report["ok"] else 3


def _skills_dir() -> Path:
    return Path(__file__).parent / "skills"


def skills_command(args):
    available = sorted(p.parent.name for p in _skills_dir().glob("*/SKILL.md"))
    if args.action == "list":
        rows = []
        for name in available:
            text = (_skills_dir() / name / "SKILL.md").read_text(encoding="utf-8")
            description = ""
            for line in text.splitlines():
                if line.startswith("description:"):
                    description = line.partition(":")[2].strip()
                    break
            rows.append({"name": name, "description": description, "path": str(_skills_dir() / name / "SKILL.md")})
        return {"skills": rows}, 0
    if args.action == "show":
        if args.name not in available:
            raise ValueError(f"unknown skill {args.name!r}; available: {', '.join(available)}")
        return {"_raw": (_skills_dir() / args.name / "SKILL.md").read_text(encoding="utf-8")}, 0
    target = Path(args.dir).expanduser() if getattr(args, "dir", None) else (Path.home() / ".claude" / "skills" if getattr(args, "claude", False) else None)
    if target is None:
        raise ValueError("say where: --claude (~/.claude/skills) or --dir DIR")
    installed, skipped = [], []
    for name in available:
        destination = target / name / "SKILL.md"
        if destination.exists() and not getattr(args, "force", False):
            skipped.append(str(destination))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((_skills_dir() / name / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        installed.append(str(destination))
    return {"installed": installed, "skipped_existing": skipped, "directory": str(target)}, 0


def pack_command(args):
    from .local import PACK_URL, pack_report, pull
    default = pack_path(args)  # --pack / OCL_PACK / config, else the user data directory
    if args.action == "pull":
        log = (lambda line: print("ocl: " + line, file=sys.stderr, flush=True))
        return pull(Path(args.to).expanduser() if getattr(args, "to", None) else default, url=getattr(args, "url", None) or PACK_URL,
                    log=log, insecure=getattr(args, "insecure", False)), 0
    report = pack_report(Path(args.path).expanduser() if getattr(args, "path", None) else default)
    if not report.get("installed"):
        return report, 4
    if args.action == "verify":
        return report, 0 if report.get("verified") else 4
    return report, 0


def run(args, client):
    if args.command in ("bundle", "quotes", "check") or (args.command == "citations" and args.action == "resolve"):
        from . import workflows
        return workflows.run(args, client)
    if args.command == "completion":
        return {"_raw": _COMPLETION_SCRIPTS[args.shell]}, 0
    if args.command == "tool":
        return tool_command(args, client)
    if args.command == "doctor":
        return doctor(args, client)
    if args.command == "skills":
        return skills_command(args)
    if args.command == "agent-guide":
        return {"_raw": _package_text("AGENTS.md")}, 0
    if args.command == "pack":
        return pack_command(args)
    if args.command == "__complete":
        return {"_raw": "\n".join(complete(build_parser(config={}), list(args.words)))}, 0
    if args.command == "decisions":
        if args.action == "search":
            params = {name: getattr(args, name) for name in ("query", "court", "canton", "language", "date_from", "date_to", "chamber", "marked_for_publication", "sort", "offset")}
            params.update(fields=args.detail, include_pinpoint=not args.no_pinpoint)
            return search(client, params, args.max_results, args.page_size, collapse=not args.no_collapse)
        if args.action == "get":
            return _batch(args, client, "get")
        decision_id = _decision_id(client, args.decision_id)
        number = normalise_pinpoint(args.number)
        if not number:
            raise ValueError("Give an Erwägung number such as 2.3, 3b or 3c/aa")
        passage, status, error = fetch_passage(client, decision_id, number)
        if status == "retrieved":
            return passage, 0
        if status == "parent_retrieved":
            passage.update(requested_e_number=number,
                           note=(f"E. {number} is not indexed as such; this is E. {passage.get('e_number')}, which contains it. "
                                 "Locate the lettered part before quoting."))
            return passage, 4
        if error.status not in (200, 404):
            raise error
        value = {"error": error.to_dict(), "decision_id": decision_id, "requested_e_number": number}
        response = getattr(error, "response", None)
        if isinstance(response, dict):
            value.update({key: item for key, item in response.items() if key != "error"})
        return value, 4
    if args.command == "laws":
        params = {name: getattr(args, name) for name in ("article", "sr_number", "as_of", "canton", "language")}
        result, code = _response(client.get("/api/laws/" + quote(args.abbreviation, safe=""), params))
        if not code and args.article and (result.get("articles") == [] or result.get("text_status") in ("heading_only", "empty")):
            # No article text was recovered (an older edition whose PDF window
            # holds only the heading): unresolved, not success.
            code = 4
        return result, code
    if args.command == "citations":
        if args.limit > 200:
            raise ValueError("citation limit must be between 1 and 200")
        params = {name: getattr(args, name) for name in ("direction", "limit", "offset", "min_confidence")}
        decision_id = _decision_id(client, args.decision_id)
        return _response(client.get("/api/citations/" + quote(decision_id, safe=""), params))
    return _batch(args, client, "cite")


_PAIR_TOKEN = re.compile(r"^[A-Za-z_][\w.\-]*=")


def _reorder_tool_call(argv: list[str]) -> list[str]:
    """`ocl tool call NAME key=value ... --option` in any order: argparse before
    Python 3.12 does not take positionals that follow an option, so the
    key=value tokens are moved right after the tool name."""
    if len(argv) < 3 or argv[:2] != ["tool", "call"] or argv[2].startswith("-"):
        return argv
    head, rest = argv[:3], argv[3:]
    pairs, others = [], []
    skip = False
    for i, token in enumerate(rest):
        if skip:
            others.append(token); skip = False; continue
        if token.startswith("-"):
            others.append(token)
            # an option with a separate value keeps its value out of the pair scan
            if token in ("--args", "--input", "--base-url", "--timeout", "--retries", "--format", "--fields", "--color", "--jobs", "--cache", "--pack") and "=" not in token:
                skip = True
            continue
        (pairs if _PAIR_TOKEN.match(token) else others).append(token)
    return head + pairs + others


def main(argv=None):
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    args = None
    try:
        parser = build_parser()
        if argv is None:
            argv = sys.argv[1:]
        args = parser.parse_args(_reorder_tool_call(list(argv)))
        needs_client = args.command not in ("completion", "skills", "agent-guide", "pack", "__complete")
        value, code = run(args, create_client(args) if needs_client else None)
        emit(value, args)
        if code:
            _diagnostic("some requested items failed or did not resolve; see the output for details", args)
        sys.stdout.flush()
        return code
    except APIError as exc:
        if output_format(args) == "text":
            _diagnostic(f"{exc.message}" + (f" (HTTP {exc.status})" if exc.status else ""), args)
        else:
            print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 4 if isinstance(exc, ResolutionError) or not _transport(exc.to_dict()) else 3
    except BrokenPipeError:
        # Prevent a second BrokenPipeError from Python's interpreter shutdown.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, AttributeError, ValueError):
            pass
        return 0
    except (ValueError, OSError) as exc:
        _diagnostic(str(exc), args)
        return 2
    except KeyboardInterrupt:
        return 130
