"""Private-body banking practice source, added 2026-11-06: the Swiss
Banking Ombudsman (bankingombudsman.py), a PRIVATE body — permission to
index and serve this material was requested by email on 2026-11-06 and had
not been granted as of that date (see the module docstring). The SBVg
self-regulation source added alongside it was withdrawn on 2026-11-09
after the SBVg declined; test_sbvg_stays_withdrawn pins that. Offline:
golden HTML fixtures captured from the live site on 2026-11-06."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.practice import bankingombudsman as bo  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "practice"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── bankingombudsman: Jahresberichte ────────────────────────────────────

def test_jahresberichte_de_every_year_full_and_summary():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_jahresberichte_de.html"))
    assert len(tiles) == 22  # 11 years (2015-2025) x {full, summary}
    kept = [(t["title"], bo.classify_report(t["title"], "de")) for t in tiles]
    assert all(info is not None for _, info in kept)
    years = {info["year"] for _, info in kept}
    assert years == {str(y) for y in range(2015, 2026)}
    full = next(info for t, info in kept if t == "Jahresbericht 2025")
    assert full == {"year": "2025", "kind": "full"}
    summary = next(info for t, info in kept if t == "2025 in Kürze")
    assert summary == {"year": "2025", "kind": "summary"}


def test_jahresberichte_de_decomposed_umlaut_still_matches():
    # The site mixes precomposed "ü" (U+00FC) with a bare "u" + combining
    # diaeresis (U+0075 U+0308) across otherwise-identical titles — only
    # the newest year used the precomposed form on 2026-11-06. Without
    # NFC normalisation every older "... in Kürze" title fails to classify.
    decomposed = "2016 in Kürze"
    assert bo.classify_report(decomposed, "de") == {"year": "2016", "kind": "summary"}


def test_jahresberichte_it_en_relist_de_fr_as_cross_references():
    for lang, fn in (("it", "bankingombudsman_jahresberichte_it.html"),
                      ("en", "bankingombudsman_jahresberichte_en.html")):
        tiles = bo.parse_file_tiles(_read(fn))
        assert len(tiles) == 33  # 11 x {DE cross-ref, FR cross-ref, own summary}
        classified = [bo.classify_report(t["title"], lang) for t in tiles]
        kept = [c for c in classified if c is not None]
        assert len(kept) == 11  # only the page's own-language summary survives
        assert all(c["kind"] == "summary" for c in kept)


def test_jahresberichte_fr_full_and_summary_every_year():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_jahresberichte_fr.html"))
    kept = [bo.classify_report(t["title"], "fr") for t in tiles]
    kept = [c for c in kept if c]
    assert {c["year"] for c in kept if c["kind"] == "full"} == {str(y) for y in range(2015, 2026)}


# ── bankingombudsman: Stellungnahmen und Publikationen ──────────────────

def test_publications_de_language_tags_stripped():
    tiles = bo.parse_file_tiles(_read("bankingombudsman_publications_de.html"))
    assert len(tiles) == 17
    tagged = [bo.classify_publication(t["title"], "de") for t in tiles]
    negativ = next(c for c in tagged if "Negativzinsen" in c["title"])
    assert negativ == {"title": "Negativzinsen bei Vorfälligkeitsentschädigungen", "language": "de"}
    faktenblatt = next(c for c in tagged if "Factsheet" in c["title"])
    assert faktenblatt["language"] == "de"  # the page's own translation, no tag needed
    assert bo.publication_doc_type(faktenblatt["title"]) == "merkblatt"
    assert bo.publication_doc_type(negativ["title"]) == "bericht"


def test_publications_fr_it_en_relist_de_items_with_tags():
    for lang, fn in (("fr", "bankingombudsman_publications_fr.html"),
                      ("it", "bankingombudsman_publications_it.html"),
                      ("en", "bankingombudsman_publications_en.html")):
        tiles = bo.parse_file_tiles(_read(fn))
        # The evaluation study's 4 language PDFs (same on every UI-language
        # page — "_DE"/"_FR"/"_EN"/"_IT" in the URL): each tagged with its
        # true language regardless of which page's titles are translated.
        eval_tiles = [t for t in tiles if "Evaluation_presserohstoff" in t["url"]]
        eval_langs = {bo.classify_publication(t["title"], lang)["language"] for t in eval_tiles}
        assert eval_langs == {"de", "fr", "en", "it"}
        # The Faktenblatt is always this page's own translation (every other
        # item keeps its true original-publication language even when its
        # anchor text is itself translated into the page's UI language).
        faktenblatt = next(t for t in tiles if "Faktenblatt-Betrug" in t["url"])
        assert bo.classify_publication(faktenblatt["title"], lang)["language"] == lang


def test_publications_same_url_across_pages_is_one_document():
    # The evaluation study PDF is linked identically (same URL, same DE
    # text) from all four language pages; a scraper that didn't dedupe by
    # URL across pages would quadruple-count it.
    urls = set()
    for fn in ("bankingombudsman_publications_de.html", "bankingombudsman_publications_it.html"):
        tiles = bo.parse_file_tiles(_read(fn))
        urls.update(t["url"] for t in tiles if "Evaluation_presserohstoff" in t["url"] and "_DE" in t["url"])
    assert len(urls) == 1


# ── bankingombudsman: hreflang-based translation grouping ───────────────

def test_hreflang_links_from_a_case_page():
    links = bo.hreflang_links(_read("bankingombudsman_case_hreflang.html"))
    assert links == {
        "de": "https://bankingombudsman.ch/nicht-umgesetzte-sonderkonditionen-fuer-mandatsgebuehren/",
        "fr": "https://bankingombudsman.ch/fr/conditions-particulieres-non-mises-en-oeuvre-pour-les-frais-de-mandat/",
        "it": "https://bankingombudsman.ch/it/mancata-applicazione-di-condizioni-preferenziali-convenute-per-le-commissioni-di-gestione/",
        "en": "https://bankingombudsman.ch/en/unimplemented-special-conditions-for-mandate-fees/",
    }
    assert "x-default" not in links  # excluded on purpose, it only duplicates "de"


def test_collect_descendant_category_ids_excludes_the_exclude_category():
    categories = [
        {"id": 44, "slug": "fallsammlung", "parent": 0, "count": 0},
        {"id": 45, "slug": "anlageberatung", "parent": 44, "count": 43},
        {"id": 224, "slug": "exclude", "parent": 43, "count": 0},
        {"id": 43, "slug": "mitteilungen", "parent": 0, "count": 1},
        {"id": 41, "slug": "aktuelles", "parent": 43, "count": 19},
    ]
    assert bo.collect_descendant_category_ids(categories, "fallsammlung") == {45}
    assert bo.collect_descendant_category_ids(categories, "mitteilungen") == {41}
    assert bo.collect_descendant_category_ids(categories, "no-such-root") == set()


def test_post_stub_strips_html_and_normalises_unicode():
    scraper = bo.BankingOmbudsmanScraper.__new__(bo.BankingOmbudsmanScraper)
    post = {
        "link": "https://bankingombudsman.ch/x/",
        "title": {"rendered": "Ein <em>Für</em> Beispiel"},
        "content": {"rendered": "<p>Zeile 1</p>\n<p>Zeile 2</p>"},
        "date": "2026-04-16T20:03:07",
    }
    stub = bo.BankingOmbudsmanScraper._post_stub(
        scraper, post, "de", "SBO Fall 1", "rechtsprechung", "case_de_1")
    assert stub["title"] == "Ein Für Beispiel"
    assert stub["body_text"] == "Zeile 1\nZeile 2"
    assert stub["date"] == "2026-04-16"
    assert stub["doc_number"] == "SBO Fall 1"
    assert stub["bo_id"] == "case_de_1"


def test_sources_registered():
    # Published on Jonas's decision of 2026-11-07 while permission is pending
    # (see the module docstrings for how to withdraw a source).
    from scrapers.practice.runner import ENABLED_SCRAPERS
    assert "bankingombudsman" in ENABLED_SCRAPERS


def test_sbvg_stays_withdrawn():
    # The SBVg declined on 2026-11-09 to have its self-regulation included;
    # the source was removed from production the same day. Re-adding it needs
    # the association's consent first.
    from scrapers.practice.runner import ALL_SCRAPERS
    assert "sbvg" not in ALL_SCRAPERS
