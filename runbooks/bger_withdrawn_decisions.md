# Withdrawn BGer decisions — detection and handling

Status: script, tests, unit and timer written 2026-09-17; **not yet installed
on the VPS** (see §5). Policy basis: `docs/governance-and-removal-policy.md`,
section "Source withdrawals and re-anonymization".

## 1. What happens at the source

The Federal Supreme Court sometimes withdraws a decision after publishing it
on search.bger.ch. The document URL then answers HTTP 200 with the site chrome
and an embedded

    <HTML><HEAD><TITLE>Dokument nicht gefunden</TITLE></HEAD>
    <BODY>Dieser AZA-Entscheid ist in elektronischer Form nicht verfügbar.</BODY></HTML>

(the message is German for `lang=de|fr|it`, charset iso-8859-1), and the docket
disappears from the AZA search index. We keep serving our copy until someone
acts.

Known cases (2026):

| docket | decided | listed | fate |
|---|---|---|---|
| 6B_499/2026 (FR) | 27.08 | 11.09 | scraped 11.09 10:01 UTC, withdrawn by 16.09, still withdrawn 17.09 |
| 7B_723/2026 | | 31.07 | withdrawn, relisted 05.08 |
| 4F_14/2026 | | 10.08 | withdrawn, relisted 17.08 |

A withdrawal is therefore **not always permanent**, and a relist may carry a
corrected decision date (then the scraper gives it a dated id
`bger_<docket>-D<YYYYMMDD>`) or corrected text under the same id (which the
scraper skips as already known — a re-sync is a manual step today).

Two look-alikes that are *not* withdrawals:

- On a batch day the document service answers "nicht gefunden" for 30–90 min
  after the Neuheiten listing (bger_poller.py, BURST_RETRIES). The check skips
  rows younger than 12 h and needs two distinct not-found days.
- "Die Anfrage des Document Dienstes ist fehlgeschlagen" is the document
  service being down (or relevancy.bger.ch lagging). Classified
  `service_error`, transient, retried next run.

## 2. The check

`scripts/check_bger_withdrawn.py`, daily via
`systemd/opencaselaw-bger-withdrawals.timer` (13:30 UTC).

1. Candidates: BGer rows scraped in the last 21 days, from the tail of
   `output/decisions/bger.jsonl` (bounded byte tail, 32 MB default — the
   production file is 1.5 GB but 64 MB reach back three months). Never opens
   `decisions.db`.
2. Each due row is re-fetched at its search.bger.ch document URL through the
   real `BgerScraper` request path (tunnel, Incapsula, PoW, CA bundle). New
   rows are checked at once, not-found rows daily, healthy rows every 3 days.
   Request cap 300 per run, ~2 s per request.
3. Ledger `logs/bger_withdrawal_state.json`: per id the first/last not-found
   observation, the distinct days, the last present observation, when it was
   last checked, and an event log. Transient classes never advance
   `last_checked`.
4. **Removal candidate** = not-found streak ≥ 10 days old, seen on ≥ 2
   distinct days, no `present` since. For those only, the AZA docket search
   (page 1) is fetched once as a second witness, read through the
   `highlight_docid` of each hit (the hit count is useless: it counts citing
   decisions):
   - docket absent → `withdrawn` (de-listing / removal candidate)
   - docket under a different date → `replaced` (re-sync candidate; the
     dated relist id is printed)
   - docket under the same date → `listed` (inconclusive; the doc page and
     the index disagree — look by hand)
5. Report `logs/bger_withdrawal_candidates.json` + table on stdout
   (`logs/bger_withdrawals.log`). One ntfy message per newly matured
   candidate set (`NTFY_TOPIC` from `/opt/caselaw/ops.env`), one informational
   line if an alerted candidate is relisted.

If the first three fetches all fail (tunnel down) the run aborts, writes
nothing and exits 0 with one log line.

The script never deletes, de-lists or rewrites anything.

## 3. Confirm by hand before acting

