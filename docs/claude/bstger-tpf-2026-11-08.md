# BStGer official collection (TPF): direct scraper, 2026-11-08

Gap #4 of `docs/claude/entscheidsuche-comparison-2026-11-08.md`. Not committed, nothing written on the VPS.

## Source

The court's site (bstger.ch → Rechtsprechung) has no TPF index of its own. Its decision database is the Weblaw
portal, which holds the collection as `rulingType` "Entscheide der Amtlichen Sammlung", filterable per
volume year. The entries carry no `rulingDate`, so `bstger.py`'s date-windowed search never returned them. The
URL pattern on the held rows (`bstger.weblaw.ch/files/TPF-<year>-<n>.pdf`) answers 404 from 2022 on. Every
entry's PDF is served at `/api/getDocumentContent/<leid>`, the endpoint the scraper already uses.

Per entry the portal gives: the reference (`title` "TPF 2024 194"), language, the trilingual regeste
(`content`), and on most entries the underlying ruling (`bstgerDossier`, e.g. BG.2024.60). Each PDF is a page
extract of the printed volume: it opens with the end of the previous entry and ends with the start of the next.
The scraper cuts the text to the entry's own heading, which prints the decision date and docket:
"29. Auszug aus dem Beschluss der Beschwerdekammer … vom 11. Dezember 2024 (BG.2024.60)". The heading is found
under the entry's reference line, which the volume prints above it.

## Live dry run (polite: 2 s between requests, generic UA; output to scratch only)

| Year | Court index | Listed | Fetched | Dated | Underlying docket | Held (es) | New |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2004 | 16 | 16 | 16 | 16 | 16 | 16 | 0 |
| 2005 | 40 | 40 | 40 | 40 | 40 | 40 | 0 |
| 2006 | 32 | 32 | 32 | 32 | 32 | 32 | 0 |
| 2007 | 37 | 37 | 37 | 37 | 37 | 37 | 0 |
| 2008 | 47 | 47 | 47 | 47 | 47 | 47 | 0 |
| 2009 | 44 | 44 | 44 | 44 | 44 | 44 | 0 |
| 2010 | 39 | 39 | 39 | 39 | 39 | 39 | 0 |
| 2011 | 49 | 49 | 49 | 49 | 49 | 49 | 0 |
| 2012 | 24 | 24 | 24 | 24 | 24 | 24 | 0 |
| 2013 | 27 | 27 | 27 | 27 | 27 | 27 | 0 |
| 2014 | 29 | 28 | 28 | 28 | 28 | 28 | 0 |
| 2015 | 27 | 27 | 27 | 27 | 27 | 27 | 0 |
| 2016 | 32 | 32 | 32 | 32 | 32 | 32 | 0 |
| 2017 | 27 | 27 | 27 | 27 | 27 | 27 | 0 |
| 2018 | 27 | 27 | 27 | 27 | 27 | 27 | 0 |
| 2019 | 27 | 27 | 27 | 27 | 27 | 27 | 0 |
| 2020 | 32 | 32 | 32 | 32 | 32 | 32 | 0 |
| 2021 | 28 | 28 | 28 | 28 | 28 | 28 | 0 |
| 2022 | 23 | 23 | 23 | 23 | 23 | 0 | 23 |
| 2023 | 24 | 24 | 24 | 24 | 24 | 0 | 24 |
| 2024 | 30 | 30 | 30 | 30 | 30 | 0 | 30 |
| 2025 | 27 | 27 | 27 | 27 | 27 | 0 | 27 |
| 2026 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Total** | 688 | 687 | 687 | 687 | 687 | 583 | 104 |

- 2014: the portal lists TPF 2014 137 twice (two leids, same file `TPF2014137.pdf`, same regeste). There are
  687 distinct entries, exactly 583 held + 104 missing.
- Languages: de 439, fr 183, it 65.
- Underlying docket = the docket the heading prints; all 687 have one. The portal names one for 662 entries, and the
  two agree on 656 (ignoring case and punctuation). The other 6 are the same ruling written another way: the
  pre-2005 renumbering (heading BK_B 138/04, portal BB.2004.46; twice), the joined "SK 003-007/04", and the
  portal's `_A`/`_B` part suffixes (three).
- Compared with the held es rows (same ids): 244 agree on the date. The 244 es rows dated 1 January now carry
  the heading date. 95 more differ:
  - ~92 es dates appear nowhere in the heading. Examples are dates of statutes or earlier decisions cited in the
    text: TPF 2007 181 as 1969-09-10, TPF 2011 5 as 1985-10-04.
  - 3 are joined rulings whose heading prints two dates: TPF 2005 123 "vom 2. und 14. Juni 2005", TPF 2012 70
    "vom 6. Juni und 23. Juli 2012", TPF 2016 1 "vom 9. Januar und 20. Mai 2015". The scraper keeps the first
    date, matching the first docket; es kept the second. TPF 2004 27 ("vom 22. September 2004 bzw. 11. März 2005")
    is the same case. **Decision for Jonas: first or last date for joined rulings.**

  No es row had the underlying docket. The es texts include the neighbouring entries.

