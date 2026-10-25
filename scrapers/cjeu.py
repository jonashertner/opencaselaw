"""
Court of Justice of the European Union — judgments relevant to Swiss law
========================================================================

  https://publications.europa.eu/webapi/rdf/sparql          CELLAR metadata (SPARQL)
  http://publications.europa.eu/resource/celex/{celex}      CELLAR content (XHTML,
                                                            Accept-Language deu/fra/ita)

Swiss courts rely on CJEU case law where Switzerland has taken over EU law
or committed to follow it:

  * Lugano Convention — Protocol 2 requires "due account" of CJEU case law on
    the Brussels regime (1968 Convention, Regulations 44/2001 and 1215/2012);
  * Schengen/Dublin association — Dublin II/III, Eurodac, Schengen Borders
    Code, Return Directive, Visa Code;
  * the Agreement on the Free Movement of Persons (FZA) — the agreement
    itself, Directive 2004/38, Regulations 1612/68 and 492/2011, social
    security coordination (1408/71, 883/2004, 987/2009), posting of workers,
    recognition of professional qualifications;
  * the Air Transport Agreement — Regulation 261/2004 (air passenger rights);
  * autonomous adaptation — VAT (77/388, 2006/112), data protection
    (95/46, GDPR), product liability.

Scope = Court of Justice judgments (CELEX 6yyyyCJ) that interpret or cite one
of SCOPE_ACTS (~3,300 distinct on 2026-10-25). General Court judgments,
orders and Advocate General opinions are not taken. CELLAR is the EU's
open-data service; reuse is free with attribution (Commission Decision
2011/833/EU, CC BY 4.0) — so the rows stay out of the CC0 export.

One row per judgment (the build deduplicates on court + docket + date, so
language versions cannot be separate rows): full text in German, French
where no German version exists. The Court's own keyword chain at the head of
the judgment („Vorlage zur Vorabentscheidung — … “) goes into regeste /
abstract_de, and the French and Italian chains into abstract_fr /
abstract_it, so French and Italian queries find the judgment too.

decision_id = cjeu_<CELEX> (joined cases share one CELEX), docket_number =
the case number(s) ('C-417/15'), external_id = the ECLI.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from models import Decision, extract_citations, make_decision_id

logger = logging.getLogger(__name__)

SPARQL_URL = "https://publications.europa.eu/webapi/rdf/sparql"
CONTENT_URL = "http://publications.europa.eu/resource/celex/{celex}"
EURLEX_URL = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/?uri=CELEX:{celex}"

# CELEX of each act -> short German label (regeste suffix and topic).
SCOPE_ACTS = {
    "41968A0927(01)": "EuGVÜ 1968", "32001R0044": "EuGVVO 44/2001", "32012R1215": "EuGVVO 1215/2012",
    "22007A1221(03)": "LugÜ 2007",
    "32003R0343": "Dublin II", "32013R0604": "Dublin III", "32013R0603": "Eurodac",
    "32006R0562": "Schengener Grenzkodex 562/2006", "32016R0399": "Schengener Grenzkodex 2016/399",
    "32008L0115": "Rückführungsrichtlinie", "32009R0810": "Visakodex",
    "22002A0430(01)": "FZA", "32004L0038": "Freizügigkeitsrichtlinie 2004/38",
    "31968R1612": "VO 1612/68", "32011R0492": "VO 492/2011",
    "31971R1408": "VO 1408/71", "32004R0883": "VO 883/2004", "32009R0987": "VO 987/2009",
    "31996L0071": "Entsenderichtlinie", "32005L0036": "Berufsanerkennungsrichtlinie 2005/36",
    "32004R0261": "Fluggastrechte-VO 261/2004",
    "31977L0388": "6. MWST-Richtlinie", "32006L0112": "MWST-Systemrichtlinie",
    "31995L0046": "Datenschutzrichtlinie 95/46", "32016R0679": "DSGVO",
    "31985L0374": "Produkthaftungsrichtlinie",
}
LEGAL_AREA = "EU-Recht / Droit de l'UE (EuGH)"
META_BATCH = 40
_LANG_URI = "http://publications.europa.eu/resource/authority/language/{}"
_LANGS = {"DEU": "de", "FRA": "fr", "ITA": "it"}

_PREFIX = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
"""


