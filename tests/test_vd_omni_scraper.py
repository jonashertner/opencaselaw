"""Offline tests for the Vaud CDAP scraper (scrapers/cantonal/vd_omni.py).

Golden pages saved from jurisprudence.vd.ch on 2026-11-08 (invariant #8):
  * vd_omni_listing_GE.2025.0357_*: one docket, three rulings: a CDAP arrêt
    (2026-04-09), the Federal Supreme Court's ruling filed under the cantonal
    docket (TF, 2026-09-08) and a CDAP ruling on costs after the remand
    (2026-10-14);
  * vd_omni_listing_1995_03_excerpt_*: five Tribunal administratif rulings;
  * vd_omni_listing_empty_1960_*: a zero-hit window ("0 de 0 fiche(s)");
  * vd_omni_error_*: what the portal prints for a failed call;
  * vd_omni_document_AC.2026.0320_*: a 2026 CDAP ruling (div.WordSection1);
  * vd_omni_document_1995_45734_*: a 1995 TA ruling (div.Section1).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.cantonal import vd_omni as V  # noqa: E402

FIX = REPO / "tests" / "fixtures"


def _fx(name: str) -> str:
    # The portal serves ISO-8859-1 and says so; requests decodes it the same way.
    return (FIX / name).read_bytes().decode("latin-1")


LISTING_GE = _fx("vd_omni_listing_GE.2025.0357_20261108.html")
LISTING_1995 = _fx("vd_omni_listing_1995_03_excerpt_20261108.html")
LISTING_EMPTY = _fx("vd_omni_listing_empty_1960_20261108.html")
ERROR_PAGE = _fx("vd_omni_error_20261108.html")
DOC_2026 = _fx("vd_omni_document_AC.2026.0320_20261108.html")
DOC_1995 = _fx("vd_omni_document_1995_45734_20261108.html")


class _Resp:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


# What the portal sent for the 2009-06 window on one try out of two (2026-11-08).
OMNIS_302 = _Resp("", 302, {"Location": "http://jurisprudence.vd.chAn error occurred while executing a method"})


def _october_only(url, data=None, **k):
    """The GE.2025.0357 page for the October 2026 window, nothing elsewhere."""
    return _Resp(LISTING_GE if (data or {}).get("dEntscheiddatum") == "01.10.2026" else LISTING_EMPTY)


def _scraper(tmp_path, monkeypatch, es_rows=(), own_rows=()):
    es = tmp_path / "es_vd_omni.jsonl"
    es.write_text("".join(json.dumps(r) + "\n" for r in es_rows), encoding="utf-8")
    own = tmp_path / "vd_omni.jsonl"
    own.write_text("".join(json.dumps(r) + "\n" for r in own_rows), encoding="utf-8")
    monkeypatch.setenv(V.ES_SHARD_ENV, str(es))
    monkeypatch.setenv(V.SHARD_ENV, str(own))
    monkeypatch.delenv(V.REFETCH_ENV, raising=False)
    s = V.VDOmniScraper(state_dir=tmp_path / "state")
    s.RETRY_PAUSES = (0, 0)
    return s


# ── listing ─────────────────────────────────────────────────────────────


def test_hit_counter_on_full_empty_and_error_pages():
    assert V.parse_hit_count(LISTING_GE) == 3
    assert V.parse_hit_count(LISTING_1995) == 123  # the window's total, page holds 5
    assert V.parse_hit_count(LISTING_EMPTY) == 0
    assert V.parse_hit_count(ERROR_PAGE) is None


def test_listing_rows_carry_authority_date_and_metadata():
    rows = V.parse_listing(LISTING_GE)
    assert [(r["docket_number"], r["authority"], str(r["decision_date"])) for r in rows] == [
        ("GE.2025.0357", "CDAP", "2026-10-14"),
        ("GE.2025.0357", "TF", "2026-09-08"),
        ("GE.2025.0357", "CDAP", "2026-04-09"),
    ]
    costs = rows[0]
    assert costs["nf30_key"] == "187044"
    assert costs["parties"].startswith("A.________/Direction générale des affaires institutionnelles")
    assert costs["resume"].startswith("Fixation des dépens pour la procédure devant le Tribunal cantonal")
    assert (costs["judge"], costs["clerk"]) == ("ATZ", "ESN")
    assert str(costs["publication_date"]) == "2026-11-04"
    assert "DÉPENS" in costs["keywords"]
    assert "LAVI-30-1" in costs["provisions"] and "LAVI-30-1" not in costs["keywords"]
    assert costs["url"] == V.doc_url("187044")
    assert "W10_KEY" not in costs["url"]


def test_listing_of_tribunal_administratif_rows():
    rows = V.parse_listing(LISTING_1995)
    assert len(rows) == 5
    assert {r["authority"] for r in rows} == {"TA"}
    assert all(r["decision_date"] and r["decision_date"].year == 1995 for r in rows)
    assert all(V.RE_DOCKET.match(r["docket_number"]) for r in rows)


# ── document ────────────────────────────────────────────────────────────


def test_document_2026_cdap():
    d = V.parse_document(DOC_2026)
    assert d["docket_number"] == "AC.2026.0320"
    assert d["authority"] == "CDAP"
    assert d["decision_date"] == date(2026, 10, 28)
    assert d["judge"] == "ATZ"
    assert d["resume"] == "Irrecevabilité du recours pour défaut de paiement de l'avance de frais."
    assert d["parties"] == "A.________/Municipalité de Lutry, B.________"
    assert "COUR DE DROIT ADMINISTRATIF ET PUBLIC" in d["full_text"]
    assert "Lausanne, le 28 octobre 2026" in d["full_text"]
    # the résumé box is not part of the ruling's text
    assert not d["full_text"].startswith("Irrecevabilité")


def test_document_1995_tribunal_administratif_uses_section1():
    d = V.parse_document(DOC_1995)
    assert d["docket_number"] == "RE.1994.0053"
    assert d["authority"] == "TA"
    assert d["decision_date"] == date(1995, 3, 31)
    assert len(d["full_text"]) > 1000


def test_document_text_spans_every_word_section():
    # Word starts a new section div at a page-layout change; the text goes on.
    html = DOC_2026.replace(
        "<p class=\"Voiederecours\">",
        "</div><div class=\"WordSection2\"><p>Annexe : plan de situation.</p></div>"
        "<div class=\"WordSection3\"><p class=\"Voiederecours\">", 1)
    d = V.parse_document(html)
    assert "Annexe : plan de situation." in d["full_text"]
    assert "Lausanne, le 28 octobre 2026" in d["full_text"]
    assert d["full_text"].rstrip().endswith(V.parse_document(DOC_2026)["full_text"].rstrip()[-60:])


# ── windows ─────────────────────────────────────────────────────────────


def test_month_windows_cover_history_without_gaps():
    w = V.month_windows(date(1800, 1, 1), date(1991, 3, 15))
    assert w == [
        (date(1800, 1, 1), date(1990, 12, 31)),
        (date(1991, 1, 1), date(1991, 1, 31)),
        (date(1991, 2, 1), date(1991, 2, 28)),
        (date(1991, 3, 1), date(1991, 3, 15)),
    ]
    w = V.month_windows(date(2024, 12, 10), date(2025, 1, 5))
    assert w == [(date(2024, 12, 10), date(2024, 12, 31)), (date(2025, 1, 1), date(2025, 1, 5))]


def test_nightly_default_is_24_monthly_requests_or_more(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    forms = []
    monkeypatch.setattr(s, "post", lambda url, data=None, **k: (forms.append(data), _Resp(LISTING_EMPTY))[1])
    assert list(s.discover_new()) == []
    assert len(forms) >= V.NIGHTLY_MONTHS  # enough for the >=3 silent-failure rule


# ── identity ────────────────────────────────────────────────────────────

ES_ROW = {"decision_id": "vd_omni_GE.2025.0357", "docket_number": "GE.2025.0357",
          "decision_date": "2026-04-09", "source_url": "http://www.jurisprudence.vd.ch/scripts/x?Aufruf=getMarkupDocument"}


def test_first_ruling_of_a_docket_takes_the_plain_id(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    assert s.resolve("AC.2026.0235", date(2026, 9, 1), "187000") == ("vd_omni_AC.2026.0235", None)
    # the same ruling again (same nF30) is the reserved one
    assert s.resolve("AC.2026.0235", date(2026, 9, 1), "187000")[0] == "vd_omni_AC.2026.0235"
    # another ruling of the docket on another date
    assert s.resolve("AC.2026.0235", date(2026, 10, 2), "187100") == ("vd_omni_AC.2026.0235_d20261002", None)
    # and one more on that same date
    assert s.resolve("AC.2026.0235", date(2026, 10, 2), "187101") == ("vd_omni_AC.2026.0235_n187101", None)


def test_es_row_of_the_same_docket_and_date_is_held_under_its_id(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW])
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182") == ("vd_omni_GE.2025.0357", "es")
    # a later ruling of the same docket never takes the es row's id
    assert s.resolve("GE.2025.0357", date(2026, 10, 14), "187044") == ("vd_omni_GE.2025.0357_d20261014", None)


def test_an_es_row_holds_only_one_of_several_same_date_rulings(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW])
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182") == ("vd_omni_GE.2025.0357", "es")
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186183") == ("vd_omni_GE.2025.0357_d20260409", None)
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182")[1] == "es"  # stable on a re-list


def test_fetch_retries_an_intermittent_error(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    answers = iter([OMNIS_302, _Resp(DOC_2026)])
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: next(answers))
    stub = {"decision_id": "vd_omni_AC.2026.0320", "docket_number": "AC.2026.0320",
            "nf30_key": "187086", "authority": "CDAP", "decision_date": date(2026, 10, 28),
            "url": V.doc_url("187086")}
    assert s.fetch_decision(stub).docket_number == "AC.2026.0320"


def _wrong_date_es_row():
    """The frozen copy of the 2026-04-09 arrêt, dated a day off, as GE.2025.0191
    really is (2025-01-06 in es_vd_omni.jsonl, 2026-01-06 on the portal)."""
    arret = next(r for r in V.parse_listing(LISTING_GE) if r["nf30_key"] == "186182")
    return {"decision_id": "vd_omni_GE.2025.0357", "docket_number": "GE.2025.0357",
            "decision_date": "2026-04-08",
            "regeste": f"{arret['parties']} | {arret['resume']}"}


def test_es_row_with_a_wrong_date_is_recognised_by_its_resume(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[_wrong_date_es_row()])
    monkeypatch.setattr(s, "post", _october_only)
    gen = s.discover_new(date(2026, 10, 1))
    first = next(gen)  # the report is on disk before the first ruling is fetched (--max, timeouts)
    assert (tmp_path / "state" / "vd_omni.es_date_mismatches.tsv").exists()
    # only the costs ruling of 2026-10-14 is new; the arrêt is the es row
    assert [first["decision_id"], *[x["decision_id"] for x in gen]] == ["vd_omni_GE.2025.0357_d20261014"]
    assert s.tally[(2026, "held_es_other_date")] == 1
    report = (tmp_path / "state" / "vd_omni.es_date_mismatches.tsv").read_text().splitlines()
    assert report == ["es_decision_id\tes_date\tportal_date\tnF30_KEY",
                      "vd_omni_GE.2025.0357\t2026-04-08\t2026-04-09\t186182"]
    # a second run keeps the report at one line per es row
    again = V.VDOmniScraper(state_dir=tmp_path / "state")
    again.RETRY_PAUSES = (0, 0)
    monkeypatch.setattr(again, "post", _october_only)
    list(again.discover_new(date(2026, 10, 1)))
    assert len((tmp_path / "state" / "vd_omni.es_date_mismatches.tsv").read_text().splitlines()) == 2


def test_the_placeholder_resume_never_counts_as_the_same_ruling(tmp_path, monkeypatch):
    # AC.1990.6992: the es row and the portal both say just "Résumé"
    row = dict(_wrong_date_es_row(), regeste="X c/ Y | Résumé")
    s = _scraper(tmp_path, monkeypatch, es_rows=[row])
    did, held = s.resolve("GE.2025.0357", date(2026, 4, 9), "186182", "Résumé")
    assert held is None and did == "vd_omni_GE.2025.0357_d20260409"


def test_the_es_rows_own_header_date_identifies_the_ruling(tmp_path, monkeypatch):
    # AC.1990.6992: field 1990-08-06 (the decision under appeal), header and
    # portal 1991-07-10, résumé only "Résumé"; same text on both sides.
    row = {"decision_id": "vd_omni_GE.2025.0357", "docket_number": "GE.2025.0357",
           "decision_date": "2025-12-01", "regeste": "X c/ Y | Résumé",
           "full_text": "Vaud Tribunal cantonal Cour de droit administratif et public 09.04.2026 "
                        "GE.2025.0357\n\nRésumé\n\nTRIBUNAL CANTONAL ..."}
    s = _scraper(tmp_path, monkeypatch, es_rows=[row])
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182", "Résumé") == ("vd_omni_GE.2025.0357", "es_other_date")
    assert s.es_date_mismatches == [("vd_omni_GE.2025.0357", "2025-12-01", "2026-04-09", "186182")]
    assert V.es_header_date(row["full_text"], "GE.2025.0357") == "2026-04-09"
    assert V.es_header_date(row["full_text"], "GE.2025.0358") == ""


def test_a_short_identical_resume_does_count(tmp_path, monkeypatch):
    # AC.1990.1144: es 1992-02-25, portal 1992-02-24, same 48-letter résumé
    res = "Etendue de l'obligation d'entretien en cas de déneigement"
    row = dict(_wrong_date_es_row(), regeste=f"X c/ Y | {res}")
    s = _scraper(tmp_path, monkeypatch, es_rows=[row])
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182", res)[1] == "es_other_date"


def test_a_date_match_beats_a_resume_match_anywhere_in_the_walk(tmp_path, monkeypatch):
    # PS.1996.0189: the es row (1997-01-20) shares its résumé with the
    # 1997-01-09 ruling but its date with the 1997-01-20 one. Walked oldest
    # first, a one-pass resolver gave it to 01-09; the date must win.
    page = LISTING_GE.replace("TF &nbsp;08.09.2026", "CDAP &nbsp;14.10.2026")  # two Oct-14 headnotes
    arret = next(r for r in V.parse_listing(LISTING_GE) if r["nf30_key"] == "186182")
    es = {"decision_id": "vd_omni_GE.2025.0357", "docket_number": "GE.2025.0357",
          "decision_date": "2026-10-14", "regeste": f"x | {arret['resume']}"}
    s = _scraper(tmp_path, monkeypatch, es_rows=[es])
    monkeypatch.setattr(s, "post", lambda url, data=None, **k: _Resp(
        page if data["dEntscheiddatum"] == "01.10.2026" else LISTING_EMPTY))
    stubs = list(s.discover_new(date(2026, 4, 1)))  # Apr..Nov: the Apr-09 page is answered in Oct here
    assert [x["decision_id"] for x in stubs] == ["vd_omni_GE.2025.0357_d20260409"]
    assert s.tally[(2026, "held_es")] == 1 and s.es_date_mismatches == []


def test_one_ruling_filed_once_per_headnote_is_one_decision(tmp_path, monkeypatch):
    # GE.2010.0088 (2011-09-01) is three fiches with one text and three
    # résumés; here the TF fiche is relabelled as a second headnote of the
    # 2026-10-14 ruling.
    page = LISTING_GE.replace("TF &nbsp;08.09.2026", "CDAP &nbsp;14.10.2026")
    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "post", lambda url, data=None, **k: _Resp(
        page if data["dEntscheiddatum"] == "01.10.2026" else LISTING_EMPTY))
    stubs = list(s.discover_new(date(2026, 10, 1)))
    oct14 = [x for x in stubs if str(x["decision_date"]) == "2026-10-14"]
    assert len(oct14) == 1
    st = oct14[0]
    assert st["nf30_keys"] == ["186844", "187044"] and st["nf30_key"] == "186844"
    assert st["resume"].startswith("Les avis médicaux ultérieurs")
    assert "\n\nFixation des dépens" in st["resume"]
    assert s.tally[(2026, "listed")] == 3 and s.tally[(2026, "rulings")] == 2
    # either fiche of the ruling is the held one on the next run
    assert s.resolve("GE.2025.0357", date(2026, 10, 14), ["187044"])[0] == st["decision_id"]


def test_discover_skips_federal_and_held_rulings(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW])
    monkeypatch.setattr(s, "post", _october_only)
    stubs = list(s.discover_new(date(2026, 10, 1)))  # one month window
    assert [(x["decision_id"], x["authority"]) for x in stubs] == [
        ("vd_omni_GE.2025.0357_d20261014", "CDAP")]
    assert s.skipped_federal == 1 and s.held_es == 1


def test_refetch_held_yields_the_es_row_under_its_own_id(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW])
    s.refetch_held = True
    monkeypatch.setattr(s, "post", _october_only)
    ids = [x["decision_id"] for x in s.discover_new(date(2026, 10, 1))]
    assert ids == ["vd_omni_GE.2025.0357", "vd_omni_GE.2025.0357_d20261014"]


def test_refetch_ids_fetches_just_those_held_rulings(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW])
    s.refetch_ids = {"vd_omni_GE.2025.0357"}
    monkeypatch.setattr(s, "post", _october_only)
    ids = [x["decision_id"] for x in s.discover_new(date(2026, 10, 1))]
    assert ids == ["vd_omni_GE.2025.0357", "vd_omni_GE.2025.0357_d20261014"]


def test_persisted_ruling_is_held_on_the_next_run(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "post", _october_only)
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(DOC_2026.replace("AC.2026.0320", "GE.2025.0357")))
    stubs = list(s.discover_new(date(2026, 10, 1)))
    assert [x["decision_id"] for x in stubs] == ["vd_omni_GE.2025.0357", "vd_omni_GE.2025.0357_d20261014"]
    for st in stubs:
        dec = s.fetch_decision(st)
        s.state.mark_scraped(dec.decision_id)
        s.on_decision_persisted(dec)
    again = V.VDOmniScraper(state_dir=tmp_path / "state")
    again.RETRY_PAUSES = (0, 0)
    monkeypatch.setattr(again, "post", _october_only)
    assert list(again.discover_new(date(2026, 10, 1))) == []
    lines = (tmp_path / "state" / "vd_omni.rulings.tsv").read_text().splitlines()
    assert any(ln.startswith("vd_omni_GE.2025.0357\t2026-04-09\t186182\tvd_omni_GE.2025.0357\tdirect")
               for ln in lines)


def test_sidecar_is_seeded_from_the_es_and_own_shards(tmp_path, monkeypatch):
    own = {"decision_id": "vd_omni_AC.2026.0235", "docket_number": "AC.2026.0235",
           "decision_date": "2026-09-01", "source_url": V.doc_url("187000")}
    s = _scraper(tmp_path, monkeypatch, es_rows=[ES_ROW, {"decision_id": "vd_omni_", "docket_number": ""}],
                 own_rows=[own])
    assert s.resolve("AC.2026.0235", date(2026, 9, 1), "187000") == ("vd_omni_AC.2026.0235", "direct")
    assert s.resolve("GE.2025.0357", date(2026, 4, 9), "186182")[1] == "es"
    assert "vd_omni_" not in s._rulings  # the docket-less es row seeds nothing


# ── fetch ───────────────────────────────────────────────────────────────


def test_fetch_builds_a_full_decision(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(DOC_2026))
    stub = {"decision_id": "vd_omni_AC.2026.0320", "docket_number": "AC.2026.0320",
            "nf30_key": "187086", "authority": "CDAP", "decision_date": date(2026, 10, 28),
            "publication_date": date(2026, 11, 4), "parties": None, "resume": None,
            "judge": "ATZ", "clerk": None, "url": V.doc_url("187086")}
    d = s.fetch_decision(stub)
    assert d.court == "vd_omni" and d.canton == "VD" and d.chamber == "CDAP"
    assert d.decision_id == "vd_omni_AC.2026.0320"
    assert d.decision_date == date(2026, 10, 28) and d.publication_date == date(2026, 11, 4)
    assert d.language == "fr"
    assert d.regeste == "Irrecevabilité du recours pour défaut de paiement de l'avance de frais."
    assert d.title == "A.________/Municipalité de Lutry, B.________"
    assert d.judges == "ATZ"
    assert d.source_url == V.doc_url("187086") and d.pdf_url is None


def test_fetch_of_an_error_page_or_a_mismatched_docket_returns_none(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    stub = {"decision_id": "vd_omni_AC.2026.0999", "docket_number": "AC.2026.0999",
            "nf30_key": "1", "authority": "CDAP", "decision_date": date(2026, 10, 28),
            "url": V.doc_url("1")}
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(ERROR_PAGE))
    assert s.fetch_decision(stub) is None
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(DOC_2026))  # says AC.2026.0320
    assert s.fetch_decision(stub) is None


# A registry card with no markup body: the shape the live portal served for
# ~200 old TA fiches (CR/PE/PS/RE dockets, 1991-92) confirmed 2026-11-10 --
# neither getMarkupDocument nor the print preview (Aufruf=getDocument,
# Template=.../printdocument.fiw) carries a Section/WordSection div for them,
# only "N° affaire" / "Autorité:, Date décision" / ... / "Résumé contenant".
def _card_only(docket: str, authority: str, ddate: str, resume: str) -> str:
    return (
        "<html><body><table><tr><td>N° affaire:</td><td>" + docket + "</td></tr>"
        "<tr><td>Autorité:, Date décision:</td><td>" + authority + ", " + ddate + "</td></tr>"
        "<tr><td>Juge:</td><td>PJ</td></tr><tr><td>Greffier:</td><td>NK</td></tr>"
        "<tr><td>Publication (revue juridique):</td><td>&nbsp;</td></tr>"
        "<tr><td>Ref. TF:</td><td>&nbsp;</td></tr>"
        "<tr><td>Nom des parties contenant: </td><td>c/ SA</td></tr>"
        "<tr><td>Résumé contenant:</td><td>" + resume + "</td></tr>"
        "</table></body></html>" + " " * 500  # past the html-length-500 floor
    )


CARD_ONLY_WITH_RESUME = _card_only("CR.1990.0190", "TA", "13.01.1992", "RPC huit mois")
CARD_ONLY_PLACEHOLDER = _card_only("RE.1991.0016", "TA", "10.01.1992", "Résumé")


def test_a_card_only_fiche_with_a_real_resume_is_held_not_dropped(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(CARD_ONLY_WITH_RESUME))
    stub = {"decision_id": "vd_omni_CR.1990.0190", "docket_number": "CR.1990.0190",
            "nf30_key": "33836", "authority": "TA", "decision_date": date(1992, 1, 13),
            "publication_date": None, "parties": None, "resume": None,
            "judge": None, "clerk": None, "url": V.doc_url("33836")}
    d = s.fetch_decision(stub)
    assert d is not None
    assert d.full_text == V.CARD_ONLY_LABEL + "RPC huit mois"
    assert d.full_text.startswith("[Fiche du registre seulement")
    assert d.regeste == "RPC huit mois"
    assert d.docket_number == "CR.1990.0190" and d.decision_date == date(1992, 1, 13)


def test_a_card_only_fiche_with_only_the_placeholder_resume_is_still_none(tmp_path, monkeypatch, caplog):
    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "get", lambda url, allow_redirects=True, **k: _Resp(CARD_ONLY_PLACEHOLDER))
    stub = {"decision_id": "vd_omni_RE.1991.0016", "docket_number": "RE.1991.0016",
            "nf30_key": "45592", "authority": "TA", "decision_date": date(1992, 1, 10),
            "publication_date": None, "parties": None, "resume": None,
            "judge": None, "clerk": None, "url": V.doc_url("45592")}
    with caplog.at_level(logging.WARNING):
        assert s.fetch_decision(stub) is None
    assert "no markup body and no usable résumé" in caplog.text


def test_usable_resume_rejects_blank_and_the_bare_placeholder():
    assert V._usable_resume(None) is None
    assert V._usable_resume("") is None
    assert V._usable_resume("   ") is None
    assert V._usable_resume("Résumé") is None
    assert V._usable_resume("  Résumé  ") is None   # stripped before comparing
    assert V._usable_resume("RPC huit mois") == "RPC huit mois"


def test_the_scraper_caches_none_returns_as_gaps_with_headroom_above_the_default():
    """So a cluster of card-only, résumé-less fiches early in a since=1800
    backfill (fetched oldest first) cannot trip the generic 200-None breaker
    and abort the whole run, and a nightly run never re-probes a confirmed one."""
    assert V.VDOmniScraper.CACHE_NONE_AS_GAP is True
    assert V.VDOmniScraper.MAX_NONE_RETURNS > 200


def test_an_intermittent_portal_error_is_retried_without_a_health_error(tmp_path, monkeypatch, caplog):
    s = _scraper(tmp_path, monkeypatch)
    calls = {"n": 0}

    def flaky(url, data=None, allow_redirects=True, **k):
        assert allow_redirects is False  # never resolve the error "host"
        calls["n"] += 1
        if data.get("dEntscheiddatum") == "01.10.2026" and calls["n"] == 1:
            return OMNIS_302
        return _october_only(url, data)

    monkeypatch.setattr(s, "post", flaky)
    with caplog.at_level(logging.WARNING, logger=V.__name__):
        ids = [x["decision_id"] for x in s.discover_new(date(2026, 10, 1))]
    assert ids == ["vd_omni_GE.2025.0357", "vd_omni_GE.2025.0357_d20261014"]
    text = caplog.text
    assert "search failed" not in text and "Max retries exceeded" not in text
    assert "attempt 1 of 3 failed (HTTP 302" in text


def test_a_window_that_keeps_failing_is_split_before_it_is_given_up(tmp_path, monkeypatch):
    s = _scraper(tmp_path, monkeypatch)
    seen = []

    def month_fails(url, data=None, **k):
        seen.append((data["dEntscheiddatum"], data["dEntscheiddatumBis"]))
        if (data["dEntscheiddatum"], data["dEntscheiddatumBis"]) == ("01.10.2026", "31.10.2026"):
            return OMNIS_302
        return _Resp(LISTING_EMPTY)

    monkeypatch.setattr(s, "post", month_fails)
    assert list(s.discover_new(date(2026, 10, 1))) == []
    assert seen.count(("01.10.2026", "31.10.2026")) == 3
    assert ("01.10.2026", "16.10.2026") in seen and ("17.10.2026", "31.10.2026") in seen


# ── health (invariant 7) ────────────────────────────────────────────────


def test_failed_listing_counts_as_a_discovery_error_in_scraper_health(tmp_path, monkeypatch):
    """A portal that answers every listing with its error page must come out
    of run_all_scrapers as FAILED, not as a quiet success with 0 new."""
    import run_all_scrapers as RA

    s = _scraper(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "post", lambda url, data=None, **k: _Resp(ERROR_PAGE))
    records: list[str] = []
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")  # run_scraper.py's

    class _H(logging.Handler):
        def emit(self, record):
            records.append(fmt.format(record))

    h = _H()
    logging.getLogger(V.__name__).addHandler(h)
    try:
        assert list(s.discover_new()) == []
    finally:
        logging.getLogger(V.__name__).removeHandler(h)
    log = "\n".join(records) + "\n2026-11-08 01:00:00 run_scraper INFO [vd_omni] Done. +0 new, 0, Errors: 0, NoneReturns: 0, Time: 1.0 min\n"

    monkeypatch.setattr(RA, "REPO_DIR", tmp_path)

    class _Done:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kw):
        (tmp_path / "logs").mkdir(exist_ok=True)
        (tmp_path / "logs" / "vd_omni.log").write_text(log, encoding="utf-8")
        return _Done()

    monkeypatch.setattr(RA.subprocess, "run", fake_run)
    res = RA.run_single_scraper("vd_omni", timeout=60)
    assert "stopping discovery" in log  # a dead portal does not cost 24 windows of retries
    assert res["discovery_errors"] >= 3
    assert res["success"] is False


def test_registered_and_no_longer_graded_as_entscheidsuche_only():
    import run_scraper
    assert run_scraper.SCRAPERS["vd_omni"] == ("scrapers.cantonal.vd_omni", "VDOmniScraper")
    sys.path.insert(0, str(REPO / "scripts"))
    import check_scraper_freshness as F
    assert "vd_omni" not in F.ENTSCHEIDSUCHE_ONLY
    policy = json.loads((REPO / "docs" / "intake_expected_stale.json").read_text())
    assert "vd_omni" not in policy["expected_stale"]
