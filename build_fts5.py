#!/usr/bin/env python3
"""
Build/update SQLite FTS5 search database from scraped decisions.

Reads from:
  - output/decisions/*.jsonl  (from run_scraper.py — full Decision objects)
  - output/data/daily/*.parquet (from pipeline.py — Parquet shards)

Produces:
  - output/decisions.db (SQLite with FTS5 full-text search)

The DB schema matches what mcp_server.py expects.

Usage:
    python3 build_fts5.py                          # default: ./output
    python3 build_fts5.py --output /opt/caselaw/repo/output
    python3 build_fts5.py --output ./output --db ~/.swiss-caselaw/decisions.db
    python3 build_fts5.py --watch 60               # rebuild every 60 seconds
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from db_schema import COVERAGE_SCHEMA_SQL, INSERT_COLUMNS, INSERT_OR_IGNORE_SQL, SCHEMA_SQL
from models import make_canonical_key

logger = logging.getLogger("build_fts5")


# Pre-swap safety gate: a freshly-built temp DB must retain at least this
# fraction of the live DB's row count before it can atomically replace it.
# Guards against an empty / partial / corrupt build (the 2026-05 ENOSPC +
# WAL-corruption incidents produced near-empty .tmp builds) silently
# swapping over a healthy 990k-row production corpus. The corpus only grows,
# so a >5% drop is always a bug; the post-swap zero-row check is too late —
# workers serve the swapped inode immediately.
SWAP_MIN_RETAIN_FRACTION = 0.95

# Per-court pre-swap gate. The GLOBAL gate above is blind to a per-court
# collapse: the SG alphabetical dedup-collision class (es_sg_publikationen
# winning INSERT OR IGNORE over the direct sg_* shards — see
# memory/sg_anomaly_root_cause_2026_04_30.md) once dropped 89-92% of
# individual chambers while netting only ~-181 rows corpus-wide, invisible to
# a 5% global floor (5% of ~990k ≈ 49.5k rows). Enforce a per-court retain
# fraction, but only on courts large enough that a big proportional drop is
# unambiguously a bug (the sub-50-row micro-courts like zh_mietgericht=1 would
# false-trip a bare percentage). Calibration 2026-06-16: across the last 10
# published snapshots the largest legitimate per-court drop on a court ≥500
# rows was -0.0% (bger -20), so 0.80 has zero false-trip history; and an
# aborted swap fails SAFE (workers keep serving the last-good DB). Coverage
# verified 2026-06-16: the SG-collision chambers (sg_publikationen 643,
# sg_kantonsgericht 1,077, sg_verwaltungsrekurskommission 1,173) all sit ≥500,
# so the floor catches the motivating incident; the 45 sub-500 micro-courts
# (~4,917 rows, 0.5% of corpus) are left to the global gate by design.
PER_COURT_MIN_RETAIN_FRACTION = 0.80
PER_COURT_MIN_SIZE = 500


def _date_inversion_guard_inline(decision_date, publication_date):
    """Gross date-inversion guard: a court cannot publish a decision MORE THAN
    A MONTH before it rules. When publication_date is >31 days before
    decision_date, the publication_date is a mislabel or date-parse error —
    NULL it (decision_date, the header ruling date, is the trusted/mandatory
    field; publication_date is optional). Only the GROSS band (>31 days) is
    corrected — the 0-3 day band may be a dispatch/Versanddatum and is left
    intact. Idempotent + self-healing on every full rebuild. See
    quality/checks/dates.py::check_publication_before_decision."""
    if not decision_date or not publication_date:
        return publication_date
    try:
        d = date.fromisoformat(str(decision_date)[:10])
        p = date.fromisoformat(str(publication_date)[:10])
    except ValueError:
        return publication_date
    if (p - d).days < -31:
        return None
    return publication_date


def _check_swap_row_gate(new_count: int, old_count: int,
                         fraction: float = SWAP_MIN_RETAIN_FRACTION) -> None:
    """Raise RuntimeError if swapping a temp DB with ``new_count`` rows over a
    live DB with ``old_count`` rows would shrink the corpus below ``fraction``
    of its current size. No-op when there is no readable live DB yet
    (old_count <= 0). Set OCL_SKIP_SWAP_GATE=1 to force an intentional large
    shrink (e.g. a deliberate mass purge)."""
    if old_count <= 0:
        return
    if os.environ.get("OCL_SKIP_SWAP_GATE") == "1":
        logger.warning(
            "pre-swap row gate OVERRIDDEN via OCL_SKIP_SWAP_GATE "
            "(new=%d, live=%d)", new_count, old_count)
        return
    if new_count < old_count * fraction:
        raise RuntimeError(
            f"pre-swap gate: refusing to swap — new build has {new_count:,} "
            f"rows = {new_count / old_count:.1%} of live {old_count:,} "
            f"(< {fraction:.0%}). Live DB left untouched; temp DB kept for "
            f"inspection. Set OCL_SKIP_SWAP_GATE=1 to force an intentional "
            f"shrink."
        )


def _check_swap_per_court_gate(
        new_by_court: dict, live_by_court: dict,
        fraction: float = PER_COURT_MIN_RETAIN_FRACTION,
        min_live_rows: int = PER_COURT_MIN_SIZE) -> None:
    """Raise RuntimeError if ANY court with >= ``min_live_rows`` live rows would
    shrink below ``fraction`` of its live count in the new build. Catches a
    per-court collapse (the SG dedup-collision class) that the global
    _check_swap_row_gate cannot see. No-op when there is no readable live DB yet
    (``live_by_court`` empty), mirroring the global gate's old_count<=0
    short-circuit. Honours the SAME OCL_SKIP_SWAP_GATE=1 escape hatch — a
    deliberate court retirement/rename trips this by design and is the intended
    operator-override case. New courts (present in new_by_court, absent from
    live_by_court) are correctly ignored: the loop iterates over live_by_court."""
    if not live_by_court:
        return
    if os.environ.get("OCL_SKIP_SWAP_GATE") == "1":
        logger.warning(
            "pre-swap per-court gate OVERRIDDEN via OCL_SKIP_SWAP_GATE")
        return
    for court, live_n in sorted(live_by_court.items()):
        if live_n < min_live_rows:
            continue
        new_n = new_by_court.get(court, 0)
        if new_n < live_n * fraction:
            raise RuntimeError(
                f"pre-swap per-court gate: refusing to swap — court "
                f"{court!r} collapsed {live_n:,} → {new_n:,} rows "
                f"({(new_n / live_n) if live_n else 0:.1%} of live, "
                f"< {fraction:.0%}). Live DB left untouched; temp DB kept for "
                f"inspection. Set OCL_SKIP_SWAP_GATE=1 to force an intentional "
                f"per-court shrink (e.g. a court retirement)."
            )


# ── Per-phase timing instrumentation ──────────────────────────
#
# Step 2 of publish.py is a 4–10 h black-box. Without per-phase
# timings we can't tell whether the variance comes from FTS5 optimize,
# hash computation, or one of the 13 normalisation passes. The context
# manager below logs:
#
#     Phase: Deduplicating decisions...
#       (existing inner logger.info lines from the phase body)
#       -> Deduplicating decisions done in 27m 13s
#
# and ``_log_phase_summary()`` at end-of-build prints a sorted
# breakdown like:
#
#     === build_fts5 phase summary ===
#         45m 02s  ( 51.8%)  FTS5 optimize
#         27m 13s  ( 31.3%)  Deduplicating decisions
#         ...
#
# State is module-scoped (ok for a one-shot rebuild script). On a
# crash, the surviving timings still print via the finally clause in
# the context manager.
_PHASE_TIMINGS: list[tuple[str, float]] = []


def _fmt_dur(seconds: float) -> str:
    """Human-friendly duration: '12.3s' / '4m 27s' / '1h 14m'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _spawn_early_stats_push(swapped_db: Path) -> None:
    """Fire-and-forget: regenerate docs/stats.json + git push so the
    public dashboard reflects the freshly-swapped FTS5 DB within ~5 min
    of the atomic swap, instead of waiting for the post-swap
    integrity check (60 GB DB → ~3 h under ionice idle) and the rest
    of the slow tier (graph rebuild, materialien, parquet, HF upload)
    to complete.

    The dashboard is a static GitHub Pages site that reads
    docs/stats.json from the main branch. As soon as we push, GitHub
    Pages picks up the change in ~30–90 s.

    Failure is non-fatal: the publish.py final Step 5/6 always runs
    at the end with full graph aggregations and will overwrite this
    early commit's stats.json.
    """
    import subprocess
    import sys
    repo_dir = Path(__file__).resolve().parent
    stats_script = repo_dir / "generate_stats.py"
    stats_out = repo_dir / "docs" / "stats.json"
    log_path = "/var/log/early_stats_push.log"
    # Bash: regen → stage → commit (--allow-empty so a no-op stats run
    # doesn't fail) → push. Each step gates the next via &&. Output
    # goes to a dedicated log so it doesn't interleave with publish.log.
    bash_cmd = (
        f"set -e; "
        f"echo '[' $(date -u +%FT%TZ) '] early-stats-push: starting'; "
        f"{sys.executable} {stats_script} "
        f"  --db {swapped_db} "
        f"  --output {stats_out} "
        f"  --no-interesting-stats; "
        f"cd {repo_dir}; "
        f"git pull --rebase origin main 2>&1 | tail -3 || true; "
        f"git add {stats_out}; "
        f"git diff --cached --quiet && {{ echo 'no stats.json change'; exit 0; }}; "
        f"git commit -m 'chore: stats.json refresh (post-swap, pre-finalization)'; "
        f"git push origin main; "
        f"echo '[' $(date -u +%FT%TZ) '] early-stats-push: done'"
    )
    try:
        log_fd = open(log_path, "ab")
    except OSError:
        # Fall back to /tmp if /var/log isn't writable (e.g. dev machine)
        log_fd = open("/tmp/early_stats_push.log", "ab")
    subprocess.Popen(
        ["bash", "-c", bash_cmd],
        stdout=log_fd, stderr=subprocess.STDOUT,
        start_new_session=True,  # detach from build_fts5's process group
    )
    logger.info(
        "  Spawned early-stats-push (fire-and-forget, log: %s) — "
        "dashboard will reflect new DB in ~5 min", log_path
    )


@contextlib.contextmanager
def _phase_timer(name: str):
    """Log start, append timing, log end. Use as `with _phase_timer("X"):`.

    Existing `logger.info("X...")` calls inside phase bodies stay; the
    context manager prefixes its own "Phase:" / "->" lines around them
    so the trace remains readable both live and post-mortem.
    """
    t0 = time.monotonic()
    logger.info(f"Phase: {name}...")
    try:
        yield
    finally:
        dt = time.monotonic() - t0
        _PHASE_TIMINGS.append((name, dt))
        logger.info(f"  -> {name} done in {_fmt_dur(dt)}")


def _log_phase_summary() -> None:
    """Sorted summary of every recorded phase. Cheap (<1ms)."""
    if not _PHASE_TIMINGS:
        return
    total = sum(d for _, d in _PHASE_TIMINGS)
    if total < 1:
        return
    logger.info("=== build_fts5 phase summary ===")
    for name, dt in sorted(_PHASE_TIMINGS, key=lambda x: -x[1]):
        pct = 100 * dt / total
        logger.info(f"  {_fmt_dur(dt):>10}  ({pct:>5.1f}%)  {name}")
    logger.info(f"  {_fmt_dur(total):>10}  (sum of phases)")

# ── Text cleaning ────────────────────────────────────────────

# Bounded and newline-anchored — 2026-08-25. The previous form was
# `<[^>]+>`, and `[^>]` matches newlines: in OCR'd text a single stray "<"
# swallowed everything up to the next ">", across thousands of characters
# and many lines, and replaced it with one space. Measured against the
# source shards before the fix:
#     bge_historical_14_I_192  12,159 -> 2,317 chars served  (80.9% lost)
#     bge_historical_11_I_5    13,081 -> 6,585               (49.7% lost)
#     bge_historical_15_I_79   13,004 -> 8,645               (33.5% lost)
# Each of those files contained 1-6 stray "<" and NO real HTML at all; the
# "<" are scanner misreads — "1<r" is OCR for "1er". In force since
# f3a087f7 (2026-02-22), and _clean_text is applied to full_text, regeste
# AND title, so headnotes were truncated too.
#
# `[^>\n]` stops a run at the first newline; `{1,200}` bounds it to a
# plausible tag length. Both narrow what is deleted, so this can only ever
# preserve MORE text than before — it cannot lose anything that survives
# today. Real tags (<b>, <span class="x">, </p>) are still stripped.
_HTML_TAG_RE = re.compile(r"<[^>\n]{1,200}>")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
}


def _fix_mojibake(text: str) -> str:
    """Fix double-encoded UTF-8 (UTF-8 bytes decoded as Latin-1).

    Common pattern: 'ä' (U+00E4) stored as 'Ã¤' (C3 A4 decoded as Latin-1).
    """
    try:
        # If the text contains typical mojibake sequences, try to fix
        fixed = text.encode("latin-1").decode("utf-8")
        # Sanity check: the fixed version should be shorter or equal
        if len(fixed) <= len(text):
            return fixed
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text