def scope_query() -> str:
    values = " ".join(f'"{c}"^^xsd:string' for c in SCOPE_ACTS)
    return _PREFIX + f"""SELECT DISTINCT ?celex ?date ?ecli ?actc WHERE {{
  VALUES ?actc {{ {values} }}
  ?act cdm:resource_legal_id_celex ?actc .
  {{ ?case cdm:case-law_interpretes_resource_legal ?act }} UNION {{ ?case cdm:work_cites_work ?act }}
  ?case cdm:resource_legal_id_celex ?celex .
  FILTER(REGEX(STR(?celex), "^6[0-9]{{4}}CJ"))
  OPTIONAL {{ ?case cdm:work_date_document ?date }}
  OPTIONAL {{ ?case cdm:case-law_ecli ?ecli }}
}}"""


def meta_query(celexes: list[str]) -> str:
    values = " ".join(f'"{c}"^^xsd:string' for c in celexes)
    langs = " ".join(f"<{_LANG_URI.format(code)}>" for code in _LANGS)
    return _PREFIX + f"""SELECT ?celex ?lang ?ident ?parties ?info WHERE {{
  VALUES ?celex {{ {values} }}
  VALUES ?lang {{ {langs} }}
  ?w cdm:resource_legal_id_celex ?celex .
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language ?lang .
  OPTIONAL {{ ?e cdm:expression_case-law_identifier_case ?ident }}
  OPTIONAL {{ ?e cdm:expression_case-law_parties ?parties }}
  OPTIONAL {{ ?e cdm:expression_title_information_additional ?info }}
}}"""


def group_scope(bindings: list[dict]) -> dict[str, dict]:
    """celex -> {date, ecli, acts}; one SPARQL row per (judgment, act)."""
    out: dict[str, dict] = {}
    for b in bindings:
        celex = b["celex"]["value"]
        rec = out.setdefault(celex, {"date": None, "ecli": "", "acts": []})
        if "date" in b and not rec["date"]:
            try:
                rec["date"] = date.fromisoformat(b["date"]["value"][:10])
            except ValueError:
                pass
        if "ecli" in b and not rec["ecli"]:
            rec["ecli"] = b["ecli"]["value"]
        act = SCOPE_ACTS.get(b["actc"]["value"])
        if act and act not in rec["acts"]:
            rec["acts"].append(act)
    return out


def group_meta(bindings: list[dict]) -> dict[str, dict[str, dict]]:
    """celex -> lang -> {ident, parties, info}."""
    out: dict[str, dict[str, dict]] = {}
    for b in bindings:
        lang = _LANGS.get(b["lang"]["value"].rsplit("/", 1)[-1])
        if not lang:
            continue
        rec = out.setdefault(b["celex"]["value"], {}).setdefault(lang, {})
        for key in ("ident", "parties", "info"):
            if key in b and not rec.get(key):
                rec[key] = re.sub(r"\s+", " ", b[key]["value"]).strip()
    return out


_CASE_NO = re.compile(r"\b(?:([CT])\s*[‑-]\s*)?(\d{1,4})\s*[/-]\s*(\d{2})\b")


def case_numbers(ident: str, celex: str) -> str:
    """'Rechtssache C-417/15' -> 'C-417/15'; joined cases keep all numbers.
    Cases lodged before the General Court existed (1989) have no 'C-':
    'Rechtssache 15-69' -> '15/69'. Without an identifier the CELEX gives
    the lead case (62015CJ0417 -> C-417/15, 61969CJ0015 -> 15/69)."""
    nums = []
    for prefix, num, yy in _CASE_NO.findall(ident or ""):
        nums.append(f"{prefix}-{int(num)}/{yy}" if prefix else f"{int(num)}/{yy}")
    if nums:
        return ", ".join(dict.fromkeys(nums))
    m = re.match(r"6(\d{4})CJ(\d{4})", celex)
    if not m:
        return celex
    year, num = int(m.group(1)), int(m.group(2))
    return f"C-{num}/{str(year)[2:]}" if year >= 1989 else f"{num}/{str(year)[2:]}"


_CHAMBER = re.compile(r"\(([^)]*(?:Kammer|chambre|Plenum|assemblée)[^)]*)\)", re.I)


def chamber_of(info: str) -> str | None:
    m = _CHAMBER.search(info or "")
    return m.group(1).strip() if m else None


