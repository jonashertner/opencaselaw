# SG Departemente (new) and AI Gerichte (fix), 2026-11-08

From `docs/claude/entscheidsuche-comparison-2026-11-08.md`, items #2 and #8.

## What changed

**`sg_departemente` (new, `scrapers/cantonal/sg_departemente.py`).** The rulings of the
St. Gallen departments on publikationen.sg.ch/rechtsprechung-departemente (same TYPO3
`tx_diamjudicalsg` plugin as the courts portal). The portal lists 779 publications
(`data-total`, 2012–2026: BUD 566, SJD 110, VD 50, DI 38, BLD 14, FD 1). Before this,
the only intake was the entscheidsuche SG spiders, stale since April 2024: we held 524
(517 in `es_sg_publikationen`, 7 in `es_sg_gerichte`), and **250 were missing**
(2023: 5, 2024: 95, 2025: 102, 2026: 48), among them 25-6932 (BUD, 2026-03-31).

- Rows: `court=sg_publikationen` (the code the 517 entscheidsuche rows carry), `chamber` =
  department ("Bau- und Umweltdepartement"), docket = Fall-Nr., `decision_date` =
  Entscheiddatum, `publication_date` = Publikationsdatum, `title` = the publication
  title ("BUDE 2026 Nr. 062"), `regeste` = the portal's summary, text from the PDF
  attachment, `source_url` = detail page, `pdf_url` = the attachment.
- Ids: `sg_publikationen_<docket, [^A-Za-z0-9_.-] → _>`, the shape entscheidsuche_ingest
  gave the held rows, so a direct row takes over its copy's id (direct shards load first).
  13 dockets carry two rulings; the earlier keeps the bare id, the later gets
  `_dYYYYMMDD`.
- State and shard: `state/sg_departemente.jsonl`, `output/decisions/sg_departemente.jsonl`.
- The listing's dates are the right ones: 159 of the held rows carry another date
  (entscheidsuche took a date from the text header, e.g. VD/G-13.12: 1996-04-01 there,
  Entscheid of 23.09.2013 on the portal). See "Build effect".

**`ai_gerichte` (fix).** Since ~2026-08-18 the ai.ch listing links carry a cache-buster
(`v-21-2025.pdf?_t=…`), some have no extension (`k-3-2025`) and eight point into a
`cms_trash_container` that answers 401. `href.endswith(".pdf")` matched nothing: +0 new,
exit 0, 82 nights. Discovery now lists the Plone media folder that holds every ruling
(REST API on the CMS backend `ai-api.webcloud7.ch`, 104 files 2015–2026 = 103 rulings)
and takes subject + listing date from the public table. New ids use the entscheidsuche
shape (`ai_gerichte_V_21-2025`); both legacy shapes in state still count as known. The
regeste is the block the court prints above "Erwägungen:" (subject + headnote). The
listing page has no charset header and is decoded as UTF-8.

**Why health did not catch it, and what now does.** Silent-success detection needs
≥ 3 discovery errors; the listing answered 200 and simply yielded no links. STALE is
skipped after a successful night; STALL ignores corpora under 1,000 rows (AI: 90). Both
scrapers now raise at the end of discovery (after yielding what they found) when a
listing parses to nothing (AI: listing or media folder; SG: zero publications or three
failing pages), so the run exits 1 and `scraper_health.json` shows FAILED — the postcom
pattern.

## Dry runs (2026-11-08, live, into scratch dirs)

- `ai_gerichte`, production state copied in (21 ids): **+82 new, 0 errors, 2.8 min**;
  0 undated, 79/82 with a headnote regeste. The five from the report are among them
  (V 21-2025 of 2026-05-19, V 7-2025 of 2026-02-03, K 3-2025 of 2026-03-17, V 15-2025 of
  2026-03-03, V 14-2025 of 2025-11-18 — V 14 is no longer on the listing, only in the
  folder). 61 of the 82 have the same id and date as their `es_ai_gerichte` copy (the
  copy is skipped at import); 21 are new to the corpus (16 from 2017–2019 plus the 5).
- `sg_departemente`, empty state: **+779 new, 779/779 against the portal's `data-total`,
  0 errors, 41.4 min**; every row dated, with summary and PDF text (median 33k chars,
  none under 2,000); 775 from the attachment, 4 from a `fileadmin/user_upload` PDF.
  778/779 decision dates appear verbatim in the ruling's own header ("Entscheid Nr.
  61/2024 vom 15. Juli 2024"). 25-6932 → `sg_publikationen_25-6932`, BUDE 2026 Nr. 018,
  2026-03-31.

## Build effect

Simulated against the production entscheidsuche rows with the build's own functions
(`insert_decision` collision rule, `_federation_twin_verdict`, cross-court dedup):

