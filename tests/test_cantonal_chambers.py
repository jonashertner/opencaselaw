"""chamber_rules + scripts/backfill_cantonal_chambers.py (offline).

Fixtures in tests/fixtures/chambers/ are the first 1,800 characters of served
rows (HF mirror, 2026-11-08), as the shards hold them: direct-scraper text, or
entscheidsuche's Kopfzeile + regeste + text for es-ingested rows. index.json
records each row's id, docket, language and source URL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import chamber_rules as cr

FIX = Path(__file__).parent / "fixtures" / "chambers"
INDEX = json.loads((FIX / "index.json").read_text())
REPO = Path(__file__).resolve().parents[1]


def _derive(name: str) -> cr.Result:
    meta = INDEX[name]
    return cr.derive(meta["court"], docket=meta["docket_number"],
                     text=(FIX / f"{name}.txt").read_text(),
                     language=meta["language"], source_url=meta["source_url"])


@pytest.mark.parametrize("name, chamber, how", [
    # Fribourg: the line under "Arrêt du / Urteil vom", in the ruling's language
    ("fr_601_2025_200", "Ie Cour administrative", "header"),
    ("fr_de_zivilappellationshof", "I. Zivilappellationshof", "header"),
    # A 605 docket decided by the IIe Cour (before the 608 series opened in
    # 2013): the head prints the row's own docket, so the header wins.
    ("fr_605_header_iie", "IIe Cour des assurances sociales", "header"),
    # Bern VG
    ("be_vg_200_2019_675", "Sozialversicherungsrechtliche Abteilung", "header"),
    ("be_vg_fr_cour_langue_francaise", "Cour des affaires de langue française", "header"),
    # es stub: only entscheidsuche's Meta block ("EinzelrichterIn des
    # Verwaltungsgerichts") after the Kopfzeile; the 100 series of a German
    # ruling is the verwaltungsrechtliche Abteilung.
    ("be_vg_es_stub_100_2025_8", "Verwaltungsrechtliche Abteilung", "docket"),
    # Basel-Landschaft
    ("bl_720_2014_312", "Kantonsgericht, Abteilung Sozialversicherungsrecht", "header"),
    ("bl_es_stub_460_2012_163", "Kantonsgericht, Abteilung Strafrecht", "docket"),
    ("bl_enteignungsgericht_650", "Enteignungsgericht", "header"),
    # Vaud findinfo: entscheidsuche's Kopfzeile says "Cour d'appel civile",
    # the ruling's header "CHAMBRE DES RECOURS PENALE".
    ("vd_decision_2011_200", "Chambre des recours pénale", "header"),
    ("vd_ocr_split_assurances", "Cour des assurances sociales", "header"),
    # "LE JUGE DELEGUE DE LA CHAMBRE DES CURATELLES": the judge's chamber
    ("vd_juge_delegue_curatelles", "Chambre des curatelles", "header"),
    # Nidwalden
    ("nw_za_24_19", "Obergericht, Zivilabteilung", "header"),
    ("nw_sv_24_15", "Verwaltungsgericht, Sozialversicherungsabteilung", "header"),
    ("nw_verfassungsgericht_vg_17_1", "Obergericht, Verfassungsgericht", "header"),
    # Uri
    ("ur_og_z_25_19", "Obergericht, Zivilrechtliche Abteilung", "header"),
    ("ur_og_bi_23_26", "Obergericht, Strafprozessuale Beschwerdeinstanz", "header"),
    # Appenzell Ausserrhoden: the header follows the parties; OCR-split court name
    ("ar_o3v_19_40", "Obergericht, 3. Abteilung", "header"),
    ("ar_kg_k1z_13_21_ocr", "Kantonsgericht, 1. Abteilung", "header"),
    # Zürich Baurekursgericht
    ("zh_brg_brge_i_0095_2017", "1. Abteilung", "header"),
])
def test_fixture_chamber(name, chamber, how):
    r = _derive(name)
    assert (r.chamber, r.how) == (chamber, how)


@pytest.mark.parametrize("name", ["fr_conflict_106_2018_45", "be_vg_conflict_100_2023_306"])
def test_foreign_text_is_a_conflict_not_a_fill(name):
    """fr 106 2018 45 holds the text of 608 2018 5; be 100 2023 306 the text of
    200 23 618. The header describes another ruling: write nothing."""
    r = _derive(name)
    assert r.chamber is None and r.how == "conflict"
    assert r.header_key and r.docket_key and r.header_key != r.docket_key


def test_single_body_courts_have_no_rule():
    meta = INDEX["zh_kass_aa060070"]
    r = cr.derive(meta["court"], docket=meta["docket_number"],
                  text=(FIX / "zh_kass_aa060070.txt").read_text(), language="de")
    assert r.chamber is None
    assert set(cr.SINGLE_BODY).isdisjoint(cr.RULES)


def test_es_kopfzeile_and_meta_block_are_never_read():
    """entscheidsuche's chamber appears in its Kopfzeile and Meta block; with
    no court text and a docket outside every series nothing is derived."""
    text = ("Basel-Land Kantonsgericht Abteilung Strafrecht 04.12.2012 999 12 1\n"
            "Bâle-Campagne Kantonsgericht 04.12.2012 999 12 1\n"
            "Basilea Campagna Kantonsgericht 04.12.2012 999 12 1\n\nBetrug\n\n"
            "Basel-Land Kantonsgericht\nBâle-Campagne Kantonsgericht\n"
            "Basilea Campagna Kantonsgericht\nAbteilung Strafrecht")
    assert cr.own_text(text).strip() == "Betrug"
    assert cr.derive("bl_gerichte", docket="999 12 1", text=text, language="de").chamber is None
    vd = ("Vaud Tribunal cantonal Cour d'appel civile 10.05.2011 Décision / 2011 / 200\n\n"
          "MOTS-CLÉS\n\nsans en-tête")
    assert cr.derive("vd_findinfo", docket="Décision / 2011 / 200", text=vd).chamber is None


@pytest.mark.parametrize("court, docket, url, chamber", [
    ("fr_gerichte", "602 2025 13", "", "IIe Cour administrative"),
    ("fr_gerichte", "607 2020 1", "", "Cour fiscale"),
    ("fr_gerichte", "106 2012 5", "", None),        # Chambre des tutelles era: no fallback
    ("bl_gerichte", "2013-05-23_sv_9", "", "Kantonsgericht, Abteilung Sozialversicherungsrecht"),
    ("bl_gerichte", "2013_12_03_sr_1", "", "Kantonsgericht, Abteilung Strafrecht"),
    ("bl_gerichte", "731 15 1", "", None),          # series below the 98 % threshold
    ("bl_gerichte", "2014-11-13_01",
     "https://www.baselland.ch/politik-und-behorden/gerichte/rechtsprechung/enteignungsgericht/x.pdf",
     "Enteignungsgericht"),
    ("nw_gerichte", "BAS 25 25", "", "Obergericht, Beschwerdeabteilung in Strafsachen"),
    ("ur_gerichte", "2024_OG V 24 14", "", "Obergericht, Verwaltungsrechtliche Abteilung"),
    ("ur_gerichte", "94/95 13", "", None),
    ("ar_gerichte", "O4V-19-34 ARGVP 2020 3775", "", "Obergericht, 4. Abteilung"),
    ("ar_gerichte", "OG ARGVP 2011 3563", "", "Obergericht"),
    ("ar_gerichte", "Verwaltung ARGVP 1988 2033", "", None),
    ("zh_baurekursgericht", "BRKE III Nr. 0167/2010", "", "3. Abteilung"),
    ("be_weitere", "2023.GSI.846", "", "GSI"),
    ("be_weitere", "JGK\xa032.13-15.36", "", "JGK"),
    ("be_weitere", "2012-0027/11 G 45",
     "https://www.gef.be.ch/gef/de/index/x/2012-0027_11_G_45_anonymisiert.pdf", "GEF"),
])
def test_docket_and_url_fallbacks(court, docket, url, chamber):
    lang = "fr" if court == "fr_gerichte" else "de"
    assert cr.chamber_for(court, docket=docket, text="", language=lang, source_url=url) == chamber


def test_fr_docket_fallback_names_the_cour_in_the_ruling_language():
    assert cr.chamber_for("fr_gerichte", docket="602 2025 13", text="", language="de") == \
        "II. Verwaltungsgerichtshof"


def test_refine_completes_a_bare_court_name_only():
    assert cr.refine(None, "Kantonsgericht, Abteilung Strafrecht") == "Kantonsgericht, Abteilung Strafrecht"
    assert cr.refine("Kantonsgericht", "Kantonsgericht, Abteilung Strafrecht") == \
        "Kantonsgericht, Abteilung Strafrecht"
    assert cr.refine("Steuergericht", "Kantonsgericht, Abteilung Strafrecht") == "Steuergericht"
    assert cr.refine("ARGVP", "Obergericht, 3. Abteilung") == "ARGVP"
    assert cr.refine("HC", "Chambre des recours civile") == "HC"
    assert cr.refine("Obergericht", None) == "Obergericht"


def test_unknown_court_is_untouched():
    assert cr.chamber_for("zh_obergericht", docket="LB240001", text="I. Zivilkammer") is None


# ---------------------------------------------------------------------------
# The repair script on a throwaway shard
# ---------------------------------------------------------------------------

def _rows():
    fr = INDEX["fr_601_2025_200"]
    conflict = INDEX["fr_conflict_106_2018_45"]
    return [
        {"decision_id": fr["decision_id"], "court": "fr_gerichte", "docket_number": fr["docket_number"],
         "language": "fr", "chamber": None, "source_url": fr["source_url"],
         "full_text": (FIX / "fr_601_2025_200.txt").read_text(), "decision_date": "2026-06-09"},
        {"decision_id": conflict["decision_id"], "court": "fr_gerichte",
         "docket_number": conflict["docket_number"], "language": "fr", "chamber": "",
         "full_text": (FIX / "fr_conflict_106_2018_45.txt").read_text()},
        {"decision_id": "bl_gerichte_720 2014 312", "court": "bl_gerichte", "docket_number": "720 2014 312",
         "chamber": "Kantonsgericht", "language": "de",
         "full_text": (FIX / "bl_720_2014_312.txt").read_text()},
        {"decision_id": "bl_gerichte_x", "court": "bl_gerichte", "docket_number": "400 20 1",
         "chamber": "Steuergericht", "language": "de", "full_text": "x"},
        {"decision_id": "zh_obergericht_LB1", "court": "zh_obergericht", "docket_number": "LB1",
         "chamber": None, "full_text": "I. Zivilkammer"},
    ]


def _write(path: Path, rows) -> list[bytes]:
    lines = [json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n" for r in rows]
    path.write_bytes(b"".join(lines) + b"not json\n")
    return lines


def _run(*args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "backfill_cantonal_chambers.py"), *args],
                          capture_output=True, text=True, cwd=REPO, check=True)


def test_backfill_dry_run_writes_nothing(tmp_path):
    shard = tmp_path / "fr_gerichte.jsonl"
    _write(shard, _rows())
    before = shard.read_bytes()
    res = json.loads(_run("--shard", str(shard)).stdout)
    assert shard.read_bytes() == before
    assert not list(tmp_path.glob("*.bak-chamber-*"))
    fr = res["courts"]["fr_gerichte"]
    assert (fr["rows"], fr["filled_before"], fr["filled_after"], fr["conflict"]) == (2, 0, 1, 1)
    bl = res["courts"]["bl_gerichte"]
    assert (bl["filled_before"], bl["refined"], bl["filled_after"]) == (2, 1, 2)


def test_backfill_apply_changes_only_chamber(tmp_path):
    shard = tmp_path / "fr_gerichte.jsonl"
    lines = _write(shard, _rows())
    report = tmp_path / "report.json"
    _run("--shard", str(shard), "--apply", "--report", str(report))
    backups = list(tmp_path.glob("fr_gerichte.jsonl.bak-chamber-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"".join(lines) + b"not json\n"
    new = shard.read_bytes().split(b"\n")
    assert len(new) == len(lines) + 2  # same rows + the unparsable line + trailing ""
    after = [json.loads(x) for x in new[:len(lines)]]
    before = [json.loads(x) for x in lines]
    assert after[0]["chamber"] == "Ie Cour administrative"
    assert new[1] == lines[1].rstrip(b"\n")          # conflict row: byte-identical
    assert after[2]["chamber"] == "Kantonsgericht, Abteilung Sozialversicherungsrecht"
    assert new[3] == lines[3].rstrip(b"\n")          # another court's name kept
    assert new[4] == lines[4].rstrip(b"\n")          # court without a rule
    assert new[5] == b"not json"
    for b, a in zip(before, after):
        assert {k: v for k, v in a.items() if k != "chamber"} == {k: v for k, v in b.items() if k != "chamber"}
    rep = json.loads(report.read_text())
    assert [c["decision_id"] for c in rep["conflicts"]] == [before[1]["decision_id"]]
    assert {c["how"] for c in rep["changes"]} == {"header"}
    assert not (tmp_path / "fr_gerichte.jsonl.tmp").exists()
    assert os.stat(shard).st_mode == os.stat(backups[0]).st_mode


def test_backfill_refuses_to_overwrite_a_backup(tmp_path):
    shard = tmp_path / "fr_gerichte.jsonl"
    _write(shard, _rows())
    _run("--shard", str(shard), "--apply")
    # A second run the same day finds nothing to do and leaves the shard alone ...
    applied = shard.read_bytes()
    _run("--shard", str(shard), "--apply")
    assert shard.read_bytes() == applied
    # ... and refuses rather than overwrite that day's backup when it would write.
    _write(shard, _rows())
    with pytest.raises(subprocess.CalledProcessError):
        _run("--shard", str(shard), "--apply")
    assert not (tmp_path / "fr_gerichte.jsonl.tmp").exists()


# ---------------------------------------------------------------------------
# Scrapers carry the chamber for new rows
# ---------------------------------------------------------------------------

def test_tribuna_fetch_decision_sets_chamber(monkeypatch):
    from scrapers.cantonal.fr_gerichte import FRGerichteScraper

    text = (FIX / "fr_601_2025_200.txt").read_text()
    s = FRGerichteScraper.__new__(FRGerichteScraper)

    class R:
        headers = {"Content-Type": "application/pdf"}
        content = b"%PDF-1.4 fake"

    monkeypatch.setattr(s, "get", lambda url, **k: R(), raising=False)
    monkeypatch.setattr(FRGerichteScraper, "_pdf_text", staticmethod(lambda data: text))
    monkeypatch.setattr(s, "clean_text", lambda t: t, raising=False)
    d = s.fetch_decision({"decision_id": "fr_gerichte_601 2025 200", "docket_number": "601 2025 200",
                          "decision_date": "2026-06-09", "enc_path": "AAAA/b+c=", "title": None})
    assert d is not None and d.chamber == "Ie Cour administrative"


def test_tribuna_portal_without_rule_keeps_no_chamber(monkeypatch):
    from scrapers.cantonal.be_bvd import BEBvdScraper

    s = BEBvdScraper.__new__(BEBvdScraper)

    class R:
        headers = {"Content-Type": "application/pdf"}
        content = b"%PDF-1.4 fake"

    monkeypatch.setattr(s, "get", lambda url, **k: R(), raising=False)
    monkeypatch.setattr(BEBvdScraper, "_pdf_text",
                        staticmethod(lambda data: "ENTSCHEID DER BAU-, VERKEHRS- UND ENERGIEDIREKTION " * 3))
    monkeypatch.setattr(s, "clean_text", lambda t: t, raising=False)
    d = s.fetch_decision({"decision_id": "be_bvd_110_2016_185", "docket_number": "110 2016 185",
                          "decision_date": "2017-03-23", "enc_path": "abcdef0123456789", "title": None})
    assert d is not None and d.chamber is None


def test_ar_gvp_entry_quoting_the_lower_court_is_not_its_chamber():
    """A GVP entry on an Obergericht ruling names the Kantonsgericht Abteilung
    it reviewed; that is not the deciding chamber."""
    text = ("Berufung gegen das Urteil des\nKantonsgericht Appenzell Ausserrhoden\n"
            "1. Abteilung\nvom 3. Mai 2018\n")
    assert cr.chamber_for("ar_gerichte", docket="OG ARGVP 2018 3728", text=text) == "Obergericht"
    assert cr.derive("ar_gerichte", docket="OG O1Z-18-5 ARGVP 2018 3728", text=text).chamber == \
        "Obergericht, 1. Abteilung"
