# ocl for agents

`ocl` is the command-line client of OpenCaseLaw (Swiss case law, statutes,
scholarship, practice). It is built for agents: JSON when piped, exit codes
that carry the verdict, a stable schema, and rules that keep citations and
quotations honest. Print this page with `ocl agent-guide`.

## Install and check

    pipx install opencaselaw-cli     # or: uv tool install opencaselaw-cli
    ocl doctor --format json         # connectivity, server generation, tool count, latency

Python 3.10+, no dependencies. Non-interactive; never prompts.
Windows without Python: the release installer (`OpenCaseLaw-CLI-<version>-setup.exe`)
puts `ocl.cmd` under `Program Files\OpenCaseLaw`; same commands, same output.
It bundles `pypdf`, so `ocl check filing.pdf` works there without pip.

## Contract

- stdout: JSON (`--format json`, or automatically when piped); `--format jsonl`
  gives one row per line plus a trailing `{"_type": "pagination", ...}` record.
  stderr: diagnostics only.
- exit 0 all resolved · 2 invalid input · 3 transport or server failure (retry
  later) · 4 something did not resolve, is not in the corpus, or names no
  single decision (read the rows).
- `--fields a,b` trims rows; the verdict fields (status, errors, notes,
  discrepancies) always survive.
- `--cache DIR` (or `OCL_CACHE`) stores responses keyed by the server's
  database generation: repeated calls in a session cost nothing and stay
  consistent until the nightly rebuild.
- Requests are paced (200 ms) and retried; `--jobs` bounds concurrency.
  Keep a session to hundreds of requests, not thousands.

## Commands

| job | command |
|---|---|
| check a whole draft (docx/md/html/txt) | `ocl check memo.docx --format json --no-report` (rows + found citations; `--report x.md` for a person) |
| check party submissions (pdf) or a folder | `ocl check eingaben/ --kind submission --format json --no-report` (`files[]` with per-file `summary`, `unreadable` entries carry `error`; exit 2 if any file could not be read) |
| check citations in a list | `ocl citations resolve --input refs.jsonl --format jsonl` |
| check quotations | `ocl quotes check --input quotes.jsonl --format jsonl` |
| a citation string to copy | `ocl cite '<reference>' --pinpoint 2.3` |
| find decisions | `ocl decisions search '<terms>' --max-results 20 --format jsonl` |
| newly published decisions (alerts) | `ocl decisions search '<terms>' --court bger --published-from <3 days ago> --sort published_desc --format jsonl` (publication date, not decision date; skip ids already seen) |
| read a decision / a passage | `ocl decisions get <id>` · `ocl decisions passage <id> 2.3` |
| statutes | `ocl laws get OR --article 336 [--as-of YYYY-MM-DD]` |
| citation graph | `ocl citations list <id> --direction incoming` |
| every other server tool | `ocl tool list` · `ocl tool schema <name>` · `ocl tool call <name> key=value ...` |
| keep evidence | `ocl bundle create ... --out folder` · `ocl bundle verify folder` |

`ocl tool call` reaches all research tools of the service (leading cases,
relevant considerations, scholarship, commentaries, practice, materials,
legislation changes, case briefs, claim support). Values that parse as JSON
are typed (`limit=5`, `flag=true`, `ids='["a","b"]'`); `--args '{...}'`
passes a whole object; `--stdin` runs one call per JSONL row.

## Statuses

