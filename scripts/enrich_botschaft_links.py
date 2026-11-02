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

For each (botschaft, sr_number) pair found this way, the ``article_anchor``
values the parser extracted into ``botschaft_paragraphs`` become
(sr_number, article, botschaft_id) link rows. In an XML document they must be
about that act: a bill explains the articles of several acts, and BBl 2023
679's comment on Art. 26 VwVG must not link to Art. 26 BEKJ. And a comment
follows the draft's numbering, which Parliament changes: where the enacted
heading shows that, the link goes to the enacted article and its evidence keeps
the document's number (``sparql_parliament_chain:draft_art=40``), which the
server reads to find the paragraphs (search_stack.article_scope.place_runs).
A PDF has no reliable outline (any capitalised line reads as a section), so its
anchors still go to every act of the bill, by the draft's numbers. Each run
replaces the chain links of the documents it covers, so a link that no longer
holds goes away; other evidence is left alone.

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
from search_stack.article_scope import (  # noqa: E402
    DRAFT_ARTICLE_EVIDENCE, _law_names_from_row, place_runs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("enrich_botschaft_links")

XML_FORMAT = "akoma-ntoso-xml"
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


def load_law_names(statutes_db: Path) -> dict[str, tuple[set[str], set[str]]]:
    """sr_number → (abbreviations, title names) in DE/FR/IT."""
    conn = sqlite3.connect(f"file:{statutes_db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT sr_number, abbr_de, abbr_fr, abbr_it, title_de, title_fr, title_it FROM laws"
    ).fetchall()
    conn.close()
    return {r[0]: _law_names_from_row(r[1:]) for r in rows}


def load_enacted_headings(statutes_db: Path, srs: set[str]) -> dict[tuple[str, str], dict[str, str]]:
    """(sr_number, lang) → {article_num: heading}, main body only."""
    conn = sqlite3.connect(f"file:{statutes_db}?mode=ro", uri=True)
    out: dict[tuple[str, str], dict[str, str]] = {}
    for sr, lang, num, heading in conn.execute(
            "SELECT sr_number, lang, article_num, heading FROM articles WHERE section = ''"):
        if sr in srs:
            out.setdefault((sr, lang), {})[num] = heading
    conn.close()
    return out


def chain_evidence(article: str, anchor: str) -> str:
    return ("sparql_parliament_chain" if article == anchor
            else f"sparql_parliament_chain:{DRAFT_ARTICLE_EVIDENCE}{anchor}")


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
        "--no-historical",
        action="store_true",
        help="Skip linking the 1850-1999 documents from the statute footnotes' "
             "volume citations (search_stack.historical_botschaft_links)",
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

    # 0. The 1850-1999 documents: linked from the statute footnotes' volume
    # citations ("BBl 1975 I 609"), locally, before (and independent of) the
    # SPARQL chain below, which knows only the typed post-1998 works.
    if not args.no_historical:
        from search_stack.historical_botschaft_links import link_historical
        hconn = sqlite3.connect(db_path)
        try:
            link_historical(hconn, statutes_path, dry_run=args.dry_run)
        finally:
            hconn.close()

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
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    law_names = load_law_names(statutes_path)
    enacted = load_enacted_headings(statutes_path, {s for v in bot_to_srs.values() for s in v})

    # Map URI → list[botschaft_id]. A Botschaft URI shares across DE/FR/IT
    # language versions in materialien.db (eli_uri carries no language
    # suffix), so each URI typically maps to up to 3 botschaft_ids. The
    # parliament-chain SPARQL is language-agnostic — the (Botschaft → SR)
    # mapping is true regardless of which expression we look at — so we
    # insert a link for EACH language version, not just one.
    bot_uri_to_ids: dict[str, list[int]] = {}
    xml_lang: dict[int, str] = {}
    for bid, uri, fmt, lang in conn.execute(
        "SELECT botschaft_id, eli_uri, format, language FROM botschaft_documents"
    ):
        bot_uri_to_ids.setdefault(uri, []).append(bid)
        if fmt == XML_FORMAT:
            xml_lang[bid] = lang
    log.info(
        f"  → {sum(len(v) for v in bot_uri_to_ids.values())} Botschaft rows "
        f"across {len(bot_uri_to_ids)} distinct URIs in materialien.db"
    )

    # For each (bot_id, sr) pair, fetch distinct article_anchor values.
    before = conn.execute(
        "SELECT COUNT(*) FROM article_botschaft_links"
    ).fetchone()[0]

    pending: list[tuple[str, str, int, str]] = []
    covered: list[int] = []
    skipped_no_id = 0
    skipped_no_anchors = 0
    out_of_scope = 0
    renumbered = 0
    for bot_uri, srs in bot_to_srs.items():
        bids = bot_uri_to_ids.get(bot_uri, [])
        if not bids:
            skipped_no_id += 1
            continue
        covered.extend(bids)
        rows_by_bid = {
            bid: conn.execute(
                "SELECT article_anchor, section_path, text FROM botschaft_paragraphs "
                "WHERE botschaft_id = ? AND article_anchor IS NOT NULL "
                "ORDER BY para_order",
                (bid,),
            ).fetchall()
            for bid in bids
        }
        skipped_no_anchors += sum(1 for rows in rows_by_bid.values() if not rows)
        # The language editions of a document share its numbering, so one
        # placement holds for all of them: decided on the German edition, whose
        # enacted headings are the most complete (BEKJ has no Italian Art. 38
        # row, so Italian alone would not see that Art. 37 moved).
        xml_bids = [b for b in bids if b in xml_lang and rows_by_bid[b]]
        primary = next((b for b in xml_bids if xml_lang[b] == "de"),
                       xml_bids[0] if xml_bids else None)
        for sr in srs:
            placed = None
            if primary is not None:
                abbrs, names = law_names.get(sr, (set(), set()))
                placed = place_runs(rows_by_bid[primary], sr, abbrs, names, len(srs) > 1,
                                    enacted.get((sr, xml_lang[primary]), {}))
            for bid, rows in rows_by_bid.items():
                anchors = {r["article_anchor"] for r in rows}
                kept = ({art: a for art, a in placed.items() if a in anchors}
                        if bid in xml_lang and placed is not None else {a: a for a in anchors})
                out_of_scope += len(anchors - set(kept.values()))
                renumbered += sum(1 for art, anchor in kept.items() if art != anchor)
                pending.extend((sr, art, bid, chain_evidence(art, anchor))
                               for art, anchor in sorted(kept.items()))

    log.info(
        f"  → candidate inserts: {len(pending)} "
        f"(skipped: no_botschaft_id={skipped_no_id}, no_anchors={skipped_no_anchors}, "
        f"not_placed={out_of_scope}, renumbered={renumbered})"
    )

    if args.dry_run:
        log.info("Dry-run — first 10 candidates:")
        for sr, art, bid, evidence in pending[:10]:
            log.info(f"  sr={sr}  art={art}  botschaft_id={bid}  {evidence}")
        log.info(f"  …and {max(0, len(pending) - 10)} more")
        return 0

    # Only documents this run saw: a partial SPARQL answer must not wipe the
    # links of the documents it left out.
    log.info("Replacing the chain links of the covered documents…")
    conn.executemany(
        "DELETE FROM article_botschaft_links "
        "WHERE botschaft_id = ? AND evidence LIKE 'sparql_parliament_chain%'",
        [(bid,) for bid in covered],
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO article_botschaft_links
            (sr_number, article, botschaft_id, relation, evidence)
        VALUES (?, ?, ?, 'considered', ?)
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
        WHERE evidence LIKE 'sparql_parliament_chain%'
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
