"""scripts/refetch_bs_gerichte_text.py — two-phase text repair for the BS rows.

fetch appends to a sidecar keyed on the decision number (resumable, no shard
writes); apply rewrites the shard once and only ever lengthens a row's text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import refetch_bs_gerichte_text as R  # noqa: E402

DOC = """<html><body><div class="WordSection1">
  <p class="MsoNormal">Sozialversicherungsgericht des Kantons Basel-Stadt</p>
  <p class="MsoNormal">IV.2017.108 URTEIL vom 28. November 2018</p>
  <p class="aaText">%s</p>
  <h2>Gemäss dem Gutachten ist die Arbeitsfähigkeit eingeschränkt.</h2>
  <p class="aaDispositiv">Die Beschwerde wird abgewiesen (BGer 9C_1/2019).</p>
</div></body></html>""" % ("Die Beschwerdeführerin macht geltend, dass " * 40)


def _row(court, d2, text, did=None, date=None):
    return {"decision_id": did or f"{court}_{d2}", "court": court, "docket_number": "IV.2017.108",
            "docket_number_2": d2, "decision_date": date, "full_text": text,
            "source_url": f"https://rechtsprechung.gerichte.bs.ch/doc/{d2}", "cited_decisions": []}


def _shard(tmp_path, rows):
    p = tmp_path / "bs_gerichte.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_fetch_document_uses_the_fixed_extractor():
    doc = R.fetch_document("u", lambda url: DOC)
    assert len(doc["full_text"]) > 1500
    assert "Gemäss dem Gutachten" in doc["full_text"]          # <h2> body text
    assert doc["decision_date"] == "2018-11-28"
    assert any("9C_1/2019" in c for c in doc["cited_decisions"])
    assert R.fetch_document("u", lambda url: "<html>short</html>") is None


def test_fetch_is_resumable_and_polite(tmp_path):
    rows = [_row("bs_sozialversicherungsgericht", "SVG.2018.352", "short " * 10),
            _row("bs_appellationsgericht", "AG.2014.40", "x" * 50000),
            {"decision_id": "bs_gerichte_es", "court": "bs_gerichte", "docket_number_2": "AG.1.1", "full_text": "es"}]
    shard = _shard(tmp_path, rows)
    sidecar = tmp_path / "side.jsonl"
    calls = []

    def fetcher(url):
        calls.append(url)
        return DOC

    stats = R.run_fetch(shard, sidecar, fetcher, R.BS_DIRECT_COURTS, None, 0.0, None)
    assert stats["fetched"] == 2 and stats["candidates"] == 2      # the es row is not a candidate
    assert len(calls) == 2
    side = [json.loads(l) for l in sidecar.read_text().splitlines()]
    assert {s["docket_number_2"] for s in side} == {"SVG.2018.352", "AG.2014.40"}
    # second run: everything already in the sidecar, no request
    calls.clear()
    stats = R.run_fetch(shard, sidecar, fetcher, R.BS_DIRECT_COURTS, None, 0.0, None)
    assert stats["already"] == 2 and calls == []
    # --only-shorter-than skips rows that are long enough
    sidecar.unlink()
    stats = R.run_fetch(shard, sidecar, fetcher, R.BS_DIRECT_COURTS, None, 0.0, 10000)
    assert stats["fetched"] == 1 and stats["long_enough"] == 1


def test_fetch_survives_a_failing_page(tmp_path):
    shard = _shard(tmp_path, [_row("bs_appellationsgericht", "AG.2014.40", "x")])
    sidecar = tmp_path / "side.jsonl"

    def fetcher(url):
        raise ConnectionError("boom")

    stats = R.run_fetch(shard, sidecar, fetcher, R.BS_DIRECT_COURTS, None, 0.0, None)
    assert stats["failed"] == 1 and not sidecar.read_text()


def test_apply_only_lengthens_and_fills_missing_dates(tmp_path):
    long_text = "voll " * 2000
    rows = [_row("bs_sozialversicherungsgericht", "SVG.2018.352", "short"),          # gets the text
            _row("bs_appellationsgericht", "AG.2014.40", "y" * 50000),                 # keeps its longer text
            _row("bs_appellationsgericht", "AG.2020.102", "z", date="2020-01-20"),     # no sidecar entry
            # the same decision under its pre-migration id: keyed on the number, still applies
            _row("bs_sozialversicherungsgericht", "SVG.2019.152", "s", did="bs_sozialversicherungsgericht_UV.2017.30")]
    side = {
        "bs_sozialversicherungsgericht|SVG.2018.352": {"full_text": long_text, "decision_date": "2018-11-28",
                                                       "cited_decisions": ["BGer 9C_1/2019"], "fetched_at": "t"},
        "bs_appellationsgericht|AG.2014.40": {"full_text": "shorter", "decision_date": "2014-02-10",
                                              "cited_decisions": [], "fetched_at": "t"},
        "bs_sozialversicherungsgericht|SVG.2019.152": {"full_text": long_text, "decision_date": None,
                                                       "cited_decisions": [], "fetched_at": "t"},
    }
    stats = R.apply_rows(rows, side)
    assert stats["updated"] == 2 and stats["kept"] == 1 and stats["no_sidecar"] == 1
    assert stats["date_filled"] == 1
    assert rows[0]["full_text"] == long_text and rows[0]["decision_date"] == "2018-11-28"
    assert rows[0]["cited_decisions"] == ["BGer 9C_1/2019"] and rows[0]["text_refetched_at"] == "t"
    assert rows[1]["full_text"] == "y" * 50000
    assert rows[3]["full_text"] == long_text and rows[3]["decision_id"] == "bs_sozialversicherungsgericht_UV.2017.30"


def test_apply_cli_rewrites_atomically(tmp_path):
    shard = _shard(tmp_path, [_row("bs_sozialversicherungsgericht", "SVG.2018.352", "short")])
    sidecar = tmp_path / "side.jsonl"
    sidecar.write_text(json.dumps({"court": "bs_sozialversicherungsgericht", "docket_number_2": "SVG.2018.352",
                                   "full_text": "long " * 500, "decision_date": "2018-11-28",
                                   "cited_decisions": [], "fetched_at": "t"}) + "\n")
    stats = R.run_apply(shard, sidecar, dry_run=True)
    assert stats["updated"] == 1
    assert json.loads(shard.read_text().splitlines()[0])["full_text"] == "short"     # dry run: untouched
    stats = R.run_apply(shard, sidecar, dry_run=False)
    assert stats["updated"] == 1
    assert json.loads(shard.read_text().splitlines()[0])["full_text"].startswith("long ")
    assert not list(tmp_path.glob("*.tmp"))
    assert R.run_apply(shard, sidecar, dry_run=False)["updated"] == 0                # idempotent
