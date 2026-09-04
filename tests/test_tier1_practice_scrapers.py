"""Tier 1 social-law practice scrapers (Caritas memo 2026-09-02): BSV, SECO
AVIG-Praxis, BAG KVG, SEM Handbuch Asyl, BJ SchKG.

All parser tests run on golden fixtures captured 2026-09-02 (trimmed to the
relevant container) via `Scraper.__new__` — no __init__, no output dir, no
network. The single run() test uses a stubbed fetch_pdf_text.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "practice"

from scrapers.practice import bsv_weisungen as bsv  # noqa: E402
from scrapers.practice import seco_alv, bag_kvg, sem_handbuch_asyl, bj_schkg  # noqa: E402
from scrapers.practice.base import first_date_iso  # noqa: E402


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ───────────────────────────────────────────── shared date helper

@pytest.mark.parametrize("text,expected", [
    ("Weisung Nr. 7 vom 16. April 2020 (COVID-19) (obsolet) PDF 200.40 kB 6. April 2020", "2020-04-16"),
    ("Directive du 31 mars 2017 sur la communication PDF 388.32 kB 11 août 2005", "2017-03-31"),
    ("Kreisschreiben Nr. 7.2 vom 2014.10.14: Bundesgesetz PDF 353 kB 17. Dezember 2021", "2014-10-14"),
    ("Weisung AVIG ALE (AVIG-Praxis ALE) gültig ab 1.7.2026 PDF | 25.06.2026", "2026-07-01"),
    ("Direttiva LADI ID valida dal 1.7.2026 PDF | 25.06.2026", "2026-07-01"),
    ("Istruzione n. 2 del 15 aprile 2014 sul precetto esecutivo", "2014-04-15"),
    ("Richtlinien Job Coaching in der ALV und öAV PDF", ""),
])
def test_first_date_iso_takes_issuance_date_first(text, expected):
    assert first_date_iso(text) == expected


# ───────────────────────────────────────────── BSV

@pytest.fixture(scope="module")
def bsv_folders():
    return bsv.parse_nav(_read("bsv_home_nav.html"))


def test_bsv_nav_yields_every_leaf_folder(bsv_folders):
    assert len(bsv_folders) == 256
    by_id = {f["id"]: f for f in bsv_folders}
    assert by_id["5638"]["label"] == "Weisungen EL"
    assert by_id["5638"]["path"][-2:] == ["EL", "Grundlagen EL"]


def test_bsv_scope_keeps_practice_folders_and_drops_tooling(bsv_folders):
    scoped = [f for f in bsv_folders if bsv.in_scope(f)]
    ids = {f["id"] for f in scoped}
    assert len(scoped) == 41
    # counsellor-relevant folders survive
    for must in ("5638", "5639", "5640", "5587",       # EL Weisungen, Erläuterungen, Nachträge, Mitteilungen
                 "5661", "5664", "5662", "5663",        # IV Kreisschreiben/Rundschreiben + archives
                 "5622", "5621", "5595", "5612", "5613"):  # AHV Renten/Beiträge, Mitteilungen, Rechtsprechung
        assert must in ids, must
    # International (EESSI/BUC/SED), eGov, Altersfragen, links, statistics, forms are out
    for gone in ("16755", "5560", "5645", "5593", "5594", "12918", "20314", "5591", "5589"):
        assert gone not in ids, gone
    assert all(not any(p == "International" for p in f["path"]) for f in scoped)


def test_bsv_folder_parses_every_version_with_language_and_date(bsv_folders):
    folder = next(f for f in bsv_folders if f["id"] == "5638")
    de = bsv.parse_folder(_read("bsv_folder_5638_weisungen_el.html"), folder, "de")
    fr = bsv.parse_folder(_read("bsv_folder_5638_weisungen_el.html"), folder, "fr")
    it = bsv.parse_folder(_read("bsv_folder_5638_weisungen_el.html"), folder, "it")
    # 4 documents: WEL 20 versions, KSBIL 11, KSIU 5, KS-R EL 1 = 37 version rows
    assert len(de) == 37 and len(fr) == 37
    # Italian exists only for a subset of versions (per-version language list)
    assert 0 < len(it) < 37
    wel = [s for s in de if s["doc_number"] == "WEL"]
    assert len(wel) == 20
    assert {s["date"] for s in wel} and all(s["date"] for s in de)
    assert all(s["language"] == "de" for s in de)
    assert all(s["pdf_url"].startswith("https://sozialversicherungen.admin.ch/de/d/6930/download?version=")
               for s in wel)
    assert all("Version " in " ".join(s["topics"]) for s in de)
    # doc_number is the abbreviation on EVERY version, even where the
    # Dokumentennummer is missing (WEL v17/v18 on the live page)
    assert {s["doc_number"] for s in wel} == {"WEL"}
    # doc types from the title
    types = Counter(s["doc_type"] for s in de)
    assert types["wegleitung"] == 20 and types["kreisschreiben"] >= 12


def test_bsv_no_current_fallback_for_a_language_the_document_lacks(bsv_folders):
    """Live run 2026-09-02: on the IT folder page a de/fr-only document fell
    through to a 'vcurrent' stub whose Italian download was empty. The
    fallback may only fire when the parent row lists the page language."""
    folder = next(f for f in bsv_folders if f["id"] == "5638")
    html = _read("bsv_folder_5638_weisungen_el.html")
    for lang in ("de", "fr", "it"):
        stubs = bsv.parse_folder(html, folder, lang)
        assert all(s["bsv_version"] != "current" for s in stubs), lang
    # the versions table of doc 15513 lists de/fr only
    assert not [s for s in bsv.parse_folder(html, folder, "it") if s["bsv_doc"] == "15513"]
    assert [s for s in bsv.parse_folder(html, folder, "fr") if s["bsv_doc"] == "15513"]
    # a parent row WITHOUT a versions table and with a de/fr language list
    stripped = html.replace('id="inline-versions-15513"', 'id="gone-15513"')
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(stripped, "html.parser")
    soup.find("tr", id="gone-15513").decompose()
    stubs_it = bsv.parse_folder(str(soup), folder, "it")
    stubs_de = bsv.parse_folder(str(soup), folder, "de")
    assert not [s for s in stubs_it if s["bsv_doc"] == "15513"]
    assert [s for s in stubs_de if s["bsv_doc"] == "15513" and s["bsv_version"] == "current"]


def test_bsv_doc_ids_unique_per_version_and_language(bsv_folders):
    folder = next(f for f in bsv_folders if f["id"] == "5638")
    s = bsv.BsvWeisungenScraper.__new__(bsv.BsvWeisungenScraper)
    ids = []
    for lang in ("de", "fr", "it"):
        ids += [s._make_doc_id(x) for x in
                bsv.parse_folder(_read("bsv_folder_5638_weisungen_el.html"), folder, lang)]
    assert len(ids) == len(set(ids))
    assert "bsv_weisungen_6930_v20_de" in ids and "bsv_weisungen_6930_v20_fr" in ids


def test_bsv_never_caches_pdfs_and_has_no_revision_field():
    assert bsv.BsvWeisungenScraper.CACHE_PDFS is False
    assert bsv.BsvWeisungenScraper.REVISION_FIELD is None


def test_bsv_run_keeps_every_version_as_its_own_record(tmp_path):
    """End-to-end run() over the fixture with a stubbed PDF fetch: one JSONL
    record per (document, version, language), superseded versions retained."""
    folder = {"id": "5638", "label": "Weisungen EL", "path": ["Alle Sozialversicherungen", "EL", "Grundlagen EL"]}
    stubs = bsv.parse_folder(_read("bsv_folder_5638_weisungen_el.html"), folder, "de")

    class _Bsv(bsv.BsvWeisungenScraper):
        REQUEST_DELAY = 0

        def __init__(self):
            self.OUTPUT_DIR = tmp_path
            super().__init__()

        def discover_documents(self):
            yield from stubs

        def fetch_pdf_text(self, pdf_url):
            return f"body for {pdf_url}"

    r = _Bsv().run()
    assert r["new"] == 37 and r["failed"] == 0
    lines = (tmp_path / "bsv_weisungen.jsonl").read_text().splitlines()
    assert len(lines) == 37
    # second run: nothing new, nothing re-fetched
    r2 = _Bsv().run()
    assert r2["new"] == 0 and r2["skipped"] == 37


# ───────────────────────────────────────────── SECO AVIG-Praxis

@pytest.mark.parametrize("lang", ["de", "fr", "it"])
def test_seco_alv_section_yields_18_documents_per_language(lang):
    stubs = seco_alv.parse_page(_read(f"seco_alv_{lang}_avig_section.html"), lang, "u")
    assert len(stubs) == 18
    assert all(s["language"] == lang for s in stubs)
    assert all("fileservice" in s["pdf_url"] for s in stubs)
    # the two Richtlinien carry no date on the index page — allowed
    assert sum(1 for s in stubs if not s["date"]) == 2
    s = seco_alv.SecoAlvScraper.__new__(seco_alv.SecoAlvScraper)
    ids = [s._make_doc_id(x) for x in stubs]
    assert len(ids) == len(set(ids))


def test_seco_alv_codes_are_stable_across_editions():
    assert seco_alv.stable_code("Weisung AVIG ALE (Arbeitslosenentschädigung) (AVIG-Praxis ALE) gültig ab 1.7.2026") == "AVIG ALE"
    assert seco_alv.stable_code("Directive LACI IC (Indemnité de chômage) (Bulletin LACI IC) valable dès 1.7.2026") == "LACI IC"
    assert (seco_alv.stable_code("Weisung über die Vergütung von AMM gültig ab 1.6.2026")
            == seco_alv.stable_code("Weisung über die Vergütung von AMM gültig ab 1.1.2027"))
    assert seco_alv.stable_code("Weisung 2026/01: Einarbeitungszuschüsse") == "Weisung 2026/01"
    assert seco_alv.stable_code("Weisung über die Auswirkungen der Verordnungen (EG) Nr. 883/2004 und 987/2009") == "VO 883/2004"
    # a connective must never be read as a code
    assert seco_alv.stable_code("Leitfaden zur Bearbeitung von Personendaten in den Bereichen AVIG und AVG") != "AVIG und"
    assert seco_alv.SecoAlvScraper.REVISION_FIELD == "pdf_url"


# ───────────────────────────────────────────── BAG KVG

@pytest.mark.parametrize("lang", ["de", "fr"])
def test_bag_kvg_parses_19_kreisschreiben(lang):
    stubs = bag_kvg.parse_page(_read(f"bag_kvg_{lang}.html"), lang, "u")
    assert len(stubs) == 19
    assert all(s["date"] for s in stubs)
    assert {s["doc_number"] for s in stubs} >= {"KS 1.1", "KS 5.1", "KS 7.10"}
    s = bag_kvg.BagKvgScraper.__new__(bag_kvg.BagKvgScraper)
    ids = [s._make_doc_id(x) for x in stubs]
    assert len(ids) == len(set(ids)) and ids[0].endswith(f"_{lang}")


def test_bag_kvg_prefers_the_stated_issuance_date():
    stubs = bag_kvg.parse_page(_read("bag_kvg_de.html"), "de", "u")
    ks72 = next(s for s in stubs if s["doc_number"] == "KS 7.2")
    assert ks72["date"] == "2014-10-14"          # "vom 2014.10.14", not the 2021 file date


# ───────────────────────────────────────────── SEM Handbuch

@pytest.mark.parametrize("lang", ["de", "fr"])
def test_sem_handbuch_parses_46_articles(lang):
    stubs = sem_handbuch_asyl.parse_page(_read(f"sem_handbuch_asyl_{lang}.html"), lang, "u")
    assert len(stubs) == 46
    assert all(s["date"] and s["doc_type"] == "handbuch" for s in stubs)
    assert any(t.startswith("Kapitel C") for s in stubs for t in s["topics"])


def test_sem_handbuch_ids_align_across_languages():
    s = sem_handbuch_asyl.SemHandbuchAsylScraper.__new__(sem_handbuch_asyl.SemHandbuchAsylScraper)
    de = {s._make_doc_id(x).rsplit("_", 1)[0] for x in
          sem_handbuch_asyl.parse_page(_read("sem_handbuch_asyl_de.html"), "de", "u")}
    fr = {s._make_doc_id(x).rsplit("_", 1)[0] for x in
          sem_handbuch_asyl.parse_page(_read("sem_handbuch_asyl_fr.html"), "fr", "u")}
    assert de == fr                       # FR labels "Article I2" what DE calls I1; ids key on the file
    assert "sem_handbuch_asyl_c61" in de


def test_sem_handbuch_empty_date_is_not_a_reissue(tmp_path):
    cls = sem_handbuch_asyl.SemHandbuchAsylScraper
    assert cls.REVISION_FIELD == "date" and cls.CACHE_PDFS is False
    s = cls.__new__(cls)
    s._seen_revisions = {"sem_handbuch_asyl_a1_de": "2019-02-19"}
    assert s._is_reissue("sem_handbuch_asyl_a1_de", {"date": ""}) is False
    assert s._is_reissue("sem_handbuch_asyl_a1_de", {"date": "2026-01-01"}) is True


# ───────────────────────────────────────────── BJ SchKG

def test_bj_weisungen_language_scoped_ids_and_issuance_dates():
    s = bj_schkg.BjSchkgScraper.__new__(bj_schkg.BjSchkgScraper)
    ids = []
    for lang in ("de", "fr", "it"):
        stubs, zips = bj_schkg.parse_page(_read(f"bj_schkg_weisungen_{lang}.html"), "weisungen", lang, "u")
        assert len(stubs) == 13 and zips == 2
        ids += [s._make_doc_id(x) for x in stubs]
        w7 = next(x for x in stubs if x["doc_number"] == "Weisung 7")
        assert w7["date"] == "2020-04-16"        # issuance, not the 2020-04-06 file date
        assert "obsolet" in w7["topics"]
        anh = [x for x in stubs if x["doc_type"] == "weisung_anhang"]
        assert len(anh) == 2 and anh[0]["doc_number"].endswith("Anhang")
    assert len(ids) == len(set(ids)) == 39
    assert {"bj_schkg_weisung_1_de", "bj_schkg_weisung_1_fr", "bj_schkg_weisung_1_it"} <= set(ids)


def test_bj_cantonal_kreisschreiben_types_come_from_the_text():
    stubs, _ = bj_schkg.parse_page(_read("bj_schkg_kreisschreiben_de.html"), "kreisschreiben_kantone", "de", "u")
    assert len(stubs) == 42
    types = Counter(s["doc_type"] for s in stubs)
    assert types["erlass"] == 2 and types["konkordat"] == 2      # GL EG SchKG, Verordnung, Konkordat + Beitritt
    assert types["richtlinie"] == 5 and types["weisung"] == 12 and types["kreisschreiben"] == 20
    # no stub whose text opens with "Weisung" may be labelled kreisschreiben
    assert not [s for s in stubs if s["title"].startswith("Weisung") and s["doc_type"] == "kreisschreiben"]
    zh = [s for s in stubs if "ZH" in s["topics"]]
    assert len(zh) == 16
    assert any("Existenzminimum" in s["topics"] for s in zh)
    assert all(s["date"] for s in stubs)
    fr, _ = bj_schkg.parse_page(_read("bj_schkg_kreisschreiben_fr.html"), "kreisschreiben_kantone", "fr", "u")
    assert len(fr) == 12 and {t for s in fr for t in s["topics"]} >= {"GE", "VD"}
    ge = next(s for s in fr if s["title"].startswith("Directive du 31 mars 2017"))
    assert ge["date"] == "2017-03-31"
    assert bj_schkg.BjSchkgScraper.REVISION_FIELD == "pdf_url"


# ───────────────────────────────────────────── runner registry

def test_runner_registry_is_consistent():
    from scrapers.practice import runner
    for key, cls in runner.ALL_SCRAPERS.items():
        assert key == cls.SOURCE_KEY, (key, cls.SOURCE_KEY)
        assert cls.ISSUING_AUTHORITY and cls.DEFAULT_DOC_TYPE
    for key in ("seco_alv", "bag_kvg", "sem_handbuch_asyl", "bj_schkg", "bsv_weisungen"):
        assert key in runner.ENABLED_SCRAPERS
    # BSV was experimental until its first full run (2026-09-03/04, 6,083
    # records, 1 h 45 min) proved the unit can hold it; the unit now has no
    # timeout. A source must be in exactly one registry.
    assert not set(runner.ENABLED_SCRAPERS) & set(runner.EXPERIMENTAL_SCRAPERS)


# ───────────────────────────────────────────── scanned PDFs

def test_scanned_pdf_gets_placeholder_body_only_when_opted_in(tmp_path, monkeypatch):
    from scrapers.practice.base import PracticeScraper
    import scrapers.practice.base as base

    class _Resp:
        content = b"%PDF-1.4 image-only"
        def raise_for_status(self): pass

    monkeypatch.setattr(base, "extract_pdf_text", lambda b: "")

    class _Plain(PracticeScraper):
        SOURCE_KEY = "unit_plain"; ISSUING_AUTHORITY = "T"; DEFAULT_DOC_TYPE = "x"
        CACHE_PDFS = False; REQUEST_DELAY = 0
        def __init__(self): self.OUTPUT_DIR = tmp_path; super().__init__()
        def discover_documents(self): yield {"pdf_url": "https://x/a.pdf", "title": "A", "doc_number": "1", "date": "", "language": "de"}
        def get(self, url, **kw): return _Resp()

    class _Opted(_Plain):
        SOURCE_KEY = "unit_opted"
        NO_TEXT_LAYER_BODY = "[scan]"

    assert _Plain().run()["failed"] == 1          # historical behaviour kept
    r = _Opted().run()
    assert r["new"] == 1 and r["failed"] == 0
    import json as _json
    rec = _json.loads((tmp_path / "unit_opted.jsonl").read_text().splitlines()[0])
    assert rec["body_text"] == "A [scan]"         # the document's OWN title + marker, no shared boilerplate
    for cls in (bag_kvg.BagKvgScraper, bj_schkg.BjSchkgScraper, bsv.BsvWeisungenScraper):
        assert cls.NO_TEXT_LAYER_BODY


# ───────────────────────────────────────────── review follow-ups (2026-09-02)

def test_non_pdf_200_is_a_failure_not_a_scan(tmp_path, monkeypatch):
    """A login bounce / HTML error page with HTTP 200 must never be indexed as
    a 'scanned' document under the title (robots on sozialversicherungen.admin.ch
    disallows *Login*, exactly the bounce this guards against)."""
    from scrapers.practice.base import PracticeScraper

    class _Resp:
        content = b"<!doctype html><html><body>Login</body></html>"
        headers = {"Content-Type": "text/html"}
        def raise_for_status(self): pass

    class _S(PracticeScraper):
        SOURCE_KEY = "unit_html"; ISSUING_AUTHORITY = "T"; DEFAULT_DOC_TYPE = "x"
        CACHE_PDFS = False; REQUEST_DELAY = 0; NO_TEXT_LAYER_BODY = "[scan]"
        def __init__(self): self.OUTPUT_DIR = tmp_path; super().__init__()
        def discover_documents(self): yield {"pdf_url": "https://x/a.pdf", "title": "A", "doc_number": "1", "date": "", "language": "de"}
        def get(self, url, **kw): return _Resp()

    r = _S().run()
    assert r["failed"] == 1 and r["new"] == 0
    assert not (tmp_path / "unit_html.jsonl").exists()


def test_first_date_iso_handles_french_ordinals():
    assert first_date_iso("Instruction n° 10 du 1er septembre 2023 PDF 179 kB 4 septembre 2023") == "2023-09-01"


def test_seco_numbered_weisung_beats_a_code_mentioned_in_the_title():
    assert seco_alv.stable_code("Weisung 2026/01: Einarbeitungszuschüsse nach AVIG Art. 65 für Personen") == "Weisung 2026/01"
    assert seco_alv.stable_code("Weisung AVIG ALE (Arbeitslosenentschädigung) gültig ab 1.7.2026") == "AVIG ALE"
    assert seco_alv.stable_code("Merkblatt zur AVIG Auslegung") != "AVIG Auslegung"      # unanchored mention


def test_bag_number_must_follow_the_document_word():
    html = ('<a href="/dam/de/sd-web/x/ks-9-9.pdf">Kreisschreiben Nr. 9.9 vom 1.7.2026 Prämien PDF 100 kB 1. Juli 2026</a>'
            '<a href="/dam/de/sd-web/y/no-number.pdf">Aufsicht vom 1.7.2026 PDF 100 kB 1. Juli 2026</a>')
    stubs = bag_kvg.parse_page(html, "de", "u")
    assert [s["doc_number"] for s in stubs] == ["KS 9.9"]          # date never becomes a number; no-number anchor skipped
    assert bag_kvg.BagKvgScraper.REVISION_FIELD == "pdf_url"


def test_bj_first_word_decides_the_type():
    stubs, _ = bj_schkg.parse_page(_read("bj_schkg_kreisschreiben_de.html"), "kreisschreiben_kantone", "de", "u")
    by_stem = {s["bj_stem"]: s for s in stubs}
    assert by_stem["02-bl-ks-d"]["doc_type"] == "weisung"          # "Weisung vom 28. Dezember 2005 betr. … Richtlinien …"
    assert by_stem["02-bl-ks-d"]["doc_number"] == "BL Weisung 2"
    assert by_stem["14-zh-ks-d"]["doc_type"] == "kreisschreiben"   # "KS der VK des OG …: Richtlinien für die Berechnung …"
    assert by_stem["01-sh-ks-d"]["doc_type"] == "kreisschreiben"   # "Beschluss des Obergerichts …"
    assert by_stem["01-bl-ks-d"]["doc_type"] == "richtlinie"       # "Richtlinien vom 24. November 2000 …"
    assert by_stem["07-gl-ks-d"]["doc_type"] == "konkordat"


def test_sem_display_code_without_label_keeps_the_file_code():
    assert sem_handbuch_asyl._display_code("c10", "") == "C10"
    assert sem_handbuch_asyl._display_code("c61", "C6.1") == "C6.1"


def test_build_practice_db_upsert_keeps_good_fields_and_counts_rows(tmp_path):
    import json as _json, sqlite3 as _sql
    from search_stack.build_practice_db import build
    jd = tmp_path / "jsonl"; jd.mkdir()
    first = {"doc_id": "seco_alv_avig_ale_de", "source": "seco_alv", "issuing_authority": "SECO",
             "doc_type": "weisung", "doc_number": "AVIG ALE", "title": "old", "date": "2026-01-01",
             "language": "de", "url": "u", "pdf_url": "https://x/old", "body_text": "old body",
             "topics": ["AVIG"], "scraped_at": "2026-01-01T00:00:00", "content_hash": "a"}
    second = dict(first, title="new", date="", pdf_url="https://x/new", body_text="new body",
                  topics=[], scraped_at="2026-07-01T00:00:00", content_hash="b")
    (jd / "seco_alv.jsonl").write_text("\n".join(_json.dumps(x) for x in (first, second)) + "\n")
    # target through a symlink: the link must survive and point at the rebuilt file
    real_dir = tmp_path / "vol"; real_dir.mkdir()
    real = real_dir / "practice.db"; real.write_bytes(b"")
    link = tmp_path / "practice.db"; link.symlink_to(real)
    summary = build(jd, link)
    assert link.is_symlink() and real.stat().st_size > 0
    assert not (tmp_path / "practice.db.tmp").exists()
    c = _sql.connect(f"file:{real}?mode=ro&immutable=1", uri=True)
    row = c.execute("SELECT title, body_text, date, pdf_url, topics_json FROM practice").fetchone()
    assert row == ("new", "new body", "2026-01-01", "https://x/new", '["AVIG"]')   # text refreshed, blank date/topics kept
    assert c.execute("SELECT doc_count FROM sources WHERE source='seco_alv'").fetchone()[0] == 1
    assert summary["by_source"]["seco_alv"]["lines"] == 2
