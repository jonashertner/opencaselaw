"""The résumé of prestations.vd.ch rulings (vd_gerichte regeste).

Golden search response saved 2026-11-08 (invariant #8). On that day the
portal returned resume=null on every hit (3,841 of 3,841 in 2025; every
month sampled 2015-2026) and its RESUME query target matched nothing, so
vd_gerichte's 0% regeste is the source's, not a mapping gap. The scraper
maps the field already; these tests keep it mapped, so a résumé the portal
starts to publish lands in regeste without a code change.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scrapers.cantonal.vd_gerichte import VDGerichteScraper  # noqa: E402

RESPONSE = json.loads(
    (REPO / "tests" / "fixtures" / "vd_gerichte_search_2026_10_20261108.json").read_text(encoding="utf-8"))


def _parse(item):
    sc = VDGerichteScraper.__new__(VDGerichteScraper)  # no network init
    return sc._parse_search_item(item)


class _PdfResp:
    status_code = 200
    headers = {"Content-Type": "application/pdf"}
    content = b"%PDF-1.4 fixture"

    def raise_for_status(self):
        pass


def _fetch(stub, text):
    sc = VDGerichteScraper.__new__(VDGerichteScraper)
    sc._rate_limit = lambda: None
    sc.TIMEOUT = 1
    sc.session = type("S", (), {"get": staticmethod(lambda url, timeout=None: _PdfResp())})()
    sc._extract_pdf_text = staticmethod(lambda b: text)
    return sc.fetch_decision(stub)


def test_live_hits_carry_no_resume_and_rows_get_no_regeste():
    items = RESPONSE["response"]["content"]
    assert items and all(it["decisionHit"]["resume"] is None for it in items)
    stub = _parse(items[0])
    assert not stub["resume"]
    d = _fetch(stub, "TRIBUNAL CANTONAL\n861\nCHAMBRE DES RECOURS PENALE\nArrêt du 22 octobre 2026\n" + "x " * 80)
    assert d.regeste is None


def test_a_published_resume_lands_in_regeste():
    item = copy.deepcopy(RESPONSE["response"]["content"][1])
    item["decisionHit"]["resume"] = "Suspension du droit à l'indemnité : faute légère."
    stub = _parse(item)
    assert stub["resume"] == "Suspension du droit à l'indemnité : faute légère."
    d = _fetch(stub, "TRIBUNAL CANTONAL\n834\nCOUR DES ASSURANCES SOCIALES\nArrêt du 20 octobre 2026\n" + "x " * 80)
    assert d.regeste == "Suspension du droit à l'indemnité : faute légère."
