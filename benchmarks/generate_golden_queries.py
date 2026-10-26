#!/usr/bin/env python3
"""Generate candidate golden set queries from citation graph.

For each legal domain (statute article, court type), finds top-cited
decisions and generates candidate search queries from their regeste text.

Output: JSON with candidate queries for manual curation.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

# Legal domains to expand — targeting weak tags in current benchmark
STATUTE_DOMAINS = [
    # Tenancy (MRR=0.148)
    ("ART.271.OR", "tenancy", "Anfechtung der Kündigung"),
    ("ART.261.OR", "tenancy", "Veräusserung der Mietsache"),
    ("ART.269.OR", "tenancy", "Missbräuchliche Mietzinse"),
    ("ART.269a.OR", "tenancy", "Mietzinserhöhung"),
    # Tax (MRR=0.083)
    ("ART.16.DBG", "tax", "Einkommenssteuer"),
    ("ART.127.BV", "tax", "Grundsätze der Besteuerung"),
    ("ART.190.DBG", "tax", "Steuerhinterziehung"),
    # Human rights / public law
    ("ART.10.BV", "constitutional", "Recht auf Leben und persönliche Freiheit"),
    ("ART.29.BV", "constitutional", "Rechtliches Gehör"),
    # Employment (MRR=0.240)
    ("ART.337.OR", "employment", "Fristlose Kündigung"),
    ("ART.328.OR", "employment", "Schutz der Persönlichkeit im Arbeitsverhältnis"),
    # Insurance
    ("ART.6.UVG", "insurance", "Unfallbegriff"),
    # Family
    ("ART.133.ZGB", "family", "Kindesunterhalt"),
    ("ART.176.ZGB", "family", "Eheschutz"),
    # Property
    ("ART.641.ZGB", "property", "Eigentum"),
    ("ART.679.ZGB", "property", "Nachbarrecht"),
    # Liability
    ("ART.42.OR", "liability", "Schadensbeweis"),
    ("ART.58.SVG", "liability", "Haftung des Motorfahrzeughalters"),
]


def find_top_decisions_by_statute(
    graph_conn: sqlite3.Connection,
    fts_conn: sqlite3.Connection,
    statute_id: str,
    limit: int = 8,
) -> list[dict]:
    """Find top-cited decisions for a given statute article."""
    rows = graph_conn.execute(
        """
        SELECT ds.decision_id,
               COUNT(*) AS mentions,
               COALESCE(
                   (SELECT COUNT(DISTINCT ct.source_decision_id)
                    FROM citation_targets ct
                    WHERE ct.target_decision_id = ds.decision_id), 0
               ) AS citations
        FROM decision_statutes ds
        WHERE ds.statute_id = ?
        GROUP BY ds.decision_id
        ORDER BY citations DESC
        LIMIT ?
        """,
        (statute_id, limit),
    ).fetchall()

    results = []
    for r in rows:
        fts_row = fts_conn.execute(
            "SELECT regeste, language, docket_number, court FROM decisions WHERE decision_id = ?",
            (r[0],),
        ).fetchone()
        if fts_row and fts_row[0] and len(fts_row[0]) >= 50:
            results.append({
                "decision_id": r[0],
                "docket_number": fts_row[2] or "",
                "court": fts_row[3] or "",
                "citations": r[2],
                "mentions": r[1],
                "language": fts_row[1] or "",
                "regeste": fts_row[0][:400],
            })
    return results


def find_top_it_decisions_by_statute(graph_conn, fts_conn, statute_id, limit=10):
    """Top-cited ITALIAN decisions for an article — the IT stratum was
    starved (3/547) because IT decisions rarely rank in the overall top-N;
    this filters to language='it' so the bench can measure Italian recall
    and ranking (user report 2026-07-04)."""
    rows = graph_conn.execute(
        """SELECT ds.decision_id,
                  COALESCE((SELECT COUNT(DISTINCT ct.source_decision_id)
                            FROM citation_targets ct
                            WHERE ct.target_decision_id = ds.decision_id),0) AS cit
           FROM decision_statutes ds
           WHERE ds.statute_id = ?
           GROUP BY ds.decision_id ORDER BY cit DESC LIMIT 60""",
        (statute_id,),
    ).fetchall()
    out = []
    for did, cit in rows:
        r = fts_conn.execute(
            "SELECT regeste, language, docket_number, court FROM decisions "
            "WHERE decision_id = ? AND language='it'", (did,)).fetchone()
        if r and r[0] and len(r[0]) >= 50:
            out.append({"decision_id": did, "docket_number": r[2] or "",
                        "court": r[3] or "", "citations": cit,
                        "language": "it", "regeste": r[0][:400]})
        if len(out) >= limit:
            break
    return out


def extract_query_terms(regeste: str) -> str:
    """Extract key legal terms from regeste for query generation."""
    # Remove "Regeste\n" prefix and "Regesto" / "Regeste" headers
    text = re.sub(r"^(Regeste|Regesto|Résumé)\s*\n?\s*", "", regeste)
    # Take first sentence
    first_sentence = re.split(r"[.;]\s", text)[0]
    # Remove article references for a cleaner query hint
    cleaned = re.sub(
        r"Art\.?\s*\d+\w?\s*(Abs\.?\s*\d+\s*)?(lit\.?\s*\w\s*)?[A-Z]{2,}\d*/?[A-Z]*\s*[;,]?\s*",
        "",
        first_sentence,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned[:100].strip()
    # drop dangling article/paren fragments left by the cleanup or the cut
    # ("Einkommenspfändung (Art" -> "Einkommenspfändung")
    cleaned = re.sub(r"\s*\(\s*(Art|art|§)?\.?\s*$", "", cleaned)
    cleaned = re.sub(r"[\s,;:–-]+$", "", cleaned)
    if "(" in cleaned and ")" not in cleaned:
        cleaned = cleaned.split("(")[0].strip()
    return cleaned


# ── Scale mode (backlog #59 / Tier 1 #1): stratified 500-1000 bench ─────
# Instead of the hand-curated STATUTE_DOMAINS, derive the most-cited
# statute articles programmatically from the graph, stratified by BRANCH
# (via law code), LANGUAGE (of the labeling decision's regeste) and ERA.
# Two query types per article: a deterministic citation-form probe
# ("Art. 271 OR") and a natural-language query from the top decision's
# regeste. Relevance labels = the top-cited citing decisions, graded by
# citation rank — deterministic, no manual labeling (spot-check a sample
# before freezing). The dev/test split is hash-frozen at generation time
# so future tuning cannot leak into test.

LAW_BRANCH = {
    "OR": "zivil", "ZGB": "zivil", "ZPO": "zivil", "SCHKG": "zivil",
    "IPRG": "zivil", "URG": "zivil", "MSCHG": "zivil", "PATG": "zivil",
    "KG": "zivil", "UWG": "zivil", "PRHG": "zivil", "VVG": "zivil",
    "STGB": "straf", "STPO": "straf", "BETMG": "straf",
    "JSTPO": "straf", "STBOG": "straf",
    "BV": "oeffentlich", "VWVG": "oeffentlich", "BGG": "oeffentlich",
    "DBG": "oeffentlich", "STHG": "oeffentlich", "MWSTG": "oeffentlich",
    "AIG": "oeffentlich", "AUG": "oeffentlich", "ASYLG": "oeffentlich",
    "RPG": "oeffentlich", "USG": "oeffentlich", "EMRK": "oeffentlich",
    "SVG": "oeffentlich", "ZG": "oeffentlich",
    "ATSG": "sozialversicherung", "UVG": "sozialversicherung",
    "IVG": "sozialversicherung", "AHVG": "sozialversicherung",
    "KVG": "sozialversicherung", "BVG": "sozialversicherung",
    "AVIG": "sozialversicherung", "ELG": "sozialversicherung",
    "FAMZG": "sozialversicherung", "MVG": "sozialversicherung",
}


def top_statute_domains(graph_conn, per_branch: int):
    """Most-cited statute articles per branch, from the graph itself."""
    rows = graph_conn.execute(
        """SELECT s.statute_id, s.law_code, COUNT(DISTINCT ds.decision_id) AS n
           FROM decision_statutes ds JOIN statutes s USING (statute_id)
           WHERE s.article IS NOT NULL AND s.article != ''
           GROUP BY s.statute_id ORDER BY n DESC LIMIT 4000"""
    ).fetchall()
    buckets: dict[str, list] = {}
    for statute_id, law_code, n in rows:
        branch = LAW_BRANCH.get((law_code or "").upper())
        if not branch:
            continue
        b = buckets.setdefault(branch, [])
        if len(b) < per_branch:
            b.append((statute_id, law_code, n))
    return buckets


def _era(date_str) -> str:
    if not date_str or len(date_str) < 4 or not date_str[:4].isdigit():
        return "unknown"
    y = int(date_str[:4])
    return "pre2000" if y < 2000 else ("2000-2014" if y < 2015 else "2015+")


def _qid(query: str) -> str:
    import hashlib
    return "s" + hashlib.sha1(query.encode()).hexdigest()[:8]


def _split(qid: str) -> str:
    import hashlib
    return "dev" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 10 < 6 else "test"


def _statute_query(statute_id: str) -> str:
    """'ART.319.ABS.1.STPO' -> 'Art. 319 Abs. 1 StPO' (law code last)."""
    parts = statute_id.split(".")
    if len(parts) < 3 or parts[0] != "ART":
        return statute_id
    law = parts[-1]
    law_pretty = {"STGB": "StGB", "STPO": "StPO", "SCHKG": "SchKG",
                  "MSCHG": "MSchG", "ASYLG": "AsylG", "STHG": "StHG",
                  "MWSTG": "MWSTG", "JSTPO": "JStPO"}.get(law, law)
    mid = []
    i = 1
    while i < len(parts) - 1:
        seg = parts[i]
        if seg == "ABS" and i + 1 < len(parts) - 1:
            mid.append(f"Abs. {parts[i + 1]}"); i += 2
        elif seg == "LIT" and i + 1 < len(parts) - 1:
            mid.append(f"lit. {parts[i + 1].lower()}"); i += 2
        else:
            mid.append(seg.lower() if seg.isalpha() and len(seg) <= 3 else seg)
            i += 1
    return f"Art. {' '.join(mid)} {law_pretty}"


def run_scale(graph_conn, fts_conn, per_branch: int, out_path: Path):
    from datetime import datetime, timezone

    buckets = top_statute_domains(graph_conn, per_branch)
    queries = []
    strata: dict[str, int] = {}
    for branch, arts in sorted(buckets.items()):
        for statute_id, law_code, n_citing in arts:
            decisions = find_top_decisions_by_statute(graph_conn, fts_conn,
                                                      statute_id, limit=10)
            if len(decisions) < 3:
                continue
            relevant = [{"decision_id": d["decision_id"],
                         "grade": 3 if i < 2 else (2 if i < 5 else 1)}
                        for i, d in enumerate(decisions)]
            top = decisions[0]
            fts_row = fts_conn.execute(
                "SELECT decision_date FROM decisions WHERE decision_id=?",
                (top["decision_id"],)).fetchone()
            era = _era(fts_row[0] if fts_row else None)
            base_tags = [branch, law_code, top["language"] or "de", era,
                         "statute-keyed"]
            q1 = _statute_query(statute_id)
            queries.append({"id": _qid(q1), "query": q1, "split": _split(_qid(q1)),
                            "tags": base_tags + ["citation-form"],
                            "statute": statute_id, "relevant": relevant})
            strata[branch] = strata.get(branch, 0) + 1
            strata[era] = strata.get(era, 0) + 1
            # natural-language variant per language present in the label set
            # (cross-lingual coverage: fr/it regestes generate fr/it queries
            # against the same graded labels)
            seen_langs = set()
            for d in decisions:
                lang = d["language"] or "de"
                if lang in seen_langs:
                    continue
                seen_langs.add(lang)
                hint = extract_query_terms(d["regeste"])
                if len(hint) >= 15 and len(hint.split()) >= 2:
                    # The decision whose regeste PRODUCED the query is
                    # relevant by construction — grade 3 regardless of its
                    # citation rank (spot-check finding: article-level
                    # labels alone under-grade the query's source).
                    rel_q = [dict(r) for r in relevant]
                    for r in rel_q:
                        if r["decision_id"] == d["decision_id"]:
                            r["grade"] = 3
                    queries.append({"id": _qid(hint), "query": hint,
                                    "split": _split(_qid(hint)),
                                    "tags": [branch, law_code, lang, era,
                                             "statute-keyed", "natural-language"],
                                    "statute": statute_id,
                                    "source_decision": d["decision_id"],
                                    "relevant": rel_q})
                    strata[lang] = strata.get(lang, 0) + 1
                if len(seen_langs) >= 3:
                    break

            # IT-guaranteed query: Italian decisions rarely rank in the
            # overall top-N, so explicitly pull top IT decisions for this
            # article and emit an Italian NL query graded against them
            # (user report 2026-07-04: IT under-surfaced).
            it_decs = find_top_it_decisions_by_statute(graph_conn, fts_conn,
                                                       statute_id, limit=8)
            if len(it_decs) >= 3:
                it_rel = [{"decision_id": d["decision_id"],
                           "grade": 3 if i < 2 else (2 if i < 5 else 1)}
                          for i, d in enumerate(it_decs)]
                it_hint = extract_query_terms(it_decs[0]["regeste"])
                if len(it_hint) >= 15 and len(it_hint.split()) >= 2:
                    qid = _qid(it_hint + "|it")
                    queries.append({"id": qid, "query": it_hint,
                                    "split": _split(qid),
                                    "tags": [branch, law_code, "it", era,
                                             "statute-keyed", "natural-language",
                                             "it-boost"],
                                    "statute": statute_id,
                                    "source_decision": it_decs[0]["decision_id"],
                                    "relevant": it_rel})
                    strata["it"] = strata.get("it", 0) + 1

    payload = {
        "version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": "Stratified statute-keyed candidates (graph-derived labels); "
                       "dev/test split hash-frozen at generation.",
        "strata": strata,
        "total": len(queries),
        "queries": queries,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    print(f"scale mode: {len(queries)} queries "
          f"(dev={sum(1 for q in queries if q['split']=='dev')}, "
          f"test={sum(1 for q in queries if q['split']=='test')})")
    print("strata:", dict(sorted(strata.items())))


def main():
    parser = argparse.ArgumentParser(description="Generate golden set expansion candidates")
    parser.add_argument("--graph-db", type=Path, required=True)
    parser.add_argument("--fts-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/golden_candidates.json"))
    parser.add_argument("--top-n", type=int, default=3, help="Top N decisions per statute")
    parser.add_argument("--scale", type=int, default=0, metavar="PER_BRANCH",
                        help="scale mode: derive top PER_BRANCH articles per branch "
                             "from the graph and emit the stratified v3 candidate set")
    args = parser.parse_args()

    if args.scale:
        graph_conn = sqlite3.connect(f"file:{args.graph_db}?mode=ro&immutable=1", uri=True)
        fts_conn = sqlite3.connect(f"file:{args.fts_db}?mode=ro&immutable=1", uri=True)
        run_scale(graph_conn, fts_conn, args.scale, args.output)
        graph_conn.close()
        fts_conn.close()
        return

    graph_conn = sqlite3.connect(str(args.graph_db))
    fts_conn = sqlite3.connect(str(args.fts_db))

    candidates = []
    for statute_id, tag, description in STATUTE_DOMAINS:
        decisions = find_top_decisions_by_statute(
            graph_conn, fts_conn, statute_id, limit=args.top_n * 2,
        )
        top = decisions[:args.top_n]
        if not top:
            print(f"  SKIP {statute_id} — no decisions with regeste")
            continue

        # Generate one candidate query per statute
        best = top[0]
        query_hint = extract_query_terms(best["regeste"])

        candidates.append({
            "statute": statute_id,
            "tag": tag,
            "description": description,
            "suggested_query": f"{statute_id.replace('ART.','Art. ').replace('.',' ',1)} {query_hint}".strip(),
            "relevant_decisions": [
                {
                    "decision_id": d["decision_id"],
                    "docket_number": d["docket_number"],
                    "citations": d["citations"],
                    "language": d["language"],
                    "grade": 3 if i == 0 else 2,
                    "regeste_excerpt": d["regeste"][:200],
                }
                for i, d in enumerate(top)
            ],
            "status": "candidate",
        })
        print(f"  {statute_id:20s} → {len(top)} decisions (top: {best['docket_number']}, {best['citations']} citations)")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates, "total": len(candidates)}, f, indent=2, ensure_ascii=False)

    print(f"\nGenerated {len(candidates)} candidate queries → {args.output}")
    graph_conn.close()
    fts_conn.close()


if __name__ == "__main__":
    main()
