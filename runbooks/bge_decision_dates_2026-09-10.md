# BGE decision dates: audit, root causes, proposed fix (2026-09-10)

**Symptom.** `cite` returns `decision_date: 1972-05-16` for BGE 150 III 367 (volume 150 =
2024; the Urteilskopf says 13. August 2024) and `2010-12-01` for BGE 138 III 425 (2012;
Urteilskopf: 17. April 2012). The `decision_date_warning` (mcp_server.py
`_bge_volume_year_mismatch`, volume N collects year N + 1874, tolerance ±1) fires
correctly on both, but the stored dates are wrong and the fix was missing.
Re-confirmed live 2026-09-10 00:xx UTC: `cite('BGE 150 III 367')` resolves to
`bge_BGE_150_III_367`, date 1972-05-16, warning present.

**Scale (audit over the VPS shards, copies pulled 2026-09-09 23:10 UTC).** The build
was running, so decisions.db was not scanned (CLAUDE.md invariant 9); the shards are the
build's input and carry the provenance that decisions.db only repeats in `json_data`.

| shard | `bge` rows | outside ±1 y | share | rewritten 03-12 | of which regressions | 1 Jan placeholders |
|---|---|---|---|---|---|---|
| `bge.jsonl` (direct CLIR scraper) | 20,867 | 1,582 | 7.6 % | 19,523 | 1,582 | 1,346 |
| `es_bge.jsonl` (entscheidsuche CH_BGE) | 24,328 (474 EGMR rows excluded) | 1,214 | 5.1 % | 4,063 | 1,175 | 15,683 |
| total | 45,195 | 2,796 | | 23,586 | 2,757 (98.6 %) | 17,029 |

