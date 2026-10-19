# ta_sst: direct sportstribunal.ch scraper (2026-09-14)

## What changed

`scrapers/ta_sst.py` no longer reads entscheidsuche stubs (`es_ta_sst.jsonl`, frozen at
49 rows / 2025-10-03 since March). It parses https://www.sportstribunal.ch/rechtsprechung
directly: 112 PDFs on one page, two `<h3>` sections (Schweizer Sportgericht 2024–;
Disziplinarkammer des Schweizer Sports von Swiss Olympic 2016–2024, kept as
`chamber`). Decision ids are unchanged (`ta_sst_SSG 2025_E_60`), so the 49 existing rows
stay valid and are skipped via state. Server side: display name "Schweizer Sportgericht",
citation form `SSG 2025/E/60 vom 3. Oktober 2025` (was "Gericht CH …").

Live probe 2026-09-14 with the 49 corpus ids pre-seeded: 65 new (17 SSG/TSS 2025-10 →
2026-07-06, 48 Disziplinarkammer 2016–2024), 0 undated; DK 2016/DO/9 has no text layer
(scan) and is cached as a 7-day gap. Expect the first nightly run to take ~4 min.

## Deploy

1. push the change set; on the VPS `git merge --ff-only origin/main`
2. the 01:00 UTC scrape picks it up (`run_all_scrapers.py` iterates `run_scraper.SCRAPERS`);
   the 03:30 build folds the rows in (`ta_sst` < 500 rows: outside the per-court gate)
3. quarantine the entscheidsuche stub shard so it can never be indexed again:

```bash
cd /opt/caselaw/repo
D=$(readlink -f output/decisions); Q=$(dirname "$D")/quarantine/es_ta_sst_2026-09-14; mkdir -p "$Q"
mv "$D"/es_ta_sst.jsonl "$Q"/
```

4. drop the two phantom rows entscheidsuche mis-keyed (same PDF and content_hash as
   their 2024 twins): `ta_sst_SSG 2025_E_15` (= SSG 2024/E/15) and `ta_sst_SSG 2025_E_45`
   (= SSG 2024/E/45). Neither docket exists on sportstribunal.ch.

```bash
cd /opt/caselaw/repo
D=$(readlink -f output/decisions)
python3 - "$D/ta_sst.jsonl" <<'PY'
import json, os, sys
p = sys.argv[1]; drop = {"ta_sst_SSG 2025_E_15", "ta_sst_SSG 2025_E_45"}
rows = [l for l in open(p, encoding="utf-8") if l.strip() and json.loads(l)["decision_id"] not in drop]
open(p + ".tmp", "w", encoding="utf-8").writelines(rows); os.replace(p + ".tmp", p)
print(len(rows), "rows kept")
PY
grep -v -x -e 'ta_sst_SSG 2025_E_15' -e 'ta_sst_SSG 2025_E_45' state/ta_sst.jsonl > state/ta_sst.jsonl.tmp && mv state/ta_sst.jsonl.tmp state/ta_sst.jsonl
```

## Verify

- `search_decisions(court="ta_sst", sort="date_desc")`: newest `SSG 2026/E/71` (2026-07-06),
  total ≈ 112 (49 − 2 phantoms + 65)
- `get_decision("SSG 2026/E/71")` → `citation_string_de` = `SSG 2026/E/71 vom 6. Juli 2026`
- `list_courts`: ta_sst earliest 2016-03-03
- `logs/scraper_health.json` ta_sst: success, new_count 65 on the first run, then 0
