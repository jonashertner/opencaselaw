# Runbook: BGE entscheidsuche rows take their direct twin's date (2026-10-14)

> **Superseded the same day by `runbooks/bge_es_retirement.md` (update 2026-10-14).** The
> direct rows hold all three Regeste languages in their abstracts; once those are composed
> into `regeste`, retiring the feed fixes the dates and leaves one id per BGE with nothing
> lost. Use this runbook only if retirement is deferred.

**What is wrong (report 2026-10-13, finding 3).** `BGE 141 II 280` and `BGE 151 III 227`
are served on 1 January (2015-01-01, 2025-01-01). Their entscheidsuche rows
(`bge_BGE_…`, `es_bge.jsonl`) carry the volume placeholder because the Kopfzeile states
only the year ("Band III 2025"); the direct rows (`bge_151 III 227`, `bge.jsonl`) hold the
date of their own Urteilskopf ("4A_418/2024 vom 20. Dezember 2024"). Served on
2026-10-14: 4,261 BGEs whose es row is a placeholder while the direct row is dated, 234
with two different real dates, and 10,003 `bge` rows on a 1 January or empty date.

**Why not retire es_bge.jsonl now (as first assessed; corrected in the retirement
runbook's 2026-10-14 update)** The feed's rows looked like the fuller copies: they hold the
Regeste in German, French and Italian in `regeste`, the direct rows only in German (BGE 151 III 227: 4,817 vs 1,499 Regeste characters; every one of the
20,718 shared BGEs has a >10 % shorter Regeste in the direct row). Retirement would drop
the French and Italian Regeste until the direct scraper fetches them from bger.ch.

**The fix.** `scripts/restore_bge_dates_from_urteilskopf.py es_bge.jsonl --twin-shard
bge.jsonl`: an es row takes the direct twin's date when the twin's own Urteilskopf states
that date (20,288 direct rows qualify); an es Kopfzeile date that contradicts it is left
alone (0 such rows). Every change carries a `date_restore` stamp (method `direct_twin`).
The build's dedup then folds each pair into the fuller row, the es row with all three
Regeste languages, as it already does for the 6,920 BGEs whose dates agree today
(e.g. BGE 140 III 244: one row, `get_decision("bge_140 III 244")` resolves to it,
pinpoints verified).

**Measured 2026-10-14** (dry run on a copy of the live shards; seven of the 184 changes of
a real date read against both Urteilskopf lines, all seven twin dates right, the es dates
were statute or ordinance dates from the 2026-03-12 pass; local build of the four BGE
shards before/after):

| | before | after |
|---|---|---|
| es rows changed | | 15,889 (15,704 placeholders, 184 real dates, 1 outside the volume) |
| distinct BGEs served | 35,687 | 35,687 (0 lost, 0 gained) |
| BGEs under more than one id | 13,798 | 506 |
| `bge` rows on 1 January / empty | 10,003 | 5,928 (the historical volumes with no date in any source) |
| refs whose served text or Regeste got shorter | | 0 |
| `bge` rows | 49,486 | 36,193 (73.1 %) |

**Swap gate.** `bge` falls to 73 % of its live rows, below the per-court 80 % floor; the
dedup that folds these pairs records no alias, so #141's accounting does not count them.
Exempt this one court for exactly one full build: `OCL_SWAP_GATE_EXEMPT=bge` (the global
row gate stays on; the corpus shrinks by ~1.2 %). Not `OCL_SKIP_SWAP_GATE`.

**Downstream.** ~13,300 direct-form ids (`bge_151 III 227`) leave the served DB and the
Hugging Face parquet; every one resolves to its surviving `bge_BGE_` row through the
id-form candidates (`db2320b5`). Citation edges already point at both forms; counts stop
being split. Note it in the dataset changelog.

## Apply

Window: any time outside a full build's step 2 (nothing appends to es_bge.jsonl; the
direct shard is only read). Then the next full build picks it up.

1. Backup, dry run (writes nothing to the shard):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; B=/mnt/HC_Volume_104655575/quarantine/bge-twin-dates-$(date -u +%Y%m%d); mkdir -p $B; cp -a $D/es_bge.jsonl $B/; ls -la $B; python3 scripts/restore_bge_dates_from_urteilskopf.py $D/es_bge.jsonl --twin-shard $D/bge.jsonl --examples 0 2>&1 | grep -v "contradicts BGE volume"'
```

Expect `twin dates confirmed …: 20,288`, `15889 rows_changed`, `18503 method:direct_twin`,
no `kopfzeile_twin_conflict`. Stop if `rows_changed` differs by more than a few rows.

2. Apply:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/restore_bge_dates_from_urteilskopf.py $D/es_bge.jsonl --twin-shard $D/bge.jsonl --apply --examples 0 2>&1 | grep -E "rows_changed|direct_twin"'
```

3. Before the next full build starts, add `OCL_SWAP_GATE_EXEMPT=bge` to
   `/opt/caselaw/repo/.env.publish` (operator edit; if the variable already exists,
   append `,bge` to it). Remove it again once that build has swapped.

Rollback: copy the backup back; the next full build restores the pairs.

## Verify after the build

```bash
for id in bge_BGE_151_III_227 bge_BGE_141_II_280 "bge_151%20III%20227"; do curl -s "https://mcp.opencaselaw.ch/api/decisions/$id" | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("decision",d); print(d.get("decision_id"), d.get("decision_date"), len(d.get("regeste") or ""))'; done
```

Expected: `bge_BGE_151_III_227 2024-12-20`, `bge_BGE_141_II_280 2015-04-13`, the third
resolving to `bge_BGE_151_III_227`; Regeste lengths in the thousands (three languages).
