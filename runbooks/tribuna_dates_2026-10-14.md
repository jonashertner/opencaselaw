# Runbook: Tribuna decision dates from the ruling's own heading (2026-10-14)

**What is wrong.** Until 2e46549d (2026-09-27) `base_tribuna` read a row's date from the
deduplicated GWT-RPC string table, so rows got another row's date, the createDate or the
Rechtskraft date. The parser is fixed; rows stored before the fix kept the old value
(the scraper never rewrites a stored row). The entscheidsuche copies (`es_*`) carry the
same wrong dates. Reported 2026-10-13: `fr_gerichte_604 2026 58` served 2026-10-26 for an
"Arrêt du 1er septembre 2026".

**The fix.** `scripts/restore_tribuna_dates.py` sets `decision_date` to the date in the
ruling's own heading ("Urteil der Einzelrichterin vom 11. November 2025", "Arrêt du 1er
septembre 2026", "Sentenza del 18 marzo 2026") for rows scraped before the fix, with a
`date_restore` stamp {from, to, method, at} per row. Guards: the heading must stand before
the parties block and may carry only designation qualifiers between noun and date; the
date must not precede the docket year or lie in the future; for FR the closing "Fribourg,
le …" must agree. Rows scraped after the fix are never changed.

**Measured 2026-10-14** (dry run on the live shards; 22 sampled changes read against the
text, all correct; local test build of the eight shards before/after):

| court | direct rows | es rows | served dates corrected | served rows |
|---|---|---|---|---|
| be_verwaltungsgericht | 3,687 | 3,608 | 3,877 | 12,186 → 12,181 |
| fr_gerichte | 533 | 11 | 402 (future 3 → 1) | 14,827 → 14,828 |
| gr_gerichte | 349 | 187 | 380 | 15,867 → 15,866 |
| zg_obergericht | 94 | 1 | 95 (NULL 24 → 0) | unchanged |

be_zivilstraf changes nothing (its documents state the date only in the closing "Berne,
le …"). The 12 ids that drop out are underscore-form es duplicates whose space-form twin
survives with the same date; the old ids keep resolving through the separator variants.
Swap gate: no court loses more than 0.1 %, no override needed.

**Both shards of a court go together.** Repairing only the direct shard splits 2,212
be_verwaltungsgericht pairs that the dedup folds today, and the es copy would come back as
a separate row with the wrong date.

## Apply

Window: after the 01:00 scrape has logged `Done.` for these courts and the incremental
build has exited, before the 03:30 full build; or after the full build exits and before
the next 01:00 scrape. Check immediately before each step:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'date -u; systemctl show -p ActiveState --value opencaselaw-publish.service opencaselaw-scrape.service opencaselaw-publish-incremental.service; pgrep -af "build_fts5|run_scraper|publish.py" | grep -v pgrep || echo idle'
```

All three `inactive` and `idle`, then backup, dry run, apply (one call):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; B=/mnt/HC_Volume_104655575/quarantine/tribuna-dates-$(date -u +%Y%m%d); mkdir -p $B; for s in be_verwaltungsgericht es_be_verwaltungsgericht fr_gerichte es_fr_gerichte gr_gerichte es_gr_gerichte zg_obergericht es_zg_obergericht; do cp -a $D/$s.jsonl $B/; done; ls -la $B; python3 scripts/restore_tribuna_dates.py $D/{,es_}{be_verwaltungsgericht,fr_gerichte,gr_gerichte,zg_obergericht}.jsonl --examples 0 2>/dev/null'
```

Expect `rows_changed` per shard as in the table (direct 3,687 / 533 / 349 / 94; es 3,608 /
11 / 187 / 1); stop if any differs by more than a few rows (new rows since 10-14 are
post-fix and never change). Then:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; python3 scripts/restore_tribuna_dates.py $D/{,es_}{be_verwaltungsgericht,fr_gerichte,gr_gerichte,zg_obergericht}.jsonl --apply --examples 0 2>/dev/null'
```

Backups go to the quarantine folder (outside `output/decisions/`, so the build never
imports them). Rollback: copy them back; or per row, `date_restore.from`.

## Verify after the next full build

```bash
for id in "fr_gerichte_604%202026%2058" "fr_gerichte_605%202025%2094" "be_verwaltungsgericht_200%202025%20737"; do curl -s "https://mcp.opencaselaw.ch/api/decisions/$id" | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("decision",d); print(d.get("decision_id"), d.get("decision_date"))'; done
```

Expected: 2026-09-01, 2026-09-10, 2025-11-11.
