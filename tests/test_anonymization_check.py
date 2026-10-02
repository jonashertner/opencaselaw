"""anonymization_check.py — personal data left in published decisions.

Offline and synthetic: every identifier below is a documentation sample
(756.9217.0769.85 is the official AHV specimen, CH93 0076 2011 6238 5295 7 the
standard IBAN example) or invented. The noise cases are the false positives of
the 2026-10-02 calibration run, reduced to their shape.

Two autouse guards: the ntfy ping is neutralised and git is never invoked, so
no test can post to the operator topic or touch a repository.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import anonymization_check as ac  # noqa: E402

PAD = "Die Beschwerdeführerin macht geltend, die Vorinstanz habe den Sachverhalt falsch festgestellt. "
HEAD = "Obergericht des Kantons X\nUrteil vom 1. Januar 2026\n" + PAD * 20
TAIL = PAD * 16


def doc(body: str) -> str:
    return HEAD + body + TAIL


def detectors(text: str, tier: str = "alert") -> list[str]:
    return [h["detector"] for h in ac.find_hits(text) if h["tier"] == tier]


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    calls = {"ntfy": [], "git": []}
    monkeypatch.setattr(ac, "_ntfy", lambda n: calls["ntfy"].append(n))
    monkeypatch.setattr(ac, "_git", lambda args: calls["git"].append(args))
    return calls


# --- checksums ---------------------------------------------------------------
def test_ahv_check_digit():
    assert ac.ahv_valid("756.9217.0769.85")
    assert not ac.ahv_valid("756.9217.0769.84")


def test_iban_mod97():
    assert ac.iban_valid("CH93 0076 2011 6238 5295 7")
    assert not ac.iban_valid("CH94 0076 2011 6238 5295 7")


# --- alert tier --------------------------------------------------------------
def test_ahv_number_is_flagged_even_in_the_letterhead():
    text = "Sozialversicherungsgericht\nIV.2024.00001\n756.9217.0769.85\n" + PAD * 30
    assert detectors(text) == ["ahv"]


def test_ahv_shape_with_wrong_check_digit_is_ignored():
    assert detectors(doc("Versichertennummer 756.9217.0769.84 wurde genannt. ")) == []


def test_mobile_number_in_the_body():
    assert detectors(doc("Nachrichten von der Telefonnummer +41 79 555 01 23 («L.________»). ")) == ["mobile"]
    assert detectors(doc("son téléphone (079 555 01 23) entre le 24 septembre et le 14 juillet. ")) == ["mobile"]


def test_landline_and_letterhead_numbers_are_ignored():
    assert detectors(doc("Bitte rufen Sie mich zurück (062 835 00 00). ")) == []
    letterhead = "Tribunal\nT. direct : 079 555 01 23\n" + PAD * 40
    assert detectors(letterhead) == []


def test_home_address_after_a_domicile_word():
    assert detectors(doc("domicilié chez ses parents, chemin des Tilleuls 13, 1318 Pompaples s'est rendu ")) == ["home_address"]
    assert detectors(doc("ihren Wohnsitz per 15. November 2007 von der Musterstrasse 24, 6313 Menzingen, an ")) == ["home_address"]


def test_home_address_after_an_anonymized_name():
    assert detectors(doc("désigne L.________, rue de la Gare 26, 1260 Nyon, en qualité de curateur ")) == ["home_address"]


@pytest.mark.parametrize("body", [
    # counsel's office after the party's (masked) domicile
    "A______, domicilié ______, représenté par Me Jeanne Exemple, avocate, rue de la Gare 7, 1207 Genève, contre ",
    # a public body whose name contains a domicile word
    "Office AI pour les assurés résidant à l'étranger, avenue Edmond-Vaucher 18, 1203 Genève, intimé. ",
    # an office named next to an anonymized officer
    "Service des curatelles et tutelles professionnelles (SCTP), Mme B.________, chemin de Mornex 32, 1014 Lausanne. Objet ",
    "domiciliée à 1003 Lausanne, [...]; III. De nommer la Tutrice générale, chemin de Mornex 32, 1014 Lausanne, en ",
])
def test_counsel_and_public_body_addresses_are_ignored(body):
    assert detectors(doc(body)) == []


def test_party_address_in_the_rubrum_is_flagged():
    text = ("Bundesgericht\nUrteil vom 8. September 2026\nVerfahrensbeteiligte\n"
            "A.A.________ und B.A.________, Musterstrasse 4, 6060 Sarnen, Beschwerdeführer, gegen\n" + PAD * 40)
    assert detectors(text) == ["home_address"]


def test_institutional_addresses_are_ignored():
    body = ("beim Bezirksgericht Winterthur, Lindstrasse 10, 8400 Winterthur, mündlich Berufung. "
            "Kantonspolizei Basel-Stadt, Unterer Rheinweg 24, 4057 Basel. ")
    assert detectors(doc(body)) == []


def test_iban_without_public_body_context():
    filler = "x" * 300
    assert detectors(doc(filler + " überwies er auf CH93 0076 2011 6238 5295 7 den Betrag. " + filler)) == ["iban"]


def test_the_cantons_own_iban_is_ignored():
    body = "IBAN (Kontonummer) CH93 0076 2011 6238 5295 7 Kontoinhaber Kanton Zug, Finanzverwaltung. "
    assert detectors(doc(body)) == []


def test_private_email_in_the_body():
    assert detectors(doc("an die Adresse hans.muster@bluewin.ch gesandt. ")) == ["email"]


@pytest.mark.parametrize("addr", [
    "A____@bluewin.ch",          # masked by the court
    "A._____@bluewin.ch",
    "xx@gmail.com",
    "Klägerin@gmail.com",
    "info@postcom.admin.ch",     # institutional
    "asservate@kapo.zh.ch",
])
def test_masked_and_institutional_emails_are_ignored(addr):
    assert detectors(doc(f"an die Adresse {addr} gesandt. ")) == []


def test_full_birth_date_is_not_a_finding():
    assert detectors(doc("A.________, né le 21 mai 1965, a exercé l'activité de parqueteur. ")) == []


# --- review tier -------------------------------------------------------------
PLACEHOLDERS = "A.________ und B.________ sowie C.________, D.________ und E.________ stritten. "


def test_rare_surname_next_to_placeholders_goes_to_review_only():
    text = doc(PLACEHOLDERS + "Es sei Herrn Beispielmann einzuvernehmen. ")
    assert detectors(text, "review") == ["surname"]
    assert detectors(text) == []


@pytest.mark.parametrize("phrase", [
    "Ordre est donné à Monsieur le Conservateur du Registre foncier",
    "Madame Monsieur Revenus : CHF 0.00",
    "vertreten durch Rechtsanwalt Herr Beispielmann",
])
def test_role_phrases_are_not_surnames(phrase):
    assert detectors(doc(PLACEHOLDERS + phrase + ". "), "review") == []


def test_surname_without_placeholders_is_not_flagged():
    assert detectors(doc("Es sei Herrn Beispielmann einzuvernehmen. "), "review") == []


# --- masking -----------------------------------------------------------------
def test_mask_hides_the_middle():
    assert ac.mask("756.9217.0769.85") == "756.••••.••••.85"
    masked = ac.mask("hans.muster@bluewin.ch")
    assert "muster" not in masked and masked.startswith("han")


def test_snippet_never_carries_the_raw_value():
    text = doc("an die Adresse hans.muster@bluewin.ch gesandt. ")
    h = ac.find_hits(text)[0]
    assert "hans.muster" not in ac._snippet(text, h["start"], h["end"])


# --- shard tail reader + scan ------------------------------------------------
def _row(i: int, scraped: datetime.datetime, text: str) -> dict:
    return {"decision_id": f"xx_gerichte_{i}", "court": "xx_gerichte",
            "docket_number": f"X {i}", "decision_date": "2026-01-01",
            "source_url": "https://example.invalid/x", "full_text": text,
            "scraped_at": scraped.isoformat()}


def _write_shard(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "decisions"
    d.mkdir()
    (d / "xx_gerichte.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return d


def test_scan_reads_only_rows_inside_the_window(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    leak = doc("Telefonnummer +41 79 555 01 23 des Beschuldigten. ")
    d = _write_shard(tmp_path, [
        _row(1, now - datetime.timedelta(days=40), leak),
        _row(2, now - datetime.timedelta(days=1), leak),
        _row(3, now - datetime.timedelta(hours=2), doc("Nichts Auffälliges. ")),
    ])
    result = ac.scan(d, window_days=4)
    assert result["scanned"] == 2
    assert [h["decision_id"] for h in result["hits"]] == ["xx_gerichte_2"]
    assert "value" not in result["hits"][0]
    assert "555" not in json.dumps(result, ensure_ascii=False)


def test_tail_reader_crosses_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "CHUNK", 2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = [_row(i, now - datetime.timedelta(hours=i), doc(f"Entscheid {i}. ")) for i in range(30)]
    d = _write_shard(tmp_path, list(reversed(rows)))
    assert ac.scan(d, window_days=4)["scanned"] == 30


def test_a_repeated_value_is_one_finding(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    text = doc("Nummer +41 79 555 01 23. " + PAD + "Erneut +41 79 555 01 23. ")
    d = _write_shard(tmp_path, [_row(1, now, text)])
    assert len(ac.scan(d, window_days=4)["hits"]) == 1


# --- report + main -----------------------------------------------------------
def test_main_writes_report_and_alerts_with_a_bare_count(tmp_path, monkeypatch, _no_side_effects):
    now = datetime.datetime.now(datetime.timezone.utc)
    d = _write_shard(tmp_path, [_row(1, now, doc("Telefonnummer +41 79 555 01 23 des Beschuldigten. "))])
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(ac, "PRIVATE_REPO", private)

    assert ac.main(["--shard-dir", str(d)]) == 0
    reports = list((private / "anonymization").glob("*.md"))
    assert len(reports) == 1
    body = reports[0].read_text()
    assert "X 1" in body and "Mobiltelefonnummer" in body and "555 01" not in body
    assert _no_side_effects["ntfy"] == [1]

    # Second run: already reported, nothing new to alert on.
    assert ac.main(["--shard-dir", str(d)]) == 0
    assert _no_side_effects["ntfy"] == [1]


def test_review_tier_alone_does_not_alert(tmp_path, monkeypatch, _no_side_effects):
    now = datetime.datetime.now(datetime.timezone.utc)
    d = _write_shard(tmp_path, [_row(1, now, doc(PLACEHOLDERS + "Es sei Herrn Beispielmann einzuvernehmen. "))])
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(ac, "PRIVATE_REPO", private)
    assert ac.main(["--shard-dir", str(d)]) == 0
    assert "Zur Durchsicht" in next((private / "anonymization").glob("*.md")).read_text()
    assert _no_side_effects["ntfy"] == []


def test_scan_only_prints_json_and_touches_nothing(tmp_path, monkeypatch, capsys, _no_side_effects):
    now = datetime.datetime.now(datetime.timezone.utc)
    d = _write_shard(tmp_path, [_row(1, now, doc("Telefonnummer +41 79 555 01 23. "))])
    monkeypatch.setattr(ac, "PRIVATE_REPO", tmp_path / "absent")
    assert ac.main(["--shard-dir", str(d), "--scan-only"]) == 0
    assert json.loads(capsys.readouterr().out)["hits"][0]["detector"] == "mobile"
    assert _no_side_effects == {"ntfy": [], "git": []}
