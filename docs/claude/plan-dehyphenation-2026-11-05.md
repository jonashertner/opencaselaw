# Line-break hyphenation in served text (2026-11-05)

## Problem

PDF-extracted rulings keep the typesetter's line breaks ("Über-⏎weisung").
Copied from a reader, the hyphen and break come along; the FTS index sees
two tokens, so a search for "Überweisung" misses the passage. NE also uses
U+00AD soft hyphens, both at every syllable and as a real hyphen
("40 jours\xadamende").

## Governing rule

**A hyphen is removed only where the evidence says it is a line break and
serves no other purpose.** When in doubt it stays (the line break may go,
the hyphen does not). The raw shards are never modified.

## Evidence: a corpus lexicon

`scripts/build_dehyphen_lexicon.py` counts, over the corpus (JSONL shards or
the HuggingFace mirror's parquet, no production read needed), what is
written *inside a line*, never next to a break:

| table | what | used for |
|---|---|---|
| `words` | every word | closed form "überweisung" |
| `hyphenated` | "x-y" with lower-case y | real hyphenated spelling "procès-verbal" |
| `spaced` | "x- y" | suspended compounds "Rechtfertigungs- noch" |
| `capitalized` | words written Capitalised | German nouns |

## Decision rules (`dehyphenate._decide`, first match wins)

1. French inclusive "-e"/"-es" ("Enseignant-⏎e"), Italian "e"/"o" → hyphen kept.
2. One letter on either side ("E-mail", "A-Post", "c'est-à-⏎dire") → hyphen
   kept: hyphenation never leaves a single letter.
3. Upper-case continuation ("Basel-⏎Stadt") → hyphen kept; after a
   lower-case fragment (OCR "juge-⏎Inent") → left as is.
4. Conjunction of 3+ letters ("Kindes-⏎und", "Mitwirkungs- bzw.") → "x- und".
5. Spaced pair attested and closed form not 5× as common
   ("Rechtfertigungs- noch", "Validen- wie") → suspended, hyphen kept.
6. German noun (closed form ≥ 80 % capitalised) with lower-case
   continuation at a line break → join: German capitalises after a hyphen
   in a noun compound ("Kranken-Versicherung"), so "Strafprozess-⏎ordnung"
   can only be a break.
7. Closed form ≥ 100× the hyphenated one → join. Mid-line (no break in
   the text) also requires the continuation to be no word of its own
   ("adé- quats" joins; "Vor- instanz", "wer- den" stay).
8. Hyphenated spelling attested at all (≥ 2, i.e. ≥ 1 % of the closed form)
   → hyphen kept, break removed ("procès-verbal", "porte-monnaie").
9. No evidence: German joins unless a function word follows or both halves
   are words ("physisch-⏎psychisch" stays); French/Italian join only a
   continuation that is no word ("Dé-⏎partement"), else left as is.
   Never across a blank line without lexicon evidence.

Soft hyphens inside a word are invisible and go, unless the pair is really
written hyphenated ("jours-amende"). U+2010/U+2011 become "-".

## Wiring (off unless `OPENCASELAW_DEHYPHEN_LEXICON` names a lexicon)

- `build_fts5.insert_decision`: full_text, regeste, title; per-build counts
  logged.
- `search_stack/extract_decision_structure.extract`: Erwägungen text.
- `search_stack/build_practice_db.build`: practice body_text and title (BJ
  SchKG scans, KBKS / FINMA / BSV PDFs); the scraper's content_hash stays
  the hash of the source text. Local practice shards (1,589 docs): 217,728
  changes, 0 letters changed, 0 idempotency failures.
- `mcp_server._normalise_for_quote_match` (always on, label `fold-v2`):
  folds hyphenation on both sides, so quotes copied before or after the
  switch verify against either text.

## Verification so far

Local shards (10,260 decisions), then the full HuggingFace mirror
(1,056,015 decisions, 120 court files, built and checked on the laptop: no
production read):

- 0 decisions with any letter changed, 0 idempotency failures, every run.
- Review rounds found and fixed: inclusive writing, Italian "e", chains,
  NE soft-hyphen syllables and stray blank lines, OCR capitals, function
  words, and two classes only the full corpus showed: closed forms that some
  source had corrupted ("straferhöhungsoder", "mitwirkungsbzw") outvoting
  suspended compounds, and mid-line suspended compounds without a
  conjunction ("Rechtfertigungs- noch", 673×).
- End-to-end `build_fts5.py` on a scratch copy, switch on vs off: same ids,
  0 letters changed, FTS recall up where words were split.

## Final verification (2026-11-05, final code, lexicon sha256 c1748775…)

- Full HF mirror, 1,056,015 decisions: 400,084 changed, 16,559,551 edits,
  32.1M characters removed; **0 decisions with a letter changed, 0
  idempotency failures**. No removed hyphen has a hyphenated spelling at
  more than 1/10 of its closed form (nouns), 1/100 otherwise.
- Practice (local shards, 1,589 docs) and today's SchKG shards (840 docs,
  101 OCR): 0 letters changed, 0 idempotency failures.
- Independent audit by a second session (3,659 docs, all 76 courts plus the
  SchKG texts, hand-judged samples): four rounds; its findings (capitals
  names, abbreviation pairs, URLs and e-mail addresses) are fixed with tests.
  Round 4: no wrong joins found.
- Head-to-head against entscheidsuche.ch's rule on 855 texts: of ~1,120
  disagreements ours is right at ~1,050, theirs at ~two dozen.
- Speed: 52M characters/s single-process (2.5x the first version, byte-
  identical output on 44,997 decisions): ~8 min per full rebuild on the dev
  Mac, est. 20-25 min on the VPS under CPUWeight=20.
- Known cautious misses (no worse than today's text): two-letter capitals
  fragments ("WE-⏎BER", "HU-⏎BER"); fix after go-live by counting capitals
  hyphenated pairs in the lexicon.

## Before enabling

1. Final census numbers + a reviewed sample on the final rules (this doc
   is updated with them).
2. Lexicon for production: build from the mirror (as here) or from the
   shards outside the build window; copy to the VPS next to the DBs.
3. Env var on the whole publish service (build_fts5 and the structure
   build), or the drift gate compares mixed text.
4. Expected first-night knock-on: `content_hash` changes for every changed
   decision (HF delta, integrity root, verification pack); Erwägungen text
   changes. Row counts unchanged.
5. `make verify` + search benchmark before and after.
6. Later: scholarship full text and commentaries; Materialien OCR keeps
   its own conservative joiner (search_stack/botschaft_historical.py) for
   now.
