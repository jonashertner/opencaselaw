#!/usr/bin/env python3
"""
check_hf_unmanaged_parquet.py — read-only: parquet files in the Hugging Face
dataset repo that no publisher writes or prunes.

Every parquet the pipeline publishes lives under a managed prefix:

  data/        publish.py step 4 (upload_folder, prunes data/*.parquet),
               publish_delta.py (data/delta-{date}.parquet),
               pipeline.py (data/daily/)
  graph/       publish.py step 4 aux upload (prunes graph/*.parquet)
  structure/   publish.py step 4 aux upload (prunes structure/*.parquet)
  artifacts/   publish_delta.py deltas + manifest, the verification pack

A parquet anywhere else is never replaced or removed, so it goes stale without
any error. On 2026-10-07 the repo root held 99 such files from the manual
uploads of 2026-03-03/14; load_dataset ignores them (the card reads
data/*.parquet), but a direct download served the March corpus. See
runbooks/hf_root_parquet_cleanup_2026-10-07.md.

Never writes to the repo. Exit status: 0 none found, 1 unmanaged parquet
found, 2 the listing failed.

Usage:
    python3 scripts/check_hf_unmanaged_parquet.py
    python3 scripts/check_hf_unmanaged_parquet.py --details     # + last commit per file
    python3 scripts/check_hf_unmanaged_parquet.py --json
    python3 scripts/check_hf_unmanaged_parquet.py --files-from list.txt   # offline
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

HF_REPO_ID = "voilaj/swiss-caselaw"

# Prefixes a publisher owns (see the module docstring). Each ends with "/":
# an empty prefix would mark the repo root managed.
MANAGED_PREFIXES: tuple[str, ...] = ("data/", "graph/", "structure/", "artifacts/")


def unmanaged_parquet(paths: Iterable[str],
                      managed_prefixes: Iterable[str] = MANAGED_PREFIXES) -> list[str]:
    """Parquet paths outside every managed prefix, sorted and de-duplicated."""
    prefixes = tuple(managed_prefixes)
    return sorted({
        p for p in paths
        if p.lower().endswith(".parquet") and not p.startswith(prefixes)
    })


def list_remote_files(repo_id: str, revision: str) -> tuple[str, list[str]]:
    """(commit sha, every path) of the dataset repo at revision."""
    from huggingface_hub import HfApi

    api = HfApi()
    sha = api.dataset_info(repo_id, revision=revision).sha
    return sha, api.list_repo_files(repo_id, repo_type="dataset", revision=sha)


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

    found = unmanaged_parquet(paths)
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
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