# C0 control chars except tab/newline/CR. An embedded NUL (\x00) in Omnis/
# Findinfo portal text (ti/ne/ge/so) makes SQLite's length() stop at the NUL, so
# the structure builder's `WHERE length(full_text) >= 500` filter silently drops
# the row (and the VPS's older sqlite3 truncates the read outright) — only the
# ~500-char header survived, zeroing decision_structure Erwaegungen extraction
# and costing search recall. Proven 2026-06-29 on ti_gerichte_34.2024.28
# (NUL at offset 498; length()=495 vs the JSONL shard's 77,544 chars).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(text: str | None) -> str | None:
    """Strip control chars + HTML tags, fix entities/mojibake, normalize whitespace."""
    if not text:
        return text

    # Remove control chars first so a NUL can't truncate the body downstream.
    text = _CONTROL_CHARS_RE.sub("", text)

    # Strip HTML tags
    text = _HTML_TAG_RE.sub(" ", text)

    # Replace HTML entities
    for entity, replacement in _HTML_ENTITIES.items():
        if entity in text:
            text = text.replace(entity, replacement)

    # Fix mojibake (only if likely — check for common mojibake markers)
    if "\xc3" in text:
        text = _fix_mojibake(text)

    # Normalize whitespace (collapse runs of spaces/tabs, preserve newlines)
    text = _MULTI_SPACE_RE.sub(" ", text)

    return text.strip()


# ── BGer regeste extraction ──────────────────────────────────

_REGESTE_START_RE = re.compile(
    r"(?:^|\n)\s*Regeste\b[:\s]*\n",
    re.IGNORECASE,
)
_REGESTE_END_MARKERS = [
    re.compile(r"^\s*Sachverhalt\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Faits\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Fatti\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Urteilskopf\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*A\.\s", re.MULTILINE),
]


def _extract_regeste_from_text(full_text: str) -> str | None:
    """Try to extract regeste from BGE/BGer full_text.

    BGE decisions typically contain:
      Regeste
      <regeste text in DE>
      Regeste      (or Regesto)
      <regeste in FR/IT>
      Sachverhalt / Faits / Fatti / A.
    """
    m = _REGESTE_START_RE.search(full_text)
    if not m:
        return None

    start = m.end()
    # Find the end: next major section header
    end = len(full_text)
    for pat in _REGESTE_END_MARKERS:
        em = pat.search(full_text, start)
        if em and em.start() < end:
            end = em.start()

    regeste = full_text[start:end].strip()
    # Skip if too short or too long (probably a false match)
    if len(regeste) < 20 or len(regeste) > 5000:
        return None
    return regeste


# ── Dedup + post-processing ──────────────────────────────────

_DEDUP_HTML_RE = re.compile(r"<[^>]+>")
_DEDUP_NONALNUM_RE = re.compile(r"[^a-z0-9]+")


def _norm_for_dedup(full_text) -> str:
    """Normalized content signature for the date-agnostic dedup pass.

    Strips HTML tags, lowercases, drops all non-alphanumerics, caps length. Two
    imports of the SAME decision (html vs plaintext rendering, es vs direct,
    or a truncated copy) collapse to the same string or a prefix of it; two
    GENUINELY DISTINCT decisions on the same docket (Zwischenentscheid vs
    Endentscheid) diverge early. Used to decide whether a same-court+docket,
    different-date row is a true duplicate (delete) or a distinct decision (keep).
    """
    t = _DEDUP_HTML_RE.sub(" ", (full_text or "")[:8000])
    return _DEDUP_NONALNUM_RE.sub("", t.lower())[:4000]


def _build_docket_aliases(conn: sqlite3.Connection) -> int:
    """Populate decision_docket_aliases from consolidated federal captions (#41).

    For every court='bger' decision, extract the joined (secondary) dockets from
    its caption and map each to the lead decision_id, so a lookup by any joined
    docket resolves to the consolidated decision. Skips any alias that is already
    a primary docket_number: such a docket resolves directly, and mapping it to
    the consolidation would be ambiguous (covers revision references and
    reciprocal consolidations). Fully rebuilt each run (a few thousand rows);
    additive and non-fatal — never fails the build.
    """
    import docket_aliases

    conn.execute("DELETE FROM decision_docket_aliases")
    # Every primary docket key; an alias colliding with one is skipped so a real
    # lead decision always wins.
    primary: set[str] = set()
    for (dn,) in conn.execute(
        "SELECT docket_number FROM decisions "
        "WHERE docket_number IS NOT NULL AND docket_number != ''"
    ):
        k = docket_aliases.normalize_docket_key(dn)
        if k:
            primary.add(k)

    rows: list[tuple] = []
    for did, dn, head in conn.execute(
        "SELECT decision_id, docket_number, substr(full_text, 1, 2500) "
        "FROM decisions WHERE court = 'bger'"
    ):
        for alias in docket_aliases.extract_joined_dockets(head, dn):
            if alias in primary:
                continue
            rows.append(("bger", alias, alias, did, "bger_caption_v1"))

    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO decision_docket_aliases "
            "(court, alias_docket, alias_docket_norm, canonical_decision_id, "
            " extraction_method) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    conn.commit()
    return len(rows)


def _dedup_decisions(conn: sqlite3.Connection) -> int:
    """Remove duplicate decisions sharing the same canonical_key.

    The canonical_key aggressively normalizes court + docket + date so that
    formatting variants (dots vs underscores, case, etc.) collapse together.
    Falls back to exact (court, docket_number, decision_date) if canonical_key
    is not yet populated.

    Strategy: keep the row with the most total content (full_text + regeste).
    Before deleting losers, merge their regeste into the survivor if the
    survivor's regeste is empty.

    This avoids the prior bug (2026-04-25 audit) where the rule "non-empty
    regeste wins" caused metadata-only federation stubs (~280 chars text +
    short regeste) to win over rich direct PDF scrapes (~9,000 chars text,
    no regeste). For Tribuna-based scrapers (gr/be/fr/zg/sz) the direct
    scrape never extracts regeste from PDF, so the regeste-precedence rule
    threw away ~9,300 GR full-PDF rows in favor of metadata stubs.
    Returns number of rows deleted.
    """
    # Check if canonical_key column exists and is populated
    has_canonical = False
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE canonical_key IS NOT NULL AND canonical_key != ''"
        ).fetchone()
        has_canonical = row[0] > 0
    except sqlite3.OperationalError:
        pass

    if has_canonical:
        # Exclude keys with empty docket part (format: court|DOCKET|date)
        dup_sql = """
            SELECT canonical_key, COUNT(*) as cnt
            FROM decisions
            WHERE canonical_key IS NOT NULL AND canonical_key != ''
              AND canonical_key NOT LIKE '%||%'
            GROUP BY canonical_key
            HAVING cnt > 1
        """
        groups = conn.execute(dup_sql).fetchall()
        # Do NOT early-return when there are no exact-key dups — the
        # date-agnostic Pass 2 below must still run independently (P0.1, 2026-07-13).
        deleted = 0
        for (canonical_key, cnt) in groups:
            rows = conn.execute(
                """
                SELECT decision_id, COALESCE(regeste, ''), LENGTH(COALESCE(full_text, ''))
                FROM decisions
                WHERE canonical_key = ?
                ORDER BY
                    LENGTH(COALESCE(full_text, '')) + LENGTH(COALESCE(regeste, '')) DESC
                """,
                (canonical_key,),
            ).fetchall()
            survivor_id, survivor_regeste, _ = rows[0]
            # Backfill regeste from a loser if survivor has none.
            if not survivor_regeste:
                for _, loser_regeste, _ in rows[1:]:
                    if loser_regeste:
                        conn.execute(
                            "UPDATE decisions SET regeste = ?, content_hash = NULL WHERE decision_id = ?",
                            (loser_regeste, survivor_id),
                        )
                        break
            for row in rows[1:]:
                conn.execute("DELETE FROM decisions WHERE decision_id = ?", (row[0],))
                deleted += 1
    else:
        # Fallback: exact match on (court, docket_number, decision_date)
        dup_sql = """
            SELECT court, docket_number, decision_date, COUNT(*) as cnt
            FROM decisions
            WHERE docket_number IS NOT NULL AND LENGTH(TRIM(docket_number)) > 0
            GROUP BY court, docket_number, decision_date
            HAVING cnt > 1
        """
        groups = conn.execute(dup_sql).fetchall()
        # Do NOT early-return when there are no exact-key dups — the
        # date-agnostic Pass 2 below must still run independently (P0.1, 2026-07-13).
        deleted = 0
        for court, docket, date, cnt in groups:
            rows = conn.execute(
                """
                SELECT decision_id, COALESCE(regeste, ''), LENGTH(COALESCE(full_text, ''))
                FROM decisions
                WHERE court = ? AND docket_number = ? AND decision_date IS ?
                ORDER BY
                    LENGTH(COALESCE(full_text, '')) + LENGTH(COALESCE(regeste, '')) DESC
                """,
                (court, docket, date),
            ).fetchall()
            survivor_id, survivor_regeste, _ = rows[0]
            if not survivor_regeste:
                for _, loser_regeste, _ in rows[1:]:
                    if loser_regeste:
                        conn.execute(
                            "UPDATE decisions SET regeste = ?, content_hash = NULL WHERE decision_id = ?",
                            (loser_regeste, survivor_id),
                        )
                        break
            for row in rows[1:]:
                conn.execute("DELETE FROM decisions WHERE decision_id = ?", (row[0],))
                deleted += 1

    # ── Pass 2: date-agnostic dedup — CONTENT-AWARE (2026-07-13, P0.1 fix) ──
    # Same court+docket with different dates is EITHER (a) one decision imported
    # twice with different date fields (es publication_date vs direct
    # decision_date; html vs plaintext rendering) — a true duplicate — OR (b)
    # GENUINELY DISTINCT decisions on the same docket decided on different dates
    # (Zwischenentscheid + Endentscheid, costs orders, remands). The old pass
    # grouped by court|docket ignoring the date and deleted all but the longest,
    # silently destroying every (b) case — a mission-critical completeness loss
    # (confirmed: e.g. bvger B-93/2007 kept the June interim and deleted the
    # December final; ~56k deletions/build, a large fraction genuinely distinct).
    # Now a member is deleted ONLY if its normalized, HTML-stripped content is
    # identical to, or a truncated prefix of, the survivor's (provably the same
    # decision). Distinct decisions on the same docket are kept.
    #
    # OCL_DATE_AGNOSTIC_DEDUP: "content_aware" (default) | "off" (skip the pass) |
    # "legacy" (old destructive behaviour — emergency rollback only).
    _da_mode = os.environ.get("OCL_DATE_AGNOSTIC_DEDUP", "content_aware").lower()
    deleted2 = 0
    if _da_mode != "off":
        all_rows = conn.execute(
            "SELECT decision_id, canonical_key, LENGTH(COALESCE(full_text, '')), "
            "LENGTH(COALESCE(regeste, '')) FROM decisions "
            "WHERE canonical_key IS NOT NULL AND canonical_key <> ''"
        ).fetchall()
        groups2 = defaultdict(list)
        for did, ckey, tlen, rlen in all_rows:
            parts = ckey.split("|")
            if len(parts) == 3 and parts[1]:
                groups2[f"{parts[0]}|{parts[1]}"].append((did, tlen, rlen))
        multi = {k: v for k, v in groups2.items() if len(v) > 1}

        if _da_mode == "legacy":
            for entries in multi.values():
                entries.sort(key=lambda x: -(x[1] + x[2]))
                for did, _, _ in entries[1:]:
                    conn.execute("DELETE FROM decisions WHERE decision_id = ?", (did,))
                    deleted2 += 1
        else:  # content_aware (default)
            # Normalized content for members of multi-member groups only (fetch a
            # bounded prefix, not full blobs, to keep IO/memory low).
            member_ids = [did for v in multi.values() for (did, _, _) in v]
            norm = {}
            for i in range(0, len(member_ids), 800):
                chunk = member_ids[i:i + 800]
                ph = ",".join("?" * len(chunk))
                for did, ft in conn.execute(
                    f"SELECT decision_id, substr(full_text, 1, 8000) "
                    f"FROM decisions WHERE decision_id IN ({ph})", chunk,
                ):
                    norm[did] = _norm_for_dedup(ft)
            kept_distinct = 0
            for entries in multi.values():
                entries.sort(key=lambda x: -(x[1] + x[2]))
                sn = norm.get(entries[0][0], "")
                for did, _, _ in entries[1:]:
                    mn = norm.get(did, "")
                    short, longn = (mn, sn) if len(mn) <= len(sn) else (sn, mn)
                    # true duplicate iff the shorter normalized content is a
                    # substantive prefix of the longer (identical / truncated /
                    # re-rendered); otherwise it is a distinct decision — KEEP it.
                    if len(short) >= 200 and longn.startswith(short):
                        conn.execute("DELETE FROM decisions WHERE decision_id = ?", (did,))
                        deleted2 += 1
                    else:
                        kept_distinct += 1
            logger.info(
                f"  Pass 2 (content-aware): kept {kept_distinct} distinct "
                f"same-docket decisions that the legacy pass would have deleted"
            )
    if deleted2:
        logger.info(f"  Pass 2 (date-agnostic): removed {deleted2} duplicates")
    deleted += deleted2

    conn.commit()
    return deleted


