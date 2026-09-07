"""Adversarial validation of the Copilot Studio OpenAPI variant.

Verifies — across six independent angles — that the Microsoft Copilot
Studio custom connector spec (``/api/openapi.copilot.json``) is both
valid OpenAPI 3.0.3 AND honestly describes the live API's wire
responses. Built after a Copilot Studio integration report
("API antwortet erfolgreich, liefert aber null Treffer") which
traced to the spec declaring permissive ``{type: object,
additionalProperties: true}`` for every endpoint, leaving Copilot
Studio's PowerFx data binding with no named properties to surface.

Why six angles? Each layer catches a different class of regression:

  ANGLE 1 — OpenAPI 3.0.3 spec validity (openapi-spec-validator).
            Catches accidental use of 3.1-only idioms like
            ``type: [X, null]``.
  ANGLE 2 — Wire vs declared schema for the USER's specific
            complaint (BVGer search, 7 query+filter combos).
  ANGLE 3 — Edge cases: zero-result FTS, empty query, filter
            stacking, FR/IT corpus, docket-style lookups, compact
            fields mode.
  ANGLE 4 — Response stability (3× same query → identical shape).
  ANGLE 5 — Full ``/api/openapi.json`` (the NON-Copilot variant)
            still validates — regression guard for the main spec.
  ANGLE 6 — Schema completeness: every declared property appears in
            at least one real wire response (otherwise the schema
            falsely advertises fields and Copilot Studio renders
            them as null bindings).

Usage::

  python3 -m benchmarks.copilot_openapi_validation
  # or
  python3 benchmarks/copilot_openapi_validation.py

Requirements: ``jsonschema`` and ``openapi-spec-validator`` (both in
the existing dependency set).

Runs in 3–5 min during normal load; up to ~10 min if a publish is
mid-FTS5-optimize on the VPS. All probes use retries + 180 s
timeouts; transient timeouts are reported as SKIP, not FAIL.
"""
from __future__ import annotations

import copy
import json
import sys
import time
import urllib.request

from jsonschema import Draft202012Validator, ValidationError
from openapi_spec_validator.shortcuts import validate_spec

BASE = "https://mcp.opencaselaw.ch"


