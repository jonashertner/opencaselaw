"""Load and validate the record_bench fixture offline.

    python -m benchmarks.record_bench.fixture --check

Checks the company, the 25 records (Ruth Record schema 0.1) and the answer
key, and confirms that every legal field copied into them appears in the
stored OpenCaseLaw snapshot it names. No network access.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import lawtext, sources

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
DOCS = FIXTURE / "docs"
RECORDS_DIR = FIXTURE / "records"
KEY_PATH = FIXTURE / "answer_key" / "events.json"

DECISION_TYPES = {"board.dividend", "board.conflict_transaction", "board.capital_loss", "board.ai_deployment",
                  "product.market_approval"}
TRIGGERS = {f"T{i}" for i in range(1, 15)}
ORIGINS = {"real", "planted", "calendar"}
ACTIONS = {"push", "log", "none"}
REVIEW_STATUSES = {"draft", "reviewed"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A typed citation in free text would bypass the copy-from-tool rule.
TYPED_CITATION = re.compile(r"\b(?:Art\.|§)\s*\d|\b(?:BGE|ATF|DTF)\s+\d+\s+[IVX]+|\bSR\s+\d")
RECORD_KEYS = ["id", "schema_version", "decision", "purpose", "decided_by", "decided_at", "minutes", "owner",
               "fallback_owner", "restrictions", "basis", "objections", "review", "watches", "history"]
LAW_KEYS = {
    "statute": ["law", "sr_number", "article", "article_label", "paragraph", "as_of", "edition", "text", "text_status",
                "source_url", "source_snapshot"],
    "cantonal_statute": ["law", "article", "article_label", "paragraph", "text", "text_status", "source_url", "source_snapshot"],
    "decision": ["decision_id", "citation", "decision_date", "pinpoint", "pinpoint_status", "url", "source_snapshot"],
    "practice": ["doc_id", "title", "url", "source_snapshot"],
    "foreign": ["jurisdiction", "description", "source", "watched"],
}


def load_company() -> dict:
    return json.loads((FIXTURE / "company.json").read_text(encoding="utf-8"))


def load_records() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECORDS_DIR.glob("*.json"))]


def load_key() -> dict:
    return json.loads(KEY_PATH.read_text(encoding="utf-8"))


def doc_path(dms: str) -> Path:
    """dms://board/x.md#3 -> fixture/docs/board/x.md"""
    return DOCS / dms.removeprefix("dms://").split("#")[0]


def _snap(key: str, errors: list[str], where: str) -> dict | None:
    if not key or not (sources.SOURCES / f"{key}.json").exists():
        errors.append(f"{where}: snapshot {key!r} missing")
        return None
    return sources.load(key)


def _attest_ok(snap: dict) -> bool:
    r = snap["result"]
    led = r.get("ledger", {})
    return bool(r.get("ok")) and led.get("statutes", {}).get("missing_article", 0) == 0 \
        and led.get("citations", {}).get("unresolved", 0) == 0


def check_law_entry(rec: dict, e: dict, errors: list[str]) -> None:
    where = f"{rec['id']}/{e.get('id')}"
    kind = e.get("kind")
    if kind not in LAW_KEYS:
        errors.append(f"{where}: unknown kind {kind!r}")
        return
    for k in LAW_KEYS[kind] + ["id", "proposition", "verification"]:
        if k not in e:
            errors.append(f"{where}: missing {k}")
    if not e.get("proposition") or TYPED_CITATION.search(e["proposition"]):
        errors.append(f"{where}: proposition empty or contains a typed citation")
    v = e.get("verification") or {}
    if kind == "foreign":
        if e.get("watched") is not False or any(k in e for k in ("citation", "article", "decision_id")):
            errors.append(f"{where}: foreign entry must be unwatched and carry no citation fields")
        if not doc_path(e.get("source", "")).exists():
            errors.append(f"{where}: source document {e.get('source')} missing")
        return
    if v.get("ok") is not True:
        errors.append(f"{where}: basis.law entry stored without passing verification")
    snap = _snap(e.get("source_snapshot"), errors, where)
    if snap is None:
        return
    if v.get("snapshot"):
        vs = _snap(v["snapshot"], errors, where)
        if vs and v.get("tool") == "attest_response" and not _attest_ok(vs):
            errors.append(f"{where}: attest_response snapshot does not pass")
        if vs and v.get("tool") != vs["tool"]:
            errors.append(f"{where}: verification tool {v.get('tool')} != snapshot tool {vs['tool']}")
    if kind in ("statute", "cantonal_statute"):
        if snap["tool"] != "get_law":
            errors.append(f"{where}: statute snapshot is not get_law")
            return
        p = lawtext.parse(sources.text_of(snap))
        for f in ("law", "article", "article_label", "source_url") + (("sr_number", "edition") if kind == "statute" else ()):
            if e.get(f) != p.get(f):
                errors.append(f"{where}: {f}={e.get(f)!r} not as in snapshot ({p.get(f)!r})")
        if kind == "statute" and e.get("as_of") != snap["args"].get("as_of"):
            errors.append(f"{where}: as_of {e.get('as_of')} differs from the get_law call")
        if e.get("text") is not None:
            want = p["body"] if e.get("paragraph") is None else p["paragraphs"].get(e["paragraph"])
            if e["text"] != want:
                errors.append(f"{where}: text is not the verbatim paragraph from the snapshot")
        elif e.get("text_status") != "unstructured_not_quoted":
            errors.append(f"{where}: text missing without unstructured status")
    elif kind == "decision":
        r = snap["result"]
        if snap["tool"] != "cite" or not r.get("exists"):
            errors.append(f"{where}: decision snapshot is not a resolving cite")
            return
        for f, g in (("decision_id", "decision_id"), ("citation", "citation_string"), ("pinpoint", "pinpoint"),
                     ("decision_date", "decision_date"), ("url", "canonical_url")):
            if e.get(f) != r.get(g):
                errors.append(f"{where}: {f}={e.get(f)!r} not as in cite output ({r.get(g)!r})")
        if e.get("pinpoint") and r.get("pinpoint_status") != "verified":
            errors.append(f"{where}: pinpoint not verified")
    elif kind == "practice":
        t = sources.text_of(snap)
        if f"doc_id: {e.get('doc_id')}" not in t or (e.get("title") or "#") not in t:
            errors.append(f"{where}: practice doc_id or title not in search_practice output")


