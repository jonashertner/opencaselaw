"""Bulk-enrich article_botschaft_links via Fedlex parliamentary chain.

The original v0.4 link table was populated only from amendment_refs
(statute footnote citations) — narrow coverage, ~1,400 links across
~120 Botschaften. After v0.5's SPARQL-discovery pulled 1,800+ real
Botschaften, most have NO entry in article_botschaft_links because
amendment_refs doesn't index them.

This enrichment script closes the gap by traversing the Fedlex
JOLUX graph:

    Botschaft (FGA, typeDocument=23)
        ↑ hasResultingLegalResource
    Project (proj/.../init)
        — parliamentDraftId →
    Project (proj/.../subsequent stages)
        ↓ hasResultingLegalResource
    Enacted draft (FGA / oc, typeDocument=21)
        ↑ basicAct
    Consolidated work (cc/...)
        ↔ statutes.db.work_uri ↔ sr_number

For each (botschaft, sr_number) pair found this way, every distinct
``article_anchor`` value already extracted by the parser into
``botschaft_paragraphs`` becomes a (sr_number, article, botschaft_id)
link row. Idempotent via INSERT OR IGNORE.

Usage:
    python3 scripts/enrich_botschaft_links.py
    python3 scripts/enrich_botschaft_links.py --dry-run  # list, don't insert
    python3 scripts/enrich_botschaft_links.py --db output/materialien.db --statutes output/statutes.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrapers.fedlex_materialien import (  # noqa: E402
    FGA_DOC_TYPES, fga_type_uri, parse_doc_types,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("enrich_botschaft_links")

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"
SPARQL_HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "opencaselaw-materialien-enrich/0.5",
}


def _sparql(q: str, timeout: int = 180) -> list[dict]:
    """POST a SPARQL query, return result bindings."""
    body = urlencode({"query": q}).encode()
    req = Request(SPARQL_ENDPOINT, data=body, headers=SPARQL_HEADERS, method="POST")
    with urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data.get("results", {}).get("bindings", [])


def build_chain_query(type_ids: tuple[int, ...] = (23,)) -> str:
    """The parliamentary-chain SPARQL for the given Fedlex resource types.
    Berichte, Stellungnahmen, Kommissionsberichte and Erlassentwürfe hang
    off the same ``proj`` nodes as the Botschaft (verified 2026-09-09), so
    one query links every ingested doc_type."""
    values = " ".join(f"<{fga_type_uri(t)}>" for t in type_ids)
    return f"""PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    SELECT DISTINCT ?bot ?cc WHERE {{
      VALUES ?td {{ {values} }}
      ?bot jolux:typeDocument ?td .
      FILTER(strstarts(STR(?bot), "https://fedlex.data.admin.ch/eli/fga/"))
      ?proj jolux:hasResultingLegalResource ?bot .
      ?proj jolux:parliamentDraftId ?pdid .
      ?proj2 jolux:parliamentDraftId ?pdid .
      ?proj2 jolux:hasResultingLegalResource ?other .
      ?cc jolux:basicAct ?other .
      FILTER(strstarts(STR(?cc), "https://fedlex.data.admin.ch/eli/cc/"))
    }}"""


def fetch_all_botschaft_cc_pairs(
    type_ids: tuple[int, ...] = (23,),
) -> list[tuple[str, str]]:
    """Return [(document_uri, cc_uri), …] for every Bundesblatt document of
    the given types that Fedlex's parliamentary graph maps to at least one
    consolidated compilation work URI."""
    q = build_chain_query(type_ids)
    rows = _sparql(q)
    return [(r["bot"]["value"], r["cc"]["value"]) for r in rows]


def build_cc_to_sr_index(statutes_db: Path) -> dict[str, str]:
    """Index cc-work-uri → sr_number from the existing statutes mirror."""
    conn = sqlite3.connect(f"file:{statutes_db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT work_uri, sr_number FROM laws WHERE work_uri IS NOT NULL"
    ).fetchall()
    conn.close()
    return {uri: sr for uri, sr in rows if uri}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--db",
        default=str(Path("output/materialien.db")),
        help="materialien.db path",
    )
    ap.add_argument(
        "--statutes",
        default=str(Path("output/statutes.db")),
        help="statutes.db path (Fedlex local mirror — SR ↔ cc-uri map)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the link delta without inserting",
    )
    ap.add_argument(
        "--types",
        default=",".join(str(t) for t in sorted(FGA_DOC_TYPES)),
        help=(
            "Comma-separated Fedlex resource-type ids to traverse "
            "(default: every type build_botschaft_corpus can ingest)."
        ),
    )
    args = ap.parse_args()
    try:
        type_ids = parse_doc_types(args.types)
    except ValueError as e:
        log.error(str(e))
        return 2

    db_path = Path(args.db).resolve()
    statutes_path = Path(args.statutes).resolve()
    if not db_path.exists():
        log.error(f"materialien.db not found: {db_path}")
        return 1
    if not statutes_path.exists():
        log.error(f"statutes.db not found: {statutes_path}")
        return 1

    # 1. Pull all Botschaft → cc pairs from Fedlex SPARQL.
    log.info(f"Querying Fedlex SPARQL for document → cc pairs (types {list(type_ids)})…")
    t0 = time.time()
    pairs = fetch_all_botschaft_cc_pairs(type_ids)
    log.info(
        f"  → {len(pairs)} pairs (parsed in {time.time() - t0:.1f}s)"
    )

    # 2. Build cc → sr_number index from local statutes.db.
    log.info("Building cc→sr index from statutes.db…")
    cc_to_sr = build_cc_to_sr_index(statutes_path)
    log.info(f"  → {len(cc_to_sr)} statute entries")

    # 3. Filter SPARQL pairs to those whose cc resolves to an SR number.
    bot_to_srs: dict[str, set[str]] = {}
    for bot, cc in pairs:
        sr = cc_to_sr.get(cc)
        if sr is None:
            continue
        bot_to_srs.setdefault(bot, set()).add(sr)
    log.info(
        f"  → {len(bot_to_srs)} Botschaften with at least one resolvable SR; "
        f"{sum(len(s) for s in bot_to_srs.values())} (Bot, SR) pairs"
    )

    # 4. Open materialien.db and join (Botschaft, SR) × article_anchor.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Map URI → list[botschaft_id]. A Botschaft URI shares across DE/FR/IT
    # language versions in materialien.db (eli_uri carries no language
    # suffix), so each URI typically maps to up to 3 botschaft_ids. The
    # parliament-chain SPARQL is language-agnostic — the (Botschaft → SR)
    # mapping is true regardless of which expression we look at — so we
    # insert a link for EACH language version, not just one.
    bot_uri_to_ids: dict[str, list[int]] = {}
    for bid, uri in conn.execute(
        "SELECT botschaft_id, eli_uri FROM botschaft_documents"
    ):
        bot_uri_to_ids.setdefault(uri, []).append(bid)
    log.info(
        f"  → {sum(len(v) for v in bot_uri_to_ids.values())} Botschaft rows "
        f"across {len(bot_uri_to_ids)} distinct URIs in materialien.db"
    )

    # For each (bot_id, sr) pair, fetch distinct article_anchor values.
    before = conn.execute(
        "SELECT COUNT(*) FROM article_botschaft_links"
    ).fetchone()[0]

    pending: list[tuple[str, str, int]] = []
    skipped_no_id = 0
    skipped_no_anchors = 0
    for bot_uri, srs in bot_to_srs.items():
        bids = bot_uri_to_ids.get(bot_uri, [])
        if not bids:
            skipped_no_id += 1
            continue
        for bid in bids:
            anchors = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT article_anchor FROM botschaft_paragraphs "
                    "WHERE botschaft_id = ? AND article_anchor IS NOT NULL",
                    (bid,),
                ).fetchall()
            ]
            if not anchors:
                skipped_no_anchors += 1
                continue
            for sr in srs:
                for art in anchors:
                    pending.append((sr, art, bid))

    log.info(
        f"  → candidate inserts: {len(pending)} "
        f"(skipped: no_botschaft_id={skipped_no_id}, no_anchors={skipped_no_anchors})"
    )

    if args.dry_run:
        log.info("Dry-run — first 10 candidates:")
        for sr, art, bid in pending[:10]:
            log.info(f"  sr={sr}  art={art}  botschaft_id={bid}")
        log.info(f"  …and {max(0, len(pending) - 10)} more")
        return 0

    # Bulk insert with INSERT OR IGNORE.
    log.info("Inserting links (idempotent via INSERT OR IGNORE)…")
    conn.executemany(
        """
        INSERT OR IGNORE INTO article_botschaft_links
            (sr_number, article, botschaft_id, relation, evidence)
        VALUES (?, ?, ?, 'considered', 'sparql_parliament_chain')
        """,
        pending,
    )
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM article_botschaft_links"
    ).fetchone()[0]
    new_links = after - before
    log.info(
        f"article_botschaft_links: {before} → {after} (+{new_links} new)"
    )

    # Sample the top-coverage SR numbers for sanity.
    by_sr = conn.execute(
        """
        SELECT sr_number, COUNT(*) AS n
        FROM article_botschaft_links
        WHERE evidence = 'sparql_parliament_chain'
        GROUP BY sr_number
        ORDER BY n DESC
        LIMIT 10
        """
    ).fetchall()
    log.info("Top SR numbers by new-link count:")
    for sr, n in by_sr:
        log.info(f"  SR {sr}: {n} links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
