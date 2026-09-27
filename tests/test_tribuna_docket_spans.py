"""Tribuna row parsing must anchor on the docket, not on ordinal position.

GitHub #68. `_parse_search_response` zipped parallel lists by index:

    n = min(len(doc_ids), len(dockets))
    ... dates_list[i], enc_paths[i], titles[i]

Two defects fell out of that, both confirmed against a live
be_verwaltungsgericht response captured 2026-08-27
(tests/fixtures/tribuna_be_vg_page0.txt):

1. ROWS VANISHED. doc_id is an OPTIONAL per-row field that older records
   mostly lack, so min() truncated the result set. A 2011 window returns 12
   dockets and 2 doc_ids -> 10 real decisions silently dropped. Per-year
   recovery tracked the doc_id adoption curve exactly (2017: 987/988,
   2013: 82/674, 2011: 2/12), which is the ~2,159-decision gap in #68. The
   portal was never at fault.

2. DATES SHIFTED. A row may emit extra dates — the Rechtskraft sentinel
   "0000-00-00" and a later createDate — so dates_list[i] slid every
   subsequent row along. The captured page has 20 dockets and 22 dates, and
   19 of its 20 rows carried another row's date.

The base class serves nine scrapers (gr_gerichte, zg_gerichte,
zg_obergericht, fr_gerichte, be_verwaltungsgericht, be_zivilstraf,
be_anwaltsaufsicht, be_bvd, be_steuerrekurs), so these are pinned on real
bytes rather than a hand-written string.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIXTURE = REPO / "tests" / "fixtures" / "tribuna_be_vg_page0.txt"


def _parse():
    from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper as S
    s = S.__new__(S)                      # no network, no state file
    return S._parse_search_response(s, FIXTURE.read_text(encoding="utf-8"))


def _strings():
    return re.findall(r'"([^"]*)"', FIXTURE.read_text(encoding="utf-8"))


def test_fixture_still_exhibits_the_shape_this_guards():
    """If the portal changes shape, these assertions stop meaning anything."""
    from scrapers.cantonal.base_tribuna import _RE_DOCKET, _RE_DATE
    a = _strings()
    dockets = [x for x in a if _RE_DOCKET.match(x)]
    dates = [x for x in a if _RE_DATE.match(x)]
    assert len(dockets) == 20
    assert len(dates) > len(dockets), (
        "fixture no longer has surplus dates — it cannot demonstrate the shift")
    assert "0000-00-00" in dates, "fixture lost the Rechtskraft sentinel"


def test_one_row_per_docket_not_per_doc_id():
    """The count must follow dockets. This is the #68 recovery."""
    from scrapers.cantonal.base_tribuna import _RE_DOCKET
    total, decs = _parse()
    assert total == 11602
    assert len(decs) == len([x for x in _strings() if _RE_DOCKET.match(x)]) == 20


FIXTURE_FR = REPO / "tests" / "fixtures" / "tribuna_fr_page0_base64path_20260914.txt"


def _parse_fr():
    from scrapers.cantonal.fr_gerichte import FRGerichteScraper as S
    s = S.__new__(S)
    return S._parse_search_response(s, FIXTURE_FR.read_text(encoding="utf-8"))


# Checked 2026-09-27 against the date each judgment states in its own text
# ("Urteil vom 13. Juli 2026", "Arrêt du 28 juillet 2026", ...), read from
# the stored corpus rows. The docket-span reading had 9 of these 18 empty
# and 3 wrong: 100 2026 142 -> 2026-08-18, 602 2025 79 -> 2026-09-24,
# 604 2025 175 -> 2026-09-15 (createDate / Rechtskraft date).
TEXT_DATES_BE_VG = {
    "200 2026 110": "2026-07-24", "100 2026 142": "2026-07-13",
    "200 2025 500": "2026-07-24", "200 2025 523": "2026-07-27",
    "200 2024 785": "2026-07-24", "200 2025 117": "2026-07-29",
    "200 2025 251": "2026-07-23", "200 2024 305": "2026-07-28",
}
TEXT_DATES_FR = {
    "601 2025 172": "2026-08-13", "102 2025 35": "2026-08-14",
    "102 2026 179": "2026-08-17", "608 2026 41": "2026-08-12",
    "602 2025 79": "2026-08-17", "602 2025 13": "2026-08-13",
    "602 2025 11": "2026-08-13", "602 2024 201": "2026-08-13",
    "604 2025 175": "2026-07-28",
    # The ruling itself says "27 juillet 2027" — a typo; the portal is right.
    "605 2025 140": "2026-07-27",
}


