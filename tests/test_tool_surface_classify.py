"""tool_surface_check.classify: an error object is a failure (2026-10-10)."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "tsc", Path(__file__).resolve().parents[1] / "scripts" / "tool_surface_check.py")
tsc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tsc)

TIMEOUT = json.dumps({"error": "server_timeout", "tool": "get_doctrine", "timeout_seconds": 60,
                      "retry_after_seconds": 30, "message": "This call exceeded the server-side limit "
                      "of 60s and was aborted so your connection is not left hanging. " * 3})


def test_a_server_timeout_answer_fails():
    assert len(TIMEOUT) > 200
    assert tsc.classify("get_doctrine", {}, TIMEOUT, 200) == "FAIL"


def test_a_substantive_json_answer_passes():
    body = json.dumps({"query": "Vertrauensprinzip", "leading_cases": [{"bge_ref": "BGE 144 III 93"}] * 20})
    assert tsc.classify("get_doctrine", {}, body, 200) == "OK"


def test_prose_and_json_without_an_error_are_untouched():
    assert tsc.classify("search", {}, "Found 20 result(s). " * 20, 200) == "OK"
    assert tsc.classify("x", {}, json.dumps({"error": None, "rows": list(range(100))}), 50) == "OK"
    assert tsc.classify("x", {}, "", 10) == "EMPTY"