def check_record(rec: dict, people: set[str], errors: list[str]) -> None:
    rid = rec.get("id", "?")
    for k in RECORD_KEYS:
        if k not in rec:
            errors.append(f"{rid}: missing {k}")
    if errors and any(e.startswith(f"{rid}: missing") for e in errors):
        return
    if rec["schema_version"] != "0.1":
        errors.append(f"{rid}: schema_version {rec['schema_version']}")
    if rec["decision"].get("type") not in DECISION_TYPES or not rec["decision"].get("text"):
        errors.append(f"{rid}: bad decision")
    if not DATE.match(rec["decided_at"]) or not DATE.match(rec["review"].get("date", "")):
        errors.append(f"{rid}: bad dates")
    if rec["review"]["date"] <= rec["decided_at"] or not rec["review"].get("reopen_if"):
        errors.append(f"{rid}: review must follow the decision and name reopen_if")
    for role in ("owner", "fallback_owner"):
        if rec[role].get("person") not in people:
            errors.append(f"{rid}: {role} {rec[role].get('person')} not a company person")
    if rec["owner"]["person"] == rec["fallback_owner"]["person"]:
        errors.append(f"{rid}: owner and fallback owner are the same person")
    if not doc_path(rec["minutes"]).exists():
        errors.append(f"{rid}: minutes {rec['minutes']} missing")
    for t in [rec["decision"]["text"], rec["purpose"]] + [f["text"] for f in rec["basis"]["facts"]]:
        if TYPED_CITATION.search(t):
            errors.append(f"{rid}: typed citation in free text: {t[:60]}")
    ids = [x["id"] for x in rec["basis"]["facts"] + rec["basis"]["law"] + rec["basis"]["commitments"]]
    if len(ids) != len(set(ids)):
        errors.append(f"{rid}: duplicate basis ids")
    for f in rec["basis"]["facts"]:
        if not re.match(r"^f\d+$", f["id"]) or not DATE.match(f.get("as_of", "")) or not doc_path(f["source"]).exists():
            errors.append(f"{rid}/{f['id']}: bad fact (id, as_of or source document)")
    for c in rec["basis"]["commitments"]:
        if not re.match(r"^c\d+$", c["id"]) or not DATE.match(c.get("test_date", "")) or not doc_path(c["source"]).exists():
            errors.append(f"{rid}/{c['id']}: bad commitment (id, test_date or source document)")
    for e in rec["basis"]["law"]:
        if not re.match(r"^l\d+$", e.get("id", "")):
            errors.append(f"{rid}: bad law id {e.get('id')}")
        check_law_entry(rec, e, errors)
    for o in rec["objections"]:
        if not all(o.get(k) for k in ("by", "text", "answer")):
            errors.append(f"{rid}: objection needs by, text and answer")
    watched = {w["basis_id"] for w in rec["watches"]}
    if not set(ids) <= watched:
        errors.append(f"{rid}: watches do not cover every basis entry")


def basis_ids(rec: dict) -> set[str]:
    b = rec["basis"]
    return {x["id"] for x in b["facts"] + b["law"] + b["commitments"]} | \
        {f"r{i}" for i in range(1, len(rec["restrictions"]) + 1)} | {"record"}


def check_review(rv: dict, where: str, errors: list[str]) -> None:
    if rv.get("status") not in REVIEW_STATUSES:
        errors.append(f"{where}: review.status {rv.get('status')!r}")
    elif rv["status"] == "reviewed" and not (rv.get("reviewed_by") and rv.get("reviewed_at")):
        errors.append(f"{where}: reviewed without a named reviewer and date")
    elif rv["status"] == "draft" and (rv.get("reviewed_by") or rv.get("reviewed_at")):
        errors.append(f"{where}: draft carries a reviewer")


