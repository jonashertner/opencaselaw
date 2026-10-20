# Federal Materialien: complete coverage from Fedlex (deploy + operation)

Goal: every federal preparatory document Fedlex publishes is in the corpus,
verbatim, and a nightly census proves it.

| Corpus | Fedlex population (census 2026-10-19) | Where | Path |
|---|---|---|---|
| Bundesblatt, typed (Botschaft 23, Bericht BR 24, Stellungnahme BR 25, Bericht Kommission 30, Erlassentwurf 38, Eröffnung Vernehmlassung 40), 1999– | 5,627 DE / 5,630 FR / 5,625 IT | `materialien.db` `botschaft_documents.doc_type` | 04:30 unit (`opencaselaw-materialien.service`) |
| Bundesblatt, historical 1850–1999 (Botschaften incl. Nachtrags-/Ergänzungsbotschaften, Berichte BR, Kommissionsberichte, Parl. Initiativen) | 12,205 DE (8,819 Botschaften + 3,386 Berichte) / ~12,000 FR / ~3,300 IT | `materialien.db`, `source_collection='bar-amtsdruckschriften'` | `opencaselaw-materialien-historical.timer` 00:20 |
| Vernehmlassungen (2,581 procedures 1993–; ~5 GB of text when complete): Erläuternder Bericht 2,434 · Vorlage 3,275 · Ergebnisbericht 1,943 · Stellungnahmen 1,214 · Synoptische Tabelle 243 · Diverses 683 | 46,168 language files, ~28,700 under the text policy | `consultations.db` (own DB, volume) | `opencaselaw-vernehmlassungen.timer` 17:45 + 22:45 |

Not on Fedlex: the Chancellery's 1995–2004 consultation index and the BJ
Rechtsetzungsprojekte feed consultations.db through
`search_stack/consultation_archive.py` (section "Vernehmlassungen outside
Fedlex" below). Still not built: ordinance Erläuterungen (news.admin.ch API,
contract unverified), Postulatsberichte outside the BBl.

## What changed (branch `materialien-complete`)

* `deploy-materialien` (Sept.) merged with main: titles + dates on every
  Botschaft row, Bundesblatt types 24/25/30/38/40, the historical backfill,
  step-2f prerequisites.
* **Historical scope widened**: the untyped discovery classifies titles
  (`classify_historical_title`) and takes Berichte des Bundesrates,
  Kommissionsberichte and Parlamentarische Initiativen, not only
  "Botschaft…"; typed-path types are excluded from it, Fedlex-hosted
  leftovers (types 18/33) are fetched too (`--host-allow bar,fedlex`).
* **Volume-aware key**: `botschaft_documents` was UNIQUE(year, page,
  language); before 1999 a year has several volumes (301 DE pairs were
  `collision`, never fetched). `--migrate-volume-key` rebuilds the table on
  (year, COALESCE(volume, 0), page, language), keeps every botschaft_id,
  resets `collision` rows.
* **`search_stack/materialien_swap.py`**: heavy writers of materialien.db run
  on a copy that is checked (quick_check, no shrink) and `os.replace`d in;
  refused if the live file changed meanwhile. Used by the 04:30 Botschaft
  unit (its typed catch-up runs for hours on the first nights), the
  historical unit and the one-off migrations below; all share
  `logs/materialien_swap.lock`.
* **`search_stack/build_consultation_corpus.py`** + `consultations.db`.
* **Server**: `search_botschaft(doc_type=erlaeuternder_bericht | vernehmlassungsvorlage | ergebnisbericht | stellungnahmen | synoptische_tabelle | vernehmlassung_diverses)`;
  without doc_type, Vernehmlassung passages ride along in
  `vernehmlassung_results` (Bundesblatt ranking unchanged);
  `get_materialien` lists `vernehmlassungen` for the act (+ Erläuternder-Bericht
  passages for the article). Tool count unchanged.
* **`scripts/check_materialien_completeness.py`**: nightly census, report
  `logs/materialien_completeness.json`, one ntfy push on a gap.

## Deploy (after the full build has exited)

Every step: check first that nothing writes materialien.db.

```bash
systemctl is-active opencaselaw-publish.service opencaselaw-materialien.service   # both: inactive
```

1. Code: push, then on the VPS `git fetch && git merge --ff-only origin/main`
   ([[mcp-deploy-path]]); `python3 -m py_compile mcp_server.py`.
2. Move materialien.db to the volume (`runbooks/materialien_db_volume_move.md`;
   the EXDEV fix is in this merge). Root has ~21 GB free; the historical
   corpus takes the file to ~5 GB and step 2f + the swap each need a twin.
