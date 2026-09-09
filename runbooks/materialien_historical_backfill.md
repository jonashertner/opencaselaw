# Runbook: historical Botschaften backfill (1850-1999, Bundesarchiv scans)

Approved by Jonas 2026-09-09 ("we must fix all"). Code: `scrapers/fedlex_materialien.py`
(`discover_fga_botschaften_untyped`), `search_stack/bar_fetcher.py` (the polite fetcher),
`search_stack/botschaft_historical.py` (cleaner, ingest, resume state), CLI flags on
`search_stack/build_botschaft_corpus.py`. Nothing runs until the flags below are passed:
the nightly `opencaselaw-materialien.service` is unchanged.

## What it adds

Fedlex lists 8,767 (DE) / 8,921 (FR) / 2,362 (IT) FGA works from 1850 to 1999 whose title
starts with "Botschaft" / "Message" / "Messaggio" and which carry no `typeDocument` (the
type vocabulary starts ~1999). Their only manifestation is a `pdf-a` with an OCR text layer
on `https://www.amtsdruckschriften.bar.admin.ch/viewOrigDoc.do?id=<id>&action=open`
(one FR file sits on Fedlex's own store). Measured 2026-09-09 over `memorialPageTo`:
199,523 (DE) / 202,619 (FR) / 82,425 (IT) printed pages, 23 pages per message on average
(35 for IT), the largest 994 pages.

Each document becomes one `botschaft_documents` row (`format='pdf'`,
`source_collection='bar-amtsdruckschriften'`, `bbl_volume`, Fedlex `title`,
`publication_date`, `bbl_citation` from Fedlex's `dct:identifier` such as "BBl 1975 I 609" /
"FF 1998 I 205", `page_count`, `text_hash`) plus its paragraphs in `botschaft_paragraphs`
(+ FTS5 via the existing triggers). PDF bytes are not kept.

The resume state is `botschaft_fetch_state` (PK `eli_uri, language`; status `done`,
`exists`, `collision`, `not_found`, `no_text`, `error`; attempts). It lives in
materialien.db so the nightly step 2f copies it across the swap like the corpus tables.
`error` / `not_found` rows are retried on a later run up to `--max-attempts` (3); the rest
are final.

## The host and the courtesy

`robots.txt` on the BAR host disallows `/viewOrigDoc.do`. Jonas decided on 2026-09-09 to
fetch anyway as a recognised non-profit effort. The fetcher therefore:

- sends `User-Agent: OpenCaseLaw/1.0 (non-profit legal corpus; contact:
  jonashertner@protonmail.ch; +https://opencaselaw.ch)`;
- uses one connection and one request at a time (never parallel, never two languages
  at once);
- waits at least 2.5 s after the end of a response before the next request
  (`--bar-min-interval`, floor 2.0);
- on 429 / 403 / 5xx or a transport error backs off 15 s, 30 s (Retry-After honoured when
  larger) and stops hard after 3 in a row; the document that failed is recorded and only
  retried on the next run;
- stops after `--max-per-run` requests so a run fits one night;
- is opt-in: the BAR host is only touched with `--host-allow bar`.

Send the Bundesarchiv a short notice before the first night (a courtesy, not a request
for permission): who we are (OpenCaseLaw, non-profit, CC0 corpus, opencaselaw.ch), what
we fetch (the ~20,000 PDFs that Fedlex itself links as the manifestation of the 1850-1999
Botschaften, once, text only), how (one request every 2.5 s, at night, identifying
User-Agent with this address), that we stop on the first sign of trouble, and the address
to write to if they want it paused. Suggested recipient: the Digitale Amtsdruckschriften
team via the contact on `amtsdruckschriften.bar.admin.ch` / `bundesarchiv.ch`.

## The plan (multi-night)

At 2.5 s spacing plus ~0.3-1 s per download:

| Night | Language | Files | Time at 2.5 s |
|---|---|---|---|
| 1-2 | DE | 8,767 | ~6.5-7.5 h (two nights of `--max-per-run 4500`, or one long one) |
| 3-4 | FR | 8,921 | ~6.5-7.5 h |
| 5 | IT | 2,362 | ~2 h |

DE first (the language most Erwägungen cite), then FR, then IT. Newest first within a
language (1999 → 1850), so the 1990s land the first night.

### Windows on the VPS

Never inside the full build (03:30 → ~17:00 UTC) and never while the 04:30 UTC
`opencaselaw-materialien.service` writes materialien.db (single writer,
`journal_mode=delete`). Safe window: after the 20:00 UTC incremental exits (~20:20 UTC)
until 03:15 UTC. With `--max-per-run 4500` a run takes ~4 h; start at 20:30 UTC and it is
done by 00:30. Do not start a run that would still be fetching at 03:30 — it holds the
materialien.db writer lock and step 2f's ATTACH is read-only, but the 04:30 service will
collide; `--max-per-run` is the guard.

### Commands

Dry run first (SPARQL only, no BAR request; lists the first 25 candidates and the counts):

```bash
cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_botschaft_corpus \
  --ingest-all --discover-untyped-by-title --host-allow bar --language de \
  --db /opt/caselaw/repo/output/materialien.db --dry-run
```

One night (DE, 4,500 requests; log to its own file):

```bash
cd /opt/caselaw/repo && systemd-run --unit=ocl-mat-hist-de-$(date +%m%d) \
  --property=WorkingDirectory=/opt/caselaw/repo --property=CPUWeight=20 --property=IOWeight=20 \
  --setenv=PYTHONPATH=/opt/caselaw/repo \
  /bin/bash -c 'python3 -m search_stack.build_botschaft_corpus --ingest-all \
     --discover-untyped-by-title --host-allow bar --language de --max-per-run 4500 \
     --db /opt/caselaw/repo/output/materialien.db \
     >> /opt/caselaw/repo/logs/materialien_historical.log 2>&1'
```

(`nohup python3 … &` works the same; `systemd-run` survives the SSH session and shows in
`systemctl list-units 'ocl-mat-hist-*'`.) Repeat with `--language fr`, then `it`.
Every run prints a JSON stats block at the end (discovered, eligible, done, exists,
collision, not_found, no_text, error, requests_made, bytes_received, stopped).

State at any time (no network):

```bash
cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_botschaft_corpus \
  --state-summary --db /opt/caselaw/repo/output/materialien.db
```

### Pause / resume

- Pause: `systemctl stop ocl-mat-hist-de-<mmdd>` (or kill the nohup PID). Every document
  commits on its own, so a stop loses at most the download in flight.
- Resume: run the same command again. `done` / `exists` / `collision` / `no_text` rows are
  skipped without a request; `error` / `not_found` rows are retried until `--max-attempts`.
- A run that ended with `"stopped": "3 consecutive failures …"` means the host pushed back.
  Do not restart it the same night; look at the log (status codes), and if it was 429 / 403
  wait a day and lower the budget or raise the interval (`--bar-min-interval 5`).
- The typed (1999-2026) nightly service is untouched and keeps running at 04:30.

### After each language

Nothing to restart: the MCP workers open materialien.db read-only per request and see new
rows at once; the nightly step 2f copies the tables (and `botschaft_fetch_state`) into the
rebuilt DB. `search_botschaft(query, year_min=1930, year_max=1939)` is the check;
`get_materialien` does not list these rows because no `article_botschaft_links` point to
them (see open questions).

## Storage and runtime

- PDF bytes: not retained. Transfer ~0.06 MB per printed page (3.7 MB for 61 pages):
  roughly 12 GB (DE) + 12 GB (FR) + 5 GB (IT) over the wire in total, nothing on disk.
- Text: the 1935 sample is 2.5 kB per page after cleaning, 8-9 paragraphs per page. Per
  language: DE ~500 MB text / ~1.7 M paragraphs, FR ~500 MB / ~1.7 M, IT ~200 MB / ~0.7 M.
  With the FTS5 index (about the size of the text) materialien.db grows from 1.19 GB
  (2026-09-09, 6,166 documents / 423,556 paragraphs) by ~1 GB per big language to
  ~3.5-4 GB with all three. Disk on the VPS root volume: 30 GB free (2026-09-09).
- Step 2f: the preserve copy took 3 s for 424k paragraphs (2026-09-09 log). It re-creates
  the FTS5 index by `INSERT … SELECT`, so expect it to scale with text volume: roughly
  1-2 min extra per big language, 3-5 min with all three. Well inside the step's budget;
  worth watching in the publish log the first night after each language.
- Server memory: the workers read the DB through the page cache; a 4 GB file is cached
  in full on the 61 GB host (43 GB buff/cache today). FTS5 query memory is per query and
  does not grow with corpus size. The two FTS-heavy tools (`search_botschaft`,
  `get_article_purpose` fallback) return `LIMIT`-bounded rows.
- SPARQL discovery: the untyped query answers in 10-60 s per language; one call per run.

## Text quality

OCR layer from the 2003 digitisation. Fraktur is not involved (the Bundesblatt is set in
Antiqua from 1850). The cleaner drops the `#ST#` scan markers, page numbers, the issue
masthead (title lines, price, Stämpfli), the printer's foot signature and the archive's
catalogue sheet on the last page; rejoins line-end hyphenation ("Schweize-|rischen") when
the next line continues in lower case; groups lines into paragraphs and stitches a
paragraph across a page break. It never changes a word: OCR misreads stay ("Eeihe",
"Begierungsrat", "Bheinstrecke" in the 1935/1955 samples) — the corpus is verbatim
(R1-R3), the title is Fedlex's. `article_anchor` follows the existing parser ("Art. 2"
lines in the draft decree at the end of a message anchor the following paragraphs).

