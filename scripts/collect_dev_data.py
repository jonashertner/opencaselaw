#!/usr/bin/env python3
"""Collect R&D datasets into the private data repo.

Runs daily on the VPS after the publish pipeline exits, gathers the
system-side data streams into /opt/caselaw/data-private (a clone of the
private opencaselaw-data repo), refreshes each dataset's card, and
commits. The goal is training-grade datasets with schemas and
provenance, not a log dump — every dataset directory carries a CARD.md
with the schema, the generating code path, and whether the data may ever
leave the private repo.

What it deliberately does NOT collect (yet): search_traces_*.jsonl.
Those files carry rerank query text under a published 30-day-deletion
promise; exporting them — even stripped — waits until the /datenschutz/
amendment (Phase 0c) ships. Nothing is lost by waiting: the server keeps
30 days, so the collector can backfill the window the day the amendment
lands. `--include-traces` exists for exactly that day and refuses to run
while the guard file is absent.

Privacy is enforced structurally, not by care: every record written is
scanned for identifier keys (IP, user agent, session id, cohort) and the
run ABORTS on the first hit. ONE dataset is exempt by explicit decision
(2026-08-23): the per-IP LLM cost ledger, archived raw — see
IDENTIFIER_EXEMPT_DATASETS for the disclosure trail. The collector only
reads; it never deletes or modifies a source.

Usage (VPS, via systemd timer; also runnable by hand):
    python3 scripts/collect_dev_data.py --dest /opt/caselaw/data-private
    python3 scripts/collect_dev_data.py --dest ... --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("collect_dev_data")

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from metric_tools import UNKNOWN_TOOL, metric_tool_name  # noqa: E402

# Keys that must never appear in an exported record, at any nesting depth.
FORBIDDEN_KEYS = {"ip", "client_ip", "remote_addr", "user_id", "session_id",
                  "sid", "ua", "user_agent", "cohort", "install_cohort",
                  "email", "stripe_customer"}

# The ONE dataset exempt from the identifier scan: the per-IP LLM cost
# ledger, archived RAW (incl. IP addresses) into the private repo by
# explicit owner decision, 2026-08-23. Disclosed at /datenschutz/
# ("Kosten- und Missbrauchskontrolle": 90 days on the serving systems
# plus an access-restricted private archive for the lifetime of the
# service). Everything else stays structurally identifier-free — adding
# a name here is a privacy-notice amendment, not a code change, and the
# test suite pins this set to exactly one member.
IDENTIFIER_EXEMPT_DATASETS = {"llm_cost_by_ip"}

# Ledger records written BEFORE the /datenschutz/ amendment was live were
# collected under the plain 90-day promise and are never archived — they
# age off the VPS as promised. The amendment ships during 2026-08-23, so
# the archive starts at the first full post-amendment day; the boundary
# is a day, not a push-moment, precisely so it cannot be argued about.
LEDGER_ARCHIVE_SINCE = "2026-08-24"

# The 0c guard: trace export stays off until the amendment is live. The
# deploy step that publishes the amended /datenschutz/ creates this file;
# its content is the amendment's publication date.
TRACES_ALLOWED_MARKER = "TRACES_EXPORT_APPROVED"


def _scan(obj, path="$"):
    """Abort-on-identifier scan, recursive. Returns list of violations."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in FORBIDDEN_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(_scan(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            hits.extend(_scan(v, f"{path}[{i}]"))
    return hits


# The per-tool counters _get_metrics() writes under `tools`. They add up;
# avg_ms and the percentiles do not, so a folded bucket carries no latency.
_TOOL_COUNTS = ("calls", "errors", "substantive", "empty",
                "outcome_declared", "outcome_from_status")
_TOOL_STAT_KEYS = {*_TOOL_COUNTS, "avg_ms", "p50_ms", "p95_ms", "empty_reasons"}


def _is_tool_stats(stats) -> bool:
    """True only for exactly what _get_metrics() writes for one tool: known
    counters holding numbers, and empty_reasons mapping text to a count."""
    if not isinstance(stats, dict) or not stats.keys() <= _TOOL_STAT_KEYS:
        return False
    reasons = stats.get("empty_reasons", {})
    return (isinstance(reasons, dict)
            and all(isinstance(n, (int, float)) for n in reasons.values())
            and all(isinstance(v, (int, float))
                    for k, v in stats.items() if k != "empty_reasons"))


