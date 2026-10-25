"""
UN human-rights texts on Switzerland in German and Italian (federal translations)
=================================================================================

The UN publishes its concluding observations on Switzerland in English,
French, Spanish, Russian, Arabic and Chinese. The federal offices that report
to each committee publish German (once Italian) translations for the
cantonal and federal administrations. They are not authoritative — German is
not a UN language; the CAT translation says so in its first line
("inoffizielle Übersetzung – massgebend sind ausschliesslich die französische
und englische Fassung") — but they are the only German text of these
documents, and a German query against the English/French un_uhri paragraphs
finds nothing.

Survey 2026-10-25 (12 documents online): BJ (CAT 2010, CCPR 2009 + 2017, SPT
visit 2019), SECO (CESCR 2019), EDA (CERD 2021), EBG (CEDAW 2003 + 2022), BSV
(CRC 2015 + 2021, CRC sale-of-children protocol 2015), EBGB (CRPD 2022,
Italian only). No German version exists
for CAT 2015/2023, CEDAW 2016, CCPR 1996/2001, CED 2021, CRPD 2022 (the EDI
link died in the EBGB migration) or the UPR.

Curated, not discovered: each entry names its page, a pattern for the file
link on that page (so a re-uploaded file under a new DAM hash is still
found), and the UN symbol, read off the document itself. The pages are
re-read on every run and any further translation-looking download on them
is logged, so a new translation is noticed and added here. Two files sit
outside an sd-web list (EBG's file service, the EBGB Nuxt site) and are
listed by direct URL.

doc_number = the UN symbol of the translated document, the same as its
English/French paragraphs in un_uhri.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from .base import PracticeScraper
from .sdweb import download_items

logger = logging.getLogger(__name__)

BJ_CAT = "https://www.bj.admin.ch/de/antifolter-konvention-der-uno"
BJ_PACTS = "https://www.bj.admin.ch/de/menschenrechtspakte-der-uno"
BJ_SPT = "https://www.bj.admin.ch/de/folterpraevention-straf-und-massnahmenvollzug"
SECO_UN = "https://www.seco.admin.ch/de/uno"
EDA_CERD = "https://www.eda.admin.ch/de/uebereinkommen-zur-beseitigung-jeder-form-von-rassendiskriminierung"
EBG_INT = "https://www.ebg.admin.ch/de/zusammenarbeit-in-der-schweiz-und-international"
BSV_CRC = "https://www.bsv.admin.ch/de/kinderrechte"

# date = the UN document's publication date, the same as its un_uhri paragraphs.
ENTRIES: tuple[dict, ...] = (
    dict(symbol="CAT/C/CHE/CO/6", authority="UN-CAT", date="2010-05-25", language="de", office="BJ",
         page=BJ_CAT, match=r"cat-ber-6-schlussfolgerungen", url=None,
         title="Ausschuss gegen Folter: Schlussbemerkungen zum sechsten periodischen Bericht der Schweiz (2010)"),
    dict(symbol="CCPR/C/CHE/CO/3", authority="UN-CCPR", date="2009-11-03", language="de", office="BJ",
         page=BJ_PACTS, match=r"schlussbem-staatenbericht-3\.pdf", url=None,
         title="Menschenrechtsausschuss: Abschliessende Bemerkungen zum dritten Bericht der Schweiz (2009)"),
    dict(symbol="CCPR/C/CHE/CO/4", authority="UN-CCPR", date="2017-08-22", language="de", office="BJ",
         page=BJ_PACTS, match=r"schlussbem-staatenbericht-4\.pdf", url=None,
         title="Menschenrechtsausschuss: Abschliessende Bemerkungen zum vierten periodischen Bericht der Schweiz (2017)"),
    dict(symbol="E/C.12/CHE/CO/4", authority="UN-CESCR", date="2019-11-18", language="de", office="SECO",
         page=SECO_UN, match=r"Obs-DESC-finale-DE", url=None,
         title="Ausschuss für wirtschaftliche, soziale und kulturelle Rechte: Abschliessende Bemerkungen "
               "zum vierten periodischen Bericht der Schweiz (2019)"),
    dict(symbol="CERD/C/CHE/CO/10-12", authority="UN-CERD", date="2021-12-27", language="de", office="EDA",
         page=EDA_CERD, match=r"2021-CERD-Schlussbemerkungen-CH_DE", url=None,
         title="Ausschuss für die Beseitigung der Rassendiskriminierung: Schlussbemerkungen zum zehnten bis "
               "zwölften periodischen Bericht der Schweiz (2021)"),
    dict(symbol="A/58/38 (Part I), paras. 87-141", authority="UN-CEDAW", date="2003-01-31", language="de",
         office="EBG", page=None, match=None,
         url="https://www.ebg.admin.ch/dam/en/sd-web/wS9AhkxohoNe/empfehlungen_cedaw-bericht12%20(3).pdf",
         title="Ausschuss für die Beseitigung der Diskriminierung der Frau: Abschliessende Bemerkungen zum "
               "ersten und zweiten Bericht der Schweiz (2003)"),
    dict(symbol="CEDAW/C/CHE/CO/6", authority="UN-CEDAW", date="2022-11-01", language="de", office="EBG",
         page=None, match=None,
         url="https://backend.ebg.admin.ch/fileservice/sdweb-docs-prod-ebgch-files/files/2023/08/28/"
             "4a13b548-c303-492b-8ce5-454e3244584c.pdf",
         title="Ausschuss für die Beseitigung der Diskriminierung der Frau: Abschliessende Bemerkungen zum "
               "sechsten periodischen Bericht der Schweiz (2022)"),
    dict(symbol="CRC/C/CHE/CO/2-4", authority="UN-CRC", date="2015-02-25", language="de", office="BSV",
         page=BSV_CRC, match=r"ConcludingObservationsSwitzerlandDe", url=None,
         title="Ausschuss für die Rechte des Kindes: Schlussbemerkungen zum zweiten bis vierten Staatenbericht "
               "der Schweiz (2015)"),
    # BSV labels this file "…ENG"; it is the German translation.
    dict(symbol="CRC/C/CHE/CO/5-6", authority="UN-CRC", date="2021-10-22", language="de", office="BSV",
         page=BSV_CRC, match=r"/j8ZTFkc2sl5Q/", url=None,
         title="Ausschuss für die Rechte des Kindes: Schlussbemerkungen zum fünften und sechsten "
               "Staatenbericht der Schweiz (2021)"),
    dict(symbol="CRC/C/OPSC/CHE/CO/1", authority="UN-CRC", date="2015-02-25", language="de", office="BSV",
         page=BSV_CRC, match=r"schlussbemerkung_kr-ausschuss", url=None,
         title="Ausschuss für die Rechte des Kindes: Schlussbemerkungen zum Bericht der Schweiz zum "
               "Fakultativprotokoll betreffend den Verkauf von Kindern (2015)"),
    dict(symbol="CAT/OP/CHE/ROSP/1", authority="UN-SPT", date="2021-03-22", language="de", office="BJ",
         page=BJ_SPT, match=r"ber-spt-besuch-1-2019", url=None,
         title="Unterausschuss zur Verhütung von Folter: Bericht über den Besuch in der Schweiz 2019, "
               "mit Stellungnahme des Bundesrates"),
    dict(symbol="CRPD/C/CHE/CO/1", authority="UN-CRPD", date="2022-04-13", language="it", office="UFPD",
         page=None, match=None,
         url="https://www.ebgb.admin.ch/dam/it/sd-web/i4f1KwhX0mv8/"
             "CRPD%20Osservazioni%20conclusive%20sul%20rapporto%20iniziale%20della%20Svizzera.pdf",
         title="Comitato sui diritti delle persone con disabilità: Osservazioni conclusive sul rapporto "
               "iniziale della Svizzera (2022)"),
)

# Downloads on these pages that look like translations but are not: SECO's
# 2021 implementation table (a government document), and the "German" file
# for the first CESCR review, which is the French original E/C.12/1/Add.30.
_NOT_TRANSLATIONS = re.compile(r"Synth%C3%A8seRecommandations|Synth.seRecommandations|"
                               r"Obs-DESC-1erRapportPacteI")

# What a further translation on one of these pages would be called.
_CANDIDATE = re.compile(r"(?i)schlussbemerkung|abschliessende bemerkung|schlussfolgerung|"
                        r"empfehlungen des (?:uno-)?ausschuss|osservazioni conclusive")
_NOTE = {"de": "Übersetzung {office}, nicht massgebend (verbindlich sind die UNO-Sprachfassungen)",
         "it": "Traduzione {office}, non vincolante (fanno fede le versioni ONU)"}


class UnChTranslationsScraper(PracticeScraper):
    SOURCE_KEY = "un_ch_translations"
    ISSUING_AUTHORITY = "UN"          # per row: the UN body that wrote the text
    DEFAULT_DOC_TYPE = "concluding_observations"
    REVISION_FIELD = "pdf_url"        # a replaced file gets a new DAM hash
    REQUEST_DELAY = 2.0

    def _make_doc_id(self, stub: dict) -> str:
        key = re.sub(r"[^a-z0-9]+", "_", stub["doc_number"].lower()).strip("_")
        return f"{self.SOURCE_KEY}_{key}_{stub['language']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["issuing_authority"]
        return doc

    def _page_items(self, page: str, cache: dict) -> list[dict]:
        if page not in cache:
            r = self.get(page)
            r.raise_for_status()
            cache[page] = list(download_items(r.text, page))
        return cache[page]

    def discover_documents(self) -> Iterator[dict]:
        pages: dict[str, list[dict]] = {}
        claimed: set[str] = set()
        missing = []
        for e in ENTRIES:
            pdf_url = e["url"]
            if e["page"]:
                hit = next((it for it in self._page_items(e["page"], pages)
                            if re.search(e["match"], it["pdf_url"])), None)
                if hit is None:
                    missing.append(e["symbol"])
                    logger.warning(f"[un_ch_translations] {e['symbol']}: no link matching "
                                   f"{e['match']!r} on {e['page']} — moved or removed?")
                    continue
                pdf_url = hit["pdf_url"]
            claimed.add(pdf_url)
            note = _NOTE[e["language"]].format(office=e["office"])
            yield {
                "doc_number": e["symbol"],
                "title": e["title"],
                "date": e["date"],
                "language": e["language"],
                "issuing_authority": e["authority"],
                "doc_type": "visit_report" if "/ROSP/" in e["symbol"] else "concluding_observations",
                "url": e["page"] or pdf_url,
                "pdf_url": pdf_url,
                "topics": [e["authority"].replace("UN-", ""), note, f"Übersetzung: {e['office']}"],
            }
        for page, items in pages.items():
            for it in items:
                if (it["pdf_url"] not in claimed and not _NOT_TRANSLATIONS.search(it["pdf_url"])
                        and _CANDIDATE.search(it["title"] + " " + it["pdf_url"])):
                    logger.warning(f"[un_ch_translations] unlisted candidate on {page}: "
                                   f"{it['title']!r} {it['pdf_url']} — add it to ENTRIES if it is a UN text")
        if len(missing) == len([e for e in ENTRIES if e["page"]]):
            raise RuntimeError("[un_ch_translations] none of the listed files found — pages restructured?")
