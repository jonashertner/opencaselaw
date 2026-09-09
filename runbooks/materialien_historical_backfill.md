# Runbook: historical Botschaften backfill (1850-1999, Bundesarchiv scans)

Approved by Jonas 2026-09-09 ("we must fix all"). Code: `scrapers/fedlex_materialien.py`
(`discover_fga_botschaften_untyped`), `search_stack/bar_fetcher.py` (the polite fetcher),
`search_stack/botschaft_historical.py` (cleaner, ingest, resume state, run lock), CLI flags
on `search_stack/build_botschaft_corpus.py`. Nothing runs until the flags below are passed:
the nightly `opencaselaw-materialien.service` is unchanged.

## What it adds

Fedlex lists 8,767 (DE) / 8,921 (FR) / 2,362 (IT) FGA works from 1850 to 1999 whose title
starts with "Botschaft" / "Message" / "Messaggio" and which do not carry
`typeDocument` 23 (Botschaft; the type vocabulary starts ~1999). Precisely: the SPARQL
excludes type 23, so a work typed as something else (24 Bericht BR) whose title starts with
"Botschaft" is included; decrees and ordinances cannot leak because their titles never
start with those words. Their only manifestation is a `pdf-a` with an OCR text layer on
`https://www.amtsdruckschriften.bar.admin.ch/viewOrigDoc.do?id=<id>&action=open` (one FR
file sits on Fedlex's own store). Measured 2026-09-09 over `memorialPageTo`: 199,523 (DE) /
202,619 (FR) / 82,425 (IT) printed pages, 23 pages per message on average (35 for IT), the
largest 994 pages.

Each document becomes one `botschaft_documents` row (`format='pdf'`,
`source_collection='bar-amtsdruckschriften'`, `bbl_volume`, Fedlex `title`,
`publication_date`, `bbl_citation`, `page_count`, `text_hash`) plus its paragraphs in
`botschaft_paragraphs` (+ FTS5 via the existing triggers). PDF bytes are not kept.

**The citation string is Fedlex's, never ours.** `bbl_citation` is the expression's
`dct:identifier`, else its `jolux:historicalLegalId` (both "BBl 1975 I 609" — verified by a
SPARQL probe on 2026-09-09). A work with neither is recorded as `no_citation` and not
fetched: the column is `NOT NULL`, and the citation is not assembled from year / volume /
page (R1-R3). DE measured 0 such works; check the FR/IT counts in the discovery log line
("N without a Fedlex citation") on the dry run. Lifting this needs the same table rebuild
as the collision limitation below.

The resume state is `botschaft_fetch_state` (PK `eli_uri, language`; status `done`,
`exists`, `collision`, `not_found`, `no_text`, `no_citation`, `error`; attempts). It lives
in materialien.db so the nightly step 2f copies it across the swap like the corpus tables.
`error` / `not_found` rows are retried on a later run up to `--max-attempts` (3); the rest
are final. Three semantics to know:

- `no_text` is terminal: a later cleaner improvement never reaches those documents without
  a manual reset (`UPDATE botschaft_fetch_state SET status='error', attempts=0 WHERE
  status='no_text'`).
- `text_hash` on these rows is the sha-256 of the PDF bytes; typed rows hash the extracted
  text. The column's delta meaning diverges between the two populations.
- `page_count` counts the PDF's pages, including the Bundesarchiv catalogue sheet appended
  to every scan (dropped from the text).

**Every document is one transaction.** The document row, its paragraphs and the state row
commit together or not at all; a failure inside the insert rolls back and records `error`.
A BAR row without paragraphs (from a run older than this rule) is deleted and fetched
again. The run loop's last-resort handler never commits: a state write that fails (locked
DB, disk) rolls back and ends the run, because requests without persistence are wasted.

## The host and the courtesy

`robots.txt` on the BAR host disallows `/viewOrigDoc.do`. Jonas decided on 2026-09-09 to
fetch anyway as a recognised non-profit effort. The fetcher therefore:

