# Historical BGE page ranges and St. Gallen twins (user report 2026-10-07)

**Report.** An external user checked the Hugging Face dataset (`voilaj/swiss-caselaw`
commit `f7d5c459`, 2026-10-06) and the API, and found six things. All six were confirmed on
2026-10-07 through the MCP tools (`get_decision`, `get_decision_structure`):

1. Historical BGE (volumes 1-79) rows overlap their neighbours: BGE 78 IV 83 (Fyg
   gegen Born) opens with the last page of No 21 and ends with the head of No 23
   (Friedlin). 1,336 of 14,578 rows carry two or more ruling headers (user's count).
2. `get_decision_structure` gives BGE 78 IV 83 one "Erwägung 23", which is No 23's
   serial number and text. The Sachverhalt is No 23's as well, and so are `statutes`
   (Art. 148/306 StGB).
3. The API dates BGE 78 IV 83 to 1951-07-17 (`extracted_from_text`), a letter quoted
   in No 21. Its header reads "vom 3. Juni 1952". The ECLI key carries 1951.
4. Dataset ids `bge_historical_78_IV_83` return "Decision not found" from the API, which
   serves the row as `bge_78_IV_83`.
5. St. Gallen rulings are held twice (`sg_gerichte_*` from entscheidsuche, plus the direct
   `sg_publikationen_*`), with two dates. VZ.2004.35 is dated 2004-08-10, the lower
   court's ruling; BZ.2006.83 is dated 2008-07-04, the Kassationsgericht's dismissal.
6. The dataset and the API hold different rows and courts for the same SG ids.

## Root causes

| # | cause | where |
|---|---|---|
| 1 | DFR serves each ruling as its scanned page range in double-page spreads; the scraper stored it verbatim | `scrapers/bge_historical.py` |
| 2 | the extractor read "23. Auszug aus dem Urteil ..." as an Erwägung marker; the unnumbered "Aus den Erwägungen" body precedes it and is dropped; depth-0 paragraphs are never written | `search_stack/extract_decision_structure.py` |
| 3 | `derive_from_text.extract_text_date` takes the first date of the head; the in-build pass `apply_to_db` gates by volume (±1 y lets 1951 through for 1952); the sidecar writer `run_write` had **no gate at all**; the API applied the sidecar date even over a real row date | `backfill_canonical_identity.py`, `mcp_server.py` |
| 4 | `build_fts5.ID_PREFIX_REMAP` serves `bge_historical_*` as `bge_*`; the resolver did not know the scraper form | `mcp_server._bge_ref_candidates` |
| 5 | `scripts/repair_decision_dates.py --all` (2026-03-12) took the first "Urteil/Entscheid vom" date of SG texts; `_cross_court_dedup` keys on docket **and date**, so a misdated twin survives | shards `sg_publikationen.jsonl`, `es_sg_gerichte.jsonl` |
| 6 | see "Dataset" below: the HF files do not come from the current export path | `export_parquet.py`, publish step 3/4 |

## Code changes (this branch)

- `bge_historical_segment.py` (new, pure): places the ruling's own header on its
  reference page (page-number lines; OCR-tolerant) and cuts the text there and at the next
  ruling's header; dates the ruling from that header only. If it cannot place the header,
  it leaves the text alone, so the 1875-1880 Fraktur OCR is never cut. Calibrated on 15
  served rows from 1875 to 1952 (DE/FR/IT, including garbled headers such as "47. A.rtet du
  3 Juin 1882" and "32. Sll1teDl& 24 ottobre 1919").
- `scrapers/bge_historical.py`: new and re-fetched rows are stored cut, with the
  header date.
- `scripts/segment_bge_historical.py` (new): repairs the existing shard (see Steps).
- `backfill_canonical_identity.gate_bge_text_date`: one gate for both the in-build pass and
  the sidecar writer. In volumes 1-79 only the own header's date counts. Later volumes keep
  the existing volume window, which the sidecar writer now gets too (it had none).
- `mcp_server.py`: a sidecar date that disagrees with a real row date is stale and is no
  longer served (nor is its date-bearing key). `bge_historical_*` ids resolve.
- `search_stack/extract_decision_structure.py`: volumes 1-79 are cut to the own ruling
  before extraction; a ruling heading is never an Erwägung and ends the paragraph before
  it. `bge_historical_segment.py` joins the extractor version hash, **so the next
  incremental structure run bootstraps (full re-extraction)**. Every change to the
  extractor already triggers this; plan for the run time.
- `scripts/restore_sg_dates.py` (new): restores SG dates from the court's own citation
  trailer ("(Kantonsgericht, ..., 14. Februar 2005, VZ.2004.35)") or the Kopfzeile, only
  where they name the row's docket.
- `build_fts5._cross_court_dedup`: the id of a folded twin is recorded in
  `decision_id_aliases` (`source='cross_court_dedup'`), so `sg_gerichte_VZ.2004.35` keeps
  resolving to the copy that is kept. This applies to every overlap group (ZH, AG, SG, ...).
- `scripts/repair_decision_dates.py`: refuses or skips `bge`, `es_bge`, `bge_historical`,
  `sg_publikationen` and `es_sg_gerichte`, the shards it is known to damage.

## Steps on the VPS (after review and merge; never during the build window)

Prerequisite: `make test` and `make verify-offline` pass, and the deploy has gone through
`scripts/agent_safe_deploy.py` / the normal deploy. Before each `--apply`, take a copy of
the shard (the BGE script also writes an undo file).

```bash
# 1. dry runs: read the counts and the examples before applying
python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl --examples 40
python3 scripts/restore_sg_dates.py output/decisions/sg_publikationen.jsonl --examples 40
python3 scripts/restore_sg_dates.py output/decisions/es_sg_gerichte.jsonl --examples 40
```

Expected from the dry runs:

- BGE: `outcome:placed` for most rows and `text_cut` around or above 1,336. Every
  `date:placeholder` row had an unreadable header date; spot-check five of them.
  `outcome:own_header_not_placed` covers the Fraktur volumes and badly garbled pages.
- SG: `own_date:regeste_trailer` / `own_date:kopfzeile` for the 03-12 rows,
  `unstamped_disagree` ideally 0. If it is not 0, look at the examples before using
  `--include-unstamped`. Any `conflict` rows stay unchanged; list them.

```bash
# 2. apply (shards are the build's input)
cp output/decisions/bge_historical.jsonl /tmp/bge_historical.jsonl.bak-20261007
python3 scripts/segment_bge_historical.py output/decisions/bge_historical.jsonl --apply
python3 scripts/restore_sg_dates.py output/decisions/sg_publikationen.jsonl --apply
python3 scripts/restore_sg_dates.py output/decisions/es_sg_gerichte.jsonl --apply
# 3. next full build picks them up (quick_publish does not re-read changed rows)
# 4. regenerate the canonical-identity sidecar from the new decisions.db, at the path
#    the MCP server reads (CANONICAL_DB_PATH = $SWISS_CASELAW_CANONICAL_DB or
#    $SWISS_CASELAW_DIR/canonical_identity.db); write a temp file, then rename
D="$SWISS_CASELAW_DIR"
python3 backfill_canonical_identity.py --db "$D/decisions.db" --write "$D/canonical_identity.db.tmp" \
  && mv "$D/canonical_identity.db.tmp" "$D/canonical_identity.db"
```

Step 4 is still needed after step 3, for two reasons. The API now ignores a stale sidecar
date only where the row carries a real date. And the sidecar's ECLI keys keep the old year
until they are rebuilt.

## Verification after the build

- `get_decision('bge_78_IV_83')`: text starts "22. Auszug aus dem Urteil des
  Kassationshofes vom 3. Juni 1952", contains no "Friedlin", date 1952-06-03, ECLI year 1952.
- `get_decision_structure('bge_78_IV_83')`: no Erwägung "23"; the Sachverhalt is not
  No 23's.
- `get_decision('bge_historical_78_IV_83')` resolves.
- `cite('VZ.2004.35')` and `cite('BZ.2006.83')`: one row each, dated 14. Februar 2005 and
  16. Oktober 2007. `get_decision('sg_gerichte_VZ.2004.35')` resolves to the kept copy.
- Search `"Prozessbetrug" court:bge date 1952`: BGE 78 IV 84 only, not 78 IV 83.

## Dataset (findings 4 and 6) — open, needs the VPS

The current export (`export_parquet.export_from_db`) reads decisions.db, which never holds
court `bge_historical`, and it deletes per-court files it did not write. The upload prunes
remote `data/*.parquet` files that are absent locally. So a `data/bge_historical.parquet` with
`bge_historical_*` ids and raw shard dates (1952-01-01) can only come from the JSONL
fallback (`export_parquet.py` without a DB at `output/decisions.db`: no court/id remap, no
dedup, no stale-file cleanup), or from an upload that has not succeeded since such a run.
The dataset card is uploaded **before** the data folder, so the repo's "last modified" date
says nothing about the data files. Check:

```bash
ls -la --time-style=full-iso output/dataset/*.parquet | sort -k6,7 | head
grep -h "falling back to JSONL\|Reading from FTS5 database\|Uploaded .* files to" logs/publish.log | tail -20
```

`structure/structure.parquet` is a separate cause, and it is known. Since the sidecar has
been built from served text (2026-09-09), the metadata export projects over its budget and
keeps the last good file (`export_parquet._bounded_stream`). The published structure.parquet
is therefore the shard-era one, with `bge_historical_*` ids, while the Sunday paragraphs
export is current, with `bge_*` ids. This is the id mismatch in finding 2. The proposed fix
(not in this branch, schema change to the sidecar): store the four has-section flags as
small columns at extraction time, or index them as expressions, so the export no longer
walks the text overflow pages.

## Not done here (proposals)

- An unnumbered Erwägung ("Aus den Erwägungen :" with no numbers, as in BGE 78 IV 83) is
  parsed as paragraph "0" (depth 0), and both structure builders skip depth 0. Serving it
  would make `get_erwaegung` reach such rulings, but `find_relevant_erwaegung` / `cite` would
  then need a rule for pinpointing "E. 0", across all courts. Decide before building.
- `statutes` and the citation graph for the cut rows follow from the next full build, from
  the cut text. No separate step is needed.