# Courts whose decisions may overlap across different court codes.
# Entscheidsuche often maps to a generic code (e.g. zh_gerichte) while
# direct scrapers use specific codes (zh_obergericht).  Grouping allows
# cross-court dedup within each set.
_COURT_OVERLAP_GROUPS: list[set[str]] = [
    # ZH: entscheidsuche → zh_gerichte, direct scraper → sub-courts
    {"zh_gerichte", "zh_obergericht", "zh_kassationsgericht", "zh_handelsgericht",
     "zh_bezirksgericht_zuerich", "zh_bezirksgericht_winterthur",
     "zh_bezirksgericht_horgen", "zh_bezirksgericht_dietikon",
     "zh_bezirksgericht_buelach", "zh_bezirksgericht_dielsdorf",
     "zh_bezirksgericht_uster", "zh_bezirksgericht_pfaeffikon",
     "zh_bezirksgericht_hinwil", "zh_bezirksgericht_meilen",
     "zh_bezirksgericht_affoltern", "zh_mietgericht", "zh_arbeitsgericht"},
    # SG: entscheidsuche → sg_gerichte, direct scraper (sg_publikationen) → sub-courts
    {"sg_gerichte", "sg_publikationen", "sg_versicherungsgericht",
     "sg_verwaltungsgericht", "sg_verwaltungsrekurskommission",
     "sg_kantonsgericht", "sg_handelsgericht"},
    # AG: entscheidsuche → ag_gerichte, direct scraper → sub-courts
    {"ag_gerichte", "ag_obergericht", "ag_versicherungsgericht",
     "ag_handelsgericht", "ag_spezialverwaltungsgericht",
     "ag_strafgericht", "ag_zivilgericht", "ag_verwaltungsgericht",
     "ag_anwaltskommission", "ag_aufsichtskommission", "ag_regierungsrat",
     "ag_departement_bks", "ag_departement_bvu", "ag_departement_gs",
     "ag_departement_vi", "ag_justizgericht", "ag_justizleitung", "ag_weitere"},
    # SH: entscheidsuche generic vs specific
    {"sh_gerichte", "sh_obergericht"},
    # TG: entscheidsuche → tg_obergericht, direct scraper → tg_gerichte
    {"tg_gerichte", "tg_obergericht"},
    # VD: historical findinfo/omni vs current scraper
    {"vd_findinfo", "vd_gerichte", "vd_omni"},
    # BS: entscheidsuche → bs_gerichte, direct scraper → sub-courts
    {"bs_gerichte", "bs_appellationsgericht", "bs_sozialversicherungsgericht"},
    # BE: steuerrekurs overlaps with verwaltungsgericht
    {"be_steuerrekurs", "be_verwaltungsgericht"},
]

# Build a lookup: court_code → frozenset of group members
_COURT_TO_GROUP: dict[str, frozenset[str]] = {}
for _group in _COURT_OVERLAP_GROUPS:
    _frozen = frozenset(_group)
    for _code in _group:
        _COURT_TO_GROUP[_code] = _frozen


def _cross_court_dedup(conn: sqlite3.Connection) -> int:
    """Remove duplicates where the same docket exists under overlapping court codes.

    Only matches within explicit court overlap groups (not all courts in a canton).
    Keeps the version with the longest full_text.
    """
    # Load decisions from courts that belong to overlap groups
    overlap_courts = set()
    for group in _COURT_OVERLAP_GROUPS:
        overlap_courts.update(group)

    if not overlap_courts:
        return 0

    placeholders = ",".join("?" * len(overlap_courts))
    rows = conn.execute(
        f"SELECT decision_id, court, docket_number, decision_date, "
        f"LENGTH(COALESCE(full_text, '')), LENGTH(COALESCE(regeste, '')) "
        f"FROM decisions "
        f"WHERE court IN ({placeholders}) "
        f"AND docket_number IS NOT NULL AND LENGTH(TRIM(docket_number)) > 0",
        list(overlap_courts),
    ).fetchall()

    # Group by (overlap_group_id, normalized_docket)
    groups: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for did, court, docket, date, tlen, rlen in rows:
        group = _COURT_TO_GROUP.get(court)
        if not group:
            continue
        docket_norm = re.sub(r"[^A-Z0-9]", "", (docket or "").upper())
        if "tg_gerichte" in group:
            docket_norm = re.sub(r"NR(?=\d)", "", docket_norm)  # TG "Nr." noise
        # Include date to avoid false matches across years
        date_compact = (date or "").replace("-", "")[:8]
        key = f"{id(group)}|{docket_norm}|{date_compact}"
        groups[key].append((did, tlen, rlen))

    deleted = 0
    for entries in groups.values():
        if len(entries) < 2:
            continue
        # Keep version with the most total content (full_text + regeste)
        entries.sort(key=lambda x: -(x[1] + x[2]))
        for did, _, _ in entries[1:]:
            conn.execute("DELETE FROM decisions WHERE decision_id = ?", (did,))
            deleted += 1

    if deleted:
        conn.commit()
    return deleted


def _normalize_dockets(conn: sqlite3.Connection) -> int:
    """König audit 2026-04-30 + QC follow-up 2026-05-01: normalise whitespace
    in docket_number.

    Two passes (idempotent, run on every nightly):

    1. Trim leading/trailing whitespace. Found ~21,000 rows in 2026-04-30,
       concentrated in zh_verwaltungsgericht (11,359), ch_vb (6,721),
       vd_findinfo (2,705), edoeb (45), sh_obergericht (10), and several
       AG chambers. Whitespace breaks exact-match queries
       (' AEG.2018.00004' won't match 'AEG.2018.00004').

    2. Replace internal newlines (LF / CR) with a single space. The QC
       gate surfaced 5,441 rows in 2026-05-01 audit where the scraper
       grabbed two adjacent table cells and joined them with a newline,
       e.g. 'A 2024 015\\nUrteil vom...'. Internal newlines also break
       URL routing (/entscheid/{id}) and exact-match queries.
    """
    cur = conn.execute(
        "UPDATE decisions SET docket_number = trim(docket_number) "
        "WHERE docket_number != trim(docket_number)"
    )
    fixed = cur.rowcount
    # Pass 2: collapse internal newlines (and tabs) to single space, then
    # collapse multiple spaces to one.
    cur2 = conn.execute(
        "UPDATE decisions SET docket_number = "
        "trim(replace(replace(replace(docket_number, char(10), ' '), "
        "                                              char(13), ' '), "
        "                                              char(9),  ' ')) "
        "WHERE docket_number LIKE '%' || char(10) || '%' "
        "   OR docket_number LIKE '%' || char(13) || '%' "
        "   OR docket_number LIKE '%' || char(9)  || '%'"
    )
    fixed += cur2.rowcount
    # Pass 3: collapse runs of spaces (often left by passes 1+2).
    cur3 = conn.execute(
        "UPDATE decisions SET docket_number = "
        "  trim(replace(replace(replace(docket_number, "
        "    '   ', ' '), '  ', ' '), '  ', ' ')) "
        "WHERE docket_number LIKE '%  %'"
    )
    fixed += cur3.rowcount
    if fixed:
        conn.commit()
    return fixed


def _normalize_source_urls(conn: sqlite3.Connection) -> int:
    """QC follow-up 2026-05-01: prefix relative source_urls with their host.

    König P2 (Apr 29) fixed 694 GL/BS rows at scraper-side, but the same
    pattern keeps reappearing because the Tribuna platform underlying
    bs_gerichte and gl_gerichte serves URLs as bare `/cgi-bin/nph-omniscgi.exe?...`
    paths. Auto-correcting at build time means any future scraper miss
    or re-ingest from the entscheidsuche archive self-heals.

    Court → host mapping:
        bs_gerichte → https://www.gerichte.bs.ch
        gl_gerichte → https://findinfo.gl.ch
    """
    HOST_BY_COURT = {
        "bs_gerichte": "https://www.gerichte.bs.ch",
        "gl_gerichte": "https://findinfo.gl.ch",
    }
    fixed_total = 0
    for court, host in HOST_BY_COURT.items():
        cur = conn.execute(
            "UPDATE decisions SET source_url = ? || source_url "
            "WHERE court = ? AND source_url IS NOT NULL AND source_url != '' "
            "AND source_url NOT LIKE 'http%'",
            (host, court),
        )
        fixed_total += cur.rowcount
    if fixed_total:
        conn.commit()
    return fixed_total


# Boundary markers that separate the head-note (Regeste) from the body
# in HUDOC-sourced ECHR judgments. Used by _truncate_oversized_regestes.
_REGESTE_BODY_BOUNDARIES = (
    "\nSachverhalt\n", "\nFaits\n", "\nFatti\n", "\nFakten\n",
    "\nProcédure\n", "\nProcedura\n",
)


def _truncate_oversized_regestes(conn: sqlite3.Connection) -> int:
    """QC follow-up 2026-05-01: HUDOC scraper duplicates entire judgment
    into BOTH `regeste` and `full_text` fields. Result: 462 rows in the
    2026-05-01 audit have regestes >8000 chars (max 875,989 chars), a
    near-mirror of full_text.

    For every row where the regeste is >8000 chars AND substantially
    duplicates the full_text (length within 90%), keep only the head-note
    portion: text up to the first body-boundary marker (Sachverhalt /
    Faits / Fatti / Fakten / Procédure / Procedura). If no boundary is
    found, truncate to 5000 chars (the longest legitimate Bundesgericht
    regeste is ~4500). full_text is left untouched — no info loss.

    Idempotent: WHERE clause filters to rows still oversized.
    """
    rows = conn.execute(
        "SELECT decision_id, regeste, full_text FROM decisions "
        "WHERE regeste IS NOT NULL AND length(regeste) > 8000"
    ).fetchall()
    updates = []
    for did, regeste, full_text in rows:
        if not regeste:
            continue
        full_len = len(full_text or "")
        # Only collapse when regeste is essentially a duplicate of full_text;
        # otherwise the regeste might be legitimately huge for some reason
        # we don't want to silently destroy.
        if full_len < 1000 or len(regeste) < 0.9 * full_len:
            continue
        cut = None
        for marker in _REGESTE_BODY_BOUNDARIES:
            idx = regeste.find(marker)
            if idx > 0 and (cut is None or idx < cut):
                cut = idx
        new_regeste = regeste[:cut] if cut else regeste[:5000]
        new_regeste = new_regeste.rstrip()
        if new_regeste != regeste:
            updates.append((new_regeste, did))
    if updates:
        conn.executemany(
            "UPDATE decisions SET regeste = ?, content_hash = NULL WHERE decision_id = ?",
            updates,
        )
        conn.commit()
    return len(updates)


def _migrate_short_text_to_regeste(conn: sqlite3.Connection) -> int:
    """König P7: short full_text values are often a regeste in the wrong field.

    Pattern: rows where full_text is 10-100 chars (typically Art./§ references
    + a one-line topic, e.g. 'Art. 116a ZPO. Verhältnis Kostenerlass...') AND
    regeste is empty. Move to regeste; null out full_text to correctly mark
    "no decision body extracted".

    Found 248 affected rows in 2026-04-30 audit (sh_gerichte 204 dominated).
    Auto-applies on every nightly so future scraper regressions self-correct.
    """
    cur = conn.execute(
        "UPDATE decisions SET regeste = full_text, full_text = NULL, content_hash = NULL "
        "WHERE LENGTH(COALESCE(full_text, '')) BETWEEN 10 AND 99 "
        "AND (regeste IS NULL OR regeste = '')"
    )
    migrated = cur.rowcount
    if migrated:
        conn.commit()
    return migrated


def _compute_content_hashes(conn: sqlite3.Connection, batch_size: int = 5000) -> int:
    """Per-decision SHA-256(regeste || full_text) for content verifiability.

    Computed AFTER all _normalize_* / _migrate_* / _truncate_* passes so the
    hash reflects the canonical (post-cleanup) content the corpus serves.
    Anyone — auditor, lawyer, researcher — can later prove that the bytes
    we returned for decision Y on date X were exactly this content.

    Dirty-tracked: the text-mutating passes (dedup regeste-merge, truncate
    oversized regeste, migrate short text, fill missing regeste) set
    content_hash=NULL when they change regeste/full_text, so this only
    re-hashes the NULL'd (changed) rows plus any genuinely-new rows — it
    no longer reads+hashes the whole ~970k-row table to repair ~17k stale
    hashes. Stores 64-hex SHA-256 per row (~64 MB extra at 970k rows).
    """
    import hashlib
    rows = conn.execute(
        "SELECT decision_id, regeste, full_text, content_hash "
        "FROM decisions WHERE content_hash IS NULL OR content_hash = ''"
    ).fetchall()
    updates: list[tuple[str, str]] = []
    for decision_id, regeste, full_text, existing_hash in rows:
        body = (regeste or "") + (full_text or "")
        # SHA-256 over UTF-8 bytes; empty body still yields the canonical
        # SHA-256 of the empty string (e3b0c4...). That's a valid signal
        # that this row has no content.
        h = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        if h != existing_hash:
            updates.append((h, decision_id))
    fixed = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i : i + batch_size]
        conn.executemany(
            "UPDATE decisions SET content_hash = ? WHERE decision_id = ?",
            batch,
        )
        fixed += len(batch)
    if fixed:
        conn.commit()
    return fixed


