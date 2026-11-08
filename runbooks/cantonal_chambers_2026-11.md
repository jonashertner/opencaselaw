# Runbook: chamber from the court's own text, nine cantonal courts (2026-11-08)

**Status: code, tests and repair script committed on branch
`claude/quirky-morse-c7e843`, not pushed, nothing run on the VPS. The shard
repair was rehearsed on copies of the live shards (section 4). Pushing the
scraper changes and applying any repair needs Jonas to name the step
(CLAUDE.md invariant 5).**

Source: `docs/claude/entscheidsuche-parity-addendum-2026-11-08.md` §3 (untracked
in the main checkout): entscheidsuche sets a chamber on every document; we left
it empty on ~110k rows of courts that have chambers.

## 1. What changes

| File | Kind |
|---|---|
| `chamber_rules.py` (new) | the rules: header first, then the court's own docket/file-name series, else nothing |
| `scrapers/cantonal/base_tribuna.py` | `fetch_decision` sets `chamber` (FR, BE VG; every other Tribuna portal has no rule and keeps `None`) |
| `scrapers/cantonal/{bl,nw,ur,ar}_gerichte.py`, `zh_baurekursgericht.py` | new rows carry the chamber |
| `scripts/backfill_cantonal_chambers.py` (new) | held rows; dry run by default |
| `tests/test_cantonal_chambers.py`, `tests/fixtures/chambers/` | offline, 50 tests, 23 golden ruling heads |
| `pyproject.toml` | `chamber_rules` in py-modules |

Scraper behaviour change (pipeline-gated): yes, `chamber` only. No other field,
no id, no listing logic.

## 2. Rules

1. **The ruling's own header**, read from the head of the decision text only
   (past the Rubrum a chamber name is one the reasons mention). Same principle
   as 86ba0c80 for BGer.
2. **The court's docket or file-name series**, only where the series maps onto
   one chamber *and* that was confirmed against the headers of the court's own
   rulings (agreement figures below). Used where the header says nothing:
   metadata-only rows, a lost first page, a `404 Not Found` stored as text.
3. Otherwise **nothing**.

