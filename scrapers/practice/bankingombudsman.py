"""
Swiss Banking Ombudsman — Stiftung Schweizerischer Bankenombudsman (bankingombudsman.ch)
=========================================================================================

LICENCE / PERMISSION STATUS (2026-11-06): the Stiftung Schweizerischer
Bankenombudsman is a PRIVATE body (a foundation under Art. 80 ZGB, recognised
by FINMA as an Ombudsstelle under the FINIG, but not a federal or cantonal
authority). Everything on bankingombudsman.ch is the foundation's own
copyrighted text. Jonas Hertner emailed the foundation on 2026-11-06 asking
permission to index this material for OpenCaseLaw. Jonas decided on
2026-11-07 to publish it while the request is pending: the texts are served
unchanged, attributed to the foundation and linked to its site, outside the
corpus's CC0 licence. If the foundation sets conditions or asks for removal,
drop the source from ENABLED_SCRAPERS, delete output/practice/
bankingombudsman.jsonl and rebuild practice.db. Together with sbvg.py this is
the first PRIVATE-body source in the practice pipeline;
every other source here (estv_ks, finma_rs, bsv_weisungen, ...) is a federal
or cantonal authority whose publications are official and carry no
copyright restriction on reuse of this kind.

What the site publishes (verified by live fetch 2026-11-06, WordPress +
WPML, four UI languages DE/FR/IT/EN under /, /fr/, /it/, /en/):

  Jahresberichte / Rapports annuels / Rapporti annuali / Annual reports
      bibliothek/jahresberichte/ (+ its FR/IT/EN equivalents): one
      wp-block-file PDF per year, 2015-2025. The FULL report exists only in
      DE and FR (its own PDF each); the "in Kürze"/"en bref"/"in breve"/
      "Summary (English)" short version exists in all four languages. The
      IT/EN pages additionally RELIST the DE and FR full-report files under
      "(Tedesco)"/"(German)"/"(Francese)"/"(French)" labels — those are
      cross-references to the same DE/FR PDF, not new documents, and are
      skipped (classify_report returns None for them).
      One older report (2013) is not on this page at all: it survives only
      as four standalone WordPress posts (a pre-redesign layout), hardcoded
      below as LEGACY_2013 since there is no listing page to discover them
      from and no indication further years exist that way (checked the
      Yoast XML sitemaps for any earlier year — none found). No earlier
      annual report (before 2013) appears anywhere on the site.

  Stellungnahmen und Publikationen / Exposés et prises de position / ...
      bibliothek/stellungnahmen-und-publikationen/: position papers,
      Vernehmlassungsantworten, a commissioned evaluation study (+ its
      press summary in all four languages), two "Schweizer Bank" article
      reprints, and (since 2026-07) a one-page Faktenblatt on fraud/
      cybercrime. Every title on this single listing ends with its own
      explicit "DE"/"FR"/"IT"/"EN" tag EXCEPT the Faktenblatt, which is the
      one item actually translated per page (own title, own file, no tag
      needed — the page's own UI language is unambiguous for it). The FR/
      IT/EN pages otherwise relist the identical German (or French) items
      verbatim, tag included — so the parser reads all four pages but keeps
      only the first file URL it sees; the Faktenblatt is the only row that
      differs by page. No separate "Fallbeispiele" reprints are on this
      page — those are the case collection below, published as native posts
      rather than PDFs.

  Fallbeispiele / Recueil de cas / Raccolta di casi / Selected Cases
      bibliothek/fallbeispiele/ (+ equivalents): ~480 individual case
      write-ups (not PDFs — ordinary WordPress posts under a "Fallsammlung"
      category tree with ~22 topic subcategories), DE and FR essentially
      1:1 (479 / 480), IT and EN roughly half-translated (215 / 216 as of
      2026-11-06). Indexed via the WP REST API (wp/v2/posts, /categories;
      WPML's lang= query param selects the per-language term tree AND post
      set). Treated as doc_type "rechtsprechung" — anonymised dispute
      write-ups are the ombudsman's equivalent of case law, same use of the
      type as BSV's AHI-Praxis digests and FINMA's Bulletin "Ausgewählte
      Verfügungen".

  Mitteilungen / Informations / Informazioni / Messages
      bibliothek/mitteilungen/: the foundation's press releases/news notes
      (its "Mitteilungen"+"Aktuelles" category tree in DE, with matching
      trees in FR/IT/EN — 19/18/14/13 as of 2026-11-06), same REST
      mechanism as the case collection. doc_type "mitteilung". A sibling
      "Medienkonferenz"/"Conférence de presse" category (press-conference
      material) is NOT under this tree and is left out — it duplicates the
      annual-report media launch, not independent practice content.

  NOT taken: the FAQ page (general information, not practice/cases), the
  complaint-form pages, the foundation's annual accounts, and the
  "Medienkonferenz" category noted above.

Cross-language identity: the site's REST API does not expose a WPML
translation-group id, so doc_number for Fallbeispiele/Mitteilungen rows is
minted from the German post's numeric WordPress id ("SBO Fall 15246",
"SBO Mitteilung 12447") and FR/IT/EN rows are matched to it by reading the
`<link rel="alternate" hreflang>` tags on the German post's own page (one
extra HTML fetch per NEW German post only — already-known German posts are
not re-fetched, so steady-state runs stay cheap; a translation added to an
old post after the fact could be missed until a forced refresh). A FR/IT/EN
post with no matching German hreflang (seen for one FR case as of 2026-11,
apparently untranslated into German) gets its own doc_number instead of an
empty one. For the PDF-based sources (Jahresberichte, Stellungnahmen),
doc_number is the shared year+kind ("SBO Jahresbericht 2022 Kurzfassung")
or the (language-stripped) publication title.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterator
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, slugify

logger = logging.getLogger(__name__)

BASE = "https://bankingombudsman.ch"
REST = f"{BASE}/wp-json/wp/v2"

REPORT_PAGES = (
    ("de", f"{BASE}/bibliothek/jahresberichte/"),
    ("fr", f"{BASE}/fr/bibliotheque/rapports-annuels/"),
    ("it", f"{BASE}/it/biblioteca/rapporti-annuali/"),
    ("en", f"{BASE}/en/library/annual-reports/"),
)
PUBLICATION_PAGES = (
    ("de", f"{BASE}/bibliothek/stellungnahmen-und-publikationen/"),
    ("fr", f"{BASE}/fr/bibliotheque/exposes-et-prises-de-position/"),
    ("it", f"{BASE}/it/biblioteca/presentazioni-e-prese-di-posizione/"),
    ("en", f"{BASE}/en/library/publications-and-position-papers/"),
)

# Hardcoded: the 2013 annual report survives only as four standalone posts
# (a layout the site abandoned before the 2015-2025 PDF-listing pages were
# built), found via the Yoast post sitemap, not any listing page. See the
# module docstring.
LEGACY_2013 = (
    ("de", f"{BASE}/jahresbericht-2013-in-kuerze/", (
        ("Jahresbericht 2013 DE", f"{BASE}/wp-content/uploads/2020/05/BOM_Jahresbericht-2013_DE.pdf", "full"),
        ("Executive Summary 2013", f"{BASE}/wp-content/uploads/2021/01/BOM_Executive-Summary_DE.pdf", "summary"),
    )),
    ("fr", f"{BASE}/fr/resume-rapport-annuel-2013/", (
        ("Rapport annuel 2013", f"{BASE}/wp-content/uploads/2020/12/Bom_Jahresbericht-2013_FR.pdf", "full"),
    )),
    ("it", f"{BASE}/it/sintesi-rapporto-annuale-2013/", (
        ("Sintesi Rapporto annuale 2013", f"{BASE}/wp-content/uploads/2021/01/BOM_Executive-Summary_IT.pdf", "summary"),
    )),
    ("en", f"{BASE}/en/executive-summary-annual-report-2013/", (
        ("Executive Summary 2013 EN", f"{BASE}/wp-content/uploads/2020/12/BOM_Executive-Summary_EN.pdf", "summary"),
    )),
)

# Fallbeispiele / Mitteilungen category roots, one per UI language (WPML
# duplicates taxonomy terms per language with distinct ids — see docstring).
CASE_ROOT_SLUGS = {"de": "fallsammlung", "fr": "recueil-de-cas",
                   "it": "raccolta-di-casi", "en": "selected-cases"}
MESSAGE_ROOT_SLUGS = {"de": "mitteilungen", "fr": "informations",
                      "it": "informazioni", "en": "messages"}

_FULL_RE = re.compile(
    r"^(?:Jahresbericht|Rapport annuel|Rapporto annuale|Annual Report)\s+(\d{4})$", re.I)
_CROSS_REF_RE = re.compile(
    r"^(?:Jahresbericht|Rapport annuel|Rapporto annuale|Annual Report)\s+\d{4}\s*"
    r"\((?:Tedesco|Francese|German|French)\)$", re.I)
_SUMMARY_RE = {
    "de": re.compile(r"^(\d{4})\s+in\s+Kürze$", re.I),
    "fr": re.compile(r"^(\d{4})\s+en\s+bref$", re.I),
    "it": re.compile(r"^Il\s+(\d{4})\s+in\s+breve$", re.I),
    "en": re.compile(r"^Annual Report\s+(\d{4})\s+Summary\s*\(English\)$", re.I),
}
_PUB_LANG_SUFFIX = re.compile(r"\s+(DE|FR|IT|EN)\.?$")


def _clean(s: str) -> str:
    # NFC-normalise first: the site mixes precomposed "ü" with a bare "u" +
    # combining diaeresis across otherwise-identical titles (seen on the
    # Jahresberichte page — only the newest year used the precomposed form),
    # which would silently fail every regex below despite looking identical.
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def parse_file_tiles(html: str) -> list[dict]:
    """[{title, url}] for every `wp-block-file` tile inside `.entry-content`
    — the WordPress block both the Jahresberichte and the Stellungnahmen
    pages use (one PDF per block, two anchors sharing the same href — the
    title link and a "Herunterladen"/"Download" button)."""
    soup = BeautifulSoup(html, "html.parser")
    entry = soup.select_one(".entry-content") or soup
    out = []
    for tile in entry.select("div.wp-block-file"):
        a = tile.find("a", href=True)
        if not a:
            continue
        out.append({"title": _clean(a.get_text()), "url": a["href"]})
    return out


def classify_report(title: str, page_lang: str) -> dict | None:
    """{year, kind: "full"|"summary"} for a Jahresberichte-page tile title,
    or None for a cross-reference tile (the IT/EN pages relisting the DE/FR
    file) or anything else unrecognised."""
    title = _clean(title)
    if _CROSS_REF_RE.match(title):
        return None
    m = _FULL_RE.match(title)
    if m:
        return {"year": m.group(1), "kind": "full"}
    m = _SUMMARY_RE[page_lang].match(title)
    if m:
        return {"year": m.group(1), "kind": "summary"}
    return None


def classify_publication(title: str, page_lang: str) -> dict:
    """{title (language tag stripped), language} for a Stellungnahmen-page
    tile. Most titles end with an explicit "DE"/"FR"/"IT"/"EN" tag (the
    site's own convention); the handful that don't (the Faktenblatt, on
    every page in its own translation) take the page's own language."""
    title = _clean(title)
    m = _PUB_LANG_SUFFIX.search(title)
    if m:
        return {"title": _clean(title[:m.start()]), "language": m.group(1).lower()}
    return {"title": title, "language": page_lang}


def publication_doc_type(title: str) -> str:
    t = title.lower()
    if re.search(r"faktenblatt|fiche d.information|guida informativa|factsheet", t):
        return "merkblatt"
    return "bericht"


def hreflang_links(html: str) -> dict[str, str]:
    """{language: url} from a page's <link rel="alternate" hreflang=...>
    tags (WPML), excluding "x-default"."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for link in soup.find_all("link", attrs={"rel": "alternate"}):
        hl, href = link.get("hreflang"), link.get("href")
        if hl and href and hl != "x-default":
            out[hl] = href
    return out


def html_to_text(html: str, preserve_breaks: bool = True) -> str:
    """Plain text of a WP REST `rendered` HTML fragment. `preserve_breaks`
    keeps one line per block element (paragraphs, headings) — right for a
    document body, wrong for a `title.rendered` fragment, where an inline
    tag (`<em>`, `<a>`) must not split one sentence across lines."""
    soup = BeautifulSoup(html or "", "html.parser")
    if preserve_breaks:
        text = unicodedata.normalize("NFC", soup.get_text("\n", strip=True))
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return _clean(unicodedata.normalize("NFC", soup.get_text(" ")))


def collect_descendant_category_ids(categories: list[dict], root_slug: str) -> set[int]:
    """Every category id under `root_slug` (its direct children AND their
    own children, e.g. Mitteilungen > Aktuelles), minus any whose slug
    contains "exclude" — the site's own name for posts kept off the public
    listing. Works off a flat /wp-json/wp/v2/categories?per_page=100
    listing (each item has id/slug/parent)."""
    by_id = {c["id"]: c for c in categories}
    root = next((c for c in categories if c["slug"] == root_slug), None)
    if root is None:
        return set()
    ids = {root["id"]}
    changed = True
    while changed:
        changed = False
        for c in categories:
            if c.get("parent") in ids and c["id"] not in ids and "exclude" not in c["slug"]:
                ids.add(c["id"])
                changed = True
    ids.discard(root["id"])  # the root itself holds no posts on these sites
    return {i for i in ids if "exclude" not in by_id[i]["slug"]}


class BankingOmbudsmanScraper(PracticeScraper):
    SOURCE_KEY = "bankingombudsman"
    ISSUING_AUTHORITY = "Bankenombudsman"
    DEFAULT_DOC_TYPE = "bericht"
    REQUEST_DELAY = 1.0
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['bo_id']}"

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def _json(self, url: str):
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
            return None

    def _categories(self, lang: str) -> list[dict]:
        data = self._json(f"{REST}/categories?lang={lang}&per_page=100")
        return data if isinstance(data, list) else []

    def _posts(self, lang: str, category_ids: set[int]) -> Iterator[dict]:
        if not category_ids:
            return
        cats = ",".join(str(i) for i in sorted(category_ids))
        page = 1
        while True:
            r = None
            try:
                r = self.get(f"{REST}/posts", params={
                    "lang": lang, "categories": cats, "per_page": 100, "page": page,
                    "_fields": "id,link,date,title,content",
                })
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] posts %s page %d failed: %s", self.SOURCE_KEY, lang, page, e)
                return
            items = r.json() or []
            if not isinstance(items, list) or not items:
                return
            yield from items
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
            if page >= total_pages:
                return
            page += 1

    # ── discovery ───────────────────────────────────────────────────

    def discover_documents(self) -> Iterator[dict]:
        yield from self._reports()
        yield from self._legacy_2013()
        yield from self._publications()
        yield from self._translated_posts(MESSAGE_ROOT_SLUGS, "mitteilung", "SBO Mitteilung", "msg")
        yield from self._translated_posts(CASE_ROOT_SLUGS, "rechtsprechung", "SBO Fall", "case")

    def _reports(self) -> Iterator[dict]:
        for lang, url in REPORT_PAGES:
            html = self._page(url)
            if html is None:
                continue
            tiles = parse_file_tiles(html)
            logger.info("[%s] %s: %d report tiles", self.SOURCE_KEY, url, len(tiles))
            for t in tiles:
                info = classify_report(t["title"], lang)
                if info is None:
                    continue
                kind, year = info["kind"], info["year"]
                number = f"SBO Jahresbericht {year}" + (" Kurzfassung" if kind == "summary" else "")
                yield {
                    "pdf_url": t["url"],
                    "url": url,
                    "title": t["title"],
                    "doc_number": number,
                    "date": year,
                    "language": lang,
                    "doc_type": "bericht",
                    "topics": ["Jahresbericht", number],
                    "bo_id": f"report_{year}_{kind}_{lang}",
                }

    def _legacy_2013(self) -> Iterator[dict]:
        for lang, page_url, pdfs in LEGACY_2013:
            for title, pdf_url, kind in pdfs:
                number = "SBO Jahresbericht 2013" + (" Kurzfassung" if kind == "summary" else "")
                yield {
                    "pdf_url": pdf_url,
                    "url": page_url,
                    "title": title,
                    "doc_number": number,
                    "date": "2013",
                    "language": lang,
                    "doc_type": "bericht",
                    "topics": ["Jahresbericht", number],
                    "bo_id": f"report_2013_{kind}_{lang}_{slugify(unquote(pdf_url.rsplit('/', 1)[-1]))[:30]}",
                }

    def _publications(self) -> Iterator[dict]:
        seen_urls: set[str] = set()
        for lang, url in PUBLICATION_PAGES:
            html = self._page(url)
            if html is None:
                continue
            tiles = parse_file_tiles(html)
            logger.info("[%s] %s: %d publication tiles", self.SOURCE_KEY, url, len(tiles))
            for t in tiles:
                if t["url"] in seen_urls:
                    continue
                seen_urls.add(t["url"])
                info = classify_publication(t["title"], lang)
                title, doc_lang = info["title"], info["language"]
                number = f"SBO {title}"[:160]
                yield {
                    "pdf_url": t["url"],
                    "url": url,
                    "title": title,
                    "doc_number": number,
                    "date": first_date_iso(title) or "",
                    "language": doc_lang,
                    "doc_type": publication_doc_type(title),
                    "topics": ["Stellungnahmen und Publikationen", number],
                    "bo_id": f"pub_{slugify(unquote(t['url'].rsplit('/', 1)[-1]))[:60]}_{doc_lang}",
                }

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        # Only fill in a MISSING date from the body (the Stellungnahmen page
        # titles don't always carry one). A year-only date ("2022" — the
        # Jahresberichte/legacy stubs always set this from the page title)
        # is left alone: scanning the body for a date instead is actively
        # wrong there — e.g. the 2022 Jahresbericht's body opens with a
        # forward-looking reference to 2023, which first_date_iso would
        # otherwise substitute for the report's real year.
        if not doc["date"]:
            doc["date"] = first_date_iso(body_text[:2500])
        return doc

    # ── Fallbeispiele / Mitteilungen (WP REST, DE-hub translation grouping) ──

    def _translated_posts(self, root_slugs: dict, doc_type: str, number_prefix: str,
                           id_prefix: str) -> Iterator[dict]:
        cat_ids: dict[str, set[int]] = {}
        for lang, slug in root_slugs.items():
            cats = self._categories(lang)
            cat_ids[lang] = collect_descendant_category_ids(cats, slug) if cats else set()
        posts_by_link: dict[str, dict] = {}
        for lang, ids in cat_ids.items():
            posts = {p["link"]: p for p in self._posts(lang, ids)}
            posts_by_link[lang] = posts
            logger.info("[%s] %s %s: %d posts", self.SOURCE_KEY, id_prefix, lang, len(posts))

        claimed: dict[str, set[str]] = {lang: set() for lang in root_slugs}
        for link, post in posts_by_link.get("de", {}).items():
            claimed["de"].add(link)
            number = f"{number_prefix} {post['id']}"
            de_id = f"{id_prefix}_de_{post['id']}"
            yield self._post_stub(post, "de", number, doc_type, de_id)
            if self._make_doc_id({"bo_id": de_id}) in self._seen_ids:
                continue  # already known — don't re-pay the hreflang fetch
            html = self._page(link)
            alt = hreflang_links(html) if html else {}
            for lang in ("fr", "it", "en"):
                href = alt.get(lang)
                other = posts_by_link.get(lang, {}).get(href) if href else None
                if other is None:
                    continue
                claimed[lang].add(href)
                yield self._post_stub(other, lang, number, doc_type,
                                       f"{id_prefix}_{lang}_{other['id']}")

        for lang in ("fr", "it", "en"):
            for link, post in posts_by_link.get(lang, {}).items():
                if link in claimed[lang]:
                    continue
                number = f"{number_prefix} {lang}{post['id']}"
                yield self._post_stub(post, lang, number, doc_type,
                                       f"{id_prefix}_{lang}_{post['id']}")

    def _post_stub(self, post: dict, lang: str, number: str, doc_type: str, bo_id: str) -> dict:
        title = html_to_text(post.get("title", {}).get("rendered", ""), preserve_breaks=False)
        body = html_to_text(post.get("content", {}).get("rendered", ""))
        return {
            "url": post.get("link", ""),
            "title": title,
            "doc_number": number,
            "date": (post.get("date") or "")[:10],
            "language": lang,
            "doc_type": doc_type,
            "topics": [number],
            "body_text": body,
            "bo_id": bo_id,
        }