def test_the_string_table_is_deduplicated_which_is_why_spans_fail():
    """If the portal ever stops deduplicating, the span reading would be
    right again and this explanation would no longer hold."""
    from scrapers.cantonal.base_tribuna import _RE_DATE
    for fx in (FIXTURE, FIXTURE_FR):
        dates = [x for x in re.findall(r'"([^"]*)"', fx.read_text(encoding="utf-8"))
                 if _RE_DATE.match(x)]
        assert len(dates) == len(set(dates))


@pytest.mark.parametrize("parse,truth", [
    (_parse, TEXT_DATES_BE_VG), (_parse_fr, TEXT_DATES_FR)])
def test_every_row_gets_the_date_its_judgment_states(parse, truth):
    _, decs = parse()
    by = {d["docket_number"]: d["decision_date"] for d in decs}
    wrong = {k: (by.get(k), v) for k, v in truth.items() if by.get(k) != v}
    assert not wrong, f"(got, judgment text): {wrong}"


def test_no_row_is_left_undated_on_either_page():
    for parse in (_parse, _parse_fr, _parse_2011):
        _, decs = parse()
        assert all(d["decision_date"] for d in decs), (
            [d["docket_number"] for d in decs if not d["decision_date"]])


def test_row_records_are_found_for_every_docket():
    """The stream reading, not the span fallback, must carry every row."""
    from scrapers.cantonal.base_tribuna import _row_dates_from_stream
    from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper as S
    for fx in (FIXTURE, FIXTURE_FR, FIXTURE_2011):
        text = fx.read_text(encoding="utf-8")
        _, decs = S._parse_search_response(S.__new__(S), text)
        assert set(_row_dates_from_stream(text)) == {d["docket_number"] for d in decs}, fx.name


def test_no_decision_date_lies_after_the_page_was_captured():
    """The span reading gave 604 2025 175 its Rechtskraft date; live rows got
    dates up to five weeks in the future (2026-10-26 for a 1 Sept ruling)."""
    for parse, captured in ((_parse, "2026-08-27"), (_parse_fr, "2026-09-14")):
        _, decs = parse()
        assert max(d["decision_date"] for d in decs) <= captured


def test_the_rechtskraft_sentinel_never_becomes_a_decision_date():
    _, decs = _parse()
    assert all(d["decision_date"] != "0000-00-00" for d in decs)


def test_enc_path_is_per_row_and_complete():
    """enc_path drives the download URL — a shifted one fetches the wrong PDF."""
    _, decs = _parse()
    assert all(d["enc_path"] for d in decs)
    assert len({d["enc_path"] for d in decs}) == 20


def test_doc_id_is_gone_from_the_stub():
    """It was written and never read by any Tribuna scraper, and gating on
    it is what caused #68. bvger/bstger have their own doc_id but extend
    BaseScraper, not TribunaBaseScraper."""
    _, decs = _parse()
    assert all("doc_id" not in d for d in decs)
    # Executable lines only — the explanatory comment quotes the old
    # expression on purpose, and matching that would be self-defeating.
    src = (REPO / "scrapers" / "cantonal" / "base_tribuna.py").read_text(encoding="utf-8")
    code = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    assert not any("min(len(doc_ids)" in l for l in code), (
        "the ordinal doc_id gate is back in executable code")


