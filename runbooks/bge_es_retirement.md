# Retire es_bge.jsonl: one id per BGE (#40)

Every BGE was served twice: under the direct scraper's id (`bge_140 III 244`,
Ia/Ib upper-case `bge_116 IA 28`, from `bge.jsonl`) and under the frozen
entscheidsuche feed's id (`bge_BGE_140_III_244`, Ia/Ib both `_Ia_` and `_IA_`,
from `es_bge.jsonl`, untouched since 2026-03-12). The build keeps a pair
whenever the two dates differ, so 15,038 BGEs were served under two or three
ids and 6,813 of them with conflicting dates (replica of the served build,
2026-09-27). Citation counts are split across the ids, and every BGE listing
shows duplicates.

The feed adds no ruling: all 20,718 BGE references in `es_bge.jsonl` are in
`bge.jsonl` (which has 159 more, volumes 151-152). Retiring the file leaves
each BGE once, under the direct id, the id the live official scraper produces
(no-entscheidsuche rule: retire an `es_*` feed after verified direct parity).

## Rehearsal (replica of the served BGE build, 2026-09-28)

| | today (both shards) | after retirement |
|---|---|---|
| distinct BGEs served | 35,455 | 35,455 (0 lost, 0 gained) |
| BGEs under more than one id | 15,038 | 0 |
| `bge` rows | ~50,500 | 35,455 |
| dates outside volume ±1 | 4 (after the 09-27 repairs) | 2 (BGE 149 IV 97 and 98 Ib 396, genuine late publications) |

## Code already on main

- `175ffe4a` serving fold (find_leading_cases, search): no-op once the ids are gone.
- `597a2679` build date guard + historical shard repair (applied 2026-09-27).
- `db2320b5` every read path resolves the direct id: `cite`/`get_decision`/
  `attest` reach upper-case Ia/Ib ids, `/entscheid/bge_BGE_…` 301s to the stored
  id, search's leading-BGE boost, the citation-graph quality check, the
  benchmark resolvers, and `ocl doctor`'s cite check.

The reference graph already resolves "BGE x" citations to the direct ids
(`search_stack/build_reference_graph.py` pass 2 matches the docket with the
prefix stripped), so citation counts consolidate on the direct id at the next
graph build.

## Preconditions

1. The 2026-09-28 full build has exited and the audit against the served DB
   shows the repaired dates:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 audit_bge_decision_dates.py --db /mnt/HC_Volume_104655575/output/decisions.db | head -8'
   ```
2. `db2320b5` is in the VPS tree (the pipeline's end-of-run pull brings it) and
   the workers have been restarted since (rolling restart), so the serving
   side resolves the direct ids before the feed's rows disappear:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git merge-base --is-ancestor db2320b5 HEAD && echo HAS_DB2320B5'
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh'
   ```

## Steps (any time outside a full build's step 2; nothing writes es_bge.jsonl)

1. Move the feed out of the build's input directory (reversible):
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo/output && mkdir -p retired && mv decisions/es_bge.jsonl retired/es_bge.jsonl && mv decisions/es_bge.jsonl.bak-datefix-20260928 retired/ && ls -la retired/'
   ```
2. Swap-gate override for exactly the next full build. `bge` drops to ~70% of
   its live rows, below the per-court 80% floor, so the gate would refuse the
   swap (see memory: swap-gate-rekey-trap). Add `OCL_SKIP_SWAP_GATE=1` to
   `/opt/caselaw/repo/.env.publish` (it also disables the global row gate:
   one night only).
3. After that build has swapped and the pipeline has exited, remove the line
   from `.env.publish` again, then verify:
   - `bge` row count ~35,500 and no `bge_BGE_%` ids;
   - `cite('BGE 116 Ia 28')` and `cite('BGE 140 III 244')` resolve (direct ids);
   - `/entscheid/bge_BGE_140_III_244` answers 301 to `/entscheid/bge_140%20III%20244`;
   - `find_leading_cases(law_code='OR', article='273')` lists each BGE once;
   - `audit_bge_decision_dates.py --db …` as in precondition 1.

## Downstream notes

- HuggingFace dataset: ~15,000 `bge_BGE_…` ids disappear from the published
  parquet; the same rulings remain under the direct ids. Note it in the dataset
  card / changelog. No stale file to delete (the court code stays `bge`).
- Paper 2's substrate (citation counts by id) changes: counts consolidate on
  one id per BGE.
- Rollback: move `retired/es_bge.jsonl` back to `decisions/`; the next full
  build restores the rows (the gate is shrink-only, growth passes).
