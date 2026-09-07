"""WEKO scraper: the Praxis-Binnenmarktgesetz pages (user request 2026-09-07).

The three BGBM pages (/de/weko, /de/weko-2, /de/weko-3) hold ~40 WEKO
Empfehlungen, Gutachten and Stellungnahmen under Art. 8/10 BGBM that never
appear on the Entscheide listing. Golden fixtures captured 2026-09-07 (<main>
only). parse_listing() is pure; discover_new() is driven with a fake state and
a stubbed get(), no network.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FIX = REPO / "tests" / "fixtures"

from scrapers import weko  # noqa: E402


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _bgbm():
    out = []
    for page in ("weko", "weko_2", "weko_3"):
        out += weko.parse_listing(_read(f"weko_bgbm_{page}.html"), legal_area="Binnenmarktrecht",
                                  file_date_fallback=False)
    return out


# ───────────────────────────────────────────── Entscheide listing (unchanged surface)

def test_entscheide_listing_still_yields_one_stub_per_pdf():
    stubs = weko.parse_listing(_read("weko_entscheide_20260907.html"))
    assert len(stubs) == 115
    assert len({s["docket_number"] for s in stubs}) == 115
    first = stubs[0]
    assert first["docket_number"] == "nova-nutzungsbedingungen-und-oev-provisionsmodell-2025-03-25"
    assert first["decision_date"] == "25. März 2025"
    assert first["legal_area"] == "Wettbewerbsrecht"
    assert all(s["decision_date"] for s in stubs)          # file date fallback stays on for this page


def test_entscheide_doc_type_survives_the_nuxt_title_without_pdf_suffix():
    """The <h4> title has no "(PDF, …)" tail any more; the type used to come
    back None for every Nuxt-era row."""
    stubs = weko.parse_listing(_read("weko_entscheide_20260907.html"))
    types = Counter(s["doc_type"] for s in stubs)
    assert types["Verfügung"] >= 40 and types["Schlussbericht"] >= 15 and types["Stellungnahme"] >= 5
    assert types[None] < 25, types
    by_docket = {s["docket_number"]: s for s in stubs}
    assert by_docket["baustoffe-und-deponien-bern-2024-05-21"]["doc_type"] == "Verfügung"


# ───────────────────────────────────────────── BGBM pages

def test_bgbm_pages_parse_to_forty_distinct_documents():
    stubs = _bgbm()
    assert len(stubs) == 41                                   # one document is on two pages
    assert len({s["docket_number"] for s in stubs}) == 40
    assert all(s["legal_area"] == "Binnenmarktrecht" for s in stubs)
    types = Counter(s["doc_type"] for s in stubs)
    assert types["Empfehlung"] >= 20 and types["Gutachten"] >= 9 and types["Stellungnahme"] == 3
    assert types["Vernehmlassung"] == 1


def _by_prefix(stubs, prefix):
    [s] = [s for s in stubs if s["docket_number"].startswith(prefix)]
    return s


def test_bgbm_dates_come_from_the_title_never_from_the_file_stamp():
    stubs = _bgbm()
    s = _by_prefix(stubs, "empfehlung-vom-27-mai-2019-betreffend")
    assert s["decision_date"] == "27. Mai 2019" and s["docket_number"].endswith("-2019-05-27")
    s = _by_prefix(stubs, "expertise-du-5-juin-2023")
    assert s["doc_type"] == "Gutachten" and s["docket_number"].endswith("-2023-06-05")
    # titles without "vom …": no date rather than the 2014/2021 migration stamp
    undated = {s["docket_number"] for s in stubs if not s["decision_date"]}
    assert len(undated) == 6
    assert any(d.startswith("raccomandazione-rlepicosc") for d in undated)
    assert any(d.startswith("adjudication-de-laffichage") for d in undated)
    assert all("-20" not in d[-11:] for d in undated)        # no date suffix on an undated docket


def test_bgbm_language_tags_are_stripped_from_the_title():
    stubs = _bgbm()
    assert not _by_prefix(stubs, "expertise-du-5-juin-2023")["title"].endswith("(französisch)")
    adjud = [s for s in stubs if s["docket_number"].startswith("adjudication-de-laffichage")]
    assert len(adjud) == 2 and all("nur auf" not in s["title"] for s in adjud)


# ───────────────────────────────────────────── discover_new: pages, since, dedup

class _State:
    def __init__(self, known=()):
        self.known = set(known)
    def is_known(self, decision_id):
        return decision_id in self.known


class _Resp:
    def __init__(self, text):
        self.text = text


def _scraper(pages: dict, known=()):
    s = weko.WEKOScraper.__new__(weko.WEKOScraper)
    s.state = _State(known)
    s.get = lambda url, **kw: _Resp(pages[url])
    return s


def _pages():
    return {
        weko.LISTING_URL: _read("weko_entscheide_20260907.html"),
        weko.BASE_URL + "/de/weko": _read("weko_bgbm_weko.html"),
        weko.BASE_URL + "/de/weko-2": _read("weko_bgbm_weko_2.html"),
        weko.BASE_URL + "/de/weko-3": _read("weko_bgbm_weko_3.html"),
    }


def test_discover_new_walks_all_four_pages_and_dedups_across_them():
    stubs = list(_scraper(_pages()).discover_new())
    ids = [weko.make_decision_id("weko", s["docket_number"]) for s in stubs]
    assert len(ids) == len(set(ids))
    # 115 Entscheide + 40 BGBM documents, minus the one Gutachten listed on both
    assert len(stubs) == 154
    assert Counter(s["legal_area"] for s in stubs) == {"Wettbewerbsrecht": 115, "Binnenmarktrecht": 39}


def test_since_date_filters_the_entscheide_listing_but_not_the_archives():
    stubs = list(_scraper(_pages()).discover_new(since_date=date(2025, 9, 1)))
    ent = [s for s in stubs if s["legal_area"] == "Wettbewerbsrecht"]
    bgbm = [s for s in stubs if s["legal_area"] == "Binnenmarktrecht"]
    assert 0 < len(ent) < 20                                  # only the newest decisions
    # 1998 … 2023, all of them — including the 2019 Gutachten the since filter
    # dropped from the Entscheide page, which the archive then supplies
    assert len(bgbm) == 40


def test_known_ids_are_skipped_on_every_page():
    all_ids = [weko.make_decision_id("weko", s["docket_number"]) for s in _scraper(_pages()).discover_new()]
    stubs = list(_scraper(_pages(), known=all_ids[:150]).discover_new())
    assert len(stubs) == 4
