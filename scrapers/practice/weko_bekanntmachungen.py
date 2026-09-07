"""
WEKO Bekanntmachungen und Erläuterungen (Wettbewerbskommission)
================================================================

  de  https://www.weko.admin.ch/de/bekanntmachungen-erlaeuterungen
  fr  https://www.weko.admin.ch/fr/communications-notes-explicatives
  it  https://www.weko.admin.ch/it/comunicazioni-2

The Competition Commission's general notices under Art. 6 KG — the
Vertikalbekanntmachung, the KMU-Bekanntmachung, the (former) KFZ-
Bekanntmachung, the Bekanntmachung zur Verwendung von Kalkulationshilfen —
and the Erläuterungen the Secretariat publishes to them (Vertikal-, KFZ-
Verordnung, SVKG), plus the Richtlinien für ökonomische Gutachten and the
Richtlinie zu den Beziehungen zu den Zivilgerichten. They state how the
authority will apply the Kartellgesetz and are cited in every vertical-
restraints memo, so they belong in practice.db next to the FINMA circulars,
not in the decision corpus (which holds the WEKO Verfügungen, court='weko').

One page per language, one download list. Three sections matter:

  * the thematic sections at the top hold the versions in force;
  * "Weitere Bekanntmachungen" / "Diverses" hold the rest of the current set;
  * "Archiv" holds every superseded version, each prefixed "Nicht aktuell:"
    (FR "Non actuelle:", IT "Non aggiornata:", with the site's own typos).

Versioning (FINMA precedent): one row per (document, version, language).
The document key is the title without its dates and status prefix
("Vertikalbekanntmachung", "Erläuterungen zur KFZ-Bekanntmachung"); the
version date is the "(Stand …)" / "(état au …)" / "(stato al …)" date when
there is one, else the issuance date after "vom" / "du" / "del", else the
file date. search_practice partitions by doc_number and shows the newest
version unless include_superseded is set, so the 2022 Vertikalbekanntmachung
hides its 2010 and 2017 predecessors by default and a question about 2015
conduct can still read the version then in force. Status is carried in
topics with the same wording FINMA uses, so one query finds both corpora.

Language: the page's. The IT page borrows two German files ("(versione
tedesca)", "(tedesco)"); those rows are dropped and come in from the DE page.

Volume: ~20 documents per language, ~60 rows. PDF URLs are DAM-hashed and
change when a file is replaced -> REVISION_FIELD="pdf_url".
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from .base import PracticeScraper, first_date_iso, slugify
from .finma_rundschreiben import _STATUS_IN_FORCE, _STATUS_SUPERSEDED
from .sdweb import download_items

logger = logging.getLogger(__name__)

_BASE = "https://www.weko.admin.ch"
PAGES = {
    "de": "/de/bekanntmachungen-erlaeuterungen",
    "fr": "/fr/communications-notes-explicatives",
    "it": "/it/comunicazioni-2",
}

# "Nicht aktuell:", "Nicht aktuelle:", "Non actuelle:", "Non acutelle :",
# "Non aggiornata:", "Non aggionrato:" — the site's own spellings, all of
# them meaning superseded.
_SUPERSEDED_PREFIX = re.compile(r"^\s*(?:nicht\s+aktuell\w*|non\s+a\w+)\s*:\s*", re.IGNORECASE)
_ARCHIVE_SECTION = re.compile(r"^(?:Archiv|Archives|Archivio)\b", re.IGNORECASE)
# version date: "(Stand 22. Mai 2017)", "(état au 22 mai 2017)", "(stato 22 maggio 2017)",
# "(stato al 09-09-2019)", "(entre en vigueur le 1er janvier 2016)", "(in vigore dal 1° gennaio 2016)"
_VERSION_DATE = re.compile(
    r"\(\s*(?:Stand|état\s+au|stato(?:\s+al)?|entre\s+en\s+vigueur\s+le|in\s+vigore\s+dal|in\s+Kraft\s+(?:seit|ab))"
    r"\s+([^)]+)\)",
    re.IGNORECASE,
)
# issuance date: "vom 12. Dezember 2022", "du 12 décembre 2022", "del 29-06-2015", "dal 28 giugno 2010"
_ISSUED = re.compile(
    r"\b(?:vom|du|del|dal)\s+(\d{1,2}(?:\.|er|°)?\s*[A-Za-zÀ-ÿ]+\s+\d{4}|\d{1,2}[.\-]\d{1,2}[.\-]\d{4})",
    re.IGNORECASE,
)
_LANG_NOTE = re.compile(
    r"\(\s*(?:versione\s+tedesca|tedesco|deutsch|version\s+allemande|en\s+allemand|"
    r"disponibile(?:\s+solo)?\s+in\s+(?:francese|tedesco|italiano)|"
    r"nur\s+auf\s+(?:Deutsch|Französisch|Italienisch)|französisch|italienisch)\s*\)",
    re.IGNORECASE,
)
_NOISE = re.compile(r"\(\s*\d\s*\)")          # "(1)" on a re-uploaded FR file
_HYPHEN_DATE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")   # IT "29-06-2015"

_TYPE_RULES = (
    (re.compile(r"Erläuterung|Notes?\s+explicatives?|Spiegazion|Opuscolo\s+esplicativo|Note\s+esplicative", re.I), "erlaeuterung"),
    (re.compile(r"Richtlinie|Directives?\s+pour|Direttive\s+sull|Note\s+sur\s+les\s+relations", re.I), "richtlinie"),
    # "Communicazione", "Comunicazioe", "Communicatione": the IT page's own spellings
    (re.compile(r"Bekanntmachung|Comm?unications?\b|Comm?unicazio|Comm?unicatione", re.I), "bekanntmachung"),
)

_LANG_FROM_NOTE = (
    (re.compile(r"tedesc|deutsch|allemand", re.I), "de"),
    (re.compile(r"frances|français|französisch", re.I), "fr"),
    (re.compile(r"italian|italienisch", re.I), "it"),
)


def _iso(text: str) -> str:
    """first_date_iso plus the Italian page's hyphenated day-month-year and
    its degree-sign ordinal ("1° gennaio 2016")."""
    return first_date_iso(_HYPHEN_DATE.sub(r"\1.\2.\3", (text or "").replace("°", "")))


def _doc_type(title: str) -> str:
    for rx, kind in _TYPE_RULES:
        if rx.search(title):
            return kind
    return "mitteilung"


def _language(page_lang: str, title: str) -> str | None:
    """The page's language, or None when the title says the file is in another
    one ("(versione tedesca)", "(tedesco)"): that language's page lists the
    file itself, so the row would only duplicate it under a foreign title."""
    m = _LANG_NOTE.search(title)
    if not m:
        return page_lang
    for rx, lang in _LANG_FROM_NOTE:
        if rx.search(m.group(0)):
            return page_lang if lang == page_lang else None
    return page_lang


def _document_key(title: str) -> str:
    """The title without status prefix, dates and language notes:
    "Nicht aktuell: Vertikalbekanntmachung vom 28. Juni 2010 (Stand 22. Mai 2017)"
    -> "Vertikalbekanntmachung"."""
    key = _SUPERSEDED_PREFIX.sub("", title)
    key = _VERSION_DATE.sub("", key)
    key = _ISSUED.sub("", key)
    key = _LANG_NOTE.sub("", key)
    key = _NOISE.sub("", key)
    key = " ".join(key.split()).strip(" ,;:-–")
    return key or " ".join(title.split())


def parse_page(html: str, lang: str, page_url: str) -> list[dict]:
    stubs: list[dict] = []
    ids: set[str] = set()
    for item in download_items(html, _BASE):
        title = item["title"]
        superseded = bool(_SUPERSEDED_PREFIX.match(title)) or bool(_ARCHIVE_SECTION.match(item["section"]))
        key = _document_key(title)
        vm = _VERSION_DATE.search(title)
        version_date = _iso(vm.group(1)) if vm else ""
        im = _ISSUED.search(title)
        issued = _iso(im.group(1)) if im else ""
        date = version_date or issued or item["file_date"]
        language = _language(lang, title)
        if language is None:
            logger.debug("[weko_bekanntmachungen] %s row skipped, file is in another language: %s", lang, title)
            continue
        doc_id_key = f"{slugify(key)[:50]}_{date.replace('-', '') or 'undated'}_{language}"
        if doc_id_key in ids:
            logger.warning("[weko_bekanntmachungen] duplicate id on %s page: %s (%s)", lang, doc_id_key, title)
            continue
        ids.add(doc_id_key)
        topics = [item["section"], key] if item["section"] else [key]
        if issued and version_date and issued != version_date:
            topics.append(f"Erlass {issued}")
        topics += _STATUS_SUPERSEDED if superseded else _STATUS_IN_FORCE
        stubs.append({
            "pdf_url": item["pdf_url"],
            "url": page_url,
            "title": title,
            "doc_number": key,
            "date": date,
            "language": language,
            "doc_type": _doc_type(key),
            "topics": [t for t in topics if t],
            "weko_id_key": doc_id_key,
        })
    return stubs


class WekoBekanntmachungenScraper(PracticeScraper):
    SOURCE_KEY = "weko_bekanntmachungen"
    ISSUING_AUTHORITY = "WEKO"
    DEFAULT_DOC_TYPE = "bekanntmachung"
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.5
    LANGUAGES = ("de", "fr", "it")
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['weko_id_key']}"

    def discover_documents(self) -> Iterator[dict]:
        for lang in self.LANGUAGES:
            url = _BASE + PAGES[lang]
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] %s fetch failed: %s", self.SOURCE_KEY, url, e)
                continue
            stubs = parse_page(r.text, lang, url)
            logger.info("[%s] %s: %d documents", self.SOURCE_KEY, lang, len(stubs))
            yield from stubs