def _recover_decision_dates(conn: sqlite3.Connection) -> int:
    """König audit P4: recover decision_date from full_text for NULL rows.

    Only applies to courts with VERIFIED clean anchor-phrase patterns:
      - zh_verwaltungsgericht: "Endentscheid vom DD.MM.YYYY" (100% precision)
      - gr_gerichte:           "Urteil/Entscheid vom DD. Monat YYYY"
      - bl_gerichte:           "Entscheid vom DD. Monat YYYY"
      - fr_gerichte:           "Arrêt du DD mois YYYY" (FR anchors)
      - be_verwaltungsgericht: "Urteil des Einzelrichters vom DD. Monat YYYY"

    Skipped (ambiguous date contexts — wrong dates worse than NULL):
      - ti_gerichte:  custody dates / cited cases dominate first match
                       (handled separately by ti_date_recovery_2026_04_30.py
                       which fetches source URL for full document)
      - mkg:          1914-archive cited Bundesratsbeschluss dates verified
                       to false-positive ~60% of the time on spot-check
      - hudoc_ch:     handled separately by HUDOC API recovery script

    2026-04-30 audit: recovered 5,258 of 5,339 NULL rows (98.5%) across the
    safe courts. Auto-applies on every nightly so future scraper regressions
    are self-healed where the full_text contains a valid anchor + date.
    """
    import re
    from datetime import date as _date

    DE_MONTHS = {
        "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
        "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
        "oktober": 10, "november": 11, "dezember": 12,
    }
    FR_MONTHS = {
        "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11,
        "décembre": 12, "decembre": 12,
    }
    IT_MONTHS = {
        "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
        "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
        "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    }
    ALL_MONTHS = {**DE_MONTHS, **FR_MONTHS, **IT_MONTHS}

    DE_RE = re.compile(
        r"(\d{1,2})\.\s*(Januar|Februar|M[äa]rz|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)\s+(\d{4})",
        re.IGNORECASE,
    )
    FR_RE = re.compile(
        r"(\d{1,2})\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|"
        r"ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(\d{4})",
        re.IGNORECASE,
    )
    IT_RE = re.compile(
        r"(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|"
        r"luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})",
        re.IGNORECASE,
    )
    DDMMYYYY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
    ANCHORS = (
        # German
        "Urteil vom", "Urteil des", "Urteil der",
        "Entscheid vom", "Entscheid des", "Endentscheid vom",
        "Verfügung vom", "Verfügung des", "Beschluss vom", "Beschluss des",
        # French
        "Arrêt du", "Décision du", "Jugement du", "Ordonnance du",
        # Italian
        "Sentenza del", "Decisione del", "Decreto del",
    )
    THIS_YEAR = _date.today().year

    def _try_parse(d, m, y):
        if isinstance(m, str):
            m = ALL_MONTHS.get(m.lower())
            if not m:
                return None
        if not (1 <= m <= 12 and 1 <= d <= 31):
            return None
        if not (1700 <= y <= THIS_YEAR + 1):
            return None
        try:
            return _date(y, m, d)
        except ValueError:
            return None

    def _extract_first(text):
        cands = []
        for m in DE_RE.finditer(text):
            d = _try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
            if d:
                cands.append((m.start(), d))
        for m in FR_RE.finditer(text):
            d = _try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
            if d:
                cands.append((m.start(), d))
        for m in IT_RE.finditer(text):
            d = _try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
            if d:
                cands.append((m.start(), d))
        for m in DDMMYYYY.finditer(text):
            d = _try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d:
                cands.append((m.start(), d))
        if not cands:
            return None
        cands.sort(key=lambda x: x[0])
        return cands[0][1]

    def _recover(full_text, docket=None):
        if not full_text:
            return None
        head = full_text[:8000]
        for anchor in ANCHORS:
            idx = 0
            while True:
                i = head.lower().find(anchor.lower(), idx)
                if i < 0:
                    break
                window = head[i:i + 200]
                d = _extract_first(window)
                # Docket-year plausibility (backlog L2): skip junk candidates
                # (statute dates, lower-court dates in Praxis digests) and
                # keep searching — a later anchor may hold the true date.
                if d and _recovered_date_plausible(d.isoformat(), docket):
                    return d
                idx = i + len(anchor)
        d = _extract_first(full_text[:5000])
        if d and _recovered_date_plausible(d.isoformat(), docket):
            return d
        return None

    SAFE_COURTS = (
        "zh_verwaltungsgericht", "gr_gerichte", "bl_gerichte",
        "fr_gerichte", "be_verwaltungsgericht",
    )
    placeholders = ",".join("?" * len(SAFE_COURTS))
    rows = conn.execute(
        f"SELECT decision_id, full_text, docket_number FROM decisions "
        f"WHERE court IN ({placeholders}) "
        f"AND (decision_date IS NULL OR decision_date='') "
        f"AND full_text IS NOT NULL AND LENGTH(full_text) > 50",
        SAFE_COURTS,
    ).fetchall()

    updates = []
    for did, ft, docket in rows:
        d = _recover(ft, docket)
        if d:
            updates.append((d.isoformat(), did))

    if updates:
        conn.executemany(
            "UPDATE decisions SET decision_date=? WHERE decision_id=?",
            updates,
        )
        conn.commit()
    return len(updates)


def _normalize_dates(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """König audit 2026-04-30 + 2026-06-07: sanitise invalid decision_date.

    - year-0000 markers ("0000-..." pattern, mostly from gr_gerichte's scraper
      default when the date can't be extracted) → NULL. 796 rows in 2026-04-30.
    - obvious future typos (> today + 365d, e.g. zg_obergericht's "2026-11-01"
      Wahlausschreibung mis-dated) → NULL.
    - pre-1700 dates (ISO-shaped poison like "0206-04-21" for a 2026 docket;
      earliest legitimate decision is BGE 1875) → NULL. BUILD-TIME backstop
      behind models.parse_date's _plausible guard (commit 51dbc48): a scraper
      that writes decision_date WITHOUT going through parse_date (direct field
      copy / JSONL passthrough / recovery heuristics) bypasses that guard, so
      one source typo could otherwise reach the served DB and trip the QC gate
      (which froze HF upload + git push for 4 nights, 2026-06-03..06).

    Soft tolerance for near-future dates (< 365d) preserves legitimate pending
    publications and hearing schedules.
    """
    import datetime as _dt
    cur1 = conn.execute(
        "UPDATE decisions SET decision_date = NULL WHERE decision_date LIKE '0000%'"
    )
    n_zero = cur1.rowcount

    cutoff = (_dt.date.today() + _dt.timedelta(days=365)).isoformat()
    cur2 = conn.execute(
        "UPDATE decisions SET decision_date = NULL WHERE decision_date > ?",
        (cutoff,),
    )
    n_future = cur2.rowcount

    # Pre-1700 clamp. The GLOB enforces ISO YYYY-MM-DD shape BEFORE the lexical
    # compare, so a DD.MM.YYYY value (also length-10, and '15.03.2024' sorts
    # < '1700-01-01' for days 01-16) is never mis-NULLed. This WRITE path must
    # be stricter than the read-only dates.pre_1700 QC check, which omits the
    # shape guard. '0206-04-21' GLOB-matches and is < '1700-01-01' → NULLed.
    cur3 = conn.execute(
        "UPDATE decisions SET decision_date = NULL "
        "WHERE decision_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
        "AND decision_date < '1700-01-01'"
    )
    n_pre1700 = cur3.rowcount

    if n_zero or n_future or n_pre1700:
        conn.commit()
    return n_zero, n_future, n_pre1700


def _fill_chamber_from_docket(conn: sqlite3.Connection) -> int:
    """P1.2: where the portal supplied no chamber, copy the docket's
    register/series code into it (branch_map.docket_chamber_code). Runs
    BEFORE _derive_branch_column so filled codes feed the series rules.
    Never overwrites a portal-supplied chamber."""
    from branch_map import docket_chamber_code

    # Drain candidates before updating (no UPDATEs under an open cursor —
    # the 2026-07-03 WAL-pinning lesson); near-no-op on fresh builds
    # because insert_decision fills the code inline.
    candidates = conn.execute(
        "SELECT decision_id, court, docket_number FROM decisions "
        "WHERE (chamber IS NULL OR chamber = '') AND docket_number IS NOT NULL"
    ).fetchall()
    updates = []
    n = 0
    for did, court, docket in candidates:
        code = docket_chamber_code(court, docket)
        if code:
            updates.append((code, did))
            if len(updates) >= 5000:
                conn.executemany(
                    "UPDATE decisions SET chamber=? WHERE decision_id=?", updates)
                n += len(updates)
                updates = []
    if updates:
        conn.executemany(
            "UPDATE decisions SET chamber=? WHERE decision_id=?", updates)
        n += len(updates)
    conn.commit()
    return n


def _derive_branch_column(conn: sqlite3.Connection) -> int:
    """P1.1: populate the coarse ``branch`` column (zivil | straf |
    oeffentlich | sozialversicherung) from branch_map.derive_branch —
    court map, per-court docket rules, official chamber names. NULL over
    guess; the same module drives the parquet export so all doors agree."""
    from branch_map import derive_branch

    # Only rows still lacking a branch (inline derivation at insert fills
    # fresh builds, so this is a near-no-op there). Candidates are fully
    # drained BEFORE updating: interleaving UPDATEs under the open read
    # cursor pinned the WAL to 34GB on 2026-07-03 (a ~3h I/O tax).
    candidates = conn.execute(
        "SELECT decision_id, court, chamber, docket_number FROM decisions "
        "WHERE branch IS NULL OR branch = ''"
    ).fetchall()
    updates = []
    n = 0
    for did, court, chamber, docket in candidates:
        b = derive_branch(court, chamber, docket)
        if b:
            updates.append((b, did))
            if len(updates) >= 5000:
                conn.executemany(
                    "UPDATE decisions SET branch=? WHERE decision_id=?", updates)
                n += len(updates)
                updates = []
    if updates:
        conn.executemany(
            "UPDATE decisions SET branch=? WHERE decision_id=?", updates)
        n += len(updates)
    conn.commit()
    return n


def _null_implausible_gr_dates(conn: sqlite3.Connection) -> int:
    """Backlog L2 (2026-07-02): NULL gr_gerichte dates that are >3y before
    the docket's registration/volume year.

    The GR Tribuna register carried only upload dates, so a past text-recovery
    pass (scripts/repair_decision_dates.py) wrote body-text dates INTO the
    shard — grabbing statute-validity dates, LOWER-court ruling dates and
    referendum cutoffs in the PKG/PVG Praxis digests (e.g. 'PVG 2021 1' dated
    2018-01-01 = the IVV Art. 27bis validity date visible in the digest head).
    188 impossible rows measured 2026-07-02. Runs BEFORE
    _recover_decision_dates, whose (now plausibility-guarded) anchor search
    may then legitimately refill a NULLed row from a deeper true-date anchor.
    NULL over guess: a missing date beats a wrong one. gr-scoped — other
    courts' stocks (ge, zh_vwg, ne …) need their own provenance checks first.
    """
    n = 0
    for did, docket, d in conn.execute(
        "SELECT decision_id, docket_number, decision_date FROM decisions "
        "WHERE court='gr_gerichte' AND docket_number IS NOT NULL "
        "AND decision_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
    ).fetchall():
        if not _recovered_date_plausible(d, docket):
            conn.execute(
                "UPDATE decisions SET decision_date=NULL WHERE decision_id=?",
                (did,),
            )
            n += 1
    if n:
        conn.commit()
    return n


def _dedup_egmr_in_bge(conn: sqlite3.Connection) -> int:
    """König audit #1: drop ECHR/CEDH cases that the BGE scraper picked up.

    Some BGE decisions cross-reference Strasbourg judgments and the BGE scraper
    follows the link, ingesting the EGMR case under court='bge' with a
    cedh.coe.int URL. The dedicated bge_egmr scraper also ingests the same case
    under court='bge_egmr'. This produces 474 duplicate pairs (pre-fix).

    Yesterday's audit applied a one-off DELETE that didn't survive the next
    rebuild because the source JSONL still contains the misclassified rows.
    This pass codifies the cleanup so the duplicates don't recur every nightly.

    Conservative: only deletes a bge row when the matching bge_egmr row exists
    (matched on identical source_url). Archive-unique entries are preserved.
    """
    cur = conn.execute(
        """DELETE FROM decisions
           WHERE court = 'bge'
             AND source_url LIKE '%cedh%'
             AND EXISTS (
               SELECT 1 FROM decisions d2
               WHERE d2.court = 'bge_egmr'
                 AND d2.source_url = decisions.source_url
             )""",
    )
    deleted = cur.rowcount
    if deleted:
        conn.commit()
    return deleted


def _remove_stubs(conn: sqlite3.Connection) -> int:
    """Remove decisions that are completely empty (no text AND no regeste).

    Only removes entries where both full_text and regeste are empty or
    near-empty.  Even short entries carry docket numbers, dates, court
    assignments and topic keywords that enable search and coverage.
    Decisions with failed PDF extraction but a regeste, or entscheidsuche
    metadata entries with topic keywords, are all kept.
    """
    result = conn.execute(
        """DELETE FROM decisions
           WHERE LENGTH(COALESCE(full_text, '')) < 10
             AND LENGTH(COALESCE(regeste, '')) < 10""",
    )
    deleted = result.rowcount
    if deleted:
        conn.commit()
    return deleted


def _fill_missing_regeste(conn: sqlite3.Connection) -> int:
    """Extract regeste from full_text for BGer/BGE decisions with empty regeste."""
    cursor = conn.execute(
        """
        SELECT decision_id, full_text FROM decisions
        WHERE court IN ('bger', 'bge')
          AND (regeste IS NULL OR LENGTH(TRIM(regeste)) = 0)
          AND LENGTH(COALESCE(full_text, '')) > 200
        """
    )
    updated = 0
    batch: list[tuple[str, str]] = []
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        for decision_id, full_text in rows:
            regeste = _extract_regeste_from_text(full_text or "")
            if regeste:
                batch.append((regeste, decision_id))
        if batch:
            conn.executemany(
                "UPDATE decisions SET regeste = ?, content_hash = NULL WHERE decision_id = ?",
                batch,
            )
            updated += len(batch)
            batch.clear()

    if updated:
        conn.commit()
    return updated


def _log_quality_summary(conn: sqlite3.Connection) -> None:
    """Log a summary of remaining data quality issues.

    Note: the previous version included a `LENGTH(full_text) < 500` count which
    forced a full table scan reading every full_text blob (~31 min on 970k
    rows / 60 GB DB measured 2026-04-30). That metric was a one-line log only
    — not load-bearing — so we drop it. The remaining two checks scan small
    columns (regeste, decision_date) without overflow pages and are cheap.
    """
    total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    no_regeste = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE regeste IS NULL OR regeste = ''"
    ).fetchone()[0]
    no_date = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date IS NULL OR decision_date = ''"
    ).fetchone()[0]
    logger.info(f"  Quality: {no_regeste} no regeste, {no_date} no date (of {total})")


