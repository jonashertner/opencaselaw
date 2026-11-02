#!/usr/bin/env python3
"""
LexFind cantonal scraper — enumerates every cantonal law on lexfind.ch and
keeps a per-canton JSONL with parsed article text current.

Run monthly for the cantons the mirror takes from LexFind: JU, SZ and VD have
no portal scraper, and ZH's portal publishes only part of the collection (see
LEXFIND_COMPLEMENT_CANTONS in search_stack/build_cantonal_laws_db.py). Each run
downloads only laws whose version date changed and reuses the rest, so the
monthly load on LexFind stays small.

A shard is written to {canton}.jsonl.part and promoted with an atomic rename.
A run whose enumeration comes back short leaves the shard as it was and parks
the output as {canton}.jsonl.failed. A law whose download fails keeps its
previous row, and a law the enumeration did not reach stays in the shard:
LexFind flags a repealed act inactive, so absence from a search is no evidence
that it went away.

Enumeration strategy: LexFind has no public listing endpoint, but the
fulltext-search API with `search_in_content=True` and a common-word seed
(e.g. 'der' / 'le' / 'il') matches ~every law in a canton's collection.
Unioning 2-3 seeds per language reaches ~100% coverage.

Output: output/lexfind_cantonal/{canton}.jsonl
  one line per law:
  {
    "lexfind_id": int,
    "canton": "ZH",
    "sr_number": "554.5",
    "title": "Hundegesetz",
    "language": "de",
    "is_active": true,
    "category": "Gesetz",
    "original_url": "...",
    "version_active_since": "...",
    "text_source": "lexfind_pdf",
    "full_text": "...",
    "articles": [{"article_num": "1", "heading": "...", "text": "..."}, ...],
    "fetched_at": "2026-04-11T07:00:00+00:00",  # when THIS text was downloaded
    "checked_at": "2026-11-02T07:00:00+00:00"   # when LexFind last showed this
                                                # version as the current one
  }

Usage:
    python -m scrapers.lexfind_cantonal                # all 26 cantons
    python -m scrapers.lexfind_cantonal --canton ZH    # single canton
    python -m scrapers.lexfind_cantonal --max 50       # cap per canton (pilot)
    python -m scrapers.lexfind_cantonal --delay 0.5    # inter-request delay
    python -m scrapers.lexfind_cantonal --refetch-all  # ignore the version check
    python -m scrapers.lexfind_cantonal --check-report DIR   # exit 1 if the last
                                                             # run left a canton
                                                             # un-refreshed, 2 if
                                                             # it did not finish
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from scrape_cantonal_laws import MIN_KEEP_RATIO, check_report, write_report
from scrapers.cantonal_laws import mount_retries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lexfind_cantonal")

OUTPUT_DIR = Path(os.environ.get("LEXFIND_CANTONAL_OUTPUT", "output/lexfind_cantonal"))

LEXFIND_BASE = "https://www.lexfind.ch/api/fe"

# Canonical LexFind entity IDs (confirmed from GET /api/fe/de/entities)
ENTITY_IDS = {
    "AG": 1,  "AI": 2,  "AR": 3,  "BE": 4,  "BL": 5,  "BS": 6,
    "FR": 7,  "GE": 8,  "GL": 9,  "GR": 10, "JU": 11, "LU": 12,
    "NE": 13, "NW": 14, "OW": 15, "SG": 16, "SH": 17, "SO": 18,
    "SZ": 19, "TG": 20, "TI": 21, "UR": 22, "VD": 23, "VS": 24,
    "ZG": 25, "ZH": 26,
    # Entity 28 is the INTERCANTONAL law collection (Konkordate /
    # interkantonale Vereinbarungen, e.g. the HarmoS-Konkordat). Keyed
    # "INTLEX" to match the serving identifier for entity 28 in
    # mcp_server.py (CANTON_ENTITY map); a mismatched key would leave the
    # scraped rows unreachable via the canton fallback.
    # Without it cantonal_laws.db carries no intercantonal law at all.
    # Entity 27 (CH/Bund) stays out deliberately: federal law comes from
    # Fedlex directly (scrapers/fedlex.py), a better source.
    "INTLEX": 28,
}

# Primary publication language per canton (for enumeration + text fetch).
# Bilingual cantons (BE, FR, VS, GR) publish in multiple languages; we take
# the primary for completeness and can optionally add secondary languages.
CANTON_LANG = {
    "AG": "de", "AI": "de", "AR": "de", "BE": "de", "BL": "de", "BS": "de",
    "FR": "fr", "GE": "fr", "GL": "de", "GR": "de", "JU": "fr", "LU": "de",
    "NE": "fr", "NW": "de", "OW": "de", "SG": "de", "SH": "de", "SO": "de",
    "SZ": "de", "TG": "de", "TI": "it", "UR": "de", "VD": "fr", "VS": "fr",
    "ZG": "de", "ZH": "de",
    "INTLEX": "de",
}

# Secondary language(s) for multilingual entities (optional enumeration pass).
# Values may be a single language or a list. INTLEX (intercantonal) needs BOTH
# fr and it: concordats romands are published French-only and TI/GR
# instruments Italian-only — a primary-language-only pass silently misses
# them entirely.
SECONDARY_LANG: dict[str, str | list[str]] = {
    "BE": "fr", "FR": "de", "VS": "de", "GR": "it",
    "INTLEX": ["fr", "it"],
}

# Broad enumeration seeds — common stopwords that match nearly every law
# when search_in_content=True. Order: most-frequent first so the first seed
# captures most of the corpus.
SEEDS = {
    "de": ["der", "und", "Art"],
    "fr": ["de", "et", "art"],
    "it": ["di", "la", "art"],
}

DEFAULT_DELAY = 0.35
REPORT_NAME = "_harvest_report.json"
# Above this share of failed downloads the run is reported as not refreshed,
# though what did arrive is kept: a law that always fails (LexFind serves a
# few as HTML, not PDF) must not turn the unit red every month, a LexFind
# outage must.
MAX_DOWNLOAD_FAILURES = 0.2


def _clean_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


@dataclass
class Law:
    lexfind_id: int
    canton: str
    sr_number: str
    title: str
    language: str
    is_active: bool
    category: str
    original_url: str | None
    version_active_since: str | None


class LexFindClient:
    def __init__(self, delay: float = DEFAULT_DELAY) -> None:
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "OpenCaseLaw/1.0 (cantonal scraper; +https://opencaselaw.ch)"
        )
        self.session.headers["Accept"] = "application/json"
        mount_retries(self.session)

    def _sleep(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def enumerate_canton(self, canton: str, language: str) -> dict[int, Law]:
        """Discover every law in a canton's collection for `language`.

        Returns a dict keyed by lexfind_id — de-duplicates across seeds.
        """
        entity_id = ENTITY_IDS[canton]
        seeds = SEEDS[language]
        laws: dict[int, Law] = {}

        for seed in seeds:
            before = len(laws)
            self._enumerate_one_seed(seed, entity_id, canton, language, laws)
            new = len(laws) - before
            log.info(
                "  [%s/%s] seed=%r → %d new (running total %d)",
                canton, language, seed, new, len(laws),
            )
            # Early stop: if a second seed adds ≤ 1% new, we've converged
            if len(laws) >= 50 and new <= max(5, len(laws) // 100) and seed != seeds[0]:
                log.info("  [%s/%s] converged, skipping further seeds", canton, language)
                break

        return laws

    def _enumerate_one_seed(
        self, seed: str, entity_id: int, canton: str,
        language: str, out: dict[int, Law],
    ) -> None:
        body = {
            "search_text": seed,
            "active_only": False,
            "search_in_systematic_number": False,
            "search_in_title": True,
            "search_in_keywords": True,
            "search_in_content": True,
            "use_global_systematics": True,
            "entity_filter": [entity_id],
            "systematic_filter": [],
            "category_filter": [],
            "direct_search": False,
        }
        try:
            r = self.session.post(
                f"{LEXFIND_BASE}/{language}/fulltext-search",
                json=body, timeout=30,
            )
            r.raise_for_status()
        except Exception as e:
            log.warning("  [%s/%s] seed=%r POST failed: %s", canton, language, seed, e)
            return

        d = r.json()
        sid = d.get("id")
        ssid = d.get("session_id", "")
        if not sid:
            return

        page = 1
        total = None
        while True:
            self._sleep()
            try:
                r2 = self.session.get(
                    f"{LEXFIND_BASE}/{language}/fulltext-search/{sid}"
                    f"?session_id={ssid}&page_no={page}&results_per_page=60",
                    timeout=30,
                )
                r2.raise_for_status()
            except Exception as e:
                log.warning(
                    "  [%s/%s] seed=%r page=%d GET failed: %s",
                    canton, language, seed, page, e,
                )
                return

            d2 = r2.json()
            tols = d2.get("texts_of_law_with_matches", [])
            if not tols:
                return

            for tol in tols:
                tid = tol.get("id")
                if not tid or tid in out:
                    continue
                entity = tol.get("entity") or {}
                sr = tol.get("systematic_number") or ""
                # Get title from first match block
                matches = tol.get("matches") or []
                title = ""
                category = ""
                version_active_since = None
                is_active = tol.get("is_active", True)
                if matches:
                    m0 = matches[0]
                    title = _clean_html(m0.get("title_hl") or m0.get("title") or "")
                    category = (m0.get("category") or {}).get("name", "")
                    version_active_since = m0.get("version_active_since")
                    is_active = is_active and m0.get("is_active", True)
                # original URL
                original_url = None
                for dta in tol.get("dta_urls") or []:
                    if dta.get("language") == language and dta.get("original_url"):
                        original_url = dta["original_url"]
                        break
                if not original_url:
                    for dta in tol.get("dta_urls") or []:
                        if dta.get("original_url"):
                            original_url = dta["original_url"]
                            break

                out[tid] = Law(
                    lexfind_id=tid,
                    canton=canton,
                    sr_number=sr,
                    title=title,
                    language=language,
                    is_active=bool(is_active),
                    category=category,
                    original_url=original_url,
                    version_active_since=version_active_since,
                )

            if total is None:
                total = sum(r.get("number_of_results", 0) for r in d2.get("results", []))
            num_pages = max(1, (total + 59) // 60)
            if page >= num_pages:
                return
            page += 1

    def fetch_law_text(self, lexfind_id: int, language: str) -> dict | None:
        """Download law PDF and extract structured text + article list."""
        try:
            r = self.session.get(
                f"https://www.lexfind.ch/tol/{lexfind_id}/{language}",
                timeout=30, allow_redirects=True,
            )
        except Exception as e:
            log.warning("  PDF fetch failed id=%s: %s", lexfind_id, e)
            return None
        if r.status_code != 200 or not r.content:
            return None
        ctype = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ctype and not r.content.startswith(b"%PDF"):
            return None

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=r.content, filetype="pdf")
            pages = [page.get_text() for page in doc]
            doc.close()
            full_text = "\n".join(pages)
        except Exception as e:
            log.warning("  PDF parse failed id=%s: %s", lexfind_id, e)
            return None

        articles = _segment_articles(full_text)
        return {
            "full_text": full_text,
            "articles": articles,
            "text_source": "lexfind_pdf",
        }


def _segment_articles(text: str) -> list[dict]:
    """Split PDF text at Art.-N / § N boundaries. Copy of mcp_server logic."""
    if not text or not text.strip():
        return []
    normalized = text.replace("\u00a0", " ").replace("\u2011", "-")
    pattern = re.compile(
        r"^\s*"
        r"(?:Art\.|§)"
        r"\s*"
        r"(\d+[a-z]?(?:bis|ter|quater|quinquies|sexies|septies)?)"
        r"\b\.?\s*",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(normalized))
    if not matches:
        return []

    def is_heading(line: str) -> bool:
        if not line or len(line) > 120 or line.endswith((".", ":", ";", ",")):
            return False
        first = line[0]
        return first.isupper() or first in "ÄÖÜÉÈÀ"

    articles: list[dict] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        body = normalized[start:end].strip()
        num = m.group(1)
        heading = None
        lines = [line.strip() for line in body.split("\n") if line.strip()]
        if lines and is_heading(lines[0]):
            heading = lines[0]
            body = "\n".join(lines[1:]).strip()
        else:
            body = "\n".join(lines).strip()
        if not heading and i > 0:
            prev = normalized[matches[i - 1].end(): m.start()].strip()
            prev_lines = [line.strip() for line in prev.split("\n") if line.strip()]
            if prev_lines and is_heading(prev_lines[-1]):
                heading = prev_lines[-1]
                if articles and articles[-1]["text"].rstrip().endswith(heading):
                    articles[-1]["text"] = (
                        articles[-1]["text"].rstrip()[: -len(heading)].rstrip()
                    )
        if body or heading:
            articles.append({"article_num": num, "heading": heading, "text": body})
    return articles


def _load_shard(path: Path) -> dict[tuple[int, str], dict]:
    """The shard on disk, keyed like the enumeration.

    Rows from before `fetched_at` existed take the file's date, which is when
    they were written.
    """
    rows: dict[tuple[int, str], dict] = {}
    if not path.exists():
        return rows
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            row.setdefault("fetched_at", written)
            rows[(row["lexfind_id"], row["language"])] = row
    return rows


def _law_row(law: Law, text: dict, fetched_at: str, checked_at: str) -> dict:
    return {
        "lexfind_id": law.lexfind_id,
        "canton": law.canton,
        "sr_number": law.sr_number,
        "title": law.title,
        "language": law.language,
        "is_active": law.is_active,
        "category": law.category,
        "original_url": law.original_url,
        "version_active_since": law.version_active_since,
        "text_source": text["text_source"],
        "full_text": text["full_text"],
        "articles": text["articles"],
        "fetched_at": fetched_at,
        "checked_at": checked_at,
    }


def scrape_canton(
    client: LexFindClient,
    canton: str,
    max_laws: int | None,
    include_secondary: bool,
    output_dir: Path | None = None,
    refetch_all: bool = False,
) -> dict:
    """Refresh one canton's shard. Returns its report entry."""
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{canton}.jsonl"
    part_path = output_dir / f"{canton}.jsonl.part"
    previous_rows = _load_shard(final_path)
    previous = len(previous_rows) or None
    primary = CANTON_LANG[canton]
    languages: list[str] = [primary]
    if include_secondary and canton in SECONDARY_LANG:
        sec = SECONDARY_LANG[canton]
        languages.extend([sec] if isinstance(sec, str) else sec)

    start = time.time()
    log.info("=== [%s] enumerating (languages=%s) ===", canton, ",".join(languages))
    all_laws: dict[tuple[int, str], Law] = {}
    for lang in languages:
        laws = client.enumerate_canton(canton, lang)
        for lid, law in laws.items():
            all_laws[(lid, lang)] = law
    enumerated = len(all_laws)

    if max_laws is not None:
        pairs = list(all_laws.items())[:max_laws]
        all_laws = dict(pairs)

    log.info("[%s] %d laws enumerated, %d in the shard", canton, enumerated, len(previous_rows))

    fetched = reused = kept_stale = failed = 0
    with part_path.open("w", encoding="utf-8") as f:
        for i, (key, law) in enumerate(all_laws.items(), 1):
            prev = previous_rows.get(key)
            unchanged = (prev is not None and prev.get("full_text")
                         and prev.get("version_active_since") == law.version_active_since)
            now = datetime.now(timezone.utc).isoformat()
            if unchanged and not refetch_all:
                row = _law_row(law, prev, prev["fetched_at"], now)
                reused += 1
            else:
                client._sleep()
                text = client.fetch_law_text(law.lexfind_id, law.language)
                if text:
                    row = _law_row(law, text, now, now)
                    fetched += 1
                elif prev is not None:
                    row = prev
                    kept_stale += 1
                else:
                    failed += 1
                    continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if i % 200 == 0:
                log.info("  [%s] %d/%d (%d downloaded, %d failed)",
                         canton, i, len(all_laws), fetched, kept_stale + failed)
        carried = 0
        if max_laws is None:
            for key, row in previous_rows.items():
                if key not in all_laws:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    carried += 1

    # Promotion. Failed downloads cost nothing here, they kept their old row;
    # only an enumeration that came back short says this run saw too little.
    floor = int(previous * MIN_KEEP_RATIO) if previous else 1
    promoted = kept_previous = False
    errors = kept_stale + failed
    if max_laws is not None:
        parked = output_dir / f"{canton}.jsonl.pilot"
        os.replace(part_path, parked)
        log.info("[%s] pilot run parked at %s; shard untouched", canton, parked)
    elif enumerated >= floor:
        os.replace(part_path, final_path)
        promoted = True
    else:
        parked = output_dir / f"{canton}.jsonl.failed"
        os.replace(part_path, parked)
        kept_previous = previous is not None
        log.error("[%s] NOT promoted: %d enumerated, floor %d; output parked at %s",
                  canton, enumerated, floor, parked)

    tried = fetched + errors
    ok = (promoted or max_laws is not None) and errors <= max(5, tried * MAX_DOWNLOAD_FAILURES)
    log.info("[%s] DONE — %d downloaded, %d unchanged, %d not enumerated, "
             "%d failed with an old row kept, %d failed with no row",
             canton, fetched, reused, carried, kept_stale, failed)
    return {
        "canton": canton,
        "ok": ok,
        "promoted": promoted,
        "kept_previous": kept_previous,
        "enumerated": enumerated,
        "fetched": fetched,
        "reused": reused,
        "carried": carried,
        "kept_stale": kept_stale,
        "failed": failed,
        "errors": errors,
        "previous": previous,
        "duration": time.time() - start,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canton", action="append", help="Limit to canton(s). Repeat.")
    parser.add_argument("--max", type=int, default=None, help="Cap laws per canton (pilot)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="Inter-request delay (seconds)")
    parser.add_argument("--include-secondary", action="store_true",
                        help="Also enumerate bilingual cantons in their secondary language")
    parser.add_argument("--refetch-all", action="store_true",
                        help="Download every law, not only those whose version changed")
    parser.add_argument("--check-report", metavar="DIR",
                        help="Do not scrape; exit 1 if the last run in DIR left a "
                             "canton un-refreshed, 2 if there is no fresh report")
    args = parser.parse_args()

    if args.check_report:
        sys.exit(check_report(Path(args.check_report), REPORT_NAME))

    client = LexFindClient(delay=args.delay)
    targets: list[str] = args.canton or sorted(ENTITY_IDS.keys())

    results: list[dict] = []
    for canton in targets:
        canton = canton.upper()
        if canton not in ENTITY_IDS:
            log.warning("Unknown canton %s, skipping", canton)
            continue
        try:
            results.append(scrape_canton(
                client, canton, max_laws=args.max,
                include_secondary=args.include_secondary,
                refetch_all=args.refetch_all,
            ))
        except Exception as e:
            log.error("[%s] CRASHED: %s", canton, e, exc_info=True)
            results.append({"canton": canton, "ok": False, "errors": 1,
                            "kept_previous": (OUTPUT_DIR / f"{canton}.jsonl").exists()})

    if args.max is None:
        write_report(OUTPUT_DIR, results, REPORT_NAME)
    log.info("============== ALL DONE — %d downloaded ==============",
             sum(r.get("fetched", 0) for r in results))


if __name__ == "__main__":
    main()
