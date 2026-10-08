# Hugging Face dataset: stale root-level parquet files (2026-10-07)

**Status (2026-10-08).**

| item | state |
|---|---|
| 99 stale root files | listed (`hf_root_parquet_cleanup_2026-10-07.files.tsv`). Deletion **ready, not run**: it needs a write token for `voilaj/swiss-caselaw`, which only the owner holds. Exact steps below. |
| `load_dataset` broken by `data/delta-*.parquet` | **fixed in this branch** (card config); takes effect with the first nightly card upload after merge. Removing the delta from `data/` is a proposal. |
| guard | `scripts/check_hf_unmanaged_parquet.py`, wired into the existing 6-hourly `check_output_freshness` run (own ntfy alert family). No systemd change. |

Follow-up to `runbooks/historical_bge_and_sg_twins_2026-10-07.md`, section "Dataset (findings
4 and 6)". That runbook reached `main` in jonashertner/opencaselaw#130; the list and the
deletion plan live here.

## Evidence (read-only, 2026-10-07)

Read from the Hub with `HfApi.list_repo_files` and `HfApi.get_paths_info(..., expand=True)`
(last commit per path). Repo head `f7d5c459`, 2026-10-06 22:54 UTC.

| prefix | files |
|---|---:|
| `artifacts/` | 446 |
| `data/` | 121 |
| repo root | 101 (99 `*.parquet`, `README.md`, `.gitattributes`) |
| `graph/` | 2 |
| `structure/` | 2 |

All 99 root parquet files were last written by two manual uploads; neither commit came from
`publish.py`:

| commit | date (UTC) | title | files |
|---|---|---|---:|
| `b4746827` | 2026-03-14 06:41 | Update dataset (full rebuild 2026-03-14) | 94 |
| `04297085` | 2026-03-03 11:46 | Update dataset (Mar 3, 2026): +NE 7.4k, +LU 605, +SZ catch-up | 5 (`ag_justizgericht`, `bs_gerichte`, `zh_bezirksgericht_affoltern`, `zh_bezirksgericht_meilen`, `zh_mietgericht`) |

Each one is stale:

- **98** have a counterpart of the same name under `data/`. In every case the counterpart was
  last committed later (2026-07-04 12:57 to 2026-10-06 22:12 UTC), and its LFS sha256
  differs.
- **1**, `bge_historical.parquet`, has no counterpart. The court is no longer exported on its
  own: `build_fts5.py` folds court `bge_historical` into `bge` and the id prefix
  `bge_historical_` into `bge_` (`COURT_REMAP` and `ID_PREFIX_REMAP`). Its
  14,578 rows are in `data/bge.parquet`.

Content check: only the `court` column of each root file was read, over HTTP range requests
at `f7d5c459`. Every file holds only its own court (court = file stem): 960,526 rows, 6.95 GB
in all. None holds a court of `export_parquet.EXCLUDED_COURTS` (ECtHR / EGMR, not CC0). So
the problem is staleness only, not licence exposure.

Nothing in this repo points at the root files. A grep for `swiss-caselaw/<name>.parquet` and
`resolve/main/<name>.parquet` outside `data/`, `graph/`, `structure/` and `artifacts/` finds
nothing. The dataset card's `load_dataset` config read `data/*.parquet` (court files only since
this branch, see below). The MCP bootstrap
downloads only `data/` (`mcp_server.py`, `f.startswith("data/")`).

The full list, with size, row count, last commit and current counterpart:
`hf_root_parquet_cleanup_2026-10-07.files.tsv` (99 rows).

## Why they persist

No publisher writes to or prunes the repo root:

- `publish.py` step 4 calls `upload_folder(path_in_repo="data", delete_patterns="*.parquet")`.
  `delete_patterns` is relative to `path_in_repo`, so it prunes only `data/`. The aux uploads
  do the same for `graph/` and `structure/`.
- `search_stack/publish_delta.py` writes `artifacts/sqlite/deltas/`,
  `artifacts/parquet/deltas/`, `artifacts/manifest.json` and `data/delta-{date}.parquet`.
- The verification pack goes to `artifacts/verification_pack/`, and `pipeline.py` writes to
  `data/daily/`.

## Deletion (ready; to be run by the owner)

**Do not use `HfApi.delete_files(..., delete_patterns=["*.parquet"])`**, as the twins
runbook proposed. `delete_files` always matches from the repo root, and `fnmatch`'s `*`
crosses `/`. Simulated with `huggingface_hub` 2.1.1 against the 2026-10-07 listing, that
pattern matches **440** files: the 99 root files plus all of `data/` (121), `graph/` (2),
`structure/` (2) and 216 parquet files under `artifacts/`. Delete by an explicit path list
only.

