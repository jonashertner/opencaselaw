"""Functional probe of every public MCP tool against a running server.

`make smoke` checks four HTTP endpoints return 200. This checks that all 42
tools actually ANSWER — with realistic arguments, and with ids harvested from
real searches rather than invented, so a broken join surfaces as a broken join
instead of as a plausible "not found".

    .venv/bin/python scripts/tool_surface_check.py [--base URL] [--json OUT]

Classification per call:
    FAIL     JSON-RPC error, an "Error:"/traceback body, or a transport failure
    EMPTY    answered, but with nothing in it where content was expected
    OK       answered with substance

EMPTY is reported separately from FAIL on purpose: for some tools it is the
honest answer (no scholarship cites this decision), for others it means a
silently broken path. The run prints both so a human decides.

Exit code is 1 if anything FAILed, else 0.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.request

DEFAULT_BASE = "https://mcp.opencaselaw.ch"
UA = "opencaselaw-probe/1.0"
# Bodies that are an error dressed as prose rather than a real answer.
_ERR_RE = re.compile(
    r"^\s*(Error:|Traceback|Internal Server Error)|cannot access local variable",
    re.IGNORECASE)


def call(base: str, tool: str, args: dict, timeout: int = 180):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/mcp", data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "User-Agent": UA})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:]), time.time() - t0
    return json.loads(raw), time.time() - t0


def body_text(resp: dict) -> str:
    res = resp.get("result") or {}
    return "".join(c.get("text", "") for c in (res.get("content") or []))


def _json_error(text: str) -> str | None:
    """The "error" of an answer whose whole body is a JSON object carrying one.

    The server answers an aborted call with {"error": "server_timeout", ...}:
    ~680 characters, so until 2026-10-10 it passed as OK (get_doctrine timed
    out at 60 s on a daily run that still reported "44 OK / 0 FAIL"). The probe
    sends realistic arguments and real ids, so an error object is a failure."""
    t = text.strip()
    if not t.startswith("{"):
        return None
    try:
        obj = json.loads(t)
    except ValueError:
        return None
    err = obj.get("error") if isinstance(obj, dict) else None
    return str(err) if err else None


def classify(tool: str, resp: dict, text: str, min_chars: int) -> str:
    if resp.get("error"):
        return "FAIL"
    if _ERR_RE.search(text):
        return "FAIL"
    if _json_error(text):
        return "FAIL"
    if len(text.strip()) < min_chars:
        return "EMPTY"
    return "OK"


# ── discovery: harvest real ids so detail tools are probed with live data ──

def harvest(base: str) -> dict:
    ids: dict = {}
    r, _ = call(base, "search_decisions",
                {"query": "missbräuchliche Kündigung", "limit": 5,
                 "fields": "compact", "include_pinpoint": False})
    t = body_text(r)
    m = re.findall(r"/entscheid/([A-Za-z0-9_\-.]+)", t)
    ids["decision_id"] = m[0] if m else "bge_BGE_132_III_115"
    # a federal decision with structure (Erwägungen) for the pinpoint tools
    ids["federal_decision_id"] = next(
        (x for x in m if x.startswith(("bge_", "bger_"))), ids["decision_id"])

    r, _ = call(base, "search_scholarship", {"query": "Kündigung", "limit": 3})
    t = body_text(r)
    m = re.findall(r"pub_id: `([^`]+)`", t)
    ids["pub_id"] = m[0] if m else None

    r, _ = call(base, "search_practice", {"query": "Arbeitsgesetz", "limit": 3})
    t = body_text(r)
    m = re.findall(r"doc_id[`'\"\s:]+([A-Za-z0-9_\-.]+)", t) or \
        re.findall(r"\b((?:seco|estv|sem|bafu)_[A-Za-z0-9_\-.]+)\b", t)
    ids["doc_id"] = m[0] if m else None

    # get_commentary takes abbreviation+article, not an opaque id.
    r, _ = call(base, "search_commentaries", {"query": "Kündigung", "limit": 3})
    m = re.findall(r"\*\*\d+\. Art\. (\S+) (\S+)\*\*", body_text(r))
    ids["commentary_article"] = list(m[0]) if m else None
    return ids


def cases(ids: dict) -> list[tuple]:
    """(tool, args, min_chars) — min_chars is the floor for a substantive answer."""
    d = ids["decision_id"]
    fd = ids["federal_decision_id"]
    c = [
        # search / discovery
        ("search_decisions", {"query": "missbräuchliche Kündigung Art. 336 OR",
                              "limit": 3, "include_pinpoint": False}, 120),
        ("search_decisions", {"query": "Verjährung", "limit": 3, "court": "bger",
                              "date_from": "2020-01-01", "fields": "compact"}, 60),
        ("search", {"query": "Tierhalterhaftung", "limit": 3}, 80),
        ("fetch", {"id": d}, 200),
        ("search_laws", {"query": "Kündigung Arbeitsvertrag", "limit": 5}, 100),
        ("search_legislation", {"query": "Datenschutz", "limit": 3}, 80),
        ("search_commentaries", {"query": "Willensmängel", "limit": 3}, 60),
        ("search_scholarship", {"query": "Mietrecht", "limit": 3}, 60),
        ("search_materialien", {"query": "Revision Aktienrecht", "limit": 3}, 60),
        ("search_botschaft", {"query": "Datenschutzgesetz", "limit": 3}, 60),
        ("search_practice", {"query": "Arbeitszeit", "limit": 3}, 60),
        # decision detail
        ("get_decision", {"decision_id": d}, 400),
        ("get_decisions", {"decision_ids": [d]}, 200),
        ("get_regeste", {"decision_id": d}, 40),
        ("get_decision_structure", {"decision_id": fd}, 60),
        ("get_erwaegung", {"decision_id": fd, "e_number": "1"}, 40),
        ("find_relevant_erwaegung", {"decision_id": fd,
                                     "claim": "Die Kündigung ist missbräuchlich"}, 40),
        ("get_case_brief", {"case": d}, 100),
        ("check_claim_support", {"claim": "Die Kündigung war missbräuchlich",
                                 "decision_id": d}, 40),
        ("cite", {"reference": "BGE 132 III 115"}, 30),
        # graph
        ("find_citations", {"decision_id": d}, 30),
        ("find_appeal_chain", {"decision_id": d}, 30),
        ("find_leading_cases", {"query": "Kündigung", "limit": 3}, 60),
        ("analyze_legal_trend", {"query": "Datenschutz"}, 60),
        # statutes
        ("get_law", {"abbreviation": "OR", "article": "336", "language": "de"}, 100),
        ("get_law", {"sr_number": "210", "article": "8", "language": "fr"}, 60),
        ("get_article_history", {"sr_number": "220", "article": "336"}, 40),
        ("get_article_purpose", {"sr_number": "220", "article": "336"}, 40),
        ("get_legislation", {"systematic_number": "235.1"}, 60),
        ("browse_legislation_changes", {"limit": 3}, 40),
        ("get_materialien", {"law_code": "DSG"}, 40),
        # scholarship bridge
        ("find_scholarship_citing_statute", {"sr_number": "220", "article": "336"}, 20),
        ("find_scholarship_citing_decision", {"decision_id": d}, 20),
        ("list_scholarship_sources", {}, 60),
        # reference / meta
        ("list_courts", {}, 200),
        ("get_statistics", {}, 100),
        # generative
        ("get_doctrine", {"query": "Vertrauensprinzip"}, 60),
        ("draft_mock_decision", {"facts": "Der Arbeitgeber kündigte, "
                                          "nachdem der Arbeitnehmer Lohnforderungen "
                                          "geltend gemacht hatte."}, 100),
        ("generate_exam_question", {"topic": "Missbräuchliche Kündigung"}, 100),
        ("attest_response", {"draft_text": "Nach BGE 132 III 115 ist eine Kündigung "
                                           "missbräuchlich, wenn sie gegen Treu und "
                                           "Glauben verstösst."}, 60),
    ]
    if ids.get("pub_id"):
        c += [("get_scholarship", {"pub_id": ids["pub_id"]}, 60),
              ("get_scholarship_full_text", {"pub_id": ids["pub_id"]}, 40)]
    if ids.get("doc_id"):
        c += [("get_practice", {"doc_id": ids["doc_id"]}, 100)]
    if ids.get("commentary_article"):
        art, abbr = ids["commentary_article"]
        c += [("get_commentary", {"abbreviation": abbr, "article": art}, 100)]
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    print(f"harvesting live ids from {a.base} ...")
    ids = harvest(a.base)
    for k, v in ids.items():
        print(f"  {k:22s} {v}")
    todo = cases(ids)
    print(f"\nprobing {len(todo)} tool calls across "
          f"{len({t for t, _, _ in todo})} distinct tools\n")

    def one(case):
        tool, args, floor = case
        try:
            resp, dt = call(a.base, tool, args)
            text = body_text(resp)
            return {"tool": tool, "args": args, "verdict": classify(tool, resp, text, floor),
                    "seconds": round(dt, 1), "chars": len(text), "head": text.strip()[:150]}
        except Exception as e:
            return {"tool": tool, "args": args, "verdict": "FAIL", "seconds": None,
                    "chars": 0, "head": f"{type(e).__name__}: {e}"[:150]}

    with cf.ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(one, todo))

    order = {"FAIL": 0, "EMPTY": 1, "OK": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], -(r["seconds"] or 0)))
    for r in rows:
        secs = f"{r['seconds']:5.1f}s" if r["seconds"] is not None else "    - "
        print(f"  {r['verdict']:5s} {secs} {r['tool']:32s} {r['chars']:6d}ch  {r['head'][:80]}")

    n_fail = sum(1 for r in rows if r["verdict"] == "FAIL")
    n_empty = sum(1 for r in rows if r["verdict"] == "EMPTY")
    print(f"\n  {len(rows) - n_fail - n_empty} OK / {n_empty} EMPTY / {n_fail} FAIL")
    slow = [r for r in rows if (r["seconds"] or 0) > 20]
    if slow:
        print(f"  slower than 20s: {', '.join(r['tool'] for r in slow)}")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"base": a.base, "ids": ids, "rows": rows}, f,
                      ensure_ascii=False, indent=2)
        print(f"  wrote {a.json_out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