## Open questions for Jonas

1. **Collisions.** `botschaft_documents` is UNIQUE on (bbl_year, bbl_page, language); before
   1998 a year has several volumes and the same page recurs: 301 (year, page) pairs in the
   German set (~3.5 % of documents), similar in FR. The first work to take a pair keeps it,
   the others are recorded as `collision` and are not fetched. Lifting this means a table
   rebuild with the key on (eli_uri, language) — not additive, so not in this branch.
   Proposal: migration in `build_botschaft_corpus --schema-only` behind a flag, run once in
   a build-free window, then a second pass picks up the `collision` rows.
2. **Links.** `article_botschaft_links` come from statute footnotes (`amendment_refs`),
   whose regex reads "BBl 1999 6013" but not "BBl 1975 I 609"; historical rows therefore
   have no links and `get_materialien` does not list them. `search_botschaft` finds them.
   A volume-aware footnote parser is a separate change.
3. **Readers.** `title` and `publication_date` are populated on these rows;
   `_linked_botschaft_documents` and `search_botschaft` still derive the title from the
   opening paragraph / omit it. Preferring the column when non-NULL is a two-line change
   in `mcp_server.py`, left out here to avoid conflicting with the title fix in flight.
4. **FR/IT typography.** The masthead / footer patterns were verified on three German
   samples (1935, 1955, 1975). The French and Italian editions are covered by best-effort
   patterns; check the first FR/IT night's `no_text` count and a few paragraphs.
5. **Other FGA types** (24 Bericht BR, 25 Stellungnahme BR, 30 Bericht Kommission, 38
   Erlassentwurf, 39 Vorentwurf, 40 Vernehmlassung) sit on Fedlex's own store and are a
   separate task; type 33 (Beschlüsse, 44,615) stays out.
