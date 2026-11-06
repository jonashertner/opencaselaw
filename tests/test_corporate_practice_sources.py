"""Company-law practice sources (2026-11-06): UEK, RAB, EHRA, FINMA
Aufsichtsmitteilungen/Bulletin, foundation supervisors, cantonal commercial
registers, and the shared language/version dedup. Offline: golden HTML
fixtures captured from the live pages on 2026-11-06."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import ehra  # noqa: E402
from scrapers.practice import finma_aufsicht as fa  # noqa: E402
from scrapers.practice import hr_kantone as hk  # noqa: E402
from scrapers.practice import rab_rs  # noqa: E402
from scrapers.practice import stiftungsaufsicht as sa  # noqa: E402
from scrapers.practice import uek_rs  # noqa: E402
from scrapers.practice.runner import ENABLED_SCRAPERS  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── UEK ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def uek_de():
    return {i["nr"]: i for i in uek_rs.parse_legaltexts(_read("uek_legaltexts_de.html"))}


def test_uek_skips_statutes_and_ps880(uek_de):
    titles = " ".join(i["title"] + i["parent_title"] for i in uek_de.values())
    assert "Finanzmarktinfrastrukturgesetz" not in titles and "Übernahmeverordnung" not in titles
    assert "PS 880" not in titles
    assert len(uek_de) == 27


def test_uek_versions_share_the_circular_number(uek_de):
    nums = {nr: uek_rs.classify(uek_de[nr]) for nr in (233, 212, 223, 221)}
    assert all(v == ("rundschreiben", "UEK-Rundschreiben Nr. 1") for v in nums.values())
    # the repealed circulars whose number was later reused keep their year
    assert uek_rs.classify(uek_de[241])[1] == "UEK-Rundschreiben Nr. 1 (1997)"
    assert uek_rs.classify(uek_de[222])[1] == "UEK-Rundschreiben Nr. 4 (2009)"
    assert uek_rs.classify(uek_de[104]) == ("mitteilung", "UEK-Mitteilung Nr. 1")


def test_uek_side_documents_never_take_the_circulars_number(uek_de):
    assert uek_rs.classify(uek_de[188])[0] == "mitteilung"            # newsletter
    assert uek_rs.classify(uek_de[183])[0] == "bericht"               # expert report
    assert uek_rs.classify(uek_de[189])[0] == "entwurf"               # Anhörungsunterlagen
    assert uek_rs.classify(uek_de[249])[0] == "entwurf"               # consultation section
    for nr in (188, 183, 189, 249):
        assert uek_rs.classify(uek_de[nr])[1] not in {"UEK-Rundschreiben Nr. 1", "UEK-Rundschreiben Nr. 2",
                                                      "UEK-Rundschreiben Nr. 5"}


def test_uek_repealed_heads_give_their_dates(uek_de):
    assert uek_de[104]["parent_title"].startswith("Mitteilung Nr. 1 vom 28. März 2000")
    assert uek_de[221]["parent_nr"] == 223


def test_uek_reports():
    reps = uek_rs.parse_reports(_read("uek_report_de.html"))
    years = {r["year"] for r in reps}
    assert {"2009", "2016", "2024", "2025"} <= years
    assert all(r["pdf_url"].startswith("https://www.takeover.ch/public/data/") for r in reps)


# ── RAB ────────────────────────────────────────────────────────────────

def test_rab_circular_history_without_redlines():
    versions = rab_rs.parse_circulars(_read("rab_rundschreiben_de.html"))
    rs07 = [v for v in versions if v["number"] == "RAB-RS 1/2007"]
    assert rs07[0]["current"] and rs07[0]["date"] == "2024-01-01"
    assert [v["date"] for v in rs07][-1] == "2007-08-27"
    assert not any("Änderungen" in v["label"] for v in versions)
    assert {lang for v in rs07 for lang, _ in v["files"]} == {"de", "fr", "it", "en"}
    rs15 = [v for v in versions if v["number"] == "RAB-RS 1/2015"]
    assert rs15 and all(v["repealed"] for v in rs15)


def test_rab_reports_and_notice():
    reports = rab_rs.parse_simple_tiles(_read("rab_geschaeftsberichte_de.html"))
    assert len(reports) == 20 and reports[0]["title"] == "Geschäftsbericht 2025"
    notices = rab_rs.parse_simple_tiles(_read("rab_aufsichtsmitteilungen_de.html"))
    assert notices[0]["title"] == "Aufsichtsmitteilung 1-2022"
    assert {l for l, _ in notices[0]["files"]} == {"de", "fr", "it", "en"}


# ── EHRA ───────────────────────────────────────────────────────────────

def test_ehra_praxismitteilungen():
    de = ehra.parse_praxis(_read("ehra_praxis_de.html"), "de", "x")
    nums = [s["doc_number"] for s in de]
    assert "EHRA-Praxismitteilung 3/2026" in nums and "EHRA-Praxismitteilung 1/2008" in nums
    assert not any("alle" in s["title"] for s in de)        # the compilation is skipped
    first = next(s for s in de if s["doc_number"] == "EHRA-Praxismitteilung 3/2026")
    assert first["date"] == "2026-08-27"
    # Beilagen get their own id and take the Praxismitteilung's date
    beilage = next(s for s in de if s["doc_number"] == "EHRA-Praxismitteilung 1/2014 Beilage")
    assert beilage["ehra_id"] == "pm_2014_01_beilage_de" and beilage["date"] == "2014-10-03"
    assert len({s["ehra_id"] for s in de}) == len(de)
    it = ehra.parse_praxis(_read("ehra_praxis_it.html"), "it", "x")
    assert {s["doc_number"] for s in it} == set(nums)


def test_ehra_weisungen_numbered_from_german_and_untranslated_skipped():
    numbers = ehra.weisung_numbers(_read("ehra_weisungen_de.html"))
    de = ehra.parse_weisungen(_read("ehra_weisungen_de.html"), "de", "x", numbers)
    fr = ehra.parse_weisungen(_read("ehra_weisungen_fr.html"), "fr", "x", numbers)
    assert not any("BlueBook" in s["title"] for s in de)
    assert {s["doc_number"] for s in fr} <= {s["doc_number"] for s in de}
    # FR "Directive …" stays a Weisung; the 1998 Richtlinien stay Richtlinien
    types = {s["doc_number"]: s["doc_type"] for s in fr}
    assert types["EHRA Richtlinien des EHRA"] == "richtlinie"
    assert all(t == "weisung" for n, t in types.items() if n.startswith("EHRA Weisung"))
    # the German-only data-structure Weisung listed on the FR page is not an FR row
    assert not any(s["title"].startswith("Weisung betreffend Umsetzung") for s in fr)


# ── FINMA ──────────────────────────────────────────────────────────────

def test_finma_aufsichtsmitteilungen_items():
    items = json.loads((FIX / "finma_aufsichtsmitteilungen_api.json").read_text(encoding="utf-8"))["Items"]
    notices = fa.parse_am_items(items, False)
    n = notices[0]
    assert n["number"] == "FINMA-Aufsichtsmitteilung 05/2026"
    assert n["title"].endswith("Quantum Computing") and n["date"] == "2026-07-09"
    assert set(n["files"]) == {"de", "fr", "en"}
    assert fa.am_number("FINMA-Mitteilung 59 Aufsichtsrechtliche Konsequenzen") == "FINMA-Mitteilung 59"


def test_finma_bulletin_extracts_one_language_each():
    ex = fa.parse_bulletins(_read("finma_bulletins_en.html"))
    assert len(ex) == 60
    assert not any("Vollständige Publikation" in x["title"] or "Portrait" in x["title"] for x in ex)
    angebot = next(x for x in ex if x["title"] == "Angebotspflicht")
    assert angebot["bulletin"] == "FINMA-Bulletin 6/2017" and angebot["lang"] == "de"
    assert next(x for x in ex if x["title"].startswith("Interdiction"))["lang"] == "fr"
    assert any(x["bulletin"] == "FINMA-Sonderbulletin 1/2011" for x in ex)
    assert len({(x["bulletin"], x["index"]) for x in ex}) == len(ex)


# ── Foundation supervisors ─────────────────────────────────────────────

def test_esa_faq_and_templates():
    faq = sa.parse_faq(_read("esa_faq_de.html"))
    assert len(faq) == 104
    assert faq[0]["question"] == "Was ist eine Stiftung nach Schweizer Recht?"
    assert faq[0]["answer"].startswith("Bei der Stiftung handelt es sich")
    tmpl = sa.parse_links(_read("esa_mustervorlagen_de.html"), sa.ESA)
    assert [t["title"] for t in tmpl][0] == "Musterstatuten für klassische Stiftungen"


def test_bbsa_and_bsabb_filters():
    rs = sa.parse_links(_read("bbsa_rundschreiben_de.html"), sa.BBSA)
    assert len(rs) == 13 and all(sa._doc_type(r["title"]) == "rundschreiben" for r in rs)
    p = sa.PAGES[9]
    mb = sa.parse_links(_read("bbsa_merkblaetter_de.html"), sa.BBSA, p.include, p.exclude)
    assert all(r["url"].lower().endswith(".pdf") for r in mb)        # .docx templates skipped
    p = sa.PAGES[11]
    pubs = [r["title"] for r in sa.parse_links(_read("bsabb_publikationen.html"), sa.BSABB, p.include, p.exclude)]
    assert "Rechtsgutachten vom 3. April 2019 für die BSABB" in pubs
    assert not any(t.startswith(("Formular", "Medienmitteilung", "Fristerstreckung")) for t in pubs)


# ── Cantonal commercial registers ──────────────────────────────────────

def test_cantonal_register_pages():
    ge = hk.parse_page(_read("hr_ge_notes.html"), hk.PAGES[0])
    assert len(ge) == 10 and ge[0]["title"] == "Note aux notaires du 17 janvier 2025"
    vd = [x["title"] for x in hk.parse_page(_read("hr_vd_rc.html"), hk.PAGES[1])]
    # Dir_171c/171d are EHRA texts (in source 'ehra'); the OFRC communications too
    assert vd == ["Déclaration LFAIE : pratique vaudoise"]
    be = [x["title"] for x in hk.parse_page(_read("hr_be_ag_de.html"), hk.PAGES[2])]
    assert "Merkblatt: Verrechnungsliberierung" in be           # "Rechnung" inside the word
    assert not any(t.startswith(("Formular", "Muster Original Rechnung")) for t in be)
    be_fr = [x["title"] for x in hk.parse_page(_read("hr_be_ag_fr.html"), hk.PAGES[3])]
    assert any("exigences formelles" in t for t in be_fr)
    assert not any(t.startswith("Réquisition d’inscription-type") for t in be_fr)


# ── shared dedup ───────────────────────────────────────────────────────

class _Fake(rab_rs.RabRundschreibenScraper):
    TEXTS: dict[str, str] = {}

    def fetch_pdf_text(self, pdf_url):  # offline
        return self.TEXTS.get(pdf_url, "")


def _stub(url, lang, rid):
    return {"pdf_url": url, "title": "t", "doc_number": "RAB-RS 1/2007", "date": "2024-01-01",
            "language": lang, "rab_id": rid}


def test_dedup_drops_a_copy_and_remembers_stored_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(_Fake, "OUTPUT_DIR", tmp_path)
    _Fake.TEXTS = {"de.pdf": "Rundschreiben deutsch", "fr.pdf": "Circulaire français",
                   "it.pdf": "Rundschreiben deutsch"}           # IT link serves the German file
    s = _Fake()
    out = [s.with_body(_stub(u, l, f"x_{l}"), "RAB-RS 1/2007") for u, l in
           (("de.pdf", "de"), ("fr.pdf", "fr"), ("it.pdf", "it"))]
    assert out[0]["body_text"] == "Rundschreiben deutsch" and out[1] and out[2] is None
    # a later run: the German row is known (no download), the copy is still refused
    for o in out[:2]:
        s._append(s._normalize(o, o["body_text"]))
    s2 = _Fake()
    again = s2.with_body(_stub("de.pdf", "de", "x_de"), "RAB-RS 1/2007")
    assert "body_text" not in again                               # skipped by run(), not refetched
    assert s2.with_body(_stub("it.pdf", "it", "x_it"), "RAB-RS 1/2007") is None


def test_sources_registered():
    for key in ("uek_rs", "rab_rs", "ehra", "finma_aufsicht", "stiftungsaufsicht", "hr_kantone"):
        assert key in ENABLED_SCRAPERS
