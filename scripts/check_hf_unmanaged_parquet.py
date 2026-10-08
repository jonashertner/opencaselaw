#!/usr/bin/env python3
"""
check_hf_unmanaged_parquet.py — read-only checks of the Hugging Face dataset
repo's layout: parquet files that no publisher writes or prunes, and what the
dataset card's default load_dataset config reads under data/.

Every parquet the pipeline publishes lives under a managed prefix:

  data/        publish.py step 4 (upload_folder, prunes data/*.parquet),
               publish_delta.py (data/delta-{date}.parquet),
               pipeline.py (data/daily/)
  graph/       publish.py step 4 aux upload (prunes graph/*.parquet)
  structure/   publish.py step 4 aux upload (prunes structure/*.parquet)
  artifacts/   publish_delta.py deltas + manifest, the verification pack

A parquet anywhere else is never replaced or removed, so it goes stale without
any error. On 2026-10-07 the repo root held 99 such files from the manual
uploads of 2026-03-03/14; load_dataset ignores them, but a direct download
served the March corpus. See runbooks/hf_root_parquet_cleanup_2026-10-07.md.

The card's default config must read every court file under data/ and nothing
else: data/delta-{date}.parquet has other columns than the court files, and
load_dataset fails on a split whose files disagree ("column names don't
match"; the card's data/*.parquet read it until 2026-10).

Never writes to the repo. Exit status: 0 clean, 1 a finding, 2 the listing
failed.

Usage:
    python3 scripts/check_hf_unmanaged_parquet.py
    python3 scripts/check_hf_unmanaged_parquet.py --details     # + last commit per file
    python3 scripts/check_hf_unmanaged_parquet.py --json
    python3 scripts/check_hf_unmanaged_parquet.py --card dataset_card.md   # local card, not the Hub's
    python3 scripts/check_hf_unmanaged_parquet.py --files-from list.txt   # offline
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path

HF_REPO_ID = "voilaj/swiss-caselaw"
REPO = Path(__file__).resolve().parent.parent
CARD_PATH = REPO / "dataset_card.md"  # uploaded as the Hub README by publish.py step 4

# Prefixes a publisher owns (see the module docstring). Each ends with "/":
# an empty prefix would mark the repo root managed.
MANAGED_PREFIXES: tuple[str, ...] = ("data/", "graph/", "structure/", "artifacts/")

# publish_delta.py's daily file under data/; every other parquet directly
# under data/ is one court of export_parquet (DECISION_SCHEMA).
DELTA_PREFIX = "data/delta-"


def unmanaged_parquet(paths: Iterable[str],
                      managed_prefixes: Iterable[str] = MANAGED_PREFIXES) -> list[str]:
    """Parquet paths outside every managed prefix, sorted and de-duplicated."""
    prefixes = tuple(managed_prefixes)
    return sorted({
        p for p in paths
        if p.lower().endswith(".parquet") and not p.startswith(prefixes)
    })


def card_default_patterns(card_text: str) -> list[str]:
    """data_files patterns of the card's default config (YAML front matter)."""
    import yaml

    m = re.match(r"\s*---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", card_text, re.DOTALL)
    meta = (yaml.safe_load(m.group(1)) if m else None) or {}
    for cfg in meta.get("configs") or []:
        if cfg.get("config_name") != "default":
            continue
        files = cfg.get("data_files") or []
        out: list[str] = []
        for entry in [files] if isinstance(files, str) else files:
            p = entry.get("path") if isinstance(entry, dict) else entry
            out.extend([p] if isinstance(p, str) else list(p or []))
        return out
    return []


def card_config_gaps(paths: Iterable[str], patterns: Iterable[str]) -> dict[str, list[str]]:
    """Against the parquet files directly under data/: court files the
    patterns skip, and other files (a delta) they read."""
    pats = list(patterns)
    data = {p for p in paths
            if p.startswith("data/") and p.count("/") == 1 and p.endswith(".parquet")}
    loaded = {p for p in data if any(fnmatchcase(p, pat) for pat in pats)}
    courts = {p for p in data if not p.startswith(DELTA_PREFIX)}
    return {"courts_not_loaded": sorted(courts - loaded),
            "non_courts_loaded": sorted(loaded - courts)}


def layout_findings(paths: list[str], card_text: str) -> list[str]:
    """One line per problem; empty when the layout is clean."""
    out = []
    stale = unmanaged_parquet(paths)
    if stale:
        out.append(f"{len(stale)} parquet file(s) outside {', '.join(MANAGED_PREFIXES)} "
                   f"(e.g. {', '.join(stale[:3])}); never replaced, so stale")
    patterns = card_default_patterns(card_text)
    if not patterns:
        out.append("dataset card has no default config data_files")
        return out
    gaps = card_config_gaps(paths, patterns)
    if gaps["non_courts_loaded"]:
        out.append(f"card default config {patterns} also reads {', '.join(gaps['non_courts_loaded'])}; "
                   f"load_dataset fails on its other columns")
    if gaps["courts_not_loaded"]:
        out.append(f"card default config {patterns} skips court file(s) "
                   f"{', '.join(gaps['courts_not_loaded'])}")
    return out


