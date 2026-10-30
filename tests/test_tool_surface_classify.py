"""tool_surface_check.classify: an error object is a failure (2026-10-10), and
so is whatever hides one (2026-10-30)."""
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
OPEN_ACCESS_NOTE = "\n\n---\nThis data is provided by OpenCaseLaw.ch, a nonprofit open-access platform."


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


def test_the_open_access_note_does_not_hide_a_timeout():
    # What the probe really received: 14 of these read as OK in 30 daily runs.
    assert tsc.classify("find_relevant_erwaegung", {}, TIMEOUT + OPEN_ACCESS_NOTE, 40) == "FAIL"


def test_the_open_access_note_does_not_hide_an_error():
    body = json.dumps({"error": "E. '1' not found", "error_code": "pinpoint_not_found"}) + OPEN_ACCESS_NOTE
    assert tsc.classify("get_erwaegung", {}, body, 40) == "FAIL"


def test_a_substantive_answer_with_the_note_passes():
    body = json.dumps({"decision_id": "x", "text": "y" * 200}) + OPEN_ACCESS_NOTE
    assert tsc.classify("get_erwaegung", {}, body, 40) == "OK"


def test_the_is_error_flag_fails_whatever_the_body_says():
    resp = {"result": {"isError": True}}
    assert tsc.classify("get_erwaegung", resp, "A long and plausible answer. " * 5, 40) == "FAIL"


def test_a_sample_the_server_ignored_fails():
    body = ("NOTE: ignored unrecognised parameter(s) limit — they did NOT affect this result.\n\n"
            + "# Recent Legislation Changes\n" * 5)
    assert tsc.classify("browse_legislation_changes", {}, body, 40) == "FAIL"
