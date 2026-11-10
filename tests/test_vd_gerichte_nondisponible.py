"""vd_gerichte: decisions whose own dateDecision the portal has not set.

Found 2026-11-09 triaging the weekly parity_audit.py residuals for
VD_FindInfo (entscheidsuche's spider for prestations.vd.ch): 13 of 18 missing
decisions, from 2013 to 2025, turned out to carry dateDecision = "Non
disponible" on the live search API (confirmed with individual portal
lookups, e.g. ZD13.021162 / AI 138/13 - 9/2019, decided per its own PDF
header "Arrêt du 15 janvier 2019" but reported by the API itself as
dateDecision "Non disponible", datePublication "17 février 2019"). This is
not a blank field: the portal's own search range-filters on dateDecision
server-side, so a decision whose dateDecision is unset matches no date
window at all, at any --since depth — discover_new's monthly dateDecision
walk can never reach it, in a nightly run or a full historical backfill.
Confirmed empirically: a direct dateDecision query for the whole month the
PDF's own date falls in returns 0 hits for that record (across every page),
while a datePublication query for the month the decision was actually
published does return it.

Fix: discover_new runs a second pass windowed on datePublication instead,
kept to hits whose own dateDecision is missing or unparseable
(require_missing_decision_date on _parse_search_page / _walk_months), so a
decision with a real dateDecision is never fetched twice."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from models import make_decision_id  # noqa: E402
from scrapers.cantonal.vd_gerichte import VDGerichteScraper  # noqa: E402

U_OK = "414f43f5-da46-4614-bb0f-212d619df78a"       # has a real dateDecision
U_GONE = "ce80b5b3-9f86-4f8e-8b50-0f12469dac82"     # dateDecision "Non disponible"


class FakeState:
    def __init__(self):
        self._seen = set()

    def is_known(self, decision_id):
        return decision_id in self._seen


def scraper():
    sc = VDGerichteScraper.__new__(VDGerichteScraper)   # no network init
    sc.state = FakeState()
    sc._known_uuids = {}
    return sc


def hit(uuid, decision_date, publication_date, hit_no="AI 138/13 - 9/2019"):
    return {"decisionHit": {
        "id": uuid, "numero": hit_no, "dateDecision": decision_date,
        "datePublication": publication_date,
        "affaireHit": None, "natureAffaire": "x", "resume": "",
        "articlesDeLoi": {}, "resultats": [], "conceptsJurivoc": [],
    }}


def page(hits):
    """A '_parse_search_page'-shaped response (already unwrapped, as
    _walk_months hands it on: data.get("response", {}))."""
    return {"totalElements": len(hits), "totalPages": 1, "content": hits}


def search_result(hits):
    """A '_search'-shaped response (the full {"response": ...} envelope)."""
    return {"response": page(hits)}


# ── _parse_search_page(require_missing_decision_date=...) ──────────────────

def test_require_missing_decision_date_skips_a_real_date():
    sc = scraper()
    resp = page([hit(U_OK, "10 décembre 2025", "10 décembre 2025")])
    assert list(sc._parse_search_page(resp, require_missing_decision_date=True)) == []
    # without the filter the same hit parses fine
    stubs = list(sc._parse_search_page(resp, require_missing_decision_date=False))
    assert stubs and stubs[0]["uuid"] == U_OK


def test_require_missing_decision_date_keeps_non_disponible():
    sc = scraper()
    resp = page([hit(U_GONE, "Non disponible", "17 février 2019")])
    stubs = list(sc._parse_search_page(resp, require_missing_decision_date=True))
    assert len(stubs) == 1
    assert stubs[0]["uuid"] == U_GONE
    assert stubs[0]["decision_date"] is None          # nothing to parse
    assert stubs[0]["publication_date"] == date(2019, 2, 17)


def test_require_missing_decision_date_keeps_a_blank_field_too():
    """Not every unreachable record prints the literal string; an empty
    dateDecision must be caught the same way."""
    sc = scraper()
    resp = page([hit(U_GONE, "", "17 février 2019")])
    stubs = list(sc._parse_search_page(resp, require_missing_decision_date=True))
    assert len(stubs) == 1 and stubs[0]["uuid"] == U_GONE


# ── discover_new: the second pass reaches what the first pass cannot ───────

def test_discover_new_finds_a_non_disponible_decision_via_publication_date(monkeypatch):
    """The dateDecision pass sees 0 hits everywhere (as it does for real on
    the portal for this record, see module docstring); only the
    datePublication pass, searching February 2019, surfaces it."""
    sc = scraper()
    sc._init_session = lambda: True
    calls = []

    def fake_search(date_from, date_to, page_=0, by="decision"):
        calls.append((tuple(date_from), tuple(date_to), by))
        if by == "publication" and date_from[:2] == [2019, 2]:
            return search_result([hit(U_GONE, "Non disponible", "17 février 2019")])
        return {"response": {"totalElements": 0, "totalPages": 0, "content": []}}

    sc._search = lambda df, dt, page=0, by="decision": fake_search(df, dt, page, by)
    stubs = list(sc.discover_new(since_date=date(2019, 1, 1)))
    # only reached since today() runs through the current month too, but the
    # fake returns 0 hits for every other window.
    assert [s["uuid"] for s in stubs] == [U_GONE]
    assert stubs[0]["decision_id"] == make_decision_id("vd_gerichte", "AI 138/13 - 9/2019")
    assert any(by == "decision" for _, _, by in calls)
    assert any(by == "publication" for _, _, by in calls)


def test_discover_new_never_double_fetches_a_decision_with_a_real_date(monkeypatch):
    """A decision found by the dateDecision pass must not be re-yielded by
    the datePublication pass, even though both windows are walked over the
    same date range."""
    sc = scraper()
    sc._init_session = lambda: True

    def fake_search(date_from, date_to, page_=0, by="decision"):
        if date_from[:2] != [2025, 12]:
            return {"response": {"totalElements": 0, "totalPages": 0, "content": []}}
        return search_result([hit(U_OK, "10 décembre 2025", "10 décembre 2025", hit_no="PT21.013398")])

    sc._search = lambda df, dt, page=0, by="decision": fake_search(df, dt, page, by)
    stubs = list(sc.discover_new(since_date=date(2025, 12, 1)))
    assert [s["uuid"] for s in stubs] == [U_OK]      # once, from pass 1 only
