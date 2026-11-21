"""Resolve the authoring inputs in src/ into the fixture, from OpenCaseLaw tool output.

    python -m benchmarks.record_bench.build records      # fixture/records/*.json
    python -m benchmarks.record_bench.build answer-key   # fixture/answer_key/*.json
    python -m benchmarks.record_bench.build prune        # drop snapshots nothing refers to

Live: every lookup goes through sources.fetch, which stores the tool result
in fixture/sources/. Re-running reuses the stored snapshots unless
--refresh is given. Every legal field written to the fixture is copied
from a snapshot; nothing here types a citation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import lawtext, sources

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
RECORDS_DIR = FIXTURE / "records"
SCHEMA_VERSION = "0.1"

# Which triggers watch which kind of basis entry (spec, trigger table).
WATCH_TRIGGERS = {
    "statute": ["T1", "T2", "T6"],
    "cantonal_statute": ["T8"],
    "decision": ["T3", "T4", "T5"],
    "practice": ["T7"],
    "foreign": ["T14"],
    "fact": ["T10"],
    "commitment": ["T12"],
    "restriction": ["T13"],
    "record": ["T11", "T14"],
}


class BuildError(RuntimeError):
    pass


def _snap_time(snap: dict) -> str:
    return snap["fetched_at"]


def _attest(draft: str, refresh: bool) -> tuple[str, dict]:
    """attest_response on a reference built from copied fields (verifier input only)."""
    return sources.fetch("attest_response", {"draft_text": draft}, refresh=refresh)


def _attest_passes(result: dict) -> bool:
    led = result.get("ledger", {})
    st, ci = led.get("statutes", {}), led.get("citations", {})
    return bool(result.get("ok")) and st.get("missing_article", 0) == 0 and st.get("unknown_law", 0) == 0 \
        and ci.get("unresolved", 0) == 0


def resolve_statute(entry: dict, decided_at: str, refresh: bool) -> dict:
    args = dict(entry["get_law"])
    if "abbreviation" in args and "as_of" not in args:
        args["as_of"] = decided_at
    k, snap = sources.fetch("get_law", args, refresh=refresh)
    md = sources.text_of(snap)
    p = lawtext.parse(md)
    want = str(args["article"])
    if p["article"] != want:
        raise BuildError(f"{entry['id']}: get_law {args} returned article {p['article']!r}")
    para = entry.get("paragraph")
    cantonal = "canton" in args
    if p["structured"]:
        if para is None:
            text, status = p["body"], "verbatim"
        elif para in p["paragraphs"]:
            text, status = p["paragraphs"][para], "verbatim"
        else:
            raise BuildError(f"{entry['id']}: paragraph {para} not in {k}: {list(p['paragraphs'])}")
    else:
        # Unstructured edition (Fedlex PDF before 2021, LexFind): the tool warns not to quote it.
        if para is not None and not lawtext.paragraph_in_excerpt(p["body"], para):
            raise BuildError(f"{entry['id']}: paragraph {para} not visible in unstructured excerpt {k}")
        text, status = None, "unstructured_not_quoted"
    out = {
        "id": entry["id"],
        "kind": "cantonal_statute" if cantonal else "statute",
        "law": p["law"],
        "sr_number": p["sr_number"] or args.get("sr_number"),
        "act_title": p["act_title"],
        "jurisdiction": p["jurisdiction"],
        "article": p["article"],
        "article_label": p["article_label"],
        "paragraph": para,
        "as_of": args.get("as_of", decided_at),
        "edition": p["edition"],
        "act_in_force": p["act_in_force"],
        "text": text,
        "text_status": status,
        "source_url": p["source_url"],
        "source_snapshot": k,
        "proposition": entry["proposition"],
    }
    # Verification. attest_response checks the reference against the statute mirror
    # (current law). For an act repealed since, or cantonal law the parser does not
    # audit, the check is that get_law returned the article in the edition applicable
    # on the as_of date.
    edition_ok = {"tool": "get_law", "ok": True, "at": _snap_time(snap), "as_of": out["as_of"],
                  "edition": p["edition"], "snapshot": k}
    if cantonal or p["repealed"]:
        out["verification"] = edition_ok
        return out
    ref = f"Art. {p['article']}" + (f" Abs. {para}" if para else "") + f" {p['law']}"
    ak, asnap = _attest(ref, refresh)
    if _attest_passes(asnap["result"]):
        out["verification"] = {"tool": "attest_response", "ok": True, "at": _snap_time(asnap), "snapshot": ak,
                               "edition_check": edition_ok}
    else:
        raise BuildError(f"{entry['id']}: attest_response failed for {ref!r}: {asnap['result'].get('issues')}")
    return out


def resolve_decision(entry: dict, refresh: bool) -> dict:
    c = entry["cite"]
    args = {"reference": c["decision_id"]}
    if c.get("pinpoint"):
        args["pinpoint"] = c["pinpoint"]
    k, snap = sources.fetch("cite", args, refresh=refresh)
    r = snap["result"]
    if not r.get("exists"):
        raise BuildError(f"{entry['id']}: cite {args} does not resolve")
    if c.get("pinpoint") and r.get("pinpoint_status") != "verified":
        raise BuildError(f"{entry['id']}: pinpoint {c['pinpoint']} status {r.get('pinpoint_status')}")
    ak, asnap = _attest(r["citation_string"], refresh)
    if not _attest_passes(asnap["result"]):
        raise BuildError(f"{entry['id']}: attest_response failed for {r['citation_string']!r}")
    return {
        "id": entry["id"],
        "kind": "decision",
        "decision_id": r["decision_id"],
        "citation": r["citation_string"],
        "decision_date": r.get("decision_date"),
        "pinpoint": r.get("pinpoint"),
        "pinpoint_status": r.get("pinpoint_status"),
        "url": r.get("canonical_url"),
        "source_snapshot": k,
        "proposition": entry["proposition"],
        "verification": {"tool": "attest_response", "ok": True, "at": _snap_time(asnap), "snapshot": ak},
    }


def resolve_practice(entry: dict, refresh: bool) -> dict:
    q = entry["practice"]
    k, snap = sources.fetch("search_practice", {"query": q["query"]}, refresh=refresh)
    md = sources.text_of(snap)
    lines = md.splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == f"doc_id: {q['doc_id']}"), None)
    if idx is None:
        raise BuildError(f"{entry['id']}: doc_id {q['doc_id']} not in search_practice output")
    title = re.sub(r"^\d+\. ", "", lines[idx - 1].strip())
    url = next((m.group(1) for ln in lines[idx: idx + 8] for m in [re.search(r"\]\((https?://\S+)\)", ln)] if m), None)
    return {
        "id": entry["id"],
        "kind": "practice",
        "doc_id": q["doc_id"],
        "title": title,
        "url": url,
        "source_snapshot": k,
        "proposition": entry["proposition"],
        "verification": {"tool": "search_practice", "ok": True, "at": _snap_time(snap), "snapshot": k},
    }


def resolve_foreign(entry: dict) -> dict:
    f = entry["foreign"]
    return {
        "id": entry["id"],
        "kind": "foreign",
        "jurisdiction": f["jurisdiction"],
        "description": f["description"],
        "source": f["source"],
        "proposition": entry["proposition"],
        "watched": False,
        "verification": {"tool": None, "ok": None,
                         "note": "Foreign law is outside the OpenCaseLaw corpus. No citation is stored; "
                                 "the entry points to the company's own advice and is watched only by the review date (T14)."},
    }


def _watches(rec: dict, law: list[dict]) -> list[dict]:
    w = [{"basis_id": e["id"], "kind": e["kind"], "triggers": WATCH_TRIGGERS[e["kind"]]} for e in law]
    w += [{"basis_id": f["id"], "kind": "fact", "source": f["source"], "triggers": WATCH_TRIGGERS["fact"]}
          for f in rec["facts"]]
    w += [{"basis_id": c["id"], "kind": "commitment", "source": c["source"], "test_date": c["test_date"],
           "triggers": WATCH_TRIGGERS["commitment"]} for c in rec["commitments"]]
    w += [{"basis_id": f"r{i}", "kind": "restriction", "text": t, "triggers": WATCH_TRIGGERS["restriction"]}
          for i, t in enumerate(rec["restrictions"], 1)]
    w.append({"basis_id": "record", "kind": "record", "triggers": WATCH_TRIGGERS["record"]})
    return w


def resolve_record(rec: dict, refresh: bool = False) -> dict:
    law = []
    for e in rec["law"]:
        if "get_law" in e:
            law.append(resolve_statute(e, rec["decided_at"], refresh))
        elif "cite" in e:
            law.append(resolve_decision(e, refresh))
        elif "practice" in e:
            law.append(resolve_practice(e, refresh))
        elif "foreign" in e:
            law.append(resolve_foreign(e))
        else:
            raise BuildError(f"{rec['id']}/{e['id']}: unknown lookup")
    return {
        "id": rec["id"],
        "schema_version": SCHEMA_VERSION,
        "decision": {"type": rec["type"], "text": rec["text"]},
        "purpose": rec["purpose"],
        "decided_by": rec["decided_by"],
        "decided_at": rec["decided_at"],
        "minutes": rec["minutes"],
        "owner": rec["owner"],
        "fallback_owner": rec["fallback_owner"],
        "restrictions": rec["restrictions"],
        "basis": {"facts": rec["facts"], "law": law, "commitments": rec["commitments"]},
        "objections": rec["objections"],
        "review": rec["review"],
        "watches": _watches(rec, law),
        "history": [],
    }


def build_records(refresh: bool = False, only: list[str] | None = None) -> list[dict]:
    from .src.records import RECORDS
    todo = [r for r in RECORDS if not only or r["id"] in only]
    errors, built = [], []

    def one(rec):
        try:
            return resolve_record(rec, refresh)
        except BuildError as exc:
            errors.append(f"{rec['id']}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        built = [b for b in pool.map(one, todo) if b]
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    for b in built:
        (RECORDS_DIR / f"{b['id']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for e in errors:
        print("ERROR", e, file=sys.stderr)
    print(f"{len(built)} records written, {len(errors)} errors")
    return built


def referenced_snapshots() -> set[str]:
    refs: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("source_snapshot", "snapshot") and isinstance(v, str):
                    refs.add(v)
                elif k == "source_snapshots" and isinstance(v, list):
                    refs.update(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for p in list(RECORDS_DIR.glob("*.json")) + list((FIXTURE / "answer_key").glob("*.json")):
        walk(json.loads(p.read_text(encoding="utf-8")))
    return refs


def prune() -> None:
    refs = referenced_snapshots()
    dropped = 0
    for p in sources.SOURCES.glob("*.json"):
        if p.stem not in refs:
            p.unlink()
            dropped += 1
    print(f"kept {len(refs)} snapshots, dropped {dropped}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["records", "answer-key", "prune"])
    ap.add_argument("--refresh", action="store_true", help="re-fetch instead of using stored snapshots")
    ap.add_argument("--only", nargs="*", help="record ids")
    a = ap.parse_args(argv)
    if a.what == "records":
        build_records(a.refresh, a.only)
    elif a.what == "answer-key":
        from .answer_key_build import build_answer_key
        build_answer_key(a.refresh)
    else:
        prune()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
