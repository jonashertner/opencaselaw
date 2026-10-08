# Historical BGE: DFR documents that hold another reference's ruling (2026-10-07)

Follow-up to `runbooks/historical_bge_and_sg_twins_2026-10-07.md`, section "Validation",
"Source errors found in passing" (branch `claude/historical-bge-sg-twins-2026-10-07`, not on
`main` when this was written). That run found six BGE 52 I rows whose DFR PDFs hold BGE 65 I
rulings, and suspected 1 I 13 and 16 I 838. This runbook checks all 14,578 rows of volumes
1-79 for the same kind of error, verifies every finding on the DFR scan image, and proposes
how to handle them.

- Verified list, one row per finding: `historical_bge_source_errors_2026-10-07.tsv`
- Read-only check: `scripts/audit_bge_historical_sources.py` (offline tests:
  `tests/test_audit_bge_historical_sources.py`)

## Result

Nine rows serve another reference's ruling. Every one was read on the scan image (header
crop, page numbers and running heads), not only in the text layer.

| class | rows | the DFR document holds | copy of that ruling in the corpus | own ruling of the reference |
|---|---|---|---|---|
| A: scan of another volume | 52 I 1, 8, 23, 39, 149, 230 | BGE 65 I at the same page numbers (1939) | `bge_65_I_*`; five texts identical, 52 I 1 overlaps 65 I 1/4 | not on DFR (HTML 404; the index links only these PDFs) |
| B: pages of another volume appended | 71 II 223 | its own pp. 223-225 (No 49), then 77 II 154-161 | `bge_77_II_154` | complete on the first two spreads and in the servat HTML |
| C: another page range of the same volume | 39 I 469 | pp. 482/483, No 87 (= 39 I 483) | `bge_39_I_483`, identical | No 83 expected; not on DFR |
| D: the HTML behind the index link is another reference | 22 I 12 | BGE 22 I 1012 (servat HTML titled so) | none: pages from 1000 on are never scraped (below) | on DFR: `https://www.fallrecht.ch/c1022012.pdf` |

The six 52 I PDFs render to the same page images as the 65 I PDFs (every page compared). Their
file sizes are equal or 6 bytes apart, and only the PDF title differs ("DFR - BGE 52 I 8"
against "DFR - BGE 65 I 8"). The 52 I neighbours (14, 27,
34, 44, 145, 154, 227, 238) are genuine 1926 rulings, so the serial numbers of the missing
rulings follow from them: 52 I 1 = No 1, 8 = No 2, 23 = No 4, 39 = No 7, 149 = No 22,
230 = No 32 (inferred, not read on a scan). On 71 II 223, spread 3 of the PDF carries a
handwritten "BGE 77 II 154".

**What is served today** (API `https://mcp.opencaselaw.ch/api/decisions/<id>`, 2026-10-07):

- `bge_52_I_8` serves the 65 I 8 text (Neef, 1939), dated 1926-01-01 (estimated).
- `bge_39_I_469` serves No 87, dated 1913-09-24, which is No 87's date.
- `bge_71_II_223`: of 21,582 characters, everything from offset 5,754 is 77 II 154.
- `bge_22_I_12` serves 22 I 1012, dated 1896-06-29. That date appears inside the 1012 text (`extracted_from_text`); it is not a ruling date.

**Citations affected.** The reference graph (`find_citations`, incoming) resolves 35 citation edges from other rulings to these rows. Running-head artefacts from the rows' own neighbours are not counted.

| row | edges | examples |
|---|---|---|
| 52 I 8 | 3 | — |
| 52 I 23 | 4 | — |
| 52 I 149 | 10 | BGer 1P.152/2002 and 1P.559/2000 |
| 52 I 230 | 1 | — |
| 39 I 469 | 6 | BGE 88 III 59 |
| 71 II 223 | 11 | BGer 4C.303/2001, BGE 114 II 250. Its own ruling is right; the text is polluted. |

A reader who follows BGE 52 I 149 from 1P.152/2002 lands on a 1939 ruling.

**The segmentation branch does not fix them.** Run on the current text:

- The 52 I rows are placed on the 65 I header. The volume gate gives them the placeholder date, but the 65 I text stays.
- 39 I 469 is placed on No 87 and keeps No 87's date, because 1913 passes the volume window.
- 71 II 223 is not cut. Its own serial is 49, and the appended No 32 has a lower serial, so the cut never triggers.
- 22 I 12 cannot be placed.

## Corrections to the parent runbook

