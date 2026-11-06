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


# ── languages: English originals and translations ─────────────────────

from scrapers.six_ser import SIXSERScraper, detect_text_language, file_stem  # noqa: E402

EN = ("Decision in the proceedings SaKo-II/2024. The Sanctions Commission has determined that "
      "the issuer intentionally violated the applicable financial reporting standard and that it was ") * 5
DE = ("Entscheid im Verfahren SaKo-026/13. Die Sanktionskommission hat festgestellt, dass der "
      "Teilnehmer die Pflichten nicht eingehalten hat und eine Busse ist zu verhängen. ") * 5
FR = ("Décision dans la procédure SaKo-026/13. La Commission des sanctions constate que le "
      "participant n'a pas respecté les obligations et qu'une amende est prononcée. ") * 5


def test_language_detection_knows_english():
    assert detect_text_language(EN) == "en"
    assert detect_text_language(DE) == "de"
    assert detect_text_language(FR) == "fr"
    assert file_stem("https://x/2016/decision_05_12_16_fr.pdf") == "https://x/2016/decision_05_12_16"


def _scraper(tmp_path, texts, listings):
    s = SIXSERScraper(state_dir=tmp_path)
    s._listing = lambda lang: listings[lang]
    s._text = lambda url, docket: (texts[url], detect_text_language(texts[url])) if url in texts else None
    return s


def _item(url, title="SaKo-026/13", marker="Sanktionskommission"):
    return {"url": url, "title": title, "marker": marker, "description": "KR 1 | Busse | x",
            "entryIntoForceDate": 1479081600000, "publicationDate": 1479081600000}


def test_every_official_language_is_a_row_and_fallback_copies_are_not(tmp_path):
    base = "https://www.ser-ag.com/dam/x/decision_05_12_16"
    one = "https://www.ser-ag.com/dam/x/sako-ii"          # English-only decision
    listings = {
        "de": {"results": [_item(base + "_de.pdf"), _item(one + "-de.pdf", title="SaKo-II/2024")]},
        "fr": {"results": [_item(base + "_fr.pdf"), _item(one + "-fr.pdf", title="SaKo-II/2024")]},
        "en": {"results": [_item(base + "_en.pdf", title="SaKo-026/13 decision"),
                           _item(one + "-en.pdf", title="SaKo-II/2024")]},
    }
    texts = {base + "_de.pdf": DE, base + "_fr.pdf": FR, base + "_en.pdf": EN,
             one + "-de.pdf": EN, one + "-fr.pdf": EN, one + "-en.pdf": EN}
    s = _scraper(tmp_path, texts, listings)
    got = [d for d in (s.fetch_decision(st) for st in s.discover_new()) if d]
    rows = {(d.docket_number, d.language) for d in got}
    assert rows == {("SaKo-026/13", "de"), ("SaKo-026/13 (EN)", "en"), ("SaKo-026/13 (FR)", "fr"),
                    ("SaKo-II/2024", "en")}
    assert len({d.decision_id for d in got}) == 4
    # a second run downloads nothing for the fallback copies of SaKo-II/2024
    for d in got:
        s.state.mark_scraped(d.decision_id)
    s2 = _scraper(tmp_path, texts, listings)
    assert list(s2.discover_new()) == []