3. Volume-key migration on a copy (seconds):
   ```bash
   cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.materialien_swap \
     --db output/materialien.db -- \
     python3 -m search_stack.build_botschaft_corpus --migrate-volume-key --db {db}
   ```
   Expect `{"migrated": true, "rows": 6202, ...}` and "swapped in".
4. Titles and dates on the 6,202 existing rows, on a copy (~3 min):
   ```bash
   cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.materialien_swap \
     --db output/materialien.db -- \
     python3 scripts/backfill_botschaft_metadata.py --db {db}
   ```
5. Units:
   ```bash
   cd /opt/caselaw/repo && cp systemd/opencaselaw-materialien.service \
     systemd/opencaselaw-vernehmlassungen.service systemd/opencaselaw-vernehmlassungen.timer \
     systemd/opencaselaw-materialien-historical.service systemd/opencaselaw-materialien-historical.timer \
     /etc/systemd/system/ && systemctl daemon-reload
   systemctl enable --now opencaselaw-vernehmlassungen.timer
   ```
   The historical timer only after the courtesy notice to the Bundesarchiv
   (`runbooks/materialien_historical_backfill.md`):
   `systemctl enable --now opencaselaw-materialien-historical.timer`.
6. consultations.db. Either seed it with the work DB built on the Mac mini
   on 2026-10-19 (copy to `/mnt/HC_Volume_104655575/output/consultations_work.db`,
   then `systemctl start opencaselaw-vernehmlassungen.service` publishes it), or
   let the unit build it (3 h per run, ~3 runs). Then:
   ```bash
   ln -sfn /mnt/HC_Volume_104655575/output/consultations.db /opt/caselaw/repo/output/consultations.db
   ```
7. Rolling restart of the workers (`scripts/rolling_restart_workers.sh`) for
   the new server code. consultations.db itself needs no restart (one
   connection per call).
8. Verify:
   ```bash
   curl -s 'https://mcp.opencaselaw.ch/api/search-botschaft?q=Hinterlassenenrente&doc_type=erlaeuternder_bericht' | head -c 1500
   cd /opt/caselaw/repo && python3 scripts/check_materialien_completeness.py --no-ntfy
   ```

## Operation

* Gap alert (ntfy): read `logs/materialien_completeness.json`.
  `bundesblatt_typed.missing_sample` → the 04:30 unit did not ingest them
  (its log: `logs/materialien_ingest.log`); `vernehmlassungen.discovery_lag`
  → the Vernehmlassungen unit has not run (build window) or Fedlex added
  documents; `text.backlog_sample` → files that keep failing (error text in
  the row).
* `no_text` (scans without a text layer), `parse_error`, `too_large` are
  reported in `without_text_sample`: an OCR pass is the follow-up, nothing is
  hidden.
* A replaced upload on Fedlex (new file URL) is re-queued automatically; a
  document that disappears from Fedlex keeps its row (takedown is an explicit
  act: delete its consultation_files / consultation_paragraphs rows in the
  work DB and publish).
* Reset a file for a re-fetch: `UPDATE consultation_files SET status='pending', attempts=0 WHERE file_id=…` in the work DB.

## Vernehmlassungen outside Fedlex (`search_stack/consultation_archive.py`, 2026-10-20)

Fedlex lists every procedure since 1993 but carries files from about 2005
on. Three sources feed the same work DB; the existing
`opencaselaw-vernehmlassungen` unit fetches and publishes their files like
Fedlex's (no unit change). Discovery is a manual, occasional step.

| Source (`consultation_files.source`) | What | Read from |
|---|---|---|
| `admin_pc_wayback` | the Federal Chancellery's index `admin.ch/ch/{d,f,i}/gg/pc/ind<YEAR>.html`, 1995–2004: per procedure the deadline and the documents of the opening ("Eröffnung": Vorlage, Bericht) and the result ("Ergebnis") | Wayback: latest capture + latest pre-2006 capture (that layout links the offices' own sites) of each page; files via one CDX prefix query per language + one exact query per office file |
| `bj` | bj.admin.ch legislative projects, `/{de,fr,it}/{laufende,abgeschlossene}-rechtsetzungsprojekte-*` (the live site carries the old archive too) | live, server-rendered; DE/FR/IT pages joined by the Nuxt page record's translation slugs |
| `bj_wayback` | the old BJ archive `bj.admin.ch/bj/<lang>/home/<area>/gesetzgebung/archiv/<slug>.html`: only files the live project page no longer lists, and projects the live site dropped | Wayback: CDX prefix per area and language |
| `curated_wayback` | hand-checked entries, `search_stack/consultation_archive_curated.json`: documents a reviewer read off an archived page that no source reads (2026-10-20: the BJ's 1997/98 list `www.admin.ch/bj/vernehm/vernehm{d,f,i}.htm` — Haager Adoptions-Übereinkommen 1997, "Tiere sind keine Sachen" 1998) | the data file (snapshot, original URL, linking page and the page's own label per file; no network) |