def judgment_text(xhtml: bytes) -> str:
    soup = BeautifulSoup(xhtml, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


_CHAIN = {"de": re.compile(r"„(.{20,3000}?)“", re.S), "fr": re.compile(r"«\s*(.{20,3000}?)\s*»", re.S),
          "it": re.compile(r"«\s*(.{20,3000}?)\s*»", re.S)}


_OLD_KEYWORDS = re.compile(r"\+{4}\s*(.{20,1500}?)\s*(?:\+{4}|\n\s*(?:Leitsätze|Sommaire|Massima|Parteien|Parties|Parti)\b)", re.S)


def keyword_chain(text: str, lang: str) -> str:
    """The Court's keyword summary at the head of the judgment ('„Vorlage zur
    Vorabentscheidung — …“'); for the older judgments, which have none, the
    'Schlüsselwörter / Mots clés ++++' block of the EUR-Lex HTML."""
    m = _CHAIN[lang].search(text[:6000])
    if not m:
        m = _OLD_KEYWORDS.search(text[:8000])
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


class CJEUScraper(BaseScraper):
    """CJEU judgments interpreting or citing EU acts that matter in Swiss law."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 120
    MAX_ERRORS = 20
    USER_AGENT = "OpenCaseLawBot/1.0 (+https://opencaselaw.ch; team@jonashertner.com)"
    CACHE_NONE_AS_GAP = True

    @property
    def court_code(self) -> str:
        return "cjeu"

    def _sparql(self, query: str) -> list[dict]:
        r = self.post(SPARQL_URL, data={"query": query},
                      headers={"Accept": "application/sparql-results+json"})
        return r.json()["results"]["bindings"]

    def _content(self, celex: str, lang3: str) -> str:
        """Text of one language version, '' when CELLAR has none. Judgments
        since the 2000s are held as XHTML; older ones (61969CJ0015) only as
        HTML and PDF."""
        for accept in ("application/xhtml+xml", "text/html"):
            self._rate_limit()
            r = self.session.get(CONTENT_URL.format(celex=celex), timeout=self.TIMEOUT,
                                 headers={"Accept": accept, "Accept-Language": lang3})
            if r.status_code == 404:
                continue
            r.raise_for_status()
            return judgment_text(r.content)
        return ""

    def discover_new(self, since_date=None) -> Iterator[dict]:
        scope = group_scope(self._sparql(scope_query()))
        todo = sorted(c for c in scope if not self.state.is_known(make_decision_id("cjeu", c)))
        if since_date:
            todo = [c for c in todo if not scope[c]["date"] or scope[c]["date"] >= since_date]
        logger.info(f"[cjeu] {len(scope)} judgments in scope, {len(todo)} new")
        if not scope:
            raise RuntimeError("[cjeu] the scope query returned nothing — CELLAR changed?")
        for i in range(0, len(todo), META_BATCH):
            batch = todo[i:i + META_BATCH]
            meta = group_meta(self._sparql(meta_query(batch)))
            for celex in batch:
                yield {"celex": celex, "decision_id": make_decision_id("cjeu", celex),
                       "docket_number": celex, **scope[celex], "meta": meta.get(celex, {})}

    def fetch_decision(self, stub: dict) -> Decision | None:
        celex, meta = stub["celex"], stub["meta"]
        text, lang = self._content(celex, "deu"), "de"
        if len(text) < 1000:
            text, lang = self._content(celex, "fra"), "fr"
        if len(text) < 1000:
            logger.info(f"[cjeu] {celex}: no German or French text")
            return None
        chains = {lang: keyword_chain(text, lang)}
        for other, code in (("fr", "fra"), ("it", "ita")):
            if other not in chains:
                chains[other] = keyword_chain(self._content(celex, code), other)
        if lang == "fr":
            chains["de"] = ""
        m = meta.get(lang, {}) or meta.get("de", {}) or meta.get("fr", {})
        docket = case_numbers(m.get("ident", "") or meta.get("fr", {}).get("ident", ""), celex)
        acts = "; ".join(stub["acts"])
        regeste = "; ".join(x for x in (chains.get(lang) or "", f"[Bezug: {acts}]" if acts else "") if x)
        return Decision(
            decision_id=stub["decision_id"],
            court="cjeu",
            canton="EU",
            chamber=chamber_of(m.get("info", "")),
            docket_number=docket,
            docket_number_2=celex,
            decision_date=stub.get("date"),
            language=lang,
            title=(m.get("parties") or "").rstrip(" .") or docket,
            legal_area=LEGAL_AREA,
            regeste=regeste or None,
            abstract_de=chains.get("de") or None,
            abstract_fr=chains.get("fr") or None,
            abstract_it=chains.get("it") or None,
            decision_type="Urteil" if lang == "de" else "Arrêt",
            full_text=text,
            source_url=EURLEX_URL.format(lang=lang.upper(), celex=celex),
            external_id=stub.get("ecli") or None,
            cited_decisions=extract_citations(text) if len(text) > 200 else [],
            scraped_at=datetime.now(timezone.utc),
        )
