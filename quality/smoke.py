"""Live-MCP-server smoke check (L4).

Hits the production server every 5 minutes via systemd timer. Verifies:

  1. /health                           → 200 + status ok
  2. /entscheid/<bge>                  → 200 + decision-body marker
  3. /api/decisions/<bge>/export.pdf   → 200 + ≥500 bytes
  4. /api/decisions?query=…            → 200 + at least one decision_id
  5. publish freshness (local marker)  → last success recent, or a build
                                         currently holding the lock
  6. served data age (/health)         → the served database was built in the
                                         last SERVED_MAX_AGE_H hours
  7. serving I/O pressure (local)      → /proc/pressure/io "full" 5-min average
                                         below IO_FULL_MAX_PCT

The .docx / .bib / /sse probes this docstring used to list were removed on
2026-05-09 (see the export_pdf comment below); the list had not been updated
since, which is its own small lesson about docs that describe intent rather
than code.

Exits 1 on any failure → systemd OnFailure → ntfy alert. Output JSON
to /var/log/opencaselaw-smoke/<timestamp>.json (or stdout) for trend
analysis.

Cost budget: < 25 s wall time. Was "< 5 s" until 2026-08-22, when the search
probe was added — search legitimately runs 2-7 s (the Haiku query-parse
dominates), so the old budget described a probe set that no longer existed.
Falls back to friendly error messages on network failure (won't fire false
alerts during transient hiccups — 3 consecutive failures escalate to
"INVESTIGATE" tag).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_BASE_URL = "https://mcp.opencaselaw.ch"
ANCHOR_DECISION = "bge_BGE_140_III_86"
TIMEOUT = 10.0

# Search probe (added 2026-08-22). Until now this file probed health, one
# decision page, one export and publish freshness — nothing that runs a query.
# On 2026-08-22 a name collision broke the reranker and every search returned
# 500 for ~70 minutes (458 tracebacks, 12:41-13:24 UTC) while all four probes
# stayed green, because none of them searches. Liveness on the read path says
# nothing about the product's main verb.
#
# The query is a constant so search telemetry can filter it: it self-hits ~288
# times a day, the same trade the export_pdf probe already makes. It is a real
# multi-term German query on purpose — a nonsense string would return zero rows
# and never reach _rerank_rows, which is precisely the code that broke.
SEARCH_PROBE_QUERY = "Tierhalterhaftung"

# publish.py holds this for the duration of a run (see its LOCK_FILE_PATH).
# Its presence is the difference between "the nightly is slow" and "the nightly
# is dead" — the freshness probe below uses it so a 13-hour build does not sit
# red for 13 hours a day.
PUBLISH_LOCK_PATH = Path("/tmp/opencaselaw-publish.lock")
# The nightly runs ~13-14h. Past this, a held lock means stuck, not slow, and
# the alert must fire anyway — otherwise a hung publish silences the probe
# forever, which is the failure this probe exists to catch.
MAX_BUILD_H = 20.0


@dataclass
class ProbeResult:
    name: str
    url: str
    status: int
    elapsed_ms: float
    content_type: str
    bytes_read: int
    passed: bool
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def _probe(name: str, url: str, *, magic: bytes | None = None,
           must_contain: bytes | None = None,
           expected_mtype_prefix: str | None = None,
           min_bytes: int = 0,
           accept_404: bool = False,
           timeout: float | None = None) -> ProbeResult:
    """Single HTTP probe with structured result.

    `timeout` overrides the module default for endpoints that are legitimately
    slower than a static read — see the search probe.
    """
    started = time.monotonic()
    notes: list[str] = []
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "opencaselaw-smoke/1.0",
        })
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
            ct = resp.headers.get("content-type", "")
            # Read up to 64 KB. Previously read only min_bytes+4096 (=4096 for
            # marker probes), so must_contain markers living past the first 4 KB
            # of a page false-failed — e.g. 'decision-body' sits past the CSS/meta
            # head in the ~52 KB /entscheid/ page, firing a bogus ntfy every 5 min
            # (the 2026-05 alert-fatigue that drowned real alerts).
            data = resp.read(64 * 1024)
            elapsed = (time.monotonic() - started) * 1000
            status = resp.status
            ok = status == 200
            if magic is not None and not data.startswith(magic):
                notes.append(f"missing magic {magic!r}, got {data[:8]!r}")
                ok = False
            if must_contain is not None and must_contain not in data:
                notes.append(f"missing marker {must_contain!r}")
                ok = False
            if expected_mtype_prefix and not ct.startswith(expected_mtype_prefix):
                notes.append(f"mtype {ct} ≠ {expected_mtype_prefix}")
                ok = False
            if min_bytes and len(data) < min_bytes:
                notes.append(f"only {len(data)} bytes (< {min_bytes})")
                ok = False
            return ProbeResult(
                name=name, url=url, status=status,
                elapsed_ms=round(elapsed, 1),
                content_type=ct, bytes_read=len(data),
                passed=ok, notes=notes,
            )
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - started) * 1000
        if accept_404 and e.code == 404:
            return ProbeResult(name=name, url=url, status=404,
                               elapsed_ms=round(elapsed, 1),
                               content_type="", bytes_read=0,
                               passed=True, notes=["404 expected"])
        return ProbeResult(
            name=name, url=url, status=e.code,
            elapsed_ms=round(elapsed, 1),
            content_type="", bytes_read=0, passed=False,
            error=f"HTTP {e.code}: {e.reason}",
        )
    except Exception as e:
        elapsed = (time.monotonic() - started) * 1000
        return ProbeResult(
            name=name, url=url, status=0,
            elapsed_ms=round(elapsed, 1),
            content_type="", bytes_read=0, passed=False,
            error=f"{type(e).__name__}: {e}",
        )


def run_smoke(base_url: str = DEFAULT_BASE_URL) -> list[ProbeResult]:
    """Run the full smoke probe set against `base_url`."""
    base = base_url.rstrip("/")
    probes = [
        ("health", f"{base}/health",
         {"must_contain": b'"status":"ok"', "expected_mtype_prefix": "application/json"}),
        # Marker: the decision-body class survives the design-system
        # refactor (Move 3, 2026-04-25) where literal font names were
        # replaced with `var(--f-serif)`. Looking for "decision-body" is
        # more durable than "Times New Roman" — it's the anchor the
        # serif-typography scope binds to in seo_pages.py.
        ("entscheid_html", f"{base}/entscheid/{ANCHOR_DECISION}",
         {"must_contain": b"decision-body", "expected_mtype_prefix": "text/html"}),
        # 2026-05-09: trimmed from 4 export-format probes to 1. The previous
        # set hammered all four formats every 5 min (~3,800 self-hits/day to
        # the same canary), which (a) inflated and uniformised the export-
        # format usage stats so we couldn't tell which format real users
        # prefer, and (b) bombed the BGE 140 III 86 page hit count by an
        # order of magnitude over real signal. .pdf is the most-used by
        # real users so we keep that one; the other three formats are
        # exercised by the publish pipeline's release-bundle build and by
        # pytest, which is sufficient for regression coverage.
        ("export_pdf", f"{base}/api/decisions/{ANCHOR_DECISION}/export.pdf",
         {"min_bytes": 500, "expected_mtype_prefix": "application/"}),
        # Search — the product's main verb, and until 2026-08-22 unprobed.
        # The marker is deliberately a result field rather than the envelope:
        # `"total"` is present on an empty page too, so it would pass while
        # search silently returned nothing. `"decision_id"` only appears when a
        # row actually came back, so this catches both a 500 and a regression
        # to zero results.
        ("search_query",
         f"{base}/api/decisions?query={SEARCH_PROBE_QUERY}&limit=3",
         {"must_contain": b'"decision_id"',
          "expected_mtype_prefix": "application/json",
          # Search is legitimately slower than a static read: the Haiku
          # query-parse dominates p50. Measured 2026-08-22 against production —
          # 2.1-3.7 s warm, 6.8 s for a cold multi-term query. At the 10 s
          # module default a cold query plus any load spike would fire a false
          # alert every few hours, and this file's own history says alert
          # fatigue is what drowns real alerts. 20 s still catches a hang while
          # never confusing "slow" with "broken".
          "timeout": 20.0}),
    ]
    results = [_probe(n, u, **kw) for n, u, kw in probes]
    results.append(_probe_publish_freshness())
    results.append(_probe_served_data_age(f"{base}/health"))
    results.append(_probe_serving_pressure())
    return results


# The served database's age, from /health's db_generation (the unix time the
# build or quick_publish wrote it). publish_freshness cannot see this: it
# passes while any build holds the lock, so from 2026-10-08 14:25 to 10-11 the
# site served the same database for three days with that probe green (two
# full builds failed to swap). A normal day swaps within ~28 h.
SERVED_MAX_AGE_H = float(os.environ.get("OCL_SMOKE_SERVED_MAX_AGE_H", "36"))


def _probe_served_data_age(url: str, max_age_h: float = SERVED_MAX_AGE_H,
                           now: float | None = None) -> ProbeResult:
    name = "served_data_age"
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "opencaselaw-smoke/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read(64 * 1024) or b"{}")
        elapsed = round((time.monotonic() - started) * 1000, 1)
        gen = int(body.get("db_generation") or 0)
        if not gen:
            return ProbeResult(name=name, url=url, status=200, elapsed_ms=elapsed,
                               content_type="application/json", bytes_read=0, passed=True,
                               notes=["no db_generation in /health — age not checked"])
        age_h = ((now or time.time()) - gen) / 3600.0
        ok = age_h <= max_age_h
        notes = [f"served database built {age_h:.1f}h ago"] if ok else [
            f"served database built {age_h:.1f}h ago (> {max_age_h:.0f}h) — no new data "
            "reaches users; check the last builds' step 2 (swap gate, pre-flight)"]
        return ProbeResult(name=name, url=url, status=200, elapsed_ms=elapsed,
                           content_type="application/json", bytes_read=0, passed=ok, notes=notes)
    except Exception as e:
        return ProbeResult(name=name, url=url, status=0,
                           elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                           content_type="", bytes_read=0, passed=False,
                           error=f"{type(e).__name__}: {e}")


# Serving reads a 73 GB database through the page cache on a 61 GB host. When
# something else takes the memory, searches go to disk and time out: 2026-09-30
# (bloated workers) and 2026-10-10 09:40-10:00 (a 16 GB local model), I/O
# "full" pressure 70-73% while a normal build night runs at 10-20%. This pages
# with the memory users named, minutes before the search probe times out.
IO_FULL_MAX_PCT = float(os.environ.get("OCL_SMOKE_IO_FULL_MAX_PCT", "45"))


def _top_memory_users(proc: Path = Path("/proc"), n: int = 3) -> list[str]:
    import pwd
    per_user: dict[str, int] = {}
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            rss_kb, uid = 0, None
            for line in (d / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                elif line.startswith("Uid:"):
                    uid = int(line.split()[1])
            if uid is None:
                continue
            try:
                user = pwd.getpwuid(uid).pw_name
            except KeyError:
                user = str(uid)
            per_user[user] = per_user.get(user, 0) + rss_kb
        except (OSError, ValueError):
            continue
    top = sorted(per_user.items(), key=lambda kv: -kv[1])[:n]
    return [f"{u} {kb / 1e6:.1f} GB" for u, kb in top]


def _probe_serving_pressure(path: Path = Path("/proc/pressure/io"),
                            max_full_pct: float = IO_FULL_MAX_PCT) -> ProbeResult:
    name = "serving_io_pressure"
    try:
        text = path.read_text()
    except OSError:
        return ProbeResult(name=name, url=str(path), status=0, elapsed_ms=0.0,
                           content_type="", bytes_read=0, passed=True,
                           notes=["no pressure stall information here — not checked"])
    full = next((ln for ln in text.splitlines() if ln.startswith("full ")), "")
    fields = dict(kv.split("=", 1) for kv in full.split()[1:] if "=" in kv)
    try:
        avg300 = float(fields.get("avg300", "0"))
    except ValueError:
        avg300 = 0.0
    ok = avg300 <= max_full_pct
    notes = [f"io full avg300 {avg300:.1f}%"]
    if not ok:
        notes = [f"io full avg300 {avg300:.1f}% (> {max_full_pct:.0f}%): searches are going to "
                 "disk — page cache squeezed. Top memory users: "
                 + ", ".join(_top_memory_users() or ["unknown"])]
    return ProbeResult(name=name, url=str(path), status=200, elapsed_ms=0.0,
                       content_type="text/plain", bytes_read=len(text), passed=ok, notes=notes)


def _publish_lock_age_h(path: Path | None = None) -> float | None:
    """Hours since a publish run took the lock, or None if no run is holding it.

    Separate function so tests can point it at a temp file — the real path is
    absolute and outside the repo.
    """
    p = path or PUBLISH_LOCK_PATH
    try:
        if not p.exists():
            return None
        return max(0.0, (time.time() - p.stat().st_mtime) / 3600.0)
    except OSError:
        return None


def _probe_publish_freshness(max_age_h: float = 28.0,
                             marker_path: Path | None = None) -> ProbeResult:
    """Detect a silently-failing nightly publish.

    The atomic-swap design keeps serving the previous-good corpus when a
    rebuild fails, and the 15-min bger poller keeps /health's db_generation
    fresh — so liveness checks stay green through a broken pipeline (exactly
    how this week's failures went unnoticed). Freshness must therefore key off
    the full-publish success marker (state/last_publish_success.json, written
    only after Step 6 completes). Missing marker = not-yet-seeded → PASS (the
    check arms itself on the first successful publish); present and older than
    max_age_h → FAIL → ntfy alert.

    `marker_path` is injectable so the behaviour can be tested offline
    against a temp file rather than the real repo-relative location.
    """
    name = "publish_freshness"
    marker = marker_path or (
        Path(__file__).resolve().parents[1] / "state" / "last_publish_success.json"
    )
    try:
        if not marker.exists():
            return ProbeResult(
                name=name, url=str(marker), status=0, elapsed_ms=0.0,
                content_type="", bytes_read=0, passed=True,
                notes=["marker not seeded — arms on first successful publish"],
            )
        d = json.loads(marker.read_text())
        ts = int(d.get("ts") or 0)
        age_h = (time.time() - ts) / 3600.0 if ts else 1e9
        ok = age_h <= max_age_h
        notes = [] if ok else [
            f"last successful publish {age_h:.1f}h ago (> {max_age_h}h) — nightly likely failing"
        ]
        # A run in progress is not a failure. The nightly takes ~13-14h, so
        # against a 28h threshold this probe spent roughly half of every day
        # red — and a monitor that is normally red is one nobody reads. That
        # is not hypothetical: it was already red when search went down on
        # 2026-08-22, so even a probe that had caught the outage would have
        # added no signal. Suppress only while the lock is genuinely held AND
        # young enough to be a real build; a lock older than MAX_BUILD_H means
        # stuck, and then the alert must fire.
        if not ok:
            build_age_h = _publish_lock_age_h()
            if build_age_h is not None and build_age_h <= MAX_BUILD_H:
                ok = True
                notes = [
                    f"last success {age_h:.1f}h ago, but a publish has been "
                    f"running for {build_age_h:.1f}h — in progress, not failing"
                ]
            elif build_age_h is not None:
                notes.append(
                    f"publish lock held {build_age_h:.1f}h (> {MAX_BUILD_H}h) "
                    "— run appears stuck, not merely slow"
                )
        return ProbeResult(
            name=name, url=str(marker), status=200, elapsed_ms=0.0,
            content_type="application/json", bytes_read=marker.stat().st_size,
            passed=ok, notes=notes,
        )
    except Exception as e:
        return ProbeResult(
            name=name, url=str(marker), status=0, elapsed_ms=0.0,
            content_type="", bytes_read=0, passed=False,
            error=f"{type(e).__name__}: {e}",
        )


def summarise(results: Iterable[ProbeResult]) -> dict:
    results = list(results)
    n_pass = sum(1 for r in results if r.passed)
    n = len(results)
    p95_ms = sorted(r.elapsed_ms for r in results)[max(0, int(0.95 * n) - 1)] if n else 0
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": n_pass,
        "total": n,
        "p95_ms": p95_ms,
        "ok": n_pass == n,
        "probes": [asdict(r) for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("opencaselaw-smoke")
    ap.add_argument("--url", default=os.environ.get("OPENCASELAW_BASE_URL",
                                                     DEFAULT_BASE_URL))
    ap.add_argument("--output", help="Write JSON summary to this path")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress stdout text summary")
    args = ap.parse_args(argv)

    results = run_smoke(args.url)
    summary = summarise(results)

    if not args.quiet:
        print(f"[{summary['ts']}] {summary['passed']}/{summary['total']} ok, "
              f"p95 {summary['p95_ms']:.0f} ms")
        for r in results:
            mark = "✓" if r.passed else "✗"
            extra = "  (" + ", ".join(r.notes) + ")" if r.notes else ""
            err = f"  ERROR: {r.error}" if r.error else ""
            print(f"  {mark} {r.name:18s} {r.status:>3d}  "
                  f"{r.elapsed_ms:>6.1f} ms  {r.bytes_read:>7d} b{extra}{err}")

    if args.output:
        try:
            from pathlib import Path
            p = Path(args.output)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(summary, indent=2))
        except Exception as e:
            print(f"  (failed to write {args.output}: {e})", file=sys.stderr)

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
