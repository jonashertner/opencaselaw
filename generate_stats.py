#!/usr/bin/env python3
"""
generate_stats.py — Generate statistics JSON from FTS5 database
=================================================================

Queries the SQLite FTS5 database and outputs docs/stats.json
for the public dashboard.

Usage:
    python3 generate_stats.py
    python3 generate_stats.py --db output/decisions.db --output docs/stats.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("generate_stats")

# Canton names for display
CANTON_NAMES = {
    "CH": "Schweiz / Suisse",
    "AG": "Aargau", "AI": "Appenzell Innerrhoden", "AR": "Appenzell Ausserrhoden",
    "BE": "Bern", "BL": "Basel-Landschaft", "BS": "Basel-Stadt",
    "FR": "Fribourg", "GE": "Genève", "GL": "Glarus", "GR": "Graubünden",
    "JU": "Jura", "LU": "Luzern", "NE": "Neuchâtel", "NW": "Nidwalden",
    "OW": "Obwalden", "SG": "St. Gallen", "SH": "Schaffhausen",
    "SO": "Solothurn", "SZ": "Schwyz", "TG": "Thurgau", "TI": "Ticino",
    "UR": "Uri", "VD": "Vaud", "VS": "Valais", "ZG": "Zug", "ZH": "Zürich",
}


# Max drift (in records) between the manifest's build corpus and the live corpus
# before the unique count is withheld as stale. Every swap (nightly + the ~daily
# incremental + poller quick_publish) re-stamps user_version, so an exact-
# generation match would go stale after each daytime swap and hide the number
# ~half the day. The manifest's *reduction* is stable across a small daytime drift
# (a handful to a few hundred new rows), and the count is an estimate with a band,
# so we tolerate drift and fail closed only when the corpus has genuinely moved
# past the manifest (e.g. the nightly rebuild has been broken for days).
_MANIFEST_DRIFT_TOLERANCE = 2500

# Canonical location of the representation manifest sidecar. decisions.db is a
# symlink onto the /mnt data volume, but the manifest is a real file in the repo
# output/ dir (they are NOT co-located), so a caller that passes the RESOLVED
# /mnt db path (the early-stats-push does) must still find the manifest here.
_CANONICAL_OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _find_manifest(db_path) -> Path | None:
    """Locate representation_manifest.db robustly: first co-located with db_path
    (the repo-relative case), then the canonical repo output/ dir (for callers
    that pass the resolved /mnt decisions.db path)."""
    for cand in (Path(db_path).with_name("representation_manifest.db"),
                 _CANONICAL_OUTPUT_DIR / "representation_manifest.db"):
        if cand.exists():
            return cand
    return None


def _representation_dual_count(db_path: Path, conn) -> dict:
    """Additive cross-identifier dual-count from the representation manifest
    sidecar (output/representation_manifest.db), or {} if absent.

    Emits `source_representations` (== total, the record count) plus an ESTIMATED
    unique-decision count that collapses cross-identifier duplicates (GE/VD/SH +
    ch_vb/nw/edoeb/ur). NEVER replaces `total`: make verify and the 950k health
    floors gate on the record count, which is unchanged. `unique` is derived from
    the LIVE total minus the manifest's stable duplicate count, so it tracks corpus
    growth (a fresh decision is a singleton until its twin is published). If the
    manifest's build corpus has drifted too far from the live corpus, the count is
    marked stale and withheld (a stale count is worse than none)."""
    manifest = _find_manifest(db_path)
    if manifest is None:
        return {}
    try:
        m = sqlite3.connect(f"file:{manifest}?mode=ro&immutable=1", uri=True)
        try:
            meta = dict(m.execute("SELECT key, value FROM manifest_meta"))
        finally:
            m.close()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("representation manifest unreadable, skipping dual-count: %s", e)
        return {}
    live_total = stats_total(conn)
    out: dict = {
        "source_representations": live_total,
        "representation_method_version": meta.get("algo_version"),
    }
    try:
        mani_total = int(meta["source_total_rows"])
        dup = int(meta["duplicate_representations"])
        band = int(meta["band_unlinked_date_disagree"])
    except (KeyError, ValueError) as e:  # pragma: no cover - defensive
        logger.warning("representation manifest meta incomplete: %s", e)
        out["unique_decisions_status"] = "stale"
        return out
    if abs(live_total - mani_total) > _MANIFEST_DRIFT_TOLERANCE:
        out["unique_decisions_status"] = "stale"  # corpus moved past the manifest
        return out
    # reduction is stable; anchor on the live total so unique + duplicates == total.
    out.update({
        "unique_decisions": live_total - dup,
        "unique_decisions_lower_bound": live_total - dup - band,
        "duplicate_representations": dup,
        "unique_decisions_status": "current",
    })
    by_court = _duplicates_by_court(manifest, conn)
    if by_court:
        out["duplicates_by_court"] = by_court
    return out


def _duplicates_by_court(manifest: Path, conn) -> dict:
    """Duplicate representations per court: manifest member rows that are not their
    own canonical, joined onto decisions.decision_id for the court code. The
    manifest carries only the canton, and ch_vb / edoeb / nw / ur share codes, so
    the join is the only exact way to attribute them. Read-only: the manifest is
    ATTACHed immutable onto the caller's connection and detached again. {} on any
    failure — the stats page then falls back to its approximate third tier."""
    try:
        conn.execute("ATTACH DATABASE ? AS repmani",
                     (f"file:{manifest}?mode=ro&immutable=1",))
    except sqlite3.Error as e:  # pragma: no cover - defensive
        logger.warning("representation manifest could not be attached: %s", e)
        return {}
    try:
        rows = conn.execute(
            "SELECT d.court, COUNT(*) FROM repmani.decision_representations r "
            "JOIN decisions d ON d.decision_id = r.member_decision_id "
            "WHERE r.member_decision_id != r.canonical_decision_id "
            "GROUP BY d.court"
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows if r[0]}
    except sqlite3.Error as e:  # pragma: no cover - defensive
        logger.warning("duplicates_by_court skipped: %s", e)
        return {}
    finally:
        try:
            conn.execute("DETACH DATABASE repmani")
        except sqlite3.Error:
            pass


def _practice_coverage(repo_dir: Path) -> dict | None:
    """Administrative practice (Verwaltungspraxis) counts from practice.db —
    BSV Wegleitungen, FINMA Rundschreiben, SECO ArG commentary and the rest.
    Same guard as materialien: missing file, missing table or any sqlite error
    returns None and the stats page hides the card. Path: output/practice.db
    (a symlink onto the data volume in production), else the server's
    SWISS_CASELAW_PRACTICE_DB override."""
    path = repo_dir / "output" / "practice.db"
    if not path.exists():
        env = os.environ.get("SWISS_CASELAW_PRACTICE_DB")
        if not env or not Path(env).exists():
            return None
        path = Path(env)
    try:
        p = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        tables = {r[0] for r in p.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "practice" not in tables:
            return None
        cols = {r[1] for r in p.execute("PRAGMA table_info(practice)").fetchall()}
        total = int(p.execute("SELECT COUNT(*) FROM practice").fetchone()[0])
        if total <= 0:
            return None
        def _group(col):
            if col not in cols:
                return {}
            return {str(r[0]): int(r[1]) for r in p.execute(
                f"SELECT {col}, COUNT(*) FROM practice GROUP BY {col} ORDER BY 2 DESC"
            ).fetchall() if r[0]}
        return {
            "total_documents": total,
            "sources": len(_group("source")),
            "by_source": _group("source"),
            "by_authority": _group("issuing_authority"),
            "by_language": _group("language"),
        }
    except sqlite3.Error:
        return None
    finally:
        p.close()


def stats_total(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]


def generate_stats(db_path: Path) -> dict:
    """Query the FTS5 database and return comprehensive statistics."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    stats: dict = {}
    now_utc = datetime.now(timezone.utc)
    current_year = now_utc.year
    today_iso = now_utc.date().isoformat()

    # Total decisions (record count — load-bearing for make verify + health floors)
    stats["total"] = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    # Additive dual-count (never replaces `total`); {} when the sidecar is absent.
    stats.update(_representation_dual_count(db_path, conn))

    # By court (with date ranges and languages)
    courts = conn.execute("""
        SELECT
            court,
            canton,
            COUNT(*) as count,
            MIN(CASE WHEN decision_date IS NOT NULL AND decision_date != 'None'
                     AND decision_date > '1800-01-01' AND decision_date <= ? THEN decision_date END) as earliest,
            MAX(CASE WHEN decision_date IS NOT NULL AND decision_date != 'None'
                     AND decision_date > '1800-01-01' AND decision_date <= ? THEN decision_date END) as latest,
            MAX(scraped_at) as last_scraped,
            GROUP_CONCAT(DISTINCT language) as languages
        FROM decisions
        GROUP BY court, canton
        ORDER BY count DESC
    """, (today_iso, today_iso)).fetchall()
    stats["by_court"] = [
        {
            "court": r["court"],
            "canton": r["canton"],
            "count": r["count"],
            "earliest": r["earliest"],
            "latest": r["latest"],
            "last_scraped": r["last_scraped"],
            "languages": r["languages"].split(",") if r["languages"] else [],
        }
        for r in courts
    ]

    # By canton (exclude CH — federal courts are not a canton)
    cantons = conn.execute("""
        SELECT canton, COUNT(*) as count
        FROM decisions
        WHERE canton != 'CH'
        GROUP BY canton
        ORDER BY count DESC
    """).fetchall()
    stats["by_canton"] = [
        {
            "canton": r["canton"],
            "name": CANTON_NAMES.get(r["canton"], r["canton"]),
            "count": r["count"],
        }
        for r in cantons
    ]

    # By language
    languages = conn.execute("""
        SELECT language, COUNT(*) as count
        FROM decisions
        GROUP BY language
        ORDER BY count DESC
    """).fetchall()
    stats["by_language"] = {r["language"]: r["count"] for r in languages}

    # By year (all years, filter invalid dates)
    years = conn.execute("""
        SELECT substr(decision_date, 1, 4) as year, COUNT(*) as count
        FROM decisions
        WHERE decision_date IS NOT NULL
          AND decision_date != 'None'
          AND length(decision_date) >= 4
          AND substr(decision_date, 1, 4) BETWEEN '1800' AND '2100'
        GROUP BY year
        ORDER BY year ASC
    """).fetchall()
    stats["by_year"] = {r["year"]: r["count"] for r in years}

    # Recent daily additions (last 30 days by decision_date)
    recent = conn.execute("""
        SELECT decision_date as day, COUNT(*) as count
        FROM decisions
        WHERE decision_date >= date('now', '-30 days')
          AND decision_date <= date('now')
        GROUP BY day
        ORDER BY day ASC
    """).fetchall()
    stats["recent_daily"] = {r["day"]: r["count"] for r in recent}

    # Date range (filter out invalid dates)
    date_range = conn.execute("""
        SELECT MIN(decision_date) as earliest, MAX(decision_date) as latest
        FROM decisions
        WHERE decision_date IS NOT NULL
          AND decision_date != 'None'
          AND decision_date > '1800-01-01'
          AND decision_date <= ?
    """, (today_iso,)).fetchone()
    stats["date_range"] = {
        "earliest": date_range["earliest"],
        "latest": date_range["latest"],
    }

    # Counts. Count distinct courts (not court×canton pairs). Some federal-
    # spanning scrapers produce multiple by_court entries with the same
    # `court` key but different `canton`s, which would otherwise inflate
    # court_count (108 distinct → 121 entries on the 2026-05 corpus).
    stats["court_count"] = len({entry["court"] for entry in stats["by_court"]})

    # ── Derived fields (no new SQL) ──

    # Top 10 courts (pre-sorted for chart)
    stats["top_courts"] = [
        {"court": c["court"], "canton": c["canton"], "count": c["count"]}
        for c in stats["by_court"][:10]
    ]

    # Year-over-year growth %
    year_items = sorted(stats["by_year"].items(), key=lambda x: x[0])
    yoy = {}
    for i in range(1, len(year_items)):
        yr, cnt = year_items[i]
        prev_cnt = year_items[i - 1][1]
        if prev_cnt > 0:
            yoy[yr] = round((cnt - prev_cnt) / prev_cnt * 100, 1)
    stats["yoy_growth"] = yoy

    # Federal vs cantonal split
    fed_total = sum(c["count"] for c in stats["by_court"] if c["canton"] == "CH")
    can_total = sum(c["count"] for c in stats["by_court"] if c["canton"] != "CH")
    stats["federal_vs_cantonal"] = {"federal": fed_total, "cantonal": can_total}

    # ── New SQL queries ──

    # Enrich by_canton with earliest, latest, court_count, languages
    canton_details = conn.execute("""
        SELECT
            canton,
            COUNT(DISTINCT court) as court_count,
            MIN(CASE WHEN decision_date IS NOT NULL AND decision_date != 'None'
                     AND decision_date > '1800-01-01' AND decision_date <= ? THEN decision_date END) as earliest,
            MAX(CASE WHEN decision_date IS NOT NULL AND decision_date != 'None'
                     AND decision_date <= ? THEN decision_date END) as latest,
            GROUP_CONCAT(DISTINCT language) as languages
        FROM decisions
        WHERE canton != 'CH'
        GROUP BY canton
    """, (today_iso, today_iso)).fetchall()
    canton_detail_map = {
        r["canton"]: {
            "court_count": r["court_count"],
            "earliest": r["earliest"],
            "latest": r["latest"],
            "languages": r["languages"].split(",") if r["languages"] else [],
        }
        for r in canton_details
    }
    for entry in stats["by_canton"]:
        detail = canton_detail_map.get(entry["canton"], {})
        entry["court_count"] = detail.get("court_count", 0)
        entry["earliest"] = detail.get("earliest")
        entry["latest"] = detail.get("latest")
        entry["languages"] = detail.get("languages", [])

    # Language by year (2005-current year for stacked area chart)
    lang_by_year = conn.execute("""
        SELECT
            substr(decision_date, 1, 4) as year,
            language,
            COUNT(*) as count
        FROM decisions
        WHERE decision_date IS NOT NULL
          AND decision_date != 'None'
          AND length(decision_date) >= 4
          AND substr(decision_date, 1, 4) BETWEEN ? AND ?
        GROUP BY year, language
        ORDER BY year ASC, language ASC
    """, ("2005", str(current_year))).fetchall()
    lby = {}
    for r in lang_by_year:
        yr = r["year"]
        if yr not in lby:
            lby[yr] = {}
        lby[yr][r["language"]] = r["count"]
    stats["language_by_year"] = lby

    # Monthly counts for last 3 years
    by_month = conn.execute("""
        SELECT
            substr(decision_date, 1, 7) as month,
            COUNT(*) as count
        FROM decisions
        WHERE decision_date IS NOT NULL
          AND decision_date != 'None'
          AND length(decision_date) >= 7
          AND decision_date >= date('now', '-3 years')
          AND decision_date < date('now', '+1 day')
        GROUP BY month
        ORDER BY month ASC
    """).fetchall()
    stats["by_month"] = {r["month"]: r["count"] for r in by_month}

    # Generated timestamp
    stats["generated_at"] = datetime.now(timezone.utc).isoformat()

    conn.close()
    return stats