def list_remote_files(repo_id: str, revision: str) -> tuple[str, list[str]]:
    """(commit sha, every path) of the dataset repo at revision."""
    from huggingface_hub import HfApi

    api = HfApi()
    sha = api.dataset_info(repo_id, revision=revision).sha
    return sha, api.list_repo_files(repo_id, repo_type="dataset", revision=sha)


def fetch_remote_card(repo_id: str, revision: str) -> str:
    """The README.md the Hub serves at revision (what load_dataset reads)."""
    from huggingface_hub import HfFileSystem

    return HfFileSystem().read_text(f"datasets/{repo_id}@{revision}/README.md", encoding="utf-8")


def last_commits(repo_id: str, paths: list[str], revision: str) -> dict[str, dict]:
    """Size and last commit per path (get_paths_info, 50 paths per call)."""
    from huggingface_hub import HfApi

    api = HfApi()
    out: dict[str, dict] = {}
    for i in range(0, len(paths), 50):
        for info in api.get_paths_info(repo_id, paths[i:i + 50], repo_type="dataset",
                                       revision=revision, expand=True):
            lc = getattr(info, "last_commit", None)
            out[info.path] = {
                "size": getattr(info, "size", None),
                "last_commit": lc.oid if lc else None,
                "last_commit_date": lc.date.isoformat() if lc else None,
                "last_commit_title": lc.title if lc else None,
            }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip(),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=HF_REPO_ID)
    ap.add_argument("--revision", default="main")
    ap.add_argument("--card", type=Path,
                    help="dataset card whose default config to check (default: the Hub's "
                         "README.md at the listed revision; dataset_card.md with --files-from)")
    ap.add_argument("--files-from", type=Path,
                    help="read the repo file list (one path per line) from this file "
                         "instead of the Hub; no network")
    ap.add_argument("--details", action="store_true",
                    help="add size and last commit per unmanaged file (Hub only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.files_from is not None:
        sha = None
        paths = [ln.strip() for ln in args.files_from.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
    else:
        try:
            sha, paths = list_remote_files(args.repo, args.revision)
        except Exception as e:  # noqa: BLE001 — a failed listing is not a finding
            print(f"listing {args.repo}@{args.revision} failed: {e}", file=sys.stderr)
            return 2

    if args.card is not None:
        card_name, card_text = str(args.card), args.card.read_text(encoding="utf-8")
    elif sha is None:
        card_name, card_text = str(CARD_PATH), CARD_PATH.read_text(encoding="utf-8")
    else:
        try:
            card_name, card_text = f"README.md@{sha[:8]}", fetch_remote_card(args.repo, sha)
        except Exception as e:  # noqa: BLE001
            print(f"reading README.md@{sha[:8]} failed: {e}", file=sys.stderr)
            return 2

    found = unmanaged_parquet(paths)
    patterns = card_default_patterns(card_text)
    gaps = card_config_gaps(paths, patterns)
    findings = layout_findings(paths, card_text)
    details: dict[str, dict] = {}
    if found and args.details and sha is not None:
        try:
            details = last_commits(args.repo, found, sha)
        except Exception as e:  # noqa: BLE001 — the list itself is still the result
            print(f"last-commit lookup failed: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "repo": args.repo,
            "revision": sha or args.revision,
            "managed_prefixes": list(MANAGED_PREFIXES),
            "files_listed": len(paths),
            "unmanaged_parquet": [{"path": p, **details.get(p, {})} for p in found],
            "card": {"source": card_name, "default_patterns": patterns, **gaps},
            "findings": findings,
        }, indent=2))
    else:
        where = f"{args.repo}@{sha[:8]}" if sha else str(args.files_from)
        print(f"{where}: {len(found)} parquet file(s) outside "
              f"{', '.join(MANAGED_PREFIXES)} ({len(paths)} files listed)")
        for p in found:
            d = details.get(p)
            if d:
                print(f"  {p}\t{d['size']}\t{(d['last_commit'] or '')[:8]}\t"
                      f"{d['last_commit_date'] or ''}\t{d['last_commit_title'] or ''}")
            else:
                print(f"  {p}")
        print(f"{card_name} default config {patterns}: skips {len(gaps['courts_not_loaded'])} court "
              f"file(s), reads {len(gaps['non_courts_loaded'])} other file(s)")
        for p in gaps["courts_not_loaded"] + gaps["non_courts_loaded"]:
            print(f"  {p}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