# ────────────────────────────────────────────────────────────────────
# Inline cleanups (2026-05-05) — pure per-row transformations.
#
# These mirror the logic of the post-import _normalize_* / _migrate_* /
# _recover_* / _truncate_* / _fill_* SQL passes so that every row is
# already in canonical form by the time it's INSERTed. The post-pass
# UPDATEs still run as a safety net (idempotent — they find nothing to
# update on a fresh build, completing in seconds rather than ~2h on
# the critical path).
#
# Why this is safe (vs. the rejected "post-swap UPDATE" alternative):
#   • The atomic-swap pattern keeps workers' immutable=1 contract intact
#     because all writes happen to decisions.db.tmp BEFORE swap.
#   • The work moves earlier in the same single-writer window — it does
#     NOT touch the live DB.
#   • The post-pass UPDATEs continue to run unchanged; if any inline
#     helper has a bug, the SQL pass catches it (idempotent recovery).
# ────────────────────────────────────────────────────────────────────


def _docket_normalize_inline(docket):
    """Collapse internal newlines/tabs + multiple spaces; trim."""
    if not docket:
        return docket
    s = str(docket)
    if "\n" in s or "\r" in s or "\t" in s:
        s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


_HOST_BY_COURT_INLINE = {
    "bs_gerichte": "https://www.gerichte.bs.ch",
    "gl_gerichte": "https://findinfo.gl.ch",
}


def _source_url_normalize_inline(court, source_url):
    """Prefix host for relative source_urls on the bs/gl Tribuna platform."""
    if not source_url or not court:
        return source_url
    s = str(source_url)
    if s.startswith("http"):
        return s
    host = _HOST_BY_COURT_INLINE.get(court)
    return host + s if host else s


def _date_normalize_inline(date_str):
    """year-0000 → None; obvious far-future typos (>today+365d) → None;
    pre-1700 ISO dates (source typos like '0206-04-21' for a 2026 docket) → None.
    Mirrors the SQL post-pass _normalize_dates safety net per-value."""
    if not date_str:
        return date_str
    s = str(date_str).strip()
    if s.startswith("0000"):
        return None
    # Pre-1700 clamp — ISO-shaped only (s[4]/s[7] == '-'), so a DD.MM.YYYY
    # value is never mis-NULLed by the lexical compare.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s < "1700-01-01":
        return None
    try:
        from datetime import date as _date_cls, timedelta
        cutoff = (_date_cls.today() + timedelta(days=365)).isoformat()
        if s > cutoff:
            return None
    except Exception:
        pass
    return s


def _regeste_truncate_inline(regeste, full_text):
    """HUDOC duplication artefact: regeste >8 K chars near-duplicating
    full_text → keep only the head-note up to the first body-boundary
    marker (Sachverhalt / Faits / Fatti / Fakten / Procédure / Procedura)."""
    if not regeste or len(regeste) <= 8000:
        return regeste
    full_len = len(full_text or "")
    if full_len < 1000 or len(regeste) < 0.9 * full_len:
        return regeste
    cut = None
    for marker in _REGESTE_BODY_BOUNDARIES:
        idx = regeste.find(marker)
        if idx > 0 and (cut is None or idx < cut):
            cut = idx
    new_regeste = regeste[:cut] if cut else regeste[:5000]
    return new_regeste.rstrip()


def _compute_row_content_hash_inline(regeste, full_text):
    """SHA-256(regeste || full_text). Mirror of _compute_content_hashes()."""
    import hashlib
    content = ((regeste or "") + (full_text or "")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


# Date-recovery helper — same anchor-phrase + month-name patterns as
# _recover_decision_dates(). Lifted to module level so both the inline
# insert path and the post-pass UPDATE can share one source of truth.

_DATE_DE_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}
_DATE_FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}
_DATE_IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_DATE_ALL_MONTHS = {**_DATE_DE_MONTHS, **_DATE_FR_MONTHS, **_DATE_IT_MONTHS}

_DATE_DE_RE = re.compile(
    r"(\d{1,2})\.\s*(Januar|Februar|M[äa]rz|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember)\s+(\d{4})", re.IGNORECASE,
)
_DATE_FR_RE = re.compile(
    r"(\d{1,2})\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|"
    r"ao[ûu]t|septembre|octobre|novembre|d[ée]cembre)\s+(\d{4})",
    re.IGNORECASE,
)
_DATE_IT_RE = re.compile(
    r"(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|"
    r"luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(\d{4})",
    re.IGNORECASE,
)
_DATE_DDMMYYYY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DATE_ANCHORS = (
    "Urteil vom", "Urteil des", "Urteil der",
    "Entscheid vom", "Entscheid des", "Endentscheid vom",
    "Verfügung vom", "Verfügung des", "Beschluss vom", "Beschluss des",
    "Arrêt du", "Décision du", "Jugement du", "Ordonnance du",
    "Sentenza del", "Decisione del", "Decreto del",
)
_DATE_SAFE_COURTS = (
    "zh_verwaltungsgericht", "gr_gerichte", "bl_gerichte",
    "fr_gerichte", "be_verwaltungsgericht",
)


def _date_try_parse(d, m, y):
    from datetime import date as _date_cls
    if isinstance(m, str):
        m = _DATE_ALL_MONTHS.get(m.lower())
        if not m:
            return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    if not (1700 <= y <= _date_cls.today().year + 1):
        return None
    try:
        return _date_cls(y, m, d)
    except ValueError:
        return None


def _date_extract_first(text):
    cands = []
    for m in _DATE_DE_RE.finditer(text):
        d = _date_try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
        if d:
            cands.append((m.start(), d))
    for m in _DATE_FR_RE.finditer(text):
        d = _date_try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
        if d:
            cands.append((m.start(), d))
    for m in _DATE_IT_RE.finditer(text):
        d = _date_try_parse(int(m.group(1)), m.group(2), int(m.group(3)))
        if d:
            cands.append((m.start(), d))
    for m in _DATE_DDMMYYYY_RE.finditer(text):
        d = _date_try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            cands.append((m.start(), d))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


# ── Docket-year plausibility for recovered dates (backlog L2, 2026-07-02) ──
# Text-recovered dates grabbed statute-validity dates, LOWER-court ruling
# dates and referendum cutoffs in GR's PKG/PVG Praxis digests (188 rows dated
# years before their docket/volume year; proven via the shard's
# date_extraction provenance: 'bare_header' + mismatched 'header_de'). A
# recovered date >3 years BEFORE the docket's registration/volume year is
# junk — courts do not rule before a case exists, and Praxis volumes do not
# publish decisions older than a few years. Strict year positions only
# (mirrors quality/checks/dates.docket_registration_year): NULL over guess.
_DOCKET_YEAR_TRAILING_RE = re.compile(r"[/_.]((?:18[7-9]|19\d|20[0-3])\d)\s*$")
_DOCKET_YEAR_SERIES_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9]{0,14}[ ./-]((?:18[7-9]|19\d|20[0-3])\d)[ ./-]\d")


def _docket_registration_year(docket):
    if not docket:
        return None
    m = _DOCKET_YEAR_TRAILING_RE.search(docket)
    if m:
        return int(m.group(1))
    m = _DOCKET_YEAR_SERIES_RE.match(docket)
    if m:
        return int(m.group(1))
    return None


def _recovered_date_plausible(iso_date, docket):
    """False iff the date is >3y before the docket's registration year.
    None-safe: no strict docket year, or unparseable date → True (no veto)."""
    year = _docket_registration_year(docket or "")
    if year is None or not iso_date or len(str(iso_date)) < 4:
        return True
    try:
        return int(str(iso_date)[:4]) >= year - 3
    except ValueError:
        return True


def _date_recover_inline(court, full_text, docket=None):
    """König P4: recover decision_date from full_text (5 safe courts only).

    Returns ISO date string ('YYYY-MM-DD') or None. Mirrors the logic of
    the post-import _recover_decision_dates() pass — both share the same
    anchor phrases and month-name regexes via the module-level helpers.
    Candidates failing docket-year plausibility are skipped (the anchor
    search continues — a later anchor may carry the true ruling date);
    an implausible bare-fallback candidate returns None.
    """
    if court not in _DATE_SAFE_COURTS:
        return None
    if not full_text or len(full_text) < 50:
        return None
    head = full_text[:8000]
    head_lower = head.lower()
    for anchor in _DATE_ANCHORS:
        idx = 0
        anchor_lower = anchor.lower()
        while True:
            i = head_lower.find(anchor_lower, idx)
            if i < 0:
                break
            window = head[i:i + 200]
            d = _date_extract_first(window)
            if d and _recovered_date_plausible(d.isoformat(), docket):
                return d.isoformat()
            idx = i + len(anchor)
    d = _date_extract_first(full_text[:5000])
    if d and _recovered_date_plausible(d.isoformat(), docket):
        return d.isoformat()
    return None


# Court code remapping: merge historical variants into canonical codes
COURT_REMAP = {
    "bge_historical": "bge",
}
# Decision ID prefix remapping (must match COURT_REMAP)
ID_PREFIX_REMAP = {
    "bge_historical_": "bge_",
}


# Derived-classification columns added 2026-07-03. ensure_derived_columns
# lets insert_decision write into DBs built before the columns existed
# (quick_publish works on a copy of the SERVED db; the salvaged 07-03 db
# lacks the last five). ALTER ADD COLUMN is metadata-only in SQLite.
DERIVED_COLUMNS = (
    ("branch", "TEXT"), ("proceeding_type", "TEXT"),
    ("procedural_code", "TEXT"), ("appealed_court_raw", "TEXT"),
    ("appealed_date", "TEXT"), ("appealed_docket", "TEXT"),
)


def ensure_derived_columns(conn: sqlite3.Connection) -> int:
    have = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    added = 0
    for name, typ in DERIVED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE decisions ADD COLUMN {name} {typ}")
            added += 1
    return added


_BGE_D2_RE = re.compile(r"\b(\d[A-Z])[._ ](\d+)/(\d{4})\b")


def _derive_bge_docket2_inline(full_text, decision_date):
    """L1 fix: the underlying BGer docket from the BGE text head.

    The es-era metadata carried this link (docket_number_2); the direct
    bge scraper does not, collapsing the BGE<->BGer join for 2025+
    volumes (8/183). The header states it ("... i.S. A. gegen B.
    (Beschwerde in Zivilsachen) 4A_576/2024 vom ..."): first separator-
    tolerant BGG-form docket in the head whose year is plausible for the
    volume (measured 164/185 = 89% recoverable; Regeste-only stubs stay
    NULL over guess)."""
    if not full_text:
        return None
    head = full_text[:4000]
    vol_year = None
    if decision_date and str(decision_date)[:4].isdigit():
        vol_year = int(str(decision_date)[:4])
    for m in _BGE_D2_RE.finditer(head):
        year = int(m.group(3))
        if vol_year and not (vol_year - 3 <= year <= vol_year + 1):
            continue
        return f"{m.group(1)}_{m.group(2)}/{m.group(3)}"
    return None