Provenance on every row: `consultation_files.source`, `original_url` (the
file's own, now dead, URL), `source_page_url` (the capture or page that
linked it); `file_url` is the Wayback snapshot (fetched in its `id_` form)
or the live bj.admin.ch file. Labels are the page's own link text in
`consultation_documents.title_*` ("Vorlage", "Ergebnis: Bericht",
"Vorentwurf"); the server shows them as `doc_type_label` with `source`,
`original_url`, `linked_from` and, for Wayback copies, `archived_copy`.
`consultation_archive_entries` keeps one row per entry read, with how it
was matched (`fedlex_link`, `deadline_title`, `opened_title`,
`opened_institution`, `title`, `document_date`, `bj_live_twin`, `created`,
`ambiguous`, `documents_dated`, `no_documents`) — audit and re-match from there.

Matching: each language entry is matched ALONE (its own-language title vs
Fedlex's title in that language) and entries are grouped by the procedure
they matched — never by position on the page (the 1998 DE and FR pages list
the AHV 11th and BVG 1st revision, same deadline, in opposite order). A
date-backed match needs title similarity >= 0.55, an undated one >= 0.88 and
the significant words of one title inside the other's; two equal
candidates = `ambiguous`, attached to neither.

A BJ project page tells of several consultations (a project and its later
amendments), so its documents are distributed: each opening document
(Vorlage / Erläuternder Bericht) dated D anchors to the BJ/EJPD Fedlex
procedure opened within 10 days of D whose title shares a significant word
(`document_date`, entry `<key>@proj/…`); every document then goes to the
anchor (or the page-level match) whose opening it follows within −1 … +3
years; the rest stays on the project's own procedure (`<key>/rest`). A
page whose documents all went elsewhere is recorded as `documents_dated`;
one without documents or match as `no_documents` (no procedure is made of
it). The old archive's files follow the live project (`bj_live_twin`).

Never twice: a file whose (type, language) the procedure already holds from
Fedlex is not added; after download, an archive file byte-identical to any
stored file is `same_as` that file (text indexed once).

Politeness: one connection, >= 2 s between Wayback requests (1 s for
bj.admin.ch), identifying User-Agent, stop on 429/403 (discovery exits 4;
in the fetch loop only that host's files stop, Fedlex's continue,
`stopped_hosts` in the stats).

### Census (measured 2026-10-20, dry run on a copy of the 10-19 work DB)

All three sources discovered into a copy of the Mac mini's 10-19 work DB
(2,581 Fedlex procedures, 46,168 files, no text), 1,179 requests in all
(cached; a re-run costs none). Report: `consultation_archive census`.

| | admin_pc_wayback | bj (live) | bj_wayback |
|---|---|---|---|
| entries read | 305 procedures 1995–2004 (DE/FR/IT pages merged) | 169 projects | 170 old project pages, 68 fully covered by the live site |
| matched to Fedlex | 304 (303 deadline + title, 1 title); every match has identical titles — Fedlex's pre-2005 records were migrated from this index | 56 page-level (32 opened date + title, 13 title, 7 opened date + office, 4 Fedlex link) + 87 procedures the documents date themselves to | 32 via the live twin, 6 title |
| procedures added (Fedlex lacks) | 1 (UVEK, "Verordnungsänderungen im Strassenverkehr", 2003) | 97 BJ project procedures (incl. `/rest`: documents outside every matched consultation's window) | 14 |
| documents already on Fedlex (skipped) | 114 (278 files): the Chancellery's 2006+ document store went to Fedlex | 585 (1,631 files) | 111 (195 files) |
| documents added | **12** (Vorlage 1, Bericht 1, Diverses 10 = hearing minutes) | **635** (Erl. Bericht 184, Vorlage 53, Ergebnisbericht 49, Stellungnahmen 121, Diverses 228) | **94** |
| files added | 22: 11 archived, 11 never captured (17 / 6 before the port fix below) | 1,871, all live | 178: 122 archived, 56 never captured |

Plus `curated_wayback`: 2 procedures (both on Fedlex without any document:
`proj/1997/2/cons_1`, `proj/1998/4/cons_1`), 2 documents, 5 files (4
archived — each fetched once on 10-20 and checked to be the PDF the page
names, 8–11 pages — and the French "Avant-projet et apport explicatif" of
1998, linked but never captured).

Fedlex procedures before 2005 with a document: 124 of 408 from Fedlex,
**135** with these sources (133 with the Chancellery index and BJ, +2
curated). The rest never had a file online at the Chancellery ("Unterlagen
können bezogen werden bei …"). One old BJ capture answers HTTP 500
persistently (`archiv/lohngleichheit.html`, reported in `pages_failed`; the
live page covers the project).

Parser fixes (2026-10-20, second pass): the 2005 captures' original is
`http://www.admin.ch:80/...`, so their relative links carry the port — they
were not taken for documents, and the CDX answers nothing for a URL with
`:80` (`www.admin.ch:80/bj/vernehm/bgg/prod.pdf`: 0 rows; without: 2).
`cdx_query` drops a default port and a document link's host is read without
it. And an entry ends with its own `</ul>`: the 2016+ template's footer
(disclaimer, impressum, contact) had joined the last entry. Effect: the same
12 documents, +5 archived files (the German hearing minutes of the
Entlastungsprogramm 2003, `proj/2003/113/cons_1`, linked only from the 2005
capture).

Office pages one hop (measured 2026-10-20 in a separate session, not built,
Jonas: drop it): 205 of the 305 entries link no PDF in either layout; 148
distinct office pages read at the capture nearest each deadline carry 546
PDF links, 322 captured; 62 of them attributable to 26 procedures by the
page section's deadline and title words, but only 6 Fedlex procedures would
gain a first document, 2 of them cleanly (now in `curated.json`), and the
strictest attribution rule still admits a wrong document (a speech, a
workshop note). The listing pages carry many consultations' files.

End-to-end check (2026-10-20): the 6 archived Chancellery files fetched
through `build_consultation_corpus.fetch_one` (Wayback `id_`, 2 s pacing):
all `done`, 4–25 pages each.

### Run it (VPS, outside the build window)

Schema: the first open of the work DB by the new code adds the provenance
columns (`ALTER TABLE ... ADD COLUMN`, NULL = Fedlex) and the
`consultation_archive_entries` table — additive, but a schema change of
consultations.db: Jonas's OK first (pipeline gate).

```bash
systemctl is-active opencaselaw-publish.service opencaselaw-vernehmlassungen.service   # both inactive
cd /opt/caselaw/repo && cp /mnt/HC_Volume_104655575/output/consultations_work.db{,.pre-archive}
PYTHONPATH=. python3 -m search_stack.consultation_archive discover \
  --work-db /mnt/HC_Volume_104655575/output/consultations_work.db \
  --source bj,bj-wayback,admin-pc --cache-dir /mnt/HC_Volume_104655575/cache/consultation_archive \
  --report logs/consultation_archive_census.json
```

BJ first (the order in `--source` does not matter: the code runs bj →
bj-wayback → admin-pc), so the old archive only adds what the live site
lacks. ~1.5 h at the polite pace with an empty cache (measured 10-20:
admin-pc ~20 min, bj ~20 min, bj-wayback ~45 min — Wayback CDX answers take
5–40 s); a re-run with the cache costs no requests. Then the next
`opencaselaw-vernehmlassungen` run fetches the ~2,000 queued files (1,871 on
bj.admin.ch at 1 s, 128 on Wayback at 2 s: inside one 3 h budget) and
publishes.

Re-run `--source bj` every few months for new BJ projects (the live
`laufende-*` pages change); the Wayback sources are static.

Applying the second pass (port + footer fixes, curated entries) to a work
DB discovered with 2dc578eb: re-run only the two sources it touches
(`--source admin-pc,curated`; with the 10-20 run's `--cache-dir` it costs a
few dozen Wayback requests at most — 0 here — ~20 min with an empty cache;
the curated part needs no network). Expected on the 10-20 census copy: admin-pc +5 files
(pending, German, `proj/2003/113/cons_1`), curated +2 documents / +5 files
(4 pending, 1 not_archived), nothing requeued, no procedure added; a second
run adds nothing.

Adding a curated entry: append it to `search_stack/consultation_archive_curated.json`
(the Fedlex event id when there is one; per file the language, the page's
own label verbatim, `original_url`, `snapshot` (null = never captured) and
`source_page_url`), fetch each snapshot once to check it is the document,
add a test, `discover --source curated`. A takedown is `suppress` as for
every source.

### Operation

* `check_materialien_completeness.py` reports them under `archive_sources`:
  per source entries / match methods / documents by type / files by status,
  `backlog_by_source`, and `fedlex_before_2005` (Fedlex procedures before
  2005 with a document from Fedlex vs from any source). The Fedlex sections
  count Fedlex rows only. The archive backlog is not a gap unless
  `--archive-strict`.
* `not_archived`: the page linked the file but the Wayback Machine never
  captured it. Final; the row keeps `original_url` so it is listed, not
  hidden.
* Takedown of an archive document: `python3 -m search_stack.consultation_archive
  suppress --work-db … --document-uri urn:opencaselaw:<source>:doc:<key>`
  deletes its text, files and row and records it in the work DB's `meta`
  (`archive_suppressed_documents`), so a later `discover` never adds it
  back; then publish.