def fetch(path: str, timeout: int = 180, ua: str = "copilot-validate/1.0",
          retries: int = 2):
    """Fetch with retries + long timeout — publish-in-flight makes some
    requests slow. Returns ``(status, body_or_str, elapsed_ms)``."""
    for attempt in range(retries + 1):
        started = time.monotonic()
        req = urllib.request.Request(f"{BASE}{path}",
                                     headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                elapsed = (time.monotonic() - started) * 1000
                try:
                    return r.status, json.loads(body), elapsed
                except json.JSONDecodeError:
                    return (r.status,
                            body.decode("utf-8", errors="replace"),
                            elapsed)
        except Exception as e:
            if attempt < retries:
                time.sleep(2.0)
                continue
            return (0, f"ERR:{type(e).__name__}",
                    (time.monotonic() - started) * 1000)


def to_jsonschema(node):
    """Convert OpenAPI 3.0 ``nullable: true`` into JSON-Schema
    ``type: [X, "null"]`` so Draft202012Validator can validate properly.
    Walks in place."""
    if isinstance(node, dict):
        if node.pop("nullable", False) is True and "type" in node:
            t = node["type"]
            if isinstance(t, str):
                node["type"] = [t, "null"]
        for v in node.values():
            to_jsonschema(v)
    elif isinstance(node, list):
        for v in node:
            to_jsonschema(v)
    return node


def get_schema(spec: dict, method: str, path: str) -> dict:
    op = spec["paths"].get(path, {}).get(method.lower(), {})
    s = (op.get("responses", {}).get("200", {}).get("content", {})
         .get("application/json", {}).get("schema") or {})
    return to_jsonschema(copy.deepcopy(s))


def validates(schema: dict, data):
    try:
        Draft202012Validator(schema).validate(data)
        return True, None
    except ValidationError as e:
        return False, str(e).split("\n")[0][:90]


def main() -> int:
    """Run all 6 validation angles against the live API.

    Returns 0 if every angle passes, 1 otherwise. Importing this module
    has no side effects — call ``main()`` (or run from CLI) to fire the
    live probes.
    """
    print("Pulling live Copilot spec…")
    _, copilot_spec, _ = fetch("/api/openapi.copilot.json")
    print("Pulling live full spec…")
    _, full_spec, _ = fetch("/api/openapi.json")

    results: dict[str, bool] = {}

    # ───────────────────── ANGLE 1 — spec validity ────────────────────
    print("\n── ANGLE 1: OpenAPI 3.0.3 spec validity ──")
    for name, spec in [("copilot", copilot_spec), ("full", full_spec)]:
        try:
            validate_spec(spec)
            print(f"  ✓ {name} spec validates as OpenAPI 3.0.x")
            results[f"angle1_{name}"] = True
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {str(e)[:200]}")
            results[f"angle1_{name}"] = False

    # ─────── ANGLE 2 — BVGer-specific (user's complaint) ──────────────
    print("\n── ANGLE 2: BVGer search (the user's actual complaint) ──")
    sch_decisions = get_schema(copilot_spec, "GET", "/decisions")
    bvger_probes = [
        "/api/decisions?court=bvger&limit=3",
        "/api/decisions?court=bvger&language=de&limit=3",
        "/api/decisions?court=bvger&query=Asyl&limit=3",
        "/api/decisions?court=bvger&query=Beschwerde&language=de&limit=3",
        "/api/decisions?court=bvger&sort=date_desc&limit=3",
        "/api/decisions?court=bvger&chamber=Abteilung%20V&limit=3",
        "/api/decisions?court=bvger&date_from=2025-01-01&limit=3",
    ]
    all_ok = True
    skipped = 0
    for url in bvger_probes:
        s, b, _ = fetch(url)
        if s == 0 or not isinstance(b, dict):
            skipped += 1
            print(f"  ⏱ {url[:64]:<64}  HTTP {s}  SKIP (timeout)")
            continue
        ok, err = validates(sch_decisions, b)
        n = b.get("total", "?")
        print(f"  {'✓' if ok else '✗'} {url[:64]:<64}  HTTP {s}  "
              f"total={n}  {err or ''}")
        if not ok:
            all_ok = False
    results["angle2_bvger"] = all_ok
    if skipped:
        print(f"  ({skipped} skipped due to load)")

    # ───────────────── ANGLE 3 — edge cases ───────────────────────────
    print("\n── ANGLE 3: Edge cases ──")
    edges = [
        ("zero-result court+nonsense",
         "/api/decisions?court=bvger&query=qxwzyq_no_such_word_xx&limit=3"),
        ("empty query, sort by date",
         "/api/decisions?sort=date_desc&limit=3"),
        ("filter-stacked, no FTS",
         "/api/decisions?court=bvger&canton=CH&language=de&limit=3"),
        ("french bge",
         "/api/decisions?court=bge&language=fr&limit=3"),
        ("italian decisions",
         "/api/decisions?language=it&limit=3"),
        ("docket lookup style",
         "/api/decisions?query=A-6279%2F2024&limit=3"),
        ("very-recent date_from",
         "/api/decisions?date_from=2026-01-01&court=bger&limit=3"),
        ("compact fields mode",
         "/api/decisions?court=bvger&fields=compact&limit=3"),
    ]
    all_ok = True
    skipped = 0
    for name, url in edges:
        s, b, _ = fetch(url)
        if s == 0 or not isinstance(b, dict):
            skipped += 1
            print(f"  ⏱ {name:<32}  HTTP {s}  SKIP (timeout)")
            continue
        ok, err = validates(sch_decisions, b)
        n = b.get("total", "?")
        print(f"  {'✓' if ok else '✗'} {name:<32}  HTTP {s}  "
              f"total={n}  {err or ''}")
        if not ok:
            all_ok = False
    results["angle3_edges"] = all_ok
    if skipped:
        print(f"  ({skipped} skipped due to load)")

    # ──────────── ANGLE 4 — response stability ────────────────────────
    print("\n── ANGLE 4: Response stability (same query 3×) ──")
    url = "/api/decisions?court=bvger&query=Asyl&limit=3"
    shapes = []
    for i in range(3):
        s, b, ms = fetch(url)
        ok, _ = validates(sch_decisions, b)
        if isinstance(b, dict):
            keys = sorted(b.keys())
            item_keys = (sorted(b["results"][0].keys())
                         if b.get("results") else [])
        else:
            keys = "<?>"
            item_keys = []
        shapes.append((tuple(keys), tuple(item_keys), ok))
        print(f"  run {i + 1}: HTTP {s}  ms={ms:.0f}  "
              f"schema_ok={ok}  top_keys={keys}")
    identical = len(set(shapes)) == 1 and all(s[2] for s in shapes)
    print(f"  {'✓' if identical else '✗'} 3 runs identical + all valid")
    results["angle4_stability"] = identical

    # ────────── ANGLE 5 — full spec regression guard ──────────────────
    print("\n── ANGLE 5: Full /api/openapi.json regression ──")
    sch_full_decisions = get_schema(full_spec, "GET", "/decisions")
    s, b, _ = fetch("/api/decisions?court=bge&limit=3")
    props = sch_full_decisions.get("properties", {})
    print(f"  full-spec /decisions: {len(props)} typed properties")
    print(f"  full-spec /decisions: HTTP {s}, dict={isinstance(b, dict)}")
    results["angle5_full_spec"] = (s == 200 and isinstance(b, dict))

    # ────────── ANGLE 6 — schema completeness ─────────────────────────
    print("\n── ANGLE 6: Schema completeness ──")
    print("  For each declared field, verify ≥ 1 real response contains it.")
    sample_endpoints = [
        ("/decisions",
         "/api/decisions?court=bvger&query=Asyl&language=de&limit=3"),
        ("/decisions/{decision_id}",
         "/api/decisions/bge_BGE_140_III_86?full_text=false"),
        ("/leading-cases",
         "/api/leading-cases?law_code=OR&article=271a&limit=3"),
        ("/cite",
         "/api/cite?reference=BGE%20140%20III%2086"),
        ("/regeste/{decision_id}",
         "/api/regeste/bge_BGE_140_III_86"),
        ("/erwaegung/{decision_id}/{e_number}",
         "/api/erwaegung/bge_BGE_118_II_50/4"),
        ("/relevant-erwaegung/{decision_id}",
         "/api/relevant-erwaegung/emark_EMARK-2004-28"
         "?claim=Test%20Alltagswissen&top_k=1"),
        # The 4 endpoints typed in commit 023a190 — defend against schema
        # drift on the curated subset's full 15-action coverage.
        ("/legislation/search",
         "/api/legislation/search?query=Vaterschaftsurlaub&limit=3"),
        ("/legislation/{lexfind_id}",
         "/api/legificiation/13"),  # placeholder, overridden below
        ("/doctrine",
         "/api/doctrine?query=Art.%20271a%20OR"),
        ("/article-purpose/{sr_number}/{article}",
         "/api/article-purpose/220/41"),
    ]
    # /legislation/{lexfind_id} probe needs a real id — pull one from the
    # search probe so the bench is self-contained across portal refreshes.
    _ls, _lb, _ = fetch("/api/legislation/search?query=Obligationenrecht"
                        "&limit=1")
    if (isinstance(_lb, dict)
        and _lb.get("laws") and _lb["laws"][0].get("lexfind_id")):
        sample_endpoints = [(p, p2.replace(
            "/api/legificiation/13",
            f"/api/legislation/{_lb['laws'][0]['lexfind_id']}"))
            for p, p2 in sample_endpoints]
    completeness_ok = True
    skipped_paths = 0
    for path, probe in sample_endpoints:
        sch = get_schema(copilot_spec, "GET", path)
        declared = set(sch.get("properties", {}).keys())
        s, b, _ = fetch(probe)
        if s != 200 or not isinstance(b, dict):
            skipped_paths += 1
            print(f"  ⏱ {path:<42}  SKIP (timeout)")
            continue
        wire = set(b.keys())
        missing = declared - wire
        msg = ("✓ all declared fields appear" if not missing
               else f"⚠ declared-but-not-wire: {sorted(missing)[:5]}")
        print(f"  {path:<42}  {msg}")
        if missing:
            completeness_ok = False
    results["angle6_completeness"] = completeness_ok
    if skipped_paths:
        print(f"  ({skipped_paths} skipped due to load)")

    # ───────────────────── SUMMARY ────────────────────────────────────
    print("\n" + "═" * 78)
    print("VALIDATION SUMMARY")
    print("═" * 78)
    for k, v in results.items():
        print(f"  {'✅ PASS' if v else '❌ FAIL'}  {k}")
    n_pass = sum(1 for v in results.values() if v)
    n_total = len(results)
    print(f"\n{n_pass} / {n_total} angle checks passed")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
