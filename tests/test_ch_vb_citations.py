"""ch_vb is the VPB/JAAC/GAAC series: cited by series title and item number, never
'Gericht CH', and every written form of the number resolves to the stored 'JAAC <n>'
docket (scripts/ch_vb_vpb_dockets.py gives the 2007-2017 rows theirs)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server as m
from scripts import ch_vb_vpb_dockets as kd

JAAC_64 = {"decision_id": "ch_vb_JAAC_64.104__", "court": "ch_vb", "canton": "CH",
           "docket_number": "JAAC 64.104", "decision_date": "2000-05-17", "language": "fr"}


def test_volume_item_cites_by_series_title_per_language():
    c = m._build_citation_strings(JAAC_64)
    assert c["citation_string_de"] == "VPB 64.104 vom 17. Mai 2000"
    assert c["citation_string_fr"] == "JAAC 64.104 du 17 mai 2000"
    assert c["citation_string_it"] == "GAAC 64.104 del 17 maggio 2000"
    assert c["canonical_url"].endswith("/entscheid/ch_vb_JAAC_64.104__")


def test_year_item_with_sub_number_and_pinpoint():
    rec = {**JAAC_64, "decision_id": "ch_vb_150000158", "docket_number": "JAAC 2009.10a",
           "decision_date": "2009-09-02"}
    c = m._build_citation_strings(rec, pinpoint="3.2")
    assert c["citation_string_de"] == "VPB 2009.10a vom 2. September 2009, E. 3.2"
    assert c["citation_string_fr"] == "JAAC 2009.10a du 2 septembre 2009, consid. 3.2"


def test_portal_id_docket_is_labelled_by_the_administration_not_a_court():
    rec = {**JAAC_64, "decision_id": "ch_vb_150000191", "docket_number": "150000191",
           "decision_date": "2009-06-10"}
    c = m._build_citation_strings(rec)
    assert c["citation_string_de"] == "Bundesverwaltung 150000191 vom 10. Juni 2009"
    assert c["citation_string_fr"].startswith("Administration fédérale 150000191")
    assert not any("Gericht" in c[k] or "Tribunal" in c[k] for k in c if k.startswith("citation"))


@pytest.mark.parametrize("ref, docket", [
    ("VPB 64.104", "JAAC 64.104"),
    ("GAAC 64.104", "JAAC 64.104"),
    ("jaac 64.104", "JAAC 64.104"),
    ("VPB 64.104 vom 17. Mai 2000, E. 3", "JAAC 64.104"),
    ("Gericht CH JAAC 64.104 vom 17. Mai 2000", "JAAC 64.104"),   # strings handed out before the fix
    ("VPB 64.104.", "JAAC 64.104"),                               # end of a sentence
    ("VPB 2007.3.1", "JAAC 2007.3.1"),
    ("JAAC 2009.10a (p. 178)", "JAAC 2009.10a"),
    ("VPB 64.1045", None),
    ("VPB 64", None),
    ("BGE 64 I 104", None),
    ("siehe VPB 64.104", None),        # a later mention is a cross-reference
])
def test_vpb_docket_reads_every_title_and_the_server_strings(ref, docket):
    assert m._vpb_docket(ref) == docket


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "decisions.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, docket_number TEXT, court TEXT, "
              "canton TEXT, decision_date TEXT)")
    c.execute("CREATE INDEX idx_decisions_docket ON decisions(docket_number)")
    c.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", [
        ("ch_vb_JAAC_64.104__", "JAAC 64.104", "ch_vb", "CH", "2000-05-17"),
        ("ch_vb_150000044", "JAAC 2007.3.1", "ch_vb", "CH", "2006-12-22"),
    ])
    c.commit()
    c.close()

    def get_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(m, "get_db", get_db)


@pytest.mark.parametrize("ref, did", [
    ("VPB 64.104", "ch_vb_JAAC_64.104__"),
    ("GAAC 64.104 del 17 maggio 2000", "ch_vb_JAAC_64.104__"),
    ("Gericht CH JAAC 64.104 vom 17. Mai 2000", "ch_vb_JAAC_64.104__"),
    ("VPB 2007.3.1 vom 22. Dezember 2006", "ch_vb_150000044"),
])
def test_written_forms_resolve_lenient_and_strict(db, ref, did):
    assert m._resolve_decision_id(ref) == did
    assert m._resolve_decision_id_strict(ref) == did


def test_cite_identity_accepts_the_series_titles():
    assert m._cite_identity("VPB 64.104 vom 17. Mai 2000", dict(JAAC_64)) == {
        "method": "exact_docket", "label": "JAAC 64.104"}
    assert m._cite_identity("VPB 64.105", dict(JAAC_64)) is None


# --- scripts/ch_vb_vpb_dockets.py ---------------------------------------------------

def _row(did, docket, text, lang="de"):
    return {"decision_id": did, "court": "ch_vb", "docket_number": docket, "language": lang,
            "full_text": text}


DE = "Die Behörde ist der Ansicht, dass die Verfügung nicht mit dem Gesetz vereinbar ist und der Bund " * 20
FR = "Le département est d'avis que la décision est conforme pour une part et que le recours des " * 20
MIXED = DE[:400] + FR[:400]


def test_item_number_is_read_from_the_header_only():
    assert kd.item_number("Gutachten VPB/JAAC/GAAC/PAAF 2007 \n 199 \n2007.10 (S. 199–212) Titel") == "2007.10"
    assert kd.item_number("Avis de droit 2007 98 2007.3.2 (p. 98–121)") == "2007.3.2"
    assert kd.item_number("VPB 3/2009 2009.10a (S. 141-177)") == "2009.10a"
    assert kd.item_number("Ergänzung zu Beitrag 2009.6 Anhang") is None
    assert kd.item_number("x" * kd.HEADER_CHARS + " 2008.4 (S. 12)") is None


def test_plan_sets_unique_numbers_skips_shared_ones_and_relabels_clear_languages():
    recs = [
        _row("ch_vb_JAAC_64.104__", "JAAC 64.104", FR),                       # language only
        _row("ch_vb_150000002", "150000002", "2007.10 (S. 199) " + DE),        # docket only
        _row("ch_vb_150000176", "150000176", "2009.1 (S. 1) " + DE),           # same number twice
        _row("ch_vb_150000179", "150000179", "2009.1 (S. 12) " + FR),
        _row("ch_vb_150000191", "150000191", "Ergänzung zu Beitrag 2009.6 " + DE),
        _row("ch_vb_150000300", "150000300", "64.104 (S. 1) " + MIXED),        # neither applies
    ]
    changes, collisions, unnumbered = kd.plan(recs)
    assert changes == {0: {"language": "fr"}, 1: {"docket_number": "JAAC 2007.10"},
                       3: {"language": "fr"}}
    assert collisions == [("ch_vb_150000176", "JAAC 2009.1"), ("ch_vb_150000179", "JAAC 2009.1")]
    assert unnumbered == ["ch_vb_150000191", "ch_vb_150000300"]


def test_a_number_already_held_by_a_series_row_is_never_reused():
    recs = [_row("ch_vb_JAAC_2008.4__", "JAAC 2008.4", DE),
            _row("ch_vb_150000140", "150000140", "2008.4 (S. 30) " + DE)]
    changes, collisions, _ = kd.plan(recs)
    assert changes == {} and collisions == [("ch_vb_150000140", "JAAC 2008.4")]


def test_apply_backs_up_and_keeps_untouched_lines_byte_identical(tmp_path):
    rows = [_row("ch_vb_150000002", "150000002", "2007.10 (S. 199) " + DE),
            # U+2028 inside a string must not split the record
            _row("ch_vb_JAAC_51.1__", "JAAC 51.1", "a b " + DE)]
    lines = [json.dumps(r, ensure_ascii=False) + "\n" for r in rows]
    shard = tmp_path / "es_ch_vb.jsonl"
    shard.write_text("".join(lines), encoding="utf-8")
    assert kd.main(["--shard", str(shard)]) == 0
    assert shard.read_text(encoding="utf-8") == "".join(lines)          # dry run
    assert kd.main(["--shard", str(shard), "--apply"]) == 0
    with open(shard, encoding="utf-8", newline="") as f:
        out = list(f)
    assert len(out) == 2 and out[1] == lines[1]
    assert json.loads(out[0]) == {**rows[0], "docket_number": "JAAC 2007.10"}
    assert (tmp_path / ("es_ch_vb.jsonl" + kd.BACKUP_SUFFIX)).read_text(encoding="utf-8") == "".join(lines)
    assert kd.main(["--shard", str(shard), "--apply"]) == 0              # second run: nothing to change
    with open(shard, encoding="utf-8", newline="") as f:
        assert list(f) == out