- sends `User-Agent: OpenCaseLaw/1.0 (non-profit legal corpus; contact:
  jonashertner@protonmail.ch; +https://opencaselaw.ch)`;
- uses one connection and one request at a time (never parallel, never two languages
  at once — enforced by the host lock, see below);
- waits at least 2.5 s after the end of a response before the next request
  (`--bar-min-interval`, floor 2.0);
- on 429 / 403 / 5xx or a transport error backs off 15 s, 30 s (Retry-After honoured when
  larger) and stops hard after 3 in a row; the document that failed is recorded and only
  retried on the next run;
- treats a **soft block** the same way: a 200 whose body is not a PDF (magic bytes), is
  shorter than 1 KB, or carries a captcha / "too many requests" / login marker is a
  failure, backed off and counted toward the stop. A portal that answers every request
  with a login page ends the run after three requests, not after the night's budget;
- stops after `--max-per-run` requests so a run fits one window;
- is opt-in: the BAR host is only touched with `--host-allow bar`.

### Host lock

`run_historical_backfill` takes `flock(LOCK_EX | LOCK_NB)` on
`/opt/caselaw/repo/logs/bar_fetch.lock` (`--lock-file` to change it) for the whole run,
before the first request. A second run on the host — another language, another day's unit,
a forgotten `nohup` — exits **3** at once without a request. A dry run takes no lock. The
command below wraps the process in `flock -n` on the same file as well, so the guard holds
even for a shell that never reaches Python.

### Courtesy notice

Send the Bundesarchiv this notice before the first night (a courtesy, not a request for
permission), to the Digitale Amtsdruckschriften team via the contact on
`amtsdruckschriften.bar.admin.ch` / `bundesarchiv.ch`. Text (fill in the date):

> Sehr geehrte Damen und Herren, ich betreibe mit OpenCaseLaw (opencaselaw.ch) ein nicht
> kommerzielles Projekt, das Schweizer Rechtsquellen als frei zugängliches Korpus (CC0) für
> Forschung und Praxis aufbereitet. Ab dem [Datum] werde ich die rund 20'000 PDF-Dateien
> der Botschaften des Bundesrates 1850 bis 1999, die Fedlex als Manifestation auf
> amtsdruckschriften.bar.admin.ch verlinkt, einmalig abrufen und nur den Textlayer
> speichern; die Dateien selbst werden nicht weiterverbreitet. Der Abruf erfolgt nachts,
> mit einer einzigen Verbindung, höchstens einer Anfrage alle 2.5 Sekunden und dem
> User-Agent «OpenCaseLaw/1.0 (non-profit legal corpus; contact:
> jonashertner@protonmail.ch)», und er stoppt beim ersten Anzeichen einer Belastung Ihres
> Systems. Sollte Ihnen ein anderer Weg lieber sein, etwa ein Bulk-Export oder eine
> langsamere Rate, stelle ich sofort um; eine kurze Nachricht an
> jonashertner@protonmail.ch genügt. Freundliche Grüsse, Jonas Hertner

## The plan (multi-night)

### The window

The quiet window on a weeknight is **00:20 → 03:15 UTC**, under three hours: the 20:00 UTC
incremental (`OnCalendar=Mon..Sat 20:00 UTC`) is queued behind the full build and has been
seen still running at 21:15 UTC in step 2g; the full build starts at 03:30 and the 04:30
`opencaselaw-materialien.service` writes materialien.db (single writer,
`journal_mode=delete`). A run must be finished before 03:15 so it never overlaps step 2f
(whose ATTACH holds a shared lock for its whole copy; at 4-5 M paragraphs that copy runs
minutes and any overlapping writer fails with "database is locked") and never the 04:30
unit.

**Check the units, never the clock.** Before starting:

```bash
systemctl is-active opencaselaw-publish.service          # must print: inactive
systemctl is-active opencaselaw-materialien.service      # must print: inactive
systemctl list-units 'ocl-mat-hist-*' --no-legend        # must be empty
```

If `opencaselaw-publish.service` says `activating` or `active`, do not start, whatever the
time. The incremental sometimes runs to 00:20 UTC or later; the window opens when it exits.

