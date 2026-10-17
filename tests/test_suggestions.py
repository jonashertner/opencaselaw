"""Suggestions from opencaselaw.ch/vorschlag/: storage, limits, privacy, REST routes."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import suggestions as sg  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test writes its own file and never reaches ntfy.sh."""
    monkeypatch.setenv("OCL_SUGGESTIONS_DB", str(tmp_path / "suggestions.db"))
    monkeypatch.delenv("OCL_SUGGESTIONS", raising=False)
    sent = []
    monkeypatch.setattr(sg, "_notify_async", lambda msg: sent.append(msg))
    return sent


def _ok(text="Die Entscheide der Berner Steuerrekurskommission fehlen.", **kw):
    return dict({"text": text, "kind": "source", "lang": "de"}, **kw)


def test_submit_stores_and_returns_number_and_token(_isolated):
    code, body = sg.submit(_ok(), "198.51.100.7")
    assert code == 201
    assert body["no"] == "0001"
    assert sg.TOKEN_RE.match(body["token"])
    code2, body2 = sg.submit(_ok("Ein Filter nach Spruchkörper wäre hilfreich."), "198.51.100.8")
    assert body2["no"] == "0002" and body2["token"] != body["token"]
    # the alert names the number and kind, never the text (ntfy topics are public)
    assert _isolated[0].startswith("Vorschlag 0001 · Quelle")
    assert "Berner" not in _isolated[0]


def test_status_reads_back_without_email_or_ip():
    _, body = sg.submit(_ok(email="person@kanzlei.ch"), "198.51.100.7")
    code, st = sg.status(body["token"])
    assert code == 200
    assert st["no"] == "0001" and st["status"] == "received" and st["kind"] == "source"
    assert "email" not in st and "ip_hash" not in st
    assert "Steuerrekurskommission" in st["text"]


@pytest.mark.parametrize("token", ["", "short", "UPPERCASE1", "aaaaaaaaa0", "../../etc", "a" * 11])
def test_status_rejects_malformed_tokens(token):
    assert sg.status(token)[0] == 404


def test_unknown_token_is_404():
    sg.submit(_ok(), "198.51.100.7")
    assert sg.status("abcdefghjk")[0] == 404


def test_server_redacts_personal_data_again():
    # quality/redact.py carries the numeric identifiers (AHV, IBAN, phone, …); names after a
    # title ("Frau Anna Müller") are caught only by the browser module the page runs first.
    _, body = sg.submit(_ok("BGE 147 III 1 hat das falsche Datum. AHV 756.1234.5678.97, IBAN CH93 0076 2011 6238 5295 7"), "198.51.100.7")
    st = sg.status(body["token"])[1]
    assert "756.1234.5678.97" not in st["text"] and "CH93 0076" not in st["text"]
    assert "BGE 147 III 1" in st["text"]  # citations are not personal data


@pytest.mark.parametrize("payload,err", [
    ("not a dict", "invalid_json"),
    ({"kind": "source"}, "text_required"),
    ({"text": "zu kurz"}, "text_too_short"),
    ({"text": "x" * (sg.MAX_LEN + 1)}, "text_too_long"),
    ({"text": "Seht hier " + " ".join(f"https://spam.example/{i}" for i in range(4))}, "too_many_links"),
    ({"text": "Eine ganz normale Anregung.", "email": "kein-mail"}, "email_invalid"),
])
def test_validation(payload, err):
    assert sg.submit(payload, "198.51.100.7") == (400, {"error": err})


def test_unknown_kind_and_lang_fall_back():
    clean, err = sg.validate({"text": "Eine ganz normale Anregung.", "kind": "<script>", "lang": "xx",
                              "context": {"type": "search", "value": "Mietzins"}})
    assert err is None and clean["kind"] == "other" and clean["lang"] == "de"
    assert (clean["context_type"], clean["context_value"]) == ("search", "Mietzins")
    clean, _ = sg.validate({"text": "Eine ganz normale Anregung.", "context": {"type": "evil", "value": "x"}})
    assert clean["context_type"] is None


