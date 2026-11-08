"""scripts/ch_bundesrat_rekey.py (offline, synthetic rows in the shape of the
three id generations measured on the production shards 2026-11-08)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import ch_bundesrat_rekey as rk  # noqa: E402

OLD_DAM = "https://www.bj.admin.ch/dam/bj/de/data/publiservice/publikationen/beschwerdeentscheide/"

TABLE = {
    "2025-10-08-beschwerde-kanton-aargau": {
        "pdf_stem": "2025-10-08-beschwerde-kanton-aargau", "docket_number": "2025-10-08-kanton-aargau",
        "decision_type": "Beschwerdeentscheid", "listed_date": "2025-10-08", "publication_date": "2025-10-08",
        "detail_url": "https://www.bj.admin.ch/de/entscheid-des-bundesrates-vom-8-oktober-2025-kanton-aargau",
        "pdf_url": "https://www.bj.admin.ch/dam/de/sd-web/IG1DrxEbiAI5/2025-10-08-beschwerde-kanton-aargau.pdf",
        "keywords": "Nichteintreten, Notariatsaufsicht",
        "title": "Entscheid des Bundesrates in der Beschwerdesache A. und B. gegen Kanton Aargau"},
    "2024-09-20-beschwerde-dpsa": {
        "pdf_stem": "2024-09-20-beschwerde-dpsa", "docket_number": "2024-09-20-dpsa",
        "decision_type": "Schreiben", "listed_date": "2024-09-20", "publication_date": "2024-09-20",
        "detail_url": "https://www.bj.admin.ch/de/schreiben-des-ejpd-vom-20-september-2024-dpsa",
        "pdf_url": "https://www.bj.admin.ch/dam/de/sd-web/wBVacvaLDFPM/2024-09-20-beschwerde-dpsa.pdf",
        "keywords": "DPSA, VBS", "title": "Schreiben des EJPD an den Chef der Armee"},
}

AARGAU = ("betreffend Regierungsratsbeschluss Nr. 2025-000485 vom 7. Mai 2025 ... "
          "3003 Bern,\nIM AUFTRAG DES SCHWEIZERISCHEN BUNDESRATES") * 5
DPSA = ("Aktenzeichen: 361-3769/1/5\nBern, 20. September 2024\nBeschwerdesache A. gegen DPSA "
        "betreffend Verfügung des DPSA vom 15. Januar 2024") * 5


def _row(did, docket, date, pdf, text, **kw):
    r = {"decision_id": did, "court": "ch_bundesrat", "canton": "CH", "docket_number": docket,
         "decision_date": date, "pdf_url": pdf, "full_text": text, "title": "Mehr über «x»",
         "language": "de", "source_url": "https://www.bj.admin.ch/old"}
    r.update(kw)
    return r


def _shards():
    direct = [
        # old scheme, wrong date (the contested ruling's)
        _row("ch_bundesrat_2025-10-08", "2025-10-08", "2025-05-07",
             OLD_DAM + "2025-10-08-beschwerde-kanton-aargau.pdf.download.pdf/2025-10-08-beschwerde-kanton-aargau-d.pdf",
             AARGAU),
        # old scheme only (an EJPD letter), wrong date
        _row("ch_bundesrat_2024-09-20", "2024-09-20", "2024-01-15",
             OLD_DAM + "2024-09-20-beschwerde-dpsa.pdf.download.pdf/2024-09-20-beschwerde-dpsa-d.pdf", DPSA),
        # flat-slug scheme
        _row("ch_bundesrat_2025-10-08-kanton-aargau", "2025-10-08-kanton-aargau", "2025-10-08",
             "https://www.bj.admin.ch/dam/x/2025-10-08-beschwerde-kanton-aargau.pdf", AARGAU),
        # a row the table does not know
        _row("ch_bundesrat_2030-01-01-x", "2030-01-01-x", "2030-01-01",
             "https://www.bj.admin.ch/dam/x/2030-01-01-beschwerde-x.pdf", "text " * 50),
    ]
    es = [
        _row("ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2025-10-08.html",
             "/bj/de/home/publiservice/publikationen/beschwerdeentscheide/2025-10-08.html", "2025-05-07",
             OLD_DAM + "2025-10-08-beschwerde-kanton-aargau.pdf.download.pdf/2025-10-08-beschwerde-kanton-aargau-d.pdf",
             AARGAU + " ", regeste="es abstract"),
        _row("ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2024-09-20.html",
             "/bj/de/home/publiservice/publikationen/beschwerdeentscheide/2024-09-20.html", "2024-01-15",
             OLD_DAM + "2024-09-20-beschwerde-dpsa.pdf.download.pdf/2024-09-20-beschwerde-dpsa-d.pdf", DPSA),
    ]
    return direct, es


def test_one_row_per_decision_with_alias_and_listing_metadata():
    direct, es = _shards()
    out = rk.plan(direct, es, TABLE)
    by = {r["decision_id"]: r for r in out["direct"]}
    assert set(by) == {"ch_bundesrat_2025-10-08-kanton-aargau", "ch_bundesrat_2024-09-20-dpsa",
                       "ch_bundesrat_2030-01-01-x"}
    aargau = by["ch_bundesrat_2025-10-08-kanton-aargau"]
    assert aargau["previous_decision_id"] == "ch_bundesrat_2025-10-08"   # old-scheme id first
    assert aargau["decision_date"] == "2025-10-08"                      # listed (no date line)
    assert aargau["title"].startswith("Entscheid des Bundesrates")
    assert aargau["source_url"].endswith("kanton-aargau") and aargau["legal_area"].startswith("Nicht")
    dpsa = by["ch_bundesrat_2024-09-20-dpsa"]                            # re-keyed old-scheme row
    assert dpsa["docket_number"] == "2024-09-20-dpsa"
    assert dpsa["previous_decision_id"] == "ch_bundesrat_2024-09-20"
    assert dpsa["decision_date"] == "2024-09-20"                        # from "Bern, 20. September 2024"
    assert dpsa["decision_type"] == "Schreiben"
    assert by["ch_bundesrat_2030-01-01-x"] is direct[3]                  # unmapped: untouched
    assert out["stats"]["unmapped_direct"] == 1


def test_es_rows_are_rekeyed_onto_the_canonical_id():
    direct, es = _shards()
    out = rk.plan(direct, es, TABLE)
    e = {r["previous_decision_id"]: r for r in out["es"]}
    a = e["ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2025-10-08.html"]
    assert a["decision_id"] == "ch_bundesrat_2025-10-08-kanton-aargau"
    assert a["docket_number"] == "2025-10-08-kanton-aargau" and a["decision_date"] == "2025-10-08"
    assert a["regeste"] == "es abstract" and a["full_text"].endswith(" ")   # content kept
    # the build serves each decision once
    assert rk.served_ids(out["direct"], out["es"]) == {r["decision_id"] for r in out["direct"]}
    assert len(rk.served_ids(direct, es)) == 6


def test_alias_accounting():
    direct, es = _shards()
    out = rk.plan(direct, es, TABLE)
    al = out["aliases"]
    assert al["ch_bundesrat_2025-10-08"] == ("ch_bundesrat_2025-10-08-kanton-aargau", "previous_decision_id")
    assert al["ch_bundesrat_2024-09-20"][1] == "previous_decision_id"
    # these need the build to record the alias of a skipped duplicate
    assert {k for k, v in al.items() if v[1] == "es_skipped_duplicate"} == {
        "ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2025-10-08.html",
        "ch_bundesrat__bj_de_home_publiservice_publikationen_beschwerdeentscheide_2024-09-20.html"}
    assert out["stats"]["aliases_lost"] == 0


def test_flat_slug_and_es_only_pair_uses_the_es_id():
    direct, es = _shards()
    direct = [direct[2]]          # no old-scheme row for Aargau
    out = rk.plan(direct, es[:1], TABLE)
    assert out["direct"][0]["previous_decision_id"].startswith("ch_bundesrat__bj_de_home")


def test_idempotent(tmp_path):
    direct, es = _shards()
    d, e = tmp_path / "ch_bundesrat.jsonl", tmp_path / "es_ch_bundesrat.jsonl"
    t = tmp_path / "t.tsv"
    cols = list(next(iter(TABLE.values())))
    t.write_text("\t".join(cols) + "\n" + "".join("\t".join(r[c] for c in cols) + "\n" for r in TABLE.values()))
    d.write_text("".join(json.dumps(r) + "\n" for r in direct))
    e.write_text("".join(json.dumps(r) + "\n" for r in es))
    assert rk.main(["--direct", str(d), "--es", str(e), "--table", str(t)]) == 0      # dry run
    assert len(d.read_text().splitlines()) == 4
    assert rk.main(["--direct", str(d), "--es", str(e), "--table", str(t), "--apply"]) == 0
    first_d, first_e = d.read_text(), e.read_text()
    assert len(first_d.splitlines()) == 3
    assert list(tmp_path.glob("ch_bundesrat.jsonl.bak-bundesrat-rekey-*"))
    assert rk.main(["--direct", str(d), "--es", str(e), "--table", str(t), "--apply"]) == 0
    assert d.read_text() == first_d and e.read_text() == first_e      # second run: no-op
    es_rows = [json.loads(x) for x in first_e.splitlines()]
    assert all(r["previous_decision_id"].startswith("ch_bundesrat__bj") for r in es_rows)


def test_refuses_duplicate_table_dockets(tmp_path):
    t = tmp_path / "t.tsv"
    t.write_text("pdf_stem\tdocket_number\na\tx\nb\tx\n")
    with pytest.raises(SystemExit):
        rk.load_table(t)


def test_committed_table_loads():
    table = rk.load_table(rk.DEFAULT_TABLE)
    assert len(table) == 19
    assert table["2024-07-09-entscheid-br-europarat"]["docket_number"] == "2025-02-12-eda"
