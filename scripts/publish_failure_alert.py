#!/usr/bin/env python3
"""Triggered by systemd OnFailure= when opencaselaw-publish fails.

Posts the tail of the publish log to ntfy.sh and writes a marker file the
dashboard can surface. Best-effort: never raises (the alert path itself
must not generate further failure noise).
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LOG = Path("/opt/caselaw/repo/logs/publish.log")
STATE_DIR = Path("/opt/caselaw/repo/state")
# Operator topic from /opt/caselaw/ops.env (systemd drop-in), never in the code:
# see publish.NTFY_TOPIC. Unset, the alert is only the marker file.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

# Lines we surface in the alert body (most recent N matching).
ERROR_MARKERS = (
    "FAILED",
    "Error",
    "failed:",
    "Traceback",
    "exit code",
    "PRE-FLIGHT",
    "disk is full",
    "No space left on device",
)


def collect_tail() -> str:
    if not LOG.exists():
        return "(publish.log missing)"
    try:
        with open(LOG) as f:
            lines = f.readlines()[-300:]
    except OSError as e:
        return f"(unable to read log: {e})"
    interesting = [
        line.rstrip() for line in lines
        if any(marker in line for marker in ERROR_MARKERS)
    ]
    if not interesting:
        return f"(no error markers in last 300 lines; see {LOG})"
    return "\n".join(interesting[-20:])


def post_ntfy(body: str) -> None:
    if not NTFY_URL:
        print("ntfy skipped: NTFY_TOPIC not set", file=sys.stderr)
        return
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=body.encode("utf-8", errors="replace"),
            headers={
                "Title": "opencaselaw publish FAILED",
                "Priority": "high",
                "Tags": "warning",
            },
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"ntfy post failed: {e}", file=sys.stderr)


def write_marker(body: str) -> None:
    try:
        STATE_DIR.mkdir(exist_ok=True)
        (STATE_DIR / "last_publish_failure.json").write_text(json.dumps({
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "tail": body,
        }, indent=2))
    except OSError as e:
        print(f"marker write failed: {e}", file=sys.stderr)


def main() -> int:
    tail = collect_tail()
    post_ntfy(tail)
    write_marker(tail)
    print("publish-failure alert dispatched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