def test_honeypot_answers_like_success_and_keeps_nothing(_isolated):
    code, body = sg.submit(_ok(website="http://spam.example"), "198.51.100.7")
    assert code == 201 and body["no"] == "0000"
    assert not sg.db_path().exists() or sg.connect().execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] == 0
    assert _isolated == []


def test_rate_limit_per_ip_per_hour():
    for i in range(sg.PER_HOUR):
        assert sg.submit(_ok(f"Anregung Nummer {i} mit genug Text."), "198.51.100.7")[0] == 201
    assert sg.submit(_ok(), "198.51.100.7") == (429, {"error": "rate_limited"})
    assert sg.submit(_ok(), "198.51.100.99")[0] == 201  # another address is unaffected


def test_ip_is_stored_only_as_a_salted_day_hash():
    sg.submit(_ok(), "198.51.100.7")
    row = sqlite3.connect(sg.db_path()).execute("SELECT ip_hash FROM suggestions").fetchone()
    assert row[0] and "198.51.100.7" not in row[0] and len(row[0]) == 20
    salt = sg.db_path().with_suffix(".salt")
    assert salt.exists() and len(salt.read_bytes()) == 32


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("OCL_SUGGESTIONS", "0")
    assert sg.submit(_ok(), "198.51.100.7") == (503, {"error": "suggestions_disabled"})
    assert sg.status("abcdefghjk") == (503, {"error": "suggestions_disabled"})


def test_set_status_and_note():
    _, body = sg.submit(_ok(), "198.51.100.7")
    row = sg.set_status(1, "planned", "Kommt mit dem nächsten Lauf.")
    assert row["status"] == "planned"
    st = sg.status(body["token"])[1]
    assert st["status"] == "planned" and st["note"] == "Kommt mit dem nächsten Lauf."
    with pytest.raises(ValueError):
        sg.set_status(1, "bogus")
    with pytest.raises(KeyError):
        sg.set_status(999, "read")


def test_admin_cli_prints_mailto_when_email_given(capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import suggestions_admin
    sg.submit(_ok(email="person@kanzlei.ch"), "198.51.100.7")
    assert suggestions_admin.main(["set", "1", "shipped", "--note", "Seit heute in der Suche."]) == 0
    out = capsys.readouterr().out
    assert "0001 → shipped" in out and out.strip().splitlines()[-1].startswith("mailto:person@kanzlei.ch?subject=")
    assert suggestions_admin.main(["list", "--all"]) == 0
    assert "0001" in capsys.readouterr().out


# ── REST ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    import uvicorn
    from starlette.testclient import TestClient
    import mcp_server as m
    for name in ("_capture_event", "_record_tool_call", "_record_tool_outcome", "_record_query"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(m, "_warm_page_cache", lambda: None)
    monkeypatch.setattr(m, "_log_startup", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app))
    m.main_remote("127.0.0.1", 0)
    return TestClient(captured["app"])


def test_rest_roundtrip(client):
    r = client.post("/api/suggestions", json=_ok(), headers={"X-Real-IP": "198.51.100.7"})
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    token = r.json()["token"]
    s = client.get(f"/api/suggestions/{token}")
    assert s.status_code == 200 and s.json()["no"] == "0001"
    assert client.get("/api/suggestions/abcdefghjk").status_code == 404


def test_rest_rejects_bad_bodies(client):
    assert client.post("/api/suggestions", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 400
    big = {"text": "x" * 30_000}
    assert client.post("/api/suggestions", json=big).status_code == 413


def test_rest_routes_stay_out_of_the_openapi_schema(client):
    paths = client.get("/api/openapi.json").json().get("paths", {})
    assert not any("suggestions" in p for p in paths)


def test_admin_cli_refuses_a_file_owned_by_another_user(monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import os
    import suggestions_admin
    sg.submit(_ok(), "198.51.100.7")
    monkeypatch.setattr(os, "geteuid", lambda: sg.db_path().stat().st_uid + 1)
    assert suggestions_admin.main(["list"]) == 2
    assert "sudo -u mcp" in capsys.readouterr().err
