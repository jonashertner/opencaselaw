"""Parser for the Schaffhauser VRG/JG commentary shard (offline, fixture ePub)."""
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_shk_kommentar_shard as shk  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "shk_kommentar"


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    epub = tmp_path_factory.mktemp("shk") / "excerpt.epub"
    with zipfile.ZipFile(epub, "w") as z:
        for f in sorted((FIXTURE / "OEBPS").glob("*.html")):
            z.write(f, f"OEBPS/{f.name}")
    records, problems = shk.parse_epub(epub)
    return {r["source_record_id"]: r for r in records}, records, problems


def test_no_parser_problems(parsed):
    assert parsed[2] == []


def test_article_hidden_behind_a_repealed_one_is_its_own_record(parsed):
    by = parsed[0]
    assert by["vrg-art-17"]["randziffern"] == 0
    assert "Aufgehoben" in by["vrg-art-17"]["full_text"]
    art18 = by["vrg-art-18"]
    assert art18["authors"] == ["Konrad Waldvogel"]
    assert art18["law"] == "VRG" and art18["article"] == "18"
    assert art18["randziffern"] == 16          # = last margin number in the PDF edition
    assert art18["pub_type"] == "commentary"


def test_first_article_folded_into_a_part_file(parsed):
    art50 = parsed[0]["jg-art-50"]
    assert art50["authors"] == ["Oliver Herrmann"]
    assert art50["law"] == "JG"
    assert art50["randziffern"] == 4
    assert "\nN 1  Art. 50 JG regelt die Zuständigkeit" in art50["full_text"]


def test_footnotes_are_kept_verbatim_and_referenced(parsed):
    text = parsed[0]["vrg-art-18"]["full_text"]
    assert "\n\nFussnoten\n[1] " in text
    body, notes = text.split("\n\nFussnoten\n")
    assert "[1]" in body
    assert len([ln for ln in notes.split("\n") if ln.startswith("[")]) == parsed[0]["vrg-art-18"]["footnotes"]


def test_notice_carried_twice_in_the_epub_yields_one_record(parsed):
    ids = [r["source_record_id"] for r in parsed[1]]
    assert ids.count("vrg-art-55a") == 1


def test_checklists_get_no_margin_numbers(parsed):
    chk = parsed[0]["checkliste-rekurs"]
    assert chk["randziffern"] == 0 and "\nN 1  " not in chk["full_text"]
    assert chk["pub_type"] == "chapter"


def test_licence_and_citation_come_from_the_imprint(parsed):
    r = parsed[0]["vrg-art-18"]
    assert r["license"] == "CC-BY-SA" and r["license_url"] is None
    assert r["rights_raw"] == ["© 2021 – CC BY-NC-ND (Werk), CC BY-SA (Text)"]
    assert r["citation_suggestion"] == (
        "WALDVOGEL, in: Meyer/Herrmann/Bilger (Hrsg.), Kommentar zur Schaffhauser "
        "Verwaltungsrechtspflege, 2021, Art. 18 VRG N. X")
    assert r["doi"] == "10.36862/eiz-411"