_FEDERAL_COURT_EXCLUDE = (
    'bger', 'bge', 'bvger', 'bstger', 'bpatger', 'bge_egmr', 'bge_historical',
    'finma', 'finma_versicherungsrecht', 'weko', 'edoeb', 'ubi', 'elcom',
    'postcom', 'comcom', 'ta_sst', 'emark', 'hudoc_ch', 'ch_bundesrat',
    'ch_vb', 'sav_international', 'sav_kantone',
)


def _truncate(text: str | None, n: int = 220) -> str:
    """Collapse whitespace, strip markup noise, and clip to n chars."""
    if not text:
        return ""
    t = " ".join(str(text).split())
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip() + "…"


def collect_recent_decisions(db_path: Path, per_group: int = 10) -> dict:
    """Most recent BGE/BGer decisions and most recent cantonal decisions.

    Returns {"bge": [...], "cantonal": [...]} with fields for dashboard rendering.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    today_iso = datetime.now(timezone.utc).date().isoformat()
    try:
        bge_rows = conn.execute(
            """
            SELECT decision_id, docket_number, decision_date, court, language,
                   regeste, title
            FROM decisions
            WHERE court IN ('bger','bge')
              AND decision_date IS NOT NULL
              AND decision_date != ''
              AND decision_date <= ?
              AND length(full_text) > 500
              AND full_text NOT LIKE '%Document Dienstes ist fehlgeschlagen%'
            ORDER BY decision_date DESC, decision_id DESC
            LIMIT ?
            """,
            (today_iso, per_group),
        ).fetchall()

        placeholders = ",".join("?" * len(_FEDERAL_COURT_EXCLUDE))
        cantonal_rows = conn.execute(
            f"""
            SELECT decision_id, docket_number, decision_date, court, canton,
                   language, regeste, title
            FROM decisions
            WHERE court NOT IN ({placeholders})
              AND canton != 'CH'
              AND decision_date IS NOT NULL
              AND decision_date != ''
              AND decision_date <= ?
              AND regeste IS NOT NULL
              AND length(regeste) > 20
              AND length(full_text) > 500
            ORDER BY decision_date DESC, decision_id DESC
            LIMIT ?
            """,
            (*_FEDERAL_COURT_EXCLUDE, today_iso, per_group),
        ).fetchall()
    finally:
        conn.close()

    def _shape(r, include_canton=False):
        summary = _truncate(r["regeste"] or r["title"])
        out = {
            "decision_id": r["decision_id"],
            "docket": r["docket_number"] or "",
            "date": r["decision_date"],
            "court": r["court"],
            "language": r["language"],
            "summary": summary,
        }
        if include_canton:
            out["canton"] = r["canton"]
        return out

    return {
        "bge": [_shape(r) for r in bge_rows],
        "cantonal": [_shape(r, include_canton=True) for r in cantonal_rows],
    }


def collect_upcoming_amendments(limit: int = 20, timeout: int = 60) -> list[dict]:
    """Query Fedlex SPARQL for statute consolidations with dateApplicability > today.

    Returns up to `limit` upcoming amendments shaped for dashboard rendering.
    Returns [] on any failure — the section simply won't render.
    """
    # Upcoming consolidations — shape of the SPARQL kept simple: first fetch
    # sr/date pairs with future dateApplicability, then fetch titles in a
    # second query. The Fedlex title relation is indirect (through expressions)
    # and the combined query times out with OPTIONAL joins.
    query = """
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>

    SELECT DISTINCT ?work ?srNumber ?date WHERE {
      ?work a jolux:ConsolidationAbstract .
      ?work jolux:historicalLegalId ?srNumber .
      ?consolidation jolux:isMemberOf ?work .
      ?consolidation jolux:dateApplicability ?date .
      FILTER(?date > NOW())
    }
    ORDER BY ASC(?date)
    LIMIT %d
    """ % int(limit * 4)

    try:
        import requests
        resp = requests.post(
            "https://fedlex.data.admin.ch/sparqlendpoint",
            data={"query": query},
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "OpenCaseLaw/1.0 (+https://opencaselaw.ch)",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Fedlex upcoming-amendments query failed: {e}")
        return []

    seen = set()
    work_uris = {}
    out = []
    for b in data.get("results", {}).get("bindings", []):
        sr = b.get("srNumber", {}).get("value", "")
        date = b.get("date", {}).get("value", "")
        work = b.get("work", {}).get("value", "")
        if not sr or not date:
            continue
        date = date[:10]
        key = (sr, date)
        if key in seen:
            continue
        seen.add(key)
        fedlex_url = work.replace(
            "https://fedlex.data.admin.ch/eli/",
            "https://www.fedlex.admin.ch/eli/",
        ) if work.startswith("https://fedlex.data.admin.ch/eli/") else ""
        entry = {
            "sr_number": sr,
            "in_force_date": date,
            "title_de": "",
            "title_fr": "",
            "title_it": "",
            "url": fedlex_url,
        }
        out.append(entry)
        if work:
            work_uris.setdefault(work, entry)
        if len(out) >= limit:
            break

    # Second query: fetch titles via expression (title is on ?expr keyed by language).
    LANG_URIS = {
        "de": "http://publications.europa.eu/resource/authority/language/DEU",
        "fr": "http://publications.europa.eu/resource/authority/language/FRA",
        "it": "http://publications.europa.eu/resource/authority/language/ITA",
    }
    if work_uris:
        values = " ".join(f"<{u}>" for u in work_uris)
        for lang, lang_uri in LANG_URIS.items():
            title_q = f"""
            PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>

            SELECT ?work ?title WHERE {{
              VALUES ?work {{ {values} }}
              ?work jolux:isRealizedBy ?expr .
              ?expr jolux:language <{lang_uri}> .
              ?expr jolux:title ?title .
            }}
            """
            try:
                resp = requests.post(
                    "https://fedlex.data.admin.ch/sparqlendpoint",
                    data={"query": title_q},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "OpenCaseLaw/1.0 (+https://opencaselaw.ch)",
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                tdata = resp.json()
                for b in tdata.get("results", {}).get("bindings", []):
                    w = b.get("work", {}).get("value", "")
                    val = b.get("title", {}).get("value", "")
                    entry = work_uris.get(w)
                    if entry and val and not entry.get(f"title_{lang}"):
                        entry[f"title_{lang}"] = val
            except Exception as e:
                logger.warning(f"Fedlex title lookup failed ({lang}): {e}")

    return out


def collect_corpus_snapshot(repo_dir: Path) -> dict:
    """Count federal laws, cantonal laws, commentaries, citation graph size.

    Each sub-key is optional: if a DB file is missing or corrupt, we skip
    that field rather than failing the whole snapshot. This lets us ship
    the corpus panel ahead of scrapers that are still backfilling.
    """
    out: dict = {}

    def _open_ro(path: Path):
        if not path.exists():
            return None
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            return c
        except sqlite3.Error:
            return None

    def _count(conn, sql: str) -> int | None:
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.Error:
            return None

    # Federal laws — statutes.db (Fedlex mirror)
    statutes_path = repo_dir / "output" / "statutes.db"
    conn = _open_ro(statutes_path)
    if conn is not None:
        try:
            n_laws = _count(conn, "SELECT COUNT(*) FROM laws")
            n_articles = _count(
                conn, "SELECT COUNT(*) FROM articles WHERE lang='de'"
            )
            if n_laws is not None:
                out["federal_laws"] = n_laws
            if n_articles is not None:
                out["federal_law_articles"] = n_articles
        finally:
            conn.close()

    # Cantonal laws — cantonal_laws.db (LexFind mirror)
    cantonal_path = repo_dir / "output" / "cantonal_laws.db"
    conn = _open_ro(cantonal_path)
    if conn is not None:
        try:
            n_laws = _count(conn, "SELECT COUNT(DISTINCT lexfind_id) FROM laws")
            n_articles = _count(conn, "SELECT COUNT(*) FROM articles")
            if n_laws is not None:
                out["cantonal_laws"] = n_laws
            if n_articles is not None:
                out["cantonal_law_articles"] = n_articles
            # Per-canton counts
            try:
                by_canton = {
                    r[0]: r[1]
                    for r in conn.execute(
                        "SELECT canton, COUNT(DISTINCT lexfind_id) FROM laws "
                        "GROUP BY canton ORDER BY canton"
                    )
                }
                if by_canton:
                    out["cantonal_laws_by_canton"] = by_canton
            except sqlite3.Error:
                pass
        finally:
            conn.close()

    # Commentaries — ok_commentaries.db (OnlineKommentar + OpenLegalCommentary)
    ok_path = repo_dir / "output" / "ok_commentaries.db"
    conn = _open_ro(ok_path)
    if conn is not None:
        try:
            n = _count(conn, "SELECT COUNT(*) FROM commentaries")
            if n is not None:
                out["commentaries"] = n
        finally:
            conn.close()

    # OA Swiss legal scholarship — legal_scholarship.db
    schol_path = repo_dir / "output" / "legal_scholarship.db"
    conn = _open_ro(schol_path)
    if conn is not None:
        try:
            n = _count(conn, "SELECT COUNT(*) FROM publications")
            if n is not None:
                out["scholarship_publications"] = n
            by_source = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT source, COUNT(*) FROM publications "
                    "GROUP BY source ORDER BY 2 DESC"
                )
            }
            if by_source:
                out["scholarship_by_source"] = by_source
            by_type = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT pub_type, COUNT(*) FROM publications "
                    "GROUP BY pub_type ORDER BY 2 DESC"
                )
            }
            if by_type:
                out["scholarship_by_type"] = by_type
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    # Citation graph — reference_graph.db
    graph_path = repo_dir / "output" / "reference_graph.db"
    conn = _open_ro(graph_path)
    if conn is not None:
        try:
            # Try known table names (schema has evolved a bit over time).
            for sql in (
                "SELECT COUNT(*) FROM decision_citations",
                "SELECT COUNT(*) FROM citation_targets",
            ):
                n = _count(conn, sql)
                if n is not None:
                    out["citation_edges"] = n
                    break
            for sql in (
                "SELECT COUNT(*) FROM decision_statutes",
                "SELECT COUNT(*) FROM statute_links",
                "SELECT COUNT(*) FROM statute_refs",
            ):
                n = _count(conn, sql)
                if n is not None:
                    out["statute_edges"] = n
                    break
        finally:
            conn.close()

    return out


def collect_interesting_stats(repo_dir: Path) -> dict:
    """Compute six intrinsic facts about the corpus for the landing's
    "Notable" section. None of them depend on scraping date or pipeline
    timing — every value is a property of the published Swiss legal
    corpus itself, so the section reads as substance about Swiss law,
    not as plumbing telemetry.

    Output shape:
        {
          "most_cited_decision": {docket, citation_count, url},
          "most_cited_statute":  {law_code, article, ref_count},
          "oldest_decision":     {docket, decision_date, year, url},
          "language_split":      {de_pct, fr_pct, it_pct, total},
          "graph_size":          {decision_edges, statute_edges, total},
          "regeste_coverage":    {pct, with_regeste, total},
          "practice_coverage":   {total_documents, sources, by_source, by_authority, by_language},
        }
    Each field is best-effort — failures hide the corresponding card via
    the renderer rather than failing the publish.
    """
    out: dict = {}
    decisions_db = repo_dir / "output" / "decisions.db"
    graph_db = repo_dir / "output" / "reference_graph.db"

    # ── 1. Most-cited decision (intrinsic to citation graph) ─────────────
    if graph_db.exists() and decisions_db.exists():
        try:
            g = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True, timeout=10)
            g.row_factory = sqlite3.Row
            top = g.execute(
                """
                SELECT target_decision_id AS decision_id, COUNT(*) AS n
                FROM citation_targets
                WHERE target_decision_id IS NOT NULL
                GROUP BY target_decision_id
                ORDER BY n DESC
                LIMIT 1
                """
            ).fetchone()
            g.close()
            if top:
                d = sqlite3.connect(f"file:{decisions_db}?mode=ro", uri=True, timeout=10)
                d.row_factory = sqlite3.Row
                row = d.execute(
                    "SELECT docket_number FROM decisions WHERE decision_id=? LIMIT 1",
                    (top["decision_id"],),
                ).fetchone()
                d.close()
                if row:
                    out["most_cited_decision"] = {
                        "docket": row["docket_number"] or top["decision_id"],
                        "citation_count": int(top["n"]),
                        "url": f"https://mcp.opencaselaw.ch/entscheid/{top['decision_id']}",
                    }
        except sqlite3.Error:
            pass

    # ── 2. Most-cited statute article (intrinsic to corpus) ──────────────
    # decision_statutes schema: (decision_id, statute_id, mention_count)
    # statute_id is a denormalised string like "ART.89.ABS.1.BGG" or
    # "ART.41.OR". Aggregate by statute_id (collapsing paragraph variants
    # to the article level), then parse the winner's article + law code
    # for display.
    if graph_db.exists():
        try:
            import re as _re
            g = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True, timeout=10)
            g.row_factory = sqlite3.Row
            # Pull the top 50 raw statute_ids by total mention count, then
            # collapse "ART.89.ABS.1.BGG" + "ART.89.ABS.2.BGG" + ... into a
            # single "ART.89.BGG" tally. Top 50 is enough headroom for the
            # collapse to find the true article-level winner.
            top = g.execute(
                """
                SELECT statute_id, SUM(mention_count) AS n
                FROM decision_statutes
                WHERE statute_id IS NOT NULL AND TRIM(statute_id) != ''
                GROUP BY statute_id
                ORDER BY n DESC
                LIMIT 50
                """
            ).fetchall()
            g.close()
            # Collapse to (law_code, article). Pattern:
            #   ART.<article>(.ABS.<n>)*.<LAW_CODE>
            pat = _re.compile(
                r"^ART\.([\dA-Za-z]+)(?:\.(?:ABS|LIT|CHIFF|ZIFF|N)\.[\dA-Za-z]+)*\.(.+)$"
            )
            tallies: dict[tuple[str, str], int] = {}
            for r in top:
                m = pat.match(r["statute_id"])
                if not m:
                    continue
                article, law_code = m.group(1), m.group(2)
                key = (law_code, article)
                tallies[key] = tallies.get(key, 0) + int(r["n"])
            if tallies:
                (law_code, article), n = max(tallies.items(), key=lambda kv: kv[1])
                out["most_cited_statute"] = {
                    "law_code": law_code,
                    "article": article,
                    "ref_count": int(n),
                }
        except sqlite3.Error:
            pass
        except Exception:
            pass

    # ── 3-6. Decisions DB facts (one connection, multiple queries) ───────
    if decisions_db.exists():
        try:
            d = sqlite3.connect(f"file:{decisions_db}?mode=ro", uri=True, timeout=10)
            d.row_factory = sqlite3.Row

            # 3. Oldest decision in the corpus.
            # Filter out garbage dates (e.g. "0000-00-00" sentinels) by
            # requiring a four-digit year between 1700 and the current year.
            today_year = datetime.now(timezone.utc).year
            row = d.execute(
                """
                SELECT decision_id, docket_number, decision_date
                FROM decisions
                WHERE decision_date IS NOT NULL
                  AND decision_date >= '1700-01-01'
                  AND decision_date <= ?
                ORDER BY decision_date ASC
                LIMIT 1
                """,
                (f"{today_year}-12-31",),
            ).fetchone()
            if row and row["decision_date"]:
                year = row["decision_date"][:4]
                out["oldest_decision"] = {
                    "docket": row["docket_number"] or row["decision_id"],
                    "decision_date": row["decision_date"],
                    "year": int(year) if year.isdigit() else None,
                    "url": f"https://mcp.opencaselaw.ch/entscheid/{row['decision_id']}",
                }

            # 4. Language distribution (DE / FR / IT)
            rows = d.execute(
                """
                SELECT language, COUNT(*) AS n
                FROM decisions
                WHERE language IN ('de','fr','it','rm')
                GROUP BY language
                """
            ).fetchall()
            if rows:
                lang = {r["language"]: int(r["n"]) for r in rows}
                total = sum(lang.values())
                if total:
                    out["language_split"] = {
                        "de_pct": round(lang.get("de", 0) * 100.0 / total, 1),
                        "fr_pct": round(lang.get("fr", 0) * 100.0 / total, 1),
                        "it_pct": round(lang.get("it", 0) * 100.0 / total, 1),
                        "total": total,
                    }

            # 6. Regeste (head-note) coverage — share of decisions that
            #    carry a real head-note (>50 chars filters out empty / stub).
            row = d.execute(
                """
                SELECT
                  SUM(CASE WHEN regeste IS NOT NULL AND LENGTH(TRIM(regeste)) >= 50
                           THEN 1 ELSE 0 END) AS with_regeste,
                  COUNT(*) AS total
                FROM decisions
                """
            ).fetchone()
            if row and row["total"]:
                with_r = int(row["with_regeste"] or 0)
                total = int(row["total"])
                out["regeste_coverage"] = {
                    "pct": round(with_r * 100.0 / total, 1),
                    "with_regeste": with_r,
                    "total": total,
                }

            d.close()
        except sqlite3.Error:
            pass

    # ── 5. Citation graph total size (decision + statute edges) ──────────
    if graph_db.exists():
        try:
            g = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True, timeout=10)
            g.row_factory = sqlite3.Row
            tables = {r[0] for r in g.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            decision_edges = 0
            statute_edges = 0
            if "citation_targets" in tables:
                r = g.execute("SELECT COUNT(*) AS n FROM citation_targets").fetchone()
                if r:
                    decision_edges = int(r["n"])
            if "decision_statutes" in tables:
                r = g.execute("SELECT COUNT(*) AS n FROM decision_statutes").fetchone()
                if r:
                    statute_edges = int(r["n"])
            g.close()
            if decision_edges or statute_edges:
                out["graph_size"] = {
                    "decision_edges": decision_edges,
                    "statute_edges": statute_edges,
                    "total": decision_edges + statute_edges,
                }
        except sqlite3.Error:
            pass

    # ── 7. ECHR rulings concerning Switzerland ───────────────────────────
    # Switzerland has been subject to the ECHR since ratification in 1974.
    # Our corpus covers 5 surfaces: bge_egmr (Swiss BGE-published German
    # translations), hudoc_ch (HUDOC tagged Switzerland), and the three
    # ECtHR chambers (chamber, committee, grand_chamber). The ecthr_*
    # courts hold judgments against all 46 respondent states, not just
    # Switzerland: `canton` is historically named but holds a jurisdiction
    # code — CH = Swiss federal / Swiss-respondent, two-letter codes =
    # cantons, CE = non-Swiss ECtHR respondent states (see
    # scrapers/hudoc.py _RESPONDENT_TO_CANTON; bge_egmr and hudoc_ch rows
    # are CH by construction). Headline number = cases against Switzerland
    # only; ecthr_corpus_total = the full ECtHR corpus across all
    # respondent states (what the dashboard card sums via by_court).
    if decisions_db.exists():
        try:
            d = sqlite3.connect(f"file:{decisions_db}?mode=ro", uri=True, timeout=10)
            d.row_factory = sqlite3.Row
            ECHR_COURTS = (
                "bge_egmr", "hudoc_ch",
                "ecthr_chamber", "ecthr_committee", "ecthr_grand_chamber",
            )
            placeholders = ",".join("?" * len(ECHR_COURTS))
            total = d.execute(
                f"SELECT COUNT(*) AS n FROM decisions "
                f"WHERE court IN ({placeholders}) AND canton='CH'",
                ECHR_COURTS,
            ).fetchone()["n"]
            corpus_total = d.execute(
                f"SELECT COUNT(*) AS n FROM decisions WHERE court IN ({placeholders})",
                ECHR_COURTS,
            ).fetchone()["n"]
            grand = d.execute(
                "SELECT COUNT(*) AS n FROM decisions "
                "WHERE court='ecthr_grand_chamber' AND canton='CH'"
            ).fetchone()["n"]
            this_year = datetime.now(timezone.utc).year
            current_year = d.execute(
                f"SELECT COUNT(*) AS n FROM decisions "
                f"WHERE court IN ({placeholders}) AND canton='CH' "
                f"AND decision_date >= ?",
                (*ECHR_COURTS, f"{this_year}-01-01"),
            ).fetchone()["n"]
            most_recent = d.execute(
                f"SELECT decision_id, decision_date, docket_number, regeste "
                f"FROM decisions "
                f"WHERE court IN ({placeholders}) AND canton='CH' "
                f"ORDER BY decision_date DESC LIMIT 1",
                ECHR_COURTS,
            ).fetchone()
            out["echr_switzerland"] = {
                "total": int(total),
                "grand_chamber": int(grand),
                f"in_{this_year}": int(current_year),
                "ecthr_corpus_total": int(corpus_total),
                "most_recent": (
                    {
                        "decision_date": most_recent["decision_date"],
                        "docket": most_recent["docket_number"],
                        "regeste_excerpt": (
                            (most_recent["regeste"] or "")[:140]
                            if most_recent["regeste"] else None
                        ),
                        "url": (
                            f"https://mcp.opencaselaw.ch/entscheid/"
                            f"{most_recent['decision_id']}"
                        ),
                    }
                    if most_recent else None
                ),
            }
            d.close()
        except sqlite3.Error:
            pass

    # ── 8. Top 5 most-cited decisions (the canonical Swiss case-law map) ─
    # most_cited_decision shows just the #1; a top-5 list reveals the
    # actual structure of legal authority — unsurprising but powerful
    # storytelling fodder.
    if graph_db.exists() and decisions_db.exists():
        try:
            g = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True, timeout=10)
            g.row_factory = sqlite3.Row
            top_rows = g.execute(
                """
                SELECT target_decision_id AS decision_id, COUNT(*) AS n
                FROM citation_targets
                WHERE target_decision_id IS NOT NULL
                GROUP BY target_decision_id
                ORDER BY n DESC
                LIMIT 5
                """
            ).fetchall()
            g.close()
            d = sqlite3.connect(f"file:{decisions_db}?mode=ro", uri=True, timeout=10)
            d.row_factory = sqlite3.Row
            items = []
            for r in top_rows:
                row = d.execute(
                    "SELECT docket_number, regeste FROM decisions "
                    "WHERE decision_id=? LIMIT 1",
                    (r["decision_id"],),
                ).fetchone()
                if not row:
                    continue
                items.append({
                    "docket": row["docket_number"] or r["decision_id"],
                    "citation_count": int(r["n"]),
                    "regeste_excerpt": (
                        (row["regeste"] or "")[:160]
                        if row["regeste"] else None
                    ),
                    "url": (
                        f"https://mcp.opencaselaw.ch/entscheid/"
                        f"{r['decision_id']}"
                    ),
                })
            d.close()
            if items:
                out["top_5_decisions"] = items
        except sqlite3.Error:
            pass

    # ── 9. Top 5 most-cited statute articles ─────────────────────────────
    # Same reasoning as #2 but expanded to top-5 with collapsed
    # ABS/LIT/Ziff variants and per-row tallies.
    if graph_db.exists():
        try:
            import re as _re
            g = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True, timeout=10)
            g.row_factory = sqlite3.Row
            rows = g.execute(
                """
                SELECT statute_id, SUM(mention_count) AS n
                FROM decision_statutes
                WHERE statute_id IS NOT NULL AND TRIM(statute_id) != ''
                GROUP BY statute_id
                ORDER BY n DESC
                LIMIT 200
                """
            ).fetchall()
            g.close()
            pat = _re.compile(
                r"^ART\.([\dA-Za-z]+)(?:\.(?:ABS|LIT|CHIFF|ZIFF|N)\.[\dA-Za-z]+)*\.(.+)$"
            )
            tallies: dict[tuple[str, str], int] = {}
            for r in rows:
                m = pat.match(r["statute_id"])
                if not m:
                    continue
                article, law_code = m.group(1), m.group(2)
                key = (law_code, article)
                tallies[key] = tallies.get(key, 0) + int(r["n"])
            if tallies:
                top5 = sorted(tallies.items(), key=lambda kv: kv[1], reverse=True)[:5]
                out["top_5_statutes"] = [
                    {"law_code": lc, "article": art, "ref_count": int(n)}
                    for (lc, art), n in top5
                ]
        except sqlite3.Error:
            pass
        except Exception:
            pass

    # ── 10. Materialien (verbatim Federal Council Botschaft) coverage ────
    materialien_db = repo_dir / "output" / "materialien.db"
    if materialien_db.exists():
        try:
            m = sqlite3.connect(f"file:{materialien_db}?mode=ro", uri=True, timeout=10)
            m.row_factory = sqlite3.Row
            # Must guard: schema migration may not have run on every host.
            tables = {r[0] for r in m.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "botschaft_documents" in tables:
                total = m.execute(
                    "SELECT COUNT(*) AS n FROM botschaft_documents"
                ).fetchone()["n"]
                paragraphs = m.execute(
                    "SELECT COUNT(*) AS n FROM botschaft_paragraphs"
                ).fetchone()["n"] if "botschaft_paragraphs" in tables else 0
                links = m.execute(
                    "SELECT COUNT(*) AS n FROM article_botschaft_links"
                ).fetchone()["n"] if "article_botschaft_links" in tables else 0
                by_lang = {
                    r["language"]: int(r["n"]) for r in m.execute(
                        "SELECT language, COUNT(*) AS n FROM botschaft_documents "
                        "GROUP BY language"
                    ).fetchall()
                }
                year_range = m.execute(
                    "SELECT MIN(bbl_year) AS lo, MAX(bbl_year) AS hi "
                    "FROM botschaft_documents"
                ).fetchone()
                out["materialien_coverage"] = {
                    "total_documents": int(total),
                    "paragraphs": int(paragraphs),
                    "article_links": int(links),
                    "by_language": by_lang,
                    "year_range": (
                        {"from": int(year_range["lo"]), "to": int(year_range["hi"])}
                        if year_range and year_range["lo"] else None
                    ),
                }
            m.close()
        except sqlite3.Error:
            pass

    # ── 10b. Administrative practice — Verwaltungspraxis documents ───────
    practice = _practice_coverage(repo_dir)
    if practice:
        out["practice_coverage"] = practice

    # ── 11. Temporal span — how far back the corpus reaches per court ────
    # Adds richness to oldest_decision: which courts cover what era?
    if decisions_db.exists():
        try:
            d = sqlite3.connect(f"file:{decisions_db}?mode=ro", uri=True, timeout=10)
            d.row_factory = sqlite3.Row
            today_year = datetime.now(timezone.utc).year
            rows = d.execute(
                """
                SELECT court,
                       MIN(decision_date) AS earliest,
                       MAX(decision_date) AS latest,
                       COUNT(*) AS n
                FROM decisions
                WHERE decision_date IS NOT NULL
                  AND decision_date >= '1700-01-01'
                  AND decision_date <= ?
                GROUP BY court
                HAVING n >= 100
                ORDER BY earliest ASC
                LIMIT 6
                """,
                (f"{today_year}-12-31",),
            ).fetchall()
            d.close()
            if rows:
                out["historical_depth"] = [
                    {
                        "court": r["court"],
                        "earliest": r["earliest"],
                        "latest": r["latest"],
                        "span_years": (
                            int(r["latest"][:4]) - int(r["earliest"][:4])
                            if r["earliest"] and r["latest"]
                                and r["earliest"][:4].isdigit()
                                and r["latest"][:4].isdigit()
                            else None
                        ),
                        "decision_count": int(r["n"]),
                    }
                    for r in rows
                ]
        except sqlite3.Error:
            pass

    return out


def collect_traffic(repo_dir: Path, days: int = 30) -> dict | None:
    """Read ``output/analytics.db`` and build a public traffic block.

    All published counts are the DP-noised ``n_public`` column (never
    ``n_exact``), and cells below K-anon (stored as NULL) are skipped.
    If the analytics DB does not yet exist, returns None.

    Returns a dict shaped like::

        {
          "window_days": 30,
          "generated_at": "...",
          "total_calls": 12345,           # DP-noised, real traffic only
          "by_client":  [{"class": "cursor", "n": 4321}, ...],
          "top_endpoints": [{"class": "rest_search_decisions", "n": 999}, ...],
          "latency_ms":  {"rest_search_decisions": {"p50": 140, "p95": 410}, ...},
          "error_rate":  {"rest_search_decisions": 0.012, ...},
          "reach": [{"class": "word_addin", "n_installs": 87}, ...],
          "status_hist": {"2xx": 11000, "404": 50, "5xx": 20}
        }
    """
    db_path = repo_dir / "output" / "analytics.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None

    out: dict = {"window_days": days}
    try:
        # Window bounds
        row = conn.execute(
            "SELECT MAX(day) AS d FROM daily_tool_calls"
        ).fetchone()
        if not row or not row["d"]:
            return None
        window_end = row["d"]
        out["window_end"] = window_end

        # Total calls (DP-noised, last N days, real traffic only)
        row = conn.execute(
            """SELECT COALESCE(SUM(n_public), 0) AS n
               FROM daily_tool_calls
               WHERE day >= date(?, ?)
                 AND n_public IS NOT NULL""",
            (window_end, f"-{days - 1} days"),
        ).fetchone()
        out["total_calls"] = int(row["n"] or 0)

        # By client class (DP-noised, sum across window)
        rows = conn.execute(
            """SELECT client_class, COALESCE(SUM(n_public), 0) AS n
               FROM daily_tool_calls
               WHERE day >= date(?, ?)
                 AND n_public IS NOT NULL
               GROUP BY client_class
               ORDER BY n DESC""",
            (window_end, f"-{days - 1} days"),
        ).fetchall()
        out["by_client"] = [
            {"class": r["client_class"], "n": int(r["n"])}
            for r in rows
            if r["n"]
        ]

        # Top endpoints (DP-noised)
        rows = conn.execute(
            """SELECT endpoint_class, COALESCE(SUM(n_public), 0) AS n
               FROM daily_tool_calls
               WHERE day >= date(?, ?)
                 AND n_public IS NOT NULL
               GROUP BY endpoint_class
               ORDER BY n DESC
               LIMIT 15""",
            (window_end, f"-{days - 1} days"),
        ).fetchall()
        out["top_endpoints"] = [
            {"class": r["endpoint_class"], "n": int(r["n"])}
            for r in rows
            if r["n"]
        ]

        # Latency + error rate per endpoint (median of daily p50/p95,
        # error rate as 4xx+5xx over total exact count — aggregates are
        # not PII even when they use the exact column).
        rows = conn.execute(
            """SELECT endpoint_class,
                      AVG(p50_ms) AS p50,
                      AVG(p95_ms) AS p95,
                      SUM(n_exact) AS n_exact_sum,
                      SUM(err_4xx) AS e4,
                      SUM(err_5xx) AS e5
               FROM daily_tool_calls
               WHERE day >= date(?, ?)
                 AND p50_ms IS NOT NULL
               GROUP BY endpoint_class
               HAVING n_exact_sum >= 20""",  # small-n suppression
            (window_end, f"-{days - 1} days"),
        ).fetchall()
        latency: dict = {}
        err_rate: dict = {}
        for r in rows:
            latency[r["endpoint_class"]] = {
                "p50": int(round(r["p50"] or 0)),
                "p95": int(round(r["p95"] or 0)),
            }
            n = r["n_exact_sum"] or 0
            if n > 0:
                err_rate[r["endpoint_class"]] = round(
                    ((r["e4"] or 0) + (r["e5"] or 0)) / n, 4
                )
        out["latency_ms"] = latency
        out["error_rate"] = err_rate

        # Distinct installs per client class (HLL estimate, DP-noised)
        rows = conn.execute(
            """SELECT client_class, MAX(n_cohorts_public) AS n
               FROM daily_reach
               WHERE day >= date(?, ?)
                 AND n_cohorts_public IS NOT NULL
               GROUP BY client_class
               ORDER BY n DESC""",
            (window_end, f"-{days - 1} days"),
        ).fetchall()
        out["reach"] = [
            {"class": r["client_class"], "n_installs": int(r["n"])}
            for r in rows
            if r["n"]
        ]

        # Status histogram
        rows = conn.execute(
            """SELECT status_bucket, COALESCE(SUM(n_public), 0) AS n
               FROM daily_status
               WHERE day >= date(?, ?)
                 AND n_public IS NOT NULL
               GROUP BY status_bucket""",
            (window_end, f"-{days - 1} days"),
        ).fetchall()
        out["status_hist"] = {
            r["status_bucket"]: int(r["n"]) for r in rows if r["n"]
        }

        # Privacy metadata — so anyone reading stats.json can verify
        # the guarantees.
        meta = conn.execute(
            """SELECT k_anon, dp_epsilon
               FROM run_metadata
               ORDER BY day DESC LIMIT 1"""
        ).fetchone()
        if meta:
            out["privacy"] = {
                "k_anon": int(meta["k_anon"]),
                "dp_epsilon": float(meta["dp_epsilon"]),
                "note": (
                    "Counts are differentially private with epsilon=1.0; "
                    "cells below k=10 are suppressed. No per-user data is "
                    "stored, published, or recoverable."
                ),
            }
    finally:
        conn.close()

    return out


def _read_health_file(path: Path) -> dict | None:
    """Read a single scraper_health*.json file, swallowing any read errors."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not read {path.name}: {e}")
        return None


