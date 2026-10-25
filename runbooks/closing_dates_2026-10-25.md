# Runbook: bger and ai_gerichte dates from the ruling's own closing line (2026-10-25)

**What is wrong.** Report 2026-10-24 (Adrian König's AI), finding 1:

* `BGer 7B.150/2001` is served "vom 17. Mai 2001", the date of the Lucerne ruling it
  reviews. The ruling is of 14 August 2001 ("Lausanne, 14. August 2001", and the source
  URL `id=14.08.2001_7B.150/2001`). Cause: `scripts/repair_decision_dates.py --all` on
  2026-03-12 took the first "Entscheid vom <date>" of the text as the decision date and
  moved the stored date into `publication_date`. In a BGer ruling the first such date is
  the decision under appeal. 1,206 bger rows, all from the February 2026 bootstrap;
  nothing scraped since is affected (the scraper never rewrites a stored row).
* `Gericht AI K 7-2021` is served "vom 19. Januar 2022", the day ai.ch listed it; the
  ruling is of 16 November 2021 (its own closing line "Kantonsgericht Appenzell I.Rh.,
  Entscheid K 7-2021 vom 16. November 2021"). The scraper dated rows by the listing, and
  the 03-12 pass replaced some of those with a body date (V 10-2023: 2009-05-01). Of the
  rows whose closing line names their own docket, not one agreed with the stored date.

**The fix.**

* `scripts/restore_closing_dates.py` sets `decision_date` to the ruling's own date, with a
  `date_restore` stamp {from, to, method, at} per row:
  * bger: the closing "<seat>, <date>" (Lausanne, Luzern, Bellinzona, Mon-Repos), only when
    it equals `publication_date` (where the 03-12 swap left the true date) and, if the
    source URL carries a date, that one too;
  * ai_gerichte: the last "Entscheid <docket> vom <date>" naming the row's own docket, not
    before the docket's year. The listing date moves to `publication_date` when that is
    empty.
* `scrapers/cantonal/ai_gerichte.py` dates new rows the same way (listing date ->
  `publication_date`).
* `scripts/repair_decision_dates.py` refuses bger and ai_gerichte from now on.

**Measured 2026-10-25** (dry run on the HF mirror's bger and ai_gerichte parquet, i.e. the
served rows):

| court | rows | changed | agrees | no own date |
|---|---|---|---|---|
| bger | 194,022 | 1,206 | 192,521 | 284 (+11 closing ≠ publication_date, left alone) |
| ai_gerichte | 182 | 81 | 0 | 101 (yearbook excerpts and compilations, left alone) |

A second run changes nothing. bger years: 2000–2002 hold about 700 of the 1,206, the rest
spread over 2003–2026. 101 ai_gerichte rows keep their date: the yearbook excerpts (`AI-2015/3`
and siblings) and compilations close with another ruling's line, and some rows are
duplicates under a second id (`kse-2-2025` / `KSE 2-2025`); that clean-up is separate.

## Apply

Window: no writer on `bger.jsonl` (01:00 scrape, 10:20 late scrapers, the hourly
bger-poller, bger-backfill, bger-withdrawals) or `ai_gerichte.jsonl` (01:00 scrape), and
no build. Same window as the Tribuna restore: after the full build exits and before the
next 01:00 scrape, between two bger-poller runs. Check immediately before:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'date -u; systemctl show -p ActiveState --value opencaselaw-publish.service opencaselaw-publish-incremental.service opencaselaw-scrape.service opencaselaw-late-scrapers.service bger-poller.service opencaselaw-bger-backfill.service opencaselaw-bger-withdrawals.service; pgrep -af "build_fts5|run_scraper|publish.py|bger" | grep -v pgrep || echo idle'
```

All `inactive` and `idle`. Then backup and dry run (the `es_` twins are included when they
exist; the script only touches rows of court bger / ai_gerichte):

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'set -e; cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; B=/mnt/HC_Volume_104655575/quarantine/closing-dates-$(date -u +%Y%m%d); mkdir -p $B; S=$(ls $D/bger.jsonl $D/es_bger.jsonl $D/ai_gerichte.jsonl $D/es_ai_gerichte.jsonl 2>/dev/null); for s in $S; do cp -a $s $B/; done; ls -la $B; python3 scripts/restore_closing_dates.py $S --examples 0 2>/dev/null'
```

Expect `rows_changed` about 1,206 for bger and 81 for ai_gerichte (summed over a court's
shards); stop if either is off by more than a few dozen. Then:

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40 'cd /opt/caselaw/repo; D=/mnt/HC_Volume_104655575/output/decisions; S=$(ls $D/bger.jsonl $D/es_bger.jsonl $D/ai_gerichte.jsonl $D/es_ai_gerichte.jsonl 2>/dev/null); python3 scripts/restore_closing_dates.py $S --apply --examples 0 2>/dev/null'
```

Rollback: copy the quarantine backups back; or per row, `date_restore.from`.

## Verify after the next full build

```bash
python3 - <<'PY'
import json, urllib.request
for ref in ("7B.150/2001", "ai_gerichte_K_7-2021", "ai_gerichte_V_10-2023"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "cite", "arguments": {"reference": ref}}}).encode()
    req = urllib.request.Request("https://mcp.opencaselaw.ch/mcp", data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    raw = [z[5:] for z in raw.splitlines() if z.startswith("data:")][-1] if "data:" in raw else raw
    print(ref, json.loads(raw)["result"]["structuredContent"]["citation_string_de"])
PY
```

Expected: "BGer 7B.150/2001 vom 14. August 2001", "Gericht AI K 7-2021 vom 16. November
2021", "Gericht AI V 10-2023 vom 18. Juni 2024".
