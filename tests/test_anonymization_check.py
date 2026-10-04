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
    assert detectors(doc("A.________, domicilié chez ses parents, chemin des Tilleuls 13, 1318 Pompaples s'est rendu ")) == ["home_address"]
    assert detectors(doc("B.________ verlegte ihren Wohnsitz per 15. November 2007 von der Musterstrasse 24, 6313 Menzingen, an ")) == ["home_address"]


def test_domicile_without_an_anonymized_party_is_review_only():
    """Whose domicile is not certain: listed for the weekly reading, not alerted."""
    text = doc("domicilié chez ses parents, chemin des Tilleuls 13, 1318 Pompaples s'est rendu ")
    assert detectors(text) == []
    assert detectors(text, tier="review") == ["home_address"]


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
    # back-scan 2026-10-02: bodies "domiciled" somewhere, upper-case names, sis/sise
    "SUVA, CAISSE NATIONALE SUISSE D'ASSURANCE EN CAS D'ACCIDENTS, domicilié Fluhmattstrasse 1, 6002 LUCERNE Intimée ",
    "Madame L______, domiciliée à Onex recourante contre SERVICE DES PRESTATIONS COMPLEMENTAIRES, sis route de Chêne 54, 1208 Genève intimé ",
    "alle wohnhaft (...) Beschwerdeführende, gegen Staatssekretariat für Migration (SEM), Quellenweg 6, 3003 Bern, Vorinstanz. ",
    "X______, domicilié ______, actuellement détenu à la prison de Champ-Dollon, chemin de Champ-Dollon 22, 1241 Puplinge, comparant ",
    # the court masked the street name itself
    "Monsieur A______, domicilié rue B______ 12, 1207 Genève, appelant d'un jugement ",
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


def test_iban_named_as_a_persons_account():
    filler = "x" * 300
    assert detectors(doc(filler + " überwies er auf sein Konto CH93 0076 2011 6238 5295 7 den Betrag. " + filler)) == ["iban"]
    assert detectors(doc(filler + " sur le compte PostFinance ouvert au nom de B.K.________, IBAN CH93 0076 2011 6238 5295 7, dès " + filler)) == ["iban"]


def test_iban_whose_owner_is_unclear_is_review_only():
    filler = "x" * 300
    text = doc(filler + " überwies er auf CH93 0076 2011 6238 5295 7 den Betrag. " + filler)
    assert detectors(text) == []
    assert detectors(text, tier="review") == ["iban"]


@pytest.mark.parametrize("body", [
    # full-corpus calibration 2026-10-05: offices', counsel's and charities' accounts
    "auf das Konto des Justiz- und Sicherheitsdepartement Basel-Stadt (5100), Bevölkerungsdienste und Migration, 4001 Basel, IBAN: CH93 0076 2011 6238 5295 7, BIC: POFICHBEXXX ",
    "payable sur le compte de Me Gillard CCP IBAN CH93 0076 2011 6238 5295 7, pour solde de tout compte ",
    "qu’il versera à Médecins sans frontières Suisse, 1211 Genève 2, sur le compte IBAN CH93 0076 2011 6238 5295 7. ",
    "le séquestre conservatoire du compte de fonctionnement IBAN CH93 0076 2011 6238 5295 7 dont Z.________ SA est titulaire ",
])
def test_accounts_of_offices_counsel_companies_and_charities_are_dropped(body):
    filler = "x" * 300
    text = doc(filler + body + filler)
    assert detectors(text) == [] and detectors(text, tier="review") == []


@pytest.mark.parametrize("body", [
    "Verfahrensbeteiligte A.________, Beschwerdeführer, gegen Kommando Operationen (Kdo Op), Korpskommandant B.________, Papiermühlestrasse 20, 3003 Bern, Beschwerdegegner ",
    "gegen B.________ in Liquidation, vormals C.________, Hauptstrasse 12, 8840 Einsiedeln, Beschwerdegegnerin ",
    "Beco Berner Wirtschaft, z.H. Frau K.________, Laupenstrasse 22, 3011 Bern, ",
])
def test_addresses_of_offices_and_companies_are_dropped(body):
    text = doc(body)
    assert detectors(text) == [] and detectors(text, tier="review") == []


@pytest.mark.parametrize("body", [
    # the bench line before a Rubrum names a court, not the party
    "Bundesrichter Zünd, Präsident, Gerichtsschreiber Feller. Verfahrensbeteiligte X.________, Weidenstrasse 12, 4054 Basel, Beschwerdeführer, ",
    "Greffière : Mme von Zwehl. Participants à la procédure A.________, rue de la Gare 26, 1213 Onex, recourant, ",
])
def test_a_rubrum_address_after_the_bench_line_is_an_alert(body):
    assert detectors(doc(body)) == ["home_address"]


def test_a_counselling_line_is_not_a_persons_phone():
    text = doc("hat sich bei der Beratungsstelle für gewaltausübende Personen (079 555 01 23) zu einer Beratung anzumelden ")
    assert detectors(text) == [] and detectors(text, tier="review") == []


def test_a_mobile_number_without_phone_context_is_review_only():
    text = doc("die Initialen MKG und 079 555 01 23 im Inserat. ")
    assert detectors(text) == [] and detectors(text, tier="review") == ["mobile"]


def test_old_ahv_number_with_its_label():
    assert detectors(doc("du compte de M. C__________, N° AVS 260.68.476.118, la somme de 35'092 fr. ")) == ["ahv"]
    assert detectors(doc("AHV-Nr. 123.45.678.113 des Versicherten ")) == ["ahv"]
    # without the label the same shape is any reference number
    assert detectors(doc("Geschäft 260.68.476.118 vom ")) == []


def test_the_cantons_own_iban_is_ignored():
    body = "IBAN (Kontonummer) CH93 0076 2011 6238 5295 7 Kontoinhaber Kanton Zug, Finanzverwaltung. "
    assert detectors(doc(body)) == []


def test_private_email_in_the_body():
    assert detectors(doc("an die Adresse hans.muster@bluewin.ch gesandt. ")) == ["email"]


@pytest.mark.parametrize("addr", [
    "A____@bluewin.ch",          # masked by the court
    "A._____@bluewin.ch",
    "xx@gmail.com",
    "A1@gmail.com",
    "xyz@gmail.com",             # full-corpus calibration 2026-10-05
    "II.@hotmail.com",
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