```
# the check's own view, no writes, no ntfy
python3 scripts/check_bger_withdrawn.py --decision-id bger_6B_499_2026

# a row that is no longer in the JSONL tail
python3 scripts/check_bger_withdrawn.py --docket 6B_499/2026 --decision-date 2026-08-27

# raw, from any machine with direct egress (Latin-1 page; grep the ASCII part)
curl -s 'https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?lang=de&type=show_document&highlight_docid=aza%3A%2F%2F27-08-2026-6B_499-2026' | grep -c 'Dokument nicht gefunden'

# index witness: does a hit carry OUR docket in highlight_docid?
curl -s 'https://search.bger.ch/ext/eurospider/live/de/php/aza/http/index.php?lang=de&type=simple_query&query_words=6B_499%2F2026&top_subcollection_aza=all' | grep -o 'highlight_docid=[^&"]*' | sort -u
```

Also check the relevancy mirror and entscheidsuche.ch (`scrapedate`) if the
publication history matters; see the memory note
`bger-aza-insertion-date-and-withdrawals`.

## 4. Acting on a candidate (maintainer decision)

Per the policy the outcome is one of: update metadata, re-sync, de-list from
public artifacts, remove from future releases, or decline. Nothing here is
automated on purpose.

- **withdrawn, confirmed for ≥ 10 days**: the usual outcome is de-listing.
  There is no de-listing lever in the build; the current manual lever is to
  remove the row from `output/decisions/bger.jsonl` (and its id from
  `state/bger.jsonl` plus the `state/bger.dates.txt` pair) in the 00:20–03:30
  UTC window, so a later relist is re-discovered as a new publication. What
  that does downstream (verified in `build_fts5.import_jsonl`, 2026-09-17):
  the checkpointed incremental import sees a file that shrank, logs
  "size shrank … reading from start" and re-reads the whole 1.5 GB shard
  (slower that night, otherwise harmless); neither the incremental import
  nor quick_publish deletes a served row that vanished from the JSONL. The
  row disappears from `decisions.db` only at the next **full** rebuild —
  the next night while fulls are nightly, up to a week once Sunday-only fulls
  are in force — and from the HF / Pages mirrors with the upload that
  follows. Record the decision (docket, date, evidence) in the commit
  message or the issue tracker.
- **replaced**: the relist is the authoritative text. Check whether the
  scraper already holds the dated id; if the same id was relisted with
  changed text, re-scrape it by hand (`scripts/rescrape_bger_errors.py`
  pattern) — the scraper does not re-fetch known ids.
- **listed** (inconclusive) or **watch** rows (< 10 days): wait; the check
  re-tests daily and clears the streak on its own when the decision returns.

Keep the ledger: after a manual removal the id leaves the JSONL window and is
pruned automatically.

## 5. Installation on the VPS (Jonas)

Unit files are in the repo; the install itself is outside the session's
permission boundary.

```
cd /opt/caselaw/repo && git merge --ff-only origin/main   # once the change is on main
sudo cp systemd/opencaselaw-bger-withdrawals.service systemd/opencaselaw-bger-withdrawals.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/opencaselaw-bger-withdrawals.service.d
sudo cp systemd/opencaselaw-bger-withdrawals.service.d/*.conf /etc/systemd/system/opencaselaw-bger-withdrawals.service.d/
sudo systemctl daemon-reload
sudo systemctl enable --now opencaselaw-bger-withdrawals.timer
# first run by hand, dry, to see the window and the classifications
sudo systemctl start opencaselaw-bger-withdrawals.service   # or:
python3 scripts/check_bger_withdrawn.py --dry-run --no-ntfy
```

First real run: if 6B_499/2026 (scraped 11.09) is still inside the 21-day
window it starts a not-found streak on that run; the streak is counted from
the first run, not from the withdrawal, so the first alert comes ten days
after the first run (two distinct not-found days are needed as well). Expect
the `WARN ... raise --tail-mb` line never; if it appears, the JSONL grew
faster than 32 MB / 21 days.

## 6. Tests

`tests/test_check_bger_withdrawn.py` — golden pages captured 2026-09-17
(`tests/fixtures/bger_aza_*`): the not-found page verbatim, a live decision
page and two docket-search pages trimmed to three hits. Offline; an autouse
guard makes any live request fail and neutralises `send_ntfy`.
`tests/test_ntfy_topic_unification.py` covers the topic env.