def collect_scraper_health(repo_dir: Path) -> dict | None:
    """Read scraper health JSON, merging the daily full-scrape file with
    the hourly federal poller file when both exist. The daily file is the
    base; per-court entries from the federal file override the daily ones
    (federal data is always fresher for the courts it polls).
    """
    logs_dir = repo_dir / "logs"
    daily = _read_health_file(logs_dir / "scraper_health.json")
    federal = _read_health_file(logs_dir / "scraper_health_federal.json")

    if not daily and not federal:
        logger.info("No scraper_health*.json found, skipping health data")
        return None

    # Start with whichever base has the most scrapers — usually daily.
    if daily and federal:
        health = dict(daily)
        merged_scrapers = dict(daily.get("scrapers", {}))
        merged_scrapers.update(federal.get("scrapers", {}))
        health["scrapers"] = merged_scrapers
        # Record both run timestamps so the dashboard can show freshness.
        health["run_at_daily"] = daily.get("run_at")
        health["run_at_federal"] = federal.get("run_at")
    else:
        health = daily or federal

    scrapers = health.get("scrapers", {})
    state_dir = repo_dir / "state"
    output_dir = repo_dir / "output" / "decisions"

    for court, info in scrapers.items():
        # State file line count = total known decisions
        state_file = state_dir / f"{court}.jsonl"
        if state_file.exists():
            try:
                with open(state_file, "rb") as f:
                    info["state_count"] = sum(1 for _ in f)
            except Exception:
                info["state_count"] = None
        else:
            info["state_count"] = None

        # JSONL output file size and mtime
        jsonl_file = output_dir / f"{court}.jsonl"
        if jsonl_file.exists():
            try:
                st = jsonl_file.stat()
                info["jsonl_size_mb"] = round(st.st_size / (1024 * 1024), 1)
                info["jsonl_mtime"] = datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat()
            except Exception:
                info["jsonl_size_mb"] = None
                info["jsonl_mtime"] = None
        else:
            info["jsonl_size_mb"] = None
            info["jsonl_mtime"] = None

    result = {
        "run_at": health.get("run_at"),
        "run_duration_s": health.get("run_duration_s"),
        "scrapers": scrapers,
    }
    # Preserve merged run-timestamps from the daily/federal split when present
    # so the dashboard can show freshness for both runs.
    if "run_at_daily" in health:
        result["run_at_daily"] = health["run_at_daily"]
    if "run_at_federal" in health:
        result["run_at_federal"] = health["run_at_federal"]
    if "disk" in health:
        result["disk"] = health["disk"]
    return result


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON to a sibling temp file, then os.replace it over ``path``.

    build_fts5's early-stats-push and publish.py Step 5 both run this script
    on docs/stats.json at the same time; with a plain open(path, "w") the two
    writers tore the file on 2026-09-05 (duplicated 16 KiB block, invalid
    JSON, pushed to the dashboard). Each writer now owns its temp file
    (pid-suffixed) and readers only ever see a complete document.
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main():
    parser = argparse.ArgumentParser(description="Generate stats.json from FTS5 database")
    parser.add_argument(
        "--db", type=str, default="output/decisions.db",
        help="Path to SQLite database (default: output/decisions.db)",
    )
    parser.add_argument(
        "--output", type=str, default="docs/stats.json",
        help="Output path for stats.json (default: docs/stats.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-interesting-stats", action="store_true",
        help="Skip the heavy interesting_stats block (full table scans). "
             "Preserves any existing block from the on-disk stats.json so "
             "the dashboard keeps showing the most recent weekly values.",
    )
    parser.add_argument(
        "--interesting-stats-only", action="store_true",
        help="Recompute only the interesting_stats block, merge into the "
             "existing stats.json, and exit. Used by the weekly timer.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    repo_dir = Path(__file__).parent.resolve()

    # Load existing stats.json once — used by both modes for either
    # preserving the interesting_stats block (daily) or merging back
    # into the rest (weekly --interesting-stats-only).
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load existing stats.json: {e}")

    # ── Weekly mode: only recompute interesting_stats, merge, write. ──
    if args.interesting_stats_only:
        if not existing:
            logger.error(
                "--interesting-stats-only requires an existing stats.json "
                f"at {output_path}; nothing to merge into."
            )
            sys.exit(1)
        try:
            existing["interesting_stats"] = collect_interesting_stats(repo_dir)
        except Exception as e:
            logger.error(f"collect_interesting_stats failed: {e}")
            sys.exit(1)
        _write_json_atomic(output_path, existing)
        logger.info(f"Wrote interesting_stats block → {output_path}")
        return

    stats = generate_stats(db_path)

    # ── Scraper health ──
    scraper_health = collect_scraper_health(repo_dir)
    if scraper_health:
        stats["scraper_health"] = scraper_health

    # ── Corpus snapshot: laws, commentaries, citation graph ──
    corpus = collect_corpus_snapshot(repo_dir)
    if corpus:
        stats["corpus"] = corpus

    # ── Did-you-know factoids for the landing page ──
    # Heavy: walks reference_graph.db (6.46M citation rows) + full
    # table scans on decisions.db (regeste, language). Daily publish
    # skips this and preserves the previous weekly value; a separate
    # weekly timer regenerates it.
    if args.no_interesting_stats:
        prior = existing.get("interesting_stats")
        if prior:
            stats["interesting_stats"] = prior
            logger.info(
                "Skipped interesting_stats (--no-interesting-stats); "
                "preserved previous block from on-disk stats.json"
            )
        else:
            logger.info(
                "Skipped interesting_stats (--no-interesting-stats); "
                "no prior block to preserve"
            )
    else:
        try:
            stats["interesting_stats"] = collect_interesting_stats(repo_dir)
        except Exception as e:
            logger.warning(f"collect_interesting_stats failed: {e}")

    # ── Traffic snapshot: DP-noised, k-anon aggregates from analytics.db ──
    traffic = collect_traffic(repo_dir, days=30)
    if traffic:
        stats["traffic"] = traffic

    # ── Recent decisions (BGE/BGer + cantonal) for dashboard "Latest" block ──
    try:
        stats["recent"] = collect_recent_decisions(db_path, per_group=10)
    except Exception as e:
        logger.warning(f"collect_recent_decisions failed: {e}")

    # ── Upcoming federal statute revisions (Fedlex SPARQL) ──
    try:
        upcoming = collect_upcoming_amendments(limit=15)
        if upcoming:
            stats["upcoming_amendments"] = upcoming
    except Exception as e:
        logger.warning(f"collect_upcoming_amendments failed: {e}")

    # ── Compute deltas vs a stats snapshot from a previous day ──
    # Using the file currently on disk breaks intra-day re-runs (second run
    # compares to the first run of the same day and shows delta=0). Instead,
    # pick the most recent git-tracked revision of stats.json whose
    # generated_at is on an earlier calendar day.
    prev = {}
    today_iso = datetime.now(timezone.utc).date().isoformat()

    # Try git history first — walk commits and pick the first one from an
    # earlier day.
    try:
        import subprocess
        # Resolve the output path relative to the repo (handle abs paths or
        # paths that don't sit inside repo_dir cleanly).
        try:
            rel_path = output_path.resolve().relative_to(repo_dir.resolve())
        except ValueError:
            # Fall back to a sensible default path inside the repo.
            rel_path = Path("docs/stats.json")
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "--pretty=%H",
             "-n", "14", "--", str(rel_path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            for commit_hash in result.stdout.strip().split("\n"):
                if not commit_hash:
                    continue
                show = subprocess.run(
                    ["git", "-C", str(repo_dir), "show",
                     f"{commit_hash}:{rel_path}"],
                    capture_output=True, text=True, timeout=15,
                )
                if show.returncode != 0:
                    continue
                try:
                    candidate = json.loads(show.stdout)
                except json.JSONDecodeError:
                    continue
                cand_ts = candidate.get("generated_at", "")
                cand_date = cand_ts[:10] if cand_ts else ""
                if cand_date and cand_date < today_iso:
                    prev = candidate
                    logger.info(
                        f"Loaded stats.json from commit {commit_hash[:8]} "
                        f"(generated {cand_ts}) for delta computation"
                    )
                    break
    except Exception as e:
        logger.warning(f"Git history lookup failed: {e}")

    # Fallback: current file on disk — but only if its date is earlier.
    if not prev and output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                candidate = json.load(f)
            cand_date = candidate.get("generated_at", "")[:10]
            if cand_date and cand_date < today_iso:
                prev = candidate
                logger.info("Using current stats.json on disk (earlier date) for delta")
        except Exception as e:
            logger.warning(f"Could not load previous stats.json: {e}")

    if prev:
        prev_total = prev.get("total", 0)
        delta_total = stats["total"] - prev_total

        # by_court delta (only where delta > 0).
        # by_court is a list of {court, canton, count, ...} dicts where
        # the SAME court can appear under multiple cantons (e.g.,
        # ecthr_chamber × CE = 1121 AND ecthr_chamber × CH = 32).
        # The previous version of this code built `prev_court_counts`
        # by repeated dict assignment, which silently overwrote earlier
        # rows — leaving only the LAST canton's count per court as the
        # baseline. Result: the 2026-05-11 dashboard showed
        # ecthr_chamber +1089 (vs the true +32), because yesterday's
        # baseline was clipped from 1121 → 32 by the overwrite. Same
        # pattern for ecthr_committee, ecthr_grand_chamber, sav_kantone.
        # Now: aggregate by court NAME on both sides before diffing.
        from collections import defaultdict
        prev_court_counts: dict = defaultdict(int)
        for c in prev.get("by_court", []):
            prev_court_counts[c["court"]] += c.get("count", 0)
        cur_court_counts: dict = defaultdict(int)
        for c in stats["by_court"]:
            cur_court_counts[c["court"]] += c.get("count", 0)
        delta_by_court = {}
        for court, cur_n in cur_court_counts.items():
            d = cur_n - prev_court_counts.get(court, 0)
            if d > 0:
                delta_by_court[court] = d

        # by_canton delta (only where delta > 0). Defensive aggregation
        # too, since the same canton may appear via multiple courts'
        # rollups in some codepaths.
        prev_canton_counts: dict = defaultdict(int)
        for c in prev.get("by_canton", []):
            prev_canton_counts[c["canton"]] += c.get("count", 0)
        cur_canton_counts: dict = defaultdict(int)
        for c in stats["by_canton"]:
            cur_canton_counts[c["canton"]] += c.get("count", 0)
        delta_by_canton = {}
        for canton, cur_n in cur_canton_counts.items():
            d = cur_n - prev_canton_counts.get(canton, 0)
            if d > 0:
                delta_by_canton[canton] = d

        # Corpus delta (federal laws, cantonal laws, commentaries, citations)
        prev_corpus = prev.get("corpus", {}) or {}
        now_corpus = stats.get("corpus", {}) or {}
        delta_corpus = {}
        for key in (
            "federal_laws", "federal_law_articles",
            "cantonal_laws", "cantonal_law_articles",
            "commentaries", "citation_edges", "statute_edges",
        ):
            if key in now_corpus and key in prev_corpus:
                d = now_corpus[key] - prev_corpus[key]
                if d != 0:
                    delta_corpus[key] = d

        stats["delta"] = {
            "total": delta_total,
            "by_court": delta_by_court,
            "by_canton": delta_by_canton,
            "corpus": delta_corpus,
            "previous_generated_at": prev.get("generated_at"),
        }
    else:
        stats["delta"] = {
            "total": 0, "by_court": {}, "by_canton": {},
            "corpus": {}, "previous_generated_at": None,
        }

    _write_json_atomic(output_path, stats)

    delta_total = stats["delta"]["total"]
    delta_str = f" (+{delta_total} new)" if delta_total > 0 else ""
    logger.info(f"Stats written to {output_path}")
    print(f"Total: {stats['total']} decisions, {stats['court_count']} courts{delta_str}")


if __name__ == "__main__":
    main()
