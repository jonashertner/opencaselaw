"""TI rulings came out as heading + signature (report 2026-10-14): the extractor
kept only <p class="MsoNormal">, the Word exports file the body under their own
styles. Fixed extractor + the re-fetch of the rows already stored."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scrapers.cantonal.ti_gerichte import _extract_document_text  # noqa: E402
import refetch_ti_gerichte_text as rf  # noqa: E402

# The shape of a sentenze.ti.ch Word export (synthetic text): heading and
# signature in MsoNormal, the body in the custom styles R1/R2, words split
# across runs, a table in the dispositivo.
PAGE = """<html><body><div class=WordSection1>
<p class=MsoNormal>Incarto n.<span>52.2023.477</span></p>
<p class=MsoNormal>Lugano</p><p class=MsoNormal>5
 marzo 2026</p>
<p class=MsoNormal>In nome<span> </span>della Repubblica e Cantone<span>
</span>Ticino</p>
<p class=R1>in fatto</p>
<p class=R1>A. Con decisione del 15 novembre 2023 il Consiglio di Stato ha respinto
il ricorso.</p>
<p class=R2>in diritto</p>
<p class=R1>1. Il ricorso è tempestivo.</p>
<table class=MsoNormalTable><tr><td><p class=MsoNormal>Per questi motivi, dichiara:</p></td></tr></table>
<ul><li>Il ricorso è respinto.<p class=R1>nested</p></li></ul>
<p class=MsoNormal>Il vicepresidente Il segretario</p>
</div></body></html>"""


def test_body_under_custom_styles_is_kept():
    t = _extract_document_text(BeautifulSoup(PAGE, "html.parser"))
    assert "in fatto" in t and "in diritto" in t
    assert "Il ricorso è tempestivo." in t
    assert "Per questi motivi, dichiara:" in t
    assert t.startswith("Incarto n.52.2023.477")
    assert t.rstrip().endswith("Il vicepresidente Il segretario")


def test_words_split_across_runs_are_not_glued():
    t = _extract_document_text(BeautifulSoup(PAGE, "html.parser"))
    assert "In nome della Repubblica e Cantone Ticino" in t
    assert "5 marzo 2026" in t


def test_nested_blocks_are_not_doubled():
    t = _extract_document_text(BeautifulSoup(PAGE, "html.parser"))
    assert t.count("nested") == 1


def _row(did, text, url="http://www.sentenze.ti.ch/doc?nF30_KEY=1"):
    return {"decision_id": did, "court": "ti_gerichte", "full_text": text, "source_url": url,
            "decision_date": "2026-03-05"}


TRUNC = "Incarto n.52.2023.477 Lugano 5 marzo 2026 Il Tribunale cantonale amministrativo Il segretario"
FULL = "Incarto n.1 Lugano in fatto A. Fatti. in diritto 1. Diritto. Per questi motivi"


def test_selection_takes_truncated_rows_only():
    assert rf.truncated(_row("a", TRUNC), 2000, False)
    assert not rf.truncated(_row("b", FULL), 2000, False)        # short, but has its body
    assert rf.truncated(_row("b", FULL), 2000, True)              # --all-short
    assert not rf.truncated(_row("c", TRUNC + " x" * 2000), 2000, False)


def test_fetch_is_resumable_and_never_writes_into_the_shard_dir(tmp_path):
    shard = tmp_path / "decisions" / "ti_gerichte.jsonl"
    shard.parent.mkdir()
    shard.write_text("\n".join(json.dumps(r) for r in (_row("a", TRUNC), _row("b", FULL))) + "\n")
    with pytest.raises(SystemExit):
        rf.run_fetch(shard, shard.parent / "ti.refetch.jsonl", lambda u: PAGE, None, 0, 2000, False)
    sidecar = tmp_path / "state" / "ti_gerichte.refetch.jsonl"
    stats = rf.run_fetch(shard, sidecar, lambda u: PAGE, None, 0, 2000, False)
    assert stats["fetched"] == 1 and stats["not_selected"] == 1 and stats["now_with_body"] == 1
    again = rf.run_fetch(shard, sidecar, lambda u: pytest.fail("no second request"), None, 0, 2000, False)
    assert again["already"] == 1


def test_apply_only_lengthens_keeps_dates_and_mode(tmp_path):
    shard = tmp_path / "ti_gerichte.jsonl"
    rows = [_row("a", TRUNC), _row("b", FULL * 50)]
    shard.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    shard.chmod(0o644)
    sidecar = tmp_path / "side.jsonl"
    sidecar.write_text("\n".join(json.dumps(d) for d in (
        {"decision_id": "a", "full_text": FULL * 3, "cited_decisions": ["2C_1/2024"], "fetched_at": "t"},
        {"decision_id": "b", "full_text": "short", "cited_decisions": [], "fetched_at": "t"})) + "\n")
    stats = rf.run_apply(shard, sidecar, dry_run=False)
    assert stats["updated"] == 1 and stats["kept"] == 1
    out = [json.loads(ln) for ln in shard.read_text().splitlines()]
    assert out[0]["full_text"] == FULL * 3 and out[0]["cited_decisions"] == ["2C_1/2024"]
    assert out[0]["decision_date"] == "2026-03-05"                 # never touched
    assert out[1]["full_text"] == FULL * 50                        # never shortened
    assert (shard.stat().st_mode & 0o777) == 0o644
    assert rf.run_apply(shard, sidecar, dry_run=False)["updated"] == 0
