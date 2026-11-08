# Runbook: practice Tier 2 sources + TPF ↔ ruling link (2026-11-08)

Follows `docs/claude/proposal-property-criminal-sources-2026-11-08.md` (Tier 2) and memory
`criminal-property-practice-sources`. Branch `claude/beautiful-williamson-b9d638`, not pushed. Everything below runs
on the VPS as root unless marked LOCAL. No decisions.db, publish.py, build_fts5.py or schema change: the practice
part lands in practice.db only, the TPF link is read-only serving code.

## What changed

| Source | Change | Dry-run rows (live, 2 s/request, generic UA, 2026-11-08) |
|---|---|---:|
| `strafvollzug` | NWI/OSK Erlasssammlung from erlasse.konkordate.ch: Konkordatsvereinbarungen, Reglemente, Tarife, the 26 Richtlinien, 24 Merkblätter, 4 Standards. One row per Erlass version (`NWI-OSK Erlass IV.16`) | 72 of 74 (2 Erlasse have no document); 0 failed |
| `bj_rechtshilfe` | rhf.admin.ch page in DE/FR/IT (the FR/IT twins were missed in Tier 1): Rundschreiben 1-7, Infomails, checklists, Strafübernahme, Überstellung, asset recovery, BJ/fedpol Aufgabenabgrenzung, Musterprotokoll; FR/IT editions of both Wegleitungen | 78 (75 new; 3 ids already held); 0 failed |
| `strafverfolgung` | BE Generalstaatsanwaltschaft (DE/FR), VD, FR (FR/DE), GE Ministère public; Bundesanwaltschaft Tätigkeitsberichte 2006-; AB-BA Tätigkeitsberichte 2011- and inspection reports | 313: BE 52, FR 58, GE 49, VD 29, BA 60, AB-BA 65; 0 failed (BE at its 10 s crawl delay) |
| `grundbuch_kantone` | GE Office du registre foncier directives and guides; VS Service du registre foncier notes to notaries (HTML); AR Kreisschreiben; GR Richtlinie AVGBS; LU Weisungen page + Dienstbarkeitspläne PDF | 36: GE 11, VS 19, AR 3, GR 1, LU 2; 0 failed (one VS article has only an attached PDF and is skipped, see below) |
| serving (`mcp_server.py`) | bstger ruling ↔ TPF entry: `published_as_tpf` (+ note) on the ruling, `full_decision` on the TPF entry, both in `get_decision` and `cite()` | n/a |

## Findings recorded, not built

- **vs.ch robots.txt disallows `/documents/` and `/*.pdf`.** The Tier 1 VS Ministère public part of
  `strafverfolgung` (already on main, 2e1d2111) downloads its directives from `/documents/515860/...pdf`, i.e.
  against robots.txt. Not changed here; decision for Jonas: drop the VS part (and its rows), or ask the VS
  Ministère public for permission. The new VS land-register part reads only the HTML news articles
  (`/web/communication/detail`, allowed) and skips an article whose content is only its attached PDF.
- **BL**: every baselland.ch request returns a Cloudflare challenge; not crawled (no bypass).
- **SG** Grundbuchaufsicht: its Kreisschreiben and Weisungen go to the Grundbuchämter via the cantonal
  intranet, not published. TG, SO, ZG, FR, VD, NE, SZ: no public land-register guidance found.
  TI, SH, AI, GL, NW, OW, UR, JU: not reached in the survey time-box.
- **Prosecutors** not published online: TI, SG, LU, BS, BL, TG, GR, JU; ZG, SZ and NE name Weisungen in
  their law but no public collection was found. The BA publishes no Weisungen.
- **Notariatsaufsicht disciplinary decisions**: no canton publishes a listing on its own site. BE decisions
  exist only on entscheidsuche (`BE_NAB_003_...`), which is not an admissible source; the FR
  Notariatskommission page was not checked.
- **BGBB practice**: no cantonal or BLW Weisungen/Merkblatt collection found (only the statute and BLW
  consultations).
- **erlasse.konkordate.ch "Änderungssammlung"** (`lexas-<year>` in the sitemap, the amending acts by year):
  not taken; the current Erlasse are, one row per in-force version.
- **LU** "Grundpfandrechte" and "Dienstbarkeiten" pages are public explanations of the law, not practice:
  skipped. The LGVE PDFs linked from the Weisungen page are court rulings (decision corpus).

## Deploy (each step needs Jonas's OK)

Windows as in `runbooks/practice_tier1_deploy.md`: never inside the 03:30 full build or the Mon–Sat 20:00
incremental (`systemctl is-active opencaselaw-publish.service opencaselaw-publish-incremental.service` both inactive).

