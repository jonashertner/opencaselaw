"""
BJ — international mutual legal assistance in criminal matters (Rechtshilfe)
=============================================================================

Federal Office of Justice (BJ), Fachbereich internationale Rechtshilfe in
Strafsachen (the "RHF" unit, also the operator of its own rhf.admin.ch
site). Four pages, all on the current Nuxt "download-item" markup shared
with EHRA/WEKO/OAK BV — but with a twist two of them don't have: the
Wegleitungen page prints a <p class="download-item__description"> between
the <h4> title and the <p class="download-item__meta-info">, which
sdweb.download_items (positional: `a.find("p")`) would misread as the meta
text. `_dam_items` below picks each <p> by its class instead.

Pages
-----
  de /de/internationale-rechtshilfe-in-strafsachen
  fr /fr/entraide-judiciaire-internationale-en-matiere-penale
  it /it/assistenza-giudiziaria-internazionale-in-materia-penale
      -> the 10 annual "Tätigkeitsbericht" reports (2015-2024) + the
         yearly Statistik, DE/FR/IT (genuinely distinct per language: same
         DAM hash, different /dam/{lang}/ content — verified 2026-11-08 by
         Content-Length, not assumed).

  de /de/auslieferung   fr /fr/extradition   it /it/estradizione
      -> extradition guidance: a Fact Sheet, a "Darlegung des Auslieferungs-
         verfahrens" explainer and a Checkliste, each DE/FR/IT (same hash,
         real per-language content) — plus the Darlegung's additional
         translations into es/pt/ro/ru/sq/sr/hr, which exist ONCE (same
         hash and byte-identical regardless of which site-language page
         links them) and are tagged by the language the FILENAME's suffix
         names, not by the page they were found on.

  rhf.admin.ch /de/muster-und-formulare (DE/FR/IT via the same slug under
      /fr/ and /it/) -> muster-ersuchen-strafrecht.pdf, the Muster-Ersuchen
      the task named (the other model-request files on that page are civil
      law — HBewUe70/HUE54, the Hague evidence/service conventions — and
      are left out).

  rhf.admin.ch/de/wegleitungen-checklisten-und-rundschreiben, with its
      FR (/fr/directives-aide-memoire-et-circulaires) and IT
      (/it/direttive-promemoria-e-circolari) twins (both in the site's own
      sitemap; found 2026-11-08 after the first pass had missed them) ->
      the Wegleitung "Die internationale Rechtshilfe in Strafsachen"
      (current edition, 15.10.2025; FR "Directives en matière pénale";
      its old English translation is the 2009 edition — rhf.admin.ch keeps
      no newer English edition), the bilateral Wegleitung Schweiz-Italien,
      and (since 2026-11-08) everything else on the page: the BJ/fedpol
      Aufgabenabgrenzung, Rundschreiben Nr. 1-7, the two Infomails (EPPO,
      Russia), the checklists for foreign requests and for the Swiss
      authorities' review, the asset-recovery brochure, the Merkblatt and
      Checkliste on stellvertretende Strafverfolgung, the prisoner-transfer
      Merkblätter and Länderliste and the Musterprotokoll for the transfer
      hearing (see RHF_FAMILIES). Files whose name carries a language
      suffix (-e, -es, -uk, -ar) are the same file on all three pages and
      are taken once, from the first page that lists them. The
      Personenfahndung checklist on this page is the one the Auslieferung
      family already takes from bj.admin.ch, so it is skipped here.

NOTE: the old rhf.admin.ch/dam/rhf/... URL pattern and the
backend.bj.admin.ch/fileservice/... host named when this scraper was
scoped both 404 now; every document above was re-found live on
2026-11-08 under the current /dam/{lang}/sd-web/<hash>/<file> pattern.

Excluded entirely: /de/gesuchsunterlagen-und-formulare — despite living
under bj.admin.ch's Rechtshilfe section, it is the international MAINTENANCE
(Alimente) collection process, a civil-law matter, not criminal.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import PracticeScraper, dateline_iso, first_date_iso, slugify
from .langdedup import TextDedupMixin

logger = logging.getLogger(__name__)

_BJ = "https://www.bj.admin.ch"
_RHF = "https://www.rhf.admin.ch"

MAIN_PAGES = (
    ("de", _BJ + "/de/internationale-rechtshilfe-in-strafsachen"),
    ("fr", _BJ + "/fr/entraide-judiciaire-internationale-en-matiere-penale"),
    ("it", _BJ + "/it/assistenza-giudiziaria-internazionale-in-materia-penale"),
)

AUSLIEFERUNG_PAGES = (
    ("de", _BJ + "/de/auslieferung"),
    ("fr", _BJ + "/fr/extradition"),
    ("it", _BJ + "/it/estradizione"),
)

MUSTER_PAGES = (
    ("de", _RHF + "/de/muster-und-formulare"),
    ("fr", _RHF + "/fr/modeles-et-formulaires"),
    ("it", _RHF + "/it/modelli-e-formulari"),
)

WEGLEITUNGEN_PAGES = (
    ("de", _RHF + "/de/wegleitungen-checklisten-und-rundschreiben"),
    ("fr", _RHF + "/fr/directives-aide-memoire-et-circulaires"),
    ("it", _RHF + "/it/direttive-promemoria-e-circolari"),
)

# filename stem (language suffix removed) on the rhf.admin.ch Wegleitungen
# page -> (id key, doc_number, doc_type). The two Wegleitungen keep the id
# keys the first build gave them ("strafsachen", "italien"), so rows already
# in bj_rechtshilfe.jsonl are recognised and not fetched again.
RHF_FAMILIES: dict[str, tuple[str, str, str]] = {
    "wegleitung-strafsachen": ("wegleitung_strafsachen",
                               "BJ Wegleitung Internationale Rechtshilfe Strafsachen", "wegleitung"),
    "wegleitung-rechtshilfe-italien": ("wegleitung_italien",
                                       "BJ Wegleitung Rechtshilfe Schweiz-Italien", "wegleitung"),
    "aufgabenabgrenzung-bj-fedpol": ("rhf_aufgabenabgrenzung",
                                     "BJ Aufgabenabgrenzung Rechtshilfe und Polizeikooperation (BJ/fedpol)",
                                     "merkblatt"),
    "rundschreiben-datendiebstahl": ("rhf_rs_1", "BJ Rechtshilfe Rundschreiben Nr. 1", "rundschreiben"),
    "rundschreiben-cyber-crime-convention": ("rhf_rs_2", "BJ Rechtshilfe Rundschreiben Nr. 2", "rundschreiben"),
    "rundschreiben-videokonferenz": ("rhf_rs_3", "BJ Rechtshilfe Rundschreiben Nr. 3", "rundschreiben"),
    "rundschreiben-anzeigenweiterleitung": ("rhf_rs_4", "BJ Rechtshilfe Rundschreiben Nr. 4", "rundschreiben"),
    "rundschreiben-mitteilungsverbot": ("rhf_rs_5", "BJ Rechtshilfe Rundschreiben Nr. 5", "rundschreiben"),
    "rundschreiben-statistikdaten": ("rhf_rs_6", "BJ Rechtshilfe Rundschreiben Nr. 6", "rundschreiben"),
    "rundschreiben-sharing": ("rhf_rs_7", "BJ Rechtshilfe Rundschreiben Nr. 7", "rundschreiben"),
    "infomail-2023-02-10": ("rhf_infomail_1", "BJ Rechtshilfe Infomail Nr. 1", "mitteilung"),
    "infomail-2023-03-21": ("rhf_infomail_2", "BJ Rechtshilfe Infomail Nr. 2", "mitteilung"),
    "checkliste-ersuchen-ausland": ("rhf_checkliste_ersuchen_ausland",
                                    "BJ Checkliste ausländische Rechtshilfeersuchen in Strafsachen", "merkblatt"),
    "checkliste-justizbehoerden": ("rhf_checkliste_justizbehoerden",
                                   "BJ Checkliste Prüfung ausländischer Rechtshilfeersuchen", "merkblatt"),
    "asset-recovery": ("rhf_asset_recovery",
                       "BJ Rückführung unrechtmässig erworbener Vermögenswerte (Asset Recovery)", "merkblatt"),
    "mb-strafuebernahmebegehren": ("rhf_mb_strafuebernahme",
                                   "BJ Merkblatt stellvertretende Strafverfolgung", "merkblatt"),
    "checkliste-strafuebernahmebegehren": ("rhf_checkliste_strafuebernahme",
                                           "BJ Checkliste Strafübernahmebegehren", "merkblatt"),
    "mb-schweizer-im-ausland-verurteilt": ("rhf_mb_ueberstellung_ch_im_ausland",
                                           "BJ Merkblatt Überstellung: im Ausland verurteilte Schweizer",
                                           "merkblatt"),
    "mb-auslaender-in-ch-verurteilt": ("rhf_mb_ueberstellung_auslaender_in_ch",
                                       "BJ Merkblatt Überstellung: in der Schweiz verurteilte Ausländer",
                                       "merkblatt"),
    "mb-laenderliste-ueberstellungen": ("rhf_laenderliste_ueberstellungen",
                                        "BJ Überstellung Länderliste", "merkblatt"),
    "mb-zp-schweizer-im-ausland-verurteilt": ("rhf_mb_zp_ueberstellung",
                                              "BJ Merkblatt Überstellung nach dem Zusatzprotokoll",
                                              "merkblatt"),
    "mb-zp-auslaender-in-ch-verurteilt": ("rhf_mb_zp_ausweisung",
                                          "BJ Merkblatt Überstellung bei Ausweisung (Zusatzprotokoll)",
                                          "merkblatt"),
    "musterprotokoll-ueberstellung-zp": ("rhf_musterprotokoll_ueberstellung",
                                         "BJ Musterprotokoll Einvernahme vor Überstellung", "formular"),
}
# On the page, but taken elsewhere: the Auslieferung family reads the same
# Personenfahndung checklist from bj.admin.ch.
RHF_SKIP = {"checkliste-personenfahndung"}

# rhf.admin.ch re-stamped files it migrated with this date; it is not the
# document's date.
_RHF_MIGRATION_DATE = "2020-07-13"


def classify_rhf_item(pdf_url: str, page_lang: str) -> dict | None:
    """{"key", "doc_number", "doc_type", "language", "suffixed"} for one PDF
    on an rhf.admin.ch Wegleitungen page, or None for a file this source
    skips. An unknown stem is logged by the caller and skipped, so a new
    file on the page shows up in the log instead of under a made-up
    doc_number."""
    base, suffix_lang = split_lang_suffix(_filename_stem(pdf_url))
    if base in RHF_SKIP:
        return None
    family = RHF_FAMILIES.get(base)
    if family is None:
        return None
    key, number, doc_type = family
    return {"key": key, "doc_number": number, "doc_type": doc_type,
            "language": suffix_lang or page_lang, "suffixed": suffix_lang is not None,
            "base": base}

_AUSLIEFERUNG_TITLES = {
    "auslieferung": "BJ Merkblatt Auslieferung",
    "darlegung-auslieferung": "BJ Darlegung des Auslieferungsverfahrens",
    "checkliste-personenfahndung": "BJ Checkliste internationale Personenfahndung",
}

# Filename language suffixes seen on the Auslieferung page's extra
# translations (not Swiss official languages, so they're not reachable any
# other way than from the filename).
_LANG_SUFFIX = {"es": "es", "pt": "pt", "ro": "ro", "ru": "ru", "sq": "sq",
                "sr": "sr", "hr": "hr", "ar": "ar", "uk": "uk", "e": "en"}


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _dam_items(html: str, base_url: str) -> list[dict]:
    """download-item anchors, admin.ch's current Nuxt markup. Like
    sdweb.download_items but picks the description and meta-info
    paragraphs by CLASS, not position — needed because the Wegleitungen
    page prints both, and sdweb.download_items' `a.find("p")` would catch
    the description where only the meta-info one carries a parseable
    date."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out = []
    for a in soup.find_all("a", href=True):
        classes = a.get("class") or []
        if "download-item" not in classes:
            continue
        href = a["href"]
        pdf_url = urljoin(base_url, href)
        if not urlparse(pdf_url).path.lower().endswith(".pdf"):
            continue
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        h4 = a.find("h4")
        title = _clean(h4.get_text(" ", strip=True)) if h4 else ""
        meta_p = a.find("p", class_="download-item__meta-info")
        meta = _clean(meta_p.get_text(" ", strip=True)) if meta_p else ""
        heading = a.find_previous(["h1", "h2", "h3"])
        out.append({
            "title": title,
            "meta": meta,
            "file_date": first_date_iso(meta),
            "pdf_url": pdf_url,
            "section": _clean(heading.get_text(" ", strip=True)) if heading else "",
        })
    return out


