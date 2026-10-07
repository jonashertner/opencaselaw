# Hugging Face dataset: stale root-level parquet files (2026-10-07)

**Status.** 99 stale files confirmed and listed. Deletion **deferred**: it writes to the
public dataset and needs the repo owner's explicit approval. Guard added:
`scripts/check_hf_unmanaged_parquet.py` (read-only, offline test
`tests/test_check_hf_unmanaged_parquet.py`).

Follow-up to `runbooks/historical_bge_and_sg_twins_2026-10-07.md`, section "Dataset (findings
4 and 6)". That runbook is on branch `claude/historical-bge-sg-twins-2026-10-07` and is not
yet on `main`, so the list lives here.

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
nothing. The dataset card's `load_dataset` config reads `data/*.parquet`. The MCP bootstrap
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

## Proposed deletion (NOT run; needs the owner's approval)

**Do not use `HfApi.delete_files(..., delete_patterns=["*.parquet"])`**, as proposed in the
twins runbook. `delete_files` always matches from the repo root, and `fnmatch`'s `*`
crosses `/`. Simulated with `huggingface_hub` 2.1.1 against the 2026-10-07 listing, that
pattern matches **440** files: the 99 root files plus all of `data/` (121), `graph/` (2),
`structure/` (2) and 216 parquet files under `artifacts/`. Delete by an explicit path list
only.

One Hub commit holds exactly the 99 documented paths, plus the dataset card with the
changelog line. `parent_commit` makes the commit fail if the repo moved after it was listed;
in that case, list again and re-run. Run from the repo root, outside the nightly build window,
with a write token in `HF_TOKEN` (never echo it):

```bash
python3 - <<'EOF'
import csv, sys
sys.path.insert(0, ".")
from huggingface_hub import HfApi, CommitOperationAdd, CommitOperationDelete
from scripts.check_hf_unmanaged_parquet import HF_REPO_ID, MANAGED_PREFIXES, unmanaged_parquet

DRY_RUN = True  # set False only after the owner has approved
api = HfApi()
head = api.dataset_info(HF_REPO_ID).sha
found = unmanaged_parquet(api.list_repo_files(HF_REPO_ID, repo_type="dataset", revision=head))
with open("runbooks/hf_root_parquet_cleanup_2026-10-07.files.tsv", encoding="utf-8") as f:
    documented = sorted(r["path"] for r in csv.DictReader(f, delimiter="\t"))
assert found == documented, sorted(set(found) ^ set(documented))
assert len(found) == 99 and all("/" not in p for p in found)
assert not any(p.startswith(MANAGED_PREFIXES) for p in found)
assert "Removed 99 parquet files" in open("dataset_card.md", encoding="utf-8").read()
ops = [CommitOperationDelete(path_in_repo=p) for p in found]
ops.append(CommitOperationAdd(path_in_repo="README.md", path_or_fileobj="dataset_card.md"))
print(f"head {head[:8]}: delete {len(found)} root parquet files ({found[0]} .. {found[-1]}), update README.md")
if not DRY_RUN:
    info = api.create_commit(
        repo_id=HF_REPO_ID, repo_type="dataset", operations=ops, parent_commit=head,
        commit_message="Remove 99 stale root-level parquet files (manual uploads of March 2026)",
        commit_description="Current files are under data/. See runbooks/hf_root_parquet_cleanup_2026-10-07.md",
    )
    print(info.commit_url)
EOF
python3 scripts/check_hf_unmanaged_parquet.py   # afterwards: exit 0, "0 parquet file(s) outside ..."
```

**Dataset card changelog line.** The card has no changelog section. Add one after "## Update
Frequency", dated with the day of the deletion:

```markdown
## Changelog

- YYYY-MM-DD: Removed 99 parquet files from the repository root, left over from manual uploads on 2026-03-03 and 2026-03-14. They were never part of the `load_dataset` configuration (`data/*.parquet`), but direct downloads of them returned the March 2026 corpus. Current files are under `data/`; the old ones remain available at revision `b4746827`.
```

The line is **not** committed with this change. `publish.py` step 4 uploads `dataset_card.md`
as the Hub's `README.md` every night, so committing it before the deletion would announce a
deletion that has not happened. Sequence on the day:

1. Commit the card line to `main` and deploy it to the VPS.
2. Run the snippet above with `DRY_RUN = False`; it uploads the same card in the same commit.
3. Run the guard and expect exit 0.

If step 2 does not run, revert the card line before the nightly publish.

**Effect and rollback.** Afterwards, a direct download of a root URL returns 404 instead of
March data. That is the intended outcome: a loud failure instead of silently stale data.
External scripts that hard-coded root URLs will break; nothing in this repo does. The files
stay in the repo history and can be fetched with `hf_hub_download(..., revision="b4746827")`,
or re-uploaded if needed. Do not squash the history to reclaim the 6.95 GB.

## Guard

```bash
python3 scripts/check_hf_unmanaged_parquet.py            # list; exit 1 if any, 0 if none, 2 if the listing failed
python3 scripts/check_hf_unmanaged_parquet.py --details  # + size and last commit per file
python3 scripts/check_hf_unmanaged_parquet.py --json
python3 scripts/check_hf_unmanaged_parquet.py --files-from list.txt   # offline
```

On 2026-10-07 it reported exactly the 99 files of the TSV (exit 1). The managed prefixes are
`data/`, `graph/`, `structure/` and `artifacts/`. A new publisher path outside them shows up
as a finding, which fails safe. The offline test pins two things: the prefixes never cover
the root, and the documented deletion list holds root-level parquet files only.

Not wired into anything yet. Two options, both proposal-only under `AGENTS.md`:

- `publish.py` step 4: after the `data/` upload, run `unmanaged_parquet()` on
  `api.list_repo_files(...)` and log a warning. It must not block, because the upload has
  already succeeded.
- The `check_output_freshness` timer (`systemd/**`): run the guard and page through ntfy on
  exit 1.

## Found in passing (not changed here)

**`load_dataset("voilaj/swiss-caselaw")` fails while a daily delta sits under `data/`.**
`publish_delta.py` writes `data/delta-{date}.parquet` with 15 columns. Only 8 of them match
the 41 columns of the court files (it has `id`, `docket`, `content_text`, ...; it lacks
`decision_id`, `full_text`, ...). The card's default config reads `data/*.parquet`, so it
includes the delta.

Reproduced on 2026-10-07 with `datasets` and `data_files=["data/gr_gerichte.parquet",
"data/delta-2026-10-06.parquet"]` at `f7d5c459`: `DatasetGenerationError`, cast error
"column names don't match". The full set was not run (6.9 GB).

Separately, all 138 rows of `delta-2026-10-06` are also in that night's court files. Step 7
runs after step 4 on the same `decisions.db`, so even a schema-matched delta would
double-count the latest day's rows. Step 4 prunes the delta the next night (its
`delete_patterns`), and step 7 adds a new one.

Fixing this changes a public path that `publish_delta.py` says external consumers use
("zero consumer breakage"). That is a decision for the owner; see the follow-up task.

## Note for the twins runbook

When `claude/historical-bge-sg-twins-2026-10-07` is merged, replace its "Proposed (needs
approval ...)" paragraph in "Dataset" with a pointer to this runbook. Its
`delete_files(..., delete_patterns=["*.parquet"])` would empty the dataset; see above.