## What the rebuild does, and why the es copies must go

Simulated with `build_fts5.import_jsonl` + `_fold_federation_date_twins` on a scratch DB, using the dry-run shard
and the real `es_bstger.jsonl` (copied read-only):

| | TPF rows after build | `_dYYYYMMDD` duplicates | 1 Jan dates | without underlying docket |
|---|---:|---:|---:|---:|
| backfill only | 844 | 156 | 112 | 157 |
| backfill + `scripts/bstger_retire_es_tpf.py --apply` | **687** | **0** | **0** | **0** |

Direct shards import first, so the direct row takes the id. Where the dates differ (339), the es copy is re-id'd
`<id>_dYYYYMMDD`, and the federation fold matches only 183 of them: the es text spans the neighbouring entries and
was extracted by another PDF library (window share ~0.7 < 0.8). Three folds would also swap the uncut es text back
in. The script drops from `es_bstger.jsonl` exactly the TPF rows the direct shard holds: 584, i.e. 583 plus the
malformed byte-identical duplicate `bstger_TPF201126` of TPF 2011 26. The 37 other es rows stay. It refuses to
write if any es TPF row lacks a direct row, and keeps a backup. The ids and public URLs stay the same, and per row
the data comes from the direct source (AGENTS.md: retire es feeds after verified direct parity, no coverage loss).

## Citation

The 583 held rows cited as "TPF TPF 2004 12 du 26 mai 2004" (FR/IT) and "BStGer TPF 2004 12 vom 26. Mai 2004" (DE),
verified live through the MCP `cite` tool. `_build_citation_strings` now gives the collection reference, "TPF 2004 12"
(", E. 2.1" / ", consid. 2.1" with a pinpoint). It reads the volume and page from the stored docket, as the BGE
branch does. Ordinary bstger rulings are unchanged.

## Changes

- `scrapers/bstger.py`: TPF discovery (rulingType + year, paginated), entry cut, heading date and docket, regeste,
  language, PDF URL. Daily runs list the last 3 volume years (a volume is indexed the following year). `--since` lists
  every volume from that year. `OCL_BSTGER_TPF_ONLY=1` skips the date-windowed pass. Listing errors raise.
  `run_scraper.py` is untouched.
- `mcp_server.py`: TPF branch in `_build_citation_strings`.
- `scripts/bstger_retire_es_tpf.py`: one-off shard step (dry run by default).
- `tests/test_bstger_tpf.py` + `tests/fixtures/bstger_tpf/`: 36 offline tests (listing, pagination, entry cut, the
  heading forms met in the dry run, citation, retirement).

## Deployment (each step needs Jonas's OK)

1. Commit, push to main. VPS: `cd /opt/caselaw/repo && git merge --ff-only origin/main`.
2. MCP rolling restart (cite() change).
3. After the pipeline exits (~17:00 UTC), before 01:00 UTC, run the backfill (~690 requests at 1.5 s, 20–50 min):
   `cd /opt/caselaw/repo && OCL_BSTGER_TPF_ONLY=1 /usr/bin/python3 run_scraper.py bstger --since 2004-01-01`.
   Check: `grep -c '"docket_number": "TPF ' output/decisions/bstger.jsonl` → 687; the log shows "TPF <year>: n entries"
   per year as in the table.
4. Then: `python3 scripts/bstger_retire_es_tpf.py` (dry run: 584 dropped, 621 → 37), then `--apply` (backup
   `es_bstger.jsonl.bak-pre-tpf-direct`).
5. The next nightly full rebuild picks both up. bstger grows by 104 rows, so the swap gate is not affected.
6. After the swap: `cite("TPF 2024 194")` → "TPF 2024 194", date 2024-12-11, `docket_number_2` BG.2024.60. 687 bstger rows with
   docket `TPF …`, none with `_d` suffix, none `source = entscheidsuche`.
7. Nothing further: the 09:00 UTC `opencaselaw-scrape-federal` timer lists the last three volumes daily.

## Not done (follow-ups)

- Reverse link at serving: a bstger ruling (BG.2024.60) does not yet name its TPF entry, and a TPF entry does not
  yet point to the full ruling row, the way `published_as_bge` / `full_decision` do for BGE. The link data is now in
  `docket_number_2`. The serving lookup needs an indexed path (see memory bge-reverse-link: the 10-16 incident).
- `make test`: the one failure, `test_consultation_text_gaps.py::test_real_tesseract_reads_a_rendered_page`, is
  local only (tesseract here has no `deu` data) and unrelated.
