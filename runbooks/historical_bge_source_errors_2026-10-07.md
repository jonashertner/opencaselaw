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
characters, the complete No 49); the PDF text with 77 II 154 replaced it later, although
`source_url` still names the HTML. Which job swapped the source is open.

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
same form as the existing rows. This is a scraper change, so proposal only.

## Handling (proposal; nothing applied)

**Constraint.** `build_fts5._remove_stubs` deletes every row whose `full_text` and `regeste`
are both shorter than 10 characters. Historical rows have no regeste. Emptying a text in the
shard therefore removes the row, and with it the reference, at the next full build.

**A (52 I x6) and C (39 I 469): never plain removal.** On a miss, `cite()` suggests the
ruling whose page range contains the queried page, with `match_reason:
"queried_page_within_this_decision"` (`mcp_server._bge_containing_decision`). Once
`bge_52_I_149` is gone, `cite("BGE 52 I 149")` would tell the caller to re-cite BGE 52 I 145,
a different ruling. That is as harmful as the current wrong text, and less visible.

Recommended, in two steps:

1. **API first (no rebuild).** Add a short reviewed list of known source defects (these seven
   ids, from the TSV), read by `mcp_server`:
   - On a hit (`get_decision`, `cite`, `get_erwaegung`, structure tools), return metadata and
     a note, without the foreign text. Example note: "the DFR source document for BGE 52 I 8
     is a scan of BGE 65 I 8; the text of BGE 52 I 8 is not available".
   - On a miss, give the same note and no containing-decision suggestion. This follows the
     existing `decision_ref.unavailable_reason` pattern (structured, honest absence).
   - Search drops these ids from its results.

   The change is small and reversible: take an entry off the list. It protects readers from
   the next deploy on.
2. **Shard at the next segmentation window.** Remove the foreign text (the row then leaves
   `decisions.db` via `_remove_stubs`, together with the dataset, graph and structure copies),
   with an undo file. The API list keeps answering for the reference. For 39 I 469 this also
   ends No 87's date on it.

Not recommended:

- removal without the API list (the containing-decision redirect above);
- an alias 52 I x → 65 I x, which would assert that BGE 52 I 8 *is* the Neef ruling;
- a flag without suppression, which leaves the wrong text in search and in quotations;
- a text-less row kept in `decisions.db`, which needs `build_fts5` and schema work for what the
  API list already gives.

**B (71 II 223): end the own ruling at a page jump.** Extend
`bge_historical_segment.segment` so the ruling ends at a page jump (the audit's `page_jump`
rule, one hit in 14,578 rows), with this row as the offline test. Add it to the segmentation
branch before that branch's `--apply`, so the shard is rewritten once. Ending at No 50
additionally needs No 50's header to be recognised.

Not recommended: restoring the servat HTML text served until March. A job unknown so far
replaced it with the PDF text and would likely do so again. Find that job either way, since
it can bring a defective PDF in elsewhere.

**D (22 I 12): re-fetch from the PDF.** Re-fetch `https://www.fallrecht.ch/c1022012.pdf`
through the scraper's PDF path and replace the text and the date. The current text is
BGE 22 I 1012 and belongs in a new row from `c1022A12.pdf`, which arrives with the side-finding
fix. Scraper rule: when a DFR HTML page's title names another reference, take the PDF of
the same code.

**Report to DFR (outward-facing; the owner decides).**

- the six 52 I PDFs that hold 65 I scans;
- `c1039469.pdf` = `c1039483.pdf`;
- 77 II 154-161 appended to `c2071223.pdf`;
- `c1022012.html` = 22 I 1012.

Recommended order:

1. Report to DFR now; it is the only source of the real 52 I and 39 I 469 rulings.
2. Next deploy: the API defect list. It covers A and C, D until its re-fetch, and B as a cut
   at the first foreign page until the segmenter cut. Guard the B entry by the row's
   `content_hash`, so it lapses when the text changes.
3. Review and merge the segmentation branch with the page-jump rule. Then, in one maintenance
   window: segment, remove the foreign texts, and re-fetch 22 I 12 from the PDF.
4. Then the letter-coded pages: about 230 new rows, additive. They go through the merged
   segmenter. Verify about ten decoded references on the scans first.
5. Run the audit after every `bge_historical` re-scrape. A finding not in the TSV goes to a
   scan check, then onto the defect list.

The repairs touch the shard, so they run outside the build window with an undo file, like
`scripts/segment_bge_historical.py`.
