# AGENTS.md - OpenCaseLaw Codex Guidance

OpenCaseLaw is a production Swiss legal corpus and public API. Treat this repo
as live legal infrastructure: favor evidence, small reversible changes, and
explicit escalation over broad autonomous edits.

## Bootstrap

Before non-trivial maintenance work, read:

1. `CLAUDE.md`
2. `~/.claude/projects/-Users-jonashertner-caselaw-repo-1/memory/MEMORY.md`
3. Relevant memory topic files from that index
4. `TECHNICAL_OVERVIEW.txt`
5. The private maintenance log under `OPENCASELAW_PRIVATE_ROOT` when set;
   otherwise maintenance scripts resolve `opencaselaw-internal` beside the
   primary checkout (including when invoked from a linked worktree)

Verify memory claims against current code, logs, or production probes before
using them as ground truth.

## Hard Rules

- Production read DBs must be opened `mode=ro&immutable=1`.
- Never add write paths to live `decisions.db`, `reference_graph.db`, or
  `decision_structure.db`.
- Preserve atomic-swap rebuild behavior; do not replace it with in-place DB
  writes or naive copies.
- All FTS5 user input must pass through `_sanitize_fts5`.
- Do not construct legal citation strings or direct quotations yourself. Use
  the project citation and verbatim-text sources.
- New scraper data paths must use official sources, not entscheidsuche. Retire
  `es_*` feeds only after verified direct parity and no coverage loss.
- Tests stay offline unless a target explicitly says it is a live check.
- Never read or print secrets from `.env*`, crontab, systemd environment files,
  or production config.

## Autonomous Boundary

Autonomous Codex work may measure, document, improve read-only monitoring, add
offline tests, and make small non-gated fixes after verification.

In autonomous mode, stop at a proposal for changes touching:

- `publish.py`, `build_fts5.py`, DB schemas, `base_scraper.py`
- new scrapers or material scraper behavior changes
- QC blocking behavior
- `state/**`, production output DBs, billing, secrets, backup destinations,
  systemd deployment behavior, or paid resources

Use `ops/autonomy-policy.json` and `scripts/agent_safe_deploy.py` as the
mechanical deployment guard. No commit, push, or deploy without explicit user
approval plus passing `make test` and `make verify-offline`.

## Maintenance Skill

For maintenance-loop, production-health, scraper-health, completeness,
backup, deploy-safety, or Codex automation tasks, use the repo skill:

`$opencaselaw-maintenance`
