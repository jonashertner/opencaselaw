#!/usr/bin/env python3
"""Nightly completeness check of the federal Materialien against Fedlex.

Ground truth is asked fresh from the Fedlex SPARQL endpoint on every run and
compared with what the served databases hold:

1. **Bundesblatt, typed** (Botschaft 23, Bericht BR 24, Stellungnahme BR 25,
   Bericht Kommission 30, Erlassentwurf 38, Vernehmlassung-Eröffnung 40), per
   language: every Fedlex work must have a botschaft_documents row. A work
   missing for longer than ``--grace-days`` (by its Fedlex date) is a GAP.
2. **Bundesblatt, historical** (untyped 1850-1999 Botschaften, Berichte,
   Kommissionsberichte, Stellungnahmen): discovered vs stored vs the fetch
   state of the backfill (no_text / no_citation / not_found / error /
   never attempted). Reported; a gap only with ``--historical-strict``
   (the backfill runs over several nights).
3. **Vernehmlassungen**: Fedlex's procedure / document / file counts vs
   consultations.db (discovery lag is a GAP), and the text backlog — files
   under the text policy still pending or failed for longer than
   ``--grace-days`` are a GAP; scans without text (OCR backlog), corrupt
   files and too-large files are listed, never hidden.

Writes ``--report`` (JSON, atomic), prints a summary, sends ONE ntfy message
when there is a gap (unless ``--no-ntfy``), exits 1 with ``--fail-on-gap``.

    python3 scripts/check_materialien_completeness.py \\
        --materialien-db output/materialien.db --consultations-db output/consultations.db
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

NTFY_URL = (
    os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    + "/"
    + os.environ.get("NTFY_TOPIC", "opencaselaw-scrapers")
)
LANGS = ("de", "fr", "it")
TYPED_BBL_TYPES = (23, 24, 25, 30, 38, 40)
SAMPLE = 20
JOLUX = "http://data.legilux.public.lu/resource/ontology/jolux#"
LANG_URI = {
    "de": "http://publications.europa.eu/resource/authority/language/DEU",
    "fr": "http://publications.europa.eu/resource/authority/language/FRA",
    "it": "http://publications.europa.eu/resource/authority/language/ITA",
}


def _ro(path: str | Path) -> sqlite3.Connection | None:
    p = Path(path)
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{os.path.realpath(p)}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _date_of(*values) -> date | None:
    for v in values:
        if not v:
            continue
        s = str(v)[:10]
        try:
            return date.fromisoformat(s)
        except ValueError:
            if s[:4].isdigit():
                return date(int(s[:4]), 12, 31)
    return None


# ── 1. Bundesblatt, typed ──────────────────────────────────────────────

def check_typed(mconn, discover: Callable, today: date, grace_days: int) -> dict:
    out: dict = {"by_language": {}, "gap": 0, "missing_sample": []}
    cutoff = today - timedelta(days=grace_days)
    for lang in LANGS:
        works = discover(lang, TYPED_BBL_TYPES)
        held = set()
        if mconn is not None:
            held = {r[0] for r in mconn.execute(
                "SELECT eli_uri FROM botschaft_documents WHERE language=? AND eli_uri IS NOT NULL",
                (lang,))}
        missing = [w for w in works if w["eli_uri"] not in held]
        aged = [w for w in missing
                if (_date_of(w.get("publication_date"), w.get("date_document"), w.get("year"))
                    or today) <= cutoff]
        by_type: dict[str, dict] = {}
        for w in works:
            t = by_type.setdefault(w["doc_type"], {"fedlex": 0, "held": 0})
            t["fedlex"] += 1
            t["held"] += w["eli_uri"] in held
        out["by_language"][lang] = {
            "fedlex": len(works), "held": len(works) - len(missing),
            "missing": len(missing), "missing_past_grace": len(aged), "by_type": by_type,
        }
        out["gap"] += len(aged)
        out["missing_sample"] += [
            {"language": lang, "eli_uri": w["eli_uri"], "doc_type": w["doc_type"],
             "year": w.get("year"), "title": (w.get("title") or "")[:160]}
            for w in aged[:SAMPLE]]
    return out


# ── 2. Bundesblatt, historical ─────────────────────────────────────────

def check_historical(mconn, discover: Callable) -> dict:
    out: dict = {"by_language": {}, "not_stored": 0}
    for lang in LANGS:
        cands = discover(lang)
        uris = {c.eli_uri for c in cands}
        held, state = set(), {}
        if mconn is not None:
            held = {r[0] for r in mconn.execute(
                "SELECT eli_uri FROM botschaft_documents WHERE language=?", (lang,))}
            try:
                state = {r[0]: r[1] for r in mconn.execute(
                    "SELECT eli_uri, status FROM botschaft_fetch_state WHERE language=?", (lang,))}
            except sqlite3.OperationalError:
                state = {}
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for c in cands:
            by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
            if c.eli_uri in held:
                continue
            st = state.get(c.eli_uri, "never_attempted")
            by_status[st] = by_status.get(st, 0) + 1
        stored = len(uris & held)
        out["by_language"][lang] = {
            "fedlex": len(uris), "stored": stored, "not_stored": len(uris) - stored,
            "not_stored_by_state": by_status, "fedlex_by_type": by_type,
            "without_fedlex_citation": sum(1 for c in cands if not c.citation),
        }
        out["not_stored"] += len(uris) - stored
    return out


# ── 3. Vernehmlassungen ────────────────────────────────────────────────

def _q_count_procedures() -> str:
    return f"PREFIX jolux: <{JOLUX}> SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE {{ ?c a jolux:Consultation }}"


def _q_count_documents() -> str:
    return f"""PREFIX jolux: <{JOLUX}>
