"""DAG-based runner for the OpenCaseLaw publish pipeline.

Replaces the linear for-loop + cascade-skip globals in publish.py with
an explicit dependency graph. Each target declares its inputs; the
runner topologically sorts and runs them; failures cascade *only* down
the dependency chain instead of triggering a global GUARDED-step skip.

Phase B v0.1 (2026-05-05): standalone module, NOT yet wired into
publish.py. Use this CLI to inspect and validate the DAG today; the
next commit will add `OCL_USE_DAG=1 python publish.py` fallthrough so
publish.py optionally hands its STEPS over to this runner without
changing default behaviour.

Why phase B exists: every recent publish failure (2026-05-04 build_fts5
timeout, ENOSPC, optimize-stall) cascaded the entire downstream chain.
The cascade is global today (CRITICAL_STEPS / GUARDED_STEPS sets in
publish.py); the DAG localises it — only the targets that genuinely
depend on the failed step skip, not every "guarded" step in the file.

CLI:
  python publish_dag.py --validate     # cycle + dep-resolution check
  python publish_dag.py --list         # show every target with its deps
  python publish_dag.py --dry-run      # print the topological order
  python publish_dag.py --target X     # show the rebuild closure of X
"""
from __future__ import annotations

import argparse
import concurrent.futures
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("publish_dag")


# ── Target schema ─────────────────────────────────────────────────────


@dataclass
class Target:
    """One node in the build DAG.

    A target is the unit of rebuild — typically the production of one
    output file (decisions.db, reference_graph.db, hf_dataset/, …) or
    one side-effect action (git push, health check). Each target lists
    the *names* of upstream targets it consumes; the runner topo-sorts
    and propagates failure only along those edges.

    Attributes:
        name: unique identifier within the registry; used in deps lists.
        deps: names of targets that must succeed before this one runs.
            If any dep failed AND that dep is not ``non_fatal``, this
            target is cascade-skipped.
        non_fatal: when True, a failure of this target does NOT cascade
            to downstream targets that depend on it (they still run).
            Mirrors publish.py's NON_FATAL_STEPS today.
        parallel_safe: marker for a future parallel scheduler — siblings
            (same depth in the DAG) that are all parallel_safe can run
            concurrently. v0.1 runner is sequential; the flag is captured
            but unused.
        opt_in: only run when explicitly requested via --target. Used
            for ingest steps that we don't want to fire on every cycle.
        description: human-readable label, shown in --list output.
    """

    name: str
    deps: list[str] = field(default_factory=list)
    non_fatal: bool = False
    parallel_safe: bool = False
    opt_in: bool = False
    description: str = ""


# Result statuses returned by run_targets.
OK = True
FAILED = False
SKIPPED_CASCADE = "skipped_cascade"
SKIPPED_OPTIN = "skipped_optin"


# ── Registry ──────────────────────────────────────────────────────────
#
# Mirrors today's publish.py STEPS list but with explicit deps and
# semantic names instead of step numbers. Numbers are noted in the
# description so the cross-reference is obvious during the wiring
# transition. NO BUILDER FUNCTIONS HERE — the registry is pure metadata
# so this module stays decoupled from publish.py and its dependencies.
# When publish.py wires the runner in, it will pass its own builder map.

REGISTRY: dict[str, Target] = {}


def _register(t: Target) -> None:
    if t.name in REGISTRY:
        raise ValueError(f"duplicate target {t.name!r} in registry")
    REGISTRY[t.name] = t


_register(Target(
    name="ingest",
    opt_in=True,
    description="Step 1 — pull entscheidsuche shards (manual / weekly only)",
))

_register(Target(
    name="build_fts5",
    deps=["ingest"],
    description="Step 2 — full FTS5 rebuild over decisions.db (atomic swap)",
))

# ── Fast tier: site shows today's date immediately after build_fts5 ──

_register(Target(
    name="stats_early",
    deps=["build_fts5"],
    parallel_safe=True,  # writes only docs/stats.json, no shared state
    # non_fatal (2026-10-07, as on the linear path): the previous stats.json
    # stays served; a timeout must not cascade to the early git push.
    non_fatal=True,
    description="Step 5a — early stats.json (kicks off the homepage update)",
))

