"""UN human-rights recommendations to Switzerland (OHCHR Universal Human Rights
Index) in practice.db.

Golden fixtures: real records from the 2026-10-23 UHRI exports
(export-full-en.json / export-full-fr.json) — nine Swiss paragraphs chosen to
cover every shape the parser meets, plus one non-Swiss record. No network: the
streaming parser reads an in-memory array, run() reads the fixtures through a
stubbed download.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "practice"

from scrapers.practice import un_uhri as u  # noqa: E402


def _load(lang: str) -> list[dict]:
    return json.loads((FIX / f"uhri_{lang}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stubs():
    return u.build_stubs(_load("en"), _load("fr"))


def _by(stubs, lang, doc_number):
    hits = [s for s in stubs if s["language"] == lang and s["doc_number"] == doc_number]
    assert len(hits) == 1, (lang, doc_number, [s["doc_number"] for s in stubs])
    return hits[0]


# ───────────────────────────────────────────── streaming parser

def test_iter_json_array_survives_chunk_boundaries():
    records = _load("en")
    raw = json.dumps(records, ensure_ascii=False, indent=1)
    # A 7-character chunk splits every object, string and escape many times.
    got = list(u.iter_json_array(io.StringIO(raw), chunk_size=7))
    assert got == records


def test_iter_json_array_rejects_truncation():
    raw = json.dumps(_load("en"))
    with pytest.raises(ValueError):
        list(u.iter_json_array(io.StringIO(raw[: len(raw) // 2]), chunk_size=64))
    with pytest.raises(ValueError):
        list(u.iter_json_array(io.StringIO('{"not": "an array"}')))


def test_empty_array():
    assert list(u.iter_json_array(io.StringIO(" [ ] "))) == []


# ───────────────────────────────────────────── selection

def test_only_swiss_paragraphs(stubs):
    # The Cuba record (special rapporteur on unilateral coercive measures) is out.
    assert not any("A/HRC/63/29" in s["doc_number"] for s in stubs)
    assert {s["language"] for s in stubs} == {"en", "fr"}


def test_untranslated_french_copy_is_dropped(stubs):
    # The French export repeats the English text of CAT/C/CHE/CO/6 verbatim.
    assert [s["language"] for s in stubs if s["doc_number"].startswith("CAT/C/CHE/CO/6")] == ["en"]


def test_french_text_in_the_english_export_is_dropped(stubs):
    # Two SPT footnotes sit in the English export in French.
    assert not any(s["language"] == "en" and "détenus" in s["body_text"] for s in stubs)
    assert not any(s["language"] == "en" and "Agents polyvalents" in s["body_text"] for s in stubs)


# ───────────────────────────────────────────── one row per paragraph

def test_concluding_observation_paragraph(stubs):
    s = _by(stubs, "en", "CCPR/C/CHE/CO/4, para. 6")
    assert s["issuing_authority"] == "UN-CCPR"
    assert s["doc_type"] == "concluding_observations"
    assert s["date"] == "2017-08-22"
    assert s["title"] == ("Human Rights Committee: Concluding observations on Switzerland "
                          "(CCPR/C/CHE/CO/4), para. 6 — Constitutional and legal framework "
                          "within which the Covenant is implemented")
    # Heading on its own line; the number regains the tab the source dropped.
    assert s["body_text"].startswith(
        "Constitutional and legal framework within which the Covenant is implemented\n"
        "6. The Committee welcomes the information that the provisions of the Covenant "
        "have been invoked in more than 300 decisions of the Federal Supreme Court.")
    assert "<" not in s["body_text"]
    assert s["url"] == s["pdf_url"] == "https://docs.un.org/en/CCPR/C/CHE/CO/4"
    assert "Concerns/Observations" in s["topics"]


def test_french_row_shares_the_annotation(stubs):
    en = _by(stubs, "en", "CCPR/C/CHE/CO/4, para. 6")
    fr = _by(stubs, "fr", "CCPR/C/CHE/CO/4, par. 6")
    assert fr["annotation_id"] == en["annotation_id"]
    assert fr["title"].startswith("Comité des droits de l'homme : Observations finales "
                                  "concernant la Suisse (CCPR/C/CHE/CO/4), par. 6")
    assert "6. Le Comité accueille avec satisfaction" in fr["body_text"]
    assert fr["url"] == "https://docs.un.org/fr/CCPR/C/CHE/CO/4"


def test_consecutive_paragraphs_are_a_range(stubs):
    s = _by(stubs, "en", "CCPR/C/CHE/CO/4, paras. 52–54")
    assert s["title"].endswith("— D. Dissemination and follow-up")


def test_upr_recommendation_carries_position_and_state(stubs):
    s = _by(stubs, "en", "A/HRC/53/12, para. 39.1")
    assert s["issuing_authority"] == "UN-UPR"
    assert s["doc_type"] == "upr_recommendation"
    assert s["title"] == "Universal Periodic Review of Switzerland, cycle 4: recommendation 39.1 (Ghana), noted"
    assert {"UPR position: Noted", "Recommending State: Ghana", "Cycle 4"} <= set(s["topics"])
    fr = _by(stubs, "fr", "A/HRC/53/12, par. 39.1")
    # "39.1<span>Redoubler" in the French export: the number is still found.
    assert fr["body_text"].startswith("39.1 Redoubler")
    assert fr["title"].endswith("recommandation 39.1 (Ghana), notée")


def test_upr_number_with_trailing_dot(stubs):
    s = _by(stubs, "en", "A/HRC/22/11, para. 123.65")
    assert s["title"].endswith("recommendation 123.65 (Namibia), supported")


def test_uhri_digest_is_never_passed_off_as_un_text(stubs):
    s = _by(stubs, "en", "CAT/C/CHE/CO/6")       # no paragraph number on a digest
    assert s["body_text"].startswith("w) the ratification")
    assert s["title"].endswith("[UHRI summary, not the verbatim UN text]")
    assert "UHRI summary (not verbatim)" in s["topics"]


def test_special_procedure_and_unnumbered_paragraph(stubs):
    s = _by(stubs, "en", "A/HRC/45/9/Add.1, para. 16")     # ADD.1 -> Add.1
    assert s["issuing_authority"] == "UN-SPECIAL PROCEDURES"
    assert s["title"].startswith("Special procedures (WG Mercenaries): Report on the country visit")
    crc = _by(stubs, "en", "CRC/C/CHE/CO/2-4")
    assert crc["title"].endswith("— Legislation")


# ───────────────────────────────────────────── run() → JSONL → practice.db

def test_run_writes_rows_and_skips_them_next_time(tmp_path, monkeypatch):
    from search_stack.build_practice_db import build

    monkeypatch.setattr(u.UnUhriScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(u, "MIN_EXPORT_RECORDS", 0)
    monkeypatch.setattr(u.UnUhriScraper, "_open_export",
                        lambda self, lang: open(FIX / f"uhri_{lang}.json", encoding="utf-8"))
    first = u.UnUhriScraper().run()
    assert first["new"] == 10 and first["failed"] == 0
    second = u.UnUhriScraper().run()
    assert second["new"] == 0 and second["skipped"] == 10

    rows = [json.loads(line) for line in (tmp_path / "practice" / "un_uhri.jsonl").read_text().splitlines()]
    assert all(r["source"] == "un_uhri" and r["issuing_authority"].startswith("UN-") for r in rows)
    assert len({r["doc_id"] for r in rows}) == 10

    summary = build(tmp_path / "practice", tmp_path / "practice.db")
    assert summary["by_source"]["un_uhri"]["doc_count"] == 10
    conn = sqlite3.connect(tmp_path / "practice.db")
    assert conn.execute(
        "SELECT COUNT(*) FROM practice_fts WHERE practice_fts MATCH 'Covenant'").fetchone()[0] >= 2


def test_empty_selection_is_an_error_not_a_quiet_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(u.UnUhriScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(u, "MIN_EXPORT_RECORDS", 0)
    monkeypatch.setattr(u.UnUhriScraper, "_open_export", lambda self, lang: io.StringIO("[]"))
    with pytest.raises(RuntimeError, match="no Switzerland paragraphs"):
        u.UnUhriScraper().run()


def test_short_export_is_an_error(tmp_path, monkeypatch):
    # An export with far fewer records than the real one (error stub,
    # truncated mirror) must fail the run, not silently shrink the scope.
    monkeypatch.setattr(u.UnUhriScraper, "OUTPUT_DIR", tmp_path / "practice")
    monkeypatch.setattr(u.UnUhriScraper, "_open_export",
                        lambda self, lang: open(FIX / f"uhri_{lang}.json", encoding="utf-8"))
    with pytest.raises(RuntimeError, match="not the full dataset"):
        u.UnUhriScraper().run()


# ───────────────────────────────────────────── search_practice

def test_search_does_not_collapse_paragraphs_of_one_document(tmp_path, monkeypatch):
    """Dozens of unnumbered paragraphs share doc_number 'CRC/C/CHE/CO/2-4';
    the version collapse must not fold them into one result."""
    import mcp_server
    from search_stack.build_practice_db import build

    out = tmp_path / "practice"
    out.mkdir()
    rows = []
    for i in range(3):
        rows.append({
            "doc_id": f"un_uhri_a{i}_en", "source": "un_uhri", "issuing_authority": "UN-CRC",
            "doc_type": "concluding_observations", "doc_number": "CRC/C/CHE/CO/2-4",
            "title": f"para {i}", "date": "2015-02-25", "language": "en",
            "url": "https://docs.un.org/en/CRC/C/CHE/CO/2-4", "pdf_url": "",
            "body_text": f"The Committee recommends corporal punishment item {i}",
            "topics": [], "scraped_at": "2026-10-23T00:00:00+00:00", "content_hash": str(i),
        })
    (out / "un_uhri.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    build(out, tmp_path / "practice.db")

    def _conn():
        c = sqlite3.connect(f"file:{tmp_path / 'practice.db'}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(mcp_server, "_open_practice_db", _conn)
    res = mcp_server._search_practice(query="corporal punishment", source="un_uhri",
                                      issuing_authority="UN-CRC")
    assert res["total"] == 3 and res["distinct_documents"] == 3 and len(res["results"]) == 3