SELECT ?dt (COUNT(DISTINCT ?doc) AS ?n) WHERE {{
  ?c a jolux:Consultation ; jolux:hasSubTask ?s .
  VALUES ?rel {{ jolux:opinionHasDraftRelatedDocument jolux:opinionIsAboutDraftDocument }}
  ?s ?rel ?doc . ?doc jolux:draftProcessDocumentType ?dt .
}} GROUP BY ?dt"""


def _q_count_files(lang: str) -> str:
    return f"""PREFIX jolux: <{JOLUX}>
SELECT ?dt (COUNT(DISTINCT ?doc) AS ?n) WHERE {{
  ?c a jolux:Consultation ; jolux:hasSubTask ?s .
  VALUES ?rel {{ jolux:opinionHasDraftRelatedDocument jolux:opinionIsAboutDraftDocument }}
  ?s ?rel ?doc . ?doc jolux:draftProcessDocumentType ?dt ; jolux:isRealizedBy ?e .
  ?e jolux:language <{LANG_URI[lang]}> .
}} GROUP BY ?dt"""


def check_vernehmlassungen(cconn, sparql: Callable, today: date, grace_days: int) -> dict:
    from search_stack.build_consultation_corpus import DOC_TYPES, FINAL_STATUSES

    def code(uri: str) -> int | None:
        tail = (uri or "").rstrip("/").rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else None

    out: dict = {"gap": 0, "fedlex": {}, "held": {}, "discovery_lag": {}, "text": {}}
    fed_procs = int(sparql(_q_count_procedures())[0]["n"]["value"])
    fed_docs = {DOC_TYPES.get(code(r["dt"]["value"]), r["dt"]["value"]): int(r["n"]["value"])
                for r in sparql(_q_count_documents())}
    fed_files = {lang: {DOC_TYPES.get(code(r["dt"]["value"]), r["dt"]["value"]): int(r["n"]["value"])
                        for r in sparql(_q_count_files(lang))} for lang in LANGS}
    out["fedlex"] = {"procedures": fed_procs, "documents": fed_docs, "files": fed_files}
    if cconn is None:
        out["error"] = "consultations.db not available"
        out["gap"] = fed_procs
        return out
    held_procs = cconn.execute("SELECT COUNT(*) FROM consultation_procedures").fetchone()[0]
    held_docs = dict(cconn.execute(
        "SELECT doc_type, COUNT(*) FROM consultation_documents GROUP BY 1").fetchall())
    held_files = {lang: dict(cconn.execute(
        "SELECT d.doc_type, COUNT(*) FROM consultation_files f JOIN consultation_documents d "
        "USING(document_uri) WHERE f.language=? GROUP BY 1", (lang,)).fetchall()) for lang in LANGS}
    out["held"] = {"procedures": held_procs, "documents": held_docs, "files": held_files}
    lag = {"procedures": max(0, fed_procs - held_procs)}
    lag["documents"] = {t: n - held_docs.get(t, 0) for t, n in fed_docs.items()
                        if n > held_docs.get(t, 0)}
    lag["files"] = {lang: {t: n - held_files[lang].get(t, 0) for t, n in fed_files[lang].items()
                           if n > held_files[lang].get(t, 0)} for lang in LANGS}
    lag["files"] = {k: v for k, v in lag["files"].items() if v}
    out["discovery_lag"] = lag
    n_lag = (lag["procedures"] + sum(lag["documents"].values())
             + sum(sum(v.values()) for v in lag["files"].values()))

    cutoff = (today - timedelta(days=grace_days)).isoformat()
    by_status = {}
    for r in cconn.execute(
            "SELECT d.doc_type, f.status, COUNT(*) n FROM consultation_files f "
            "JOIN consultation_documents d USING(document_uri) WHERE f.text_policy='text' "
            "GROUP BY 1, 2"):
        by_status.setdefault(r["doc_type"], {})[r["status"]] = r["n"]
    backlog = cconn.execute(
        f"""SELECT f.document_uri, f.language, f.status, f.error, d.doc_type,
                   COALESCE(f.publication_date, p.start_date, p.year || '-12-31') AS d
            FROM consultation_files f JOIN consultation_documents d USING(document_uri)
            JOIN consultation_procedures p ON p.procedure_uri = d.procedure_uri
            WHERE f.text_policy='text'
              AND f.status NOT IN ({','.join('?' * len(FINAL_STATUSES))})""",
        tuple(sorted(FINAL_STATUSES))).fetchall()
    aged = [r for r in backlog if (r["d"] or "")[:10] <= cutoff]
    problems = cconn.execute(
        "SELECT d.doc_type, f.status, f.document_uri, f.language, f.error "
        "FROM consultation_files f JOIN consultation_documents d USING(document_uri) "
        "WHERE f.status IN ('no_text', 'parse_error', 'too_large', 'not_found') "
        "ORDER BY f.status, f.document_uri LIMIT ?", (SAMPLE,)).fetchall()
    out["text"] = {
        "by_type_and_status": by_status,
        "backlog": len(backlog), "backlog_past_grace": len(aged),
        "backlog_sample": [dict(r) for r in aged[:SAMPLE]],
        "without_text_sample": [dict(r) for r in problems],
    }
    out["gap"] = n_lag + len(aged)
    return out


# ── report ─────────────────────────────────────────────────────────────

def send_ntfy(title: str, message: str, url: str | None = None) -> bool:
    req = urllib.request.Request(
        url or NTFY_URL, data=message.encode("utf-8", errors="replace"),
        headers={"Title": title.encode("ascii", errors="replace").decode("ascii"),
                 "Priority": "default", "Tags": "warning"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"ntfy send failed: {e}", file=sys.stderr)
        return False


def summarise(report: dict) -> list[str]:
    lines = []
    t = report.get("bundesblatt_typed", {})
    for lang, v in t.get("by_language", {}).items():
        lines.append(f"BBl typed {lang}: {v['held']}/{v['fedlex']} held, "
                     f"{v['missing']} missing ({v['missing_past_grace']} past grace)")
    h = report.get("bundesblatt_historical", {})
    for lang, v in h.get("by_language", {}).items():
        lines.append(f"BBl 1850-1999 {lang}: {v['stored']}/{v['fedlex']} stored, "
                     f"not stored by state {v['not_stored_by_state']}")
    c = report.get("vernehmlassungen", {})
    if c:
        lines.append(f"Vernehmlassungen: {c.get('held', {}).get('procedures')}/"
                     f"{c.get('fedlex', {}).get('procedures')} procedures, discovery lag "
                     f"{c.get('discovery_lag')}, text backlog {c.get('text', {}).get('backlog')} "
                     f"({c.get('text', {}).get('backlog_past_grace')} past grace)")
    return lines


def run(args, *, sparql=None, discover_typed=None, discover_untyped=None,
        today: date | None = None, sender=send_ntfy) -> tuple[dict, bool]:
    from scrapers import fedlex_materialien as fm
    sparql = sparql or (lambda q: fm.sparql_query(q, timeout=300))
    discover_typed = discover_typed or (lambda lang, types: fm.discover_fga_documents(
        lang, types, timeout=300))
    discover_untyped = discover_untyped or (lambda lang: fm.discover_fga_botschaften_untyped(lang))
    today = today or datetime.now(timezone.utc).date()
    mconn = _ro(args.materialien_db)
    cconn = _ro(args.consultations_db)
    try:
        report = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "grace_days": args.grace_days}
        report["bundesblatt_typed"] = check_typed(mconn, discover_typed, today, args.grace_days)
        if not args.skip_historical:
            report["bundesblatt_historical"] = check_historical(mconn, discover_untyped)
        report["vernehmlassungen"] = check_vernehmlassungen(cconn, sparql, today, args.grace_days)
    finally:
        for c in (mconn, cconn):
            if c is not None:
                c.close()
    gap = (report["bundesblatt_typed"]["gap"] + report["vernehmlassungen"]["gap"]
           + (report.get("bundesblatt_historical", {}).get("not_stored", 0)
              if args.historical_strict else 0))
    report["gap"] = gap
    report["summary"] = summarise(report)
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        tmp = rp.with_suffix(rp.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        os.replace(tmp, rp)
    if gap and not args.no_ntfy:
        sender("Materialien completeness: gap", "\n".join(report["summary"])[:3500])
    return report, bool(gap)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--materialien-db", default=str(REPO / "output" / "materialien.db"))
    ap.add_argument("--consultations-db", default=str(REPO / "output" / "consultations.db"))
    ap.add_argument("--report", default=str(REPO / "logs" / "materialien_completeness.json"))
    ap.add_argument("--grace-days", type=int, default=7)
    ap.add_argument("--skip-historical", action="store_true")
    ap.add_argument("--historical-strict", action="store_true")
    ap.add_argument("--no-ntfy", action="store_true")
    ap.add_argument("--fail-on-gap", action="store_true")
    args = ap.parse_args(argv)
    report, gap = run(args)
    print("\n".join(report["summary"]))
    print(f"gap: {report['gap']}")
    return 1 if (gap and args.fail_on_gap) else 0


if __name__ == "__main__":
    sys.exit(main())