_register(Target(
    name="rss_feeds",
    deps=["build_fts5"],
    parallel_safe=True,  # writes only docs/feeds/, no shared state
    # non_fatal: convenience artifact — the previous run's XML stays in docs/
    # and git_push_early ships it. Mirrors publish.py NON_FATAL_STEPS ("5b",
    # added after the 2026-09-03 timeout turned an otherwise-green run red).
    non_fatal=True,
    description="Step 5b — generate decision RSS feeds (best-effort)",
))

_register(Target(
    name="qc_gate",
    deps=["build_fts5"],
    parallel_safe=True,  # read-only on decisions.db, writes quality_report.json
    description="Step 5c — quality-control gate (blocks publish on CRITICAL regression)",
))

_register(Target(
    name="release_manifest",
    deps=["qc_gate"],
    parallel_safe=True,  # writes only releases/<date>/manifest.json
    non_fatal=True,
    description="Step 5d — release manifest (audit trail; failure doesn't block push)",
))

_register(Target(
    name="git_push_early",
    deps=["stats_early", "rss_feeds", "qc_gate", "release_manifest"],
    description="Step 6a — early git push of stats + feeds + manifest",
))

# ── Slow tier: enrichment, graphs, exports ──

_register(Target(
    name="enrich_quality",
    deps=["build_fts5"],
    description="Step 2d — quality enrichment of decisions.db rows",
))

_register(Target(
    name="anwaltsrecht_tags",
    deps=["enrich_quality"],
    parallel_safe=True,
    non_fatal=True,
    description="Step 2e — Anwaltsrecht topic tags (best-effort)",
))

_register(Target(
    name="quality_report",
    deps=["enrich_quality"],
    parallel_safe=True,
    description="Step 2b — quality_report.json + dashboard data",
))

_register(Target(
    name="reference_graph",
    deps=["enrich_quality"],
    parallel_safe=True,
    description="Step 2c — citation + statute reference graph (~75 min)",
))

_register(Target(
    name="materialien_build",
    deps=["enrich_quality"],
    parallel_safe=True,
    description="Step 2f — materialien.db rebuild from raw scrape stores",
))

_register(Target(
    name="decision_structure",
    deps=["enrich_quality"],
    parallel_safe=True,
    description="Step 2g — Sachverhalt/Erwägungen/Dispositiv sidecar (~75 min)",
))

_register(Target(
    name="export_parquet",
    deps=["enrich_quality"],
    parallel_safe=True,
    description="Step 3 — export decisions.db → dataset/*.parquet",
))

_register(Target(
    name="verification_pack",
    deps=["decision_structure", "export_parquet"],
    parallel_safe=True,
    non_fatal=True,
    description="Step 3b — weekly offline verification pack (Sunday): metadata, citation strings, aliases, Erwägungen; uploaded to HuggingFace",
))

_register(Target(
    name="upload_hf",
    deps=["export_parquet", "qc_gate"],
    # non_fatal: a failed HF upload does NOT mean the publish failed —
    # the corpus is on disk, the dashboard is fresh, MCP serves OK. The
    # only consumer that loses is downstream HF parquet pullers (next
    # nightly catches them up). Marking non_fatal also lets the terminal
    # health_check (which depends on upload_hf for ordering) run even
    # when HF rejected the push. Caught in 2026-05-16 review (cascade-
    # skip of health_check on upload_hf failure).
    non_fatal=True,
    description="Step 4 — push parquet shards to HuggingFace dataset mirror",
))

_register(Target(
    name="publish_delta",
    deps=["build_fts5"],
    # non_fatal: env-gated step (OCL_PUBLISH_DELTA), and even when on,
    # delta/snapshot artifact publish is best-effort vs the full Step 4
    # upload. Same reasoning as upload_hf above re: terminal health_check.
    non_fatal=True,
    description="Step 7 — publish delta bundle / SQLite snapshot (env-gated)",
))

