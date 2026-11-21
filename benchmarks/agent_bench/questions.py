"""The question set: schema validation, import, and live verification of gold ids.

Every gold or forbidden decision_id must resolve through the server's
own `cite` tool: ids are copied from tool output, never typed (AGENTS.md,
"Do not construct legal citation strings"). `--verify` re-checks that
against the live corpus and fails on any id that no longer resolves.

Usage:
    python3 -m benchmarks.agent_bench.questions --check
    python3 -m benchmarks.agent_bench.questions --verify
    python3 -m benchmarks.agent_bench.questions --build drafts/*.jsonl   # merge + import RAG bench
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = HERE / "questions_v0.jsonl"
RAG_BENCH = HERE.parent / "swiss_legal_rag_bench" / "questions.jsonl"

TYPES = {"doctrine", "praxisaenderung", "temporal", "cross_lingual", "pinpoint"}
LANGUAGES = {"de", "fr", "it"}
REVIEW_STATUSES = {"draft", "imported", "reviewed"}


def validate(q: dict) -> list[str]:
    errs = []
    for key in ("id", "type", "language", "question", "rubric", "gold", "forbidden", "provenance", "review"):
        if key not in q:
            errs.append(f"missing {key}")
    if errs:
        return errs
    if q["type"] not in TYPES:
        errs.append(f"type {q['type']!r}")
    if q["language"] not in LANGUAGES:
        errs.append(f"language {q['language']!r}")
    if not q["rubric"] or not any(p.get("critical") for p in q["rubric"]):
        errs.append("rubric needs at least one critical point")
    for p in q["rubric"]:
        if not isinstance(p.get("point"), str) or not p["point"].strip():
            errs.append("empty rubric point")
    for g in q["gold"].get("decisions", []):
        if not g.get("decision_id"):
            errs.append("gold decision without decision_id")
    for s in q["gold"].get("statutes", []):
        if not s.get("law") or not s.get("article"):
            errs.append("gold statute without law/article")
    for f in q["forbidden"]:
        if not f.get("decision_id"):
            errs.append("forbidden entry without decision_id")
    review = q["review"]
    if review.get("status") not in REVIEW_STATUSES:
        errs.append(f"review.status {review.get('status')!r}")
    if review.get("status") == "reviewed" and not (review.get("reviewed_by") and review.get("reviewed_at")):
        # A review names the person who did it, not an organisation label.
        errs.append("reviewed without reviewed_by / reviewed_at")
    return errs


def load_questions(path: Path = DEFAULT_QUESTIONS) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    problems = {}
    for q in rows:
        errs = validate(q)
        if errs:
            problems[q.get("id", "?")] = errs
    ids = [q.get("id") for q in rows]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems["duplicates"] = sorted(dupes)
    if problems:
        raise ValueError(f"invalid question set {path.name}: {problems}")
    return rows


def import_rag_bench(path: Path = RAG_BENCH) -> list[dict]:
    """The 30 Swiss Legal RAG Bench questions, as 'doctrine' questions.
    Their reference answer becomes the single critical rubric point and
    their annotated evidence the gold set (supporting, not must-cite:
    the RAG bench counts any annotated passage as a retrieval hit)."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        ev = r.get("evidence") or {}
        out.append({
            "id": f"rb-{r['id'].split('-')[-1]}",
            "type": "doctrine",
            "language": r["language"],
            "question": r["question"],
            "facts_date": None,
            "rubric": [{"point": r["reference_answer"], "critical": True}],
            "gold": {
                "decisions": [{"decision_id": d, "role": "supporting", "must_cite": False, "pinpoint": None}
                              for d in ev.get("decisions", [])],
                "statutes": [{"law": s["law_code"], "aliases": [s["law_code"]], "article": str(s["article"]), "as_of": None}
                             for s in ev.get("statutes", [])],
            },
            "forbidden": [],
            "provenance": f"imported from swiss_legal_rag_bench/questions.jsonl ({r['id']}, {r.get('legal_area', '')}, {r.get('claim_type', '')})",
            "review": {"status": "imported", "prepared_by": "swiss_legal_rag_bench v0.1", "reviewed_by": None, "reviewed_at": None},
        })
    return out


def verify_ids(rows: list[dict], endpoint: str | None = None) -> dict[str, str]:
    """decision_id -> problem, for every gold/forbidden id `cite` does not
    resolve to itself. Empty dict means every id is real."""
    from .mcp_http import DEFAULT_ENDPOINT, call_tool
    ids = sorted({g["decision_id"] for q in rows for g in q["gold"]["decisions"]}
                 | {f["decision_id"] for q in rows for f in q["forbidden"]})
    bad = {}
    for did in ids:
        try:
            r = call_tool("cite", {"reference": did}, endpoint=endpoint or DEFAULT_ENDPOINT)
        except Exception as exc:  # noqa: BLE001
            bad[did] = f"cite failed: {exc}"
            continue
        if r.get("exists") is not True:
            bad[did] = "does not exist"
        elif r.get("decision_id") != did:
            bad[did] = f"resolves to {r.get('decision_id')}"
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Question-set tools")
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--check", action="store_true", help="schema check (offline)")
    ap.add_argument("--verify", action="store_true", help="resolve every gold id with live `cite`")
    ap.add_argument("--build", nargs="*", type=Path, help="merge these draft files + the RAG bench import into --questions")
    args = ap.parse_args(argv)
    if args.build is not None:
        rows = []
        for path in args.build:
            rows += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows += import_rag_bench()
        args.questions.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in rows), encoding="utf-8")
    rows = load_questions(args.questions)
    by_type = {}
    for q in rows:
        by_type[q["type"]] = by_type.get(q["type"], 0) + 1
    print(f"{len(rows)} questions OK: {by_type}")
    if args.verify:
        bad = verify_ids(rows)
        for did, problem in bad.items():
            print(f"  {did}: {problem}", file=sys.stderr)
        print("all gold ids resolve" if not bad else f"{len(bad)} gold ids do not resolve")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