**Who runs it.** Someone with a write token for `voilaj/swiss-caselaw`: the owner, or the
publish host's token. The cloud sessions that prepared this have none (the Hub answered them
unauthenticated). Run it outside the nightly build window, from a checkout of `main` once this
branch is merged, with the token in `HF_TOKEN` (never echo it).

**Step 1: delete.** One Hub commit holds exactly the 99 documented paths. `parent_commit`
makes it fail if the repo moved after the listing; then simply run it again. `DRY_RUN = True`
prints the plan and changes nothing (verified on 2026-10-08 at `f57a5b42`).

```bash
python3 - <<'EOF'
import csv, sys
sys.path.insert(0, ".")
from huggingface_hub import HfApi, CommitOperationDelete
from scripts.check_hf_unmanaged_parquet import HF_REPO_ID, MANAGED_PREFIXES, unmanaged_parquet

DRY_RUN = True  # set False to delete
api = HfApi()
head = api.dataset_info(HF_REPO_ID).sha
found = unmanaged_parquet(api.list_repo_files(HF_REPO_ID, repo_type="dataset", revision=head))
with open("runbooks/hf_root_parquet_cleanup_2026-10-07.files.tsv", encoding="utf-8") as f:
    documented = sorted(r["path"] for r in csv.DictReader(f, delimiter="\t"))
assert found == documented, sorted(set(found) ^ set(documented))
assert len(found) == 99 and all("/" not in p for p in found)
assert not any(p.startswith(MANAGED_PREFIXES) for p in found)
print(f"head {head[:8]}: delete {len(found)} root parquet files ({found[0]} .. {found[-1]})")
if not DRY_RUN:
    info = api.create_commit(
        repo_id=HF_REPO_ID, repo_type="dataset", parent_commit=head,
        operations=[CommitOperationDelete(path_in_repo=p) for p in found],
        commit_message="Remove 99 stale root-level parquet files (manual uploads of March 2026)",
        commit_description="Current files are under data/. See runbooks/hf_root_parquet_cleanup_2026-10-07.md",
    )
    print(info.commit_url)
EOF
python3 scripts/check_hf_unmanaged_parquet.py   # afterwards: "0 parquet file(s) outside ..."
```

**Step 2: changelog, the same day, only after step 1 succeeded.** Add this entry to the
"## Changelog" section of `dataset_card.md` (the section exists since this branch), commit it
to `main`. The nightly publish uploads the card as the Hub `README.md`, so do not commit it
before the deletion:

```markdown
- YYYY-MM-DD: Removed 99 parquet files from the repository root, left over from manual uploads on 2026-03-03 and 2026-03-14. They were never part of the `load_dataset` configuration, but direct downloads of them returned the March 2026 corpus. Current files are under `data/`; the old ones remain available at revision `b4746827`.
```

**Effect and rollback.** Afterwards, a direct download of a root URL returns 404 instead of
March data: a loud failure instead of silently stale data. External scripts that hard-coded
root URLs will break; nothing in this repo does. The files stay in the repo history
(`hf_hub_download(..., revision="b4746827")`) and can be re-uploaded if needed. Do not squash
the history to reclaim the 6.95 GB.

## Guard

```bash
python3 scripts/check_hf_unmanaged_parquet.py            # exit 1 on a finding, 0 clean, 2 listing failed
python3 scripts/check_hf_unmanaged_parquet.py --details  # + size and last commit per stale file
python3 scripts/check_hf_unmanaged_parquet.py --json
python3 scripts/check_hf_unmanaged_parquet.py --card dataset_card.md   # check the local card instead of the Hub's README
python3 scripts/check_hf_unmanaged_parquet.py --files-from list.txt   # offline
```

Two checks:

1. Parquet files outside `data/`, `graph/`, `structure/` and `artifacts/`. A new publisher
   path outside them shows up as a finding, which fails safe.
2. The card's default config, as the Hub serves it (`README.md` at the listed revision): it
   must read every court file directly under `data/` and nothing else.

On 2026-10-08 (`f57a5b42`) it reports the 99 files of the TSV and the live config reading
`data/delta-2026-10-08.parquet`. With this branch's card (`--card dataset_card.md`) the second
finding is gone.

**Monitoring.** `scripts/check_output_freshness.py` runs the same checks
(`check_hf_layout`) on its existing timer (`opencaselaw-output-freshness.timer`, every 6 h; three
Hub API calls per run). Findings go out as their own ntfy family, "opencaselaw
HF dataset layout": default priority, own state file
(`state/hf_layout_last_dispatched.json`), the same de-duplication, a daily re-nag while
unresolved, and one all-clear. The freshness page keeps its wording and state.