| entscheidsuche row | n | outcome |
|---|---|---|
| `es_sg_publikationen`, same id + same date | 368 | copy skipped at import |
| same id, other date, text contained | 10 | `_d` twin folded into the direct row |
| **same id, other date, no verdict** | **137** | **would stay as a second row** |
| `19-3453_SG_VB_001` (same docket + date as a direct row) | 1 | canonical-key dedup |
| `RDRM.2019.77` (text is a portal error page; gone from the portal) | 1 | junk |
| `es_sg_gerichte` `*_SG_DEP_00N` (same docket + date) | 7 | cross-court dedup, aliased |

The 137: the es copy is the portal's PDF rendering (metadata block + summary + ruling,
window share 0.76 < 0.8) and its date is the lower-court decision the text quotes
("Beschluss vom 5. März 2018" for an Entscheid of 3.9.2020). Left alone they would be
served twice, once under the wrong date. Hence deploy step 4: once the direct rows are
written, the 517 department rows leave `es_sg_publikationen.jsonl` (quarantined, not
deleted). 516 of their ids are held by direct rows, so links keep resolving; only the
junk RDRM.2019.77 id goes. `es_sg_gerichte` stays as it is.

Net after the first build: `sg_publikationen` +262 (779 direct − 517 copies),
`ai_gerichte` +21 (82 direct − 61 copies skipped by id). Both counts rise, so the
per-court swap gate is not involved.

## Deploy (needs Jonas's OK; nothing below has been run)

Do steps 2–4 outside the build window (after `opencaselaw-publish.service` is inactive,
~17:00 UTC), so step 4 lands before the next 03:30 build.

1. Commit the change set on a branch, fast-forward `main`, push; on the VPS
   `cd /opt/caselaw/repo && git fetch && git merge --ff-only origin/main`.
2. Scrape both by hand (the 01:00 run would do it too, `run_all_scrapers.py` iterates
   `run_scraper.SCRAPERS`; by hand lets step 4 follow the same day):

```bash
cd /opt/caselaw/repo
systemctl is-active opencaselaw-publish.service   # must print "inactive"
python3 run_scraper.py ai_gerichte                 # ~3 min, expect "+82 new"
python3 run_scraper.py sg_departemente             # ~42 min, expect "+779 new, 779/779"
```

3. Check: `wc -l output/decisions/sg_departemente.jsonl` → 779; `logs/` tail shows exit 0.
   If it is short, stop here (step 4 waits for a complete run).
4. Quarantine the entscheidsuche department rows (outside the shard dir: the build
   ingests every `*.jsonl` in it):

```bash
cd /opt/caselaw/repo
D=$(readlink -f output/decisions); Q=$(dirname "$D")/quarantine/es_sg_departemente_2026-11-08; mkdir -p "$Q"
cp -p "$D"/es_sg_publikationen.jsonl "$Q"/es_sg_publikationen.jsonl.bak
python3 - "$D/es_sg_publikationen.jsonl" "$Q/es_sg_departemente_rows.jsonl" <<'PY'
import json, os, sys
src, dropped = sys.argv[1], sys.argv[2]
keep, drop = [], []
for line in open(src, encoding="utf-8"):
    if not line.strip():
        continue
    url = json.loads(line).get("source_url") or ""
    (drop if "publikationen.sg.ch/rechtsprechung-departemente" in url else keep).append(line)
assert len(drop) == 517, len(drop)
open(dropped, "w", encoding="utf-8").writelines(drop)
open(src + ".tmp", "w", encoding="utf-8").writelines(keep); os.replace(src + ".tmp", src)
print(len(keep), "kept", len(drop), "quarantined")
PY
```

   Expected: 11,670 kept, 517 quarantined. Delete the `.bak` once the next build has
   swapped and the verify list below holds.

## Verify (after the first build)

- `logs/scraper_health.json`: `ai_gerichte` success, new_count 82; `sg_departemente`
  success, new_count 779, portal_count 779; both 0 on the night after.
- `get_decision("25-6932")` → St. Gallen BUD ruling of 2026-03-31, chamber
  "Bau- und Umweltdepartement".
- `search_decisions(court="ai_gerichte", sort="date_desc")` → newest V 21-2025
  (2026-05-19); `get_decision("V 14-2025")` resolves.
- `search_decisions(court="sg_publikationen", query="Bau- und Umweltdepartement", sort="date_desc")`
  → 2026 rulings (BUDE 2026 Nr. 062 of 2026-09-08 at the top).

## Not done here

- `es_ai_gerichte`: its AI_Aktuell rows (73) now all have a direct row; its 31 AI_Bericht
  rows (annual compilations 1995–2024) do not, so the shard cannot be retired yet.
- `mcp_server.py` labels `sg_publikationen` "SG Gerichte", which is how the 517
  department rows are already shown; a department-aware label is a serving change.
