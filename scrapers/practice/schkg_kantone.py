"""
Cantonal SchKG supervisory authorities + KBKS
=============================================

Every canton has a supervisory authority for debt enforcement and bankruptcy
(Art. 13 SchKG). What they publish — Kreisschreiben / circulaires / circolari,
Weisungen, and above all the guideline for the betreibungsrechtliches
Existenzminimum (Art. 93 SchKG) — lives on 26 different websites. The BJ
mirrors a frozen 2000-2010 selection (bj_schkg); this source reads the
cantons' own pages, plus the national texts of the Konferenz der Betreibungs-
und Konkursbeamten der Schweiz (KBKS).

One REGISTRY row per publication page. A row is either a listing page whose
links are harvested by `include` (regex on the absolute href) and optional
`text_include` (regex on the anchor text), or a single document (`mode`
"pdf" / "html").

Identity: issuing_authority is the canton code ("ZH") or "KBKS". The
Existenzminimum guideline of a canton always gets doc_number "<KT>
Existenzminimum", so search_practice collapses a canton's editions to the
newest and include_superseded returns the edition in force at an earlier
date; its doc_id carries the edition year, so a yearly re-issue (GE, NE) is a
new row rather than an overwrite.

ne.ch, jura.ch, ti.ch and gl.ch refuse connections from the production
server's IP range; their requests go through NE_PROXY / JU_PROXY / TI_PROXY /
GL_PROXY when set (the SOCKS tunnel the court scrapers use) and fail softly
otherwise.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlsplit, unquote

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, sha256_hex, slugify
from .bj_schkg import _TYPE_RULES

logger = logging.getLogger(__name__)

# Same per-canton variables as the NE/JU/TI court scrapers' units; gl.ch
# also times out from the server (found on the first VPS run, 2026-11-05).
TUNNEL_ENV = {"ne.ch": "NE_PROXY", "jura.ch": "JU_PROXY", "ti.ch": "TI_PROXY", "gl.ch": "GL_PROXY"}

# Texts a script cannot fetch (baselland.ch answers every non-browser request
# with a Cloudflare challenge), copied by hand from the live page.
STATIC_DIR = Path(__file__).resolve().parent / "static"

_EXISTENZMINIMUM = re.compile(
    r"Existenzminimum|Notbedarf|minimum vital|minimum d.existence|insaisissabilit"
    r"|minim[oi] (?:vitale|di esistenza|d.esistenza)",
    re.IGNORECASE)
_NUMBER_RULES = (
    (re.compile(r"Kreisschreiben\s+(?:Nr\.?\s*)?([A-Z]?\s?\d+[a-z]?)\b", re.I), "KS"),
    (re.compile(r"\bKS\s+(?:Nr\.?\s*)?([A-Z]?\s?\d+[a-z]?)\b"), "KS"),
    (re.compile(r"Circolare\s+(?:CEF\s+)?(?:n(?:r|o)?\.?\s*)?(\d+)", re.I), "Circolare"),
    # BE writes "Circulaire no A1" and, twice, "Circulaire non C4".
    (re.compile(r"Circulaire\s+(?:no?n?\.?\s*|N[°º]\s*)?([A-C]?\d+)\b", re.I), "Circulaire"),
    (re.compile(r"Weisung\s+(?:Nr\.?\s*)?(\d+)", re.I), "Weisung"),
    (re.compile(r"Directive\s+(?:N[°ºo]?\.?\s*)?(\d+)", re.I), "Directive"),
)
_TAIL = re.compile(r"\s*\(?(?:PDF|DOCX?)\b[^)]*\)?\s*(?:,?\s*[\d.,']+\s*[kKM]B)?\s*$")
# "(pdf, 183 KB) 23.01.2012 …" (SO), "(Startet einen Download)" (BS)
_NOISE = re.compile(r"\s*\((?:pdf|Startet einen Download)\b[^)]*\).*$", re.I)
_LEAD_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}\s+")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


@dataclass(frozen=True)
class Entry:
    canton: str
    url: str
    lang: str
    issuer: str
    mode: str = "listing"                  # listing | pdf | html | static
    static_file: str | None = None         # mode static: text file under ./static
    include: str = r"\.pdf(?:$|\?)"        # regex on the absolute href
    text_include: str | None = None        # regex on the anchor text
    exclude: str | None = None             # regex on href or anchor text
    title: str | None = None               # mode pdf/html: the document's title
    date: str | None = None                # mode pdf/html: issuance date
    html_selector: str | None = None       # mode html: CSS selector of the text
    section: str | None = None             # listing: only links under the heading matching this
    # regex on href naming the canton's guideline; when set, only that link is
    # the guideline (a debtor leaflet titled "minimum vital" is not)
    existenzminimum: str | None = None
    titles: tuple[tuple[str, str], ...] = ()   # (href regex, title) for links with poor anchor text
    topics: tuple[str, ...] = field(default_factory=tuple)


_PDF = r"\.pdf(?:$|\?)"

_KBKS = "Konferenz der Betreibungs- und Konkursbeamten der Schweiz"
_OW_PUBS = (
    ("1178", "Richtlinien für die Berechnung des betreibungsrechtlichen Existenzminimums "
             "(Notbedarf) nach Art. 93 SchKG, Obergerichtskommission SK 09/013, 24. Juli 2009"),
    ("3303", "Hinweise zur Forderungseingabe im Konkursverfahren"),
    ("29368", "Ablauf Konkursverfahren"),
    ("29371", "Erläuterung zu Lohnforderung bei Konkurseröffnung"),
    ("29383", "Merkblatt für Gläubiger im Konkursverfahren"),
    ("29386", "Merkblatt für Schuldner im Konkursverfahren"),
)

# Surveyed 2026-11-05, rechecked 2026-11-06. AR and GR publish on their court
# sites (the BJ page only links there); BL as page text (static copy below). No public SchKG publication of their
# own: UR (BJ: "Keine Kreisschreiben vorhanden"; its courts apply the KBKS
# text directly), SH, AI, VS (laws only; its courts apply the KBKS text), TG
# (links the KBKS text, held under KBKS). LU's Weisungen are LGVE rulings,
# already in the decision corpus (lu_gerichte). BL's page sits behind a
# Cloudflare challenge. BS/SH/AI's last guideline texts are the 2009/2010 BJ
# copies in bj_schkg. Blank forms (BS, NE, NW, KBKS) are left out; the BJ
# model forms are in bj_schkg.
REGISTRY: tuple[Entry, ...] = (
    Entry("KBKS", "https://www.betreibung-konkurs.ch/informationen/betreibung", "de", _KBKS,
          include=r"/fileadmin/user_upload/.*\.pdf", existenzminimum=r"Richtlinien_Existenz",
          titles=((r"Richtlinien_Existenz", "Richtlinien für die Berechnung des betreibungsrechtlichen "
                                            "Existenzminimums (Notbedarf) nach Art. 93 SchKG vom 1. Juli 2009"),)),
    Entry("KBKS", "https://www.poursuite-faillite-offic.ch/informations/poursuite", "fr", _KBKS,
          include=r"/fileadmin/user_upload/.*\.pdf", existenzminimum=r"Richtlinien_Existenz",
          titles=((r"Richtlinien_Existenz", "Lignes directrices pour le calcul du minimum d'existence "
                                            "en matière de poursuite (art. 93 LP) du 1er juillet 2009"),)),
    Entry("KBKS", "https://www.esecuzione-fallimento.ch/informazioni/esecuzione", "it", _KBKS,
          include=r"/fileadmin/user_upload/.*\.pdf", existenzminimum=r"Richtlinien_Existenz",
          titles=((r"Richtlinien_Existenz", "Direttive per il calcolo del minimo di esistenza "
                                            "(art. 93 LEF) del 1° luglio 2009"),)),
    Entry("KBKS", "https://www.betreibung-konkurs.ch/ueber-uns/news", "de", _KBKS,
          include=r"/fileadmin/user_upload/01_UeberUns/.*\.pdf", existenzminimum=r"(?!)",
          titles=((r"Teuerung", "Medienmitteilung vom 8. Dezember 2022: Das betreibungsrechtliche "
                                "Existenzminimum im Kontext der aktuellen Teuerung"),
                  (r"Heiz", "Medienmitteilung vom 25. Januar 2023: Umgang mit Nachzahlungen von Heiz- "
                            "und Nebenkosten während einer Einkommenspfändung"))),
    Entry("KBKS", "https://www.poursuite-faillite-offic.ch/a-propos-de-nous/nouveautes", "fr", _KBKS,
          include=r"/fileadmin/user_upload/01_UeberUns/.*\.pdf", existenzminimum=r"(?!)",
          titles=((r"Teuerung", "Communiqué du 8 décembre 2022 : le minimum vital LP dans le "
                                "contexte du renchérissement actuel"),)),
    Entry("KBKS", "https://www.esecuzione-fallimento.ch/chi-siamo/novita", "it", _KBKS,
          include=r"/fileadmin/user_upload/01_UeberUns/.*\.pdf", existenzminimum=r"(?!)",
          titles=((r"Teuerung", "Comunicato dell'8 dicembre 2022: il minimo di esistenza LEF nel "
                                "contesto dell'attuale rincaro"),)),
    Entry("KBKS", "https://www.betreibung-konkurs.ch/informationen/statistik", "de", _KBKS,
          include=r"/fileadmin/user_upload/.*Statistik.*\.pdf"),
    Entry("BE", "https://www.zsg.justice.be.ch/de/start/dienstleistungen/kreisschreiben-musterformulare.html",
          "de", "Obergericht, Aufsichtsbehörde in Betreibungs- und Konkurssachen",
          include=r"/kreisschreiben/schkg/.*\.pdf"),
    Entry("BE", "https://www.zsg.justice.be.ch/fr/start/dienstleistungen/kreisschreiben-musterformulare.html",
          "fr", "Cour suprême, Autorité de surveillance en matière de poursuite et de faillite",
          include=r"/kreisschreiben/schkg/.*\.pdf"),
    Entry("ZH", "https://www.gerichte-zh.ch/de/kreisschreiben", "de",
          "Obergericht, Verwaltungskommission", include=r"/sites/default/files/.*\.pdf",
          text_include=r"Verzeichnis",
          titles=((r"Chronolog", "Kreisschreiben des Obergerichts: Chronologisches Verzeichnis"),)),
    Entry("ZH", "https://www.gerichte-zh.ch/de/themen/betreibung-und-konkurs/hilfen", "de",
          "Obergericht, Verwaltungskommission", include=r"/sites/default/files/.*\.pdf",
          titles=((r"16_09_2009", "Kreisschreiben der Verwaltungskommission des Obergerichts vom "
                                  "16. September 2009: Richtlinien für die Berechnung des "
                                  "betreibungsrechtlichen Existenzminimums"),)),
    Entry("ZH", "https://www.gerichte-zh.ch/de/themen/betreibung-und-konkurs/formulare-und-merkblaetter",
          "de", "Obergericht", include=r"/sites/default/files/.*\.pdf",
          titles=((r"M_Beschwerde", "Merkblatt: Betreibungsrechtliche Beschwerde"),
                  (r"M_RV_nV", "Merkblatt: Rechtsvorschlag mangels neuen Vermögens"))),
    Entry("SO", "https://so.ch/gerichte/obergericht/weisungen-kreisschreiben/schuldbetreibung-konkurs/",
          "de", "Obergericht, Aufsichtsbehörde für Schuldbetreibung und Konkurs",
          include=r"/fileadmin/internet/gerichte/obergericht/pdf/.*\.pdf"),
    Entry("SZ", "https://www.sz.ch/behoerden/justiz/kantonsgericht/gesetze-und-richtlinien.html/"
                "8756-8758-8801-11732-12234",
          "de", "Kantonsgericht", include=r"/public/upload/assets/.*\.pdf",
          text_include=r"Existenzminimum|Notbedarf", exclude=r"Konferenz"),
    Entry("ZG", "https://zg.ch/de/gerichte/zivil-und-strafrechtspflege/obergericht/publikationen-des-obergerichts",
          "de", "Obergericht, Justizkommission", include=r"cdn\.zg\.ch/dam/", text_include=r"Existenzminimum"),
    Entry("GL", "https://www.gl.ch/verwaltung/sicherheit-und-justiz/justiz/betreibungs-konkursamt/"
                "richtlinien-zur-berechnung-des-existenzminimums.html/1250",
          "de", "Departement Sicherheit und Justiz", include=r"/public/upload/assets/.*\.pdf"),
    Entry("AR", "https://www.ar.ch/gerichte/obergericht/weisungen/", "de",
          "Obergericht, Aufsichtsbehörde für Schuldbetreibung und Konkurs",
          include=r"/fileadmin/user_upload/Gerichte/Obergericht/.*\.pdf",
          section=r"Aufsichtsbehörde für Schuldbetreibung",
          titles=((r"Betreibungsausk", "Weisung des Obergerichts vom 14. Juli 1993 betreffend "
                                       "Betreibungsauskünfte im Verwaltungsstrafverfahren"),
                  (r"Rechtsvorschlag", "Weisung des Obergerichts vom 18. November 1998 betreffend "
                                       "Vorgehen bei Rechtsvorschlag mangels neuen Vermögens"),
                  (r"Existenzminimum", "Weisung des Obergerichts vom 26. August 2009 betreffend "
                                       "Berechnung des Existenzminimums (Richtlinien der KBKS vom "
                                       "1. Juli 2009, verbindlich ab 1. September 2009)"),
                  (r"Zustellung", "Weisung des Obergerichts vom 25. November 2014 betreffend "
                                  "Zustellung von Betreibungsurkunden"))),
    Entry("GR", "https://www.justiz-gr.ch/gerichte/obergericht/dokumentation/kreisschreiben/", "de",
          "Kantonsgericht, Schuldbetreibungs- und Konkurskammer als Aufsichtsbehörde",
          include=r"/Kreisschreiben/Kreisschreiben_.*\.pdf",
          titles=((r"2009-08-18", "Beschluss KSK 09 39 vom 18. August 2009: Änderung der Richtlinien "
                                  "für die Berechnung des betreibungsrechtlichen Existenzminimums "
                                  "(Notbedarf) nach Art. 93 SchKG"),
                  (r"2011-02-08", "Kreisschreiben KSK 11 5 vom 8. Februar 2011 betreffend "
                                  "Pfändungsvollzug/Distanzpfändung"))),
    Entry("GR", "https://www.justiz-gr.ch/fileadmin/dateien/Kantonsgericht/Kreisschreiben/"
                "Circolare_2009-08-18.pdf", "it",
          "Tribunale cantonale, Camera delle esecuzioni e dei fallimenti quale autorità di vigilanza",
          mode="pdf", date="2009-08-18", existenzminimum=r"2009-08-18",
          title="Decisione KSK 09 39 del 18 agosto 2009: modifica delle direttive per il calcolo "
                "del minimo d'esistenza (art. 93 LEF)"),
    Entry("GR", "https://www.justiz-gr.ch/fileadmin/dateien/Kantonsgericht/Kreisschreiben/"
                "Circolare_2011-02-08.pdf", "it",
          "Tribunale cantonale, Camera delle esecuzioni e dei fallimenti quale autorità di vigilanza",
          mode="pdf", date="2011-02-08", existenzminimum=r"(?!)",
          title="Circolare KSK 11 5 dell'8 febbraio 2011 concernente esecuzione del "
                "pignoramento/pignoramento a distanza"),
    # Published as text in the "Revision Betreibungsrechtliches Existenzminimum"
    # section of the office's page: the KBKS 2009 guideline, adopted by the
    # Regierungsrat on 18 August 2009 (RRB Nr. 1222). Copied 2026-11-06.
    Entry("BL", "https://www.baselland.ch/politik-und-behorden/direktionen/sicherheitsdirektion/"
                "zivilrechtsverwaltung/betreibungsamt/",
          "de", "Betreibungsamt (Zivilrechtsverwaltung, Sicherheitsdirektion)", mode="static",
          static_file="bl_existenzminimum.txt", date="2009-08-18", existenzminimum=r"betreibungsamt",
          title="Richtlinien für die Berechnung des betreibungsrechtlichen Existenzminimums "
                "(Richtlinien der KBKS vom 1. Juli 2009, Regierungsratsbeschluss Nr. 1222 vom "
                "18. August 2009)"),
    Entry("NW", "https://www.nw.ch/_rte/publikation/17288", "de", "Obergericht",
          include=r"/_doc/\d+", text_include=r"Merkblatt|Richtlinien"),
    Entry("NW", "https://www.nw.ch/_doc/22444", "de", "Obergericht", mode="pdf",
          title="Weisung des Obergerichts vom 26. August 2009 zum Notbedarf im Familienrecht und "
                "bei der unentgeltlichen Rechtspflege", existenzminimum=r"(?!)"),
    Entry("BS", "https://www.bs.ch/gerichte-judikative/betreibungs-und-konkursamt/konkurse",
          "de", "Betreibungs- und Konkursamt", include=r"media\.bs\.ch/original_file/",
          text_include=r"Merkblatt"),
    *(Entry("OW", f"https://www.ow.ch/_rte/publikation/{pid}", "de",
            "Obergerichtskommission" if pid == "1178" else "Konkursamt",
            include=r"/_doc/\d+", titles=((r"/_doc/", title),))
      for pid, title in _OW_PUBS),
    Entry("AG", "https://www.ag.ch/de/themen/recht-justiz/konkurse-und-betreibungen/"
                "kreisschreiben-der-schuldbetreibungs-und-konkurskommission",
          "de", "Obergericht, Schuldbetreibungs- und Konkurskommission",
          include=r"/kreisschreiben/.*\.pdf"),
    Entry("FR", "https://www.fr.ch/de/staat-und-recht/justiz/betreibungsverfahren/existenzminimum",
          "de", "Kanton Freiburg", mode="html", html_selector="main"),
    Entry("FR", "https://www.fr.ch/etat-et-droit/justice/procedure-de-poursuite/minimum-vital",
          "fr", "Canton de Fribourg", mode="html", html_selector="main"),
    Entry("GE", "https://silgeneve.ch/legis/data/rsg_e3_60p04.htm",
          "fr", "Chambre de surveillance des Offices des poursuites et faillites", mode="html"),
    Entry("JU", "https://jura.ch/JUST/Instances-judiciaires/Tribunal-cantonal/Circulaires.html",
          "fr", "Tribunal cantonal, Cour des poursuites et faillites",
          include=_PDF, section=r"Cour des poursuites et faillites"),
    Entry("NE", "https://www.ne.ch/autorites/dsdc/sepf/bases-legales-documents",
          "fr", "Service des poursuites et faillites (autorité inférieure de surveillance)",
          include=_PDF, text_include=r"insaisissabilit|Fiche",
          titles=((r"Fiche_info", "Fiche d'information : le commandement de payer"),)),
    Entry("SG", "https://www.sg.ch/recht/gerichte/informationen---formulare/weisungen.html",
          "de", "Kantonsgericht (Aufsichtsbehörde Schuldbetreibung und Konkurs)",
          include=r"kant-aufsichtsbeh(?:%C3%B6|ö)rde-schuldbetreibung-und-konkurs/.*\.pdf"),
    Entry("TI", "https://www4.ti.ch/poteri/giudiziario/giustizia-civile/circolari",
          "it", "Camera di esecuzione e fallimenti del Tribunale d'appello",
          include=r"/circolari_CEF/.*\.pdf",
          titles=((r"Tabella_riassuntiva", "Tabella riassuntiva allegata alla Circolare n. 36/2009 "
                                           "sulle tasse e le spese in materia di fallimento"),)),
    Entry("TI", "https://www4.ti.ch/poteri/giudiziario/giustizia-civile/tabella-dei-minimi-desistenza",
          "it", "Camera di esecuzione e fallimenti del Tribunale d'appello", mode="html",
          title="Tabella per il calcolo del minimo di esistenza agli effetti del diritto "
                "esecutivo (art. 93 LEF)", date="2009-09-01", existenzminimum=r"minimi-desistenza"),
    Entry("VD", "https://www.vd.ch/economie/poursuites-et-faillites/procedure-de-poursuite-ordinaire/"
                "vous-etes-poursuivi-debiteur",
          "fr", "Office des poursuites", include=_PDF,
          existenzminimum=r"Lignes_directrices_minimum_vital",
          titles=((r"Lignes_directrices_minimum_vital",
                   "Lignes directrices pour le calcul du minimum d'existence en matière de "
                   "poursuite (art. 93 LP), 2009"),
                  (r"pliant_OJV", "Dépliant : le minimum vital (Ordre judiciaire vaudois)"))),
    Entry("VD", "https://www.vd.ch/fileadmin/user_upload/themes/etat_droit/justice/fichiers_pdf/"
                "Calcul_du_minimum_vital.pdf",
          "fr", "Ordre judiciaire vaudois", mode="pdf",
          title="Calcul du minimum vital (notice)", existenzminimum=r"(?!)"),
)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def classify(title: str, canton: str, allow_existenzminimum: bool = True) -> tuple[str, str]:
    """Return (doc_type, doc_number) for a document title."""
    if allow_existenzminimum and _EXISTENZMINIMUM.search(title):
        return "richtlinie", f"{canton} Existenzminimum"
    doc_type = "kreisschreiben"
    for rx, kind in _TYPE_RULES:
        if rx.search(title):
            doc_type = kind
            break
    for rx, label in _NUMBER_RULES:
        m = rx.search(title)
        if m:
            return doc_type, f"{canton} {label} {m.group(1).replace(' ', '')}"
    return doc_type, f"{canton} {title[:70]}".strip()


def _title_from(a, href: str) -> str:
    text = _NOISE.sub("", _clean(a.get_text(" ", strip=True)))
    text = _LEAD_DATE.sub("", _TAIL.sub("", text)).strip().rstrip(" [")
    if len(text) < 4:
        text = _clean(a.get("title") or "")
    if len(text) < 4:
        stem = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
        text = re.sub(r"\.(pdf|docx?)$", "", stem, flags=re.I).replace("_", " ").replace("-", " ")
    return text


_HEADINGS = ("h1", "h2", "h3", "h4", "h5")


def _anchors(soup: BeautifulSoup, section: str | None) -> list:
    if not section:
        return soup.find_all("a", href=True)
    rx = re.compile(section, re.I)
    head = next((h for h in soup.find_all(_HEADINGS) if rx.search(h.get_text(" ", strip=True))), None)
    if head is None:
        return []
    level = _HEADINGS.index(head.name)
    out = []
    for el in head.find_all_next():
        if el.name in _HEADINGS and _HEADINGS.index(el.name) <= level:
            break
        if el.name == "a" and el.get("href"):
            out.append(el)
    return out


def parse_listing(html: str | bytes, entry: Entry) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    inc = re.compile(entry.include, re.I)
    tinc = re.compile(entry.text_include, re.I) if entry.text_include else None
    exc = re.compile(entry.exclude, re.I) if entry.exclude else None
    exm = re.compile(entry.existenzminimum, re.I) if entry.existenzminimum else None
    titles = [(re.compile(rx, re.I), t) for rx, t in entry.titles]
    # justiz-gr.ch writes relative hrefs ("fileadmin/…") against <base href>.
    base = soup.find("base", href=True)
    root = urljoin(entry.url, base["href"].strip()) if base else entry.url
    stubs, seen = [], set()
    for a in _anchors(soup, entry.section):
        href = urljoin(root, a["href"].strip())
        if href in seen or not inc.search(href):
            continue
        title = next((t for rx, t in titles if rx.search(href)), None) or _title_from(a, href)
        if tinc and not tinc.search(title):
            continue
        if exc and (exc.search(href) or exc.search(title)):
            continue
        seen.add(href)
        date = first_date_iso(title) or first_date_iso(_clean(a.get_text(" ", strip=True))) or None
        stubs.append(make_stub(entry, title, href, date,
                               existenzminimum=bool(exm.search(href)) if exm else None))
    # Two files under one title (AG's EDV-Richtlinien, Anhang, Beilage) must not
    # share a doc_id: the second would be skipped as already seen.
    counts: dict[str, int] = {}
    for s in stubs:
        counts[s["doc_number"]] = counts.get(s["doc_number"], 0) + 1
    for s in stubs:
        if counts[s["doc_number"]] > 1 and not s["doc_number"].endswith("Existenzminimum"):
            stem = unquote(urlsplit(s["pdf_url"]).path.rsplit("/", 1)[-1])
            stem = re.sub(r"\.pdf$", "", stem, flags=re.I)[:40]
            s["doc_number"] = f"{s['doc_number'][:50]} ({stem})"
    return stubs


def make_stub(entry: Entry, title: str, pdf_url: str, date: str | None = None,
              body_text: str | None = None, existenzminimum: bool | None = None) -> dict:
    """existenzminimum: True = the canton's guideline, False = never, None =
    decide from the title."""
    if existenzminimum:
        doc_type, doc_number = "richtlinie", f"{entry.canton} Existenzminimum"
    else:
        doc_type, doc_number = classify(title, entry.canton,
                                        allow_existenzminimum=existenzminimum is None)
    date = date or first_date_iso(title)
    if not date:
        m = _YEAR.search(title)
        date = m.group(1) if m else ""
    topics = ["SchKG", entry.canton, entry.issuer, *entry.topics]
    if doc_type == "richtlinie" and doc_number.endswith("Existenzminimum"):
        topics.append("Existenzminimum")
    stub = {
        "canton": entry.canton,
        "issuer": entry.issuer,
        "pdf_url": pdf_url,
        "url": entry.url,
        "title": title,
        "doc_number": doc_number,
        "doc_type": doc_type,
        "date": date,
        "language": entry.lang,
        "topics": topics,
    }
    if body_text is not None:
        stub["body_text"] = body_text
    # HTML pages are edited in place at a stable URL: their content is the edition.
    stub["revision"] = sha256_hex(body_text) if body_text is not None else pdf_url
    return stub


def page_title(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    h = soup.find("h1") or soup.find("title")
    return _clean(h.get_text(" ", strip=True)) if h else ""


def html_text(html: str | bytes, selector: str | None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    node = (soup.select_one(selector) if selector else None) or soup.find("main") \
        or soup.find("article") or soup.body or soup
    lines = [_clean(t) for t in node.get_text("\n").split("\n")]
    return "\n".join(t for t in lines if t)


class SchkgKantoneScraper(PracticeScraper):
    SOURCE_KEY = "schkg_kantone"
    ISSUING_AUTHORITY = "KBKS"             # per row: the canton code, see _normalize
    DEFAULT_DOC_TYPE = "kreisschreiben"
    REVISION_FIELD = "revision"
    CACHE_PDFS = False
    OCR_SCANS = True
    REQUEST_DELAY = 1.5
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def __init__(self, registry: tuple[Entry, ...] | None = None):
        super().__init__()
        self.registry = REGISTRY if registry is None else registry

    def get(self, url: str, **kw):
        host = urlsplit(url).hostname or ""
        for domain, var in TUNNEL_ENV.items():
            if host == domain or host.endswith("." + domain):
                proxy = os.environ.get(var, "")
                if proxy:
                    kw.setdefault("proxies", {"http": proxy, "https": proxy})
                break
        return super().get(url, **kw)

    def _make_doc_id(self, stub: dict) -> str:
        base = f"{self.SOURCE_KEY}_{slugify(stub['doc_number'])}"
        if stub["doc_number"].endswith("Existenzminimum"):
            year = (stub.get("date") or "")[:4]
            base = f"{base}_{year}" if year else base
        return f"{base}_{stub.get('language', 'de')}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub["canton"]
        doc["revision"] = stub["revision"]
        if not doc["date"]:
            # JU and NE anchors carry no date; the document's first date is
            # its issuance date in every circular checked.
            doc["date"] = first_date_iso(body_text[:3000])
        return doc

    def discover_documents(self) -> Iterator[dict]:
        for entry in self.registry:
            try:
                yield from self._discover(entry)
            except Exception as e:
                logger.warning("[%s] %s %s failed: %s", self.SOURCE_KEY, entry.canton, entry.url, e)

    def _discover(self, entry: Entry) -> Iterator[dict]:
        exm = bool(re.search(entry.existenzminimum, entry.url, re.I)) if entry.existenzminimum else None
        if entry.mode == "pdf":
            yield make_stub(entry, entry.title or entry.url, entry.url, entry.date,
                            existenzminimum=exm)
            return
        if entry.mode == "static":
            body = (STATIC_DIR / entry.static_file).read_text(encoding="utf-8")
            yield make_stub(entry, entry.title or entry.url, entry.url, entry.date,
                            body_text=body, existenzminimum=exm)
            return
        r = self.get(entry.url)
        r.raise_for_status()
        # Bytes, not r.text: silgeneve.ch serves windows-1252 without a header
        # charset, and BeautifulSoup honours the page's own <meta charset>.
        if entry.mode == "html":
            yield make_stub(entry, entry.title or page_title(r.content) or entry.url, entry.url,
                            entry.date, body_text=html_text(r.content, entry.html_selector),
                            existenzminimum=exm)
            return
        stubs = parse_listing(r.content, entry)
        logger.info("[%s] %s %s: %d documents", self.SOURCE_KEY, entry.canton, entry.url, len(stubs))
        yield from stubs
