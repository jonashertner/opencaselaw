"""A BGer judgment names the BGE that publishes it (LinkedIn, 2026-10-16).

get_decision("4A_149/2025") served the judgment of 12 January 2026 without a
word about BGE 152 III 240, which publishes it; the BGE row named its judgment,
the judgment never named its BGE, so a model told the user "no publication
noted" and cited the BGer docket where a brief cites the BGE."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

KEY = "ECLI:CH:BGER:2026:4A_149.2025"
BGE_TEXT = ("Urteilskopf 152 III 240 22. Extrait de l'arrêt de la Ire Cour de droit civil "
            "dans la cause A. contre Banque B. SA (recours en matière civile) 4A_149/2025 "
            "du 12 janvier 2026 Regeste Art. 400 al. 1 CO")


def _db(path, extra=()):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE decisions(decision_id TEXT PRIMARY KEY, court TEXT, canton TEXT, "
              "docket_number TEXT, docket_number_2 TEXT, decision_date TEXT, publication_date TEXT, "
              "language TEXT, full_text TEXT, json_data TEXT)")
    rows = [("bger_4A_149_2025", "bger", "CH", "4A_149/2025", None, "2026-01-12", None, "fr", "arrêt", None),
            ("bge_152 III 240", "bge", "CH", "152 III 240", "4A_149/2025", "2026-01-12", None, "fr",
             BGE_TEXT, None),
            ("bger_5A_1_2024", "bger", "CH", "5A_1/2024", None, "2024-05-06", None, "de", "y", None),
            *extra]
    c.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()
    return path


def _sidecar(path, rows=None):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE canonical_identity(decision_id TEXT PRIMARY KEY, decision_date TEXT, "
              "decision_date_provenance TEXT, publication_date TEXT, publication_date_provenance TEXT, "
              "ecli TEXT, canonical_key TEXT)")
    rows = rows or [("bger_4A_149_2025", None, None, None, None, KEY, KEY),
                    ("bge_152 III 240", None, None, None, None, KEY, KEY),
                    ("bge_BGE_152_III_240", None, None, None, None, KEY, KEY)]   # stale id, not in the DB
    c.executemany("INSERT INTO canonical_identity VALUES(?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()
    return path


def _rconn(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row
    return c


def _wire(monkeypatch, tmp_path, extra=(), sidecar_rows=None, sidecar=True):
    dbp = _db(tmp_path / "d.db", extra)
    scp = _sidecar(tmp_path / "ci.db", sidecar_rows) if sidecar else tmp_path / "missing.db"
    monkeypatch.setattr(m, "get_db", lambda: _rconn(dbp))
    monkeypatch.setattr(m, "CANONICAL_DB_PATH", Path(scp))
    monkeypatch.setattr(m, "_canonical_warned", False)
    monkeypatch.setattr(m, "_bge_by_key_sig", None)
    monkeypatch.setattr(m, "_bge_by_key", {})


def test_judgment_names_its_bge(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m.get_decision_by_id("4A_149/2025")
    assert [b["decision_id"] for b in out["published_as_bge"]] == ["bge_152 III 240"]
    b = out["published_as_bge"][0]
    assert (b["citation_string_de"], b["citation_string_fr"], b["citation_string_it"]) == \
        ("BGE 152 III 240", "ATF 152 III 240", "DTF 152 III 240")
    assert "BGE 152 III 240" in out["published_as_bge_note"]


def test_cite_of_the_docket_carries_the_bge(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m._handle_cite(reference="4A_149/2025")
    assert out["citation_string"].startswith("BGer 4A_149/2025")      # the docket's own citation, unchanged
    assert out["published_as_bge"][0]["citation_string_de"] == "BGE 152 III 240"


def test_bge_names_its_full_decision(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    out = m.get_decision_by_id("bge_152 III 240")
    assert out["full_decision"]["decision_id"] == "bger_4A_149_2025"
    assert out["full_decision"]["citation_string_de"].startswith("BGer 4A_149/2025")
    assert "published_as_bge" not in out


def test_interim_order_under_the_same_docket_is_not_linked(tmp_path, monkeypatch):
    # Another ruling under the docket in the same year (an order of 3 March 2026)
    # shares the canonical key; only the judgment of the BGE's date is published.
    order = ("bger_4A_149_2025_order", "bger", "CH", "4A_149/2025", None, "2026-03-03", None, "fr", "ordonnance", None)
    _wire(monkeypatch, tmp_path, extra=[order])
    out = m.get_decision_by_id("bger_4A_149_2025_order")
    assert "published_as_bge" not in out
    full = m.get_decision_by_id("bge_152 III 240")["full_decision"]
    assert full["decision_id"] == "bger_4A_149_2025"


def test_key_derived_when_the_judgment_is_newer_than_the_sidecar(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, sidecar_rows=[("bge_152 III 240", None, None, None, None, KEY, KEY)])
    out = m.get_decision_by_id("bger_4A_149_2025")
    assert out["published_as_bge"][0]["decision_id"] == "bge_152 III 240"


def test_unpublished_judgment_and_missing_sidecar_add_nothing(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path)
    assert "published_as_bge" not in m.get_decision_by_id("bger_5A_1_2024")
    (tmp_path / "x").mkdir()
    _wire(monkeypatch, tmp_path / "x", sidecar=False)
    assert "published_as_bge" not in m.get_decision_by_id("bger_4A_149_2025")


def test_sidecar_ids_from_before_the_one_id_change_resolve(tmp_path, monkeypatch):
    # The sidecar of 2026-10-13 still names BGEs "bge_BGE_152_III_240"; the
    # live row is "bge_152 III 240" (1,134 of 1,584 BGEs of 2020-2026).
    _wire(monkeypatch, tmp_path, sidecar_rows=[("bge_BGE_152_III_240", None, None, None, None, KEY, KEY)])
    out = m.get_decision_by_id("bger_4A_149_2025")
    assert [b["decision_id"] for b in out["published_as_bge"]] == ["bge_152 III 240"]


def test_space_and_dot_dockets_link_both_ways(tmp_path, monkeypatch):
    # bger stores "9C 292/2007" (and "4C.215/2005"); the BGE caption says "9C_292/2007".
    k = "ECLI:CH:BGER:2007:9C_292.2007"
    bger = ("bger_9C_292_2007", "bger", "CH", "9C 292/2007", None, "2007-10-29", None, "de", "Urteil", None)
    bge = ("bge_133 V 640", "bge", "CH", "133 V 640", "9C_292/2007", "2007-10-29", None, "de",
           "Urteilskopf 133 V 640 Auszug aus dem Urteil der II. sozialrechtlichen Abteilung i.S. A. "
           "gegen B. 9C_292/2007 vom 29. Oktober 2007 Regeste", None)
    _wire(monkeypatch, tmp_path, extra=[bger, bge],
          sidecar_rows=[("bger_9C_292_2007", None, None, None, None, k, k), ("bge_133 V 640", None, None, None, None, k, k)])
    assert m.get_decision_by_id("9C 292/2007")["published_as_bge"][0]["citation_string_de"] == "BGE 133 V 640"
    assert m.get_decision_by_id("bge_133 V 640")["full_decision"]["decision_id"] == "bger_9C_292_2007"


def test_consolidated_proceeding_keyed_on_the_joined_docket(tmp_path, monkeypatch):
    # BGE 151 I 3 = "2C_36/2023 / 2C_38/2023": keyed 2C_38, judgment stored as
    # 2C_36 with 2C_38 as its joined docket.
    k38 = "ECLI:CH:BGER:2024:2C_38.2023"
    bger = ("bger_2C_36_2023", "bger", "CH", "2C_36/2023", None, "2024-06-05", None, "fr", "arrêt", None)
    bge = ("bge_151 I 3", "bge", "CH", "151 I 3", "2C_36/2023", "2024-06-05", None, "fr", "x", None)
    _wire(monkeypatch, tmp_path, extra=[bger, bge], sidecar_rows=[("bge_151 I 3", None, None, None, None, k38, k38)])
    out = m._published_as_bge({**m.get_decision_by_id("bger_2C_36_2023"), "joined_dockets": ["2C_38/2023"]})
    assert [b["decision_id"] for b in out] == ["bge_151 I 3"]
    assert m._published_as_bge(m.get_decision_by_id("bger_2C_36_2023")) == []   # without the alias: no key


def test_bger_key_format():
    assert m._bger_key("9C 292/2007", "2007-10-29") == "ECLI:CH:BGER:2007:9C_292.2007"
    assert m._bger_key("4C.215/2005", "2005-12-20") == "ECLI:CH:BGER:2005:4C_215.2005"
    assert m._bger_key("4A_149/2025", "2026-01-12") == KEY
    assert m._bger_key("", "2026-01-12") is None
    assert m._bger_key("4A_1/2025", None) is None