A failed listing is logged, not paged, and sends no all-clear either (an armed alert stays
armed). Logging rather than paging is the script's convention for every signal, to avoid
paging on transient Hub errors. A Hub outage long enough to matter also stops the
mirror's `lastModified`, which the freshness signal pages on.

**Expect after deploy:** one layout page right away and one a day until the deletion is done.
The delta line clears with the first nightly card upload after merge.

## `data/delta-*.parquet` and `load_dataset`

### Finding (verified 2026-10-07/08)

`load_dataset("voilaj/swiss-caselaw")`, the call the card recommends, fails while a daily
delta sits under `data/`.

- `publish_delta.py` writes `data/delta-{date}.parquet` with `BASE_COLS`: 15 string
  columns, of which 8 match the court files' 41 (`DECISION_SCHEMA`). It has `id`, `docket`,
  `content_text`, ...; it lacks `decision_id`, `full_text`, ....
- The card's config `data/*.parquet` reads it. `datasets` refuses a split whose files have
  different columns ("column names don't match").
- Reproduced end to end on 2026-10-08: a local copy of the repo layout with the published
  `data/gr_gerichte.parquet`, `data/ag_strafgericht.parquet` and
  `data/delta-2026-10-08.parquet`. With the old card, `load_dataset` raises
  `DatasetGenerationError`; with this branch's card it loads 18,036 rows, 41 columns.
- Since when: the Hub history has 228 "daily delta" commits since 2026-02-08, and step 4 prunes
  the previous delta each night, so `data/` holds one delta almost all the time. Probably
  broken for about eight months, but not proven day by day.
- Nobody reported it: no GitHub issue, and the Hub's only discussion is the parquet bot's.
- All 120 court files share one schema (footers read on 2026-10-08), so the delta is the only
  file in the way.
- The delta's rows are also in the court files of the same night: all 138 rows of
  `delta-2026-10-06`. Step 7 runs after step 4 on the same `decisions.db`. So even a delta with
  the right columns would double-count the latest day.

### What "outside users rely on that path" amounts to

`publish_delta.py` says it keeps the old private pipeline's paths for external consumers. For
`data/delta-{date}.parquet` specifically:

- It lives about a day: step 4's `delete_patterns` prunes it each night.
- `artifacts/manifest.json` does not list it; it lists 217 entries under
  `artifacts/parquet/deltas/`.
- No doc in this repo mentions it.
- The same rows are in `artifacts/parquet/deltas/{date}.parquet`, written with
  `DECISION_SCHEMA` (`_export_parquet_base_schema`), and in the court files.

A consumer would have to poll the dated URL every day. That cannot be ruled out: the Hub gives
no per-file download counts.

### Options

| option | fixes `load_dataset` | delta consumers | state |
|---|---|---|---|
| A. card config reads court files only: `data/*[!0-9].parquet` (delta names end in a digit, court codes never do) | yes; verified end to end and against the live listing | unaffected | **done in this branch** |
| B. stop writing `data/delta-*` in `publish_delta.py` (drop the `BASE_COLS` export and its upload); step 4 prunes the last one the next night | yes, and `data/*.parquet` would be correct again by construction | a daily poller of `data/delta-{date}.parquet` gets 404; the same rows stay in `artifacts/parquet/deltas/` | **proposal** (pipeline publish path, public path) |
| C. move the delta to its own folder with its own card config | yes | the dated URL changes anyway (same breakage as B); a one-day config has little value | not recommended |
| D. write `data/delta-*` with `DECISION_SCHEMA` | yes | every column name a consumer reads changes; the latest day's rows load twice | not recommended |

A's one weakness is naming: a court code ending in a digit would be skipped silently.
Two checks close it:

- the offline test `test_card_reads_every_court_of_the_committed_stats` checks every court
  code in `docs/stats.json` (refreshed nightly), so it fails `make test`;
- the guard's card check reports any court file the live config skips.

**Recommendation.** Merge A now. Do B once the owner is satisfied that nobody polls
`data/delta-{date}.parquet`; add a changelog line pointing to `artifacts/parquet/deltas/`.
After B, the config can stay as it is.

## Note for the twins runbook

Its "Dataset" paragraph first proposed `delete_files(..., delete_patterns=["*.parquet"])`,
which would have emptied the dataset (see above). jonashertner/opencaselaw#133 (merged
2026-10-08) replaced it with the explicit-list method and a pointer here.