_register(Target(
    name="stats_interesting",
    # Recompute the interesting_stats block (graph + top-cited fields
    # of docs/stats.json) AFTER reference_graph is rebuilt. Without
    # this, docs/stats.json would show fresh corpus counts paired with
    # last-week's graph numbers — caught in 2026-05-16 review.
    # Non-fatal: if it fails, prior interesting_stats block is kept.
    deps=["reference_graph", "stats_early"],
    non_fatal=True,
    description="Step 5e — refresh interesting_stats block of stats.json after graph rebuild",
))

_register(Target(
    name="git_push_final",
    # Deps minimised to qc_gate + git_push_early + stats_interesting:
    # those are the ONLY
    # upstream targets that produce docs/ artifacts this final push
    # would commit. The slow-tier targets (enrich_quality, reference_
    # graph, materialien_build, decision_structure, export_parquet,
    # upload_hf, publish_delta) write to their own DBs / parquet /
    # external HF — none touch docs/. Including them as deps in an
    # earlier fix introduced a regression versus linear mode: in
    # linear mode, a publish_delta or upload_hf failure does NOT
    # cascade-skip git_push_final (they're not in GUARDED_STEPS); in
    # DAG mode with those as deps, their failure would cascade-skip
    # the final push and the health check. The pre-existing concern
    # — that git_push_final could topologically run before git_push_
    # early — is addressed by the explicit git_push_early dep here.
    # stats_interesting added 2026-05-16 so the final push includes
    # the freshly-recomputed graph block of stats.json.
    deps=["qc_gate", "git_push_early", "stats_interesting"],
    description="Step 6 — final git push of stats + dashboard. Diff-check short-circuits when nothing changed.",
))

_register(Target(
    name="health_check",
    # Must depend on EVERY delivery target so it truly is the terminal
    # step in DAG mode. git_push_final alone is insufficient — in DAG
    # topological order, upload_hf and publish_delta have no path to
    # health_check via git_push_final (final push deps are minimised
    # to avoid cascade failures, see comment there) and would otherwise
    # schedule AFTER health_check. The docstring "at the very end"
    # was a lie in DAG mode (caught in 2026-05-16 review).
    deps=["git_push_final", "upload_hf", "publish_delta"],
    non_fatal=True,
    description="Step 6b — post-publish smoke check (alerts on failure). Truly terminal.",
))


# ── Topological sort ──────────────────────────────────────────────────


def topological_sort(targets: dict[str, Target]) -> list[str]:
    """Kahn's algorithm. Stable: ties broken alphabetically so the same
    DAG always produces the same order.

    Raises ValueError on cycle or unknown dep.
    """
    in_count: dict[str, int] = {name: 0 for name in targets}
    for name, t in targets.items():
        for dep in t.deps:
            if dep not in targets:
                raise ValueError(f"target {name!r} depends on unknown {dep!r}")
        in_count[name] = len(t.deps)

    # Reverse adjacency: who depends on me?
    dependents: dict[str, list[str]] = defaultdict(list)
    for name, t in targets.items():
        for dep in t.deps:
            dependents[dep].append(name)

    ready: list[str] = sorted([n for n, c in in_count.items() if c == 0])
    order: list[str] = []

    while ready:
        ready.sort()
        current = ready.pop(0)
        order.append(current)
        for dep_name in dependents[current]:
            in_count[dep_name] -= 1
            if in_count[dep_name] == 0:
                ready.append(dep_name)

    if len(order) != len(targets):
        unresolved = [n for n in targets if n not in order]
        raise ValueError(
            f"cyclic dependency detected; unresolved targets: {unresolved}"
        )
    return order


def closure(targets: dict[str, Target], requested: list[str]) -> list[str]:
    """Return the topological order of `requested` plus all transitive
    dependencies. Useful for `make X` semantics — rebuild the minimal
    subgraph needed to produce X.
    """
    keep: set[str] = set()

    def visit(name: str) -> None:
        if name in keep:
            return
        if name not in targets:
            raise ValueError(f"unknown target {name!r}")
        keep.add(name)
        for dep in targets[name].deps:
            visit(dep)

    for r in requested:
        visit(r)
    return [n for n in topological_sort(targets) if n in keep]


# ── Validation ────────────────────────────────────────────────────────