At 2.5 s spacing plus ~0.5 s per download a request costs ~3 s: 00:20 → 03:15 is ~10,000 s,
so **`--max-per-run 3000`** (≈2.5 h) is the weeknight budget, with margin for backoffs.

| Nights | Language | Files | Runs at 3,000 |
|---|---|---|---|
| 1-3 | DE | 8,767 | three weeknights, or Saturday night + one weeknight |
| 4-6 | FR | 8,921 | three weeknights |
| 7 | IT | 2,362 | one weeknight |

DE first (the language most Erwägungen cite), then FR, then IT — one language at a time, one
run at a time. Newest first within a language (1999 → 1850), so the 1990s land the first
night. **Saturday night is the long window**: there is no Sunday 20:00 incremental
(`Mon..Sat`), so a run can start after the Saturday incremental exits and run until 03:15
Sunday with `--max-per-run 8000` — the full DE night. After Stage A (Sunday-only full
builds) every weeknight window widens; re-measure before raising the weeknight budget.

Do not start FR before the DE nights have been read: the masthead / footer / hyphenation
patterns were verified on German samples (1935, 1955, 1975) and the FR/IT hyphenation
rules on synthetic fixtures; check the first FR/IT night's `no_text` count and a few
paragraphs (`search_botschaft(query, language='fr', year_max=1999)`).

### Prerequisite: step 2f

Before the first night lands in a nightly rebuild, `build_materialien_db.py` needs the
rowid-preserving FTS copy (its `INSERT … SELECT *` into the external-content FTS5 table
drops the rowid; a gap in `paragraph_id` — which the delete-and-refetch of a paragraph-less
row can create — misaligns every paragraph after it) and the `.db.tmp` next to the realpath
(a cross-device rename fails once materialien.db moves to the volume), and `publish.py`'s
step 2f timeout 600 → 1800 s. Both are pipeline-gate files: separate commit, Jonas's OK.

### Commands

Dry run first (SPARQL only, no BAR request, no lock; lists the first 25 candidates, the
counts, and how many works have no Fedlex citation):

```bash
cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_botschaft_corpus \
  --ingest-all --discover-untyped-by-title --host-allow bar --language de \
  --db /opt/caselaw/repo/output/materialien.db --dry-run
```

One weeknight (DE, 3,000 requests; log to its own file; the `flock -n` and the in-process
lock both refuse a second run):

```bash
cd /opt/caselaw/repo && systemctl is-active opencaselaw-publish.service opencaselaw-materialien.service
# both inactive? then:
systemd-run --unit=ocl-mat-hist-de-$(date +%m%d) \
  --property=WorkingDirectory=/opt/caselaw/repo --property=CPUWeight=20 --property=IOWeight=20 \
  --setenv=PYTHONPATH=/opt/caselaw/repo \
  /bin/bash -c 'flock -n /opt/caselaw/repo/logs/bar_fetch.lock \
     python3 -m search_stack.build_botschaft_corpus --ingest-all \
     --discover-untyped-by-title --host-allow bar --language de --max-per-run 3000 \
     --lock-file /opt/caselaw/repo/logs/bar_fetch.lock \
     --db /opt/caselaw/repo/output/materialien.db \
     >> /opt/caselaw/repo/logs/materialien_historical.log 2>&1'
```

Saturday night: the same with `--max-per-run 8000`. Exit codes: 0 finished (see the JSON
stats block: discovered, eligible, done, exists, collision, not_found, no_text,
no_citation, error, requests_made, bytes_received, stopped), 2 bad flags, 3 another run
holds the lock (`flock -n` itself exits 1 in that case).

State at any time (no network, no lock):

```bash
cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_botschaft_corpus \
  --state-summary --db /opt/caselaw/repo/output/materialien.db
```

### Pause / resume

- Pause: `systemctl stop ocl-mat-hist-de-<mmdd>`. Every document commits as one
  transaction, so a stop loses at most the download in flight.
