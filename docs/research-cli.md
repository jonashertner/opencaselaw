# Research from the command line

`ocl` is a small command-line client for OpenCaseLaw. It searches the same
corpus as [opencaselaw.ch](https://opencaselaw.ch) and the
[MCP endpoint](https://opencaselaw.ch/mcp/): 1M+ published Swiss court
decisions from every federal and cantonal court, federal and cantonal
statutes, and the citation graph. It hands you the service's own records,
with identifiers, citation strings and passage text exactly as served. It is
made for two jobs that a chat window does badly: checking the citations in a
draft, and keeping the evidence behind a memo in a folder you can open years
later.

## Start here

Python 3.10 or newer. Install it as a tool with `pipx` or `uv`, which create
the isolated environment that current Python installations require:

```bash
pipx install opencaselaw-cli
ocl --help
```

`uv tool install opencaselaw-cli` does the same. Without either, use a
virtual environment: `python3 -m venv .venv && .venv/bin/pip install
opencaselaw-cli`, then run `.venv/bin/ocl`. A plain `pip install` outside a
virtual environment is refused by Homebrew and Debian Pythons (PEP 668).
Upgrade with `pipx upgrade opencaselaw-cli` (or `uv tool upgrade
opencaselaw-cli`). The source lives in `clients/python` of the repository.

Windows without Python (a court's managed desktop): each `cli-v*` release on
GitHub carries `OpenCaseLaw-CLI-<version>-setup.exe`, which installs the
python.org runtime, this client and the pure-Python `pypdf` package (so
filings that arrive as PDF are read without pip) under `Program
Files\OpenCaseLaw` with `ocl.cmd`, a "Send to" entry "Eingabe oder Entwurf
prüfen (offline)" that runs `ocl check --local` on the selected file or files
(several files are one run with one index report) and opens the report, and a
Start-menu entry for `ocl pack pull`. What it installs, how to allow-list and
verify it, and how to mirror the pack:
[court-it-install.md](court-it-install.md) (German).

Every command is a read-only call to the public service at
`mcp.opencaselaw.ch`. Nothing is downloaded, no account or key is needed.
Queries are subject to the [privacy notice](https://opencaselaw.ch/datenschutz/);
`--base-url` points at a separately operated server. At a terminal, results
are shown as readable, coloured text; when piped, or with `--format json` or
`--format jsonl`, they are JSON on standard output, so the same command feeds
both a person and a script. `--color never` or `NO_COLOR` turns colour off.
Messages go to standard error, and the exit code says how it went: 0 complete,
2 invalid input, 3 the service or network failed, 4 partial or unresolved.

Your first search:

```bash
ocl decisions search 'Rachekündigung Art. 336 OR' --max-results 3 --format jsonl --fields decision_id,citation_string_de,decision_date,court
```

```
{"decision_id": "bge_BGE_136_III_513", "citation_string_de": "BGE 136 III 513", "decision_date": "2010-10-07", "court": "bge"}
{"decision_id": "ow_gerichte_AbR_1992_93_Nr._8", "citation_string_de": "Gericht OW AbR 1992/93 Nr. 8 vom 26. November 2015", "decision_date": "2015-11-26", "court": "ow_gerichte"}
{"decision_id": "sg_gerichte_BZ.2005.9", "citation_string_de": "Gericht SG BZ.2005.9 vom 20. Juli 2005", "decision_date": "2005-07-20", "court": "sg_gerichte"}
{"_type": "pagination", "total": 125, "total_is_lower_bound": true, "returned": 3, "has_more": true, "next_offset": 3, ...}
```

The last line is the service's own account of the selection: at least 125
matches exist, you asked for three, more are retrievable. Keep it with the
results.

## Two jobs it does well

### Check a draft as it is

Hand over the document and read the report:

```bash
ocl check memo.docx
```

The draft is read (Word with its footnotes, Markdown, HTML or text), the
citations and the quotations next to them are found in the prose, each is
checked, and `memo.check.html` is written next to the draft: what exists,
what needs attention, and what to do about it. `--report notes.md` writes
Markdown instead. The command exits 4 when anything needs attention, so a
script or an agent can branch on it; `--format json` returns the rows. `ocl --local check
memo.docx` does the same against the verification pack on this machine, so
nothing about the draft is sent anywhere (see "For agents" below).

The report says only what was established. Its scope statement stands above
the results: existence, identity and wording only; not whether a decision
supports the argument or is still good law. A cited decision "exists" and,
where a pinpoint was cited, its "passage retrieved"; it is never "verified".
A quotation is "verbatim", "differs" or "not found" only after a served text
was compared; when no text could be compared (offline, the pack carries no
full texts, so a quotation next to a citation without an indexed pinpoint has
nothing to stand against) it is "not checked", with the advice to check
against the decision. A reference that is "not in the corpus" is qualified
by what the corpus holds for the court the reference names, read from the
reference itself (label, court word, canton), with the corpus's decision
count and year span for that court when the service or the pack can supply
it: an unpublished decision or the decision under appeal cannot be in any
corpus, a wrong citation can be. Strings that look like dockets or
collection references (ZR, Pra, GVP, SJZ, JdT, ...) but were not read as a
citation are listed under "possibly citations, not checked", so a miss is
never silent; they are not attention items. `--language de|fr|it` sets the
language of the report's labels and advice (the CLI's default is `de`);
anything else reads English.

Statutes are checked too. `Art. 8 Abs. 1 ZGB`, `art. 335 al. 1 CO`, `art. 8
cpv. 1 CC`, `Art. 8, 9 und 10 ZGB`, `Art. 41 ff. OR`, `SR 210` and cantonal
acts written with the paragraph sign (`§ 12 Abs. 2 StG/ZH`, `§ 18 VRG (ZH)`)
are found in the prose and looked up: does the act exist, does it have the
article, and does a quotation next to the reference stand in the served
article text. The report has its own "Statutes" table with an excerpt of the
served text; the findings are `article retrieved`, `article not in the act`,
`article has no text` (repealed or empty in the current edition), `act not
found`, `quotation differs` / `quotation not found`, and `not checked` for
what could not be asked (a `§` reference without a canton, unless
`OCL_CANTON=ZH` names the canton the draft belongs to). "Not checked" never
fails the run; the others exit 4.

### Check party submissions

Electronic filings arrive as PDF. `ocl check` reads a PDF page by page (with the
`pypdf` package: `pip install opencaselaw-cli[pdf]`, or `pdftotext` on PATH), so
every finding carries its page. A scanned PDF without a text layer is refused
with a clear message: OCR has to come first. Several files, or a directory of
filings, are checked in one run:

```bash
ocl check eingaben/ --kind submission
```

Each filing gets its own report next to it and `check-index.html` in the folder
lists them with their findings; `--report index.md` writes the index as
Markdown. `--kind submission` titles the report for the party's filing and puts
the count of citations not in the corpus, differing and not checked on the
first line; a missing citation states in full that an unpublished decision or
the decision under appeal is expected to be absent. Exit 4 when any file needs
attention, 2 when a file could not be read (the others are still reported).

### Check the citations in a list

Before a brief goes out, you want to know that every cited decision exists,
that every pinpointed Erwägung is really there, and that what the draft says
about a decision (its date, its docket) matches the record. Put the references
in a file exactly as the draft writes them, one per line; JSON lines when you
want to pass the pinpoint separately. Long forms are understood: `BGer
4A_747/2012 vom 5. April 2013`, `arrêt du TF 4A_485/2015 du 15 février 2016
consid. 3`, `Obergericht ZH LA210005 vom 15. Juni 2021`, `ATF 137 III 303
consid. 2 p. 305`, `BGE 121 V 240 E. 3c/aa`:

```bash
cat > references.jsonl <<'JSONL'
{"reference":"BGE 136 III 513 E. 2.3"}
{"reference":"BGer 4A_747/2012 vom 5. April 2013"}
{"reference":"BGE 999 III 1"}
{"reference":"BGE 140 III 86","pinpoint":"2.3"}
{"reference":"BGer 4A_714/2014 vom 22. Mai 2016"}
JSONL
ocl citations resolve --input references.jsonl --format jsonl --fields reference,decision_id > resolution.jsonl
```

At a terminal the same command prints a table, one line per reference with a
coloured status; the JSON lines below are what a script receives:

```
{"reference": "BGE 136 III 513 E. 2.3", "status": "resolved", "decision_id": "bge_BGE_136_III_513", "pinpoint_status": "retrieved"}
{"reference": "BGer 4A_747/2012 vom 5. April 2013", "status": "resolved", "decision_id": "bger_4A_747_2012"}
{"reference": "BGE 999 III 1", "status": "missing", "note": "No decision carries this label; close_matches are for the author to inspect, never substitutes"}
{"reference": "BGE 140 III 86", "status": "pinpoint_unavailable", "decision_id": "bge_BGE_140_III_86", "pinpoint_status": "unavailable"}
{"reference": "BGer 4A_714/2014 vom 22. Mai 2016", "status": "discrepancy", "decision_id": "bger_4A_714_2014", "discrepancies": [{"kind": "date", "written": "2016-05-22", "decision": "2015-05-22"}]}
{"_type": "pagination", "status": "partial", "counts": {"resolved": 2, "missing": 1, "pinpoint_unavailable": 1, "discrepancy": 1}, ...}
```

The first reference exists and so does its E. 2.3, which was read from the
reference itself. The long form resolved through its docket. `BGE 999 III 1`
is not in the corpus: check the citation, or accept that coverage may have a
gap. BGE 140 III 86 exists, but the index has no E. 2.3 for it, so quote from
the decision itself, not from memory. The last reference names a real decision
with the wrong year. The exit code is 4 because not everything resolved.
`--fields` keeps the verdict and what qualifies it (status, errors, notes,
discrepancies); without it, each row also carries the service's citation
strings in German, French and Italian, the source link, the identity
evidence, and the passage text when a pinpoint was found.

What the check does not do: it never says that a decision supports your
proposition, is still good law, or fits your facts. That reading is yours.

### Check the quotations

What the draft puts in quotation marks must stand in the decision it is
attributed to. Give the reference (with its Erwägung when known) and the
quotation; the tool looks in the cited Erwägung first, then in the whole
decision text:

```bash
ocl quotes check 'BGE 136 III 513 E. 2.3' --quote "le contrat de travail conclu pour une durée indéterminée"
ocl quotes check --input quotes.jsonl --format jsonl     # rows: {"reference": ..., "pinpoint": ..., "quote": ...}
```

`quote_status` is `exact` (verbatim once typography, OCR line hyphenation,
whitespace and the service's link markup are folded), `near` (the best match
scores 90% or better; the differing spans and the served wording are listed)
or `not_found` (the closest served text and its score are still shown). When
no served text could be compared at all (offline without an indexed
pinpoint, since the pack carries no full texts) the status is `unverifiable`
with `reason: "no served text"`: not a verdict, check against the decision.
`found_in` says whether the match lies in the cited Erwägung or elsewhere in
the decision. The served wording is authoritative and is never rewritten;
exit code 4 unless every quotation is exact. A `quote` field on a
`citations resolve` row is checked the same way (`quote_check`).

### Keep the evidence behind a memo

When a memo relies on a search, you want a record of what was retrieved and
when. One command runs the search and saves the decisions it selects (ten by
default), the Erwägungen you name and the statute articles you name into a
folder:

```bash
ocl bundle create 'Rachekündigung Art. 336 OR' --max-results 10 --passage 2 --law OR:336 --out rachekuendigung-2026-09
```

The folder is readable without any tool:

```
rachekuendigung-2026-09/
  INDEX.md                                      what was saved, in plain language
  manifest.json                                 the record: every request, timestamp, file hash, source link
  decisions/bge_BGE_136_III_513-5b3e22ef.txt    full text as served (a .json next to it holds the metadata)
  passages/bge_BGE_136_III_513_2-ff487655.txt   E. 2, verbatim
  laws/OR_336-812a5382.json                     Art. 336 OR with its consolidation date and Fedlex link
  search/page-0000-1850ce82.json                the search page the selection came from
```

`INDEX.md` from a real run with two decisions:

```
## Decisions
- BGE 136 III 513: saved, decided 2010-10-07 [decisions/bge_BGE_136_III_513-5b3e22ef.json, ...]
- Gericht OW AbR 1992/93 Nr. 8 vom 26. November 2015: saved, decided 2015-11-26 [...]
## Passages (Erwägungen)
- BGE 136 III 513, E. 2: saved [passages/bge_BGE_136_III_513_2-ff487655.json, ...]
- Gericht OW AbR 1992/93 Nr. 8 vom 26. November 2015, E. 2: failed: No structured Erwägungen found for 'ow_gerichte_AbR_1992_93_Nr._8'.
## Statute articles
- OR Art. 336 (consolidated 2026-01-01): saved [laws/OR_336-812a5382.json]
```

That run ended `partial` (exit 4) because the cantonal decision has no
numbered E. 2 in the index; the manifest says so and nothing is dropped
silently. Saved files are never overwritten. If a run is interrupted, repeat
the same command with `--resume`: the client re-verifies the hash of every
saved file, then fetches only what is missing. The folder preserves what the
service returned on that day; the same query next month can select other
decisions, because the corpus is rebuilt nightly.

## What the results mean

- `resolved`: the decision exists in the corpus and carries a label written in
  the reference. With a pinpoint, the named Erwägung exists and its text is in
  the row (`pinpoint_status: retrieved`; `pinpoint_source` says whether the
  number came from the reference or from the input row).
- `pinpoint_unavailable`: the decision exists; the numbered passage is not in
  the structure index. The index holds numeric Erwägung numbers only (2, 2.3,
  2.3.1). For a lettered sub-number the index lacks (`E. 2a`, `E. 3c/aa`, the
  style of BGE before about 2000) the parent number is retrieved instead
  (`pinpoint_status: parent_retrieved`): locate the letter inside that text.
  Some Italian-language BGE volumes have no structure at all yet; for them
  this is the normal outcome, and the decision text is the source to quote.
- `discrepancy`: the decision was identified, but the reference says something
  about it that the record contradicts: the date (`BGer 4A_714/2014 vom 22.
  Mai 2016` for a ruling of 22 May 2015) or a docket written next to a BGE
  label that names a different ruling. `discrepancies` lists each one.
- `missing`: no decision with that citation or docket. A wrong citation or a
  gap in coverage, never proof that a citation was invented. The service's
  `close_matches` are listed for the author; nothing is substituted.
- `ambiguous`: more than one decision carries that label (dockets are reused
  across courts, and some portals file summaries of federal rulings under the
  federal docket). Name the court in the reference (`BGer 4A_191/2019`) or
  pick a `decision_id` from `candidates`.
- `unrecognized`: the service proposed a decision that carries no label
  written in the reference (a docket fragment matched by substring, for
  example). The proposal is under `service_candidate`, never in
  `decision_id`. `resolution_incomplete` and `error` mean identity could not
  be established or the request failed. Nothing is guessed.
- `identity_check.method` says why a match is trusted: `exact_canonical_id`
  (the reference is the id), `exact_server_citation` (the service's own
  string), `exact_server_docket` (the decision's own docket is the label the
  reference writes first), `exact_server_joined_docket` (the label is one of
  the joined dockets of a consolidated proceeding, which the record lists
  under `joined_dockets`; the check names the `lead_docket` it is filed
  under) or `exact_candidate_label` (a docket the lookup index knows in
  another form, whose only in-scope carrier is the proposed decision).
  `uniqueness` says whether other carriers of that docket were checked. A
  candidate at a court the reference rules out is listed under
  `out_of_scope_candidates` and does not make the reference ambiguous. The
  label written first is the citation; a docket mentioned later (`vgl. auch
  BGer 4A_747/2012`, a joined file) is listed under `other_dockets` and never
  taken for it.
- `canonical_decision_id` on a resolved row means the service stores the same
  ruling under another id as well and names that record as the canonical one
  (`bge_143 III 38` next to `bge_BGE_143_III_38`, for example). `decision_id`
  stays the record the reference resolved to; nothing is substituted. The
  field is absent when the record is the canonical one or the server has no
  representation manifest loaded.
- `quote_status` (`quotes check`, or `quote_check` on a resolve row): `exact`
  means the quotation stands verbatim in the served text after folding
  typography and line breaks; `near` means it differs (the spans are
  listed: quote wording against served wording); `not_found` means no
  window of the compared text comes close (the closest window is in
  `served`); `unverifiable` means nothing was compared because no text was
  served (`reason: "no served text"`). Never repair a quotation from the
  differences by hand: copy the served wording.
- Passage `text` is the served string; the service marks cross-references
  inside it as Markdown links. `text_plain` is the same text with those links
  reduced to their labels, for comparisons with the decision text.
- A bundle is `complete` when every requested item was saved, otherwise
  `partial`. An `unavailable` item is one the service does not have (a
  passage that is not indexed for that decision, an unknown decision); a
  `failed` item is a download that `--resume` will retry. Complete describes
  the requested collection, not the law: `exhaustive_legal_research` is
  always `false`.
- `total_is_lower_bound: true` means "at least this many". A text query is
  ranked over a bounded candidate pool, so `has_more: false` never proves that
  every relevant decision was seen. Nothing here replaces reading the decision.

## For agents

An agent needs four things: a way to install without prompts, output it can
parse, verdicts it can branch on, and rules it cannot talk itself out of.

```bash
pipx install opencaselaw-cli          # or: uv tool install opencaselaw-cli
ocl doctor --format json              # reachability, server generation, tool count, latency
ocl agent-guide                       # the contract, commands, statuses and rules on one page
ocl skills install --claude           # the bundled skills into ~/.claude/skills (or --dir DIR)
```

Piped, every command is JSON; `--format jsonl` gives one row per line; exit
codes carry the verdict (0 resolved, 2 invalid input, 3 transport, 4 something
did not resolve). `--cache DIR` (or `OCL_CACHE`) keeps responses keyed by the
server's database generation, so a session that re-reads the same passages
pays once. `ocl tool list` shows every server tool, `ocl tool schema <name>`
its arguments, and `ocl tool call <name> key=value ...` runs it, returning the
tool's structured output:

```bash
ocl tool call find_leading_cases query='Rachekündigung Art. 336 OR' limit=5 --format json
ocl tool call get_regeste decision_id=bge_BGE_136_III_513
ocl tool call search_scholarship query='missbräuchliche Kündigung' limit=5
```

Confidential drafts can be checked without sending anything: `ocl pack pull`
downloads the verification pack (a weekly SQLite snapshot with the service's
citation strings, docket aliases and every indexed Erwägung, several GB, CC0)
and `ocl --local ...` answers `check`, `citations resolve`, `cite`, `decisions
passage`, `quotes check` and bundles from it. `--local` is a switch and goes
before or after the command (`ocl --local check memo.docx`, `ocl check
memo.docx --local`, or `OCL_LOCAL=1`); `--pack PATH` (or `OCL_PACK`) names a
pack file elsewhere than the pulled one, which lives in `%LOCALAPPDATA%\ocl`
on Windows and `~/.local/share/ocl` (or `$XDG_DATA_HOME/ocl`) elsewhere. A
missing pack is exit 2 with the advice to run `ocl pack pull`. Search,
statutes and tools stay online-only, and the pack carries no full texts;
`ocl --local doctor` reports the pack's path, size, generation, counts and
age (a warning past 14 days, the snapshots being weekly) without sending
anything, and `ocl pack info` shows the same metadata.
and `ocl --local ...` answers `citations resolve`, `cite`, `decisions
passage`, `quotes check`, `check` and bundles from it. Search and tools stay
online-only, and the pack carries no full texts; `ocl pack info` shows which
corpus generation it holds and how the download was verified (the section
below covers the checksum, resumable pulls and mirroring on a share).
Federal statutes are answered offline when a
statutes database sits next to the pack (`statutes.sqlite`, or the file
`OCL_STATUTES` points to: the `statutes.db` the server serves, built by
`search_stack/build_statutes_db.py`); without it, `ocl check` reports statute
references as "not checked" rather than wrong, and cantonal acts stay
online-only.

Three skills ship in the package: `citation-check` (verify a draft's
citations and quotations), `research` (search, read, follow citations, cite,
verify), `evidence-bundle` (keep what was relied on). Scripts that prefer code
over a shell use `opencaselaw_cli.api` (`resolve`, `check_quotes`, `passage`,
`search`, `tool`).

## Offline pack: download, verify, mirror

The verification pack is one SQLite file, published weekly on the HuggingFace
mirror as `artifacts/verification_pack/latest.sqlite.gz` next to a
`latest.sqlite.gz.sha256` sidecar in sha256sum format (the dated copy
`<date>.sqlite.gz` has one too). `ocl pack pull` downloads the gzip resumably,
verifies it against the sidecar before unpacking, installs the pack atomically
and records the verification next to it. Without a published checksum it stops
(exit 2) unless `--insecure` is given, and it never replaces an installed pack
with one that failed verification or that the client cannot read.

For a court whose workstations do not reach the internet, IT downloads once,
verifies, and mirrors the pair on a file share:

```bash
# 1. On a machine with internet access, once a week (about 8 GB):
curl -LO https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/artifacts/verification_pack/latest.sqlite.gz
curl -LO https://huggingface.co/datasets/voilaj/swiss-caselaw/resolve/main/artifacts/verification_pack/latest.sqlite.gz.sha256
sha256sum -c latest.sqlite.gz.sha256
# PowerShell: curl.exe -LO ...; then
#   (Get-FileHash latest.sqlite.gz).Hash -eq ((Get-Content latest.sqlite.gz.sha256) -split ' ')[0]
# 2. Copy both files, unchanged, to a read-only share, e.g. \\server\share\ocl\
# 3. Each workstation (a login script, or the clerk) installs from the share:
ocl pack pull --url file://server/share/ocl/latest.sqlite.gz
ocl pack verify
```

`pull` reads the `.sha256` from the same place as the gzip, so the share must
carry both. The pack lands in `%LOCALAPPDATA%\ocl\verification_pack.sqlite`
on Windows and `~/.local/share/ocl/` elsewhere (`--to FILE` for another
location); `\\server\share\ocl\latest.sqlite.gz`,
`D:\packs\latest.sqlite.gz` and `file:///mnt/share/latest.sqlite.gz` are
accepted as well, and any mirror that serves the gzip and its sidecar over
https works. An interrupted pull continues from the bytes already saved in
`<pack>.gz.part` (HTTP Range with the source's ETag; a seek on a share; a
mirror that ignores Range starts over). It prints a line every 50 MB and waits
at most 120 s for each read, with no overall limit. A checksum mismatch keeps
the partial file for inspection and exits 2; the next pull starts over. A pull
with `--insecure` installs the pack but records it as unverified.

`ocl pack verify` and `ocl pack info` print the same report: what the pull
recorded (`source_url`, `checksum_url`, `gzip_sha256`, `pack_sha256`,
`pulled_at`, `client_version`) together with the pack's `schema_version`,
`built_at`, `db_generation`, `decisions` and `paragraphs`. `verify` exits 4
when the pack was pulled with `--insecure`, copied by hand (no `<pack>.json`
next to it) or changed size since, so a login script can branch on it. Packs
of schema 1 and 2 open; a newer major version is refused on open with a
message naming the client and pack versions (upgrade the client).

## Reusable setup

Defaults can live in `~/.config/ocl/config` (one `key = value` per line:
`base_url`, `timeout`, `retries`, `format`, `color`, `language`, `jobs`,
`cache`, `local`, `pack`) or in `OCL_BASE_URL`, `OCL_TIMEOUT`, `OCL_RETRIES`,
`OCL_FORMAT`, `OCL_COLOR`, `OCL_LANGUAGE`, `OCL_JOBS`, `OCL_CACHE`, `OCL_LOCAL`
(`1` for offline mode; a pack path here, the 0.6 grammar, still works) and
`OCL_PACK` (the pack file). A flag beats the environment, which beats the file.
`ocl completion zsh > ~/.zfunc/_ocl` (or `eval "$(ocl completion bash)"`, or
`ocl completion fish > ~/.config/fish/completions/ocl.fish`) installs tab
completion for commands, options and choices.

Output formats beyond text and JSON: `--format table` for an aligned plain
table, `--format csv` for a spreadsheet, `--format md` for a memo appendix
(a Markdown table for lists, a quoted passage with its citation for
`decisions passage`). Batch commands and bundles run up to `--jobs` requests
at once (default 4) under the same 200 ms pacing, and stop after five
consecutive transport failures instead of retrying every remaining item.
`--verbose` logs every request to stderr; outputs carry the request count.

## Recipes

Search, then fetch the full decisions, in one pipeline:

```bash
ocl decisions search 'Rachekündigung Art. 336 OR' --max-results 5 --format jsonl |
  ocl decisions get --stdin --format jsonl > decisions.jsonl
```

List everything a court decided in a period. Without query text the filters
enumerate an exact set, page by page:

```bash
ocl decisions search --court bge --date-from 2026-01-01 --date-to 2026-06-30 --sort date_desc --max-results 500 --format jsonl > bge-2026-h1.jsonl
```

Quote one Erwägung verbatim, and get the citation to go with it:

```bash
ocl decisions passage bge_BGE_136_III_513 2.3 --fields citation_string_de,text
ocl cite 'BGE 136 III 513' --pinpoint 2.3 --language fr
```

Read a statute as it stood on a date:

```bash
ocl laws get OR --article 41 --as-of 2015-01-01
```

See who cites a leading case:

```bash
ocl citations list bge_BGE_140_III_86 --direction incoming --limit 50 --format jsonl
```

Finish an interrupted bundle:

```bash
ocl bundle create 'Rachekündigung Art. 336 OR' --max-results 10 --passage 2 --law OR:336 --out rachekuendigung-2026-09 --resume
```

Check a bundle a colleague sent, compare it with last month's run of the same
question, and add a decision found elsewhere:

```bash
ocl bundle verify rachekuendigung-2026-09
ocl bundle diff rachekuendigung-2026-08 rachekuendigung-2026-09
ocl bundle add rachekuendigung-2026-09 bge_BGE_140_III_86 --passage 2
```

Coding agents such as Claude Code or Codex can run the same commands from a
shell and read the JSON, which keeps the evidence in files you can check
rather than in the model's memory.

## Where it fits

- Conversation: connect Claude, ChatGPT or another MCP client to
  `mcp.opencaselaw.ch` ([setup per client](https://opencaselaw.ch/mcp/)).
- Scripts, notebooks and agents: `ocl`, or the
  [REST API](https://opencaselaw.ch/api/) directly. Same records, same limits.
- Corpus-scale analysis: the
  [Parquet dataset](https://huggingface.co/datasets/voilaj/swiss-caselaw).

## Reference: search and retrieval

Text mode (the default at a terminal) renders search hits, decisions,
passages, statutes, citation lists, resolution reports and bundle summaries
for reading; it folds Markdown link markup inside served text for display and
ignores `--fields`. JSON is the default when output is piped. Search JSON keeps a result envelope with the server's
pagination fields and `_client` retrieval metadata; JSONL writes one decision
per line followed by a record with `_type: "pagination"`. Batch commands
(`decisions get`, `citations resolve`, `cite`) accept plain lines or JSONL
from `--input FILE` or `--stdin` and skip pagination records, so a search can
be piped straight into a fetch. `--fields a,b` projects result fields while
always keeping errors, statuses and completeness metadata; on `citations
resolve` rows it is a real projection (reference, status, errors, notes and
discrepancies survive, the evidence blocks do not). `--detail compact` (the
default) asks the service for lean records that carry `citation_string_de`
only; `full` adds regeste, snippet, a pinpoint suggestion and the French and
Italian citation strings (`decisions get` always has all three).

A text query is ranked by relevance over one bounded candidate pool whose size
depends on the requested window, so its pages are not composable: `ocl` sends
it as a single request of `--max-results` (at most 800). Without query text
the filters enumerate an exact, stably ordered set in pages of `--page-size`
(default 50); rows repeated across pages are dropped and counted in
`_client.duplicates_dropped`. Rows the service links to one
`canonical_decision_id` (the same ruling stored under several ids) are
reduced to one row per ruling, the canonical record when the selection holds
it, at the group's first-seen rank; the others are counted in
`_client.duplicates_collapsed` and listed under
`_client.collapsed_representations`. `--no-collapse` keeps every row.

A reference containing a slash (a docket such as `4A_747/2012`) is resolved
through the service first; `ocl` then requires the resolved decision to carry
that exact docket or citation label and otherwise stops, because the service
also matches docket fragments. `ocl cite --pinpoint` checks that the Erwägung exists
before it formats the citation and reports `pinpoint_exists: false` (exit 4)
otherwise; `--no-verify-pinpoint` restores formatting only. Statute responses can carry consolidation or
edition information; inspect it before relying on the text for a historical
question. `--as-of` covers federal law only.

## Reference: bundles

`--passage NUMBER` (repeatable) requests that Erwägung from every selected
decision; `--law ABBREVIATION:ARTICLE` (repeatable) requests federal statute
articles and `--law CANTON/ABBREVIATION:ARTICLE` cantonal ones, in the search
language when it is `de`, `fr` or `it`, otherwise in German. `--court`, `--canton`, `--language`, `--date-from` and `--date-to`
filter the search.

The manifest links the request parameters and every response with retrieval
timestamps, source URLs, consolidation dates, errors, and a SHA-256 over the
saved bytes of each file. That hash verifies the file as saved, not the court's
original publication or the service's wire encoding; the service's own
`content_hash` is kept separately, as supplied. Decision `.txt` files are the
exact served full text; passage `.txt` files are the served passage text, in
which cross-references can carry Markdown links added by the service.
`INDEX.md` is a convenience view regenerated on every run; only files listed
in the manifest form the collection. The manifest also records the service's
database generation and decision count when the run started
(`corpus_snapshot`); it identifies the corpus state, it is not a copy anyone
can fetch later. `bundle verify` re-hashes every listed file and reports
changed, missing and unlisted files; `bundle diff` compares two bundles
(decisions added or removed, decisions whose served text changed, item
statuses, the corpus generation); `bundle add` appends decisions with the
bundle's passages.

Resume requires the same query, filters and options in the same directory.
A crash between saving a response and checkpointing the manifest can leave an
unlisted file; resume keeps it and records a new request. Use one writer per
bundle directory. Resuming can span corpus updates; each response carries its
own timestamp. A case-number query behaves as a text search and can select
decisions citing that case; inspect the selected IDs.

## Reference: citation resolution

Input is one reference per line, or JSONL records with `reference` and an
optional `pinpoint`; `--stdin` and positional references also work. Extra keys
of a JSONL record come back under `input`, so rows can be correlated.

Each reference is parsed the way it is written: the collection label
(`BGE`/`ATF`/`DTF`), a docket in any of the separators the corpus stores
(`4A_747/2012`, `4A 747/2012`, `4C.230/2005`, the EVG chambers `B 59/2001`
and `I 25/2005`, the two-digit chambers `12T 3/2013`) or a cantonal form
(`LA210005`, `WBE.2026.33`, `C/11532/2013`, `HC / 2020 / 38`, `K 2015/3`,
`810 16 9`, `BRGE I Nr. 0167/2014`, `AI 12/14 - 140/2014`), court words (`BGer`, `TF`, `Obergericht ZH`, `Cour de justice de
Genève`), a date, page references (`S. 357`, `p. 305`, `ff.`) and an inline
pinpoint (`E. 2.3`, `consid. 3b`, `E. 3c/aa`). The label is what is queried
(`query` in the row says which); the decision the service proposes must carry
a label written in the reference, compared after folding case, whitespace,
the docket separators and the `ATF`/`DTF` spellings. Nothing is ever
rewritten for output. Pinpoint fields accept the author's spelling
(`consid. 2.3`, `E. 3b`); an invalid one fails that row, never the batch.

A docket carried by more than one decision at courts the reference does not
rule out is `ambiguous`; the court words in the reference scope the check, so
`BGer 4A_191/2019` resolves even where a cantonal portal filed a summary under
the same docket. A lookup window filled with exact matches is
`resolution_incomplete`. A `--language` other than German only changes which
citation string is primary.

## Bounds, errors and reusable scripts

Search stops at `--max-results` (default 50). A text query is ranked over one
bounded candidate pool whose size depends on the requested window, so its pages
are not composable: `ocl` sends it as a single request of `--max-results`
(at most 800). A filter-only search (no query text) enumerates an exact, stably
ordered set and is fetched in pages of `--page-size` (default 50); rows repeated
across pages are dropped and counted in `_client.duplicates_dropped`, and
duplicate representations of one ruling are collapsed unless `--no-collapse`
is given (`_client.duplicates_collapsed`). Inspect
`total_is_lower_bound`, `has_more`, `next_offset` and `_client` metadata.
A relevance query can exhaust the server's bounded candidate pool before all
matches are returned. `has_more: false` does not prove exhaustive coverage of a
legal question. There is no exhaustive `--all` option. For corpus-scale work,
use the [Parquet dataset](https://huggingface.co/datasets/voilaj/swiss-caselaw).

Batch commands accept plain references/IDs or JSONL from `--input FILE` or
`--stdin`. Keep stdout for JSON/JSONL results and stderr for diagnostics. Check
the exit status as well as the output:

| Exit | Meaning |
|---|---|
| `0` | Successful command, including an intentionally bounded search |
| `2` | Invalid arguments or input |
| `3` | Transport or server failure; a retry may succeed |
| `4` | Partial or unresolved result: a decision or passage the service does not have, a reference that names no single decision, an incomplete batch or workflow |
| `130` | Interrupted |

Leaf commands expose `--base-url`, `--timeout`, `--retries`, `--format` and
`--fields`. The client paces requests and uses bounded retries, respecting
`Retry-After` up to its retry-delay limit. A longer requested wait returns an
error so the job can be rescheduled without retrying early. Pace concurrent scripts so their
combined traffic stays within the service's published rate guidance. Save
selection parameters, client version, output and exit status with reusable
scripts. Do not discard a partial result or present it as complete.

## Research API contract

The selected public research operations have a typed OpenAPI document at
`/api/research/openapi.json` in this source version. It covers the core search,
retrieval, passage, statute and citation operations without the broader
application's billing/admin routes. The main specification remains at
`/api/openapi.json`.

The research contract becomes available on a server only after that source
version is deployed. This guide does not assert that it is live on the public
host. The client calls existing REST research routes and does not need the new
schema endpoint to be deployed. The schema describes returned evidence and
pagination; it does not certify legal relevance, corpus completeness or the
accuracy of every upstream record.

Three fields matter for honest scripting; each is absent rather than false
when a server does not set it, so test for its presence. `degraded: true` on a
search means the service hit its time budget and returned a reduced ranking;
retry later or narrow the query rather than treating the page as the best
matches. On a passage, `text_source` is `structure_index` (the normal case) or
`full_text_heading` (the numbered heading was located in the decision text
because the index has no row for it; check the block's boundaries before
quoting). Passage `text` is served with the service's Markdown links around
cross-references; `text_plain`, added by the client, reduces them to their
labels for comparisons with the decision text. `/api/lookup?exact=true` returns only decisions whose own docket or
BGE label is the reference, which is what `citations resolve` uses to detect a
docket reused by another court.

Two identity fields follow the same rule. `joined_dockets` on a decision, a
lookup hit or a `cite` answer lists the secondary dockets of a consolidated
proceeding filed under that decision's lead docket, so a reference by any of
them names the record. `canonical_decision_id` (with `is_canonical`) on
decisions, lookup hits, `cite` answers and search rows names the canonical
record when the service stores the same ruling under several ids; the
requested record is always the one served.
