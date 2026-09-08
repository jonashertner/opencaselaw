"""M-3 (2026-06-28 audit): cantonal citation strings used the raw collection code
as the court abbreviation ('FR_GERICHTE 101 2026 140 vom 17. August 2026') and
surfaced a placeholder/future date. Fix: a readable court label per language, and
no unreliable date inside a citable string.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402

# The original fixtures hard-coded 2026-08-17 as "a future date"; on
# 2026-08-18 it became the past and both assertions flipped. A date-relative
# fixture tests the property (future dates are unreliable) instead of a
# calendar coincidence.
_FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
_FUTURE_YEAR = _FUTURE[:4]


def test_cantonal_court_label_derivation():
    assert m._cantonal_court_label("zh_obergericht", "ZH", "de") == "Obergericht ZH"
    assert m._cantonal_court_label("be_verwaltungsgericht", "BE", "de") == "Verwaltungsgericht BE"
    # platform/generic code -> language-appropriate generic, never the raw code
    assert m._cantonal_court_label("fr_gerichte", "FR", "de") == "Gericht FR"
    assert m._cantonal_court_label("fr_gerichte", "FR", "fr") == "Tribunal FR"
    assert m._cantonal_court_label("ti_gerichte", "TI", "it") == "Tribunale TI"


def test_citation_date_reliability():
    assert m._citation_date_reliable("2025-09-27") is True
    assert m._citation_date_reliable("2026-01-01") is False   # placeholder
    assert m._citation_date_reliable(_FUTURE) is False        # future
    assert m._citation_date_reliable(None) is False


def test_cantonal_citation_string_no_raw_code_no_bad_date():
    dec = {
        "court": "fr_gerichte", "canton": "FR",
        "docket_number": "101 2026 140", "decision_date": _FUTURE,
        "decision_id": "fr_gerichte_101 2026 140",
    }
    cs = m._build_citation_strings(dec)
    assert "FR_GERICHTE" not in cs["citation_string_de"]
    assert cs["citation_string_de"].startswith("Gericht FR")
    assert cs["citation_string_fr"].startswith("Tribunal FR")
    # future date suppressed: nothing follows the docket
    assert _FUTURE_YEAR not in cs["citation_string_de"].split("140")[-1]
    assert "vom" not in cs["citation_string_de"]


def test_real_dated_cantonal_keeps_date():
    dec = {
        "court": "zh_obergericht", "canton": "ZH",
        "docket_number": "LB230012", "decision_date": "2024-03-15",
        "decision_id": "zh_obergericht_LB230012",
    }
    cs = m._build_citation_strings(dec)
    assert cs["citation_string_de"].startswith("Obergericht ZH LB230012")
    assert "vom" in cs["citation_string_de"]  # reliable date kept


def test_ager_z_yearbook_docket_is_the_citation():
    """Arbeitsgericht Zürich yearbook rows (scrapers/cantonal/zh_arbeitsgericht_sammlung.py):
    the docket is the court's own Zitiervorschlag, so no court label is prefixed."""
    dec = {
        "court": "zh_arbeitsgericht", "canton": "ZH",
        "docket_number": "AGer-Z 2023 Nr. 7", "decision_date": "2023-06-05",
        "decision_id": "zh_arbeitsgericht_AGer-Z 2023 Nr. 7",
    }
    cs = m._build_citation_strings(dec)
    assert cs["citation_string_de"] == "AGer-Z 2023 Nr. 7 vom 5. Juni 2023"
    assert cs["citation_string_fr"].startswith("AGer-Z 2023 Nr. 7 du ")
    assert cs["citation_string_it"].startswith("AGer-Z 2023 Nr. 7 del ")
    assert cs["canonical_url"].endswith("/entscheid/" + m._decision_path(dec["decision_id"]))
    # undated excerpt: the docket alone, no dangling "vom"
    dec["decision_date"] = None
    cs = m._build_citation_strings(dec, pinpoint="E. 3.2")
    assert cs["citation_string_de"] == "AGer-Z 2023 Nr. 7, E. 3.2"


def test_zh_arbeitsgericht_typo3_rows_get_a_court_label():
    dec = {
        "court": "zh_arbeitsgericht", "canton": "ZH",
        "docket_number": "AH230041-L", "decision_date": "2025-01-13",
        "decision_id": "zh_arbeitsgericht_AH230041-L",
    }
    assert m._build_citation_strings(dec)["citation_string_de"] == "Arbeitsgericht ZH AH230041-L vom 13. Januar 2025"
    assert m._cantonal_court_label("zh_bezirksgericht_zuerich", "ZH") == "Bezirksgericht Zürich ZH"