def test_dockets_are_unique_and_in_response_order():
    from scrapers.cantonal.base_tribuna import _RE_DOCKET
    _, decs = _parse()
    got = [d["docket_number"] for d in decs]
    assert got == [x for x in _strings() if _RE_DOCKET.match(x)]
    assert len(set(got)) == len(got)


def test_empty_and_malformed_responses_still_behave():
    """Guard the paths tests/test_tribuna_protocol.py already covers."""
    from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper as S
    s = S.__new__(S)
    assert S._parse_search_response(s, '//OK[0,[],0,7]') == (0, [])
    assert S._parse_search_response(s, 'garbage') == (0, [])


# ── the actual #68 recovery, on a real old-year window ────────────────

FIXTURE_2011 = REPO / "tests" / "fixtures" / "tribuna_be_vg_2011.txt"


def _parse_2011():
    from scrapers.cantonal.be_verwaltungsgericht import BEVerwaltungsgerichtScraper as S
    s = S.__new__(S)
    return S._parse_search_response(s, FIXTURE_2011.read_text(encoding="utf-8"))


def test_old_window_recovers_every_row_the_ordinal_gate_dropped():
    """The 2011 window, captured live 2026-08-27 — this IS GitHub #68.

    doc_id had not been adopted yet in 2011, so the response carries 2 of
    them against 12 dockets. min(len(doc_ids), len(dockets)) emitted 2 rows
    and the other 10 decisions were invisible to us for as long as the
    parser has existed. The portal reports total=12 and always did.
    """
    from scrapers.cantonal.base_tribuna import _RE_DOC_ID, _RE_DOCKET
    a = re.findall(r'"([^"]*)"', FIXTURE_2011.read_text(encoding="utf-8"))
    n_doc = len([x for x in a if _RE_DOC_ID.match(x)])
    n_dock = len([x for x in a if _RE_DOCKET.match(x)])
    assert (n_doc, n_dock) == (2, 12), "fixture no longer shows sparse doc_ids"

    total, decs = _parse_2011()
    assert total == 12
    assert len(decs) == 12, "the ordinal gate is back — #68 has regressed"
    assert len(decs) > min(n_doc, n_dock), "no recovery over the old behaviour"


def test_recovered_rows_carry_real_dates_not_nulls():
    """A recovered row is only useful if it is dated.

    '200 2011 322' is the decision the coverage audit found stored with
    decision_date: null while the portal says 2015-12-23.
    """
    _, decs = _parse_2011()
    by = {d["docket_number"]: d for d in decs}
    assert by["200 2011 322"]["decision_date"] == "2015-12-23"
    assert sum(1 for d in decs if d["decision_date"]) == 12
    assert len({d["decision_date"] for d in decs}) > 1


def test_titles_are_subject_lines_not_internal_ids():
    """An all-digit run sits between the enc_path and the real subject line."""
    for parse in (_parse, _parse_2011):
        _, decs = parse()
        assert not [d for d in decs if d["title"].isdigit()], (
            "an internal row id is being stored as the title")
    _, decs = _parse_2011()
    by = {d["docket_number"]: d for d in decs}
    assert by["200 2011 322"]["title"] == "Einspracheentscheid vom 22. August 2011"


def test_the_timeout_allows_the_backlog_to_drain():
    """Recovery is worthless if the run is SIGKILLed mid-catch-up.

    ~580 discovery pages plus a ~2,159-decision backlog at REQUEST_DELAY=4.0
    is ~3h, against the 7200s default the scraper had.
    """
    from run_all_scrapers import SLOW_SCRAPERS
    assert SLOW_SCRAPERS.get("be_verwaltungsgericht", 0) >= 10800


def test_the_alert_no_longer_blames_the_portal():
    """It prescribed a rescan, then said the portal withholds old records.
    Both were wrong: it was our parser."""
    from scripts.check_scraper_freshness import KNOWN_GAP_REMEDIES
    msg = KNOWN_GAP_REMEDIES["be_verwaltungsgericht"]
    assert "#68" in msg and "2013" in msg
    assert "OCL_SCRAPER_RESCAN_ALL" not in msg
    assert "base_tribuna" in msg