def validate_dag(targets: dict[str, Target]) -> tuple[bool, list[str]]:
    """Returns (ok, errors). Checks for unknown deps and cycles."""
    errors: list[str] = []
    for name, t in targets.items():
        for dep in t.deps:
            if dep not in targets:
                errors.append(f"target {name!r} depends on unknown {dep!r}")
    try:
        topological_sort(targets)
    except ValueError as e:
        errors.append(str(e))
    return (not errors), errors


# ── Runner ────────────────────────────────────────────────────────────


def _eval_dep_status(
    targets: dict[str, Target],
    results: dict[str, bool | str],
    name: str,
) -> str:
    """Inspect a target's deps and report what to do with it.

    Returns:
        "ready"             — all deps complete, no fatal cascade
        "wait"              — at least one dep hasn't finished yet
        "cascade"           — a non-non_fatal dep failed/cascaded
    """
    cascaded = False
    waiting = False
    for dep in targets[name].deps:
        dep_status = results.get(dep)
        if dep_status is None:
            waiting = True
            continue
        if dep_status is True or dep_status == SKIPPED_OPTIN:
            continue
        if dep_status is False or dep_status == SKIPPED_CASCADE:
            if not targets[dep].non_fatal:
                cascaded = True
                break
    if cascaded:
        return "cascade"
    if waiting:
        return "wait"
    return "ready"


def _exec_one(
    name: str,
    target: Target,
    builder: Callable | None,
    args,
) -> bool:
    """Run one target's builder. Returns OK/FAILED. Catches exceptions."""
    if builder is None:
        return OK  # no-op target — caller logs separately
    try:
        return bool(builder(
            args,
            dry_run=getattr(args, "dry_run", False),
            full_rebuild=getattr(args, "full_rebuild", False),
        ))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"  {name}: EXCEPTION — {exc}", exc_info=True)
        return FAILED


def run_targets(
    targets: dict[str, Target],
    builder_map: dict[str, Callable],
    args,
    *,
    requested: list[str] | None = None,
    max_workers: int = 1,
    checkpoint_load: Callable[[], dict[str, bool | str] | None] | None = None,
    checkpoint_save: Callable[[str, bool | str], None] | None = None,
) -> dict[str, bool | str]:
    """Run targets respecting deps, parallel_safe siblings, opt-in skips.

    Args:
        targets: the registry to execute.
        builder_map: name → callable invoked as
            ``builder(args, dry_run=…, full_rebuild=…) -> bool``. Targets
            with no entry in this map are treated as no-ops (logged as OK
            so dependent targets don't cascade-skip).
        args: argparse.Namespace forwarded to each builder.
        requested: if given, run only these targets + their transitive
            dependencies. Otherwise run the entire registry.
        max_workers: 1 (default) for sequential, >1 to enable parallel
            scheduling. Within parallel mode the rule is:
              • parallel_safe=True targets can run concurrently with
                each other, up to max_workers.
              • parallel_safe=False targets run alone — nothing else
                may be executing while they run.
        checkpoint_load: callable returning prior {name: status} dict.
            Targets with status=OK are skipped (already done in a prior
            run). Pass None to disable checkpoint resume.
        checkpoint_save: callback invoked after every target completion
            with (name, status). Pass None to disable persistence.

    Returns:
        ``{target_name: True|False|"skipped_cascade"|"skipped_optin"}``.
    """
    order = closure(targets, requested) if requested else topological_sort(targets)
    in_scope = set(order)
    requested_set = set(requested or [])

    results: dict[str, bool | str] = {}

    # Seed from checkpoint: any target marked True in a prior run is
    # treated as already complete. Failed/skipped statuses are NOT
    # restored — those should be retried on the new run.
    if checkpoint_load is not None:
        prior = checkpoint_load() or {}
        for name, status in prior.items():
            if name in in_scope and status is True:
                results[name] = OK
                logger.info(f"  {name}: SKIPPED (already OK in checkpoint)")

    # First pass: handle opt-in skips for everything still unresolved.
    for name in order:
        if name in results:
            continue
        if targets[name].opt_in and name not in requested_set:
            results[name] = SKIPPED_OPTIN
            logger.info(f"  {name}: SKIPPED (opt-in; pass --target to run)")
            if checkpoint_save:
                checkpoint_save(name, SKIPPED_OPTIN)

    # Sequential path — small, well-tested, used when max_workers <= 1.
    if max_workers <= 1:
        return _run_sequential(
            order, targets, builder_map, args,
            results, checkpoint_save,
        )

    # Parallel path — concurrent.futures with parallel_safe gating.
    return _run_parallel(
        order, targets, builder_map, args,
        results, checkpoint_save, max_workers,
    )


