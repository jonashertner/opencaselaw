"""JS ↔ Python parity for the five structural patterns.

The Word add-in redacts in the browser (tools/word-addin/js/redact.js);
the server re-runs the five structural patterns (quality/redact.py) as a
guard and defence-in-depth scrub. If the two drift, either the guard
rejects text the client considered clean (Pro calls fail) or the scrub
disagrees with what the client un-redacts. This test feeds one fixture
through both and requires byte-identical output.

Skipped when node is not installed (the JS side cannot run). Offline.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from quality.redact import redact

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_JS = _ROOT / "tools" / "word-addin" / "js" / "redact.js"

# Structural PII only — the client also redacts names, addresses, DOBs
# and postal codes, which the server deliberately does not enforce.
FIXTURE = (
    "Kontakt: info@kanzlei-mueller.ch oder jonas.hertner+tag@sub.example.org. "
    "AHV 756.1234.5678.90, nochmals 756.1234.5678.90 und 756 9999 8888 77. "
    "IBAN CH93 0076 2011 6238 5295 7 sowie CH9300762011623852957. "
    "UID CHE-123.456.789 / CHE 123 456 789. "
    "Tel. +41 79 123 45 67, +41 (0)44 123 45 67, 079-123-45-67, 044 123 45 67. "
    "Wie das Bundesgericht in BGE 143 III 480, E. 3.2 ausführt, gilt nach "
    "Art. 41 Abs. 1 OR i.V.m. Art. 756 OR das Verschuldensprinzip; "
    "vgl. Urteil 6B_756/2025 vom 5. April 2025 und SR 220."
)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_structural_patterns_agree_between_js_and_python():
    script = (
        "var r=require(process.argv[1]);"
        "var src=JSON.parse(process.argv[2]);"
        "var out=r.redactPII(src);"
        "process.stdout.write(JSON.stringify({redacted:out.redacted,summary:out.summary.byType}));"
    )
    proc = subprocess.run(
        ["node", "-e", script, str(_JS), json.dumps(FIXTURE)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    js = json.loads(proc.stdout)
    py = redact(FIXTURE)
    assert js["redacted"] == py.redacted
    assert js["summary"] == py.summary
    for leaked in ("info@kanzlei", "756.1234", "CH93", "CHE-123", "79 123 45 67"):
        assert leaked not in py.redacted
    assert "BGE 143 III 480" in py.redacted and "Art. 756 OR" in py.redacted