"Rewritten 03-12" = rows stamped `date_extraction` by `scripts/repair_decision_dates.py`.
"Regression" = the stamp's `metadata_date` (the value it overwrote) was volume-consistent.
By extraction method of the bad rows: header_de 1,054, bare_header 1,037, header_fr 502,
signoff 131, header_it 36, no stamp 36 (entscheidsuche's own Datum wrong). Every volume
80–151 is affected (2–78 rows each; direct worst at 130–151, entscheidsuche at 100–110).
Full per-volume table: `python3 audit_bge_decision_dates.py --jsonl-dir <shards>`.

**The volume rule sees only the gross errors.** Re-deriving every direct row's date from
its own Urteilskopf (see fix B) additionally changes 1,359 dates that sit *inside* the ±1 y
window but are lower-court dates ("Beschluss vom 27. März 2024" for BGE 151 I 32, ruling
20.02.2025). So roughly 2,900 of the 20,867 direct rows carry a wrong day, not 1,582.

**Duplicates make it worse than the counts say.** 20,718 BGE exist under two ids
(`bge_150 III 367` direct, `bge_BGE_150_III_367` entscheidsuche; canonical dedup keys on
the date, so disagreeing rows never merge — the known dual-id issue #40). Dates disagree
on 15,038 of them. `_bge_ref_candidates` tries the entscheidsuche id first, so for 792
BGE `cite`/`get_decision` serve a corrupted entscheidsuche row while the direct row is
right (the two reported cases are in this class); for 1,337 the wrong direct row hides
behind a fine entscheidsuche row (visible through search results and the citation graph,
which keys on the direct id). 3,136 entscheidsuche keys also exist as `Ia`/`IA` id
case variants of the same decision.

## Root causes (all three verified, not inferred)

1. **Scraper reads half the Urteilskopf** (`scrapers/bge.py`, `_parse_document_metadata`).
   The CLIR page splits the header over two `<div class="paraatf">` blocks:
   `37. Auszug aus dem Urteil der II. zivilrechtlichen Abteilung i.S. A. gegen B. (…)` and,
   separately, `5A_691/2023 vom 13. August 2024`. `soup.find()` returned the first only,
   so `RE_META` never saw `vom <Datum>` and every row fell through to
   `date(year, 1, 1)` with chamber and docket_2 NULL. 117 of 117 rows scraped after the
   03-12 pass are such placeholders — the defect is live. Verified on four pages fetched
   2026-09-10 (DE 2024, DE 2012, FR 1993, IT 2022; trimmed headers are the new fixtures).
   Italian headers (`del 12 dicembre 2022`, `nella causa`) and the older French form
   (`arrêt de la Ire Cour civile du 13 juillet 1993 dans la cause …`, date before the
   parties) could never match the old anchored regex either.
2. **The 2026-03-12 one-off repair overwrote good dates with body-text dates**
   (`scripts/repair_decision_dates.py --all`, `bge` and `bge_egmr` in its court list,
   provenance stamp on every row). `scripts/extract_decision_date.py` takes the first
   `Urteil vom …` in the first 1,500 chars as a *high-confidence header*; in a BGE that
   phrase is the lower-court judgment in the Sachverhalt (`Urteil der … Abteilung … vom`
   is not `Urteil\s+vom`). Without a typed header it takes the first bare date in the first
   500 chars, which in the Regeste is a statute or treaty date (`Übereinkommens vom
   16. Mai 1972`, `seit 1. Dezember 2010 geltenden Fassung`) that `_is_statute_date` does
   not recognise. BGE text has no sign-off line to contradict either. The entscheidsuche
   Kopfzeile's numeric ruling date (`Band III 13.08.2024`) is only tried last. For
   entscheidsuche rows the correct Datum was moved into `publication_date` — it is still
   there, which is why the shard repair below is exact.
3. **No guard covers BGE.** `_recovered_date_plausible` / `check_docket_year_plausibility`
   return None for `150 III 367` dockets, `_null_implausible_gr_dates` is gr-scoped, and
   `_normalize_dates` only clears 0000/pre-1700/future dates.

## Proposed fix, by layer

**A. Scraper (implemented in this worktree, uncommitted).** `urteilskopf_text()` joins
every `paraatf` block before `div#regeste`; `parse_urteilskopf(header, volume_year)`
keeps `RE_META` as the primary path (it matched 2,534 of 2,815 joined headers in a shard
simulation) and adds fallbacks: every `vom/du/del <Datum>` token in text order, first
BGG-form docket, chamber phrase, DE/FR/IT months. The FIRST candidate that passes the
volume gate wins — where a header carries two dates the ruling date comes first (BGE
126 III 283: `arrêt … du 15 mai 2000 dans la cause … contre la décision du 31 janvier
2000`; 6 such headers in the shard). The source-side gate is asymmetric,
`header_date_plausible`: up to three years BEFORE the volume year, never more than one
after — BGE 149 IV 97 is 6B_1079/2021 of 22.11.2021 in the 2023 volume (corroborated by
the BGer row) and BGE 98 Ib 396 an arrêt of 1970 in the 1972 volume, while `28. August
1985` in volume 84 (1958) is a typo. A rejected date is kept as `date_rejected`, the
placeholder is flagged `date_is_placeholder`. `tests/test_bge_urteilskopf_dates.py`: 13
tests, 4 live fixtures, end-to-end `fetch_decision`. Full suite: 3,239 passed, 21 skipped.

**B. Shard repair (implemented as `scripts/restore_bge_dates_from_urteilskopf.py`, DRY
RUN by default, not run on the VPS).** Per row: (1) the row's own Urteilskopf date
(direct, via `parse_urteilskopf`) or Kopfzeile numeric date (entscheidsuche), source gate;
(2) else keep the current date if it passes its gate — strict ±1 for a value the 03-12
pass wrote, the source gate for an untouched scraper/entscheidsuche value; (3) else
restore the stamp's `metadata_date` if it passes the source gate (undoes 03-12); (4) else
1 January of the volume year.
Resets the `publication_date` the 03-12 pass planted; stamps `date_restore`; also fills
`docket_number_2`/`chamber` for direct rows. Dry run on the pulled copies:

| shard | rows changed | bad → fixed | placeholders refined | inside-window corrections | header dates | metadata restores | left as placeholder |
|---|---|---|---|---|---|---|---|
| `bge.jsonl` | 16,961 | 1,581 | 1,276 | 1,359 | 20,273 (97.2 %) | 21 | 0 |
| `es_bge.jsonl` | 1,274 | 1,212 | 12 | 50 | 4,581 | 889 | 37 |

The two bad rows per shard that stay are BGE 149 IV 97 and 98 Ib 396, the genuine late
publications: their dates are right and the ±1 warning will keep flagging them, which is
the honest outcome until the warning's lower bound is widened (see D).

Cross-check of the two independent sources: on all 4,579 BGE where both a direct
Urteilskopf date and an entscheidsuche Kopfzeile date exist, they agree (0 disagreements).

**C. Build backstop (proposal).** `_null_implausible_bge_dates(conn)` in `build_fts5.py`,
next to `_null_implausible_gr_dates`: for `court='bge'`, volume from `docket_number`;
where `|year − (volume + 1874)| > 1`, restore `publication_date` into `decision_date` if
that one is volume-consistent (the 03-12 swap) and NULL it, else NULL the date (NULL over
guess). Same rule inline in `insert_decision` for bge rows. ~30 lines plus a test in the
style of `tests/test_gr_date_plausibility.py`.

**D. QC check (proposal).** `check_bge_volume_year` in `quality/checks/dates.py`
(auto-discovered), WARNING with a recorded baseline, `by_source` split direct vs
entscheidsuche id form, so a scraper or repair regression shows up the next morning.
Consider widening `_bge_volume_year_mismatch`'s lower bound to −3 at the same time: two
genuine late publications trip the ±1 window today.

**E. `extract_decision_date` hardening (only if the repair script is ever re-run).** Drop
`bge`/`bge_egmr` from `COURTS_PUBLICATION_DATE_AS_DECISION` (use `parse_urteilskopf`
instead); add `übereinkommen`, `abkommen`, `fassung`, `in kraft` to the statute
indicators; try the Kopfzeile numeric date first for entscheidsuche rows.

**F. Out of scope, noted.** The dual-id/resolver preference (#40) and the `Ia`/`IA`
entscheidsuche id variants. Separate structure issue seen on BGE 119 II 339: the sidecar
lists `1`, `1aa`, `1bb` — the text's `1. c) aa) / bb)` lost its `c)` level, so the
pinpoint is flattened to `1aa` (see memory: lettered-erwaegungen-sort-incident). Not fixed
here.

## Deploy sequence (after review)

1. Commit A (scraper + tests + fixtures) and deploy through the normal path (commit, push,
   `merge --ff-only` on the VPS). New rows get real dates from the next 01:00 scrape. Note
   the VPS tree sat at c58bc85 on 09-09, 18 commits behind origin/main: the ff-merge that
   carries this change also lands Sprint 1 (laws aliases, never-empty, attest ledger,
   docket resolver, EMARK, zg_gvp, stale detector), whose VPS deploy was planned for after
   the 09-09 build exit anyway; the mcp_server parts go live at the next rolling restart.
2. After a build exits (never 03:30 UTC → pipeline exit), on the VPS:
   ```bash
   ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo && export SWISS_CASELAW_DIR=/opt/caselaw/repo/output && python3 scripts/restore_bge_dates_from_urteilskopf.py output/decisions/bge.jsonl && python3 scripts/restore_bge_dates_from_urteilskopf.py output/decisions/es_bge.jsonl'
   ```
   Read the dry-run counts (expect the table above), then re-run both with `--apply`, then
   `python3 audit_bge_decision_dates.py --jsonl-dir output/decisions` (expect 0 bad direct
   rows, 39 entscheidsuche placeholders, the 36 unstamped entscheidsuche rows fixed from the
   Kopfzeile or set to the volume's 1 January). The next full build ships the shards.
3. Verify served data once that build has swapped and the pipeline has exited:
   `python3 audit_bge_decision_dates.py --db /mnt/HC_Volume_104655575/output/decisions.db`
   (the script refuses while publish.py runs) and `cite('BGE 150 III 367')` → 2024-08-13,
   no `decision_date_warning`; `cite('BGE 138 III 425')` → 2012-04-17.
4. C and D as a follow-up so the class cannot regrow silently.

## Files in this worktree (branch claude/sad-golick-fb4d33, nothing committed)

- `scrapers/bge.py` — fix A (patch)
- `tests/test_bge_urteilskopf_dates.py`, `tests/fixtures/bge_{150_III_367,138_III_425,119_II_339,149_I_105}_head.html`
- `audit_bge_decision_dates.py` — the audit (`--db` / `--jsonl-dir`, invariant-9 guard,
  constant parity check against mcp_server.py, duplicate analysis, `--out` JSONL)
- `scripts/restore_bge_dates_from_urteilskopf.py` — fix B, dry run by default