1. LOCAL — land the branch on main (rebase over the bot's commits; the coordinating session pushes):
   ```bash
   git fetch origin && git rebase origin/main && make test && make verify-offline
   ```
2. Merge on the VPS:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && git status --porcelain | head && git fetch origin && git merge --ff-only origin/main && git log --oneline -1'
   ```
3. Back up practice.db, then run the three changed sources (first run ≈ 25 min: the BE host asks for a 10 s
   crawl delay, ~55 requests; later runs fetch only the listing pages):
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'V=/mnt/HC_Volume_104655575/output && cp -p $V/practice.db $V/practice.db.prev-$(date -u +%Y%m%d) && cd /opt/caselaw/repo && PYTHONPATH=. python3 -m scrapers.practice.runner --only strafvollzug,bj_rechtshilfe,strafverfolgung,grundbuch_kantone 2>&1 | tail -8'
   ```
   Expected (± revisions since 11-08): strafvollzug +72, bj_rechtshilfe +75, strafverfolgung +313,
   grundbuch_kantone +36 (~496 rows); 0 failed. The VPS has tesseract deu/fra/ita, so the scans that got a title
   placeholder on the dev machine get OCR text there: BJ Rundschreiben Nr. 5 (DE), BE Richtlinien Hooliganismus
   13.12 (DE/FR), BA Tätigkeitsbericht 2006 (DE/FR/IT), the three AR Kreisschreiben, the LU Dienstbarkeitspläne. Stop if any source failed or crashed;
   `logs/practice_health.json` holds the summary.
   If strafvollzug logs "the /ajax answer holds no tree": erlasse.konkordate.ch changed its wire format, the
   other strafvollzug parts are unaffected; re-capture the request (see the module comment) before retrying.
4. Rebuild practice.db and check the counts:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && PYTHONPATH=. python3 -m search_stack.build_practice_db --jsonl-dir output/practice --db output/practice.db | tail -30'
   ```
5. Rolling restart (health-gated; also activates the TPF link code):
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh'
   ```
6. Smoke via the MCP client:
   - `search_practice("Urlaub Ausgang", source="strafvollzug", issuing_authority="NWI-OSK")` → `NWI-OSK Erlass IV.16`.
   - `search_practice("Videokonferenz", source="bj_rechtshilfe", language="fr")` → Rundschreiben Nr. 3 (FR).
   - `search_practice("Strafbefehl", canton="BE")`, `canton="VD"`, `canton="GE"`, `canton="FR"` → each the prosecutor's directives.
   - `search_practice("Inspektion", issuing_authority="AB-BA")` → AB-BA inspection reports.

## TPF ↔ ruling link: when it shows

The code is inert until the TPF rows carry their underlying docket, i.e. after the BStGer TPF backfill
(`OCL_BSTGER_TPF_ONLY=1 python3 run_scraper.py bstger --since 2004-01-01`, 687 rows), the es retirement
(`scripts/bstger_retire_es_tpf.py --apply`) and the next nightly swap (memory `bstger-tpf-series`). Today's
production table holds 583 TPF rows, all with `docket_number_2` NULL, so nothing is linked yet and nothing
can be wrong. After the swap:
```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && python3 - <<EOF
import sqlite3
c = sqlite3.connect("file:output/decisions.db?mode=ro&immutable=1", uri=True)
rows = c.execute("SELECT count(*), count(docket_number_2) FROM decisions INDEXED BY idx_decisions_docket WHERE docket_number >= \"TPF \" AND docket_number < \"TPF!\"").fetchone()
print(rows)   # expect (687, 687)
EOF'
```
Then `get_decision("bstger_BG.2024.60")` → `published_as_tpf` = TPF 2024 194; `cite("TPF 2024 194")` →
`full_decision` = BStGer BG.2024.60.

### Query plans (production decisions.db, read-only `mode=ro&immutable=1`, 2026-11-08)

```
SELECT decision_id, court, docket_number, docket_number_2, decision_date FROM decisions
  INDEXED BY idx_decisions_docket WHERE docket_number >= 'TPF ' AND docket_number < 'TPF!'
  → SEARCH decisions USING INDEX idx_decisions_docket (docket_number>? AND docket_number<?)
  → 583 rows, 0.015 s (once per decisions.db file per worker; re-read when inode/mtime/size change)

SELECT decision_id, court, docket_number, decision_date FROM decisions
  INDEXED BY idx_decisions_docket WHERE docket_number IN (?,?)
  → SEARCH decisions USING INDEX idx_decisions_docket (docket_number=?)
  → BG.2024.60: 1 row, < 1 ms
```
No `court =` term in either query (the 2026-10-16 incident); court is checked in Python.
`tests/test_tpf_reverse_link.py` EXPLAINs every SELECT the link issues and fails on `idx_decisions_court` or a
table scan.

Matching rules: ruling → entry needs the entry to name the ruling's docket (or a joined docket) and to carry the
ruling's date; for an entry naming several joined rulings the docket alone decides, since its heading prints only
the first date (TPF 2005 123). Entry → ruling: the bstger row under the first named docket with the entry's date,
else (joined entries only) the single ruling held under the first docket. An interim order under the same docket
is never linked.

## Rollback

`git revert` the commits, merge on the VPS, rolling restart; copy `practice.db.prev-<date>` back over practice.db.
The new JSONL lines are harmless to the old code.
