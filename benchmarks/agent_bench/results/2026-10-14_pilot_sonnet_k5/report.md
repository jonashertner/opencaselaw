# Agent bench report

Model `sonnet` via claude-code 2.1.296 (Claude Code), k=5, endpoint `https://mcp.opencaselaw.ch/mcp-edu`, questions `benchmarks/agent_bench/questions_v0.jsonl` (sha256 7a13180aea62), started 2026-10-14T18:16:05+00:00.

| slice | questions | runs | mean_score | spread | pass_rate | s1_run_rate | worst_of_k_s1 | all_k_pass |
|---|---|---|---|---|---|---|---|---|
| all | 10 | 50 | 0.74 | 0.02 | 0.70 | 0.12 | 0.30 | 0.50 |
| cross_lingual | 2 | 10 | 0.92 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| doctrine | 2 | 10 | 0.00 | 0.00 | 0.00 | 0.20 | 0.50 | 0.00 |
| pinpoint | 2 | 10 | 0.94 | 0.05 | 0.90 | 0.00 | 0.00 | 0.50 |
| praxisaenderung | 2 | 10 | 0.96 | 0.07 | 1.00 | 0.00 | 0.00 | 1.00 |
| temporal | 2 | 10 | 0.88 | 0.00 | 0.60 | 0.40 | 1.00 | 0.00 |

## Failures

| check | severity | runs | questions | example |
|---|---|---|---|---|
| unknown_statute | S1 | 4 | 2 | tv-001#1: Art. 49 al. 1 Tit: law_abbreviation_unknown |
| unverified_quote | S1 | 2 | 1 | tv-001#3: « Lorsque le nouveau droit prévoit des délais de prescription plus longs que l'ancien droit, le nouveau droit s'applique dès lors  |
| wrong_answer | S1 | 2 | 1 | rb-024#0: Selon l'art. 221 al. 2 CPP, la détention provisoire pour risque de passage à l'acte exige (i) de forts soupçons que l'auteur ait c |
| incomplete_answer | S2 | 8 | 2 | rb-019#0: not_met: Nach ständiger bundesgerichtlicher Rechtsprechung kann ein verpflichtender Vertrauensschutz nach Art. 9 BV begründet werd |
| missing_authority | S2 | 1 | 1 | pp-001#0: bge_BGE_149_III_105 |
| statute_not_named | S3 | 19 | 6 | pa-001#2: Art. 4 IVG |
| overruled_authority | S3 | 7 | 2 | pa-001#0: bge_BGE_130_V_352 (overruled by BGE 141 V 281 E. 3.5 (Aufgabe der Überwindbarkeitsvermutung; Regeste: Änderung der Rechtsprechung, |
| pinpoint_missing | S3 | 4 | 2 | pa-001#0: bge_BGE_141_V_281: cited ['2.1', '4.1'], gold E. 3.5 |
| cited_without_retrieval | S3 | 1 | 1 | pa-003#0: bger_4A_554_2019 |
| no_closing_attest | S3 | 1 | 1 | tv-001#4: attest_response never called |

## Per question

| question | type | scores | S1 runs | passed runs |
|---|---|---|---|---|
| cl-001 | cross_lingual | 1.00 1.00 1.00 1.00 1.00 | 0 | 5 |
| cl-005 | cross_lingual | 0.83 0.83 0.83 0.83 0.83 | 0 | 5 |
| pa-001 | praxisaenderung | 1.00 1.00 1.00 1.00 0.80 | 0 | 5 |
| pa-003 | praxisaenderung | 1.00 0.83 1.00 1.00 1.00 | 0 | 5 |
| pp-001 | pinpoint | 1.00 1.00 1.00 1.00 1.00 | 0 | 4 |
| pp-003 | pinpoint | 0.80 1.00 0.80 0.80 1.00 | 0 | 5 |
| rb-019 | doctrine | 0.00 0.00 0.00 0.00 0.00 | 0 | 0 |
| rb-024 | doctrine | 0.00 0.00 0.00 0.00 0.00 | 2 | 0 |
| tv-001 | temporal | 0.75 0.75 0.75 0.75 0.75 | 3 | 2 |
| tv-002 | temporal | 1.00 1.00 1.00 1.00 1.00 | 1 | 4 |

Cost: 19.3 USD list price (0.386 per run); 13.4 tool calls per run on average.