def _dam_hash(pdf_url: str) -> str:
    """The hash segment of an admin.ch /dam/{lang}/sd-web/<hash>/<file>
    URL — shared across a document's language prefixes even when the
    served content differs per language (verified for every family here by
    Content-Length, not assumed)."""
    parts = urlparse(pdf_url).path.split("/")
    try:
        return parts[parts.index("sd-web") + 1]
    except (ValueError, IndexError):
        return urlparse(pdf_url).path


def _filename_stem(pdf_url: str) -> str:
    name = urlparse(pdf_url).path.rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".pdf") else name


def split_lang_suffix(stem: str) -> tuple[str, str | None]:
    """("darlegung-auslieferung-es", "es"), ("auslieferung", None) — the
    longest matching suffix wins so "-es" isn't mistaken for a shorter one."""
    for suf in sorted(_LANG_SUFFIX, key=len, reverse=True):
        if stem.endswith(f"-{suf}"):
            return stem[: -(len(suf) + 1)], _LANG_SUFFIX[suf]
    return stem, None


_YEAR = re.compile(r"\b(20\d{2})\b")

# "10. Auflage 2025", "9th edition 2009", "1re édition 2003", "1a edizione 2003"
_EDITION = re.compile(
    r"\b(?:\d{1,2}(?:\.|st|nd|rd|th|e|re|ème|a)?|prima|seconda|terza|quarta|quinta|sesta|settima"
    r"|ottava|nona|decima)\s+(?:Auflage|edition|édition|edizione)\s+((?:19|20)\d{2})\b",
    re.I)


