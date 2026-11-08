# Weekly entscheidsuche parity audit

Status: script, tests, allow-list, unit and timer written 2026-11-08 on
branch `claude/lucid-joliot-c19e07`. **Not installed on the VPS** (§5 is
Jonas's call).

Policy (memory `no-entscheidsuche-dependency`): entscheidsuche.ch is an
outside list that tells us where to look. It is never a feed and never ground
truth. A residual means: go to the court's own portal and fix our direct
scraper. The audit never writes to the corpus.

Why it exists: from March to November 2026 VD CDAP and BE BVD fell behind
silently after the es feed was switched off. Nobody noticed for eight months.
Background: `docs/claude/entscheidsuche-comparison-2026-11-08.md` and
`docs/claude/entscheidsuche-parity-addendum-2026-11-08.md` (private notes).

## 1. What it compares

| Side | Source | Cost |
|---|---|---|
| theirs, listed | `docs/Sitemaps/*.xml` (~65 files, ~829k unique IDs) | ~70 requests |
| theirs, index | `docs/Snapshots/<today>.json`; for every leaf signature whose index count exceeds the listing, all IDs via `_searchV2.php` (`_source`: id, date, reference; 1000 per page, `search_after`) | ~320 requests |
| theirs, second look | per residual, `docs/<Spider>/<id>.json`: `Num` (dockets) and the PDF/HTML source URL. Cached forever in the cache dir, so each ID costs one request ever | first run ≤ 2,000, then a handful |
| ours | HF mirror `voilaj/swiss-caselaw`, light columns (court, canton, dockets, date, `bge_reference`, URLs) of every court parquet, by HTTP range requests on the exact column chunks, pinned to one commit | ~850 requests, ~4 min |

Every entscheidsuche request goes through one client at 1 request/s with a
generic User-Agent. No text is read on either side.

**Why the mirror and not `decisions.db`.** The build window runs from 03:30
UTC for ~17 h, and a corpus-wide scan of production `decisions.db` inside it
starves the quality gate (CLAUDE.md invariant 9). A docket-index read outside
the window would need a window check, a VPS-only path and a fall-back for a
late build. The mirror needs none of that, runs anywhere, and audits what
users actually receive. Its 1–2 day lag is absorbed by the two-run
confirmation (§3). Limitation: the mirror holds no ECtHR or UN treaty-body
rows (`export_parquet.EXCLUDED_COURTS`, licensing), so `CH_BGE_012` is
allow-listed as not auditable here.

## 2. Matching rules

Dockets are normalised (HTML stripped, upper case, split on separators, leading
`NR`/`BGE`/`CAUSE`/`NO`/`OG` dropped, `NR`/`NRN` dropped anywhere) into three
keys: joined, joined with leading zeros stripped, and with a trailing
four-digit year shortened. Then, in order:

1. **exact** key match within the canton (CH also searches CE).
2. **prefix** match for IDs whose docket was truncated to 20 characters.
3. **date**: same canton and decision date against our still-unclaimed rows,
   only for `DATE_FALLBACK_SPIDERS` (GE, VD FindInfo, EDÖB, WEKO, Bundesrat,
   GL, VB) and for unkeyable dockets (hashes, slugs without a number).
   Elsewhere it hides real gaps: on 11-08 it hid 652 VD CDAP, 22 TA_SST and
   35 BStGer misses.
4. **doc**: the second look, matching `Num`, the normalised source URL, or the
   VD portal uuid.

Per-court quirks: GE case number read off our PDF name
(`ATAS_000573_2022_A_97_2020.pdf` → `A/97/2020`); URL stems as keys for ZH
Baurekurs, Bundesrat, NW, AG Grundbuch/Baugesetzgebung, EDÖB, WEKO; ZH
Baurekurs file counter dropped (`BRGE-I-0165-0167-2012-664`); UR
`2026-OG-V-25-16-Leis` → `OG V 25 16`; TG zero padding; AR `OG ` prefix;
`XX_Upload` takes the canton from the signature.

## 3. Output and alerting

- `logs/parity_audit.json`: per spider `theirs`, `matched`, `matched_by`,
  `allowlisted`, `residual`, `residual_by_year`; the open residuals (id,
  docket, date, their URL, `first_seen`, `notified`); the allow-listed ones
  with their reason; `stale_allowlist`; `inputs` (sitemaps, snapshot day,
  HF revision, request counts). Dated copies in `logs/parity_audit/`.
- Cache: `logs/parity_audit_cache/` (sitemaps, snapshot, enumeration, the
  mirror rows of the current HF revision, `doc_cache.json`).
- **Confirmation:** a residual has to be open in two runs at least six days
  apart. One ntfy message per run, only for residuals newly confirmed, to
  `NTFY_TOPIC` from `/opt/caselaw/ops.env` (unset: nothing is sent). A
  `--no-ntfy` run or a failed post leaves them unannounced, so the next run
  tries again.
- **Baseline:** the first run (no previous report) records every open
  residual as `notified: "baseline"` and sends nothing. The known gaps of
  2026-11-08 are in that baseline.
- Allow-list: `scripts/parity_audit_allowlist.json`. Each entry needs a
  reason and a date. Allow-listed IDs that stop being residual show up under
  `stale_allowlist`; remove them.

## 4. When the alert fires

1. Open `logs/parity_audit.json`, filter `residuals` by
   `notified == <today>`.
2. Per spider, check one or two residuals on the court's own portal.
3. Held by us under another docket form → add a matching rule in
   `scripts/parity_audit.py` with a test, not an allow-list entry.
4. Missing → fix the direct scraper (listing window, filter, portal change).
5. Not fixable and explained → allow-list entry with the reason.

## 5. Install (Jonas)

```bash
cp systemd/opencaselaw-parity-audit.service systemd/opencaselaw-parity-audit.timer /etc/systemd/system/
mkdir -p /etc/systemd/system/opencaselaw-parity-audit.service.d
cp systemd/opencaselaw-parity-audit.service.d/ntfy.conf /etc/systemd/system/opencaselaw-parity-audit.service.d/
systemctl daemon-reload
python3 -c "import pyarrow; print(pyarrow.__version__)"   # system python needs pyarrow
systemctl start opencaselaw-parity-audit.service           # baseline run, sends nothing
systemctl enable --now opencaselaw-parity-audit.timer
```

The first run takes ~45 min (≤ 2,000 document lookups at 1 request/s); later
runs ~10 min. Schedule: Sunday 20:15 UTC (+ up to 15 min), Nice 19, CPU/IO
weight 10, idle I/O class, memory capped at 5 GB (peak 2.7 GB on the first run).

By hand: `python3 scripts/parity_audit.py --no-ntfy`, or `--offline` to
rematch the cached inputs after a rule change.

## 6. Baseline, 2026-11-08 (run from a laptop, HF revision 40299984)

Their side: 65 sitemaps, 829,108 listed IDs, plus 36,224 index-only IDs
from 47 signatures (each enumeration returned exactly the snapshot count).
865,332 entries in total; 862,961 matched (94.9% by exact docket, the rest by prefix,
date and document lookup); 702 allow-listed (162 BGE before 1954 Teil I,
540 CH_BGE_012 ECtHR); **1,669 open**. The first run fetched 2,000 document
JSONs and hit the per-run cap, the second the remaining 1,711. A steady week
costs ~400 entscheidsuche requests.

It reproduces the 11-08 residuals:

| Addendum / report item | Audit |
|---|---|
| BGer 2C_1023/2018, 9C_806/2018, 1B_232/2010, 6B_371/2015, 8C_7/2008 | all 5 open |
| AG Baugesetzgebung 9 (1991–93) | 9 open |
| BGE before 1954, 162 Teil I | 162 allow-listed |
| VD CDAP (in flight) | VD_Omni 890 open (776 of them from 2026); the report's 237 was after a date fallback that hid ~650 |
| SG Departemente 225 (in flight) | 249 |
| BE BVD 133 / StRK 23 / Weitere 9 (in flight) | 148 / 25 / 10 |
| BStGer TPF 2022–25 104 (in flight) | 104 (+7 SK/2025, 1 2026) |
| AI 5 | 5 |
| VD FindInfo "1 uuid to check" | 20 hashed duplicate IDs whose document names no uuid we hold |

New, not in either report (stricter matching, or not compared before):
- BStGer: 35 rulings of Jan–Mar 2005 (BA/BB/BE/BH/BK-2004-*), absent in
  production too (checked through the MCP: BB.2004.36 missing, BB.2004.60
  present).
- BGer: 2C_170/2009 and 8C_243/2012, absent in production; BVGer
  C-5904/2013 (2015-11-27), absent.
- TA_SST: 29 Disziplinarkammer rulings CD/DK 2016–2024 (the report counted
  7; its date fallback hid 22).
- XX_Upload: 83 rulings uploaded to entscheidsuche directly (ZG 46, BE 20,
  …); no court portal behind them.
- ZH OG FE250140 (2026-01-19), index-only.
- CH_UNIBE BGE 42 II 81 and 66 II 99 (Teil II, not in the held-back set).

## 7. Baseline gaps diagnosed, 2026-11-08

### BStGer, 35 rulings of January to early March 2005

Root cause: the state says known, but no shard holds a row. The first 35 lines
of `state/bstger.jsonl` are exactly these rulings (BA.2004.11, BB.2004.36, …,
the first window of the February 2026 backfill, when `START_DATE` was still
2005-01-01). `output/decisions/bstger.jsonl` has no row for any of them.
Checked read-only on the VPS: state 11,212 IDs, shard 11,177, and the
difference is these 35 and nothing else. The scraper skips them as known on
every run. The portal serves all of them, and its paging works (January 2005:
22 rulings over three pages of 10). Today's `run_scraper` writes the row
before it marks the state, so this is a historical leftover. No scraper
change is needed.

Repair (Jonas, VPS). It edits `state/`, so first check that no bstger run is
active:

```bash
pgrep -af "run_scraper.py bstger" || echo "no bstger writer"
```

```bash
cd /opt/caselaw/repo && python3 scripts/state_orphans.py bstger
```

Check that it prints `orphans 35`, then:

```bash
cd /opt/caselaw/repo && python3 scripts/state_orphans.py bstger --apply
```

```bash
cd /opt/caselaw/repo && nice -n 10 python3 run_scraper.py bstger --since 2005-01-01
```

The last command walks the rulingDate windows from 2005 to today (a few hundred
requests, known rulings skipped) and the TPF volumes from 2005. Expect about
35 new rows. Verify with
`grep -c '"docket_number": "BB.2004.36"' output/decisions/bstger.jsonl`
(should print 1); they go live with the next nightly build.

### BVGer C-5904/2013

Root cause: decided 2015-11-27, **published 2026-10-20**. The Weblaw
discovery walks back from today by ruling date and stops after 730 days with
nothing new, so a daily run never reaches 2015. A late-published old ruling
stays invisible. Fix in `scrapers/bvger.py`: daily runs first list everything
published in the last 45 days (`publicationDate` window, the same adaptive
split), whatever the ruling date. Checked live 2026-11-08: 735 published in
45 days, 6 of them decided before 2024; 5 are held, and C-5904/2013 is the one
missing. If the pass fails, the ruling-date scan still runs.

Backfill: none needed beyond the deploy. The first nightly bvger run after the
merge picks it up (it lies within the 45 days until 2026-12-04). By hand:
`python3 run_scraper.py bvger`.

### BGer 2C_170/2009 and 8C_243/2012

The count-reconciliation sweep (branch `claude/bger-count-reconcile`,
`runbooks/bger_reconcile_2026-11.md`) should fetch both. Their windows (with
`--since Y-01-01` and 4-day steps: 2009-05-13..16 and 2012-08-24..27) hold
77 and 50 rulings. entscheidsuche lists 78 and 51 for the same days. Our
held rulings there are a strict subset of their list (no extra held row that
could mask the shortfall), and neither docket is held under another date
(that would be reported as a date conflict instead). So if the AZA count
matches, the window is short by one and gets paged. To verify after those
year runs:
`grep -c -e '2C_170/2009' -e '8C_243/2012' output/decisions/bger.jsonl`
(should print 2). Not covered: 8C_7/2008. entscheidsuche dates it
2008-02-10, a Sunday the AZA does not know, so its true date is unknown and
no window will come up short for it.

## 8. XX_Upload: 83 decisions traced to their source, 2026-11-08

Jonas's decision: "Find the sources". entscheidsuche's `XX_Upload` holds
decisions that people uploaded to it directly, not ones it scraped from a
court portal. Each was traced to its issuing body (the document's header) and
checked against that body's official publication: the court portal or
collection named per group below, by our own mirror of the portal and,
independently, entscheidsuche's own spider for the same portal, plus direct
queries where a portal allows a search by number (ZH Obergericht, ZG GVP).
Nothing was ingested from entscheidsuche.

Result: **no official public copy exists for any of the 83**, so there is no
source to scrape or import. They stay open in the audit, documented here and
not allow-listed. If a court later publishes one, the weekly audit still
lists it and our scraper for that portal should pick it up; if not, the gap
is the court's publication choice. Four other uploads were already held:
ZG A1 2018 11 (GVP), AR O4V-23-10, BGer 6B_650/2025, and BE 2020.GSI.415.
The last one is now matched: the audit cuts appended clerks
("/ kr, stm, pz") off their number.

Two side findings went into the matcher: the same-date fallback for
unkeyable dockets no longer applies to uploads or to canton CH (a SECO upload
without a number had "matched" an unrelated federal row of the same day), and
ZH "SB24" is very likely SB240422, the only SB24* ruling the II. Strafkammer
published that day. We hold it, but the truncated number can't confirm it.

| # | Decision (as listed) | Date | Issued by | Status |
|---|---|---|---|---|
| | **ZG Kantonsgericht, 1. Abteilung** (25): No own portal. Selected rulings appear only in the GVP (decwork.zg.ch, scraper zg_gvp). GVP chronology checked 2026-11-08 (1,361 entries): none of these. A1 2018 11 is in the GVP and held. | | | |
| 1 | A1 2012 55 | 2015-07-22 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 2 | A1 2013 25 | 2015-09-10 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 3 | A1 2014 42 | 2015-12-16 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 4 | A1 2013 7 | 2016-02-10 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 5 | A1 2012 40 | 2016-04-13 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 6 | A1 2014 55 | 2016-06-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 7 | A1 2015 62 | 2016-11-02 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 8 | A1 2015 75 | 2016-11-02 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 9 | A1 2016 20 | 2017-07-05 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 10 | A1 2016 14 | 2017-08-30 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 11 | A1 2014 74 | 2018-08-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 12 | A1 2015 34 | 2019-05-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 13 | A1 2016 38 | 2019-05-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 14 | A1 2017 13 | 2019-05-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 15 | A1 2010 116 | 2019-12-04 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 16 | A1 2015 55 | 2019-12-19 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 17 | A1 2017 49 | 2020-04-29 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 18 | A1 2018 41 | 2020-06-10 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 19 | A1 2019 50 | 2020-11-18 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 20 | A1 2017 32 | 2020-12-16 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 21 | A1 2019 26 | 2021-03-24 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 22 | A1 2017 65 | 2021-03-31 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 23 | A1 2017 24 | 2021-07-28 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 24 | A1 2017 6 | 2021-08-25 | Kantonsgericht 1. Abteilung | open: no official public copy |
| 25 | A1 2020 56 | 2022-06-22 | Kantonsgericht 1. Abteilung | open: no official public copy |
| | **ZG Verwaltungsgericht (Sozialversicherungs-, Fürsorge-, Verwaltungsrechtliche Kammer, Haftrichter)** (20): verwaltungsgericht.zg.ch (Tribuna, scraper zg_verwaltungsgericht) and the GVP. Not on the portal (neither our mirror nor entscheidsuche's own ZG_Verwaltungsgericht spider lists them), not in the GVP. | | | |
| 26 | F 2016 64 | 2016-04-27 | Verwaltungsgericht Fürsorgerechtliche Kammer | open: no official public copy |
| 27 | F 2017 16 | 2017-04-03 | Verwaltungsgericht Fürsorgerechtliche Kammer | open: no official public copy |
| 28 | V 17 40 | 2017-04-07 | Verwaltungsgericht Haftrichter | open: no official public copy |
| 29 | V 2017 34 | 2017-04-10 | Verwaltungsgericht Verwaltungsrechtliche Kammer | open: no official public copy |
| 30 | F 2017 17 | 2017-04-12 | Verwaltungsgericht Fürsorgerechtliche Kammer | open: no official public copy |
| 31 | S 2014 64 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 32 | S 2016 112 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 33 | S 2016 116 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 34 | S 2016 149 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 35 | S 2016 154 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 36 | S 2016 160 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 37 | S 2016 2 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 38 | S 2016 65 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 39 | S 2016 80 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 40 | S 2017 11 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 41 | S 2017 14 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 42 | S 2017 1 | 2017-04-27 | Verwaltungsgericht Sozialversicherungsrechtliche Kammer | open: no official public copy |
| 43 | V 2016 122 | 2017-04-27 | Verwaltungsgericht Verwaltungsrechtliche Kammer | open: no official public copy |
| 44 | V 2017 12 | 2017-04-27 | Verwaltungsgericht Verwaltungsrechtliche Kammer | open: no official public copy |
| 45 | V 17 56 | 2017-05-15 | Verwaltungsgericht Der Haftrichter | open: no official public copy |
| | **ZG Strafgericht** (1): No own portal; selected rulings only in the GVP. Not in the GVP. | | | |
| 46 | SE 2017 401 41 / 42 | 2020-12-30 | Strafgericht des Kantons Zug Einzelrichterin | open: no official public copy |
| | **BE Obergericht and Handelsgericht** (6): zsg-entscheide.apps.be.ch (Tribuna, scraper be_zivilstraf). Not on the portal (our mirror and entscheidsuche's BE_ZivilStraf spider). | | | |
| 47 | HG 11 28 POP | 2011-04-29 | Handelsgericht des Kantons Bern | open: no official public copy |
| 48 | BK 12 140 SCE | 2012-08-17 | Obergericht Beschwerdekammer in Strafsachen | open: no official public copy |
| 49 | GL 13 125 | 2014-07-08 | Obergericht des Kantons Bern Generalsekretariat | open: no official public copy |
| 50 | BK 21 166 MOR | 2021-08-24 | Obergericht Beschwerdekammer in Strafsachen | open: no official public copy |
| 51 | SK 22171 | 2022-06-20 | Obergericht 1. Strafkammer | open: no official public copy |
| 52 | ZK 22 354 | 2023-05-05 | Obergericht 2. Zivilkammer | open: no official public copy |
| | **BE Kantonales Zwangsmassnahmengericht and Regionalgerichte** (9): The same Tribuna portal has filters for the regional courts but publishes almost none (BM 2, WSG 3). Not on the portal. | | | |
| 53 | CIV 16 984 | 2016-04-14 | Regionalgericht Bern-Mittelland Zivilabteilung | open: no official public copy |
| 54 | KZM 18 1669 NUM | 2018-12-17 | Kantonales Zwangsmassnahmengericht | open: no official public copy |
| 55 | KZM 19 409 BÜH | 2019-03-29 | Kantonales Zwangsmassnahmengericht | open: no official public copy |
| 56 | KZM 19 429 P02 | 2019-04-05 | Kantonales Zwangsmassnahmengericht | open: no official public copy |
| 57 | KZM 19 1251 BÜH | 2019-10-28 | Kantonales Zwangsmassnahmengericht | open: no official public copy |
| 58 | KZM 19 1347 NUM | 2019-11-20 | Kantonales Zwangsmassnahmengericht | open: no official public copy |
| 59 | CIV 19 2967 | 2019-12-20 | Regionalgericht Oberland Zivilabteilung | open: no official public copy |
| 60 | CIV 22 926 ALL | 2022-08-02 | Regionalgericht Oberland | open: no official public copy |
| 61 | PEN 23 839 | 2024-08-15 | Regionalgericht Bern-Mittelland Strafabteilung | open: no official public copy |
| | **BE Staatsanwaltschaft (regional offices)** (4): Prosecution orders are not published. No public copy. | | | |
| 62 | BM 18 43675 | 2019-03-28 | Staatsanwaltschaft des Kantons Bern Region Bern-Mittelland | open: no official public copy |
| 63 | BJS 21 2589 | 2021-11-02 | Staatsanwaltschaft des Kantons Bern Region Berner Jura-Seeland | open: no official public copy |
| 64 | O 21 10476 | 2021-11-16 | Staatsanwaltschaft des Kantons Bern Region Oberland | open: no official public copy |
| 65 | O 21 10476 | 2022-05-06 | Staatsanwaltschaft des Kantons Bern Region Oberland | open: no official public copy |
| | **ZH Obergericht** (3): gerichte-zh.ch decision database (scraper zh_gerichte). livesearch by number: LB160039 and SB180452 not found (control LB160040 found). 'SB24' carries a truncated number; the only SB24* ruling of that chamber and day is SB240422, which we hold. | | | |
| 66 | LB160039-O/U.doc | 2016-12-16 | Obergericht des Kantons Zürich I. Zivilkammer | open: no official public copy |
| 67 | SB180452-O/U/ad | 2020-08-03 | Obergericht des Kantons Zürich II. Strafkammer | open: no official public copy |
| 68 | SB24 | 2025-08-15 | Obergericht des Kantons Zürich II. Strafkammer | open: likely held as SB240422 |
| | **ZH Stadtrichteramt Zürich** (1): Administrative penal orders are not published. No public copy. | | | |
| 69 | 2023-066-252 | 2024-06-10 | Stadt Zürich, Stadtrichteramt | open: no official public copy |
| | **Federal: BGer 1993, Eidg. Schätzungskommission, SECO** (4): bger.ch AZA starts in 2000 (our rows: 5,309 in 2000, a handful before); the 1993 Kassationshof ruling is not a BGE. The expropriation commissions and SECO have no public decision collection (VPB ended 2017). No public copy found. | | | |
| 70 | 65.360/1993/tbr | 1993-11-23 | Bundesgericht, Kassationshof | open: no official public copy |
| 71 | (no number) | 2017-01-03 | SECO | open: no official public copy |
| 72 | SEC0-471.4-1/16 | 2021-05-27 | SECO | open: no official public copy |
| 73 | K06-0002/2019 | 2022-11-30 | Eidgenössische Schätzungskommission, Kreis 6 | open: no official public copy |
| | **Other cantonal first instances and authorities** (10): AG, FR, TG, VD, VS publish only higher-court rulings; LU (gerichte.lu.ch) and OW (rechtsprechung.ow.ch, frozen since 2022) publish selections. Not on any of those portals (our mirrors and entscheidsuche's own spiders); TG DJS, SH StA: no publication. | | | |
| 74 | 50 2014 28, 29/mpo | 2014-09-16 | FR Polizeirichter des Sensebezirks | open: no official public copy |
| 75 | ST.2014.2865 | 2016-06-08 | SH Staatsanwaltschaft | open: no official public copy |
| 76 | AB 18/013/SKE | 2019-05-10 | OW Obergericht | open: no official public copy |
| 77 | 2O6 20 203 | 2021-03-24 | LU Kriminalgericht, Abteilung 2 | open: no official public copy |
| 78 | JJ21.022301 | 2021-08-30 | VD Justice de paix de Lausanne | open: no official public copy |
| 79 | ST.2021.113/ho | 2021-09-07 | AG Bezirksgericht Baden | open: no official public copy |
| 80 | DG 61 / 2021; S5.2021.10 | 2021-11-04 | TG Bezirksgericht Münchwilen | open: no official public copy |
| 81 | AE 21/OOI/RHU | 2022-01-10 | OW Obergerichtspräsidium | open: no official public copy |
| 82 | 537/2022 | 2023-03-08 | TG Departement für Justiz und Sicherheit | open: no official public copy |
| 83 | S1 22 47 | 2024-01-25 | VS Bezirksgericht Visp | open: no official public copy |