def _fold_unknown_tools(rec: dict) -> dict:
    """Give a daily_metrics record the shape the server writes now: counts
    under a name off the metric_tools list move into UNKNOWN_TOOL, and the
    name is dropped.

    Lines written before the server bucketed these names still hold whatever
    a client put in a URL, and a worker keeps writing them until it restarts,
    so `tools.email` from one request to /api/email would otherwise abort every run
    while such a line sits in the window. _scan does not change. Only a value
    of the exact counter shape folds, and its empty_reasons keys land in the
    bucket where the scan still reads them; any other value stays where it
    is, so `tools.ip = "1.2.3.4"` or an identifier nested under an unknown
    name still aborts.
    """
    tools = rec.get("tools")
    if not isinstance(tools, dict):
        return rec
    strays = {name: stats for name, stats in tools.items()
              if name != UNKNOWN_TOOL
              and metric_tool_name(name) == UNKNOWN_TOOL
              and _is_tool_stats(stats)}
    bucket = tools.get(UNKNOWN_TOOL, {})
    if not strays or not _is_tool_stats(bucket):
        return rec
    bucket = dict(bucket)
    reasons = dict(bucket.get("empty_reasons", {}))
    for stats in strays.values():
        for key in _TOOL_COUNTS:
            bucket[key] = bucket.get(key, 0) + stats.get(key, 0)
        for reason, count in stats.get("empty_reasons", {}).items():
            reasons[reason] = reasons.get(reason, 0) + count
    bucket["empty_reasons"] = reasons
    kept = {name: stats for name, stats in tools.items()
            if name not in strays}
    return {**rec, "tools": {**kept, UNKNOWN_TOOL: bucket}}


