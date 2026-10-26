# Reference data

Small, versioned reference artifacts derived from the OpenCaseLaw corpus.
Regenerate rather than edit by hand.

## `law_codes.json` / `law_codes.csv`

Cross-language law-code table: abbreviation → SR number, for federal acts,
plus cantonal acts keyed (canton, systematic number).

**Why it exists.** The citation extractor
(`search_stack/reference_extraction.py`) emits *language-specific* law
codes by design. A consumer that does not know `OR == CO` or `ZGB == CC`
silently loses most French-language statute references and gets no error
at all — just quietly incomplete results. Our own QC module measures the
gap: `OR` alone is 125,592 statute edges, `OR` + `CO` is 359,195. This
table converts a silent wrong answer into a resolvable lookup.

Raised as [#74](https://github.com/jonashertner/opencaselaw/issues/74).

**How it is produced.** Derived, not curated:
`output/statutes.db` already carries `sr_number` with `abbr_de` /
`abbr_fr` / `abbr_it` per act, so the alias groups *are* the data.
Repealed acts and entry-into-force dates come from Fedlex SPARQL.

```
python3 scripts/build_law_code_table.py \
    --statutes output/statutes.db \
    --cantonal output/cantonal_laws.db \
    --fedlex-cache /tmp/fedlex_law_codes.json \
    --out docs/data/law_codes.json --csv docs/data/law_codes.csv \
    --forms-out search_stack/law_code_forms.py
```

Run it against the production databases: the local `statutes.db` is a
five-law development slice. `--fedlex-cache` stores the Fedlex fetch
(~10 minutes) for reruns; `--no-fedlex` skips it.

The derivation independently reproduces every pair
`quality/checks/statute_graph.py` maintains by hand:

| Alias | SR | Alias | SR |
|---|---|---|---|
| OR | 220 | CO | 220 |
| ZGB | 210 | CC | 210 |
| StGB | 311.0 | CP | 311.0 |
| BGG | 173.110 | LTF | 173.110 |

**Contents** (2026-10-26). 1,177 federal acts in force (1,154 with
multilingual abbreviations, each with its ELI and `in_force_from`), 3,031
distinct aliases; 1,015 repealed acts with 2,023 aliases; 15,560 cantonal
acts.

- `alias_to_sr`: code → SR number of the act in force, or a list where
  several acts in force share the code.
- `alias_to_repealed`: code → ELI of each repealed act that bore it; the
  act's row in `repealed` has `in_force_from` / `in_force_until`. 787 codes
  are in both indexes (`ELG`, `MWSTG`, `AsylG` ...): an earlier act bore the
  code too, so resolve by the decision date. A repealed act is never mapped
  by SR number alone, because the number is often reused (the
  Gerichtsstandsgesetz sat at 272, now the ZPO).
- `alias_notes`: codes a plain lookup gets wrong, by kind:
  `usage_override` (`OPC` means the OPC-AVS/AI, SR 831.301, not Fedlex's
  281.41; `KLV` is the Krankenpflege-Leistungsverordnung), `case_collision`
  (`KLV`/`KlV` and five others merged by upper-casing),
  `cantonal_homonym` (`StG`, `PBG`, `LAF`, `LC`, `LPers`, `PG`, `LAS`:
  the canton's own act in a cantonal decision), and
  `repealed_alias_excluded` (Fedlex short titles of repealed acts that
  collide with usage, such as `TVA` or `ALV`, left out of
  `alias_to_repealed`). The overrides and lists live in
  `scripts/build_law_code_table.py`, each with its evidence (anomaly
  report of 2026-10-26).
- `compound_codes`: two-word short title → the joined code the extractor
  emits (`ASYLV 1` → `ASYLV1`, `GEBV SCHKG` → `GEBVSCHKG`, 133 forms). Both
  spellings are keys of `alias_to_sr` / `alias_to_repealed`. A joined form
  that is already another act's one-word code is left out (`OA Fam` would
  collide with `OAFam`).
- Edition prefix: a lower-case `a` or `n` before a known code is kept
  (`ART.12.aZG`, the Zollgesetz 1925, not `AZG`, the Arbeitszeitgesetz).
  Resolve the base code by the decision date through `alias_to_repealed`.
  `search_stack/law_code_forms.py` is the extractor's copy of these lists,
  generated with `--forms-out`.

**Limits, stated plainly.**

- **Federal abbreviation coverage is 21 %.** Only 1,177 of 5,543 federal
  acts carry an abbreviation at all. The table is complete for what is
  abbreviated, not for the corpus.
- **No cantonal abbreviations.** `cantonal_laws.laws` has no abbreviation
  column — we never captured one. Published instead: canton, systematic
  number, title per language, source URL. Cantonal abbreviations also
  collide across cantons (`PBG`, `StG`, `VRPG` exist in many), so any
  future field must be canton-prefixed (`ZH_PBG`) to be a usable key.
- Graphs built before late October 2026 dropped ordinance numbers
  (`AsylV 1` arrived as `ASYLV`, `BVV 2` as `BVV`) and the edition prefix
  (`aZG` arrived as `AZG`, the Arbeitszeitgesetz). A bare family code such
  as `ASYLV` still appears where the decision itself omits the number.

**Licence.** CC0-1.0, like the rest of the project-created data.