Never from entscheidsuche. es-ingested rows carry its Kopfzeile ("Vaud Tribunal
cantonal Cour d'appel civile 10.05.2011 …") and, when they had no text, its Meta
block ("Basel-Land Kantonsgericht / … / Abteilung Strafrecht").
`chamber_rules.own_text` strips both before anything is read. They are wrong
for whole series: vd_findinfo "Décision / 2011 / 200" has Kopfzeile "Cour d'appel
civile" and header "CHAMBRE DES RECOURS PENALE".

**Header and docket disagree.** The header wins if the head prints the row's own
docket. Example: FR 605 2012 204, where the IIe Cour des assurances sociales sat
under 605 dockets before the 608 series opened in 2013. Otherwise the row is
left alone and listed as a **conflict**. On every conflict checked, the stored
text was another ruling's: `fr_gerichte_106 2018 45` holds the text of 608 2018
5, `be_verwaltungsgericht_100 2023 306` the text of 200 23 618,
`be_verwaltungsgericht_200 2023 618` a 2017 ruling 100.2016.5U. That is a
text-identity defect in its own right (section 6).

**Completing a bare court name.** The script changes `chamber` where it is empty.
It also completes a value that names only the court: bl_gerichte "Kantonsgericht"
becomes "Kantonsgericht, Abteilung Strafrecht", ar_gerichte "Obergericht" becomes
"Obergericht, 3. Abteilung" (`chamber_rules.refine`). Any other existing value is
kept ("ARGVP", "Steuergericht").

**Names.** Each court's own names, in the ruling's language where the court uses
two: FR "IIe Cour administrative" / "II. Verwaltungsgerichtshof", the way BGer
divisions are stored. For a court code that bundles several courts, the value
names the court too ("Kantonsgericht, Abteilung Strafrecht", "Obergericht,
Zivilabteilung"), as bl_gerichte and ar_gerichte already store "Kantonsgericht"
and "Obergericht".

### Per court

| Court | Header | Fallback (agreement with header where both exist) |
|---|---|---|
| `fr_gerichte` | the line under "Arrêt du …" / "Urteil vom …" | docket prefix = the Cour's number: 101/102 Ie/IIe Cour d'appel civil, 104 Cour de modération, 105 Chambre des poursuites et faillites, 106 Cour de protection (≥2013), 501 Cour d'appel pénal, 502 Chambre pénale, 601–603 Ie–IIIe Cour administrative, 604/607 Cour fiscale, 605 Ie (≥2011) and 608 IIe (≥2013) Cour des assurances sociales. 14,787 / 14,816 agree; the 29 others are 20 rulings whose head prints their own docket (header wins) and 9 foreign texts |
| `be_verwaltungsgericht` | "Verwaltungsgericht des Kantons Bern / Sozialversicherungsrechtliche Abteilung" | 100 = verwaltungsrechtliche, 200 = sozialversicherungsrechtliche Abteilung for a German ruling; a French ruling under either series = Cour des affaires de langue française. Language read from the row's own text (the stored tag is wrong on 107 French rulings). 11,724 / 11,729 agree; 4 of the 5 are foreign texts |
| `bl_gerichte` | "Entscheid des Kantonsgerichts Basel-Landschaft, Abteilung X vom …"; "… Steuer- und Enteignungsgerichts …, Abteilung Enteignungsgericht" | file-name code on baselland.ch (`_sv_`, `_vv_`, `_zr_`, `_sr_`, `stger`, `entger`); path `/steuergericht/` etc.; swisslex series with ≥20 header rows and ≥98 % one Abteilung (400–430 Zivilrecht, 460/470 Strafrecht, 710–760 Sozialversicherungsrecht, 810 Verfassungs- und Verwaltungsrecht, 650 Enteignungsgericht; 731 left out at 95 %). 8,642 / 8,652 agree; the 10 others are court-name synonyms (`entger` / `enteignungsgericht`) or rulings whose head prints their own docket |
| `vd_findinfo` (frozen es shard) | caps block under "TRIBUNAL CANTONAL" ("CHAMBRE / DES RECOURS PENALE"), compared without spaces and accents because the OCR splits words ("COUR DES ASSU RANCES SOCIALES") | none: "Arrêt / 2015 / 48" carries no chamber |
| `nw_gerichte` | "OBERGERICHT / … / Urteil vom … / Zivilabteilung" | docket letters ZA, SA, BAZ, BAS (Obergericht), SV, VA, ST (Verwaltungsgericht). 1,043 / 1,045 agree |
| `ur_gerichte` | "OBERGERICHT / Zivilrechtliche Abteilung" (also the Strafprozessuale Beschwerdeinstanz) | OG V / OG Z / OG S. 216 / 216 agree. OG BI, AK, SK and the Rechenschaftsbericht extracts ("94/95 13") only from a header |
| `ar_gerichte` | "Obergericht Appenzell Ausserrhoden / 3. Abteilung" (after the parties); Kantonsgericht likewise | docket O1–O5 / K1–K5 ("O3V-19-40"); GVP entries "OG ARGVP …" / "KG ARGVP …" get the authority ("Obergericht"), as the scraper already stores it. 1,222 / 1,225 agree |
| `zh_baurekursgericht` | "Baurekursgericht des Kantons Zürich / 1. Abteilung" | numeral of the BRGE/BRKE number; "G.-Nr. R4.2010.00132". 439 / 440 agree |
| `be_weitere` (frozen es shard) | none | directorate in the docket ("2023.GSI.846", "JGK 32.13-15.36") or the directorate's host (www.gef.be.ch), stored as the abbreviation, as `be_direktionen._chamber` and 45 held rows already do |

### Checked and left out

| Court | Why |
|---|---|
| `zh_kassationsgericht` | a single body. Rulings print "Kassationsgericht des Kantons Zürich / Kass.-Nr. AA060070", no division. AA/AC are case registers (civil/criminal Nichtigkeitsbeschwerden), not chambers |
| `be_bvd` | the BVD's Rechtsamt decides alone; header "ENTSCHEID DER BAU-, VERKEHRS- UND ENERGIEDIREKTION / RA Nr. …" |
| `be_steuerrekurs` | the Steuerrekurskommission has no chambers in its rulings or listing |
| `sh_obergericht`, `sh_gerichte` | the Obergericht SH names only the composition (Einzelrichter, Dreierkammer). The 718 `sh_obergericht` rows are es metadata stubs |
| `lu_gerichte` | **has** chambers: 2,700 of the 3,351 empty rows name one in their head (Verwaltungsrechtliche, Abgaberechtliche, Sozialversicherungsrechtliche Abteilung, Kantonsgericht 1.–4. Abteilung, Obergericht I./II. Kammer). Left out today because another session is repairing LU dates (`restore_lu_dates.py`, uncommitted). Follow-up |

## 3. Served rows (HF mirror 2026-11-08, rules applied to the served text)

Measured by applying `chamber_rules` to the served text of every row
(`data/<court>.parquet`, the first 3,000 characters of each text). "Written by"
counts the rows that get a new value, including the completions.

| Court | Served rows | Chamber set today | After | Written by header / docket·URL | Completed | Conflicts (left alone) |
|---|---:|---:|---:|---:|---:|---:|
| `fr_gerichte` | 14,858 | 5 (0.0 %) | 14,848 (99.9 %) | 14,841 / 2 | 0 | 9 |
| `be_verwaltungsgericht` | 12,254 | 0 (0.0 %) | 12,249 (100.0 %) | 11,726 / 523 | 0 | 4 |
| `bl_gerichte` | 19,485 | 4,085 (21.0 %) | 18,141 (93.1 %) | 8,791 / 9,011 | 3,746 | 0 |
| `vd_findinfo` | 74,800 | 32,357 (43.3 %)* | 74,282 (99.3 %) | 41,925 / 0 | 0 | 0 |
| `nw_gerichte` | 1,069 | 0 (0.0 %) | 1,054 (98.6 %) | 1,049 / 5 | 0 | 2 |
| `ur_gerichte` | 1,254 | 0 (0.0 %) | 749 (59.7 %) | 236 / 513 | 0 | 0 |
| `ar_gerichte` | 4,615 | 1,621 (35.1 %) | 3,781 (81.9 %) | 1,244 / 1,138 | 222 | 1 |
| `zh_baurekursgericht` | 1,163 | 0 (0.0 %) | 1,160 (99.7 %) | 442 / 718 | 0 | 1 |
| `be_weitere` | 840 | 45 (5.4 %) | 840 (100.0 %) | 0 / 795 | 0 | 0 |
| **total** | **130,338** | **38,113 (29.2 %)** | **127,104 (97.5 %)** | | **3,968** | **17** |

\* build-filled register codes, see below.

The VD figure needs care: 32,220 of the 32,357 vd_findinfo rows served with a
chamber today get it from the **build**, not the shard: P1.2
(`build_fts5._fill_chamber_from_docket`) copies the docket's series code into an
empty chamber ("HC / 2010 / 125" → "HC", "AI 28 & 62/18" → "AI"). Those are
case-register codes, not chambers. After the backfill these rows carry the
chamber name from their header ("Chambre des recours civile"), and P1.2 leaves
them alone because it never overwrites. `vd_gerichte` keeps its portal codes
("CREP", "CASSO"); see section 6.

**Branch (legal area).** `branch_map.derive_branch` reads chamber keywords, so
~87,600 served rows gain a branch: FR 13,045, BL 16,731, VD 56,041, NW 1,052,
UR 749. No row loses or changes a branch. BE VG and ZH BRG have a fixed court
branch; AR's "n. Abteilung" carries no keyword.

## 4. Rehearsal on shard copies

All 17 shards, copied read-only from the VPS on 2026-11-08 (small shards in the
afternoon; FR, BE VG, BL and VD at 21:50 UTC, after the nightly build exited at
21:44). Each one had a dry run (`--report`), then `--apply` on a copy, then
four checks:

- line by line against the original, only `chamber` differs (0 other-field
  changes and 0 length mismatches on every shard);
- the `.bak-chamber-<date>` hard link is byte-identical to the original;
- a second run changes nothing;
- the file mode is kept.

The shard figures count every stored row. The build later folds duplicates
across the direct and `es_` shards, so these totals are higher than the served
rows in section 3. `vd_findinfo` shows 0 % before because its stored chamber is
empty; the "HC/AI" values in section 3 are added only at build time.

| Shard | Court | Rows | Filled before | Filled after | Newly filled | Refined | Conflicts |
|---|---|---:|---:|---:|---:|---:|---:|
| fr_gerichte | fr_gerichte | 14,826 | 0 (0.0%) | 14,816 (99.9%) | 14,816 | 0 | 5 |
| es_fr_gerichte | fr_gerichte | 13,956 | 0 (0.0%) | 13,946 (99.9%) | 13,946 | 0 | 4 |
| be_verwaltungsgericht | be_verwaltungsgericht | 11,780 | 0 (0.0%) | 11,776 (100.0%) | 11,776 | 0 | 4 |
| es_be_verwaltungsgericht | be_verwaltungsgericht | 11,112 | 0 (0.0%) | 11,111 (100.0%) | 11,111 | 0 | 0 |
| bl_gerichte | bl_gerichte | 9,431 | 9,431 (100.0%) | 9,431 (100.0%) | 0 | 8,214 | 0 |
| es_bl_gerichte | bl_gerichte | 16,907 | 0 (0.0%) | 15,507 (91.7%) | 15,507 | 0 | 0 |
| bl_wayback | bl_gerichte | 903 | 0 (0.0%) | 903 (100.0%) | 903 | 0 | 0 |
| es_vd_findinfo | vd_findinfo | 74,866 | 0 (0.0%) | 74,210 (99.1%) | 74,210 | 0 | 0 |
| nw_gerichte | nw_gerichte | 561 | 0 (0.0%) | 559 (99.6%) | 559 | 0 | 2 |
| es_nw_gerichte | nw_gerichte | 508 | 0 (0.0%) | 495 (97.4%) | 495 | 0 | 0 |
| ur_gerichte | ur_gerichte | 440 | 0 (0.0%) | 389 (88.4%) | 389 | 0 | 0 |
| es_ur_gerichte | ur_gerichte | 1,104 | 0 (0.0%) | 571 (51.7%) | 571 | 0 | 0 |
| ar_gerichte | ar_gerichte | 1,665 | 1,665 (100.0%) | 1,665 (100.0%) | 0 | 807 | 1 |
| es_ar_gerichte | ar_gerichte | 4,322 | 0 (0.0%) | 2,300 (53.2%) | 2,300 | 0 | 1 |
| zh_baurekursgericht | zh_baurekursgericht | 1,162 | 0 (0.0%) | 1,159 (99.7%) | 1,159 | 0 | 1 |
| es_zh_baurekursgericht | zh_baurekursgericht | 1 | 0 (0.0%) | 1 (100.0%) | 1 | 0 | 0 |
| es_be_weitere | be_weitere | 840 | 0 (0.0%) | 840 (100.0%) | 840 | 0 | 0 |
| **total** | | **164,384** | **11,096 (6.8%)** | **159,679 (97.1%)** | **148,583** | **9,021** | **18** |

"Refined" means the stored court-only value gains its division:
`bl_gerichte` "Kantonsgericht" becomes "Kantonsgericht, Abteilung
Sozialversicherungsrecht", and `ar_gerichte` "Obergericht" becomes
"Obergericht, 4. Abteilung".

VD values written (all from the header): Cour des assurances sociales 27,268;
Chambre des recours pénale 10,573; Cour d'appel civile 9,272; Cour d'appel
pénale 5,834; Cour des poursuites et faillites 5,659; Chambre des recours civile
5,019; Chambre des curatelles 2,963; Cour civile 1,917; Chambre des recours
1,673; Tribunal d'accusation 1,263; Chambre des tutelles 923; Cour de cassation
pénale 921; Cour administrative 683; Chambre des avocats 218; Chambre des
révisions civiles et pénales 24.

**Conflicts (18 rows, left as they are).** The header names one chamber and the
docket another, and the docket does not appear in the ruling's head. On the rows
checked, the stored text is another ruling's:

| Shard | Docket | Header says | Docket says |
|---|---|---|---|
| fr_gerichte | 102 2018 190 | 101 | 102 |
| fr_gerichte | 608 2017 165 | 102 | 608 |
| fr_gerichte | 106 2018 45 | 608 | 106 |
| fr_gerichte | 102 2018 137 | 106 | 102 |
| fr_gerichte | 605 2018 49 | 102 | 605 |
| es_fr_gerichte | 602 2021 163 | 502 | 602 |
| es_fr_gerichte | 502 2024 179 | 602 | 502 |
| es_fr_gerichte | 602 2025 5 | 502 | 602 |
| es_fr_gerichte | 608 2019 222 | 605 | 608 |
| be_verwaltungsgericht | 200 2023 618 | VRA | SVA |
| be_verwaltungsgericht | 100 2023 306 | SVA | VRA |
| be_verwaltungsgericht | 200 2023 595 | VRA | SVA |
| be_verwaltungsgericht | 100 2018 97 | SVA | VRA |
| nw_gerichte | ZA 21 1 | SA | ZA |
| nw_gerichte | SV 24 12 | SA | SV |
| ar_gerichte, es_ar_gerichte | OG O4V-17-31 | 3. Abteilung | 4. Abteilung |
| zh_baurekursgericht | BRGE I Nr. 0153/2014 | 4. Abteilung | 1. Abteilung |

These are text-identity defects, not chamber defects. Refetching them from the
portal is a follow-up (section 6).

**Order on the VPS.** The source-dates repair
(`runbooks/source_dates_repair_2026-11.md`) rewrites the nw/ur/ar/bl/be shards
too. Run this backfill after it. Neither script changes the other's field, so
the figures above should hold within the rows scraped in between. Both write
their own backup names, so neither refuses because of the other's backup.

## 5. Apply (Jonas)

Outside the build window (after the publish unit has exited, before 03:30 UTC),
with no scrape of these courts running. Each command is a dry run first. Stop
if the dry run's `fill_rate_after`, `conflict` count or values differ from
section 4 by more than the rows scraped since 2026-11-08.

1. Deploy the code (scraper change, pipeline-gated, approved by Jonas on
   2026-11-08). The code is on `origin/main` as 974f8feb. On the VPS run
   `git fetch && git merge --ff-only origin/main`. The scrapers fill new rows
   from the next run. Schedule: Monday 2026-11-09 evening, after the
   source-dates repair. This is step 2.4 of `runbooks/night_2026-11-08_deploy.md`.
2. Back up nothing by hand: the script hard-links
   `<shard>.bak-chamber-<UTC date>` itself, and that name does not match the
   build's `*.jsonl` glob.
3. Per shard, as the repo user, from `/opt/caselaw/repo`:

```bash
export SWISS_CASELAW_DIR=/opt/caselaw/repo/output
for s in fr_gerichte es_fr_gerichte be_verwaltungsgericht es_be_verwaltungsgericht bl_gerichte es_bl_gerichte bl_wayback es_vd_findinfo nw_gerichte es_nw_gerichte ur_gerichte es_ur_gerichte ar_gerichte es_ar_gerichte zh_baurekursgericht es_zh_baurekursgericht es_be_weitere; do
  ionice -c3 nice python3 scripts/backfill_cantonal_chambers.py --shard output/decisions/$s.jsonl --report /tmp/chamber-$s.json > /tmp/chamber-$s.summary.json || break
done
```

   Compare the summaries with section 4, then the same loop with `--apply`.
4. The next nightly build serves the chambers. Check on the HF mirror the day
   after (`data/<court>.parquet`, column `chamber`).
5. Roll back one shard: `mv output/decisions/<s>.jsonl.bak-chamber-<date>
   output/decisions/<s>.jsonl` (the backup is the untouched original inode).
   Delete the backups after one good build.

## 6. Follow-ups found on the way

1. **Foreign texts.** The conflict lists in the rehearsal reports
   (`conflicts`) name rows whose text is another ruling's: FR 9, BE VG 4, NW 2,
   AR 2, ZH BRG 1 on the served set. Some are duplicate rows of one docket
   (`fr_gerichte` 102 2018 190 is served twice: once with its own text, once
   with 101 2018 64's). Needs a text-identity check (does the head print the
   row's docket?) across all Tribuna courts.
2. **BE VG language tags.** 107 French rulings of the Cour des affaires de
   langue française are tagged `language=de`.
3. **VD chamber vocabulary.** `vd_gerichte` stores the portal's codes
   (CREP, CASSO, CACI); `vd_findinfo` will store names. A filter on one misses
   the other. Decision for Jonas: map the codes to names in `vd_gerichte`
   (one table, the codes are the TC's own abbreviations), or leave both.
4. **LU** (section 2, left out): rules ready to write once the LU dates repair
   has landed.
5. **AR "ARGVP"** (1,187 served rows): the scraper stores the GVP behoerde code
   as the chamber. Not a chamber; left as it is.
6. **UR** stays at ~60 %: 366 Rechenschaftsbericht extracts and the OG AK/SK
   series state no Abteilung in text or listing.
