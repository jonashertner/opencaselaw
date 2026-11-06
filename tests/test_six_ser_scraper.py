"""SIX Exchange Regulation sanction decisions (scrapers/six_ser.py), offline.
Fixture: the AEM results.json behind ser-ag.com/de/resources/sanctions.html,
captured 2026-11-06 (61 entries)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.six_ser import docket_for, parse_results  # noqa: E402

DATA = json.loads((ROOT / "tests" / "fixtures" / "six_ser_results_de.json").read_text(encoding="utf-8"))


def _by_docket():
    return {s["docket_number"]: s for s in parse_results(DATA)}


def test_court_judgments_are_left_to_bger_and_bvger():
    stubs = parse_results(DATA)
    assert len(DATA["results"]) == 61 and len(stubs) == 58
    dockets = {s["docket_number"] for s in stubs}
    assert not any(d.startswith(("4A_", "B-2233")) for d in dockets)


def test_dockets_are_unique_and_use_sers_references():
    stubs = parse_results(DATA)
    assert len({s["docket_number"] for s in stubs}) == len(stubs)
    d = _by_docket()
    assert "SB-AHP-III/24" in d and "SaKo-X/2023" in d                # "SaKo-X/2023 betr. SB-AHP-III/23"
    assert "SaKo-2015-KTR-I/15 / MT-I/15 / MP-I/15" in d
    # an arbitral award must not share the docket of the decision it reviews
    assert d["Schiedsgericht SaKo-IV/2023"]["decision_type"] == "Schiedsentscheid"
    assert d["SaKo-IV/2023"]["decision_type"] == "Sanktionsentscheid"
    assert d["Schiedsgericht SaKo-VI/2023"]["decision_type"] == "Vergleich"


def test_untitled_items_fall_back_to_the_file_stem():
    item = {"title": None, "url": "https://x/2024/bi-kot-241-de.pdf"}
    assert docket_for(item) == "BI-KOT-241"
    assert _by_docket()["BI-KOT-241"]["decision_type"] == "Beschwerdeentscheid"


def test_dates_regeste_and_title():
    s = _by_docket()["SB-AHP-III/24"]
    assert str(s["decision_date"]) == "2024-07-02"           # the PDF: "Ordonnance de sanction du 2 juillet 2024"
    assert str(s["publication_date"]) == "2024-08-21"
    assert s["regeste"].startswith("KR 53") or "|" in s["regeste"]
    assert s["title"].startswith("SB-AHP-III/24: ")
    assert s["decision_type"] == "Sanktionsbescheid"
