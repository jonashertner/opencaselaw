# SAV/FSA sources: retirement and shard quarantine (2026-09-14)

## Why

sav-fsa.ch (Schweizerischer Anwaltsverband / Fédération Suisse des Avocats) is a
republisher, not a court. Its two scrapers produced 37 corpus rows and none of them is a
usable decision:

| rows | court | what the full text actually is |
|---|---|---|
| 33 | `sav_kantone` | the SAV "Cloud Guidelines" PDF (`20221111_cloud+guidelines_F.pdf`, content_hash `6893f293…`), under 33 different titles such as "Entscheid der Anwaltskommission des Kantons Obwalden vom 2. Oktober 2008". The Liferay detail pages' first PDF link is the site-wide guideline, so every row got the same document. |
| 1 | `sav_international` | the same Cloud Guidelines PDF |
| 3 | `sav_international` | an Anwaltsrevue excerpt (`bgfa_ar_0407_s172.pdf`) and two CJEU judgments (C-313/01, C-309/99) hosted by the SAV |

Policy (Jonas, 2026-09-14): only original court / authority data. Retire the sources,
quarantine the rows before the handover.

## Code side (this change set)

- removed from `run_scraper.SCRAPERS`, `branch_map.py`, `proceeding_map.py`,
  `generate_stats._FEDERAL_COURT_EXCLUDE`, `mcp_server.COURT_DISPLAY_NAMES`,
  `quality/checks/dates.KNOWN_NULL_DATE_FLOORS`
- deleted `scrapers/sav_international.py`, `scrapers/sav_kantone.py` and their two
  smoke tests; `tests/test_sav_fsa_retired.py` keeps the codes out of every registry
- untouched: `publish.py` step 2e (BGFA article ↔ BGE mapping fetched from sav-fsa.ch,
  NON_FATAL). It is a mapping aid, not decision content; decide separately.

## VPS steps (data mutations: Jonas runs them, not a session)

Window: after the nightly publish has exited and while no incremental publish is
running (`systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service`
both `inactive`), before 03:30 UTC.

```bash
cd /opt/caselaw/repo && git merge --ff-only origin/main
D=$(readlink -f output/decisions); Q=$(dirname "$D")/quarantine/sav_fsa_2026-09-14; mkdir -p "$Q"
mv "$D"/sav_kantone.jsonl "$D"/sav_international.jsonl "$Q"/
for f in state/sav_kantone.jsonl state/sav_international.jsonl state/sav_kantone.gaps.jsonl state/sav_international.gaps.jsonl; do [ -f "$f" ] && mv "$f" "$Q"/; done
rm -f output/dataset/sav_kantone.parquet output/dataset/sav_international.parquet
ls -la "$Q"
```

HuggingFace mirror (`voilaj/swiss-caselaw`) still carries `data/sav_kantone.parquet` and
`data/sav_international.parquet`; the nightly upload only adds and replaces files, so
delete them once (token from the publish environment):

```bash
python3 - <<'PY'
from huggingface_hub import HfApi
api = HfApi()
for f in ("data/sav_kantone.parquet", "data/sav_international.parquet"):
    api.delete_file(f, repo_id="voilaj/swiss-caselaw", repo_type="dataset",
                    commit_message="retire SAV/FSA sources (republisher, not court data)")
PY
```

Gates: both courts have far fewer than the 500 live rows the per-court swap gate looks
at, and 37 rows out of ~1.05 M do not move the global 95 % gate. No
`OCL_SKIP_SWAP_GATE` needed.

## Verify (after the next full build)

- `list_courts` shows neither `sav_kantone` nor `sav_international`
- `get_decision("sav_kantone_glarus-1")` → not found
- `docs/stats.json` `by_court` has no sav rows; HF `data/` listing has no sav files
