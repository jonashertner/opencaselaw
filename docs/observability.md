# Observability — Health Metrics + Synthetic Alerts

What the system collects, where it comes from, what would fire as an
alert, and how to turn dry-run alerts into real notifications after
Monday's PR 1 gate passes.

## Endpoints

| Path | Type | Purpose |
|---|---|---|
| `/health` | JSON | Liveness probe (always-on, public). Returns `{status, decisions, db_generation}`. |
| `/metrics` | JSON | Existing in-process tool counters (latency, calls, error rates, queries). Unchanged by D2. |
| `/metrics/health` | JSON | New (D2). Structured health snapshot + `alerts_dry_run`. Cheap; safe to poll every 30 s. |
| `/dev/health` | HTML | New (D2). Read-only operator dashboard built on `/metrics/health`. |
| `/metrics/history` | JSON | Existing lifetime counters from `metrics.db`. Unchanged. |

## Metrics in `/metrics/health`

| Key | Source | What it means | Default if missing |
|---|---|---|---|
| `ts` | `health_metrics._now()` | Server's wall clock at collection time | — |
| `db_generation` | `mcp_server.get_db_generation()` | Last `PRAGMA user_version` observed (see `db_contract.md`) | `0` |
| `pipeline_last_success_ts` | `mtime(output/decisions.db)` | Last successful atomic swap | `null` |
| `quick_publish_last_run_ts` | `mtime(logs/bger_poller.log)` | Last `bger_poller` wake | `null` |
| `bger_poller_last_run_ts` | Same as above today | Will diverge after A1 lands `quick_publish_metrics.jsonl` | `null` |
| `freshness_seconds_by_court` | `MAX(scraped_at)` per court in `decisions.db` | Per-court age of newest scraped row | `{}` |
| `daily_cost_usd_24h` | Sum of `cost_usd` in `logs/llm_usage.jsonl` (last 24h) | LLM spend | `0.0` |
| `alerts_dry_run` | `health_alerts.check_all()` | Would-fire alerts; never actually fire today | `[]` |

### Why `scraped_at` for freshness, not `ingest_ts`?

`scraped_at` records when a scraper grabbed the row, not when it
landed in the published DB. For "publication-to-MCP visible" we
want a true `ingest_ts` column — not in the schema yet (planned for
the Saturday A6 deploy). Until then `scraped_at` is the best signal
we have; per the v2 plan's edit, this gap is documented, not
worked around.

## Alert rules (currently dry-run)

| Key | Level | Condition | Threshold |
|---|---|---|---|
| `pipeline_stale` | critical | `pipeline_last_success_ts` older than threshold | 26 h |
| `quick_publish_stale` | warning | On a weekday (UTC), `quick_publish_last_run_ts` older than threshold | 2 h |
| `mcp_error_rate_high` | warning | `sum(tool_errors) / sum(tool_calls)` over the in-process counters | 1% (min 100 samples) |
| `pipeline_unknown` | warning | `pipeline_last_success_ts` is `null` | — |
| `quick_publish_unknown` | warning | On a weekday, `quick_publish_last_run_ts` is `null` | — |

Source: `health_alerts.py`. Each rule is wrapped in a try/except so
a bug in one cannot suppress the others — the failing rule is
itself reported as `<rule>_error`.

### Why these thresholds?

- **26 h pipeline staleness**: catches a missed nightly with one
  margin hour. Currently every weekday runs `quick_publish` (mtime
  bump) and Sunday runs full rebuild — both visible via the same
  signal.
- **2 h quick_publish staleness on weekday**: `bger-poller.timer`
  fires every 15 min Mon-Fri 05:00–16:45 UTC. A gap longer than 2 h
  in that window indicates the poller is wedged.
- **1% MCP error rate**: in normal operation the rate is <0.1%.
  Anything sustained above 1% indicates a real malfunction (DB
  unreachable, downstream API down, schema mismatch).

## How alerts become real

The Monday gate (after PR 1 transition lands) determines whether
to wire alerts to a notifier. Sequence:

1. **Monday morning**: verify the PR 1 contract works in production
   (see `runbooks/db_generation_mismatch.md` and the Monday gate
   checklist below).
2. **If the gate passes**: open a follow-up PR (D2b) that adds a
   notifier — push notification, email, or webhook. Wire `check_all`
   results into it; deduplicate via `(key, level, day)`.
3. **If the gate fails**: pause D2, fix PR 1 first.

### Monday gate checklist

The first `bger_poller` wake (~05:00 UTC Monday) is the first
production exercise of the `db_generation` contract. Verify:

- [ ] `/health` reports `db_generation != 0` on all 4 workers
- [ ] Each worker logs exactly one `db_generation transitioned 0 → <ts>`
- [ ] `_query_cache` clear happens without an error spike
- [ ] `decisions` count is stable or increases only by the row count
      reported by `quick_publish`
- [ ] `/api/billing/reflect` and `/search_decisions` smoke pass

If all five hold: deploy notifier wiring. If any fails: pause and
debug per `runbooks/db_generation_mismatch.md`.

## Operating

- `/metrics/health` is safe to poll at 30 s cadence (the freshness
  query is `MAX(scraped_at)` grouped by court, ~50 ms on the
  current DB).
- `/dev/health` is a static HTML page that polls the JSON every
  30 s — keep it open during deploys.
- For external monitoring: use `/health` for uptime, `/metrics/health`
  for structured drift signals. Both return JSON; both work without
  authentication.

## Adding a new alert rule

1. Write `check_<name>(health, now=None)` in `health_alerts.py`.
   Return `None` for clear or `dict{level, key, message, ...}`.
2. Register it in `check_all()`'s rule tuple.
3. Add a unit test in `tests/test_health_metrics.py`.
4. Document in the threshold table above.

## Per-court intake rate (manual invocation, 2026-09)

`scripts/check_intake_rates.py` is the corpus-side complement to
`check_scraper_freshness.py`: the scraper monitors watch the *process*
(exit code, portal answer, `our_count` movement), this one watches the
*rows*. It reads `docs/coverage.json` (per-court `by_year`, regenerated by
publish step 5g) and compares the current year's decision-dated rows with
the pro-rated mean of the previous two full years:

| ratio | status | effect |
|---|---|---|
| `< 0.25` | `STALE` | exit 1 unless the court is in `expected_stale` |
| `< 0.60` | `LOW` | listed, never fails |
| both previous years at 0 rows | `STALE` ("no rows since YYYY") | the ow_gerichte shape |
| window under 30 days after `lag_days` | `WAIT` | not judged (bge) |

Exemptions live in `docs/intake_expected_stale.json` (`expected_stale`, with a
reason per court) — courts that are silent **by design**: abolished
(zh_kassationsgericht), ended series (emark, ch_vb), no new volumes (mkg).
A `docs/coverage_notes.json` note is printed as context but does not exempt:
ow_gerichte and be_steuerrekurs are noted outages that are "re-probed
weekly", and the check exists precisely to keep them visible until the
probe yields rows. The same file carries `lag_days` for series whose
decision dates trail publication by months (bge: volume 152 holds 104
decisions, 28 of them dated 2026 on 2026-09-09 — the official collection
publishes 6-12 months after judgment; 270 days, judged from Q4, and covered
until then by the `our_count` stall detector in `check_scraper_freshness.py`).

Courts under 100 rows, and courts whose pro-rated expectation is under 5
rows, are `SMALL` and not judged; a court with rows this year and none in
the two previous years is `NEW`.

Run it by hand this week — no unit or timer yet (frozen this week with the
rest of the pipeline); wire a weekly timer next to
`opencaselaw-health-alerts.timer` once the flagged list has been triaged:

```
python3 scripts/check_intake_rates.py --only-flagged      # table + exit code
python3 scripts/check_intake_rates.py --json               # machine-readable
python3 scripts/check_intake_rates.py --notify             # opt-in: one ntfy line
python3 scripts/check_intake_rates.py --as-of 2026-09-09 --coverage /path/coverage.json
```

`--notify` posts through the same urllib POST `dispatch_health_alerts.send`
uses (topic from `NTFY_URL` / `NTFY_TOPIC` in `/opt/caselaw/ops.env`,
fallback `opencaselaw-scrapers`), dedup-on-change with a 24 h re-nag and one
all-clear on recovery; state in `logs/intake_rates_state.json`. The default
`--as-of` is `coverage.json`'s `generated_at`, so an old snapshot is judged
against its own date. `STALE` on an umbrella court code (`ag_gerichte`,
`zh_gerichte`, `sg_publikationen`) can also mean the code repair moved this
year's rows to finer codes — check `scraper_health.json` `our_count` for the
scraper of the same name before calling the feed dead.

## Non-goals (in this phase)

- No external push, email, or webhook notification. Dry-run only.
- No persistence of alert history. The notifier PR will add that.
- No cross-worker aggregation. Each worker reports its own state.
- No timer-driven alerting on freshness-per-court yet (the manual
  intake-rate check below covers it this week). Will come once A6 lands
  `ingest_ts` and we have a defensible threshold per court.