- Resume: run the same command again (units, then the flags). `done` / `exists` /
  `collision` / `no_text` / `no_citation` rows are skipped without a request; `error` /
  `not_found` rows are retried until `--max-attempts`.
- A run that ended with `"stopped": "3 consecutive failures …"` means the host pushed back
  — including a soft block (`HTTP 200 not a PDF … login page`). Do not restart it the same
  night; look at the log, and if it was 429 / 403 / a block page wait a day and lower the
  budget or raise the interval (`--bar-min-interval 5`).
- `"stopped": "persistence failure …"` means materialien.db could not be written (locked by
  step 2f or the 04:30 unit — the window was wrong). Nothing was committed for that
  document; check the units and rerun in the next window.
- The typed (1999-2026) nightly service is untouched and keeps running at 04:30.

### After each language

Nothing to restart: the MCP workers open materialien.db read-only per request and see new
rows at once; the nightly step 2f copies the tables (and `botschaft_fetch_state`) into the
rebuilt DB. `search_botschaft(query, year_min=1930, year_max=1939)` is the check;
`get_materialien` does not list these rows because no `article_botschaft_links` point to
them (see open questions). The morning after each night: `Preserved … botschaft_documents(N)`
and the 2f duration in `logs/publish.log`.

## Storage and runtime

- PDF bytes: not retained. Transfer ~0.06 MB per printed page (3.7 MB for 61 pages):
  roughly 12 GB (DE) + 12 GB (FR) + 5 GB (IT) over the wire in total, nothing on disk.
- Text: the 1935 sample is 2.5 kB per page after cleaning, 8-9 paragraphs per page:
  ~1.2 GB text, ~4.1 M paragraphs for all three languages (avg ~300 B, five times shorter
  than modern rows, so the FTS `_docsize` and row overhead grow more than the text ratio
  suggests). Expect materialien.db (1.19 GB on 2026-09-09, 6,166 documents / 423,556
  paragraphs) at ~3.5-4 GB with all three languages. It is a plain file on `/` (30 GB free);
  step 2f needs the live file plus a `.db.tmp` of the same size, ~8 GB peak. Move it to the
  volume (92 GB free) per the CLAUDE.md rule for DBs > 500 MB — only after the realpath fix
  in `build_materialien_db.py`, with `cp` then `ln -sfn` in the 00:20 window, never while
  the 04:30 unit writes.
- Step 2f: measured 2026-09-09 at 30 s for the preserve copy and 37.9 s for the step (cap
  600 s in `publish.py`). It re-creates the FTS5 index by `INSERT … SELECT`, so it scales
  with the tokens re-indexed: ~3-4 min with all three languages, inside 600 s only just;
  hence the 1800 s cap above.
- Writes into the live file: the workers open materialien.db with `immutable=1` and take no
  lock, so a call that overlaps one of the run's commits (one every ~3 s for ~2.5 h) can
  read torn pages for that call. Same pattern as the 04:30 writer, now in the serving
  night. The clean fix is a sidecar (`output/materialien_historical.db`) merged by step 2f's
  preserve loop — a `build_materialien_db.py` change (pipeline gate) for Jonas to decide.
- `search_botschaft` post-filters language and year after `MATCH` and sorts the whole match
  set by rank, so common-term latency grows with the corpus (~10× more "Bundesrat" matches
  after DE). Measure 20 queries × 5 runs on a copy before FR lands; the fix, if p95 exceeds
  ~1 s, is server-side (language as an FTS column, or a pre-limited rank subquery).
- SPARQL discovery: the untyped query answers in 10-60 s per language; one call per run.

## Text quality