def edition_year(text: str) -> str:
    """The year of the edition printed on a guide's cover, or ""."""
    m = _EDITION.search(text or "")
    return m.group(1) if m else ""


class BjRechtshilfeScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "bj_rechtshilfe"
    ISSUING_AUTHORITY = "BJ"
    DEFAULT_DOC_TYPE = "bericht"
    REVISION_FIELD = "pdf_url"
    REQUEST_DELAY = 1.5
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['bj_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        if doc["doc_type"] == "wegleitung":
            # rhf.admin.ch stamped several old files with its 2020-07-13
            # migration date; the edition printed on the cover is the date a
            # reader cites ("9th edition 2009", "1. Auflage 2003").
            year = edition_year(body_text[:1500])
            if year and not doc["date"].startswith(year):
                doc["date"] = year
        if not doc["date"]:
            # rows whose listing date was rhf.admin.ch's migration stamp
            # read the letterhead date first ("Bern, 15. Januar 2014")
            doc["date"] = ((dateline_iso(body_text[:3000]) if stub.get("date_from_text") else "")
                           or first_date_iso(body_text[:2500]))
        return doc

    # ── annual reports + statistics ──

    def _report_stubs(self) -> Iterator[dict]:
        for lang, url in MAIN_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] main page %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = _dam_items(r.text, url)
            logger.info("[%s] %s main page: %d PDFs", self.SOURCE_KEY, lang, len(items))
            for it in items:
                m = re.search(r"jb-irh-(\d{4})\.pdf", it["pdf_url"])
                if m:
                    year = m.group(1)
                    yield {
                        "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                        "doc_number": f"BJ Tätigkeitsbericht Internationale Rechtshilfe {year}",
                        "date": first_date_iso(it["meta"]) or it["file_date"] or year,
                        "language": lang, "doc_type": "bericht",
                        "topics": ["Internationale Rechtshilfe", "Tätigkeitsbericht", year],
                        "bj_id": f"jb_irh_{year}_{lang}",
                    }
                elif "rhf-statistik.pdf" in it["pdf_url"]:
                    yield {
                        "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                        "doc_number": "BJ Statistik Internationale Rechtshilfe",
                        "date": it["file_date"],
                        "language": lang, "doc_type": "bericht",
                        "topics": ["Internationale Rechtshilfe", "Statistik"],
                        "bj_id": f"rhf_statistik_{lang}",
                    }

    # ── Auslieferung ──

    def _auslieferung_stubs(self) -> Iterator[dict]:
        for page_lang, url in AUSLIEFERUNG_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] Auslieferung %s fetch failed: %s", self.SOURCE_KEY, page_lang, e)
                continue
            items = _dam_items(r.text, url)
            logger.info("[%s] Auslieferung %s: %d PDFs", self.SOURCE_KEY, page_lang, len(items))
            for it in items:
                base, suffix_lang = split_lang_suffix(_filename_stem(it["pdf_url"]))
                number = _AUSLIEFERUNG_TITLES.get(base)
                if not number:
                    continue       # a Word template (muster-haftbefehl etc.) or unrecognised file
                lang = suffix_lang or page_lang
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": number,
                    "date": it["file_date"],
                    "language": lang, "doc_type": "merkblatt",
                    "topics": ["Auslieferung", "Internationale Rechtshilfe", number],
                    "bj_id": f"ausl_{base}_{lang}",
                }

    # ── Muster-Ersuchen ──

    def _muster_stubs(self) -> Iterator[dict]:
        for lang, url in MUSTER_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] Muster page %s fetch failed: %s", self.SOURCE_KEY, lang, e)
                continue
            items = _dam_items(r.text, url)
            for it in items:
                if "muster-ersuchen-strafrecht.pdf" not in it["pdf_url"]:
                    continue       # the civil-law HBewUe70/HUE54 model requests on the same page
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": "BJ Muster-Ersuchen Strafrecht",
                    # undated form; its file date (2020-07-13) is rhf.admin.ch's
                    # migration stamp, shared with unrelated older files
                    "date": "",
                    "language": lang, "doc_type": "formular",
                    "topics": ["Muster-Ersuchen", "Internationale Rechtshilfe"],
                    "bj_id": f"muster_ersuchen_strafrecht_{lang}",
                }

    # ── rhf.admin.ch: Wegleitungen, Rundschreiben, Checklisten, Merkblätter ──

    def _wegleitung_stubs(self) -> Iterator[dict]:
        taken: set[str] = set()
        for page_lang, url in WEGLEITUNGEN_PAGES:
            try:
                r = self.get(url)
                r.raise_for_status()
            except Exception as e:
                logger.warning("[%s] rhf %s page fetch failed: %s", self.SOURCE_KEY, page_lang, e)
                continue
            items = _dam_items(r.text, url)
            logger.info("[%s] rhf %s page: %d PDFs", self.SOURCE_KEY, page_lang, len(items))
            for it in items:
                c = classify_rhf_item(it["pdf_url"], page_lang)
                if c is None:
                    base = split_lang_suffix(_filename_stem(it["pdf_url"]))[0]
                    if base not in RHF_SKIP:
                        logger.warning("[%s] rhf: unknown file %s (%s), skipped",
                                       self.SOURCE_KEY, it["pdf_url"], it["title"])
                    continue
                bj_id = f"{c['key']}_{c['language']}"
                if bj_id in taken:
                    continue          # a suffixed file again, from the next language's page
                taken.add(bj_id)
                date = it["file_date"]
                if date == _RHF_MIGRATION_DATE:
                    date = ""
                if c["base"].startswith("infomail-"):
                    date = c["base"][len("infomail-"):]
                yield {
                    "pdf_url": it["pdf_url"], "url": url, "title": it["title"],
                    "doc_number": c["doc_number"],
                    "date": date, "date_from_text": not date,
                    "language": c["language"], "doc_type": c["doc_type"],
                    "topics": ["Internationale Rechtshilfe", it["section"]] if it["section"]
                              else ["Internationale Rechtshilfe"],
                    "bj_id": bj_id,
                }

    def discover_documents(self) -> Iterator[dict]:
        for gen in (self._report_stubs, self._auslieferung_stubs,
                    self._muster_stubs, self._wegleitung_stubs):
            for stub in gen():
                out = self.with_body(stub, stub["doc_number"])
                if out:
                    yield out
