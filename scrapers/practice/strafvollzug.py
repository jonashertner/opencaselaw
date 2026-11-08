"""
Inter-cantonal penal-enforcement concordats — CLDJP + NWI/OSK
===============================================================

Switzerland's prison-and-probation enforcement is run by three concordats
(groups of cantons), each publishing its own implementing regulations,
decisions and guidance beside the federal StGB/StPO/JStPO:

  CLDJP  Conférence latine des chefs des départements de justice et police
         (the six Latin cantons: GE, VD, VS, NE, FR, JU, plus TI and BE as
         members of individual sub-concordats). WordPress site (cldjp.ch),
         every document as a wp/v2 'media' (PDF) attachment, most grouped
         under a parent page naming the concordat body (adultes, mineurs,
         police, CES = détention administrative, LMC, gens du voyage).
         issuing_authority "CLDJP".

  NWI/OSK  Nordwest- und Innerschweizer + Ostschweizer Strafvollzugskonkordat
         (the German-speaking cantons' two sister concordats, run jointly).
         Webflow site (justizvollzug.ch/konkordate): a "Vorlagen /
         Arbeitsdokumente" page (Vollzugsplan/-bericht templates, RIPOL
         annexes, assessment catalogues) and a "Jahresberichte" page
         (annual/Tätigkeitsberichte, both concordats, 2010-). Their own
         Richtlinien live on a separate ExtJS app, erlasse.konkordate.ch,
         which turned out not to expose a discoverable JSON API (see
         _ERLASSE_KONKORDATE_NOTE below) — not scraped.
         /konkordate/rechtsprechung-bundesgericht is Federal Supreme Court
         rulings already in the decision corpus and is not scraped here.
         issuing_authority "NWI-OSK".

Both sites publish every revision of a text as a NEW, permanent file (a
fresh WordPress attachment id / a fresh Webflow CDN hash) rather than
overwriting one in place, so there is no re-issue-at-a-stable-URL case to
detect (unlike EHRA/hr_kantone): each upload is simply a new row with its
own doc_id, and search_practice's newest-per-doc_number collapse does the
rest. doc_id is derived from the PDF's own URL (stable, content-addressed),
so the DE/FR page fallback that serves the SAME file under two listings
(seen on the NWI/OSK Jahresberichte page, which links the identical PDF set
from both its German and French URL) collapses to one row for free, before
any text is even downloaded.

CLDJP filtering (666 WP media items reported by X-WP-Total; see
_iter_cldjp_media for why only ~465 are actually retrievable through the
paginated endpoint — a live discrepancy, not a bug on our side). Kept:
concordat texts, règlements (implementing regulations), décisions/
Beschlüsse/decisioni of the conference, directives/Richtlinien,
recommandations, the concordat bodies' own statutes/rules of procedure,
annual/activity reports (Rapports d'activité, exposés des motifs). Dropped:
membres-/listes-/organigramme- (member and facility rosters), présentation-/
darstellung- leaflets, PV/OJ (meeting minutes and agendas), communiqués and
"comm_" announcements, newsletters, internal mandates, named-individual
case documents, and a handful of concept/pilot-project papers that are
background material rather than binding guidance (see _classify_cldjp).

doc_number: versions of one text share a canonical, language-neutral
subject key (_cldjp_subject, with a small cross-language synonym table for
the règlements/décisions that are reliably co-published — TIG/GA/LUP, EM,
semi-détention/HG/semiprigionia, établissements/Anstalten/stabilimenti,
sorties/Ausgang/uscita, etc.); search_practice's collapse groups by
(source, LANGUAGE, doc_type, doc_number), so an imperfect cross-language
match only loses the "same doc_number across languages" convenience, never
correctness.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
import unicodedata
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PracticeScraper, first_date_iso, month_year_year, slugify
from .langdedup import TextDedupMixin, body_lang

logger = logging.getLogger(__name__)

# ── erlasse.konkordate.ch: not crackable within the time budget ──
# Scala.js (Scala-GWT style, idparc AG's platform — same stack serves
# erlasse.unisg.ch and rechtsbuch.ur.ch) running behind akka-http. The
# bundle (main.js, 5.6 MB across two files) opens exactly two app-level
# endpoints, both opaque: "/ws" (a WebSocket) and "/ajax", whose request
# body is built by a dedicated serializer function (not JSON.stringify) —
# there is no "/api/..." path, no Ext.Direct REMOTING_API descriptor, and
# no plain query-string listing endpoint anywhere in either bundle. Probed
# ~20 common REST guesses (/api, /api/v1, /svc, /rest, /dgn/api, ...), all
# 404. Session is a JWT in a cookie named "dingsessiontoken". Concluded
# not worth reverse-engineering a bespoke binary/WS protocol in the time
# available; the NWI/OSK Richtlinien are therefore NOT in this scraper.

# ════════════════════════════════════════════════════════════════════
# CLDJP (cldjp.ch — WordPress)
# ════════════════════════════════════════════════════════════════════

_CLDJP_BASE = "https://www.cldjp.ch"
_CLDJP_MEDIA = _CLDJP_BASE + "/wp-json/wp/v2/media?mime_type=application/pdf&per_page=20&page={page}"

# WP parent-page id -> which concordat body / section the document belongs
# to (hardcoded from a one-time lookup against /wp-json/wp/v2/pages/<id>,
# 2026-11-08; used only to enrich `topics`, never for filtering).
_CLDJP_PARENT_TOPIC: dict[int, str] = {
    397: "Concordat adultes", 403: "Concordat mineurs", 456: "Concordat police",
    461: "Concordat CES", 856: "Concordat LMC", 925: "Gens du voyage",
    830: "Domaine interconcordataire", 592: "Fondation latine",
    768: "Entreprises de sécurité", 792: "Affaires militaires",
    560: "Affaires militaires et protection de la population",
    3369: "PLESORR", 3606: "PLESORR-Pro", 104: "Justice", 107: "Police",
    109: "Asile et migrants", 482: "Organes", 527: "Organisation",
}

# ── exclusion rules (member rosters, leaflets, minutes, agendas, press
# releases, newsletters, internal mandates, named-individual case papers,
# concept/pilot papers — none of these are binding legal/practice texts) ──
_CLDJP_EXCLUDE = re.compile(
    r"membre|organigramme|liste[-_ ]?(membres|etablissements|actes)|\bliste\b|"
    r"pr[eé]sentation|darstellung|\bpv\b|proc[eè]s[-_ ]?verbal|protokoll|"
    r"\boj\b|ordre.du.jour|traktanden|communiqu|^comm[_-]|comm_centre|newsletter|"
    r"\bmandat\b|^a\.[ivx]+\.\d|^0-[12]\s|saisine|note[-_ ](ad|historique|concernant)|"
    r"r[eé]sum[eé]|projet.pilote|standards_osk|^concept-|doc-test|r[eé]visions?$",
    re.I,
)

# first-match-wins classification: (pattern, doc_type)
_CLDJP_ORGAN_RULES = re.compile(
    r"commission|conf[eé]rence|cldjp|clcpc|\bclp\b|organisation|fonctionnement|"
    r"fondation|autorit[eé]|recours",
    re.I,
)
_CLDJP_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bconcordat\b|\bkonkordat\b|\bconcordato\b|\bstatuts?\b|vereinbarung|"
                r"\baccord\b|\bconvention\b", re.I), "konkordat"),
    (re.compile(r"r[eè]glement|reglement|regolamento", re.I), "__reglement__"),   # resolved below
    (re.compile(r"\bd[eé]cision\b|\bbeschluss\b|\bdecisione\b", re.I), "weisung"),
    (re.compile(r"recommand|empfehlung|raccoman", re.I), "empfehlung"),
    (re.compile(r"directive|richtlinie|richlinie|protocole|protocollo|standards\b|"
                r"lignes directrices|document.cadre|principes r[eé]gissant", re.I), "richtlinie"),
    (re.compile(r"merkblatt|\bnota\b|\bnotice\b", re.I), "merkblatt"),
    (re.compile(r"vademecum|vademekum|wegleitung|vade.mecum", re.I), "handbuch"),
    (re.compile(r"\brapport\b|\bbericht\b|\brapporto\b|activit[eé]|vue.d.ensemble|"
                r"expos[eé].*motifs|anstaltsplanung|planification|"
                r"kapazitatsmonitoring|monitorage.*capacit", re.I), "bericht"),
    (re.compile(r"^anhang-[ab]|^annexe-[ab]", re.I), "erlass"),
]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _clean(s: str) -> str:
    return " ".join((html_lib.unescape(s or "")).split())


def classify_cldjp(title: str, slug: str) -> tuple[bool, str] | tuple[bool, None]:
    """(keep, doc_type) for one CLDJP media item, from its title (or slug
    when WordPress never got a human title) and file slug."""
    text = f"{title} {slug}"
    # underscore is a \w character, so "20261012_Bericht" or "x_pv_y" has no
    # \b right after the "_" — check both the raw text (for exclude patterns
    # that deliberately look for a literal "_"/"-", e.g. "comm_centre") and
    # an underscore/period-normalized, accent-stripped copy (for every
    # \b-bounded word below, e.g. "Kapazitätsmonitoring").
    words = _strip_accents(re.sub(r"[_.]", " ", text))
    if _CLDJP_EXCLUDE.search(text) or _CLDJP_EXCLUDE.search(words):
        return False, None
    for pattern, doc_type in _CLDJP_RULES:
        if pattern.search(words):
            if doc_type == "__reglement__":
                # the concordat organ's OWN rules of procedure are grouped
                # with the founding concordat texts, not with implementing
                # regulations (Règlement sur le TIG, Reglement über EM, ...)
                return True, "konkordat" if _CLDJP_ORGAN_RULES.search(words) else "erlass"
            return True, doc_type
    return False, None


# cross-language subject synonyms for the règlements/décisions that are
# reliably co-published (same adoption/revision date across languages)
_SUBJECT_SYNONYMS: dict[str, str] = {
    "ga": "tig", "lup": "tig",
    "hg": "semidetention", "halbgefangenschaft": "semidetention",
    "semiprigionia": "semidetention", "semidetention": "semidetention",
    "etablissement": "etablissements", "anstalten": "etablissements", "stabilimenti": "etablissements",
    "sorties": "sortie", "ausgang": "sortie", "ausgangsbewilligungen": "sortie",
    "uscita": "sortie", "autorizzazioni": "sortie",
    "disziplinarrecht": "disciplinaire", "disciplinare": "disciplinaire",
    "arbeitsexternat": "travailexterne", "lavoroesterno": "travailexterne", "externat": "travailexterne",
    "arbeitsentgelt": "remuneration", "remunerazione": "remuneration", "remuneration": "remuneration",
    "zahnbehandlungen": "soinsdentaires", "dentaires": "soinsdentaires",
    "unfallversicherung": "assuranceaccidents", "accidents": "assuranceaccidents",
    "bewaehrungshilfe": "probation", "bewahrungshilfe": "probation",
    "ausbildung": "formation", "formazione": "formation",
}
_SUBJECT_NOISE = re.compile(
    r"\b(reglement|regolamento|decision|beschluss|decisione|directive|richtlinie|richlinie|"
    r"recommandations?|empfehlungen|raccomandazioni|rapport|bericht|rapporto|vereinbarung|"
    r"concordat|konkordat|concordato|statuts?|cldjp|clcpc|clp|du|de|des|la|le|les|sur|sul|"
    r"sulla|uber|vom|concerning|concernant|relatif|relative|applicable|aux|a|vf|mod|v|pdf)\b"
)
_DATE_TOKEN = re.compile(r"(?<!\d)\d{6,8}(?!\d)")


def cldjp_subject(text: str) -> str:
    """Canonical, language-neutral-ish subject key for a doc_number."""
    t = _strip_accents(text.lower())
    t = _DATE_TOKEN.sub(" ", t)
    t = re.sub(r"[_\-]", " ", t)
    t = _SUBJECT_NOISE.sub(" ", t)
    words = [w for w in re.findall(r"[a-z]+", t) if len(w) > 1]
    words = [_SUBJECT_SYNONYMS.get(w, w) for w in words]
    seen: set[str] = set()
    out = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return " ".join(out[:6]).strip()


_DOC_NUMBER_LABEL = {
    "konkordat": "Concordat", "erlass": "Règlement", "weisung": "Décision",
    "richtlinie": "Directive", "empfehlung": "Recommandation", "merkblatt": "Notice",
    "handbuch": "Guide", "bericht": "Rapport", "formular": "Formulaire",
}


def cldjp_doc_number(doc_type: str, title: str, slug: str) -> str:
    subject = cldjp_subject(title) or cldjp_subject(slug) or slugify(title)[:40]
    label = _DOC_NUMBER_LABEL.get(doc_type, "Document")
    return f"CLDJP {label} {subject}".strip()


_BARE_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def cldjp_date(title: str, slug: str, upload_date: str) -> str:
    d = first_date_iso(title)
    if d:
        return d
    combined = f"{title} {slug}"
    tokens = _DATE_TOKEN.findall(combined)
    if tokens:
        tok = tokens[-1]
        if len(tok) == 8:
            y, m, dd = tok[:4], tok[4:6], tok[6:8]
        else:
            yy, m, dd = tok[:2], tok[2:4], tok[4:6]
            y = f"19{yy}" if int(yy) >= 50 else f"20{yy}"
        try:
            if 1 <= int(m) <= 12 and 1 <= int(dd) <= 31:
                return f"{y}-{m}-{dd}"
        except ValueError:
            pass
    # no day-precision date anywhere — a bare year in the title/slug
    # ("...-ces-2012", "rapport-2015-fondation-lmc") beats the upload date.
    years = _BARE_YEAR.findall(combined)
    if years:
        return years[-1]
    return (upload_date or "")[:10]


# language detection: title vocabulary, not which WP field it came from.
# "règlement"/"reglement" is deliberately NOT a marker for either language —
# after accent-stripping it is spelled identically in French and German, so
# it would be a coin-flip; a bare "Règlement TIG" with no other vocabulary
# correctly falls through to the French default instead.
_LANG_MARKERS = {
    "de": re.compile(
        r"\buber\b|beschluss|richtlinie|richlinie|wegleitung|vademekum|anstalten|"
        r"ausgangsbewilligungen|arbeitsexternat|arbeitsentgelt|unfallversicherung|"
        r"bewaehrungshilfe|bewahrungshilfe|zahnbehandlungen|halbgefangenschaft|"
        r"gesundheitskosten|vollzugsplan|gemeinnutzige|einsatz.*hunden|jahrliche|"
        r"grundausbildung|ehrenhaftigkeitsbezeugnis|mitteilung|merkblatt", re.I),
    "it": re.compile(
        r"regolamento|decisione|direttiva|raccomandazioni|rapporto|esecuzione|"
        r"lavoro.esterno|utilita|semiprigionia|\blup\b|stabilimenti|autorizzazioni|"
        r"uscita|minorenni|disciplinare|formazione|remunerazione|decisioni|"
        r"\bnota\b|alleggeriment", re.I),
    "fr": re.compile(
        r"decision|directive|recommandation|rapport|resume|execution|"
        r"detention|presentation|autorisations?|sortie|disciplinaire|formation|"
        r"probation|remuneration|soins.dentaires|assurance.accidents|travail.externe|"
        r"etablissements|conference|commission|comite|organe|standards|historique|"
        r"\bnotice\b|allegement", re.I),
}


def detect_lang_cldjp(text: str) -> str:
    t = re.sub(r"[_.]", " ", _strip_accents(text))
    scores = {lang: len(rx.findall(t)) for lang, rx in _LANG_MARKERS.items()}
    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_lang if best_score > 0 else "fr"


def _iter_cldjp_media(scraper: "StrafvollzugScraper") -> Iterator[dict]:
    """Page through the WP media endpoint until an empty page comes back.

    X-WP-Total reports 666 PDFs, but per_page is not honoured consistently
    by this install (confirmed across two independent full scans with
    different orderings): paging to exhaustion yields a stable 465 unique
    attachment ids both times. We trust what the endpoint actually hands
    back over the advertised total.
    """
    page = 1
    seen_ids: set[int] = set()
    while True:
        try:
            r = scraper.get(_CLDJP_MEDIA.format(page=page))
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            logger.warning("[strafvollzug] cldjp media page %d failed: %s", page, e)
            break
        if not batch:
            break
        new = 0
        for item in batch:
            if item.get("id") in seen_ids:
                continue
            seen_ids.add(item["id"])
            new += 1
            yield item
        if new == 0:
            break
        page += 1
        if page > 60:          # safety stop well past the expected ~34 pages
            break


def _cldjp_stub(item: dict) -> dict | None:
    media_id = item.get("id")
    slug = item.get("slug") or ""
    raw_title = (item.get("title") or {}).get("rendered") or slug
    title = _clean(raw_title)
    keep, doc_type = classify_cldjp(title, slug)
    if not keep:
        return None
    lang = detect_lang_cldjp(f"{title} {slug}")
    date = cldjp_date(title, slug, item.get("date", ""))
    number = cldjp_doc_number(doc_type, title, slug)
    parent_topic = _CLDJP_PARENT_TOPIC.get(item.get("post"))
    topics = ["CLDJP", number]
    if parent_topic:
        topics.append(parent_topic)
    pdf_url = item.get("source_url") or ""
    if not pdf_url:
        return None
    return {
        "pdf_url": pdf_url,
        "url": item.get("link") or _CLDJP_BASE,
        "title": title,
        "doc_number": number,
        "date": date,
        "language": lang,
        "doc_type": doc_type,
        "topics": topics,
        "sv_id": f"cldjp_{media_id}",
    }


# ════════════════════════════════════════════════════════════════════
# NWI/OSK (justizvollzug.ch — Webflow)
# ════════════════════════════════════════════════════════════════════

_NWIOSK_BASE = "https://www.justizvollzug.ch"
_NWIOSK_VORLAGEN = _NWIOSK_BASE + "/konkordate/vorlagen-arbeitsdokumente"
_NWIOSK_JAHRESBERICHTE = _NWIOSK_BASE + "/konkordate/jahresberichte"
# The FR pages (/fr/konkordate/vorlagen-arbeitsdokumente, /fr/konkordate/
# jahresberichte) link the IDENTICAL German-only PDF set (checked
# 2026-11-08: all hrefs on the FR page are a subset of the DE page's, same
# count) — no French translation exists, so they are not fetched again.

# Vollzugsplan-Richtziele translations for inmates (ES/PT/SR-HR/SQ) are not
# indexed; DE/FR/IT/EN are.
_RICHTZIELE_SKIP_LANG = re.compile(r"spanisch|portugiesisch|serbokroatisch|albanisch", re.I)
_RICHTZIELE_LANG = {"deutsch": "de", "franzosisch": "fr", "italienisch": "it", "englisch": "en"}


def _parse_webflow_cards(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen_url: set[str] = set()
    for item in soup.select("div.cr-cms-item"):
        a = item.find("a", href=True)
        if not a or not a["href"].lower().endswith(".pdf"):
            continue
        url = urljoin(base_url, a["href"])
        if url in seen_url:
            continue
        seen_url.add(url)
        bold = item.select_one(".rt-bold")
        small = item.select_one(".rt-small")
        title = _clean(bold.get_text(" ", strip=True)) if bold else _clean(a.get_text(" ", strip=True))
        meta = _clean(small.get_text(" ", strip=True)) if small else ""
        out.append({"title": title, "meta": meta, "pdf_url": url})
    return out


# title (lowercased) -> (doc_type, doc_number) for the Vorlagen page. RIPOL's
# two catalogue entries point at the SAME PDF (checked 2026-11-08); only the
# more descriptive one is listed here, the duplicate is simply never yielded
# twice under the same key (dedup is by pdf_url anyway).
_VORLAGEN_TYPES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"musterformular.*ripol|formular.*ripol", re.I), "formular", "NWI-OSK RIPOL Musterformular"),
    (re.compile(r"informationsrecht des opfers", re.I), "formular", "NWI-OSK Formular Informationsrecht des Opfers"),
    (re.compile(r"vollzugsauftrag", re.I), "formular", "NWI-OSK Vollzugsauftrag"),
    (re.compile(r"richtziele vollzugsplan", re.I), "richtlinie", "NWI-OSK Richtziele Vollzugsplan"),
    (re.compile(r"^vollzugsplan light", re.I), "formular", "NWI-OSK Vollzugsplan (light)"),
    (re.compile(r"^vollzugsplan kurz", re.I), "formular", "NWI-OSK Vollzugsplan (kurz)"),
    (re.compile(r"^vollzugsplan\b", re.I), "formular", "NWI-OSK Vollzugsplan"),
    (re.compile(r"vollzugsbericht mit kommentar", re.I), "formular", "NWI-OSK Vollzugsbericht (mit Kommentar)"),
    (re.compile(r"^vollzugsbericht\b", re.I), "formular", "NWI-OSK Vollzugsbericht"),
    (re.compile(r"kurzerlauterungen", re.I), "merkblatt", "NWI-OSK Kurzerläuterungen Vollzugsplan/-bericht"),
    (re.compile(r"^erlauterungen", re.I), "merkblatt", "NWI-OSK Erläuterungen Vollzugsplan/-bericht"),
    (re.compile(r"interventionsplan", re.I), "formular", "NWI-OSK Interventionsplan Bewährungsdienste"),
    (re.compile(r"sozialbericht", re.I), "formular", "NWI-OSK Sozialbericht Bewährungsdienste"),
    (re.compile(r"schlussbericht", re.I), "bericht", "NWI-OSK Schlussbericht Schnittstelle Justizvollzug/Sozialhilfe"),
    (re.compile(r"kriterienkatalog", re.I), "richtlinie", "NWI-OSK Basler Kriterienkatalog Legalprognose"),
    (re.compile(r"fragenkatalog", re.I), "formular", "NWI-OSK Fragenkatalog forensisch-psychiatrische Gutachten"),
]


def classify_vorlage(title: str) -> tuple[str, str, str | None] | None:
    """(doc_type, doc_number, language) or None to skip (non-DE/FR/IT/EN
    Richtziele translation)."""
    ascii_title = _strip_accents(title)
    m = re.search(r"richtziele vollzugsplan (\w+)", ascii_title, re.I)
    if m:
        if _RICHTZIELE_SKIP_LANG.search(ascii_title):
            return None
        lang = _RICHTZIELE_LANG.get(m.group(1).lower(), "de")
        return "richtlinie", "NWI-OSK Richtziele Vollzugsplan", lang
    for pattern, doc_type, number in _VORLAGEN_TYPES:
        if pattern.search(ascii_title):
            return doc_type, number, "de"
    return "formular", f"NWI-OSK {title}", "de"


# The page also carries annual reports of the concordats' sub-bodies
# (KoFako, the Fachkommission, the shared audit organisation) beside the
# NWI/OSK/combined reports named in the brief — all 44 PDFs are kept, not
# just the 3 headline series.
_JB_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Jahresbericht NWI\s*&\s*OSK\s+(\d{4})", re.I), "NWI-OSK Jahresbericht {}"),
    (re.compile(r"T[aä]tigkeitsbericht Strafvollzugskonkordat NWI\s+(\d{4})", re.I), "NWI Tätigkeitsbericht {}"),
    (re.compile(r"Jahresbericht Strafvollzugskonkordat OSK\s+(\d{4})", re.I), "OSK Jahresbericht {}"),
    (re.compile(r"KoFako Jahresbericht\s+(\d{4})", re.I), "NWI-OSK KoFako Jahresbericht {}"),
    (re.compile(r"Jahresbericht der Fachkommission\s+(\d{4})", re.I), "NWI-OSK Fachkommission Jahresbericht {}"),
    (re.compile(r"Jahresbericht konkordatliche Auditorganisation\s+(\d{4})", re.I),
     "NWI-OSK Auditorganisation Jahresbericht {}"),
]
_JB_STRIP = re.compile(r"\bJahresbericht\b|\bT[aä]tigkeitsbericht\b|\bStrafvollzugskonkordat\b"
                       r"|\b(?:19|20)\d{2}\b", re.I)


def classify_jahresbericht(title: str) -> tuple[str, str] | None:
    for pattern, label in _JB_PATTERNS:
        m = pattern.search(title)
        if m:
            return m.group(1), label.format(m.group(1))
    # generic fallback for any other "<body> Jahresbericht/Tätigkeitsbericht
    # <year>" title the page may carry, so an unrecognised sub-body is
    # indexed under its own name instead of silently dropped.
    years = re.findall(r"\b(?:19|20)\d{2}\b", title)
    if not years:
        return None
    year = years[-1]
    body = " ".join(_JB_STRIP.sub(" ", title).split())
    number = f"NWI-OSK {body} Jahresbericht {year}".strip() if body else f"NWI-OSK Jahresbericht {year}"
    return year, number


class StrafvollzugScraper(TextDedupMixin, PracticeScraper):
    SOURCE_KEY = "strafvollzug"
    ISSUING_AUTHORITY = "CLDJP"          # overridden per row (CLDJP / NWI-OSK)
    DEFAULT_DOC_TYPE = "erlass"
    REQUEST_DELAY = 1.1
    OCR_SCANS = True
    NO_TEXT_LAYER_BODY = "[Textlayer fehlt: gescanntes PDF]"

    def _make_doc_id(self, stub: dict) -> str:
        return f"{self.SOURCE_KEY}_{stub['sv_id']}"

    def _normalize(self, stub: dict, body_text: str) -> dict:
        doc = super()._normalize(stub, body_text)
        doc["issuing_authority"] = stub.get("issuing_authority", "CLDJP")
        if not doc["date"]:
            # the NWI/OSK Vorlagen page gives some annexes no date at all
            # (just "PDF" with no size/date, e.g. the RIPOL Musterformular);
            # fall back to a date in the document's own text.
            doc["date"] = first_date_iso(body_text[:3000])
        return doc

    def discover_documents(self) -> Iterator[dict]:
        yield from self._cldjp()
        yield from self._nwiosk_vorlagen()
        yield from self._nwiosk_jahresberichte()

    # ── CLDJP ──
    def _cldjp(self) -> Iterator[dict]:
        kept, seen_media = 0, 0
        for item in _iter_cldjp_media(self):
            seen_media += 1
            stub = _cldjp_stub(item)
            if stub is None:
                continue
            stub["issuing_authority"] = "CLDJP"
            out = self.with_body(stub, stub["doc_number"])
            if out:
                # the text, not the file name, decides the language; doc_id
                # keeps the title-based tag so later runs still recognise it
                lang = body_lang(out.get("body_text", ""))
                if lang and lang != out["language"]:
                    out = {**out, "language": lang}
                kept += 1
                yield out
        logger.info("[strafvollzug] cldjp: %d media items seen, %d kept after filtering",
                    seen_media, kept)

    # ── NWI/OSK ──
    def _page(self, url: str) -> str | None:
        try:
            r = self.get(url)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[strafvollzug] %s fetch failed: %s", url, e)
            return None

    def _nwiosk_vorlagen(self) -> Iterator[dict]:
        html = self._page(_NWIOSK_VORLAGEN)
        if html is None:
            return
        cards = _parse_webflow_cards(html, _NWIOSK_VORLAGEN)
        logger.info("[strafvollzug] nwiosk vorlagen: %d PDFs", len(cards))
        for card in cards:
            classified = classify_vorlage(card["title"])
            if classified is None:
                continue
            doc_type, number, lang = classified
            date = first_date_iso(card["meta"]) or month_year_year(card["meta"]) or ""
            stub = {
                "pdf_url": card["pdf_url"],
                "url": _NWIOSK_VORLAGEN,
                "title": card["title"],
                "doc_number": number,
                "date": date,
                "language": lang,
                "doc_type": doc_type,
                "topics": ["NWI-OSK", number],
                "issuing_authority": "NWI-OSK",
                "sv_id": f"nwiosk_v_{slugify(card['pdf_url'].rsplit('/', 1)[-1])[:60]}",
            }
            out = self.with_body(stub, number)
            if out:
                yield out

    def _nwiosk_jahresberichte(self) -> Iterator[dict]:
        html = self._page(_NWIOSK_JAHRESBERICHTE)
        if html is None:
            return
        cards = _parse_webflow_cards(html, _NWIOSK_JAHRESBERICHTE)
        logger.info("[strafvollzug] nwiosk jahresberichte: %d PDFs", len(cards))
        for card in cards:
            classified = classify_jahresbericht(card["title"])
            if classified is None:
                logger.warning("[strafvollzug] unrecognised Jahresbericht title: %s", card["title"])
                continue
            year, number = classified
            stub = {
                "pdf_url": card["pdf_url"],
                "url": _NWIOSK_JAHRESBERICHTE,
                "title": card["title"],
                "doc_number": number,
                "date": year,
                "language": "de",
                "doc_type": "bericht",
                "topics": ["NWI-OSK", number, "Tätigkeitsbericht"],
                "issuing_authority": "NWI-OSK",
                "sv_id": f"nwiosk_jb_{slugify(card['pdf_url'].rsplit('/', 1)[-1])[:60]}",
            }
            out = self.with_body(stub, number)
            if out:
                yield out
