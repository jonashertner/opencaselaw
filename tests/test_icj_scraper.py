"""International Court of Justice (scrapers/icj.py).

HTML fixtures captured 2026-10-25 from icj-cij.org and trimmed to the parts
the scraper reads: case 143 (Jurisdictional Immunities, upper-case file
names), 182 (Ukraine v. Russia, orders) and 187 (Climate Change advisory
opinion, lower-case file names). PDFs are generated in the test; CDX lines
are synthetic, in the Archive's shape. No network."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures" / "icj"

from scrapers import icj as c  # noqa: E402

FILES = "https://www.icj-cij.org/sites/default/files/case-related"
JUD_FR = f"{FILES}/143/143-20120203-JUD-01-00-FR.pdf"
JUD_EN = f"{FILES}/143/143-20120203-JUD-01-00-EN.pdf"
OLD_FR = "https://www.icj-cij.org/public/files/case-related/143/143-20120203-JUD-01-00-FR.pdf"
NAME_FR = "Immunités juridictionnelles de l'État (Allemagne c. Italie ; Grèce (intervenant))"
NAME_EN = "Jurisdictional Immunities of the State (Germany v. Italy: Greece intervening)"


def _pdf(text: str) -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=7)
    return doc.tobytes()


FR_TEXT = ("COUR INTERNATIONALE DE JUSTICE\nARRÊT DU 3 FÉVRIER 2012\n"
           + "La Cour, ainsi composée, après délibéré en chambre du conseil, rend l'arrêt suivant. " * 20)
EN_TEXT = ("INTERNATIONAL COURT OF JUSTICE\nJUDGMENT OF 3 FEBRUARY 2012\n"
           + "The Court, composed as above, after deliberation, delivers the following Judgment. " * 20)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(self, url, **_):
        raise AssertionError(f"network access in a test: {url}")
    monkeypatch.setattr(c.ICJScraper, "get", refuse)
    monkeypatch.setattr(c.ICJScraper, "post", refuse)


def _scraper(tmp_path, pdfs: dict[str, bytes], archive: dict | None = None):
    """Scraper whose session serves `pdfs` (anything else: Cloudflare's 403
    challenge page) and whose Archive index is `archive`."""
    s = c.ICJScraper(state_dir=tmp_path)
    s._rate_limit = lambda: None
    s.archive = archive or {}

    def get(url, **_):
        body = pdfs.get(url)
        if body is None:
            return SimpleNamespace(status_code=403, content=b"<html>Just a moment...</html>")
        return SimpleNamespace(status_code=200, content=body)
    s.session = SimpleNamespace(get=get)
    return s


def _stub(**kw):
    return {"code": "143-20120203-JUD-01", "case": 143, "kind": "JUD", "date": "2012-02-03",
            "files": {"EN": JUD_EN, "FR": JUD_FR}, "decision_id": "icj_143-20120203-JUD-01",
            "docket_number": "143-20120203-JUD-01", "name_fr": NAME_FR, "name_en": NAME_EN, **kw}


def test_case_lists_titles_and_tabs():
    assert c.case_ids((FIX / "list-of-all-cases.html").read_text()) >= {191, 199}
    assert c.case_ids((FIX / "pending-cases.html").read_text()) == {164, 171, 175}
    page = (FIX / "case_143.html").read_text()
    assert c.case_title(page) == NAME_EN
    assert c.case_title((FIX / "fr_affaire_143.html").read_text()) == NAME_FR
    assert c.case_tabs(page, 143) == {"judgments", "orders"}


def test_documents_leave_the_opinions_out():
    [jud] = c.parse_documents((FIX / "case_143_judgments.html").read_text())
    assert jud["code"] == "143-20120203-JUD-01" and jud["date"] == "2012-02-03" and jud["kind"] == "JUD"
    assert jud["files"] == {"EN": JUD_EN, "FR": JUD_FR, "BI": f"{FILES}/143/143-20120203-JUD-01-00-BI.pdf"}
    [adv] = c.parse_documents((FIX / "case_187_advisory-opinions.html").read_text())
    assert adv["code"] == "187-20250723-ADV-01" and sorted(adv["files"]) == ["EN", "FR"]
    orders = c.parse_documents((FIX / "case_182_orders.html").read_text())
    assert len(orders) == 8 and orders[0]["code"] == "182-20220316-ORD-01"
    assert all(o["kind"] == "ORD" and o["case"] == 182 for o in orders)


def test_archive_index_keeps_the_largest_capture_of_either_path():
    old = ("https://www.icj-cij.org/public/files/case-related/1/001-19490409-JUD-01-00-FR.pdf 20211022003452 4211189\n"
           "https://www.icj-cij.org/public/files/case-related/1/001-19490409-JUD-01-00-FR.pdf 20231003042701 979887\n")
    new = "https://www.icj-cij.org/sites/default/files/case-related/1/001-19490409-JUD-01-00-FR.pdf 20230924095530 979900\n"
    idx = c.parse_cdx(new, c.parse_cdx(old))
    assert idx == {"case-related/1/001-19490409-jud-01-00-fr.pdf": (
        "20211022003452", "https://www.icj-cij.org/public/files/case-related/1/001-19490409-JUD-01-00-FR.pdf", 4211189)}
    assert c._url_key(f"{FILES}/1/001-19490409-JUD-01-00-FR.pdf") == "case-related/1/001-19490409-jud-01-00-fr.pdf"


def test_substantive_orders():
    assert c.substantive_order("DEMANDE EN INDICATION DE MESURES CONSERVATOIRES. " * 250)
    assert not c.substantive_order("Fixation des délais. Vu l'ordonnance indiquant des mesures conservatoires. " * 40)


def test_fetch_falls_back_to_the_archive_and_builds_the_citation(tmp_path):
    import mcp_server
    wayback = c.WAYBACK_URL.format(ts="20211025145812", url=OLD_FR)
    s = _scraper(tmp_path, {wayback: _pdf(FR_TEXT)},
                 {c._url_key(JUD_FR): ("20211025145812", OLD_FR, 1000)})
    d = s.fetch_decision(_stub())
    assert d.court == "icj" and d.canton == "UN" and d.language == "fr"
    assert d.docket_number == "143-20120203-JUD-01" and d.docket_number_2 == "General List No. 143"
    assert d.decision_date == date(2012, 2, 3) and d.decision_type == "Arrêt"
    assert d.title == NAME_FR and d.regeste == f"[EN: {NAME_EN}]"
    assert d.source_url == JUD_FR and "rend l'arrêt suivant" in d.full_text
    row = {"decision_id": d.decision_id, "court": d.court, "docket_number": d.docket_number,
           "title": d.title, "decision_date": "2012-02-03", "language": d.language}
    cs = mcp_server._build_citation_strings(row, pinpoint="57")
    assert cs["citation_string_fr"] == f"CIJ, {NAME_FR}, arrêt du 3 février 2012, par. 57"
    assert cs["citation_string_de"] == f"IGH, {NAME_FR}, Urteil vom 3. Februar 2012, Ziff. 57"
    assert mcp_server._build_citation_strings({**row, "docket_number": "187-20250723-ADV-01",
                                               "decision_date": "2025-07-23"})["citation_string_it"] \
        == f"CIG, {NAME_FR}, parere consultivo del 23 luglio 2025"


def test_live_pdf_english_fallback_and_missing_text(tmp_path):
    s = _scraper(tmp_path, {JUD_EN: _pdf(EN_TEXT)})
    d = s.fetch_decision(_stub())
    assert d.language == "en" and d.title == NAME_EN and d.regeste is None
    assert d.decision_type == "Judgment" and d.source_url == JUD_EN
    assert _scraper(tmp_path, {}).fetch_decision(_stub()) is None


def test_procedural_order_is_recorded_and_not_rediscovered(tmp_path, monkeypatch):
    order = f"{FILES}/182/182-20221007-ord-01-00-fr.pdf"
    s = _scraper(tmp_path, {order: _pdf("ORDONNANCE DU 7 OCTOBRE 2022. Fixation des délais. " * 40)})
    stub = _stub(code="182-20221007-ORD-01", case=182, kind="ORD", date="2022-10-07",
                 files={"FR": order}, decision_id="icj_182-20221007-ORD-01")
    assert s.fetch_decision(stub) is None
    assert (tmp_path / "icj.skipped.tsv").read_text().startswith("icj_182-20221007-ORD-01\t")
    assert "icj_182-20221007-ORD-01" in c.ICJScraper(state_dir=tmp_path).skipped


def test_discovery_lists_new_documents_and_settles_concluded_cases(tmp_path, monkeypatch):
    pages = {
        c.LIST_URLS[0]: '<a href="case/143">x</a>',
        c.PENDING_URL: "",
        f"{c.BASE}/case/143": (FIX / "case_143.html").read_text(),
        f"{c.BASE}/fr/affaire/143": (FIX / "fr_affaire_143.html").read_text(),
        f"{c.BASE}/case/143/judgments": (FIX / "case_143_judgments.html").read_text(),
        f"{c.BASE}/case/143/orders": "<html></html>",
    }
    s = c.ICJScraper(state_dir=tmp_path)
    monkeypatch.setattr(s, "get", lambda url, **_: SimpleNamespace(text=pages[url]))
    [stub] = list(s.discover_new())
    assert stub["decision_id"] == "icj_143-20120203-JUD-01"
    assert stub["name_fr"] == NAME_FR and stub["name_en"] == NAME_EN
    assert not (tmp_path / "icj.cases_done.tsv").exists()          # not scraped yet
    s.state.mark_scraped("icj_143-20120203-JUD-01")
    assert list(s.discover_new()) == []
    assert (tmp_path / "icj.cases_done.tsv").read_text().startswith("143\t")
    s2 = c.ICJScraper(state_dir=tmp_path)
    monkeypatch.setattr(s2, "get", lambda url, **_: SimpleNamespace(text=pages[url]))
    assert list(s2.discover_new()) == [] and 143 in s2.done_cases   # case pages not fetched again
    pages[c.LIST_URLS[0]] = ""
    with pytest.raises(RuntimeError, match="no case links"):
        list(s2.discover_new())


def test_registries_know_icj():
    import cli_ch
    import ecli
    import export_parquet
    import mcp_server
    import run_all_scrapers
    import run_scraper
    assert run_scraper.SCRAPERS["icj"] == ("scrapers.icj", "ICJScraper")
    assert "icj" in run_all_scrapers.SKIP_BY_DEFAULT and "icj" in export_parquet.EXCLUDED_COURTS
    assert mcp_server.COURT_LEVELS["icj"] == "international" and "icj" in mcp_server.COURT_DISPLAY_NAMES
    assert ecli.mint_ecli("icj_143-20120203-JUD-01", "icj", "143-20120203-JUD-01", "2012-02-03") is None
    assert cli_ch.mint_cli_ch("icj_143-20120203-JUD-01", "icj", "143-20120203-JUD-01") is None
