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

## Update 2026-10-14: the direct rows first get the Regeste in all three languages

The feed's rows carry the Regeste in German, French and Italian in `regeste`; the direct
rows had only the German one there. The direct scraper has always fetched all three
(`abstract_de` / `abstract_fr` / `abstract_it`, 20,880 of 20,882 rows), but FTS indexes
`regeste` and `full_text`, not the abstracts, and `get_regeste` returns `regeste`: retiring
the feed as it stood would have made the French and Italian Regeste of every BGE
unsearchable. `scrapers/bge.py` now composes `regeste` from the three
(`trilingual_regeste`), and `scripts/compose_bge_trilingual_regeste.py` does it for the rows
already stored, offline, from those fields (no request to bger.ch).

Rehearsal 2026-10-14 (local build of copies of the live bge, es_bge, bge_historical and
bge_egmr shards; A = today, B = backfill applied and es_bge.jsonl retired):

| | A | B |
|---|---|---|
| distinct BGEs | 35,687 | 35,687 (0 lost, 0 gained) |
| BGEs under more than one id | 13,798 | 0 |
| `bge` rows | 49,486 | 35,687 (72.1 %) |
| rows on 1 January / empty | 10,003 | 5,731 (historical volumes with no date in any source) |
| BGEs with French / Italian Regeste text | 14,256 / 20,718 | 14,382 / 20,880 |
| FTS `regeste:"déroulement de la procédure"` (and the Italian, and "prohibition de concurrence") | 13 / 13 / 7 hits | 13 / 13 / 7 hits |
| BGEs whose best Regeste is >10 % shorter | | 1 (127 V 237: the direct row holds no abstracts and a 202-character German Regeste against 671 in the feed; one re-fetch once bger.ch answers again) |

BGE 151 III 227 / 141 II 280 / 138 III 67 / 116 Ia 28 end as one row each, dated
2024-12-20 / 2015-04-13 / 2012-01-10 / 1990-05-02, with all three Regeste languages.
This replaces the twin-date repair (runbooks/bge_twin_dates_2026-10-14.md), which keeps
the feed and is only the fallback if retirement is deferred.

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

3. `scrapers/bge.py` with `trilingual_regeste` is in the VPS tree (pushed and
   fast-forwarded), so BGEs scraped from now on carry all three languages.

## Steps (any time outside a full build's step 2; nothing writes es_bge.jsonl)

0. Backfill the direct rows' Regeste (bge.jsonl is appended by the 09:00 federal
   scrape and the 10:20 late run: not during those). Backup, dry run, apply:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; B=/mnt/HC_Volume_104655575/quarantine/bge-trilingual-$(date -u +%Y%m%d); mkdir -p $B; cp -a $D/bge.jsonl $B/; ls -la $B; python3 scripts/compose_bge_trilingual_regeste.py $D/bge.jsonl --examples 0 2>/dev/null'
   ```
   Expect `composed` ~20,881 (one per direct BGE with abstracts), `no_abstracts` 1. Then:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; python3 scripts/compose_bge_trilingual_regeste.py /mnt/HC_Volume_104655575/output/decisions/bge.jsonl --apply --examples 0 2>/dev/null'
   ```

1. Move the feed out of the build's input directory (reversible):
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo/output && mkdir -p retired && mv decisions/es_bge.jsonl retired/es_bge.jsonl && mv decisions/es_bge.jsonl.bak-datefix-20260928 retired/ && ls -la retired/'
   ```
2. Swap-gate exemption for exactly the next full build. `bge` drops to ~72 % of
   its live rows, below the per-court 80 % floor, so the gate would refuse the
   swap (see memory: swap-gate-rekey-trap). Add `OCL_SWAP_GATE_EXEMPT=bge` to
   `/opt/caselaw/repo/.env.publish` (operator edit; append `,bge` if the variable
   exists). It exempts this one court; the global row gate stays on (the corpus
   shrinks by ~1.3 %). Not `OCL_SKIP_SWAP_GATE`, which switches every gate off.
3. After that build has swapped and the pipeline has exited, remove the line
   from `.env.publish` again, then verify:
   - `bge` row count ~35,500 and no `bge_BGE_%` ids;
   - `cite('BGE 116 Ia 28')` and `cite('BGE 140 III 244')` resolve (direct ids);
   - `/entscheid/bge_BGE_140_III_244` answers 301 to `/entscheid/bge_140%20III%20244`;
   - `find_leading_cases(law_code='OR', article='273')` lists each BGE once;
   - `audit_bge_decision_dates.py --db …` as in precondition 1;
   - `get_regeste('BGE 151 III 227')` returns the German, French and Italian Regeste,
     and `search_decisions('"déroulement de la procédure"', court='bge')` finds it.

## Downstream notes

- HuggingFace dataset: ~15,000 `bge_BGE_…` ids disappear from the published
  parquet; the same rulings remain under the direct ids. Note it in the dataset
  card / changelog. No stale file to delete (the court code stays `bge`).
- Paper 2's substrate (citation counts by id) changes: counts consolidate on
  one id per BGE.
- Rollback: move `retired/es_bge.jsonl` back to `decisions/`; the next full
  build restores the rows (the gate is shrink-only, growth passes).