def insert_decision(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert a single decision. Returns True if inserted, False if
    skipped (duplicate or stub).

    Inline cleanups (2026-05-05): every per-row normalisation that the
    publish pipeline used to run as a separate post-import UPDATE pass
    is now applied at insert time:

      * docket whitespace + internal newlines collapsed
      * source_url host prefixed for bs/gl_gerichte
      * year-0000 / far-future dates → NULL
      * decision_date recovered from full_text where the scraper missed it
        (5 safe courts, anchor-phrase + month-name regex)
      * short full_text (10–99 chars) migrated to regeste
      * oversized regeste (HUDOC duplication artefact) truncated at the
        first body-boundary marker
      * missing regeste extracted from full_text for BGer/BGE
      * stub rows (text<10 AND regeste<10) dropped (return False)
      * content_hash = SHA-256(regeste || full_text) computed inline

    The post-import UPDATE passes (_normalize_dockets, _normalize_dates,
    _truncate_oversized_regestes, _migrate_short_text_to_regeste,
    _normalize_source_urls, _remove_stubs, _fill_missing_regeste,
    _recover_decision_dates, _compute_content_hashes) still run as a
    safety net — they're idempotent so on a clean rebuild they find
    nothing to update and complete in seconds rather than the ~2h they
    used to need on the post-import critical path.
    """
    try:
        # Remap court codes and decision IDs (e.g. bge_historical → bge)
        court = row.get("court", "")
        if court in COURT_REMAP:
            row["court"] = COURT_REMAP[court]
            court = row["court"]
        did = row.get("decision_id", "")
        for old_prefix, new_prefix in ID_PREFIX_REMAP.items():
            if did.startswith(old_prefix):
                row["decision_id"] = new_prefix + did[len(old_prefix):]
                break

        # Clean text fields
        for field in ("full_text", "regeste", "title"):
            if field in row and row[field]:
                row[field] = _clean_text(row[field])

        # ── Inline cleanups (post-pass UPDATEs are now mostly no-ops) ──

        # docket whitespace + internal newline collapse
        row["docket_number"] = _docket_normalize_inline(row.get("docket_number"))

        # source_url host prefix for the bs/gl Tribuna platform
        row["source_url"] = _source_url_normalize_inline(
            court, row.get("source_url"),
        )

        # year-0000 / far-future date sanitisation
        row["decision_date"] = _date_normalize_inline(row.get("decision_date"))

        # date recovery from full_text where the scraper missed
        if not row.get("decision_date"):
            recovered = _date_recover_inline(court, row.get("full_text"),
                                             row.get("docket_number"))
            if recovered:
                row["decision_date"] = recovered

        # gross date-inversion guard: NULL a publication_date that precedes
        # the ruling by >1 month (impossible → mislabel/parse error). Runs
        # after decision_date is finalised so the comparison is reliable.
        row["publication_date"] = _date_inversion_guard_inline(
            row.get("decision_date"), row.get("publication_date"))

        # short full_text → regeste migration (König P7)
        if (
            10 <= len(str(row.get("full_text") or "")) <= 99
            and not (row.get("regeste") or "").strip()
        ):
            row["regeste"] = row["full_text"]
            row["full_text"] = None

        # oversized regeste truncation (HUDOC duplication)
        row["regeste"] = _regeste_truncate_inline(
            row.get("regeste"), row.get("full_text"),
        )

        # missing regeste extraction for BGer/BGE
        if (
            court in ("bger", "bge")
            and not (row.get("regeste") or "").strip()
            and len(str(row.get("full_text") or "")) > 200
        ):
            extracted = _extract_regeste_from_text(row.get("full_text") or "")
            if extracted:
                row["regeste"] = extracted

        # stub filter — drop the row before insert
        if (
            len(str(row.get("full_text") or "")) < 10
            and len(str(row.get("regeste") or "")) < 10
        ):
            return False

        # SHA-256 content hash — computed inline so the post-pass
        # _compute_content_hashes() finds nothing to update.
        row["content_hash"] = _compute_row_content_hash_inline(
            row.get("regeste"), row.get("full_text"),
        )

        # ── Derived classification (inline, 2026-07-03) ────────────────
        # Computed at INSERT so no post-pass UPDATE loop runs over the
        # full table: the 07-03 build paid a ~3h I/O tax when 640k branch
        # UPDATEs ran under a pinned read cursor (34GB WAL). Post-passes
        # remain as guards for rows that predate these columns.
        from branch_map import derive_branch, docket_chamber_code
        from proceeding_map import derive_proceeding

        if not (row.get("chamber") or "").strip():
            code = docket_chamber_code(court, row.get("docket_number") or "")
            if code:
                row["chamber"] = code
        if not row.get("branch"):
            row["branch"] = derive_branch(
                court, row.get("chamber"), row.get("docket_number"))
        if not row.get("proceeding_type"):
            slug, pcode = derive_proceeding(
                court, row.get("chamber"), row.get("docket_number"))
            row["proceeding_type"] = slug
            row["procedural_code"] = row.get("procedural_code") or pcode
        if court == "bger" and not row.get("appealed_date"):
            from appeal_extract import extract_appealed
            ap = extract_appealed(row.get("full_text") or "")
            if ap:
                row["appealed_court_raw"] = ap["appealed_court_raw"]
                row["appealed_date"] = ap["appealed_date"]
                row["appealed_docket"] = ap["appealed_docket"]
        if court == "bge" and not (row.get("docket_number_2") or "").strip():
            d2 = _derive_bge_docket2_inline(
                row.get("full_text"), row.get("decision_date"))
            if d2:
                row["docket_number_2"] = d2

        # Handle cited_decisions — could be list or JSON string
        cited = row.get("cited_decisions", [])
        if isinstance(cited, list):
            cited = json.dumps(cited)
        row["cited_decisions"] = cited

        # json_data: full row as JSON blob (after cleaning)
        row["json_data"] = json.dumps(row, default=str)

        # Canonical key for dedup (aggressive normalization of court+docket+date)
        row["canonical_key"] = make_canonical_key(
            row.get("court", ""), row.get("docket_number", ""), row.get("decision_date"),
        )

        # Build values tuple matching INSERT_COLUMNS order.
        # Convert None-like values properly (avoid storing literal "None" strings).
        def _val(col: str):
            v = row.get(col)
            if v is None or v == "None":
                return None
            if col in ("decision_date", "publication_date", "scraped_at") and v:
                return str(v) if v else None
            return v

        values = tuple(_val(col) for col in INSERT_COLUMNS)

        cursor = conn.execute(INSERT_OR_IGNORE_SQL, values)
        if cursor.rowcount > 0:
            return True

        # ── Collision disambiguation ───────────────────────────────────
        # INSERT OR IGNORE dropped the row because decision_id already
        # exists. Distinguish two cases:
        #   (i)  TRUE duplicate — same canonical_key (court, docket, date)
        #         → genuinely the same decision, skip.
        #   (ii) Same docket but DIFFERENT date — admin courts (BVGer,
        #         BGer, etc.) often have multiple decisions under one
        #         docket (Zwischenverfügung, Teilurteil, Endurteil,
        #         Revision …). User-reported example: BVGer B-1092/2009
        #         has decisions dated both 2009-02-20 and 2010-01-05;
        #         the second was silently dropped historically.
        #         → re-id the new row with a `_d{YYYYMMDD}` date suffix
        #           and retry the insert. ~200 BVGer historical cases
        #           plus +5-50/year going forward.
        existing = conn.execute(
            "SELECT canonical_key FROM decisions WHERE decision_id = ?",
            (row["decision_id"],),
        ).fetchone()
        if existing is None:
            return False  # defensive: shouldn't happen on real collisions
        if existing[0] == row.get("canonical_key"):
            # case (i): same decision already stored (direct shard won via
            # direct-first ordering). Normally a true duplicate to skip —
            # BUT if THIS copy carries substantially richer full_text,
            # upgrade the stored text in place. The Ticino truncation
            # (measured 2026-06-15): the direct ti_gerichte scraper stores
            # ~1.5K of truncated PDF text while es_ti_gerichte holds the
            # full 20K+; ~40K decisions corpus-wide (98.5% Ticino) were
            # served truncated. We keep the existing row's metadata
            # (chamber labels — the reason for direct-first; same
            # canonical_key ⇒ court/docket/date identical) and only swap in
            # the longer text (+ regeste if the stored row lacks one). The
            # decisions_au trigger reindexes decisions_fts, so search recall
            # gets the restored text too. Threshold matches the audit:
            # incoming >2x longer AND +1000 chars.
            incoming_ft = row.get("full_text") or ""
            cur = conn.execute(
                "SELECT full_text, regeste FROM decisions WHERE decision_id = ?",
                (row["decision_id"],),
            ).fetchone()
            existing_len = len(cur[0] or "") if cur else 0
            if (len(incoming_ft) > existing_len * 2
                    and len(incoming_ft) - existing_len > 1000):
                final_regeste = (cur[1] if cur and cur[1] else None) or row.get("regeste")
                new_hash = _compute_row_content_hash_inline(final_regeste, incoming_ft)
                conn.execute(
                    "UPDATE decisions SET full_text = ?, regeste = ?, "
                    "content_hash = ? WHERE decision_id = ?",
                    (incoming_ft, final_regeste, new_hash, row["decision_id"]),
                )
                logger.info(
                    "text-upgrade: %s %s — full_text %d -> %d chars "
                    "(richer shard copy)",
                    row.get("court"), row.get("docket_number"),
                    existing_len, len(incoming_ft),
                )
                return True
            return False  # true duplicate, no richer text — skip as before
        # case (ii): same id but different (court, docket, date) tuple
        date_str = str(row.get("decision_date") or "").replace("-", "")
        if not date_str or len(date_str) != 8:
            # Can't disambiguate without a clean YYYYMMDD date — accept the
            # drop, log so we have visibility on the rate.
            logger.warning(
                "docket-collision drop (no clean date): %s vs existing",
                row.get("decision_id"),
            )
            return False
        new_id = f"{row['decision_id']}_d{date_str}"
        row["decision_id"] = new_id
        # rebuild json_data so the embedded id matches
        row["json_data"] = json.dumps(row, default=str)
        values = tuple(_val(col) for col in INSERT_COLUMNS)
        cursor = conn.execute(INSERT_OR_IGNORE_SQL, values)
        if cursor.rowcount > 0:
            logger.info(
                "docket-collision disambiguated: court=%s docket=%s date=%s -> id=%s",
                row.get("court"), row.get("docket_number"),
                row.get("decision_date"), new_id,
            )
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to import {row.get('decision_id', '?')}: {e}")
        return False


def import_jsonl(
    conn: sqlite3.Connection, jsonl_dir: Path, checkpoint: dict | None = None,
) -> tuple[int, int, dict]:
    """Import decisions from JSONL files.

    Args:
        conn: SQLite connection.
        jsonl_dir: Directory containing .jsonl files.
        checkpoint: If provided, a dict mapping filename -> {"size": int, "imported": int}.
            Files whose size matches the checkpoint are skipped entirely; files that grew
            are read starting from the checkpoint byte offset (JSONL files are append-only).

    Returns:
        (imported, skipped, new_checkpoint) where new_checkpoint has the same structure.
    """
    imported = 0
    skipped = 0
    new_checkpoint: dict = {}

    # Process direct shards (filename does not start with "es_") BEFORE
    # entscheidsuche shards.  This prevents the SG-bug-class
    # alphabetical-collision pattern (commit f249f1f, 2026-04-30):
    # es_<court>.jsonl uses generic court_code in SPIDER_MAP, while
    # direct <court>.jsonl writes chamber-specific court codes.  When
    # both shards happen to use the SAME decision_id prefix and es
    # processes first (alphabetical order, 'e' < most letters),
    # INSERT OR IGNORE silently drops direct's chamber-specific rows.
    # Direct-first ordering means direct's chamber labels win all
    # dedups; es supplements with rows that have no direct counterpart.
    # Trade-off: for shared decisions, direct's metadata wins over es.
    # Set BUILD_FTS5_DIRECT_FIRST=0 to revert to plain-alphabetical
    # order (use only as an emergency revert).
    _direct_first = os.environ.get("BUILD_FTS5_DIRECT_FIRST", "1") not in ("0", "false", "no")
    _all_files = sorted(jsonl_dir.glob("*.jsonl"))
    if _direct_first:
        _direct_shards = [f for f in _all_files if not f.name.startswith("es_")]
        _es_shards = [f for f in _all_files if f.name.startswith("es_")]
        _files_to_process = _direct_shards + _es_shards
    else:
        _files_to_process = _all_files

    for jsonl_file in _files_to_process:
        fname = jsonl_file.name
        current_size = jsonl_file.stat().st_size

        if checkpoint is not None:
            prev = checkpoint.get(fname, {})
            prev_size = prev.get("size", 0)

            if current_size == prev_size:
                # No new data — carry forward checkpoint entry unchanged
                new_checkpoint[fname] = prev
                continue

            if current_size < prev_size:
                # File shrank (unexpected) — read from start to be safe
                logger.warning(
                    f"  {fname}: size shrank ({prev_size} → {current_size}), reading from start"
                )
                prev_size = 0
        else:
            prev_size = 0

        file_imported = 0
        with open(jsonl_file, "rb") as fb:
            if prev_size > 0:
                fb.seek(prev_size)
                # Discard partial line at seek position (seek may land mid-
                # UTF-8 character or mid-JSON line if file was rewritten)
                fb.readline()
                logger.debug(f"  {fname}: seeking to byte {prev_size} (skipped partial line)")
            # Wrap in text mode for remaining lines
            f = io.TextIOWrapper(fb, encoding="utf-8", errors="replace")
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if insert_decision(conn, row):
                        imported += 1
                        file_imported += 1
                    else:
                        skipped += 1
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in {jsonl_file}: {e}")
                except Exception as e:
                    logger.warning(f"Error importing from {jsonl_file}: {e}")

        if file_imported:
            conn.commit()
            logger.info(f"  {jsonl_file.name}: +{file_imported} decisions")
        elif checkpoint is not None and prev_size > 0:
            logger.debug(f"  {fname}: no new decisions (new bytes were dupes)")

        prev_imported = (checkpoint or {}).get(fname, {}).get("imported", 0)
        new_checkpoint[fname] = {
            "size": current_size,
            "imported": prev_imported + file_imported,
        }

    return imported, skipped, new_checkpoint


def import_parquet(conn: sqlite3.Connection, parquet_dir: Path) -> tuple[int, int]:
    """Import decisions from Parquet shards. Returns (imported, skipped)."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        logger.info("pyarrow not installed, skipping Parquet import")
        return 0, 0

    if not parquet_dir.exists():
        return 0, 0

    imported = 0
    skipped = 0

    for pf in sorted(parquet_dir.glob("*.parquet")):
        try:
            table = pq.read_table(pf)
            file_imported = 0
            for batch in table.to_batches():
                for row in batch.to_pylist():
                    if insert_decision(conn, row):
                        imported += 1
                        file_imported += 1
                    else:
                        skipped += 1
            conn.commit()
            if file_imported:
                logger.info(f"  {pf.name}: +{file_imported} decisions")
        except Exception as e:
            logger.warning(f"Failed to read {pf}: {e}")

    return imported, skipped


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add missing columns to an existing decisions table.

    Safe to call on every startup — ALTER TABLE ADD COLUMN is a no-op
    if the column already exists (caught by the try/except).
    """
    migrations = [
        ("canonical_key", "TEXT"),
        # Per-decision SHA-256 of (regeste || full_text). Computed at the
        # tail of every full rebuild via _compute_content_hashes(). Lets
        # any consumer prove "what we served on date X for decision Y was
        # exactly this content" without re-fetching the row.
        ("content_hash", "TEXT"),
    ]
    for col_name, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE decisions ADD COLUMN {col_name} {col_type}")
            logger.info(f"Schema migration: added column '{col_name}'")
        except sqlite3.OperationalError:
            pass  # column already exists


def _fts5_optimize_with_heartbeat(conn, interval: int = 600) -> None:
    """Run the FTS5 'optimize' merge while emitting a heartbeat line every ``interval`` seconds.

    'optimize' is one blocking SQLite call that runs for hours with NO output; the publish
    stall-watchdog (publish.py — kills a step after N seconds with no output line) false-killed
    this healthy step on 2026-06-22 once optimize crossed the 4h mark. The optimize runs in THIS
    (main) thread — the connection is never touched cross-thread — while a daemon thread only
    prints (never touches ``conn``), so there is no SQLite threading hazard. ``flush=True`` makes
    each heartbeat reach the subprocess stdout immediately (pipes are block-buffered otherwise),
    resetting the watchdog's idle timer well under its limit.
    """
    import threading
    import time as _t

    stop = threading.Event()
    t0 = _t.monotonic()

    def _beat() -> None:
        while not stop.wait(interval):
            print(
                f"  FTS5 optimize still running… {int((_t.monotonic() - t0) // 60)}m elapsed (heartbeat)",
                flush=True,
            )

    hb = threading.Thread(target=_beat, name="fts5-optimize-heartbeat", daemon=True)
    hb.start()
    try:
        conn.execute("INSERT INTO decisions_fts(decisions_fts) VALUES('optimize')")
        conn.commit()
    finally:
        stop.set()
        hb.join(timeout=2)


def build_database(
    output_dir: Path,
    db_path: Path | None = None,
    incremental: bool = False,
    no_optimize: bool = False,
    full_rebuild: bool = False,
) -> Path:
    """
    Build/update the FTS5 database from all available sources.

    Args:
        output_dir: Directory containing decisions/ and data/ subdirs.
        db_path: Path for the SQLite DB (default: output_dir/decisions.db).
        incremental: Only read new bytes from JSONL files using checkpoint.
        no_optimize: Skip the FTS5 optimize step.
        full_rebuild: Delete existing DB and checkpoint, rebuild from scratch.

    Returns the path to the database.
    """
    db_path = db_path or output_dir / "decisions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / ".fts5_checkpoint.json"

    # Full rebuild: build to a temp file, swap at the end (zero downtime)
    # Resolve symlinks so temp file is on the same filesystem (atomic rename)
    final_db_path = None
    if full_rebuild:
        final_db_path = db_path.resolve()
        db_path = final_db_path.with_suffix(".db.tmp")
        if db_path.exists():
            logger.info(f"Full rebuild: removing stale temp DB {db_path}")
            db_path.unlink()
        # Also remove any leftover WAL/SHM for the temp DB
        for suffix in (".db.tmp-wal", ".db.tmp-shm"):
            p = db_path.parent / (db_path.stem.replace(".db", "") + suffix)
            if p.exists():
                p.unlink()
        if checkpoint_path.exists():
            logger.info(f"Full rebuild: deleting {checkpoint_path}")
            checkpoint_path.unlink()

    # Load checkpoint for incremental mode
    checkpoint = None
    if incremental and checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text()).get("files", {})
            logger.info(f"Loaded checkpoint: {len(checkpoint)} files tracked")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load checkpoint, reading all files: {e}")
            checkpoint = None

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA_SQL)
    conn.executescript(COVERAGE_SCHEMA_SQL)

    # Migrate: add columns that may be missing in older databases
    _migrate_schema(conn)

    # Count existing
    existing = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    jsonl_dir = output_dir / "decisions"
    if incremental and checkpoint is None:
        logger.info(
            "No checkpoint file found — first incremental run will read all JSONL files. "
            "Subsequent runs will be fast. To skip this, run --full-rebuild first."
        )

    # Import from JSONL (run_scraper.py output)
    jsonl_imported, jsonl_skipped, new_checkpoint = 0, 0, {}
    if jsonl_dir.exists():
        logger.info(f"Importing from JSONL: {jsonl_dir}")
        jsonl_imported, jsonl_skipped, new_checkpoint = import_jsonl(
            conn, jsonl_dir, checkpoint if incremental else None,
        )

    # Import from Parquet (pipeline.py output)
    parquet_dir = output_dir / "data" / "daily"
    pq_imported, pq_skipped = 0, 0
    if parquet_dir.exists():
        logger.info(f"Importing from Parquet: {parquet_dir}")
        pq_imported, pq_skipped = import_parquet(conn, parquet_dir)

    total_imported = jsonl_imported + pq_imported
    total_skipped = jsonl_skipped + pq_skipped

    # ── Post-import data quality passes ──
    if total_imported > 0:
        with _phase_timer("dedup decisions"):
            deduped = _dedup_decisions(conn)
            if deduped:
                logger.info(f"  Removed {deduped} duplicate decisions")

        with _phase_timer("cross-court dedup"):
            cross_deduped = _cross_court_dedup(conn)
            if cross_deduped:
                logger.info(f"  Removed {cross_deduped} cross-court duplicates")

        with _phase_timer("EGMR dedup"):
            egmr_deduped = _dedup_egmr_in_bge(conn)
            if egmr_deduped:
                logger.info(f"  Removed {egmr_deduped} bge+cedh duplicates (canonical entries remain in bge_egmr)")

        with _phase_timer("normalise dockets + dates"):
            ws_fixed = _normalize_dockets(conn)
            if ws_fixed:
                logger.info(f"  Trimmed whitespace from {ws_fixed} docket_numbers")
            n_zero, n_future, n_pre1700 = _normalize_dates(conn)
            if n_zero or n_future or n_pre1700:
                logger.info(f"  Cleared {n_zero} year-0000 + {n_future} far-future (>today+365d) + {n_pre1700} pre-1700 dates → NULL")
            n_gr_implausible = _null_implausible_gr_dates(conn)
            if n_gr_implausible:
                logger.info(f"  Cleared {n_gr_implausible} gr_gerichte dates >3y before docket year (Praxis-digest junk recovery, L2) → NULL")
            n_chamber = _fill_chamber_from_docket(conn)
            logger.info(f"  Filled chamber from docket register code for {n_chamber} decisions (P1.2)")
            n_branch = _derive_branch_column(conn)
            logger.info(f"  Derived branch for {n_branch} decisions (zivil/straf/oeffentlich/sozialversicherung, P1.1)")
            recovered = _recover_decision_dates(conn)
            if recovered:
                logger.info(f"  Recovered {recovered} decision_date values from full_text (zh_verwaltungsgericht/gr_gerichte/bl_gerichte)")
            text_migrated = _migrate_short_text_to_regeste(conn)
            if text_migrated:
                logger.info(f"  Migrated {text_migrated} short full_text values → regeste (correct field for Art./§ references)")

        with _phase_timer("normalise source_urls"):
            urls_fixed = _normalize_source_urls(conn)
            if urls_fixed:
                logger.info(f"  Prefixed host on {urls_fixed} relative source_urls (bs_gerichte / gl_gerichte Tribuna paths)")

        with _phase_timer("truncate oversized regestes"):
            regestes_truncated = _truncate_oversized_regestes(conn)
            if regestes_truncated:
                logger.info(f"  Truncated {regestes_truncated} oversized regestes to head-note portion (full_text untouched)")

        with _phase_timer("remove stub decisions"):
            stubs_removed = _remove_stubs(conn)
            if stubs_removed:
                logger.info(f"  Removed {stubs_removed} stub decisions")

        with _phase_timer("fill missing regeste"):
            filled = _fill_missing_regeste(conn)
            if filled:
                logger.info(f"  Extracted regeste for {filled} decisions")

        # Joined-docket aliases (#41): must run AFTER _normalize_dockets so the
        # lead docket_number is clean. Non-fatal — a failure here must never
        # abort the build (the alias table just stays empty for this run).
        with _phase_timer("docket aliases (consolidated proceedings)"):
            try:
                n_alias = _build_docket_aliases(conn)
                logger.info(f"  Mapped {n_alias} joined-docket aliases to lead decisions (#41)")
            except Exception as _al_err:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("docket-alias build skipped (non-fatal): %s", _al_err)

        # Per-decision content hash — must run AFTER all _normalize_* /
        # _migrate_* / _truncate_* / _fill_* passes so the hash reflects
        # the canonical post-cleanup content the corpus serves.
        with _phase_timer("content hashes (SHA-256 over regeste||full_text)"):
            hashes = _compute_content_hashes(conn)
            if hashes:
                logger.info(f"  Hashed/refreshed {hashes} decisions")

        with _phase_timer("log quality summary"):
            _log_quality_summary(conn)

    # Canonical-identity date correction: replace synthetic YYYY-01-01 BGE dates
    # with the text-verified Urteilsdatum so search/sort/filter/by_year use the
    # real date and agree with get_decision (audit 2026-06-28 C-2). Runs BEFORE
    # optimize so the FTS trigger churn from the UPDATEs is cleaned up. DEFENSIVE:
    # any failure is logged and skipped — it can never fail the build.
    if total_imported > 0:
        with _phase_timer("canonical date correction"):
            try:
                import backfill_canonical_identity as _bci
                _n_d, _n_p = _bci.apply_to_db(conn)
                logger.info("canonical dates: %d decision dates corrected, %d publication dates set", _n_d, _n_p)
            except Exception as _cd_err:
                # discard any partial UPDATE transaction so the open conn proceeds
                # to optimize/swap in the pre-correction (status-quo) state.
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("canonical date correction skipped (non-fatal): %s", _cd_err)

    # GitHub #57: the scraper-side chamber derivation was fixed (7B/7F ->
    # II. strafrechtliche Abteilung; foreign-court pickup removed), but
    # chamber is written at scrape time, so ~4,700 stored bger rows keep the
    # bad values until re-derived. Same defensive contract as above: can
    # never fail the build.
    if total_imported > 0:
        with _phase_timer("bger chamber correction"):
            try:
                import backfill_bger_chambers as _bbc
                _n_f, _n_r, _n_n = _bbc.apply_to_db(conn)
                logger.info(
                    "bger chambers: %d 7B/7F forced to II. Strafrechtliche "
                    "Abteilung, %d broken values re-derived from full_text, "
                    "%d cleared to NULL", _n_f, _n_r, _n_n)
            except Exception as _bc_err:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("bger chamber correction skipped (non-fatal): %s", _bc_err)

    # BVGer Abteilung substring bug (scrapers/bvger.py, fixed 2026-09-17):
    # "Abt. I" matched Abt. II/III/IV and "Abt. V" matched Abt. VI, so
    # 59,124 Weblaw-era rows (Feb-Sep 2026) carry the wrong Abteilung and
    # ~14,800 older rows have NULL / bare-letter chambers. Header-first,
    # docket-letter fallback; same defensive contract: can never fail the
    # build. quick_publish (incremental nights) copies and inserts only, so
    # stored rows are corrected here, at the full rebuild.
    if total_imported > 0:
        with _phase_timer("bvger chamber correction"):
            try:
                import backfill_bvger_chambers as _bvc
                _n_rl, _n_fl, _n_un = _bvc.apply_to_db(conn)
                logger.info(
                    "bvger chambers: %d labels corrected from the judgment "
                    "header/docket letter, %d NULL/bare-letter/raw values "
                    "filled, %d left unresolved", _n_rl, _n_fl, _n_un)
            except Exception as _bvc_err:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning("bvger chamber correction skipped (non-fatal): %s", _bvc_err)

    if not no_optimize and total_imported > 0:
        with _phase_timer("FTS5 optimize"):
            # heartbeat-wrapped so the publish stall-watchdog doesn't false-kill this ~4h
            # silent step (it did on 2026-06-22 when optimize crossed the 14400s no-output cap)
            _fts5_optimize_with_heartbeat(conn)
    elif no_optimize and total_imported > 0:
        logger.info("Skipping FTS5 optimize (--no-optimize)")
        conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    # Court breakdown
    courts = conn.execute(
        "SELECT court, COUNT(*) as n FROM decisions GROUP BY court ORDER BY n DESC"
    ).fetchall()
    # Per-court map of the NEW build, captured BEFORE conn.close() below — the
    # per-court pre-swap gate can't re-query the temp DB after it's closed.
    new_by_court = {c: n for c, n in courts}

    # Switch from WAL to DELETE mode before closing (immutable=1 compat)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    # Bump db_generation so MCP workers' _query_cache invalidates on next
    # get_db call. See docs/db_contract.md. Must run AFTER the final
    # durable write (journal_mode=DELETE folds WAL into main) and BEFORE
    # close, so the new value is persisted into the file that os.replace
    # will rename into the live path. Value is unix epoch seconds —
    # fits the 32-bit signed user_version field until 2038.
    _db_generation = int(time.time())
    conn.execute(f"PRAGMA user_version = {_db_generation}")
    logger.info(f"db_generation set to {_db_generation}")
    # Capture the built row count BEFORE close — the pre-swap gate below
    # can't query the temp DB after the connection is closed.
    new_row_count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    conn.close()

    # Full rebuild: atomically swap temp DB into place
    if final_db_path is not None:
        # Pre-swap row-count gate: never replace a healthy live DB with a
        # catastrophically shrunken build (see _check_swap_row_gate).
        live_row_count = 0
        live_by_court: dict = {}
        if final_db_path.exists():
            try:
                _oc = sqlite3.connect(
                    f"file:{final_db_path}?mode=ro&immutable=1", uri=True)
                try:
                    live_row_count = _oc.execute(
                        "SELECT COUNT(*) FROM decisions").fetchone()[0]
                    live_by_court = {
                        c: n for c, n in _oc.execute(
                            "SELECT court, COUNT(*) FROM decisions "
                            "GROUP BY court")
                    }
                finally:
                    _oc.close()
            except sqlite3.Error:
                live_row_count = 0  # unreadable live DB → don't block recovery
                live_by_court = {}
        _check_swap_row_gate(new_row_count, live_row_count)
        _check_swap_per_court_gate(new_by_court, live_by_court)
        logger.info("pre-swap row gate OK: new=%d, live=%d",
                    new_row_count, live_row_count)
        logger.info("pre-swap per-court gate OK: %d live courts checked "
                    "(≥ %d rows)", len(live_by_court), PER_COURT_MIN_SIZE)
        logger.info(f"Swapping {db_path} → {final_db_path}")
        os.replace(str(db_path), str(final_db_path))
        # Clean up leftover WAL/SHM on BOTH sides of the swap:
        #   (a) Source — temp-DB sidecars (.tmp-wal, .tmp-shm). Orphaned by
        #       the os.replace which only renames the main file.
        #   (b) Destination — STALE sidecars from a previous build at the
        #       final path (e.g. decisions.db-wal from a build three days
        #       ago). The new DB is in DELETE journal mode and doesn't
        #       need them. Leftover sidecars cause SQLite to mis-recover
        #       a non-existent transaction on the first non-immutable=1
        #       connection, surfacing as "database disk image is
        #       malformed". Latent bug — masked when the atomic swap was
        #       failing daily; surfaced 2026-05-05 on the first
        #       successful swap in three days when generate_stats.py
        #       opened the freshly-swapped decisions.db without
        #       immutable=1 and tried to apply the stale May-2 WAL.
        # Workers using immutable=1 don't open the WAL at all, so removing
        # these sidecars under their reads is safe.
        for ext in ("-wal", "-shm"):
            for stale_path in (
                Path(str(db_path) + ext),         # source: .tmp-wal / .tmp-shm
                Path(str(final_db_path) + ext),   # destination: stale from previous build
            ):
                if stale_path.exists():
                    stale_path.unlink()
                    logger.info(f"  Removed stale sidecar {stale_path.name}")
        db_path = final_db_path

        # MISSION-CRITICAL ordering: open the integrity-check connection
        # to the just-swapped DB BEFORE emitting OCL_SWAP_DONE. The
        # connection's open file descriptor pins the inode we just
        # built — if quick_publish (or any other writer) os.replace's
        # the path AFTER OCL_SWAP_DONE, the path points at the new
        # inode but our check_conn still reads the just-built inode
        # (POSIX semantics: an FD survives the path being replaced).
        # Without this ordering, the post-swap integrity check could
        # silently validate a quick-publish DB instead of our own
        # build (caught in 2026-05-16 code review). Held until the
        # finally: at the end of the integrity block.
        check_conn = sqlite3.connect(str(final_db_path))
        # Touch the DB to force fd materialisation (defensive — sqlite3
        # may defer the open until first query in some build options).
        check_conn.execute("SELECT 1").fetchone()

        # Tell the parent (publish.py) the swap is committed so it can
        # release the publish lock NOW, instead of waiting for the 1–3 h
        # post-swap integrity_check tail. While we still hold the lock,
        # quick_publish skips silently, stranding fresh BGer poller
        # scrapes in bger.jsonl until tomorrow. publish.py greps stdout
        # for this exact token to fire fcntl.LOCK_UN — see publish.py's
        # run_cmd on_line callback. Token chosen to be unambiguous and
        # grep-stable.
        logger.info("OCL_SWAP_DONE — publish lock releasable; integrity_check continues")

        # Fire-and-forget early dashboard refresh — spawn a subprocess that
        # regenerates docs/stats.json (minimal, no graph aggregations) +
        # git pushes, so opencaselaw.ch reflects the freshly-swapped
        # decisions.db within minutes instead of ~3 h. Empirically (today's
        # 2026-05-10 publish): integrity_check on the 60 GB DB took 2h 55m
        # under ionice idle + encoder I/O contention. The MCP starts
        # serving new data the instant we os.replace above; the dashboard
        # was lagging entirely on this one process.
        # The subprocess runs in parallel with our own post-swap integrity
        # check below — they don't share resources beyond disk I/O. The
        # final Step 5/6 in publish.py still runs at the end (with full
        # graph aggregations) so this is purely an early-update; no data
        # is lost if the subprocess fails.
        # Disable with OCL_EARLY_STATS_PUSH=0.
        if os.environ.get("OCL_EARLY_STATS_PUSH", "1") not in {"0", "false", "no"}:
            try:
                _spawn_early_stats_push(final_db_path)
            except Exception as _e:
                logger.warning(f"  early-stats-push spawn failed: {_e}")

        # Post-swap integrity check (added 2026-05-05 after WAL-corruption
        # incident). Open the freshly-swapped DB with a PLAIN connection
        # — i.e. without ?immutable=1 — so any leftover WAL/SHM that
        # would corrupt non-immutable readers is caught HERE, not 30 min
        # later when generate_stats.py / generate_feeds.py crash.
        # Three cheap probes:
        #   1. PRAGMA integrity_check — verifies B-tree page consistency
        #      and FTS5 index well-formedness. Returns 'ok' or a list of
        #      structural problems.
        #   2. SELECT COUNT(*) — flushes the page cache, runs an index
        #      scan, would surface "database disk image is malformed"
        #      from any orphan WAL.
        #   3. SELECT a single row — exercises the read path end-to-end.
        # If any check fails, raise so the caller marks Step 2 FAILED.
        # The atomic-swap stays — a swap-then-fail path is safer than
        # a swap-then-pretend-it-worked path, because the post-mortem
        # diagnosis is much easier when the new file is on disk.
        # Post-swap integrity verification — two layers, weekday-fast +
        # weekend-thorough.
        #
        # CHEAP layer (always runs, ~1 s total): SELECT COUNT(*) and
        # SELECT a sample row. This catches the original 2026-05-05
        # motivation (orphan WAL/SHM sidecars left in the swapped path
        # would crash non-immutable readers like generate_stats /
        # generate_feeds — both surface as exceptions here within
        # seconds, not 3 h later).
        #
        # EXPENSIVE layer (off by default, ~3.5 h on the 60 GB DB):
        # full PRAGMA integrity_check that walks every B-tree page +
        # FTS5 index. Catches deep structural corruption that would
        # NOT manifest via a row SELECT. After ~6 months of production
        # runs we have zero recorded cases where this caught something
        # the cheap layer missed — and the atomic os.replace() makes
        # such corruption nearly impossible to introduce. Gate it
        # behind OCL_FULL_INTEGRITY_CHECK=1 so weekday nightlies skip
        # the 3.5 h block and the legacy weekly full rebuild (Sundays,
        # once we cut over) can re-enable it for belt-and-braces.
        try:
            # NOTE: check_conn was opened above, BEFORE OCL_SWAP_DONE,
            # to pin the build's inode before quick_publish can replace
            # the path. Do not re-open here — that would defeat the race
            # fix.
            if os.environ.get("OCL_FULL_INTEGRITY_CHECK", "0") in {"1", "true", "yes"}:
                # Heartbeat keeps publish.py's stall watchdog satisfied
                # during the long silent PRAGMA call.
                import threading as _threading
                _hb_stop = _threading.Event()

                def _hb_pulse():
                    i = 0
                    while not _hb_stop.wait(300):
                        i += 5
                        logger.info(
                            f"  Post-swap integrity_check still running "
                            f"({i} min elapsed) — silent PRAGMA, no progress signal"
                        )

                _hb_thread = _threading.Thread(target=_hb_pulse, daemon=True)
                _hb_thread.start()
                try:
                    integrity = check_conn.execute("PRAGMA integrity_check").fetchone()
                    if not integrity or integrity[0] != "ok":
                        raise RuntimeError(
                            f"post-swap integrity_check failed: {integrity}"
                        )
                finally:
                    _hb_stop.set()
                    _hb_thread.join(timeout=2)
            else:
                logger.info(
                    "  Post-swap PRAGMA integrity_check skipped (set "
                    "OCL_FULL_INTEGRITY_CHECK=1 for the full ~3.5 h "
                    "walk). Cheap row-level checks still run below."
                )

            # Cheap checks — always run, ~1 s total. Catch WAL/SHM-sidecar
            # corruption (the original 2026-05-05 motivation) via the
            # plain (non-immutable) check_conn opened pre-OCL_SWAP_DONE.
            n_rows = check_conn.execute(
                "SELECT COUNT(*) FROM decisions"
            ).fetchone()[0]
            sample = check_conn.execute(
                "SELECT decision_id FROM decisions LIMIT 1"
            ).fetchone()
            check_conn.close()
            if n_rows == 0:
                raise RuntimeError("post-swap row count is 0 — empty DB")
            if sample is None:
                raise RuntimeError("post-swap sample SELECT returned NULL")
            logger.info(
                f"  Post-swap integrity OK: {n_rows} rows, sample id={sample[0]!r}"
            )
        except Exception as e:
            logger.error(
                f"  Post-swap integrity check FAILED: {e}. "
                f"The new {final_db_path.name} exists but is unreadable. "
                f"Workers using ?immutable=1 may be unaffected, but "
                f"generate_stats / generate_feeds / quality.cli without "
                f"immutable=1 will crash. Manual recovery: inspect "
                f"sidecar files at {final_db_path}-wal / -shm; if any "
                f"are present, remove them and retry."
            )
            raise

    # Save checkpoint
    if incremental or full_rebuild:
        now = datetime.now(timezone.utc).isoformat()
        # Load existing checkpoint metadata to preserve last_full_build
        prev_meta = {}
        if checkpoint_path.exists() and not full_rebuild:
            try:
                prev_meta = json.loads(checkpoint_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        meta = {
            "files": new_checkpoint,
            "last_full_build": now if full_rebuild else prev_meta.get("last_full_build"),
            "last_incremental": now if incremental else prev_meta.get("last_incremental"),
        }
        checkpoint_path.write_text(json.dumps(meta, indent=2))
        logger.info(f"Saved checkpoint: {len(new_checkpoint)} files tracked")

    logger.info(f"Database: {db_path} ({db_path.stat().st_size / 1024 / 1024:.1f} MB)")
    logger.info(f"  Existing: {existing}, New: {total_imported}, Skipped: {total_skipped}")
    logger.info(f"  Total decisions: {total}")
    for court, n in courts:
        logger.info(f"    {court}: {n}")

    _log_phase_summary()

    return db_path


def main():
    parser = argparse.ArgumentParser(description="Build FTS5 search database")
    parser.add_argument(
        "--output", type=str, default="output",
        help="Output directory containing decisions/ and data/ subdirs"
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Database path (default: {output}/decisions.db)"
    )
    parser.add_argument(
        "--watch", type=int, default=None,
        help="Rebuild every N seconds (for use alongside running scrapers)"
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Only read new bytes from JSONL files (skip already-processed content)"
    )
    parser.add_argument(
        "--no-optimize", action="store_true",
        help="Skip FTS5 optimize step (useful with --incremental)"
    )
    parser.add_argument(
        "--full-rebuild", action="store_true",
        help="Delete existing DB and checkpoint, rebuild from scratch"
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    output_dir = Path(args.output)
    db_path = Path(args.db) if args.db else None

    if args.watch:
        logger.info(f"Watch mode: rebuilding every {args.watch}s")
        while True:
            try:
                build_database(
                    output_dir, db_path,
                    incremental=args.incremental,
                    no_optimize=args.no_optimize,
                    full_rebuild=args.full_rebuild,
                )
            except Exception as e:
                logger.error(f"Build failed: {e}", exc_info=True)
            time.sleep(args.watch)
    else:
        build_database(
            output_dir, db_path,
            incremental=args.incremental,
            no_optimize=args.no_optimize,
            full_rebuild=args.full_rebuild,
        )


if __name__ == "__main__":
    main()
