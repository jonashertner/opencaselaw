#!/usr/bin/env python3
"""Mint or revoke an API key for the per-IP quota bypass system.

Usage:
    # Mint a 10× key for a known institutional adopter
    python3 scripts/mint_quota_key.py mint --label "Institutional adopter" \
        --multiplier 10

    # Mint a 100× key for a paying commercial integrator
    python3 scripts/mint_quota_key.py mint --label "ACME-Legal Pro" \
        --multiplier 100 --note "Stripe sub_xyz123"

    # List existing keys
    python3 scripts/mint_quota_key.py list

    # Revoke a key
    python3 scripts/mint_quota_key.py revoke <key>

The key is sent as the X-OCL-Key header on the request and resolves to a
multiplier on the per-endpoint daily quota (DEFAULT_QUOTAS in
web_api/ocl_quota.py). 10× lets a careful commercial caller comfortably
operate; 100× is paid-tier territory.

This is the scaffold for the full Stripe-billed commercial-key tier;
manual minting today, Stripe-webhook minting later.
"""
from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "output" / "quota.db"


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        # Initialize via the canonical path
        sys.path.insert(0, str(REPO_ROOT))
        from web_api import ocl_quota  # noqa
        ocl_quota._init_db()
    return sqlite3.connect(str(DB_PATH))


def mint(label: str, multiplier: float, note: str | None) -> None:
    key = "ocl_" + secrets.token_urlsafe(24)
    conn = _connect()
    conn.execute(
        "INSERT INTO api_keys (key, label, multiplier, created_at, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (key, label, multiplier, datetime.now(timezone.utc).isoformat(), note),
    )
    conn.commit()
    conn.close()
    print(f"Minted key: {key}")
    print(f"  label:      {label}")
    print(f"  multiplier: {multiplier}x")
    if note:
        print(f"  note:       {note}")
    print()
    print("Send to caller as the X-OCL-Key header.")
    print("Test:")
    print(f"  curl -H 'X-OCL-Key: {key}' https://mcp.opencaselaw.ch/api/attest ...")


def list_keys() -> None:
    conn = _connect()
    rows = conn.execute(
        "SELECT key, label, multiplier, created_at, note "
        "FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    if not rows:
        print("(no keys)")
        return
    for key, label, mult, created, note in rows:
        masked = key[:8] + "…" + key[-4:]
        print(f"{masked}  {mult:5.1f}x  {created[:19]}  {label}")
        if note:
            print(f"          note: {note}")


def revoke(key: str) -> None:
    conn = _connect()
    cur = conn.execute("DELETE FROM api_keys WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    print(f"Revoked {cur.rowcount} key(s)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("mint", help="Issue a new key")
    pm.add_argument("--label", required=True, help="Human-readable label")
    pm.add_argument("--multiplier", type=float, default=10.0,
                    help="Quota multiplier (default 10x)")
    pm.add_argument("--note", help="Free-form note (e.g. Stripe sub id)")

    sub.add_parser("list", help="List existing keys")

    pr = sub.add_parser("revoke", help="Revoke a key")
    pr.add_argument("key")

    args = p.parse_args()
    if args.cmd == "mint":
        mint(args.label, args.multiplier, args.note)
    elif args.cmd == "list":
        list_keys()
    elif args.cmd == "revoke":
        revoke(args.key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