`resolved` · `pinpoint_unavailable` (decision found, passage not indexed;
`parent_retrieved` when the parent number was served instead) · `discrepancy`
(date or docket written next to the label contradicts the record) · `missing` ·
`ambiguous` (several carriers; name the court) · `unrecognized` (the service's
proposal carries no label you wrote; see `service_candidate`, never cite it).
A `missing` row of `ocl check` carries `coverage`: the court read from the
reference (`inferred.label`, `court_word`, `canton`) and, when obtainable,
the corpus's `decisions`, `first_year`, `last_year` for it (`source`:
`list_courts` online, `pack` offline, null when neither answered). An
unpublished ruling or the decision under appeal is expected to be absent.
Quotes: `exact` · `near` (differences listed) · `not_found` (a served text
was compared; it is in `served`) · `unverifiable` (`reason: "no served
text"`: nothing was compared, typically offline without an indexed pinpoint;
check against the decision, never treat as "not found").
`ocl check` also returns `unparsed`: docket-like strings and collection
references (ZR, Pra, GVP, ...) that were not read as a citation and were not
checked; `summary` counts `checked`, `exists`, `passages_retrieved`,
`attention`, `unparsed`. The report's labels follow `--language` (de, fr,
it; English otherwise).
Statutes (`ocl check`, the `statutes` list): `statute_found` · `law_found`
(SR number only) · `article_missing` · `article_empty` (repealed or empty in
the current edition) · `law_unknown` · `unverifiable` (a `§` reference
without a canton, or statutes not available offline; never exit 4) ·
`error`. A quotation next to the reference gets a `quote_check` against the
served article text. `OCL_CANTON=ZH` routes bare `§` references.

## Rules that are not optional

1. Citation strings come from the service (`citation_string`); never compose one.
2. Quotations come from served passages; `quotes check` decides, not memory.
3. A close match or service candidate is information for the author, never a substitute.
4. Existence and wording are what the tool establishes; legal support is not.

## Offline

    ocl pack pull                                   # several GB, weekly snapshot (CC0)
    ocl --local doctor --format json                # the pack: path, size, generation, age
    ocl --local check memo.docx
    ocl pack pull                                   # several GB, weekly snapshot (CC0), verified against the published .sha256
    ocl pack pull --url file://server/share/latest.sqlite.gz   # from a mirror or a share (Windows: \\server\share\... too)
    ocl pack verify                                 # how the installed pack was verified; exit 4 if it never was
    ocl --local citations resolve --input refs.jsonl --format jsonl
    ocl --local quotes check --input quotes.jsonl --format jsonl
    ocl --local --pack /srv/packs/verification_pack.sqlite check memo.docx

`--local` is a switch (before or after the command; `OCL_LOCAL=1`); `--pack
PATH` (or `OCL_PACK`) names the pack file, otherwise the one `ocl pack pull`
stored (`%LOCALAPPDATA%\ocl` on Windows, `~/.local/share/ocl` elsewhere). A
missing pack is exit 2 with the advice to run `ocl pack pull`. With `--local`
nothing about the draft leaves the machine: identity, pinpoints and
quotations are checked against the pack (no full texts, no search, no tools).
`ocl --local doctor` reports the pack's generation and age (a warning past 14
days) and exits 0; `ocl pack info` shows the same metadata. Results carry
`offline: true`; a failure inside the pack is a row with `status: error`.
With `--local` nothing about the draft leaves the machine: identity, pinpoints
and quotations are checked against the pack (no full texts, no search, no
tools). A pull resumes after an interruption, stops without a published
checksum (exit 2; `--insecure` overrides and records the pack as unverified)
and never replaces the installed pack with one that failed verification or
that this client cannot read (schema 1 and 2). `ocl pack info` shows the
snapshot's generation and verification; results carry `offline: true`. Federal statutes are checked offline when `statutes.sqlite`
sits next to the pack (or `OCL_STATUTES` names the file); otherwise statute
rows are `unverifiable`, not errors.

## Skills

`ocl skills list` · `ocl skills show citation-check` · `ocl skills install --claude`
(copies the bundled skills into `~/.claude/skills/`; `--dir` for any other harness).

## As a library

    from opencaselaw_cli import api
    rows = api.resolve([{"reference": "BGE 136 III 513 E. 2.3"}])
    result = api.tool("find_leading_cases", query="Rachekündigung")