OCR layer from the 2003 digitisation. Fraktur is not involved (the Bundesblatt is set in
Antiqua from 1850). The cleaner never deletes body text: a line is dropped only by position
and pattern together — the page number in the first line (and the Geschäftsnummer in the
second, only when the title block follows), the masthead block on an issue-first page
(recognised by "Bundesblatt" / "Feuille fédérale" / "Foglio federale" alone on a line in
the head zone), and in the last two lines a bare page number, the print-job number
("1975-45") or the printer's signature ("Bundesblatt. 87. Jahrg. Bd. I. 25" — starts with
the gazette's name, names the "Jahrg"/"année"/"anno", ends with a small integer). A year in
a table, "Die Nachnahme …" as a first line, "Bundesblatt 1934, Bd. I, S. 350 …" as a foot
line all survive (tests). Known residue: a bare number that is the very last line of a page
is taken for the page number; the `#ST#` scan markers are dropped everywhere.

Line-end hyphenation is rejoined conservatively: only a lower-case continuation whose two
halves are plain word fragments and not a known hyphenated prefix / suffix of the language
("Schweize-|rischen", "fé-|déral"); "peut-|être", "c'est-à-|dire", "porte-|parole",
"au-|dessus", "vis-à-|vis", "ci-|joint", "vice-|presidente" keep their hyphen; an
upper-case continuation ("Saint-|Gall", "Basel-|Stadt") keeps it; digits on both sides are
a range; a suspended hyphen before "und" / "et", and anything in doubt, keeps the hyphen
and turns the line break into a space. The residue of the rule is a kept hyphen inside an
ordinary word ("anti-|cipé", "sous-|traire"), never a word that did not exist. It never
changes a word: OCR misreads stay ("Eeihe", "Begierungsrat", "Bheinstrecke" in the
1935/1955 samples) — the corpus is verbatim (R1-R3), the title is Fedlex's.
`article_anchor` follows the existing parser ("Art. 2" lines in the draft decree at the end
of a message anchor the following paragraphs). The Geschäftsnummer line is dropped from the
title block, so `mcp_server`'s `_BOTSCHAFT_HEAD_RE` does not match these rows and the
title falls back to the excerpt (open question 3).

## Known limitation: UNIQUE(bbl_year, bbl_page, language) — later schema migration

`botschaft_documents` is UNIQUE on (bbl_year, bbl_page, language), a constraint from the
volume-less post-1998 Bundesblatt. Before 1998 a year has several volumes and the same page
number recurs: **301 (year, page) pairs in the German set** (~3.5 % of documents), similar
in FR. The first work to take a pair keeps it, the others are recorded as `collision` and
are not fetched. Lifting this means a table rebuild with the key on (eli_uri, language) or
on (bbl_year, bbl_volume, bbl_page, language) — not additive, so **not in this branch and
not now**. Proposal: a migration in `build_botschaft_corpus --schema-only` behind a flag,
run once in a build-free window (the 00:20 window, with the 04:30 unit and step 2f
inactive), together with making `bbl_citation` nullable for the `no_citation` rows; then a
second pass resets `collision` / `no_citation` to `error` with `attempts=0` and picks them
up. Until then `_botschaft_meta_for_bbl_ref`-style lookups by (year, page) must require the
volume for `bbl_year < 1999` or restrict to `source_collection IS NULL`.

## Open questions for Jonas

1. **Sidecar DB** (see Storage): run the nightly writes into `materialien_historical.db`
   and let step 2f attach and copy it, instead of committing into the live file every ~3 s
   for the whole serving night.
2. **Links.** `article_botschaft_links` come from statute footnotes (`amendment_refs`),
   whose regex reads "BBl 1999 6013" but not "BBl 1975 I 609"; historical rows therefore
   have no links and `get_materialien` does not list them. `search_botschaft` finds them.
   A volume-aware footnote parser is a separate change.
3. **Readers.** `title` and `publication_date` are populated on these rows;
   `_linked_botschaft_documents` and `search_botschaft` still derive the title from the
   opening paragraph / omit it. Preferring the column when non-NULL is a two-line change
   in `mcp_server.py`, left out here to avoid conflicting with the title fix in flight.
4. **Other FGA types** (24 Bericht BR, 25 Stellungnahme BR, 30 Bericht Kommission, 38
   Erlassentwurf, 39 Vorentwurf, 40 Vernehmlassung) sit on Fedlex's own store and are a
   separate task; type 33 (Beschlüsse, 44,615) stays out.
