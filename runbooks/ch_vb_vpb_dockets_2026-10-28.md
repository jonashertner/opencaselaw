# Runbook: ch_vb citations, item numbers and languages (2026-10-28)

**What is wrong.** After the VPB-only filter went live (10-28 swap, 2,502 rows):

* Every ch_vb row cites through the cantonal fallback of `_build_citation_strings`:
  `Gericht CH JAAC 64.104 vom 17. Mai 2000`. The series is not a court, and the German
  and Italian titles are VPB and GAAC. Worse for R1: `cite()` resolves neither
  `VPB 64.104` nor its own string `Gericht CH JAAC 64.104 vom 17. Mai 2000`.
* The 116 rows of 2007-2017 carry the portal id as docket (`Gericht CH 150000032`),
  although each PDF prints its item number in the header (`2007.1 (S. 1–8)`).
* All 2,502 rows are tagged `language: de`; about 1,150 are French and 55 Italian.

**The fix** (branch `claude/ch-vb-citations`).

* `mcp_server.py`: a ch_vb branch cites `VPB <n>` / `JAAC <n>` / `GAAC <n>` with the
  usual date and pinpoint; a row without an item number is labelled
  `Bundesverwaltung <portal id>`. `_vpb_docket()` maps any of the three titles, bare or
  inside a server string (old `Gericht CH …` strings included), to the stored
  `JAAC <n>`; used by `_resolve_decision_id`, `_resolve_decision_id_strict`
  (attest_response) and `_cite_identity`. Serving only: live after the worker restart.
* `scripts/ch_vb_vpb_dockets.py` repairs the shard: `docket_number = "JAAC <n>"` from the
  header for the portal-id rows, `language` where one language's function words clearly
  dominate the full text. decision_id and row count unchanged, so no swap-gate exemption.

**Measured 2026-10-28** (dry run on a copy of the live shard, sha256 `5b31110d…`):

| | rows |
|---|---|
| docket set from the header (2007.1 … 2017.2, incl. 2007.3.1/.2, 2009.10a/b) | 109 |
| left as portal id: number claimed twice | 6 |
| left as portal id: no number (150000191, annex "Ergänzung zu Beitrag 2009.6") | 1 |
| language de → fr | 882 |
| language de → it | 65 |
| rows changed | 1,013 of 2,502 |

The six collisions, for a later reading by hand: 150000176/179 both print `2009.1`
(German and French edition of one item), 150000242/245 both `2011.3` (same), and
150000182/188 both `2009.3`, where 188 (S. 48-56, "Geobasisdaten zu Rohrleitungen") is
probably a misprint for 2009.5.

## Steps

1. Deploy the code (commit, push, `git merge --ff-only origin/main` on the VPS,
   `scripts/rolling_restart_workers.sh`). Check: `cite("VPB 64.104")` returns
   `VPB 64.104 vom 17. Mai 2000`, and `cite("Gericht CH JAAC 64.104 vom 17. Mai 2000")`
   resolves to `ch_vb_JAAC_64.104__`.
2. In the window after a build exits and before 01:00 UTC, on the VPS:

   ```
   cd /opt/caselaw/repo
   python3 scripts/ch_vb_vpb_dockets.py           # must print the table above
   python3 scripts/ch_vb_vpb_dockets.py --apply   # backup: es_ch_vb.jsonl.bak-pre-vpb-dockets
   python3 scripts/ch_vb_vpb_dockets.py           # second dry run: 0 to change
   ```

   Stop if the dry run differs from the table (the shard changed since 10-28).
3. After the next full build swaps: `cite("VPB 2007.1")` → `ch_vb_150000032`,
   `VPB 2007.1 vom 4. Dezember 2006`; `search_decisions(court="ch_vb", language="fr")`
   returns JAAC items; the per-court gate line shows ch_vb at 2,502.

**Rollback.** Restore `es_ch_vb.jsonl.bak-pre-vpb-dockets` over the shard; the next build
serves the old dockets and tags. The serving change stays correct either way.
