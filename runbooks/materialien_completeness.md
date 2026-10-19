# Federal Materialien: complete coverage from Fedlex (deploy + operation)

Goal: every federal preparatory document Fedlex publishes is in the corpus,
verbatim, and a nightly census proves it.

| Corpus | Fedlex population (census 2026-10-19) | Where | Path |
|---|---|---|---|
| Bundesblatt, typed (Botschaft 23, Bericht BR 24, Stellungnahme BR 25, Bericht Kommission 30, Erlassentwurf 38, Eröffnung Vernehmlassung 40), 1999– | 5,627 DE / 5,630 FR / 5,625 IT | `materialien.db` `botschaft_documents.doc_type` | 04:30 unit (`opencaselaw-materialien.service`) |
| Bundesblatt, historical 1850–1999 (Botschaften incl. Nachtrags-/Ergänzungsbotschaften, Berichte BR, Kommissionsberichte, Parl. Initiativen) | 12,205 DE (8,819 Botschaften + 3,386 Berichte) / ~12,000 FR / ~3,300 IT | `materialien.db`, `source_collection='bar-amtsdruckschriften'` | `opencaselaw-materialien-historical.timer` 00:20 |
| Vernehmlassungen (2,581 procedures 1993–; ~5 GB of text when complete): Erläuternder Bericht 2,434 · Vorlage 3,275 · Ergebnisbericht 1,943 · Stellungnahmen 1,214 · Synoptische Tabelle 243 · Diverses 683 | 46,168 language files, ~28,700 under the text policy | `consultations.db` (own DB, volume) | `opencaselaw-vernehmlassungen.timer` 17:45 + 22:45 |

Not on Fedlex, scoped but not built (memory `materialien-non-fedlex-sources`):
1995–2004 consultation documents (Wayback of `admin.ch/ch/d/gg/pc/ind<YEAR>.html`),
BJ Rechtsetzungsprojekte (expert-commission Vorentwürfe / Begleitberichte),
ordinance Erläuterungen (news.admin.ch API, contract unverified).

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