class Collector:
    def __init__(self, dest: Path, dry_run: bool = False):
        self.dest = dest
        self.dry = dry_run
        self.today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        self.manifest: list[dict] = []

    # ── plumbing ─────────────────────────────────────────────────────

    def _write_jsonl_gz(self, dataset: str, records: list[dict],
                        schema_note: str, private_only: bool = True) -> None:
        """One dated, compressed shard per day per dataset + a card."""
        if not records:
            log.info("[%s] nothing to collect", dataset)
            return
        if dataset not in IDENTIFIER_EXEMPT_DATASETS:
            for rec in records:
                bad = _scan(rec)
                if bad:
                    raise SystemExit(
                        f"PRIVACY ABORT: identifier key(s) {bad} in dataset "
                        f"'{dataset}' — nothing was written")
        d = self.dest / "datasets" / dataset
        out = d / f"{self.today}.jsonl.gz"
        if self.dry:
            log.info("[%s] dry-run: %d records -> %s", dataset, len(records), out)
            return
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._card(d, dataset, schema_note, private_only)
        self.manifest.append({"dataset": dataset, "file": str(out.name),
                              "records": len(records)})
        log.info("[%s] %d records -> %s", dataset, len(records), out.name)

    def _copy_file(self, dataset: str, src: Path, schema_note: str,
                   dated: bool = True, compress: bool = False) -> None:
        """Snapshot-copy a whole artifact (DB, JSONL) without parsing it."""
        if not src.exists():
            log.info("[%s] source absent: %s", dataset, src)
            return
        d = self.dest / "datasets" / dataset
        name = f"{self.today}-{src.name}" if dated else src.name
        if compress:
            name += ".gz"
        if self.dry:
            log.info("[%s] dry-run: copy %s -> %s", dataset, src, d / name)
            return
        d.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".db":
            # A consistent snapshot of a possibly-live SQLite file.
            tmp = d / (name.removesuffix(".gz") + ".tmp")
            con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                dst = sqlite3.connect(tmp)
                con.backup(dst)
                dst.close()
            finally:
                con.close()
            if compress:
                with open(tmp, "rb") as fi, gzip.open(d / name, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                tmp.unlink()
            else:
                tmp.rename(d / name)
        elif compress:
            with open(src, "rb") as fi, gzip.open(d / name, "wb") as fo:
                shutil.copyfileobj(fi, fo)
        else:
            shutil.copy2(src, d / name)
        self._card(d, dataset, schema_note, True)
        self.manifest.append({"dataset": dataset, "file": name,
                              "records": None})
        log.info("[%s] copied %s", dataset, name)

    def _card(self, d: Path, dataset: str, schema_note: str,
              private_only: bool) -> None:
        """Refresh the dataset card: hand-written schema, generated stats."""
        shards = sorted(p.name for p in d.glob("*.jsonl.gz"))
        card = d / "CARD.md"
        card.write_text(
            f"# {dataset}\n\n"
            f"{schema_note}\n\n"
            f"- **May leave the private repo:** "
            f"{'no' if private_only else 'yes — see licence note above'}\n"
            f"- **Generated by:** `scripts/collect_dev_data.py` "
            f"(source paths in the collector)\n"
            f"- **Shards:** {len(shards)}"
            + (f" ({shards[0]} .. {shards[-1]})" if shards else "")
            + f"\n- **Card refreshed:** {self.today}\n",
            encoding="utf-8")

    @staticmethod
    def _read_jsonl(path: Path, since: str | None = None) -> list[dict]:
        if not path.exists():
            return []
        out = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since and str(r.get("ts") or r.get("flushed_at") or "") < since:
                    continue
                out.append(r)
        return out

    # ── the day-one datasets ─────────────────────────────────────────

    def collect(self) -> None:
        yesterday = (_dt.datetime.now(_dt.timezone.utc)
                     - _dt.timedelta(days=1)).strftime("%Y-%m-%d")

        # Pipeline runs: the full history is small; ship the whole file's
        # NEW rows each day (dedup by (run_id, type, step) on the consumer
        # side is trivial; shards are per-day already).
        self._write_jsonl_gz(
            "publish_runs",
            self._read_jsonl(REPO / "state" / "publish_runs.jsonl",
                             since=yesterday),
            "One record per pipeline step and per run "
            "(`type`: run_start | step | run_summary). Step records carry "
            "`status` (ok|failed|exception|skipped_cascade) and `elapsed_s`; "
            "summaries carry `outcome`, `total_s`, `failed_steps`. "
            "System telemetry only — no user data by construction.")

        # Daily metrics: aggregate counters incl. outcome provenance.
        self._write_jsonl_gz(
            "daily_metrics",
            [_fold_unknown_tools(r) for r in self._read_jsonl(
                REPO / "output" / "research_logs" / "daily_metrics.jsonl",
                since=yesterday)],
            "Periodic `_get_metrics()` snapshots: per-tool calls, errors, "
            "latency percentiles, substantive/empty outcomes, "
            "outcome_declared vs outcome_from_status, empty_reasons. "
            "Calls under a name that is no known tool (scanner paths, "
            "invented tool names) are counted in `_unknown`. "
            "Aggregates only; no query content, no identifiers.")

        # LLM spend: cost per feature, no prompt/completion text.
        self._write_jsonl_gz(
            "llm_usage",
            self._read_jsonl(REPO / "logs" / "llm_usage.jsonl",
                             since=yesterday),
            "One record per LLM call: model, feature, token counts, "
            "cost_usd, ok. No prompt or completion text.")

        # Per-IP LLM cost ledger: the sole RAW identifier-bearing archive
        # (see IDENTIFIER_EXEMPT_DATASETS). The serving copy rotates out at
        # 90 days (logrotate maxage); these shards are the permanent record.
        # First run bootstraps from LEDGER_ARCHIVE_SINCE, then daily
        # increments (consumer dedups on ts — µs-precision ISO — + ip,
        # like every other overlapping stream).
        ledger_dir = self.dest / "datasets" / "llm_cost_by_ip"
        self._write_jsonl_gz(
            "llm_cost_by_ip",
            self._read_jsonl(
                REPO / "logs" / "llm_cost_by_ip.jsonl",
                since=yesterday if ledger_dir.exists() else LEDGER_ARCHIVE_SINCE),
            "RAW per-IP LLM cost ledger: ts, ip, feature, source (mcp|rest), "
            "cost_usd — one record per edge-originated LLM call. CONTAINS IP "
            "ADDRESSES: the single dataset exempt from the identifier scan, "
            "by owner decision 2026-08-23, disclosed at /datenschutz/ "
            "('Kosten- und Missbrauchskontrolle': 90 days on serving "
            "systems + this access-restricted private archive). NEVER "
            "leaves the private repo.")

        # Scraper health: today's snapshot, kept per-day (the source file
        # is overwritten every run, so history exists only here).
        for name in ("scraper_health.json", "scraper_health_federal.json",
                     "practice_health.json"):
            src = REPO / "logs" / name
            if src.exists():
                try:
                    payload = json.loads(src.read_text())
                except Exception:
                    continue
                self._write_jsonl_gz(
                    f"scraper_health/{name.removesuffix('.json')}",
                    [{"date": self.today, "snapshot": payload}],
                    "Per-day snapshot of the scraper health report (the "
                    "source file is overwritten each run; the trend lives "
                    "only in these shards). Court-level counts and errors; "
                    "no user data.")

        # Quality: history DB snapshot + all per-run reports.
        self._copy_file("quality_history",
                        REPO / "quality" / "history.db",
                        "SQLite snapshot of quality/history.db: one row per "
                        "check per run (measurements + run_log). Corpus "
                        "quality only.")
        reports = REPO / "quality" / "reports"
        if reports.exists():
            recs = []
            for p in sorted(reports.glob("*.json")):
                if p.name == "latest.json":
                    continue
                try:
                    recs.append({"file": p.name,
                                 "report": json.loads(p.read_text())})
                except Exception:
                    continue
            self._write_jsonl_gz(
                "quality_reports", recs,
                "Per-run QC reports (scope: full | critical_only | subset). "
                "Check results over the corpus; no user data.")

        # Benchmarks: every current artifact, dated.
        bench = REPO / "benchmarks"
        if bench.exists():
            for p in sorted(bench.glob("*.json")) + sorted(bench.glob("*.jsonl")):
                self._copy_file("benchmarks", p,
                                "Search-quality and citation benchmark "
                                "artifacts as of the shard date. Authored "
                                "test queries, not user queries.")

        # Corpus provenance already produced by other work.
        self._copy_file("cantonal_law_names",
                        REPO / "output" / "cantonal_abbreviations.jsonl",
                        "Harvested cantonal law names: canton, sr_number, "
                        "abbreviation, short_title, qualified (ZH/StG), "
                        "source provenance. Public-record law metadata.")

        # Scraper provenance: coverage.db is ~600 MB — a daily snapshot
        # would sink a git repo in a week. Weekly (Sunday), gzipped; the
        # in-between days are recoverable from the source, which is
        # append-mostly.
        if _dt.datetime.now(_dt.timezone.utc).weekday() == 6:
            self._copy_file("scraper_coverage",
                            REPO / "state" / "coverage.db",
                            "WEEKLY gzipped SQLite snapshot of "
                            "state/coverage.db: per-run discovery + fetch "
                            "events, gap queue, portal snapshots. "
                            "Upstream-portal provenance; no user data.",
                            compress=True)

        # Demand queue: corpus-derived (unresolved citations), no user
        # data. Regenerated from the reference graph, so a daily snapshot
        # tracks how the acquisition priorities move as the corpus grows.
        self._build_and_copy(
            "demand_queue",
            [sys.executable, str(REPO / "scripts" / "build_demand_queue.py"),
             "--db", str(REPO / "output" / "reference_graph.db"),
             "--out", str(self.dest / "datasets" / "demand_queue")],
            self.dest / "datasets" / "demand_queue",
            "Decisions Swiss courts cite but the corpus does not hold, "
            "ranked by how many decisions cite each — an acquisition queue "
            "built from evidence. Corpus-derived; no user data.")

        # Interaction dataset: the impression→fetch join (reranker
        # training pairs). Only meaningful once full capture is on; the
        # join reads the capture stream, which stays private.
        self._build_and_copy(
            "interactions",
            [sys.executable,
             str(REPO / "scripts" / "build_interaction_dataset.py"),
             "--captures", str(REPO / "output" / "research_logs"),
             "--out", str(self.dest / "datasets" / "interactions")],
            self.dest / "datasets" / "interactions",
            "Reranker training pairs: each search's ranked list with which "
            "results were fetched and cited. Joined offline per session; "
            "the training rows carry no session id.")

        if not self.dry:
            (self.dest / "MANIFEST.jsonl").open("a", encoding="utf-8").write(
                json.dumps({"date": self.today, "collected": self.manifest},
                           ensure_ascii=False) + "\n")

    def _build_and_copy(self, dataset: str, cmd: list, out_dir: Path,
                        schema_note: str) -> None:
        """Run a builder that writes straight into the dataset dir, then
        refresh the card. The builders are read-only against production."""
        if self.dry:
            log.info("[%s] dry-run: would run %s", dataset, " ".join(cmd[:2]))
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            log.error("[%s] builder failed: %s", dataset,
                      (r.stderr or r.stdout)[-300:])
            return
        self._card(out_dir, dataset, schema_note, True)
        self.manifest.append({"dataset": dataset, "file": f"{self.today}.jsonl",
                              "records": None})
        log.info("[%s] built", dataset)

    # ── traces: 0c-gated ─────────────────────────────────────────────

    def collect_traces(self) -> None:
        """Search-trace export — EXACTLY ONCE per source day.

        Runs only once the amendment marker exists. Two skips make the
        export idempotent so it can run on the daily timer (2026-08-24):

        * **today's file** is still being appended to by the workers, so
          exporting it would capture a partial day and then never revisit
          it — the source day is exported the day AFTER it closes;
        * **an already-exported source day** is skipped. Shards are named
          by COLLECTION date inside a per-SOURCE-day directory, so without
          this the 30-day retention window would be re-exported nightly:
          ~280k records × 30 copies a month, all duplicates.

        Net effect: each source day lands in the archive once, the day
        after it closes, and the archive grows by one day per day.
        """
        marker = self.dest / TRACES_ALLOWED_MARKER
        if not marker.exists():
            raise SystemExit(
                f"traces export refused: {marker} absent. Create it (content: "
                "the /datenschutz/ amendment publication date) only after the "
                "amendment is live in all five languages.")
        src_dir = REPO / "output" / "research_logs"
        exported = skipped_open = skipped_done = 0
        for p in sorted(src_dir.glob("search_traces_*.jsonl")):
            mt = re.match(r"search_traces_(\d{4}-\d{2}-\d{2})", p.name)
            if mt and mt.group(1) >= self.today:
                skipped_open += 1
                continue
            dest_dir = self.dest / "datasets" / "search_traces" / p.stem
            if any(dest_dir.glob("*.jsonl.gz")):
                skipped_done += 1
                continue
            self._write_jsonl_gz(
                f"search_traces/{p.stem}",
                self._read_jsonl(p),
                "Search traces incl. rerank labels (query ≤200 chars, "
                "candidate_ids, llm_order), CE scores, structured parse, "
                "per-signal contributions. One shard per source day, "
                "exported once, the day after that day closes. Collected "
                "under the amended notice; never leaves the private repo.")
            exported += 1
        log.info("[search_traces] %d exported, %d already archived, "
                 "%d still open", exported, skipped_done, skipped_open)


def _git_commit_push(dest: Path, today: str, dry: bool) -> None:
    if dry:
        log.info("dry-run: skipping git commit/push")
        return
    env_ssh = ("ssh -i /opt/caselaw/keys/opencaselaw-data-deploy "
               "-o IdentitiesOnly=yes")
    def g(*args):
        return subprocess.run(
            ["git", "-C", str(dest), *args],
            env={"GIT_SSH_COMMAND": env_ssh, "PATH": "/usr/bin:/bin",
                 "GIT_AUTHOR_NAME": "ocl-collector",
                 "GIT_AUTHOR_EMAIL": "collector@opencaselaw.ch",
                 "GIT_COMMITTER_NAME": "ocl-collector",
                 "GIT_COMMITTER_EMAIL": "collector@opencaselaw.ch"},
            capture_output=True, text=True, timeout=300)
    g("add", "-A")
    r = g("commit", "-m", f"collect {today}")
    if "nothing to commit" in (r.stdout + r.stderr):
        log.info("nothing new to commit")
        return
    p = g("push")
    if p.returncode != 0:
        log.error("push failed: %s", (p.stderr or p.stdout)[-400:])
        raise SystemExit(1)
    log.info("pushed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dest", required=True, type=Path,
                    help="clone of the private opencaselaw-data repo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-traces", action="store_true",
                    help="also export search traces (refuses without the "
                         "amendment marker file)")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s")
    if not (args.dest / ".git").exists() and not args.dry_run:
        log.error("%s is not a git clone — refusing", args.dest)
        return 1
    c = Collector(args.dest, dry_run=args.dry_run)
    c.collect()
    if args.include_traces:
        c.collect_traces()
    if not args.no_push:
        _git_commit_push(args.dest, c.today, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
