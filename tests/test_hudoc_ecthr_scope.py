"""Scope and identity rules for the full-corpus ECtHR ingest (2026-08-26).

Three HUDOC behaviours cost us coverage before this change, and each of
them is silent — the scraper exits 0 and the health log looks clean:

  * placeholder rows: HUDOC lists a row per language but stores only the
    authoritative text, so the other returns 204 with an empty body;
  * unsorted deep paging: with sort="" HUDOC reorders between pages and a
    3,531-row query yielded 2,411 distinct itemids, losing 32%;
  * docket collisions: 158 application numbers in scope carry more than
    one judgment (merits, then just satisfaction years later), and keying
    on the application number alone dropped the later one via is_known.

The tests below pin all three, plus the language rules that let English
judgments into a corpus that is otherwise DE/FR/IT only.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Decision  # noqa: E402
from scrapers.hudoc import (  # noqa: E402
    _FULL_QUERY,
    HUDOCFullScraper,
    HUDOCScraper,
    _hudoc_docket,
)


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _listing_row(**over):
    row = {
        "itemid": "001-219065",
        "appno": "47358/20",
        "docname": "AFFAIRE C. c. ROUMANIE",
        "doctypebranch": "CHAMBER",
        "typedescription": "15",
        "languageisocode": "FRE",
        "importance": "1",
        "judgementdate": "30/08/2022 00:00:00",
        "kpdate": "2022-08-30T00:00:00",
        "ecli": "ECLI:CE:ECHR:2022:0830JUD004735820",
        "respondent": "ROU",
        "conclusion": "Violation de l'article 8",
        "violation": "8",
        "nonviolation": "",
        "article": "8;8-1",
    }
    row.update(over)
    return row


# ---------------------------------------------------------------- scope


def test_full_query_carries_the_whole_agreed_scope():
    assert "isplaceholder:False" in _FULL_QUERY
    assert "documentcollectionid2:JUDGMENTS" in _FULL_QUERY
    for imp in ("importance:1", "importance:2", "importance:3"):
        assert imp in _FULL_QUERY
    # importance 4 is the low/repetitive tier and the entire Committee
    # docket. Jonas's scope decision was explicitly "except 4".
    assert "importance:4" not in _FULL_QUERY
    assert "languageisocode:FRE" in _FULL_QUERY
    assert "languageisocode:ENG" in _FULL_QUERY
    # Admissibility decisions stay out.
    assert "documentcollectionid2:DECISIONS" not in _FULL_QUERY


# ------------------------------------------------------- stable paging


def test_full_scraper_never_pages_unsorted(tmp_path, monkeypatch):
    """An empty sort silently loses ~32% of a multi-page result set."""
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    seen_sorts = []

    def fake_get(url, params=None, timeout=None, **kw):
        if params is None:  # the session warm-up hit on /eng
            return FakeResponse(text="ok")
        seen_sorts.append(params.get("sort"))
        return FakeResponse({"resultcount": 1, "results": [{"columns": _listing_row()}]})

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", fake_get)

    list(scraper.discover_new(since_date=date(2022, 1, 1)))

    assert seen_sorts, "discovery issued no listing request"
    assert all(s for s in seen_sorts), f"unsorted paging: {seen_sorts}"


def test_swiss_scraper_never_pages_unsorted(tmp_path, monkeypatch):
    scraper = HUDOCScraper(state_dir=tmp_path)
    seen_sorts = []

    def fake_get(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        seen_sorts.append(params.get("sort"))
        return FakeResponse({"resultcount": 1, "results": [{"columns": _listing_row()}]})

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", fake_get)

    list(scraper.discover_new())

    assert seen_sorts
    assert all(s for s in seen_sorts), f"unsorted paging: {seen_sorts}"


def test_short_shard_is_retried_once(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    attempts = {"n": 0}

    def fake_get(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        attempts["n"] += 1
        if attempts["n"] == 1:
            # claims 2 rows, hands back 1 — the short-read signature
            return FakeResponse({"resultcount": 2, "results": [{"columns": _listing_row()}]})
        return FakeResponse({
            "resultcount": 2,
            "results": [
                {"columns": _listing_row()},
                {"columns": _listing_row(itemid="001-219066", ecli="ECLI:X", appno="1/20")},
            ],
        })

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", fake_get)

    rows = scraper._discover_year(2022)
    assert attempts["n"] == 2, "a short shard must be retried"
    assert len(rows) == 2


# ------------------------------------------------------------- dockets


def test_docket_is_first_application_number_plus_date():
    assert _hudoc_docket("47358/20", date(2022, 8, 30), "001-1") == "47358/20_20220830"


def test_merits_and_just_satisfaction_do_not_collide():
    """158 application numbers in scope carry more than one judgment."""
    merits = _hudoc_docket("30808/11", date(2016, 3, 10), "001-1")
    satisfaction = _hudoc_docket("30808/11", date(2018, 6, 21), "001-2")
    assert merits != satisfaction


# ------------------------------------------------- language selection


def test_group_prefers_french_and_keeps_english_as_fallback():
    fr = _listing_row(itemid="001-FR", languageisocode="FRE")
    en = _listing_row(itemid="001-EN", languageisocode="ENG",
                      docname="CASE OF C. v. ROMANIA")
    stubs = HUDOCFullScraper._group_judgments({"001-FR": fr, "001-EN": en})

    assert len(stubs) == 1, "one judgment must not become two rows"
    stub = stubs[0]
    assert stub["item_id"] == "001-FR"
    assert stub["lang_iso"] == "FRE"
    assert stub["alt_item_id"] == "001-EN"
    assert stub["court"] == "ecthr_chamber"
    assert stub["decision_id"] == "ecthr_chamber_47358_20_20220830"


def test_english_only_judgment_is_still_ingested():
    en = _listing_row(itemid="001-EN", languageisocode="ENG")
    stubs = HUDOCFullScraper._group_judgments({"001-EN": en})
    assert len(stubs) == 1
    assert stubs[0]["lang_iso"] == "ENG"
    assert stubs[0]["alt_item_id"] == ""


def test_grand_chamber_maps_to_its_own_court():
    row = _listing_row(doctypebranch="GRANDCHAMBER")
    stubs = HUDOCFullScraper._group_judgments({row["itemid"]: row})
    assert stubs[0]["court"] == "ecthr_grand_chamber"


# ----------------------------------------------------------- fetching


def _stub(**over):
    base = {
        "court": "ecthr_chamber",
        "decision_id": "ecthr_chamber_47358_20_20220830",
        "docket_number": "47358/20_20220830",
        "decision_date": date(2022, 8, 30),
        "item_id": "001-FR",
        "lang_iso": "FRE",
        "alt_item_id": "001-EN",
        "alt_lang_iso": "ENG",
        "appno": "47358/20",
        "docname": "AFFAIRE C. c. ROUMANIE",
        "doc_type": "15",
        "branch": "CHAMBER",
        "respondent": "ROU",
        "ecli": "ECLI:CE:ECHR:2022:0830JUD004735820",
        "article": "8;8-1",
        "conclusion": "Violation de l'article 8",
        "violation": "8",
        "nonviolation": "",
        "importance": "1",
    }
    base.update(over)
    return base


BODY = "<p>" + ("La Cour rappelle que l'article 8 de la Convention. " * 40) + "</p>"


def test_fetch_falls_back_to_english_when_the_french_row_is_empty(tmp_path, monkeypatch):
    """A 204 on the authoritative language must not lose the judgment."""
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    calls = []

    def fake_get(url):
        calls.append(url)
        if "001-FR" in url:
            return FakeResponse(text="", status_code=204)
        return FakeResponse(text=BODY, status_code=200)

    monkeypatch.setattr(scraper, "get", fake_get)
    decision = scraper.fetch_decision(_stub())

    assert decision is not None
    assert decision.language == "en"
    assert "001-EN" in decision.source_url
    assert len(calls) == 2


def test_fetch_returns_none_when_no_language_has_a_body(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(
        scraper, "get", lambda url: FakeResponse(text="", status_code=204)
    )
    assert scraper.fetch_decision(_stub()) is None


def test_french_judgment_keeps_french(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", lambda url: FakeResponse(text=BODY))
    decision = scraper.fetch_decision(_stub())
    assert decision.language == "fr"
    assert decision.canton == "CE"
    assert decision.decision_id == "ecthr_chamber_47358_20_20220830"


def test_swiss_respondent_keeps_canton_ch(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", lambda url: FakeResponse(text=BODY))
    decision = scraper.fetch_decision(_stub(respondent="CHE"))
    assert decision.canton == "CH"


# ------------------------------------------------------ the en schema


def test_decision_model_accepts_english():
    d = Decision(
        decision_id="ecthr_chamber_1_20200101",
        court="ecthr_chamber",
        canton="CE",
        docket_number="1/20_20200101",
        language="en",
        full_text="x" * 200,
        source_url="https://hudoc.echr.coe.int/eng?i=001-1",
    )
    assert d.language == "en"


@pytest.mark.parametrize("bad", ["es", "pt", "gsw"])
def test_decision_model_still_rejects_other_languages(bad):
    with pytest.raises(Exception):
        Decision(
            decision_id="ecthr_chamber_1_20200101",
            court="ecthr_chamber",
            canton="CE",
            docket_number="1/20_20200101",
            language=bad,
            full_text="x" * 200,
            source_url="https://hudoc.echr.coe.int/eng?i=001-1",
        )


# ------------------------------- en is confined to Strasbourg courts


def _db_with(rows):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decisions (language TEXT, court TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?, ?)", rows)
    return conn


def test_quality_gate_allows_en_on_ecthr_courts():
    from quality.checks.languages import check_unexpected_language_values

    conn = _db_with([("en", "ecthr_chamber"), ("fr", "bger"), ("de", "bge")])
    assert check_unexpected_language_values(conn).passed


def test_quality_gate_rejects_en_on_a_swiss_court():
    """An `en` row on a Swiss court is the same scraper bug it always was —
    widening the check to `en` corpus-wide would have hidden it."""
    from quality.checks.languages import check_unexpected_language_values

    conn = _db_with([("en", "bger"), ("de", "bge")])
    result = check_unexpected_language_values(conn)
    assert not result.passed
    assert result.sample_rows[0]["court"] == "bger"


def test_quality_gate_still_rejects_junk_languages():
    from quality.checks.languages import check_unexpected_language_values

    conn = _db_with([("xx", "ecthr_chamber"), ("de", "bge")])
    assert not check_unexpected_language_values(conn).passed


# ------------------------- the docket must still cite (R1 contract)
#
# `_ecthr_app_numbers` parses docket_number to build the application-number
# clause of a Strasbourg citation, and returns "" rather than guessing when
# the docket is unparseable. Appending the judgment date to the docket without
# teaching it to strip that date silently dropped "Nr. 47358/20" from all
# 8,275 citation strings — a regression in exactly the field R1 requires
# callers to copy verbatim.


def test_new_docket_still_yields_the_application_number():
    from mcp_server import _ecthr_app_numbers

    assert _ecthr_app_numbers(
        "ecthr_chamber", "47358/20_20220830", "de") == "Nr. 47358/20"


def test_two_application_numbers_are_both_named():
    from mcp_server import _ecthr_app_numbers

    assert _ecthr_app_numbers(
        "ecthr_chamber", "43868/18_25883/21_20220830", "de"
    ) == "Nr. 43868/18 und 25883/21"


def test_three_or_more_applications_collapse_to_u_a():
    from mcp_server import _ecthr_app_numbers

    assert _ecthr_app_numbers(
        "ecthr_chamber", "1474/62_1677/62_1691/62_19670209", "fr"
    ) == "n° 1474/62 et al."


def test_legacy_dockets_without_a_date_still_parse():
    """hudoc_ch and the pre-2026-08-26 ecthr rows carry bare numbers."""
    from mcp_server import _ecthr_app_numbers

    assert _ecthr_app_numbers("hudoc_ch", "16279/90", "de") == "Nr. 16279/90"
    assert _ecthr_app_numbers(
        "bge_egmr", "20201020_78630_12", "de") == "Nr. 78630/12"


def test_an_unparseable_docket_still_returns_empty():
    from mcp_server import _ecthr_app_numbers

    assert _ecthr_app_numbers("hudoc_ch", "001-25894", "de") == ""


def test_docket_keeps_at_most_three_application_numbers():
    """Turan and Others v. Turkey lists 395 of them; the citation builder
    renders at most three."""
    monster = ";".join(f"{n}/16" for n in range(75805, 76200))
    docket = _hudoc_docket(monster, date(2021, 11, 23), "001-213369")
    assert docket == "75805/16_75806/16_75807/16_20211123"
    assert len(docket) < 60


# ================= second review pass (2026-08-26) =====================
# Everything below fixes a defect found by an adversarial review of the
# change above, and each test names the defect it pins.


# ---- third-party translations are NOT excluded by the language filter ----
#
# The header originally claimed ENG/FRE excludes every translation. It does
# not: four English rows in scope are European Roma Rights Centre
# translations of French-authoritative judgments, and _group_judgments kept
# them as the English fetch fallback. Copyright sits with the translator.


def test_third_party_translations_are_dropped():
    from scrapers.hudoc import _THIRD_PARTY_TRANSLATION

    errc = ("CASE OF SAMPANIS AND OTHERS v. GREECE - [English Translation] "
            "by European Roma Rights Centre")
    assert _THIRD_PARTY_TRANSLATION.search(errc)

    fr = _listing_row(itemid="001-FR", languageisocode="FRE")
    trans = _listing_row(itemid="001-ERRC", languageisocode="ENG", docname=errc)
    stubs = HUDOCFullScraper._group_judgments({"001-FR": fr, "001-ERRC": trans})
    assert len(stubs) == 1
    assert stubs[0]["item_id"] == "001-FR"
    assert stubs[0]["alt_item_id"] == "", "a translation must not be the fallback either"


def test_a_translation_only_group_yields_nothing():
    errc = "CASE OF WALLOVÁ AND WALLA v. THE CZECH REPUBLIC - [English Translation] by ERRC"
    row = _listing_row(itemid="001-ERRC", languageisocode="ENG", docname=errc)
    assert HUDOCFullScraper._group_judgments({"001-ERRC": row}) == []


def test_court_apparatus_and_hyphenated_case_names_survive():
    """'[Extracts]' is the Court's own note, and four case names contain a
    bare ' - '. Neither is a translation."""
    from scrapers.hudoc import _THIRD_PARTY_TRANSLATION

    assert not _THIRD_PARTY_TRANSLATION.search("CASE OF X v. Y - [Extracts]")
    assert not _THIRD_PARTY_TRANSLATION.search(
        "AFFAIRE FILIPPOS MAVROPOULOS - PAN. ZISIS O.E. c. GRECE")


# ---- a retried shard must not throw away what the first attempt had ----
#
# HUDOC orders kpdate ties non-deterministically across a page boundary, so
# attempt 2 can be missing a row attempt 1 had. Replacing instead of merging
# loses it.


def test_retry_merges_with_the_first_attempt(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    calls = {"n": 0}
    a = _listing_row(itemid="001-A", appno="1/20", ecli="ECLI:A")
    b = _listing_row(itemid="001-B", appno="2/20", ecli="ECLI:B")

    def fake_get(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        calls["n"] += 1
        # each attempt returns a DIFFERENT single row of the two
        row = a if calls["n"] == 1 else b
        return FakeResponse({"resultcount": 2, "results": [{"columns": row}]})

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", fake_get)

    rows = scraper._discover_year(2022)
    assert set(rows) == {"001-A", "001-B"}, "attempts must be unioned, not replaced"


def test_a_lost_shard_is_counted_and_logged_as_a_search_failure(tmp_path, monkeypatch, caplog):
    """run_all_scrapers counts a discovery failure by matching ERROR lines
    containing 'search failed'. A year lost without that phrase exits 0 and
    is reported as a clean run."""
    import logging

    scraper = HUDOCFullScraper(state_dir=tmp_path)

    def boom(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        raise RuntimeError("connection reset")

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", boom)

    with caplog.at_level(logging.ERROR, logger="scrapers.hudoc"):
        scraper._discover_year(2022)

    assert scraper.shard_failures == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and all("search failed" in m for m in errors), errors


def test_short_shard_after_retry_is_counted(tmp_path, monkeypatch):
    scraper = HUDOCFullScraper(state_dir=tmp_path)

    def short(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        return FakeResponse({"resultcount": 5, "results": [{"columns": _listing_row()}]})

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", short)
    scraper._discover_year(2022)
    assert scraper.shard_failures == 1


# ---- the scope axis has to survive into serving ----


def test_key_cases_are_marked_for_publication(tmp_path, monkeypatch):
    """HUDOC importance 1 is the Court's own 'Key cases' selection. Without
    persisting it, a Key case is indistinguishable from a routine level-3
    Chamber judgment."""
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", lambda url: FakeResponse(text=BODY))
    assert scraper.fetch_decision(_stub(importance="1")).marked_for_publication is True
    assert scraper.fetch_decision(_stub(importance="3")).marked_for_publication is None


def test_regeste_carries_german_and_italian_convention_references(tmp_path, monkeypatch):
    """The corpus is DE/FR/IT and the retrieval stack has no lexical bridge
    from German to a French or English judgment; roughly half these rows are
    English-only. 'Art. 8 EMRK' has to appear somewhere FTS5 indexes."""
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    monkeypatch.setattr(scraper, "get", lambda url: FakeResponse(text=BODY))
    d = scraper.fetch_decision(_stub(article="8;8-1;41"))
    assert "EMRK" in d.regeste and "CEDU" in d.regeste
    assert "Art. 8" in d.regeste and "Art. 41" in d.regeste
    assert "EGMR" in d.regeste
    # and the Court's own conclusion is still there, first
    assert d.regeste.startswith("Violation de l'article 8")


def test_convention_keywords_handle_protocols_and_empty_input():
    from scrapers.hudoc import _ecthr_convention_keywords

    assert _ecthr_convention_keywords("P1-1;6") == (
        "[EGMR / CourEDH / CorteEDU; Art. 6 EMRK / CEDH / CEDU; "
        "Art. 1 ZP 1 EMRK / Prot. 1 CEDH / CEDU]")
    assert _ecthr_convention_keywords("") == "[EGMR / CourEDH / CorteEDU]"
    # paragraph sub-numbers collapse into their article, in first-seen order
    assert "Art. 10, Art. 8" in _ecthr_convention_keywords("10;10-1;10-2;8;8-1")


# ---- a bare application number must still find its judgment ----
#
# The date suffix removed the exact docket_number hit that used to serve
# every ECtHR lookup, which pushed it onto the LIKE '%x%' full-table scan.


def _appno_db():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decisions (decision_id TEXT, docket_number TEXT, "
                 "decision_date TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?,?,?)", [
        ("ecthr_chamber_47358_20_20220830", "47358/20_20220830", "2022-08-30"),
        ("ecthr_chamber_30808_11_20160310", "30808/11_20160310", "2016-03-10"),
        ("ecthr_chamber_30808_11_20180621", "30808/11_20180621", "2018-06-21"),
    ])
    return conn


def test_bare_application_number_resolves():
    from mcp_server import _lookup_ecthr_appno

    assert _lookup_ecthr_appno(_appno_db(), "47358/20") == [
        "ecthr_chamber_47358_20_20220830"]


def test_ambiguous_application_number_returns_all_earliest_first():
    """158 application numbers name more than one judgment. Callers resolve
    only a unique hit — this must not silently pick one."""
    from mcp_server import _lookup_ecthr_appno

    ids = _lookup_ecthr_appno(_appno_db(), "30808/11")
    assert len(ids) == 2
    assert ids[0].endswith("20160310"), "merits (earliest) first"


def test_appno_lookup_ignores_non_application_numbers():
    from mcp_server import _lookup_ecthr_appno

    for ref in ("6B_1234/2020", "BGE 140 III 86", "", None, "001-25894"):
        assert _lookup_ecthr_appno(_appno_db(), ref) == []


def test_appno_lookup_cannot_be_widened_by_a_neighbouring_docket():
    """The range predicate must not spill into '47358/200...' style dockets."""
    import sqlite3
    from mcp_server import _lookup_ecthr_appno

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decisions (decision_id TEXT, docket_number TEXT, "
                 "decision_date TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?,?,?)", [
        ("a", "47358/20_20220830", "2022-08-30"),
        ("b", "47358/200_20220830", "2022-08-30"),
        ("c", "47358/20x_20220830", "2022-08-30"),
    ])
    assert _lookup_ecthr_appno(conn, "47358/20") == ["a"]


# ---- the key suffix must never reach a reader ----


def test_public_display_docket_drops_the_date_suffix():
    import ecthr_docket

    assert ecthr_docket.display_docket("ecthr_chamber", "47358/20_20220830") == "47358/20"
    assert ecthr_docket.display_docket(
        "ecthr_grand_chamber", "1474/62_1677/62_19670209") == "1474/62_1677/62"


def test_display_docket_leaves_other_courts_alone():
    import ecthr_docket

    assert ecthr_docket.display_docket("bger", "6B_1234/2025") == "6B_1234/2025"
    # bge_egmr encodes its date as a PREFIX and must not be touched
    assert ecthr_docket.display_docket("bge_egmr", "20201020_78630_12") == "20201020_78630_12"


def test_the_citation_builder_and_the_display_helper_share_one_regex():
    """Three display sites (MCP citations, SEO pages, RSS) strip this suffix.
    One definition, or they drift."""
    import ecthr_docket
    import mcp_server

    assert mcp_server._ECTHR_DOCKET_DATE_SUFFIX is ecthr_docket.DATE_SUFFIX_RE


# ---- the monolithic 01:00 scrape must not race the backfill ----


def test_ecthr_is_not_in_the_nightly_monolithic_scrape():
    """opencaselaw-scrape.timer runs run_all_scrapers over every registered
    scraper at 01:00 UTC with a 7200 s per-scraper cap, and — unlike
    opencaselaw-ecthr.service — has no backfill guard."""
    from run_all_scrapers import SKIP_BY_DEFAULT

    assert "ecthr" in SKIP_BY_DEFAULT


# ============ the Swiss structure extractor must not touch Strasbourg =======
#
# extract_decision_structure.extract() fell back to SWISS GERMAN patterns for
# any language outside {de, fr, it}. An ECtHR judgment run through it does not
# come out empty — it comes out wrong. Measured on production 2026-08-26:
# English judgments yielded colliding e_numbers ['1','1','2','2'] (and
# erwaegungen_paragraph is keyed (decision_id, e_number), so one of each pair
# is silently overwritten), and cite('ecthr_chamber_15783_21', pinpoint='3')
# returned "§ 3" over text that is the Court's §§ 174ff.


def test_strasbourg_rows_get_no_structure_rather_than_wrong_structure():
    from search_stack.extract_decision_structure import extract

    # Swiss markers embedded in a text that is nominally an ECtHR judgment:
    # the extractor must refuse it on the court, not on the content.
    bait = (
        "THE LAW\n1.\nThe applicant complained.\n2.\nThe Court finds.\n"
        "Demnach erkennt das Bundesgericht:\n1. Die Beschwerde wird abgewiesen.\n"
    )
    for did in ("ecthr_chamber_47358_20_20220830",
                "ecthr_grand_chamber_1_20200101",
                "ecthr_committee_1_20200101"):
        s = extract(bait, "en", did)
        assert s.erwaegungen is None, did
        assert s.dispositiv is None, did
        assert s.erwaegungen_paragraphs == [], did
        assert s.sachverhalt is None, did


def test_the_gate_is_keyed_on_the_court_not_the_language():
    """An English judgment is the obvious case, but the French ones fail
    worse — they hit the marker patterns rather than the language fallback
    and produce plausible-looking wrong pinpoints."""
    from search_stack.extract_decision_structure import extract

    for lang in ("en", "fr", "de", "it", ""):
        s = extract("EN DROIT\n40.\nLa Cour rappelle.\n", lang,
                    "ecthr_chamber_47358_20_20220830")
        assert s.erwaegungen_paragraphs == [], lang


def test_bge_egmr_and_hudoc_ch_keep_their_structure():
    """bge_egmr rows are the Federal Supreme Court's own BGE-published
    translations and carry Swiss BGE structure, so the extractor is correct
    for them. Gating them would delete good structure to fix a problem they
    do not have."""
    from search_stack.extract_decision_structure import is_ecthr_decision

    assert not is_ecthr_decision("bge_egmr_20201020_78630_12")
    assert not is_ecthr_decision("hudoc_ch_16279_90")
    assert not is_ecthr_decision("bger_6B_1234_2025")
    for did in ("ecthr_chamber_1_2", "ecthr_committee_1_2",
                "ecthr_grand_chamber_1_2"):
        assert is_ecthr_decision(did), did


def test_swiss_decisions_are_completely_unaffected():
    from search_stack.extract_decision_structure import extract

    swiss = ("Erwägungen:\n1.\nDie Beschwerde ist zulässig.\n"
             "2.\nSie ist unbegründet.\n"
             "Demnach erkennt das Bundesgericht:\n"
             "1. Die Beschwerde wird abgewiesen.\n")
    s = extract(swiss, "de", "bger_6B_1234_2025")
    assert s.dispositiv, "the Swiss path must still extract a Dispositiv"


# ---- Strasbourg judgments are not cantonal ----


def test_ecthr_courts_have_a_display_name_and_an_international_level():
    """Without these the default applies and Strasbourg judgments are served
    as court_level 'cantonal' under the name 'Ecthr Chamber'."""
    import mcp_server as m

    for court in ("ecthr_chamber", "ecthr_grand_chamber", "ecthr_committee",
                  "hudoc_ch", "bge_egmr"):
        assert court in m.COURT_DISPLAY_NAMES, court
        assert "EGMR" in m.COURT_DISPLAY_NAMES[court], court
        assert m.COURT_LEVELS.get(court) == "international", court


def test_swiss_court_levels_are_unchanged():
    import mcp_server as m

    assert m.COURT_LEVELS.get("bger") == "federal_supreme"
    assert m.COURT_LEVELS.get("bvger") == "federal_appellate"
    assert m.COURT_LEVELS.get("zh_obergericht") is None  # cantonal default


def test_the_largest_year_shard_is_one_request(tmp_path, monkeypatch):
    """2009 (538 rows) and 2010 (545) straddled a 500-row page boundary
    inside a run of same-date judgments; HUDOC orders those ties differently
    per request, and the 2009 shard came back one row short 200 times
    (2026-08-28..11-17). A year must fit one page so no boundary exists."""
    scraper = HUDOCFullScraper(state_dir=tmp_path)
    all_rows = [_listing_row(itemid=f"001-{i:06d}", ecli=f"ECLI:{i}", appno=f"{i}/09")
                for i in range(545)]
    lengths = []

    def fake_get(url, params=None, timeout=None, **kw):
        if params is None:
            return FakeResponse(text="ok")
        lengths.append(params["length"])
        s, n = params["start"], params["length"]
        return FakeResponse({"resultcount": len(all_rows),
                             "results": [{"columns": r} for r in all_rows[s:s + n]]})

    monkeypatch.setattr(scraper, "_rate_limit", lambda: None)
    monkeypatch.setattr(scraper.session, "get", fake_get)

    rows = scraper._discover_year(2010)
    assert len(rows) == 545
    assert len(lengths) == 1, f"a 545-row year took {len(lengths)} requests"
    assert scraper.shard_failures == 0