def check_key(key: dict, records: dict[str, dict], errors: list[str]) -> None:
    start, end = key["replay"]["start"], key["replay"]["end"]
    seen = set()
    for ev in key["events"]:
        eid = ev.get("id", "?")
        if eid in seen:
            errors.append(f"{eid}: duplicate id")
        seen.add(eid)
        if ev.get("origin") not in ORIGINS or ev.get("trigger") not in TRIGGERS:
            errors.append(f"{eid}: bad origin or trigger")
        if not DATE.match(ev.get("published_at", "")) or not start <= ev["published_at"] <= end:
            errors.append(f"{eid}: published_at outside the replay")
        if ev["origin"] == "calendar" and not re.match(r"^cal:rec_\w+:(record|c\d+):T1[24]$", eid):
            errors.append(f"{eid}: calendar id format")
        if ev["origin"] != "calendar" and not re.match(r"^ev_\d{4}$", eid):
            errors.append(f"{eid}: event id format")
        check_review(ev.get("review", {}), eid, errors)
        ch = ev.get("change", {})
        if ev["origin"] == "planted":
            synthetic = [v for k, v in ch.items() if k in ("new_text", "summary") and isinstance(v, str)]
            if ch.get("kind") in ("statute_amendment", "court_decision") and not all(s.startswith("[SYNTHETIC") for s in synthetic):
                errors.append(f"{eid}: planted legal text not marked SYNTHETIC")
            if ch.get("kind") == "statute_amendment":
                cf = ch.get("copied_from", {})
                if "record_id" in cf:
                    src = next((x for x in records[cf["record_id"]]["basis"]["law"] if x["id"] == cf["basis_id"]), {})
                    if any(ch.get(f) != src.get(f) for f in ("law", "sr_number", "article")):
                        errors.append(f"{eid}: planted change does not copy its target's law fields")
                elif _snap(cf.get("source_snapshot"), errors, eid) is None:
                    pass
            if ch.get("kind") == "court_decision" and not str(ch.get("decision_id")).startswith("SYNTH_"):
                errors.append(f"{eid}: synthetic decision id must start with SYNTH_")
        if ev["origin"] == "real":
            keys = [ch["source_snapshot"]] if "source_snapshot" in ch else ch.get("source_snapshots", [])
            snaps = [s for s in (_snap(k, errors, eid) for k in keys) if s]
            if not snaps:
                errors.append(f"{eid}: real event without a stored snapshot")
            text = "\n".join(sources.text_of(s) for s in snaps)
            for f in ("footnote", "citation", "label", "regeste"):
                if ch.get(f) and ch[f] not in text and json.dumps(ch[f], ensure_ascii=False)[1:-1] not in text:
                    errors.append(f"{eid}: {f} not found verbatim in its snapshot")
            for p in (ch.get("current_paragraphs") or {}).values():
                if p not in text:
                    errors.append(f"{eid}: current paragraph not in snapshot")
        inj = ev.get("injection")
        if inj:
            body = ch.get("text", "")
            if inj.get("canary") and inj["canary"] not in body:
                errors.append(f"{eid}: canary not in document")
            for r in inj.get("forbidden_recipients", []):
                if r not in body:
                    errors.append(f"{eid}: forbidden recipient not in document")
        if not ev.get("expected"):
            errors.append(f"{eid}: no expected outcome")
        recs_seen = set()
        for x in ev.get("expected", []):
            rid = x.get("record_id")
            if rid not in records:
                errors.append(f"{eid}: unknown record {rid}")
                continue
            if rid in recs_seen:
                errors.append(f"{eid}: two expectations for {rid}")
            recs_seen.add(rid)
            a, sev = x.get("action"), x.get("severity")
            if a not in ACTIONS or (a == "push" and sev not in ("high", "medium")) or \
                    (a == "log" and sev != "low") or (a == "none" and sev is not None):
                errors.append(f"{eid}/{rid}: action {a} with severity {sev}")
            if a != "none" and records[rid]["decided_at"] > ev["published_at"]:
                errors.append(f"{eid}/{rid}: expects {a} before the record exists")
            if a != "none" and (not x.get("basis_ids") or not set(x["basis_ids"]) <= basis_ids(records[rid])):
                errors.append(f"{eid}/{rid}: basis ids {x.get('basis_ids')} not in the record")
            if not x.get("rationale"):
                errors.append(f"{eid}/{rid}: rationale missing")


def check(verbose: bool = True) -> list[str]:
    errors: list[str] = []
    company = load_company()
    people = {p["id"] for p in company["people"]}
    records = load_records()
    if len(records) != 25:
        errors.append(f"expected 25 records, found {len(records)}")
    for r in records:
        check_record(r, people, errors)
    by_id = {r["id"]: r for r in records}
    key = load_key()
    check_key(key, by_id, errors)
    if verbose:
        n_planted = sum(e["origin"] == "planted" for e in key["events"])
        print(f"{len(records)} records, {len(key['events'])} events ({n_planted} planted): "
              f"{'OK' if not errors else f'{len(errors)} errors'}")
        for e in errors:
            print("  ", e)
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    return 1 if check() else 0


if __name__ == "__main__":
    sys.exit(main())
