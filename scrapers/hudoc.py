"""
HUDOC Scraper (ECHR Swiss Cases)
=================================

Scrapes European Court of Human Rights (ECHR/EGMR) judgments and decisions
concerning Switzerland from the HUDOC database.

Architecture:
- HUDOC has an undocumented JSON API at hudoc.echr.coe.int/app/query/results
- Filter: respondent=CHE, documentcollectionid2=JUDGMENTS or DECISIONS
- Returns JSON with metadata + document ID
- Full text available at hudoc.echr.coe.int/app/conversion/docx/html/body/{itemid}
- Also: PDF at hudoc.echr.coe.int/app/conversion/pdf/?library=ECHR&id={itemid}

Coverage: ~800-1,500 judgments + decisions against Switzerland
Rate limiting: 2.0 seconds (public ECHR server)

Note: HUDOC registers a listing row per language but only stores the text
of the authoritative one; the rest return HTTP 204 with an empty body.
`isplaceholder:False` filters them out server-side. See the header of the
generalized scraper below for the full set of HUDOC quirks.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Iterator

from bs4 import BeautifulSoup

from base_scraper import BaseScraper
from scrapers import pdf_ocr
from models import (
    Decision,
    detect_language,
    extract_citations,
    make_decision_id,
    parse_date,
)

logger = logging.getLogger(__name__)

# HUDOC API endpoint (reverse-engineered from browser network tab)
HUDOC_API = "https://hudoc.echr.coe.int/app/query/results"

# Query for Swiss cases — judgments and decisions
# NOTE: Do NOT put quotes around filter values — HUDOC API ignores quoted values
QUERY_TEMPLATE = (
    'contentsitename:ECHR AND '
    '(NOT (doctype:PR OR doctype:HFCOMOLD OR doctype:HECOMOLD)) AND '
    'isplaceholder:False AND '
    'respondent:{respondent} AND '
    'documentcollectionid2:{collection}'
)

COLLECTIONS = ["JUDGMENTS", "DECISIONS"]

# Full text URL — item_id goes as query parameter, NOT path segment
FULLTEXT_URL = "https://hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id={item_id}"
PDF_URL = "https://hudoc.echr.coe.int/app/conversion/pdf/?library=ECHR&id={item_id}"


class HUDOCScraper(BaseScraper):
    """Scraper for ECHR/HUDOC decisions concerning Switzerland."""

    REQUEST_DELAY = 2.0
    TIMEOUT = 60
    MAX_ERRORS = 30
    # HUDOC sometimes 404s on individual case documents (missing HTML even
    # though the metadata entry exists). Cache those for the weekly TTL.
    CACHE_NONE_AS_GAP = True

    @property
    def court_code(self) -> str:
        return "hudoc_ch"

    def discover_new(self, since_date=None) -> Iterator[dict]:
        """Query HUDOC API for Swiss cases."""
        # Establish session cookies by visiting main page first
        try:
            self.session.get("https://hudoc.echr.coe.int/eng", timeout=30)
        except Exception:
            pass

        found = 0
        seen_keys = set()  # Dedup by appno+collection (not just appno)

        for collection in COLLECTIONS:
            query = QUERY_TEMPLATE.format(respondent="CHE", collection=collection)
            start = 0
            page_size = 500

            while True:
                params = {
                    "query": query,
                    "select": (
                        "itemid,applicability,appno,article,conclusion,"
                        "docname,doctypebranch,ecli,importance,"
                        "judgementdate,kpdate,languageisocode,"
                        "originatingbody,respondent,separateopinion,"
                        "typedescription,violation,nonviolation"
                    ),
                    # MUST NOT be empty. HUDOC reorders results between
                    # pages when unsorted: a 3,531-row query returned only
                    # 2,411 distinct itemids (32% lost). Verified 2026-08-26.
                    "sort": "kpdate Ascending",
                    "start": start,
                    "length": page_size,
                }

                try:
                    self._rate_limit()
                    r = self.session.get(HUDOC_API, params=params, timeout=self.TIMEOUT)
                    r.raise_for_status()
                except Exception as e:
                    logger.error(f"[hudoc_ch] API query failed: {e}")
                    break

                try:
                    data = r.json()
                except json.JSONDecodeError:
                    logger.error(f"[hudoc_ch] Invalid JSON response")
                    break

                results = data.get("results", [])
                if not results:
                    break

                for item in results:
                    columns = item.get("columns", {})
                    item_id = columns.get("itemid", "")
                    if not item_id:
                        continue

                    appno = columns.get("appno", "")
                    docname = columns.get("docname", "")
                    judgement_date = columns.get("judgementdate", "")
                    lang_iso = columns.get("languageisocode", "")
                    doc_type = columns.get("typedescription", "")
                    ecli = columns.get("ecli", "")
                    article = columns.get("article", "")
                    conclusion = columns.get("conclusion", "")
                    violation = columns.get("violation", "")
                    nonviolation = columns.get("nonviolation", "")
                    importance = columns.get("importance", "")

                    # Build docket from application number
                    docket = appno.replace(";", "_") if appno else item_id
                    decision_id = make_decision_id("hudoc_ch", docket)

                    if self.state.is_known(decision_id):
                        continue

                    # Skip duplicate language versions (keep first encountered)
                    # Key on appno+collection so both judgments and decisions
                    # for the same application number are retained
                    dedup_key = f"{appno}|{collection}" if appno else None
                    if dedup_key and dedup_key in seen_keys:
                        continue
                    if dedup_key:
                        seen_keys.add(dedup_key)

                    # Parse date — HUDOC format: "19/02/2026 00:00:00"
                    decision_date = None
                    if judgement_date:
                        parts = judgement_date.split(" ")[0]  # "19/02/2026"
                        # Convert DD/MM/YYYY to DD.MM.YYYY for parse_date
                        decision_date = parse_date(parts.replace("/", "."))

                    if since_date and decision_date and decision_date < since_date:
                        continue

                    found += 1
                    yield {
                        "docket_number": docket,
                        "decision_date": decision_date,
                        "item_id": item_id,
                        "appno": appno,
                        "docname": docname,
                        "doc_type": doc_type,
                        "lang_iso": lang_iso,
                        "ecli": ecli,
                        "article": article,
                        "conclusion": conclusion,
                        "violation": violation,
                        "nonviolation": nonviolation,
                        "importance": importance,
                        "collection": collection,
                    }

                # Pagination
                total = data.get("resultcount", 0)
                start += page_size
                if start >= total:
                    break

                logger.info(
                    f"[hudoc_ch] {collection}: fetched {start}/{total} metadata entries"
                )

        logger.info(f"[hudoc_ch] Found {found} new decisions to fetch")

    def fetch_decision(self, stub: dict) -> Decision | None:
        """Fetch full text of an ECHR decision."""
        item_id = stub["item_id"]
        docket = stub["docket_number"]

        url = FULLTEXT_URL.format(item_id=item_id)
        try:
            response = self.get(url)
        except Exception as e:
            logger.warning(f"[hudoc_ch] Failed to fetch {docket}: {e}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        full_text = soup.get_text(separator="\n", strip=True)

        if not full_text or len(full_text) < 100:
            logger.debug(f"[hudoc_ch] {docket}: too short ({len(full_text)} chars)")
            return None

        full_text = self.clean_text(full_text)

        # Map HUDOC language codes to our codes
        # ECHR decisions are in ENG/FRE (official languages) — detect from text
        lang_map = {"FRE": "fr", "GER": "de", "ITA": "it"}
        lang = lang_map.get(stub.get("lang_iso", ""), None)
        if not lang:
            lang = detect_language(full_text)

        # Build title
        title = stub.get("docname", "")
        if not title:
            title = f"ECHR {stub['appno']}"

        # Build regeste from conclusion
        regeste = None
        conclusion = stub.get("conclusion", "")
        violation = stub.get("violation", "")
        nonviolation = stub.get("nonviolation", "")
        if conclusion or violation:
            parts = []
            if conclusion:
                parts.append(conclusion)
            if violation:
                parts.append(f"Violation: {violation}")
            if nonviolation:
                parts.append(f"No violation: {nonviolation}")
            regeste = "; ".join(parts)

        source_url = f"https://hudoc.echr.coe.int/eng?i={item_id}"

        return Decision(
            decision_id=make_decision_id("hudoc_ch", docket),
            court="hudoc_ch",
            canton="CH",
            docket_number=docket,
            decision_date=stub.get("decision_date"),
            language=lang,
            title=title,
            legal_area="EMRK / Menschenrechte",
            regeste=regeste,
            decision_type=stub.get("doc_type"),
            full_text=full_text,
            source_url=source_url,
            external_id=stub.get("ecli") or stub.get("item_id"),
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
            scraped_at=datetime.now(timezone.utc),
        )


# ============================================================
# Generalized HUDOC scraper — substantial ECtHR judgments
# ============================================================
#
# Scope (decided 2026-08-26): every ECtHR *judgment* of importance 1-3
# from all 46 Council of Europe respondent states, in its authoritative
# language. Excluded on purpose:
#
#   * importance 4 — low-importance / repetitive judgments. All 18,610
#     Committee judgments sit in this tier, which is why `ecthr_committee`
#     no longer receives rows.
#   * admissibility DECISIONS (~63k) — bulk without practitioner value.
#   * press releases and old European Commission documents.
#   * third-party translations — copyright is held by the translator, not
#     the Court, so they must not be redistributed. Restricting to ENG/FRE
#     is NOT sufficient on its own: four English rows in scope are
#     translations contributed by the European Roma Rights Centre (e.g.
#     001-77715, CASE OF WALLOVA AND WALLA v. THE CZECH REPUBLIC), where
#     the Court's own text is French. They are excluded by docname instead,
#     which is where HUDOC records the attribution.
#
# Measured against the live HUDOC API on 2026-08-26, the scope is
# 10,584 documents = 8,274 distinct judgments (Chamber 7,760 /
# Grand Chamber 514; importance 1: 1,103, 2: 1,088, 3: 6,083;
# 74 respondent states; 184 of them against Switzerland).
#
# Three HUDOC behaviours this scraper has to work around — each verified
# against the live API, each previously costing us coverage:
#
# 1. PLACEHOLDER ROWS. HUDOC registers a listing row per language for
#    every judgment but only holds the text of the authoritative one. The
#    other returns HTTP 204 with an empty body from both the HTML and the
#    PDF conversion endpoints. `isplaceholder` marks them and is
#    server-side filterable, so they never reach the fetch stage. Before
#    the filter this scraper burned ~1,622 dead fetches a night — 88% of
#    the French rows at importance 3 are placeholders, because that tier
#    is overwhelmingly English-authoritative.
#
# 2. UNSTABLE DEEP PAGING. With an empty `sort`, HUDOC reorders results
#    between pages: a 3,531-row query returned 2,411 distinct itemids,
#    silently losing 32%. `sort=kpdate Ascending` returns 3,530 of 3,531.
#    Every paged query here is sorted, and each shard verifies that it
#    collected `resultcount` distinct itemids before it is trusted.
#
# 3. A 10,000-RESULT PAGING CAP. The full scope is 10,585 rows, just over
#    the ceiling, so discovery is sharded by year. Both language versions
#    of a judgment always carry the same judgement date (verified: 0 of
#    8,274 groups span two dates), so a year shard never splits a case.
#
# Language: FR where the Court published a French text, EN otherwise
# (2,016 judgments are FR-only, 3,948 EN-only, 2,310 bilingual). English
# rows are the reason `Decision.language` accepts `en`; the quality gate
# restricts that code to ECtHR courts.
#
# Copyright: © ECHR-CEDH. Permitted reuse per
# https://www.echr.coe.int/copyright-and-disclaimer conditional on
# attribution (© ECHR-CEDH) and free-of-charge information/education use.
# A registry permission letter has been sent to secure bulk + commercial
# redistribution; until it returns, ingest runs but HF publication of
# ECtHR content is gated to a separate repository (not CC0).
# ============================================================

# doctypebranch (HUDOC) -> our court code
_BRANCH_TO_COURT = {
    "GRANDCHAMBER": "ecthr_grand_chamber",
    "CHAMBER": "ecthr_chamber",
    "COMMITTEE": "ecthr_committee",
}

# Official languages of the Court, in ingest preference order. FR first:
# the corpus is DE/FR/IT and a French text serves Swiss practitioners
# better than an English one. EN is used only where no French text exists.
_AUTHORITATIVE_LANGS = ("FRE", "ENG")

_LANG_ISO_TO_CODE = {"FRE": "fr", "ENG": "en", "GER": "de", "ITA": "it"}

# HUDOC records a translation's provenance in the docname, after a spaced
# dash: 'CASE OF ... v. GREECE - [English Translation] by European Roma
# Rights Centre'. Cut on ' - [' specifically and require the word
# "translation": four case names legitimately contain a bare ' - '
# (AFFAIRE FILIPPOS MAVROPOULOS - PAN. ZISIS O.E. c. GRECE), and bracketed
# editorial notes like '[Extracts]' or '[GC]' are the Court's own apparatus,
# not somebody else's copyright.
_THIRD_PARTY_TRANSLATION = re.compile(
    r"\s-\s\[[^\]]*\b(?:translation|traduction|übersetzung|traduzione)\b",
    re.IGNORECASE,
)

# HUDOC respondent state ISO-3 → our canton-like code. Swiss respondent
# keeps canton='CH' to preserve compatibility with the existing
# hudoc_ch filter and the `canton='CH'` federal-level search facet.
_RESPONDENT_TO_CANTON = {"CHE": "CH"}


def _extract_pdf_text(data: bytes) -> str:
    """Text layer of a PDF, via fitz (PyMuPDF) with a pdfplumber fallback."""
    try:
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        return "\n\n".join(page.get_text() for page in doc)
    except ImportError:
        pass
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    return ""

# HUDOC's importance scale is inverted from intuition: 1 = Key cases,
# 2 = high, 3 = medium, 4 = low/repetitive.
_IMPORTANCE_IN_SCOPE = ("1", "2", "3")

_FULL_QUERY = (
    "contentsitename:ECHR AND "
    "(NOT (doctype:PR OR doctype:HFCOMOLD OR doctype:HECOMOLD)) AND "
    "documentcollectionid2:JUDGMENTS AND "
    "isplaceholder:False AND "
    "(" + " OR ".join(f"importance:{i}" for i in _IMPORTANCE_IN_SCOPE) + ") AND "
    "(" + " OR ".join(f"languageisocode:{l}" for l in _AUTHORITATIVE_LANGS) + ")"
)

# First judgment in the corpus is Lawless v. Ireland (1960); start a year
# early so a re-dated entry can't fall off the front of the shard range.
_CORPUS_START_YEAR = 1959
_PAGE_SIZE = 500
# HUDOC refuses to page past this offset; shards must stay under it.
_HUDOC_PAGING_CAP = 10000

_SELECT_FIELDS = (
    "itemid,appno,article,conclusion,docname,doctypebranch,ecli,importance,"
    "judgementdate,kpdate,languageisocode,originatingbody,respondent,"
    "separateopinion,typedescription,violation,nonviolation"
)


# The citation builder renders at most three application numbers
# ("Nr. X", "Nr. X und Y", "Nr. X u.a."), so carrying more than three in
# the docket buys nothing — and carrying all of them is not an option:
# multi-applicant cases reach 3,795 characters (Turan and Others v.
# Turkey) and ``make_decision_id`` does not truncate.
_MAX_DOCKET_APPNOS = 3


# HUDOC's `article` metadata is a structured list: '8;8-1;41' means Article 8,
# its first paragraph, and Article 41; 'P1-1' means Article 1 of Protocol 1.
_ARTICLE_PLAIN = re.compile(r"^(\d+)(?:-\d+)*$")
_ARTICLE_PROTOCOL = re.compile(r"^P(\d+)-(\d+)(?:-\d+)*$", re.IGNORECASE)


def _ecthr_convention_keywords(article: str) -> str:
    """Convention article references in the three Swiss citation forms.

    The corpus is DE/FR/IT and the retrieval stack has no lexical bridge
    from German to a French or English judgment, so a practitioner typing
    "Art. 8 EMRK" would not reach any of these rows — and roughly half of
    them exist in English only. Restating HUDOC's own structured `article`
    field in the abbreviations Swiss practitioners actually type gives the
    FTS5 index the hook it otherwise lacks.

    This is a mechanical restatement of a metadata field, not a translation
    of the Court's prose: it never touches ``full_text``, and it is appended
    to the regeste inside brackets so it cannot be mistaken for the Court's
    own conclusion text when quoted.
    """
    plain: list[str] = []
    protocols: list[str] = []
    for token in (article or "").split(";"):
        token = token.strip()
        if not token:
            continue
        m = _ARTICLE_PLAIN.match(token)
        if m and m.group(1) not in plain:
            plain.append(m.group(1))
            continue
        m = _ARTICLE_PROTOCOL.match(token)
        if m:
            label = f"{m.group(2)}/{m.group(1)}"
            if label not in protocols:
                protocols.append(label)
    parts: list[str] = []
    if plain:
        parts.append(", ".join(f"Art. {n}" for n in plain) + " EMRK / CEDH / CEDU")
    for label in protocols:
        art, proto = label.split("/")
        parts.append(f"Art. {art} ZP {proto} EMRK / Prot. {proto} CEDH / CEDU")
    head = "EGMR / CourEDH / CorteEDU"
    return f"[{head}" + ("; " + "; ".join(parts) if parts else "") + "]"


def _hudoc_docket(appno: str, decision_date, item_id: str) -> str:
    """Docket for an ECtHR judgment: application number(s) + judgment date.

    The application number alone is not unique — 158 application numbers
    in scope carry more than one judgment (merits, then just satisfaction
    or revision, years apart), and keying on the application number alone
    silently dropped the later one through ``is_known``. The date is also
    how the Court's own citation format identifies a judgment
    ("no. 75805/16, § 42, 23 November 2021"). Verified collision-free
    across all 8,275 judgments in scope.

    Shape is ``<appno>[_<appno>[_<appno>]]_<yyyymmdd>``, keeping the
    established underscore-joined application-number convention that
    ``mcp_server._ecthr_app_numbers`` parses; that function strips the
    trailing date group before reading the application numbers.
    """
    parts = [p.strip() for p in (appno or "").split(";") if p.strip()]
    parts = parts[:_MAX_DOCKET_APPNOS] or [item_id]
    stamp = decision_date.strftime("%Y%m%d") if decision_date else "00000000"
    return "_".join(parts) + f"_{stamp}"


class HUDOCFullScraper(BaseScraper):
    """Full-corpus ECtHR judgment scraper (all respondent states)."""

    REQUEST_DELAY = 1.5
    TIMEOUT = 60
    MAX_ERRORS = 50
    # With isplaceholder:False every discovered row is known to have a
    # body, so a None return is now a real failure rather than expected
    # noise. Kept generously above zero for transient 5xx, but low enough
    # that a HUDOC outage still trips the circuit breaker.
    MAX_NONE_RETURNS = 500
    CACHE_NONE_AS_GAP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Year shards are 68 independent failure domains. A lost shard is a
        # silently missing year, so count them and report at the end.
        self.shard_failures = 0

    @property
    def court_code(self) -> str:
        # State-tracking key. Individual decisions carry chamber-specific
        # court codes from _BRANCH_TO_COURT.
        return "ecthr"

    # -- discovery ------------------------------------------------------

    def _fetch_shard(self, year: int) -> tuple[dict[str, dict], int]:
        """All listing rows for one calendar year, keyed by itemid.

        Returns (rows, resultcount). A short read means the shard is
        incomplete and the caller should retry it.
        """
        query = (
            f"{_FULL_QUERY} AND kpdate:["
            f"{year}-01-01T00:00:00.0Z TO {year}-12-31T23:59:59.0Z]"
        )
        rows: dict[str, dict] = {}
        start = 0
        total = 0

        while True:
            params = {
                "query": query,
                "select": _SELECT_FIELDS,
                # Never leave this empty — see note 2 in the header.
                "sort": "kpdate Ascending",
                "start": start,
                "length": _PAGE_SIZE,
            }
            self._rate_limit()
            r = self.session.get(HUDOC_API, params=params, timeout=self.TIMEOUT)
            r.raise_for_status()
            data = r.json()

            total = int(data.get("resultcount") or 0)
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                columns = item.get("columns", {})
                item_id = columns.get("itemid") or ""
                if item_id:
                    rows[item_id] = columns

            start += _PAGE_SIZE
            if start >= total:
                break
            if start >= _HUDOC_PAGING_CAP:
                # "search failed" is load-bearing: run_all_scrapers counts a
                # discovery failure by matching ERROR lines containing that
                # phrase, and a shard lost without it exits 0 and reports a
                # clean run.
                logger.error(
                    f"[ecthr] {year}: shard search failed — hit HUDOC's "
                    f"{_HUDOC_PAGING_CAP}-result paging cap with {total} rows; "
                    f"this year needs sub-sharding, coverage is incomplete"
                )
                break

        return rows, total

    def _discover_year(self, year: int) -> dict[str, dict]:
        """One year's rows, retried once if the listing came back short.

        Attempts are MERGED, not replaced. HUDOC orders kpdate ties
        non-deterministically across a 500-row page boundary, so attempt 2
        can drop a row attempt 1 had and vice versa; taking the union
        recovers both. Sets ``self.shard_failures`` for the caller.
        """
        merged: dict[str, dict] = {}
        total = 0
        for attempt in (1, 2):
            try:
                rows, total = self._fetch_shard(year)
            except Exception as e:
                # "search failed" is what run_all_scrapers' discovery-error
                # detector matches on; without it a lost year reads as a
                # clean run.
                logger.error(
                    f"[ecthr] {year}: shard search failed "
                    f"(attempt {attempt}): {e}"
                )
                continue
            merged.update(rows)
            if len(merged) >= total:
                return merged
            logger.warning(
                f"[ecthr] {year}: got {len(merged)} of {total} rows "
                f"(attempt {attempt})"
            )
        if total and len(merged) < total:
            self.shard_failures += 1
            logger.error(
                f"[ecthr] {year}: shard search failed to return "
                f"{total - len(merged)} of {total} row(s) after retry"
            )
        elif not merged:
            self.shard_failures += 1
        return merged

    @staticmethod
    def _group_judgments(rows: dict[str, dict]) -> list[dict]:
        """Collapse language versions into one stub per judgment.

        Grouped on (branch, ECLI): the ECLI identifies the judgment, the
        language versions of one judgment share it. Both language rows
        always carry the same judgement date, so a year shard never
        splits a group.
        """
        groups: dict[tuple[str, str], dict[str, dict]] = {}
        order: list[tuple[str, str]] = []
        for columns in rows.values():
            # Drop third-party translations before anything else, so one can
            # be neither the primary text nor the English fallback. The
            # ENG/FRE language filter does not catch these: four rows in
            # scope are English translations by the European Roma Rights
            # Centre of French-authoritative judgments.
            if _THIRD_PARTY_TRANSLATION.search(columns.get("docname") or ""):
                continue
            branch = (columns.get("doctypebranch") or "").upper()
            key = (branch, columns.get("ecli") or columns.get("itemid") or "")
            lang = (columns.get("languageisocode") or "").upper()
            if key not in groups:
                groups[key] = {}
                order.append(key)
            groups[key][lang] = columns

        stubs: list[dict] = []
        for key in order:
            by_lang = groups[key]
            langs = [l for l in _AUTHORITATIVE_LANGS if l in by_lang]
            if not langs:
                continue
            primary = by_lang[langs[0]]
            alt = by_lang[langs[1]] if len(langs) > 1 else None

            branch = key[0]
            court = _BRANCH_TO_COURT.get(branch, "ecthr")
            appno = primary.get("appno") or ""
            jd = primary.get("judgementdate") or ""
            decision_date = (
                parse_date(jd.split(" ")[0].replace("/", ".")) if jd else None
            )
            docket = _hudoc_docket(appno, decision_date, primary.get("itemid") or "")

            stubs.append({
                "court": court,
                "decision_id": make_decision_id(court, docket),
                "docket_number": docket,
                "decision_date": decision_date,
                "item_id": primary.get("itemid") or "",
                "lang_iso": langs[0],
                # Fallback text if the authoritative language 204s anyway.
                "alt_item_id": (alt or {}).get("itemid") or "",
                "alt_lang_iso": langs[1] if alt else "",
                "appno": appno,
                "docname": primary.get("docname") or "",
                "doc_type": primary.get("typedescription") or "",
                "branch": branch,
                "respondent": (primary.get("respondent") or "").upper(),
                "ecli": primary.get("ecli") or "",
                "article": primary.get("article") or "",
                "conclusion": primary.get("conclusion") or "",
                "violation": primary.get("violation") or "",
                "nonviolation": primary.get("nonviolation") or "",
                "importance": primary.get("importance") or "",
            })
        return stubs

    def discover_new(self, since_date=None) -> Iterator[dict]:
        try:
            self.session.get("https://hudoc.echr.coe.int/eng", timeout=30)
        except Exception:
            pass

        start_year = since_date.year if since_date else _CORPUS_START_YEAR
        end_year = date.today().year
        found = 0
        known = 0

        for year in range(start_year, end_year + 1):
            rows = self._discover_year(year)
            if not rows:
                continue
            stubs = self._group_judgments(rows)
            new_in_year = 0
            for stub in stubs:
                if since_date and stub["decision_date"] and stub["decision_date"] < since_date:
                    continue
                if self.state.is_known(stub["decision_id"]):
                    known += 1
                    continue
                found += 1
                new_in_year += 1
                yield stub
            logger.info(
                f"[ecthr] {year}: {len(rows)} rows → {len(stubs)} judgments, "
                f"{new_in_year} new (running total {found})"
            )

        if self.shard_failures:
            logger.error(
                f"[ecthr] discovery complete but {self.shard_failures} year "
                f"shard(s) had a search failed condition — coverage for those "
                f"years is incomplete; re-run to pick them up"
            )
        logger.info(
            f"[ecthr] discovery complete: {found} new judgments "
            f"({known} already known, {self.shard_failures} shard failures)"
        )

    # -- fetch ----------------------------------------------------------

    def _fetch_body(self, item_id: str) -> str | None:
        """Plain text of one HUDOC document, or None if it has no body.

        The docx-to-html converter is the fast path, and it can fail
        permanently for one document while the same judgment converts fine as
        PDF. 001-139179 (application 7974/11, judgment of 19.12.2013) answered
        500 there every night from at least 2026-09-11: discovery rediscovered
        it daily, the fetch returned None, the shard gained nothing for five
        days and the freshness check fired "no write for 5d". A 204 or an
        empty body still means a placeholder row and spends no PDF request.
        """
        if not item_id:
            return None
        try:
            response = self.get(FULLTEXT_URL.format(item_id=item_id))
        except Exception as e:
            logger.warning(f"[ecthr] fetch {item_id}: {e}")
            return self._fetch_pdf_body(item_id)
        # 204 = placeholder row. Should not happen now that discovery
        # filters isplaceholder, but a stale listing can still produce one.
        if response.status_code == 204 or not response.text.strip():
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        full_text = soup.get_text(separator="\n", strip=True)
        if not full_text or len(full_text) < 100:
            return self._fetch_pdf_body(item_id)
        return self.clean_text(full_text)

    def _fetch_pdf_body(self, item_id: str) -> str | None:
        """Text of the PDF rendering of one HUDOC document, or None.

        Same document, different converter: /app/conversion/pdf answered 200
        with 237 kB for 001-139179 on 2026-09-16, the day the html converter
        was still returning 500 for it.
        """
        try:
            response = self.get(PDF_URL.format(item_id=item_id))
        except Exception as e:
            logger.warning(f"[ecthr] pdf fetch {item_id}: {e}")
            return None
        data = getattr(response, "content", b"") or b""
        if not data:
            return None
        full_text = _extract_pdf_text(data)
        if len(full_text.strip()) < pdf_ocr.MIN_TEXT_CHARS:
            full_text = pdf_ocr.ocr_pdf_bytes(data)
        full_text = (full_text or "").strip()
        if len(full_text) < 100:
            logger.warning(
                f"[ecthr] pdf gave no usable text for {item_id} "
                f"({len(data)} bytes, OCR included)"
            )
            return None
        logger.info(
            f"[ecthr] {item_id}: html conversion failed, recovered "
            f"{len(full_text)} chars from the PDF endpoint"
        )
        return self.clean_text(full_text)

    def fetch_decision(self, stub: dict) -> Decision | None:
        court = stub["court"]
        lang_iso = (stub.get("lang_iso") or "").upper()

        full_text = self._fetch_body(stub["item_id"])
        item_id = stub["item_id"]
        if not full_text and stub.get("alt_item_id"):
            logger.info(
                f"[ecthr] {stub['docket_number']}: no {lang_iso} body, "
                f"falling back to {stub['alt_lang_iso']}"
            )
            full_text = self._fetch_body(stub["alt_item_id"])
            if full_text:
                item_id = stub["alt_item_id"]
                lang_iso = (stub.get("alt_lang_iso") or "").upper()
        if not full_text:
            return None

        lang = _LANG_ISO_TO_CODE.get(lang_iso) or detect_language(full_text)

        title = stub.get("docname") or f"ECtHR {stub.get('appno') or item_id}"

        regeste_parts: list[str] = []
        conclusion = stub.get("conclusion") or ""
        violation = stub.get("violation") or ""
        nonviolation = stub.get("nonviolation") or ""
        if conclusion:
            regeste_parts.append(conclusion)
        if violation:
            regeste_parts.append(f"Violation: {violation}")
        if nonviolation:
            regeste_parts.append(f"No violation: {nonviolation}")
        # Bracketed tail, always last: the German and Italian Convention
        # abbreviations are the only lexical bridge a DE/IT query has to a
        # French or English judgment. See _ecthr_convention_keywords.
        regeste_parts.append(_ecthr_convention_keywords(stub.get("article") or ""))
        regeste = "; ".join(regeste_parts) if regeste_parts else None

        lang_seg = "fre" if lang == "fr" else "eng"
        source_url = f"https://hudoc.echr.coe.int/{lang_seg}?i={item_id}"

        # One legal_area string for every ECtHR row regardless of the text's
        # language: varying it by language fragmented the facet three ways
        # for what is a single subject area.
        legal_area = "EMRK / CEDH / CEDU — Menschenrechte"

        canton = _RESPONDENT_TO_CANTON.get(stub.get("respondent") or "", "CE")

        # HUDOC importance 1 is "Key cases" — the Court's own selection of
        # the judgments that matter, and the direct analogue of a Swiss
        # decision marked for publication. Persisting it here is what makes
        # the scope axis survive into serving: without it a Key case is
        # indistinguishable from a routine level-3 Chamber judgment, and it
        # is already exposed as the search_decisions marked_for_publication
        # filter.
        importance = (stub.get("importance") or "").strip()

        return Decision(
            marked_for_publication=(importance == "1") or None,
            decision_id=stub["decision_id"],
            court=court,
            canton=canton,
            docket_number=stub["docket_number"],
            decision_date=stub.get("decision_date"),
            language=lang,
            title=title,
            legal_area=legal_area,
            regeste=regeste,
            decision_type=stub.get("doc_type"),
            full_text=full_text,
            source_url=source_url,
            external_id=stub.get("ecli") or item_id,
            cited_decisions=extract_citations(full_text) if len(full_text) > 200 else [],
            scraped_at=datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape HUDOC")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use full-corpus scraper (all respondent states, importance 1-3 "
        "judgments, authoritative language). Default: Swiss-respondent only.",
    )
    parser.add_argument("--since", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--max", type=int, default=5, help="Max decisions")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    since = date.fromisoformat(args.since) if args.since else None
    scraper = HUDOCFullScraper() if args.full else HUDOCScraper()
    decisions = scraper.run(since_date=since, max_decisions=args.max)
    scraper.mark_run_complete(decisions)
    for d in decisions:
        print(
            f"  {d.decision_id}  {d.court}  {d.canton}  {d.decision_date}  "
            f"{len(d.full_text)} chars  {d.title[:60]}"
        )
    label = "ECtHR (full)" if args.full else "HUDOC Swiss"
    print(f"\nScraped {len(decisions)} {label} decisions")
