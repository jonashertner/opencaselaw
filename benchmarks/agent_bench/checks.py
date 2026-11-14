"""Deterministic checks over one run: question + trajectory + attest + verdicts.

Offline and pure, so tests can pin them. The two inputs that need the
network are produced by run.py: the server's own `attest_response`
audit of the final answer (citations, pinpoints, quotes, statutes,
dates) and the rubric judge's verdicts.

Severity (README, "Severity"):
  S1  misleads: invented authority, pinpoint or quote; wrong conclusion;
      an overruled decision relied on as good law
  S2  materially incomplete: required authority or point missing,
      wrong date, run produced no answer
  S3  method: server rules skipped, citation not traced to retrieval,
      imprecise pinpoint, statute not named
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

S1, S2, S3 = "S1", "S2", "S3"

# attest_response issue `problem` -> (check name, severity). Problems not
# listed here are kept as attest_other/S3 so a new rail never vanishes.
ATTEST_PROBLEMS = {
    "decision_not_in_corpus": ("fabricated_decision", S1),
    "pinpoint_not_in_decision": ("invalid_pinpoint", S1),
    "quote_not_in_cited_sources": ("unverified_quote", S1),
    "article_not_in_law": ("unknown_statute_article", S1),
    "law_abbreviation_unknown": ("unknown_statute", S1),
    "date_does_not_match_decision": ("wrong_date", S2),
}
# Corpus-side data problems, not the agent's error.
IGNORED_PROBLEMS = {"stored_date_suspect"}

# Statute abbreviations across DE / FR / IT, so a gold "OR" matches "CO".
LAW_ALIASES = [
    {"OR", "CO"}, {"ZGB", "CC"}, {"StGB", "CP"}, {"BV", "Cst.", "Cost."},
    {"ZPO", "CPC"}, {"StPO", "CPP"}, {"SchKG", "LP", "LEF"}, {"BGG", "LTF"},
    {"VwVG", "PA"}, {"ATSG", "LPGA"}, {"IVG", "LAI"}, {"AHVG", "LAVS"},
    {"UVG", "LAA", "LAINF"}, {"KVG", "LAMal"}, {"DSG", "LPD"}, {"ArG", "LTr", "LL"},
    {"SchlT ZGB", "SchlT", "Schlusstitel ZGB", "Schlusstitel des ZGB", "Tit. fin. CC", "Tit. fin.",
     "titre final du CC", "titre final CC", "Titre final du CC", "Tit. fin. CC svizzero"}, {"BGFA", "LLCA"},
    {"BVG", "LPP"}, {"AVIG", "LACI"}, {"EMRK", "CEDH", "CEDU"},
]

_LINK = re.compile(r"/entscheid/([A-Za-z0-9_.\-]+?)(?:#e-([0-9a-z][0-9a-z\-]*))?(?=[)\s\]\"'>|,]|$)")


@dataclass
class Finding:
    check: str
    severity: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def cited_decisions(*texts: str) -> dict[str, set[str]]:
    """decision_id -> pinpoints cited, from /entscheid/<id>#e-4-1 links.
    Fed the agent's answer and attest's linked_text, which links every
    citation the server resolved, so plain-text citations count too."""
    out: dict[str, set[str]] = {}
    for text in texts:
        for m in _LINK.finditer(text or ""):
            pins = out.setdefault(m.group(1), set())
            if m.group(2):
                pins.add(m.group(2).replace("-", "."))
    return out


def retrieved_text(steps: list[dict]) -> str:
    """Everything the agent got back from research tools. The closing
    attest audit is excluded: it verifies, it does not retrieve."""
    return "\n".join(s.get("result", "") for s in steps if s.get("name") != "attest_response")


def _aliases(law: str, extra: list[str]) -> set[str]:
    names = {law, *extra}
    for group in LAW_ALIASES:
        if names & group:
            names |= group
    return names


def statute_mentioned(answer: str, law: str, article: str, aliases: list[str] | None = None) -> bool:
    names = sorted(_aliases(law, aliases or []), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    pat = rf"\b(?:Art|art)\.?\s*{re.escape(str(article))}(?![0-9a-z])[^\n]{{0,40}}?(?<![A-Za-z])(?:{alt})(?![A-Za-z])"
    return re.search(pat, answer or "") is not None


def _pinpoint_relation(cited: set[str], gold: str) -> str:
    if any(p == gold or p.startswith(gold + ".") for p in cited):
        return "exact"
    if any(gold.startswith(p + ".") for p in cited):
        return "parent"
    return "none"


def check_run(question: dict, traj: dict, attest: dict | None, verdicts: list[dict]) -> dict:
    findings: list[Finding] = []
    answer = traj.get("answer") or ""
    steps = traj.get("steps") or []

    if traj.get("status") != "completed" or not answer.strip():
        findings.append(Finding("no_answer", S2, traj.get("status", "error") + ": " + (traj.get("error") or "")[:200]))

    # 1. The server's own audit of the final answer.
    checked = attest is not None and "_error" not in attest and "issues" in attest
    if answer.strip() and not checked:
        findings.append(Finding("attest_unavailable", S3, str((attest or {}).get("_error", "no audit"))[:200]))
    for issue in (attest or {}).get("issues", []) if checked else []:
        problem = issue.get("problem", "")
        if problem in IGNORED_PROBLEMS:
            continue
        if issue.get("category") == "grounding":
            name, sev = "unsupported_proposition", S2
        else:
            name, sev = ATTEST_PROBLEMS.get(problem, ("attest_other", S3))
        findings.append(Finding(name, sev, f"{issue.get('citation', '')}: {problem}".strip()))

    # 2. Rubric verdicts.
    critical_ok = True
    for v, point in zip(verdicts, question["rubric"]):
        if not point.get("critical"):
            continue
        if v["verdict"] == "contradicted":
            critical_ok = False
            findings.append(Finding("wrong_answer", S1, point["point"][:200]))
        elif v["verdict"] != "met":
            critical_ok = False
            findings.append(Finding("incomplete_answer", S2, f"{v['verdict']}: {point['point'][:180]}"))

    # 3. Authorities against the gold set.
    cited = cited_decisions(answer, (attest or {}).get("linked_text", ""))
    seen = retrieved_text(steps)
    for g in question["gold"]["decisions"]:
        did, pins = g["decision_id"], cited.get(g["decision_id"])
        if pins is None:
            if g.get("must_cite"):
                findings.append(Finding("missing_authority", S2, did))
            continue
        if g.get("pinpoint"):
            rel = _pinpoint_relation(pins, str(g["pinpoint"]))
            if rel == "parent":
                findings.append(Finding("pinpoint_imprecise", S3, f"{did}: cited {sorted(pins)}, gold E. {g['pinpoint']}"))
            elif rel == "none":
                findings.append(Finding("pinpoint_missing", S3, f"{did}: cited {sorted(pins) or 'no E.'}, gold E. {g['pinpoint']}"))
    for f in question.get("forbidden", []):
        if f["decision_id"] in cited:
            # Citing an overruled decision is fine when the answer says it
            # was overruled; the judge's critical points decide which.
            sev = S3 if critical_ok else S1
            findings.append(Finding("overruled_authority", sev, f"{f['decision_id']} ({f.get('reason', '')})"[:240]))
    for s in question["gold"].get("statutes", []):
        if not statute_mentioned(answer, s["law"], s["article"], s.get("aliases")):
            findings.append(Finding("statute_not_named", S3, f"Art. {s['article']} {s['law']}"))

    # 4. Method: the server's rules, read off the trajectory.
    names = [s.get("name") for s in steps]
    if answer.strip() and "attest_response" not in names:
        findings.append(Finding("no_closing_attest", S3, "attest_response never called"))
    if any(s.get("name") == "search_decisions" and (s.get("input") or {}).get("language") for s in steps):
        findings.append(Finding("language_filter", S3, "search_decisions called with a language filter"))
    for did in sorted(cited):
        if did not in seen:
            findings.append(Finding("cited_without_retrieval", S3, did))

    weights = [2 if p.get("critical") else 1 for p in question["rubric"]]
    met = [w for w, v in zip(weights, verdicts) if v["verdict"] == "met"]
    counts = {sev: sum(f.severity == sev for f in findings) for sev in (S1, S2, S3)}
    return {
        "score": round(sum(met) / sum(weights), 4) if weights else None,
        "audited": checked,
        # An answer the server could not audit is not a pass.
        "passed": checked and counts[S1] == 0 and counts[S2] == 0,
        "counts": counts,
        "cited_decisions": {k: sorted(v) for k, v in sorted(cited.items())},
        "findings": [f.to_dict() for f in findings],
    }
