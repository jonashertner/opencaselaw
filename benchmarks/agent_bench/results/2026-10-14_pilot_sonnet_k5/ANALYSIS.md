# Pilot analysis (2026-10-14, sonnet, 10 questions × k=5)

`report.md` is the raw output of the checks. This note records which
S1/S2 findings were checked by hand against the trajectories, and what
they turned out to be. None of the questions has been reviewed by a
lawyer; this is a check of the harness, not a result to publish.

## Raw numbers

| | mean | spread | pass | S1 runs | worst-of-5 S1 | all 5 pass |
|---|---|---|---|---|---|---|
| all | 0.74 | 0.02 | 0.70 | 0.12 | 0.30 | 0.50 |

Cost 19.3 USD list price (0.386 per run), 13.4 tool calls per run.

## S1 findings: all six runs are scoring artefacts

| question | runs | finding | what happened |
|---|---|---|---|
| tv-001 | 1, 3, 4 | unknown_statute | Answer cites "Art. 49 … Tit. fin. CC"; `attest_response` parses the law code as "Tit" and reports `law_abbreviation_unknown`. |
| tv-002 | 0 | unknown_statute | Answer cites "Art. 15 SchlT ZGB"; `attest_response` parses the law code as "SchlT". |
| tv-001 | 3 | unverified_quote | Both quotes are verbatim in the agent's own `get_law(Tit. fin. CC, 49)` result. The auditor never searched that text, because the abbreviation did not resolve (same bug as above). |
| tv-001 | 4 | unverified_quote | The quote is the pre-2020 text of Art. 60 al. 1 CO, verbatim (apostrophes and line breaks aside) in the agent's `get_law(CO, 60, as_of 2019-06-01)` result. The auditor searched only the current version (`statute:CO_60`). |
| rb-024 | 0, 1 | wrong_answer | The imported reference answer is wrong: it requires "forts soupçons" for Art. 221 al. 2 CPP. That condition is in al. 1, not al. 2, and BGE 140 IV 19 E. 2.1 says the ground "verlangt nicht zwangsläufig noch zusätzlich einen dringenden Tatverdacht". The agent said this; the judge, bound to the rubric, scored it as contradicted. |

Two server bugs follow from this (`attest_response`, not the bench):
the statute parser does not recognise the final title of the ZGB/CC
("SchlT ZGB", "Tit. fin. CC"), and the quote check does not search
historical statute versions the agent retrieved with `as_of`. Both
produce S1 false positives for any user who cites transitional law.

## S2 findings

- **rb-019 (5 runs, incomplete_answer).** The imported reference answer is
  one critical rubric point bundling about eight elements, so any omission
  scores 0. The answers give the five-element test as stated in
  BGE 131 II 627 E. 6.1 and BGE 143 V 341 E. 5.2.1. One omission is real:
  no run mentions that a supervisory authority may revoke the subordinate
  authority's decision, which the Regeste of BGE 137 I 69 states (E. 2.1).
- **rb-024 (3 runs, incomplete_answer).** Same defective reference answer
  as above.
- **pp-001 run 0 (missing_authority).** The run cites BGer 4A_407/2021 of
  13 September 2022 instead of BGE 149 III 105 (same date, same subject).
  These appear to be the same judgment, unpublished and published; `cite`
  returns them as two canonical decisions without a link between them.
  Likely an artefact; a lawyer would still cite the BGE.

## What this means

- No confirmed misleading answer in 50 runs. The worst-of-5 S1 rate of
  0.30 is entirely scoring error.
- The imported RAG-bench questions are not usable as they stand: their
  reference answers need to be split into separate rubric points and
  checked (rb-024 contains an error).
- Gold sets should list the docket id of a published BGE as an
  equivalent, or the check should resolve both to one judgment.
- Run-to-run spread is small (0.02), so k=5 is enough to separate
  question-level differences on this set; it says nothing yet about rare
  failures, which need more questions, not more runs.