1. "BGE 1 I 13 ... 16 I 838 ... look like further DFR links to the next volume's page": no.
   Both scans are pages of their own volume. 1 I 13 sits under the running head "III.
   Doppelbesteuerung. No 3. u. 4." on p. 13. 16 I 838 is followed by the sheet signature
   "XVI — 1890". The later year is printed in the original. The same holds for 44 II 30 ("24
   janvier 1919" in volume 44), 52 I 301 ("12 novembre 1927" in volume 52, No 42 dated 1926)
   and 61 I 194 (the scan reads "1925" in volume 61; facts of September 1934). The gate's
   placeholder is the right answer for these rows; the scan cannot give their true year.
2. "Volume 4 I 370/371, 46 II 76/77 and 62 II 193/194 hold the same text twice": they share
   their opening spread (2,981/10,739, 2,616/18,858 and 5,246/15,616 characters), as two
   rulings that start on facing pages do. Not source errors.
3. The identical pairs within one volume are 20 I 384/385 and 62 II 48/49: one PDF for two
   rulings that start on facing pages. The PDFs differ only in their title. Not source errors.
   But both 20 I rows carry No 69's date (25 May 1894), while 20 I 385 is No 70 ("70. Arrêt
   du 15 Juin 1894 dans la cause Eisele contre masse Porchat" on the scan). The segmenter does
   not recognise the OCR header "69. Artet dn 25 Mai 1894 dans ia canse".

## Method

Data:

- Current: `data/bge.parquet` of `voilaj/swiss-caselaw` (last commit `72a3f4a6`, 2026-09-28), the 14,578 rows of volumes 1-79.
- For comparison: the March root export `bge_historical.parquet` (`b4746827`).
- The segmenter of `claude/historical-bge-sg-twins-2026-10-07` (`a7e23576`).

| # | check | candidates | outcome |
|---|---|---|---|
| 1 | own header (10,926 placed) or first confirmed header (11,599 with a year) names a year outside [volume year - 1, volume year] | 528 | twin lookup in volume (year - 1874 / - 1873), same part, pages ±3, 3-word shingle containment ≥ 0.3: only the six 52 I rows. Rest: 218 years no volume 1-79 can hold ("1999", "1810": OCR), 300 one digit off the window, 4 two digits off with a plausible year (37 II 480 read a statute of 1893 because its own date "13 ootobre 1911" is unparsed; 51 I 423, 54 III 313, 61 I 194 checked on the scan) |
| 2 | identical text (whitespace-normalised) | 8 pairs | 5 × 52 I / 65 I, 39 I 469 / 483, 20 I 384 / 385, 62 II 48 / 49 |
| 3 | near-identical text (MinHash, 6-word shingles, containment ≥ 0.5) | 238 pairs | 190 facing or adjacent pages; the rest a long ruling's range holding a later short ruling of the same volume; across volumes only 52 I / 65 I and 71 II 223 ⊃ 77 II 154 (containment 0.81) |
| 4 | serial sequence: longest increasing run of own serials per volume part, by page | 49 outliers | 5 class A, 1 class C (No 87 between No 82 and No 84), 6 sharing a serial with a row ≤ 6 pages away, 37 OCR'd serials or numbering of the print; 4 of these on the scan: 2 I 23 OCR ("13." for "6."), 25 I 34, 36 I 160, 47 II 99 printed so on their own pages |
| 5 | printed page numbers at the start of a PDF text miss the reference page | 31 | 26 one OCR digit off (66/67 for 56/57), 2 volume registers on page 1 (12 I 1, 14 I 1), 3 lists of years (34 II 586, 35 II 525, 37 I 565) |
| 6 | after the reference page, the page numbers restart far below it and run on | 1 | 71 II 223 (224, then 154, 155, 156, 157, 159, 161) |
| 7 | DFR HTML title names another reference | 1 of 346 HTML rows | 22 I 12 |

**Scans read** (header crops at 90-250 dpi):

- **A:** the six 52 I PDFs, and their 65 I counterparts compared by size and title.
- **B:** 71 II 223.
- **C:** 39 I 469 and 483.
- **D:** 22 I 12 (PDF), and 22 I 1012 (`c1022A12.pdf`).
- **Same spread:** 20 I 384/385, 62 II 48/49.
- **Printed year:** 1 I 13, 16 I 838, 44 II 24/30, 52 I 301, 61 I 194.
- **OCR, chosen candidates:** 46 I 48, 51 I 423, 54 III 313, 58 III 62, 71 II 68.
- **OCR, random sample of the one-digit group** (one row each for offsets +1, +2, +5, +7, -3, -5, -6, -10, -20): 38 II 366, 49 II 230, 51 I 137, 70 II 162, 71 III 10, 72 II 79, 73 II 102, 78 IV 134. The ninth sample row was 44 II 24, a printed year, listed above.
- **Serial outliers:** 2 I 23, 25 I 34, 36 I 160, 47 II 99.

**March text.** Same findings, except 71 II 223. In March it held the servat HTML (4,117
characters, the complete No 49). The PDF text with 77 II 154 replaced it later, while
`source_url` and `scraped_at` (2026-03-03) stayed the same.

**How the swap most likely happened.** `build_fts5.insert_decision` processes direct shards
first and `es_*` shards after them. On a collision with the same canonical key it keeps the
stored row's metadata but swaps in the incoming `full_text` when that text is more than
twice as long and at least 1,000 characters longer: the "text-upgrade" step, added for the
truncated Ticino texts. 71 II 223 qualifies (4,117 → 21,582) if any `es_*` shard carries
the PDF text under the same id. Entscheidsuche has a DFR spider (`CH_UNIBE`,
`scripts/source_coverage_audit.py`).

Check on the VPS (read-only):

```bash
grep -l '71_II_223' output/decisions/es_*.jsonl
journalctl -u <build unit> | grep 'text-upgrade: .*71_II_223'
```

If confirmed, the text-upgrade can bring any defective entscheidsuche copy into a historical
BGE row, without changing its metadata. The shard audit cannot see that, so also run
`scripts/audit_bge_historical_sources.py` on the built rows (an export of `decisions.db`, or
the published `data/bge.parquet` volume 1-79 rows as JSONL), not only on
`output/decisions/bge_historical.jsonl`. Retiring that es feed for `bge_historical`, or
excluding volumes 1-79 from the text-upgrade, is a `build_fts5` change: proposal only.

**Residual risk.** A scan from another volume can still slip through if four things hold at once:

- its year is one digit off the volume's;
- its true row is missing from the corpus;
- its header cannot be placed;
- its page numbers match.

The random sample of the one-digit group found no such row. The audit's text checks only fire when the other copy is in the corpus.

## The audit script

`scripts/audit_bge_historical_sources.py` is read-only, offline, does not use the segmenter,
and runs in about 45 s for 14,578 rows. It reports:

- `same_text`, classified as `same_spread`, `other_pages` or `other_volume`;
- `shared_text` across volumes (sampled 8-word shingles);
- `page_jump` (check 6);
- `html_label` (check 7).

On both published texts it reports exactly the verified rows. It adds the two same-spread pairs and three `shared_text` pairs that follow from class A (52 I 1 / 65 I 4, 52 I 39 / 65 I 47, 52 I 149 / 65 I 147), and nothing else. Run it on the server shard (the ids there are `bge_historical_*`):

```bash
python3 scripts/audit_bge_historical_sources.py output/decisions/bge_historical.jsonl \
  --out /tmp/bge_sources_$(date +%F).tsv
```

Expected:

- the nine rows above (71 II 223 only if the shard holds the PDF text);
- `same_spread` for 20 I 384/385 and 62 II 48/49;
- the three class-A `shared_text` pairs.

Any other finding needs a scan check before anything is changed. Proposed for the maintenance loop: run it after every `bge_historical` re-scrape.

## Side finding: DFR pages from 1000 on are never scraped

The DFR index codes pages ≥ 1000 with a letter for the hundreds (A = 10 ... J = 19):
`c1021C39.pdf` is BGE 21 I 1239 and `c1022A12.pdf` is 22 I 1012.
`scrapers/bge_historical.DECISION_CODE_RE` (`c([1-5])(\d{3})(\d{3,})`) takes digits only, so 230
index entries are skipped: 20 I (21), 21 I (32), 22 I (47), 23 I (128) and 25 II (2). No
published row has a page ≥ 1000. `c1022012.html` (class D) is the same scheme miscoded on the
DFR side. DFR has no part II before volume 24 (the corpus agrees: volumes 1-23 hold only
part I, paginated through the whole volume), so 23 I 1955 is page 1955 of volume 23, in the
same form as the existing rows. Four decoded references checked on the scans:

- `c1020A00` = pp. 1000/1001, "C. Civilrechtspflege", No 151 of 1894;
- `c1021C35` = pp. 1234/1235, No 159 of 1895;
- `c1022D55` opens on p. 1354 (running head "B. Civilrechtspflege"), a ruling of 1896;
- `c1023H01` = pp. 1700-1702, a ruling of 1897.

Above page 999 these volumes hold their civil-law part (running heads "B." / "C. Civilrechtspflege").

### Scraper change (PR after jonashertner/opencaselaw#131; not run in production)

`scrapers/bge_historical.py`: `DECISION_CODE_RE` takes `[A-J]\d{2}` as a page code, and
`decode_page()` turns `C39` into 1239. Nothing else changes: ids, dockets and titles keep the
`V_P_PAGE` / "BGE V P PAGE" form. Offline tests: `tests/test_bge_historical_letter_pages.py`.

Merging it is the rollout: `bge_historical` runs every night in `run_all_scrapers.py`
(`opencaselaw-scrape.timer`, 01:00 UTC; not in `SKIP_BY_DEFAULT`), so the first night after
the server pulls `main` fetches the 230 pages and the next full build publishes them. Merge
only once that is wanted; the staging run below can go first. Re-checked on `main` with the
merged segmenter (2026-10-08): 20 I 1000, 21 I 1235, 22 I 1355 and 23 I 1319 come out cut to
their own ruling and dated by their header (1894-11-10, 1895-12-30, 1896-10-03, 1897-11-10);
23 I 1701 keeps the volume placeholder (Fraktur caveat below).

**Measured offline** (no production state touched):

- **Discovery** over the eight DFR index pages saved on 2026-10-07, with every published id
  counted as known: 230 new stubs, none colliding with a published id. By volume: 23 I 128,
  22 I 47, 21 I 32, 20 I 21, 25 II 2. Pages run from 1000 to 1981. The other 161 new stubs
  the dry run lists are the known scan-only rulings, which production skips through its gap
  cache, so the change adds exactly the 230.
- **Fetch** (`fetch_decision`) on the five downloaded PDFs (20 I 1000, 21 I 1235, 22 I 1355,
  23 I 1319, 23 I 1701): each row is built with its four-digit page (`bge_historical_21_I_1235`,
  "BGE 21 I 1235").
- **With the segmentation branch:** the patch applies cleanly on
  `claude/historical-bge-sg-twins-2026-10-07`, and the tests pass together (37). Four of the
  five rows then get their own header date (1894-11-10, 1895-12-30, 1896-10-03, 1897-11-10).
- **Fraktur caveat:** 23 I 1701 has a Fraktur text layer read as Antiqua ("ba~er erft mit tlem
  ..."), so it keeps the volume placeholder and is detected as French. Existing rows of these
  volumes have the same fault; this adds no new kind of problem.

**Rollout proposal** (after review; outside the build window):

1. Merge the segmentation branch first, so the new rows are cut and dated by their own header.
2. Staging run, with production state and coverage left untouched:

   ```bash
   mkdir -p /tmp/bgeh-stage/state /tmp/bgeh-stage/out
   cp state/bge_historical.jsonl /tmp/bgeh-stage/state/
   SWISS_CASELAW_COVERAGE_DB=/tmp/bgeh-stage/coverage.db \
     python3 run_scraper.py bge_historical --state /tmp/bgeh-stage/state \
       --output /tmp/bgeh-stage/out --max 10
   python3 scripts/audit_bge_historical_sources.py /tmp/bgeh-stage/out/decisions/bge_historical.jsonl
   ```

   Expected: 10 rows, all with pages >= 1000. Read about five on the scans. The audit should
   report nothing new for them.
3. Production run: `python3 run_scraper.py bge_historical`. That is about 230 PDF downloads
   at the scraper's 1.5 s delay, roughly 15-30 min and well inside the 7,200 s budget. Then a
   full build; `quick_publish` does not pick up the new rows.
4. Then re-run the audit, on the shard and on the built rows. 22 I 1012 now gets its own row
   from `c1022A12.pdf`. The `source_defects` entry for 22 I 12 stays until 22 I 12 is
   re-fetched from `c1022012.pdf`.

Rollback: the run only adds rows with new ids. Remove the 230 `bge_historical_*` rows whose
page is >= 1000 from the shard and from `state/bge_historical.jsonl`; existing rows are never
touched.

## Coordination check (2026-10-07, 22:40 UTC)

**Other sessions on this repository.** None of them works on the same files except the
segmentation branch.

| session | branch | state | overlap with this branch |
|---|---|---|---|
| Historical BGE/SG twins (original + validation) | `claude/historical-bge-sg-twins-2026-10-07` = `claude/bge-sg-twins-validation-ios81l` (`a7e23576`) | idle, waiting for review and the server go-ahead | `mcp_server.py`, `pyproject.toml`, `scrapers/bge_historical.py` |
| Fix stale structure.parquet export | `claude/funny-hawking-7xp27x` | idle, waiting for design decisions | none (own proposal, script, test) |
| Remove stale root-level parquet files on HF | `claude/eloquent-tesla-333wvz` | idle, waiting for approval | none (own runbook, script, test) |

**Combining with the segmentation branch.**

- A dry-run merge (`git merge-tree`) of this branch with `a7e23576` is clean.
- On the merged tree plus `...segment-page-jump.patch`, `make test` passes (4,437 passed,
  26 skipped).
- Suggested order: merge the segmentation branch with the patch, then this branch. Either
  order merges cleanly.

**CI.** `.github/workflows/ci.yml` runs only on pushes to `main` and on pull requests, so
neither branch has a GitHub CI run yet. A pull request would give one. The last 20 runs on
`main` are green.

**Production, read-only:**

- `/health`: ok, 1,078,321 decisions, `db_generation` 2026-10-07 17:15 UTC.
- Today's full publish (timer 03:30 UTC) swapped about 18:00 and finalized 18:31 UTC
  (commits `379b29ea`, `b0e3857e`). The incremental publish timer runs Mon-Sat 20:00 UTC.
- Shard repairs and the letter-coded scraper run go after the full publish has finished
  and must be done before the nightly scrape starts at 01:00 UTC (`opencaselaw-scrape.timer`,
  `run_all_scrapers.py`, which appends to the shards and runs for hours). Check
  `systemctl is-active opencaselaw-publish.service opencaselaw-scrape.service` (both
  inactive), not only the absence of `/tmp/opencaselaw-publish.lock`: `publish.py` releases
  the lock at the DB swap, and later steps still read the shards. The incremental publish (20:00 UTC; it ended about 02:28 UTC
  on 2026-10-08) reads `decisions.db`, not the shards (its production `ExecStart` has no
  `--structure-from-shards`), so it does not race a shard repair. No scraper may append to
  the shard being repaired while the repair runs: the atomic replace would drop the lines it
  appended.
- Deploying the serving change (`source_defects`) is a server deploy through
  `scripts/agent_safe_deploy.py`; it needs no build.

## Handling

Nothing is applied to production yet. The serving change and the segmenter patch below are
on branch `claude/modest-dirac-r3nqvk`, for review.

**Constraint.** `build_fts5._remove_stubs` deletes every row whose `full_text` and `regeste`
are both shorter than 10 characters. Historical rows have no regeste. Emptying a text in the
shard therefore removes the row, and with it the reference, at the next full build.

**Removal on its own is safe today, but uninformative.** On a miss, `cite()` suggests the
ruling whose page range contains the queried page (`mcp_server._bge_containing_decision`).
That lookup matches dockets of the form `52 I 149`, while historical rows store `52_I_149`.
Live, `cite("BGE 52 I 151")` answers `exists=false` with no suggestion. A removed row would
therefore not be redirected to a neighbour; the caller only gets "the citation is wrong or
not indexed". Two consequences:

- The serving list below says why the text is missing instead.
- If pinpoint resolution is ever extended to the underscore form, it must consult
  `source_defects` first.

### Built: serving list `source_defects.py` (branch `claude/modest-dirac-r3nqvk`)

A reviewed list of the nine rows. `tests/test_source_defects.py` pins it to the TSV, so
neither can change without the other.

**Kinds of entry:**

- **withhold** (52 I ×6, 39 I 469, 22 I 12): the row is served with metadata and a note, no
  text and no regeste. Its date falls back to the volume placeholder: the stored dates of
  39 I 469 and 22 I 12 came from the foreign text, and the canonical-identity date is
  overridden too. Its statutes, cited decisions and outgoing citation edges are dropped,
  because they were extracted from that text. Incoming edges stay: they cite this reference.
- **truncate** (71 II 223): the text is served up to its own last page (5,729 characters),
  and search hits whose snippet comes from the appended pages are dropped. Both apply only
  while the stored text has the SHA-256 the cut was verified on, so they lapse by themselves
  once the text is re-segmented. Its structure and outgoing citations stay withheld until
  the entry is removed, so the old structure is never served before the structure DB is
  rebuilt.

**Hooks:**

- `get_decision_by_id`, used by the `get_decision`, `get_decisions` and `fetch` tools and by
  REST `/decisions/{id}`.
- `_get_decision_strict`, used by the claim and quotation checks.
- `_fetch_structure_row`, `_fetch_structure_paragraphs`, `_compute_pinpoint` and
  `find_relevant_erwaegung`: structure is withheld for all nine rows. For 71 II 223 the live
  structure is Erwägungen 4-7 of 77 II 154.
- `search_fts5`, which also covers vector and deep-research search: hits on withheld rows
  are dropped. A page that lost a hit is refilled from a slightly larger query, and `total`
  stays the index's count, so paging is not cut short.
- `find_leading_cases` and its FTS fallback drop withheld rows.
- `find_citations` serves no outgoing edges for listed rows; `get_decision` counts none.
- `cite`. On a hit it gives `source_defect`, `text_available: false` and no
  `rule_statement`. On a miss it gives `not_found_reason: "source_defect"` and no close
  matches.
- The `get_decision` / `get_decisions` text and the REST 404 detail carry the note.

**Checked on the real rows** (published texts in a throwaway `decisions.db`):

- the six withheld rows serve no text and placeholder dates;
- 71 II 223 serves 5,727 characters, with no Frigaliment text;
- 52 I 14 and 65 I 8 are unchanged.

**Code review (2026-10-07).** An independent review of the branch found ten points. Fixed:

- the pinpoint and relevant-Erwägung paths;
- outgoing citations;
- the 71 II 223 search snippets;
- leading cases;
- paging;
- the order of the `cite` check;
- two audit-script points.

Not fixed, with reasons:

- **A pinpoint page inside a defective reference** (`cite("BGE 52 I 12")`): the
  containing-ruling suggestion does not match historical dockets today (`52_I_8`), so cite
  answers a plain not-found. If pinpoint resolution is extended to that form, it must consult
  `source_defects` first.
- **Trend counts** (`analyze_legal_trend`) still count the withheld rows: at most nine rows
  among a million, no text shown.
- **Language:** the `language` field of a withheld row is the one detected from the other
  ruling's text.

**Residual, until the shard repair:**

- The dataset export and the static pages carry the stored text.
- A `.docx`/`.pdf` export of a withheld row carries the citation and metadata only, without
  the note.

Take an entry off the list when its source is fixed and re-scraped, and the truncate entry
once the repaired text has been served and the structure rebuilt.

### Built: segmenter rule (applied with the segmentation branch)

Commit `bge_historical_segment: end a ruling where another volume's pages begin`, on top of
the segmentation branch (`a7e23576`) in the PR that brings that branch to `main`; it was
kept here as a patch file until then. A ruling also ends where the printed page numbers restart well below the reference page, run on for
three page lines and never come back. That is the audit's `page_jump` rule, starting after
the own header.

- On the 14,578 published rows it changes 71 II 223 only (cut at 5,754 of 21,582
  characters). The March text is unchanged.
- That branch's tests pass (29). The new 71 II 223 test fails without the rule.
- A second new test checks that an OCR'd page run that comes back is not cut.
- The two ruff findings in that test file already exist on the branch.

### Built: shard repair `scripts/repair_bge_historical_sources.py` (proposal; not run)

It applies the list to a shard. **Dry run by default.** `--apply` writes atomically and puts
every removed or changed row, as it was, into `<shard>.source-repair-<date>.jsonl`. A second
run changes nothing, and rows of other courts are never touched.

- **withhold:** the row is removed from the shard. Its id stays in
  `state/bge_historical.jsonl`, so the same defective document is not fetched again. The
  reference leaves `decisions.db`, the dataset, the graph and the structure DB at the next
  full build, and `source_defects` keeps answering for it.
- **truncate:** the row is cut to the ruling's own pages (only while it is the verified
  text), its `cited_decisions` is recomputed, and a `source_repair` stamp is added.

**Dry runs:**

- Published text: 9 rows changed (8 removed, 71 II 223 cut), 14,569 untouched.
- March text: 8 removed; 71 II 223 left alone, because it held the servat HTML then.

If the server shard looks like March, the PDF text of 71 II 223 comes from an `es_*` shard
through the build's text-upgrade. Run the repair on that shard as well:

```bash
python3 scripts/repair_bge_historical_sources.py output/decisions/bge_historical.jsonl
for f in $(grep -lE '["_](52_I_(1|8|23|39|149|230)|39_I_469|22_I_12|71_II_223)"' output/decisions/es_*.jsonl); do
  python3 scripts/repair_bge_historical_sources.py "$f"; done
# read the dry-run lines, then the same commands with --apply (copy the shards first)
```

### Still proposals

**A (52 I ×6) and C (39 I 469): shard repair.** Run the script above at the next
segmentation window: after the full publish has finished, done before the 01:00 UTC
nightly scrape (see "Coordination check").

Not recommended:

- an alias 52 I x → 65 I x, which would assert that BGE 52 I 8 *is* the Neef ruling;
- a text-less row kept in `decisions.db`, which needs `build_fts5` and schema work for what
  the serving list already gives.

**B (71 II 223): apply the segmenter patch with the segmentation branch.** That way the shard
is rewritten once. Not recommended: restoring the servat HTML text served until March. A job
unknown so far replaced it with the PDF text and would likely do so again. Find that job
either way, since it can bring a defective PDF in elsewhere.

**D (22 I 12): re-fetch from the PDF.** Re-fetch `https://www.fallrecht.ch/c1022012.pdf`
through the scraper's PDF path and replace the text and the date, then take the entry off the
list. The current text is BGE 22 I 1012 and belongs in a new row from `c1022A12.pdf`, which
arrives with the side-finding fix. Scraper rule: when a DFR HTML page's title names another
reference, take the PDF of the same code.

**Report to DFR: not sent.** The owner decided on 2026-10-08 not to write to DFR. For the
record, the report would name: the six 52 I PDFs that hold 65 I scans; `c1039469.pdf` =
`c1039483.pdf`; 77 II 154-161 appended to `c2071223.pdf`; `c1022012.html` = 22 I 1012.
Without it, the real rulings come from the neighbouring scans (next section) or not at all.

### Recovery from neighbouring scans (checked 2026-10-08)

DFR scans are two-page spreads cut at whole pages. The scan of a ruling therefore usually
also holds the last page of the ruling before it and the first page of the ruling after it,
and so do the corpus rows made from it. Five of the eight withheld rulings are wholly or
partly in such neighbouring scans. Checked on the rendered spreads (not only the text layer)
and on the dataset rows of 2026-10-07. The headers are as printed on the scans:

| Reference | Ruling (header on the scan) | Pages, and the scan holding them | Coverage |
|---|---|---|---|
| 22 I 12 | No 4, Urteil vom 25. März 1896 in Sachen Fietz und Leuthold | pp. 12-17, its own scan `c1022012.pdf`; the text layer has it | complete |
| 39 I 469 | No 83, Arrêt du 11 septembre 1913 dans la cause Giroud | p. 469: last spread of `c1039465.pdf`; pp. 470-471: first spread of `c1039471.pdf` | complete. Neither text layer has it: OCR (a test OCR here read "1943" for a printed 1913 in the facts) |
| 52 I 23 | No 4, Urteil vom 26. Februar 1926 i. S. Walz gegen Luzern | p. 23: last spread of `c1052014.pdf` (row 52 I 14); p. 26: first spread of `c1052027.pdf` (row 52 I 27) | header, regeste, facts, start of the reasons, end; pp. 24-25 missing |
| 52 I 39 | No 7, Urteil vom 26. März 1926 i. S. Dällenbach gegen Staatsanwaltschaft und Obergericht des Kantons Aargau | p. 39: last spread of `c1052034.pdf` (row 52 I 34); p. 44: first spread of `c1052044.pdf` (row 52 I 44) | header, regeste, end of the reasons, Dispositiv; pp. 40-43 missing |
| 52 I 149 | No 22, Auszug aus dem Urteil vom 5. März 1926 i. S. Rosenthal und Schilling gegen Regierungsrat Thurgau | p. 149: last spread of `c1052145.pdf` (row 52 I 145) | header, regeste, start of the facts; pp. 150-153 missing |
| 52 I 8 | No 2; its header page is not held | p. 14: first spread of `c1052014.pdf` (row 52 I 14) | last page only: end of the reasons, Dispositiv; no date, no parties |
| 52 I 1 | No 1 | none: the next scan, 52 I 8, is itself a 65 I scan | nothing |
| 52 I 230 | — | none: `c1052227.pdf` ends on p. 229, `c1052238.pdf` starts with its own ruling | nothing |

The 52 I pages are in the current rows' text (text layer, no OCR); the segmentation repair
cuts exactly these pages out of the neighbouring rows. The texts were therefore taken now and
kept in the repo.

### Built: recovered texts and `scripts/apply_bge_historical_recoveries.py` (not run)

`runbooks/historical_bge_recovered_2026-10-08/`: one text per recovered ruling,
`manifest.json` (ruling, date and where it was read, pages held and missing, sources, method,
SHA-256 of each text and of the three scans), and `build.py`, which made them:

- 52 I 23, 39, 149: the parts `bge_historical_segment.segment` cuts off the neighbouring rows,
  joined, with `[Pages 24-25 are missing from the source.]` where pages are missing. 52 I 23
  and 149 take their date from the scan image: the text layer garbles the month.
- 39 I 469: OCR of the page crops, Tesseract model `fra`. The scraper's Fraktur model read the
  headnote's "Art. 69 ch. 3 LP" as "Art. 89 ch. 8"; `fra` reads it right but misreads other
  figures ("14 mars 1943" for 1913, "31 juillet 1918"), and both read "art. 482" for 182.
  The text is not proofread: no hand edits. A served note must say it was read by OCR.
- 22 I 12: the scraper's PDF path on `c1022012.pdf`.

`scripts/apply_bge_historical_recoveries.py` replaces (or adds) the five rows of the
`bge_historical` shard with rows built as the scraper builds them, stamped `source_recovery`.
Dry run by default, undo file, idempotent; it refuses a text whose SHA-256 is not the
manifest's. `segment_bge_historical.py` and `repair_bge_historical_sources.py` leave stamped
rows alone. Measured on the published rows (13 rows: the five, their neighbours, 52 I 8): 5
replaced; then the segmentation skips the 5 and cuts the 6 neighbours, and the repair keeps
the 5 and removes 52 I 8.

**The `es_*` repair is required.** The build swaps a row's text for a much longer copy with
the same canonical key ("How the swap most likely happened"). The recovered 52 I texts are
short (52 I 39: 982 characters); an entscheidsuche copy holding the 65 I text would replace
them. Run `repair_bge_historical_sources.py` on every `es_*.jsonl` that carries a listed row.

**Serving follows the data (built 2026-10-08).** The five `source_defects` entries carry the
SHA-256 of their recovered text (the manifest's; the build's `_clean_text` leaves all five
unchanged, measured). While the stored text is exactly that text, the server serves it: the
row keeps its own date and regeste, and `source_defect` carries a recovery note naming the
pages held and missing (52 I 23, 39, 149: partial) or the OCR and the scans to check figures
against (39 I 469). Search hits count only on the recovered text. Any other stored text is
withheld as before, so nothing changes until the shard repair and a build have stored the
recovered text. The structure and the outgoing citations stay withheld for these references.
52 I 1, 8 and 230 stay withheld: a single last page of reasons, with no facts, date or
parties, misleads more than it helps.

What stays missing (52 I 1 and 230 entirely; pp. 9-13, 24-25, 40-43, 150-153) exists only in
the printed volume BGE 52 I and in DFR's own files.

### Recommended order

1. Done: the serving list, jonashertner/opencaselaw#129 (merged 2026-10-08; live after the
   server's next pull and worker restart).
2. Done: the segmentation branch with the page-jump rule, jonashertner/opencaselaw#130
   (merged 2026-10-08).
3. In one maintenance window (after the full publish, before the 01:00 UTC scrape; check
   `systemctl is-active opencaselaw-publish.service opencaselaw-scrape.service`), copy the
   shards, then dry run each step and `--apply` it:

   ```bash
   S=output/decisions
   python3 scripts/apply_bge_historical_recoveries.py $S/bge_historical.jsonl
   python3 scripts/segment_bge_historical.py $S/bge_historical.jsonl --examples 40
   python3 scripts/restore_sg_dates.py $S/sg_publikationen.jsonl --examples 40
   python3 scripts/restore_sg_dates.py $S/es_sg_gerichte.jsonl --examples 40
   python3 scripts/repair_bge_historical_sources.py $S/bge_historical.jsonl
   for f in $(grep -l -E '["_](52_I_(1|8|23|39|149|230)|39_I_469|22_I_12|71_II_223)"' $S/es_*.jsonl); do
     python3 scripts/repair_bge_historical_sources.py "$f"; done
   ```

   Expected: recoveries 5 replaced; segmentation as in its runbook, plus 5
   `outcome:recovered`; repair 5 `kept:recovered`. The next full build serves the result;
   then regenerate the canonical-identity sidecar (segmentation runbook, step 4).
4. Check the five through the API after the build: the gated entries serve them from then
   on (previous section).
5. Then the letter-coded pages (see "Side finding"): merging the scraper change is the
   rollout, at the next 01:00 UTC scrape. 230 new rows, additive, cut and dated by the
   merged segmenter. A staging run on the server can go first.
6. Run the audit after every `bge_historical` re-scrape. A finding not in the TSV goes to a
   scan check, then onto the list.

The repairs touch the shard, so they run outside the build window with an undo file, like
`scripts/segment_bge_historical.py`.
