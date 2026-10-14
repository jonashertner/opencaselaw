"""scripts/compose_bge_trilingual_regeste.py — direct BGE rows get the Regeste in
all three languages from the abstracts they already hold (2026-10-14)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import compose_bge_trilingual_regeste as c  # noqa: E402

DIRECT = {"decision_id": "bge_151 III 227", "court": "bge", "regeste": "Regeste DE",
          "abstract_de": "Regeste DE", "abstract_fr": "Regeste FR", "abstract_it": "Regesto IT"}
ES = {"decision_id": "bge_BGE_151_III_227", "court": "bge", "source": "entscheidsuche",
      "regeste": "DE FR IT", "abstract_de": None, "abstract_fr": None, "abstract_it": None}
ONE_LANG = {"decision_id": "bge_150 I 1", "court": "bge", "regeste": "Nur DE",
            "abstract_de": "Nur DE", "abstract_fr": None, "abstract_it": None}


def test_direct_row_is_composed_in_de_fr_it_order():
    assert c.decide(DIRECT) == ("composed", "Regeste DE\n\nRegeste FR\n\nRegesto IT")


def test_es_rows_and_single_language_rows_are_left_alone():
    assert c.decide(ES)[0] == "not_direct_bge"
    assert c.decide(ONE_LANG)[0] == "already_trilingual"   # regeste == its only abstract
    longer = {**ONE_LANG, "regeste": "Nur DE, aber vollständiger als das Abstract"}
    assert c.decide(longer)[0] == "single_language_kept"


def test_single_language_fills_an_empty_regeste():
    only_it = {"decision_id": "bge_113 III 68", "court": "bge", "regeste": "",
               "abstract_de": None, "abstract_fr": None, "abstract_it": "Regesto IT"}
    assert c.decide(only_it) == ("composed", "Regesto IT")
    none = {**only_it, "abstract_it": None}
    assert c.decide(none)[0] == "no_abstracts"


def test_apply_stamps_keeps_mode_and_is_idempotent(tmp_path):
    p = tmp_path / "bge.jsonl"
    odd = {**DIRECT, "decision_id": "bge_151 III 1", "regeste": "something else"}
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (DIRECT, ES, odd)), encoding="utf-8")
    p.chmod(0o600)
    stats = c.run(p, apply=True, examples=0)
    assert stats["rows_changed"] == 2 and stats["not_direct_bge"] == 1
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["regeste"] == "Regeste DE\n\nRegeste FR\n\nRegesto IT"
    assert "from" not in rows[0]["regeste_restore"]          # old value == abstract_de
    assert rows[2]["regeste_restore"]["from"] == "something else"
    assert rows[1] == ES
    assert (p.stat().st_mode & 0o777) == 0o600
    assert c.run(p, apply=True, examples=0)["rows_changed"] == 0
    assert list(tmp_path.iterdir()) == [p]