def _run_sequential(
    order: list[str],
    targets: dict[str, Target],
    builder_map: dict[str, Callable],
    args,
    results: dict[str, bool | str],
    checkpoint_save: Callable[[str, bool | str], None] | None,
) -> dict[str, bool | str]:
    for name in order:
        if name in results:
            continue
        status = _eval_dep_status(targets, results, name)
        if status == "cascade":
            results[name] = SKIPPED_CASCADE
            logger.warning(f"  {name}: SKIPPED (cascade)")
            if checkpoint_save:
                checkpoint_save(name, SKIPPED_CASCADE)
            continue
        # status == "ready" — ("wait" can't happen in topo order)
        builder = builder_map.get(name)
        if builder is None:
            results[name] = OK
            logger.info(f"  {name}: no builder registered — treating as OK")
            if checkpoint_save:
                checkpoint_save(name, OK)
            continue
        start = time.time()
        ok = _exec_one(name, targets[name], builder, args)
        elapsed = time.time() - start
        results[name] = ok
        logger.info(f"  {name}: → {'OK' if ok else 'FAILED'} ({elapsed:.1f}s)")
        if checkpoint_save:
            checkpoint_save(name, ok)
    return results


def _run_parallel(
    order: list[str],
    targets: dict[str, Target],
    builder_map: dict[str, Callable],
    args,
    results: dict[str, bool | str],
    checkpoint_save: Callable[[str, bool | str], None] | None,
    max_workers: int,
) -> dict[str, bool | str]:
    """Concurrent runner. parallel_safe siblings co-run up to max_workers;
    parallel_safe=False targets run exclusively (nothing else executing).

    Implementation: dynamic Kahn's-algorithm scheduling — at any moment
    look at all unfinished targets, mark cascade-skips, schedule any
    runnable ones onto the executor pool subject to the parallel-safe
    exclusivity rule, then wait for at least one to finish before
    re-evaluating. This handles the "diamond" pattern naturally: as soon
    as a slow critical step completes, all its parallel-safe children
    become eligible together.
    """
    pending = [n for n in order if n not in results]
    running: dict[concurrent.futures.Future, tuple[str, float]] = {}
    exclusive_active = False

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="dag",
    ) as executor:
        while pending or running:
            # Resolve any new cascade-skips first — once a parent fails,
            # children waiting on it should be marked SKIPPED before we
            # try to schedule anything else.
            still_pending: list[str] = []
            for name in pending:
                status = _eval_dep_status(targets, results, name)
                if status == "cascade":
                    results[name] = SKIPPED_CASCADE
                    logger.warning(f"  {name}: SKIPPED (cascade)")
                    if checkpoint_save:
                        checkpoint_save(name, SKIPPED_CASCADE)
                else:
                    still_pending.append(name)
            pending = still_pending

            # Schedule as many runnable targets as the rules allow.
            scheduled_this_pass = True
            while scheduled_this_pass:
                scheduled_this_pass = False
                if not pending:
                    break
                if exclusive_active:
                    break  # nothing can join while a non-parallel-safe runs
                if len(running) >= max_workers:
                    break
                # Pick the next ready target. Prefer parallel_safe so we
                # don't starve the pool by picking a non-parallel-safe
                # target that requires the pool to be empty.
                pick_idx: int | None = None
                for i, name in enumerate(pending):
                    if _eval_dep_status(targets, results, name) != "ready":
                        continue
                    t = targets[name]
                    if t.parallel_safe:
                        pick_idx = i
                        break
                    # non-parallel-safe needs the entire pool empty
                    if not running:
                        pick_idx = i
                        break
                if pick_idx is None:
                    break
                name = pending.pop(pick_idx)
                t = targets[name]
                builder = builder_map.get(name)
                if builder is None:
                    results[name] = OK
                    logger.info(f"  {name}: no builder registered — treating as OK")
                    if checkpoint_save:
                        checkpoint_save(name, OK)
                    scheduled_this_pass = True
                    continue
                if not t.parallel_safe:
                    exclusive_active = True
                logger.info(f"  {name}: started")
                fut = executor.submit(_exec_one, name, t, builder, args)
                running[fut] = (name, time.time())
                scheduled_this_pass = True

            if not running:
                # Nothing scheduled and nothing running — either everything
                # is done OR all remaining targets are blocked on cascades.
                # The cascade-resolution pass at the top of the loop will
                # handle the latter; if even that doesn't progress, break.
                if not pending:
                    break
                # If pending targets all have at least one un-resolved dep
                # somehow, we're stuck. This shouldn't happen given topo
                # order, but guard against an infinite loop:
                stuck_count_before = sum(
                    1 for n in pending
                    if _eval_dep_status(targets, results, n) == "wait"
                )
                if stuck_count_before == len(pending):
                    logger.error(
                        f"scheduler stuck: {len(pending)} targets waiting "
                        f"with no runners. Pending: {pending}"
                    )
                    for name in pending:
                        results[name] = SKIPPED_CASCADE
                        if checkpoint_save:
                            checkpoint_save(name, SKIPPED_CASCADE)
                    break
                continue

            # Wait for at least one running target to finish.
            done, _ = concurrent.futures.wait(
                running.keys(),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                name, started_at = running.pop(fut)
                t = targets[name]
                ok = fut.result()  # _exec_one already swallowed exceptions
                results[name] = ok
                if not t.parallel_safe:
                    exclusive_active = False
                elapsed = time.time() - started_at
                logger.info(
                    f"  {name}: → {'OK' if ok else 'FAILED'} ({elapsed:.1f}s)"
                )
                if checkpoint_save:
                    checkpoint_save(name, ok)

    return results


# ── CLI ───────────────────────────────────────────────────────────────


def _print_target(name: str, t: Target) -> None:
    tags: list[str] = []
    if t.non_fatal:
        tags.append("non-fatal")
    if t.parallel_safe:
        tags.append("parallel-safe")
    if t.opt_in:
        tags.append("opt-in")
    tag_str = f"  [{', '.join(tags)}]" if tags else ""
    deps_str = f" ← {', '.join(t.deps)}" if t.deps else " (root)"
    print(f"  {name}{deps_str}{tag_str}")
    if t.description:
        print(f"      {t.description}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="DAG runner for OpenCaseLaw publish pipeline (Phase B v0.1)"
    )
    p.add_argument("--validate", action="store_true",
                   help="Check the DAG (no cycles, all deps resolved).")
    p.add_argument("--list", action="store_true",
                   help="Print every registered target with its deps.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the topological order (or the rebuild "
                        "closure when --target is given).")
    p.add_argument("--target", action="append",
                   help="Restrict to this target and its ancestors. "
                        "Can be repeated. Implies --dry-run for v0.1.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.validate:
        ok, errors = validate_dag(REGISTRY)
        if not ok:
            for e in errors:
                logger.error(e)
            return 1
        logger.info(
            f"DAG OK: {len(REGISTRY)} targets, no cycles, all deps resolved."
        )
        return 0

    if args.list:
        for name in topological_sort(REGISTRY):
            _print_target(name, REGISTRY[name])
        return 0

    if args.target:
        order = closure(REGISTRY, args.target)
        print(f"Rebuild closure for {args.target}:")
        for i, name in enumerate(order, 1):
            print(f"  {i:>2}. {name}")
        return 0

    if args.dry_run:
        order = topological_sort(REGISTRY)
        print("Topological order (full pipeline):")
        for i, name in enumerate(order, 1):
            print(f"  {i:>2}. {name}")
        return 0

    print(
        "publish_dag.py — Phase B v0.1 (registry only). "
        "Use --validate, --list, --dry-run, or --target to inspect.\n"
        "The actual pipeline still runs via publish.py; the next commit "
        "wires this runner in behind an OCL_USE_DAG=1 env flag."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
