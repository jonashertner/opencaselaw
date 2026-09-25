"""Extract citations from scholarship full_text and populate
pub_citations_decisions + pub_citations_statutes in legal_scholarship.db.

Two outputs per scholarship publication:
  1. Case citations resolved against decisions.db (BGE, BGer, BVGer, BStGer,
     BPatGer dockets, and Schaffhausen Obergericht "OGE 60/2017/43").
     Unresolvable refs are silently dropped.
  2. Statute references resolved via statutes.db's abbreviation → sr_number
     map (Art. 41 OR → SR 220, Art. 8 BV → SR 101, etc.).

Idempotent via INSERT OR IGNORE. Wired into build_legal_scholarship.py
so it runs every full rebuild of legal_scholarship.db.

Reuses the case/statute regex primitives in search_stack/reference_extraction.py
that already power the main reference graph build, so extraction recall here
matches what we apply to decisions themselves.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Optional

from search_stack.reference_extraction import (
    extract_case_citations,
    extract_statute_references,
)

log = logging.getLogger("scholarship_citation_extractor")


# "OGE 60/2017/43" — Schaffhausen Obergericht, Abteilung/Jahr/Nummer, an
# optional letter suffix (60/2008/20A). Stored dockets may carry "Nr. ".
_OGE_RE = re.compile(r"\bOGE\s+(\d{1,3}/\d{4}/\d{1,4}[A-Z]?)(?![\d/])")
_SH_DOCKET_RE = re.compile(r"(?:Nr\.\s*)?(\d{1,3}/\d{4}/\d{1,4}[A-Z]?)")


# Match decision_id_variants() in mcp_server.py — BGE keys are kept both with
# and without the "BGE " prefix so either form in a paper resolves.
def load_decision_lookups(decisions_db_path: str) -> dict[str, str]:
    """Build in-memory dict: normalized citation key → decision_id.

    Loaded once per build; ~50 MB for the full corpus.
    """
    lookups: dict[str, str] = {}
    conn = sqlite3.connect(
        f"file:{decisions_db_path}?mode=ro&immutable=1", uri=True
    )

    # BGE rows: docket_number is "151 III 481"; canonical id is "bge_151 III 481"
    n_bge = 0
    for decision_id, docket in conn.execute(
        "SELECT decision_id, docket_number FROM decisions "
        "WHERE court='bge' AND docket_number IS NOT NULL"
    ):
        key = (docket or "").strip()
        if not key:
            continue
        lookups[key] = decision_id
        lookups[f"BGE {key}"] = decision_id
        n_bge += 1

    # BGer rows: dockets like "4A_571/2008" or "4P.253/1999"
    n_bger = 0
    for decision_id, docket in conn.execute(
        "SELECT decision_id, docket_number FROM decisions "
        "WHERE court='bger' AND docket_number IS NOT NULL"
    ):
        key = (docket or "").strip()
        if not key:
            continue
        lookups[key] = decision_id
        # Cross-form variants (period ↔ underscore between chamber + number)
        variant = key.replace("_", ".") if "_" in key else key.replace(".", "_", 1)
        if variant != key:
            lookups.setdefault(variant, decision_id)
        n_bger += 1

    # BVGer + BStGer + BPatGer dockets (e.g. "A-2038/2006", "SK.2004.8")
    n_other = 0
    for decision_id, docket in conn.execute(
        "SELECT decision_id, docket_number FROM decisions "
        "WHERE court IN ('bvger','bstger','bpatger') "
        "AND docket_number IS NOT NULL"
    ):
        key = (docket or "").strip()
        if not key:
            continue
        lookups[key] = decision_id
        n_other += 1

    # Schaffhausen Obergericht: literature cites it as "OGE 60/2017/43"
    # (Obergerichtsentscheid). The same ruling is held twice, as sh_gerichte
    # "Nr. 60/2017/43" (the direct scraper) and sh_obergericht "60/2017/43";
    # sh_gerichte is read first and wins, and find_scholarship_citing_decision
    # reaches the twin through the representation manifest.
    n_sh = 0
    for court in ("sh_gerichte", "sh_obergericht"):
        for decision_id, docket in conn.execute(
            "SELECT decision_id, docket_number FROM decisions "
            "WHERE court = ? AND docket_number IS NOT NULL", (court,)
        ):
            m = _SH_DOCKET_RE.fullmatch((docket or "").strip())
            if not m:
                continue
            if lookups.setdefault(f"OGE {m.group(1)}", decision_id) == decision_id:
                n_sh += 1

    conn.close()
    log.info(
        "decision lookups: %d entries (bge=%d, bger=%d, other=%d, sh=%d)",
        len(lookups), n_bge, n_bger, n_other, n_sh,
    )
    return lookups


def load_law_abbr_lookups(statutes_db_path: str) -> dict[str, str]:
    """Build map: law abbreviation (de/fr/it) → sr_number."""
    lookups: dict[str, str] = {}
    conn = sqlite3.connect(
        f"file:{statutes_db_path}?mode=ro&immutable=1", uri=True
    )
    for sr, ab_de, ab_fr, ab_it in conn.execute(
        "SELECT sr_number, abbr_de, abbr_fr, abbr_it FROM laws"
    ):
        for ab in (ab_de, ab_fr, ab_it):
            if not ab:
                continue
            key = ab.strip().upper()
            if key:
                lookups.setdefault(key, sr)
    conn.close()
    log.info("law-abbr lookups: %d entries", len(lookups))
    return lookups


def _snippet_for(text: str, match_raw: str, halfwidth: int = 60, cap: int = 140) -> Optional[str]:
    """Return ±halfwidth chars around the first occurrence of match_raw,
    collapsed whitespace, truncated to cap. None if not found."""
    idx = text.find(match_raw)
    if idx < 0:
        return None
    start = max(0, idx - halfwidth)
    end = min(len(text), idx + len(match_raw) + halfwidth)
    snip = " ".join(text[start:end].split())
    return snip[:cap] if snip else None


def extract_for_publication(
    full_text: str,
    decision_lookups: dict[str, str],
    law_lookups: dict[str, str],
) -> tuple[list[tuple[str, Optional[str]]], list[tuple[str, str, Optional[str]]]]:
    """Extract resolved citations from one publication's full_text.

    Returns (decisions, statutes) where:
        decisions = [(decision_id, snippet), ...]   — deduped
        statutes  = [(sr_number, article, snippet), ...]  — deduped
    """
    if not full_text or len(full_text) < 100:
        return [], []

    decisions: list[tuple[str, Optional[str]]] = []
    statutes: list[tuple[str, str, Optional[str]]] = []
    seen_decisions: set[str] = set()
    seen_statutes: set[tuple[str, str]] = set()

    for cit in extract_case_citations(full_text):
        # extract_case_citations returns CaseCitation(raw, citation_type, normalized).
        # Try normalized first, then raw.
        decision_id = decision_lookups.get(cit.normalized.strip())
        if not decision_id:
            decision_id = decision_lookups.get(cit.raw.strip())
        if not decision_id or decision_id in seen_decisions:
            continue
        seen_decisions.add(decision_id)
        decisions.append((decision_id, _snippet_for(full_text, cit.raw)))

    for m in _OGE_RE.finditer(full_text):
        decision_id = decision_lookups.get(f"OGE {m.group(1)}")
        if not decision_id or decision_id in seen_decisions:
            continue
        seen_decisions.add(decision_id)
        decisions.append((decision_id, _snippet_for(full_text, m.group(0))))

    for ref in extract_statute_references(full_text):
        sr = law_lookups.get(ref.law_code.upper())
        if not sr:
            continue
        article = ref.article or ""
        key = (sr, article)
        if key in seen_statutes:
            continue
        seen_statutes.add(key)
        statutes.append((sr, article, _snippet_for(full_text, ref.raw)))

    return decisions, statutes


def extract_all(
    conn: sqlite3.Connection,
    decisions_db_path: str,
    statutes_db_path: str,
    commit_every: int = 500,
) -> dict[str, int]:
    """Iterate all has_full_text=1 publications and write resolved citations.

    Caller passes an open writable connection to legal_scholarship.db
    (e.g. the .db.tmp during the atomic-swap build).
    """
    t_start = time.time()
    decision_lookups = load_decision_lookups(decisions_db_path)
    law_lookups = load_law_abbr_lookups(statutes_db_path)
    log.info(
        "lookups loaded in %.1fs (%d decisions, %d law abbrevs)",
        time.time() - t_start, len(decision_lookups), len(law_lookups),
    )

    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, pub_id, full_text FROM publications "
        "WHERE has_full_text=1 AND full_text IS NOT NULL"
    ).fetchall()
    log.info("scanning %d full-text publications", len(rows))

    n_pubs = n_with = n_dec = n_stat = 0
    t_loop = time.time()
    for i, (db_id, pub_id, full_text) in enumerate(rows):
        n_pubs += 1
        decisions, statutes = extract_for_publication(
            full_text, decision_lookups, law_lookups
        )
        if decisions or statutes:
            n_with += 1
        for decision_id, snippet in decisions:
            cur.execute(
                "INSERT OR IGNORE INTO pub_citations_decisions "
                "(pub_id, decision_id, snippet) VALUES (?, ?, ?)",
                (db_id, decision_id, snippet),
            )
            n_dec += 1
        for sr, article, snippet in statutes:
            cur.execute(
                "INSERT OR IGNORE INTO pub_citations_statutes "
                "(pub_id, sr_number, article, snippet) VALUES (?, ?, ?, ?)",
                (db_id, sr, article, snippet),
            )
            n_stat += 1

        if (i + 1) % commit_every == 0:
            conn.commit()
            elapsed = time.time() - t_loop
            rate = (i + 1) / max(elapsed, 0.001)
            log.info(
                "  %d/%d pubs, %d dec-cites, %d stat-cites, %.0f pubs/s",
                i + 1, len(rows), n_dec, n_stat, rate,
            )

    conn.commit()
    elapsed = time.time() - t_start
    log.info(
        "citations extracted in %.1fs: %d pubs (%d with citations), "
        "%d decision-cites, %d statute-cites",
        elapsed, n_pubs, n_with, n_dec, n_stat,
    )
    return {
        "elapsed_seconds": round(elapsed, 1),
        "pubs_scanned": n_pubs,
        "pubs_with_citations": n_with,
        "decision_citations": n_dec,
        "statute_citations": n_stat,
    }


if __name__ == "__main__":
    # CLI entry point for ad-hoc runs against an existing DB
    import argparse
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, required=True,
                   help="legal_scholarship.db (writable)")
    p.add_argument("--decisions-db", type=Path, required=True)
    p.add_argument("--statutes-db", type=Path, required=True)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if not args.db.exists():
        print(f"ERR: {args.db} does not exist", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(args.db))
    summary = extract_all(conn, str(args.decisions_db), str(args.statutes_db))
    conn.close()
    print(summary)
