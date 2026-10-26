"""Build the public law-code reference table (issue #74).

The cross-language abbreviation pairing is currently re-encoded by hand in
at least three places — quality/checks/statute_graph.py TOP_FEDERAL_LAWS,
benchmarks/build_canonical_top_statutes.py, and the SR mapping the statute
tools assume. Adding a law code today means remembering three edit sites,
and forgetting one degrades a QC threshold or a benchmark silently.

This derives the table from statutes.db instead of curating it: `laws`
carries sr_number plus abbr_de/abbr_fr/abbr_it per act, so the alias
groups ARE the data. Cantonal codes come from cantonal_laws.db where
available.

Consumer problem this solves (reported by @Tomagori): the extractor emits
language-specific codes by design, so a downstream tool that does not know
OR == CO silently loses the majority of French statute references — our own
QC comment measures OR alone at 125,592 edges against 359,195 for OR+CO.
Without the table that is a wrong answer with no error.

Two layers sit on top of the derivation (anomaly report of 2026-10-26,
measured against the HF snapshot of that day):

- Repealed acts. statutes.db holds only acts in force, so a code borne
  only by a repealed act (SGG, GestG, BEHG, OLE, VDSG ...) resolved to
  nothing. Fedlex still carries their short titles; they are published in
  a separate index keyed to the act (ELI) with its validity dates, never
  merged into alias_to_sr, because a repealed act's SR number is often
  reused by an unrelated successor (GestG sat at 272, now the ZPO).
- A short, documented list of codes whose Fedlex reading is not what the
  corpus means by them (USAGE_OVERRIDES, CANTONAL_HOMONYMS,
  REPEALED_ALIAS_EXCLUDED). Each entry carries its evidence; nothing else
  is curated.

  python3 scripts/build_law_code_table.py \
      --statutes output/statutes.db \
      --cantonal output/cantonal_laws.db \
      --fedlex-cache /tmp/fedlex_law_codes.json \
      --out docs/data/law_codes.json --csv docs/data/law_codes.csv

--fedlex-cache reuses an earlier Fedlex fetch (written on first use, or
again with --refresh-fedlex); --no-fedlex builds without the repealed
index and the entry-into-force dates.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Codes the corpus uses for another act than the one Fedlex abbreviates
# with them. "Not found" = share of cited article numbers that do not
# exist in that act (2026-10-26 anomaly report, statute_references.parquet).
USAGE_OVERRIDES = {
    "OPC": {
        "sr_number": "831.301",
        "note": "Fedlex gives OPC as the French short title of SR 281.41 "
                "(VVAG, 1923). In decisions it is the OPC-AVS/AI, SR 831.301: "
                "the extractor stops at the hyphen, so 'OPC-AVS/AI' arrives as "
                "'OPC'. 8,002 references, not found 69.1 % against 281.41, "
                "0.4 % against 831.301.",
        "fedlex_sr": "281.41",
    },
    "KLV": {
        "sr_number": "832.112.31",
        "note": "Upper-casing merges KLV (Krankenpflege-Leistungsverordnung, "
                "SR 832.112.31, since 1996) with KlV (Klimaschutzverordnung, "
                "SR 814.310.1, in force 2025-01-01). A decision before "
                "2025-01-01 can only mean 832.112.31; after that date the "
                "code is ambiguous and 832.112.31 remains the established "
                "reading. 4,806 references, not found 0.3 % against "
                "832.112.31, 30.5 % against 814.310.1.",
        "fedlex_sr": ["814.310.1", "832.112.31"],
    },
}

# Federal codes that cantonal courts mostly use for the canton's own act.
# They stay in alias_to_sr (federal courts use them for the federal act);
# a consumer should resolve them federally only in a federal decision.
# Value = not-found share of the cited articles in the federal act, by
# deciding canton (2026-10-26 anomaly report).
CANTONAL_HOMONYMS = {
    "STG": "Steuergesetz: BE 81.9 %, SG 69.6 %, GR 56.9 %, AR 66.2 %, "
           "OW 62.6 %, NW 76.3 %; federal courts 42.7 %",
    "PBG": "Planungs- und Baugesetz: SG 80.5 %, NW 44.4 %; federal courts 17.4 %",
    "LAF": "cantonal family-allowance acts: VD 90.0 %, TI 75.6 %, GE 47.1 %",
    "LC": "loi sur les communes: BE 61.3 %, VD 47.0 %, VS 45.3 %; "
          "federal courts 37.3 %",
    "LPERS": "cantonal staff acts: FR 57.5 %, VD 25.7 %; federal courts 3.5 %",
    "PG": "Personalgesetz: BE 48.3 %; federal courts 7.2 %",
    "LAS": "legge sull'assistenza sociale: TI 20.9 %",
}

# Fedlex short titles of repealed acts that collide with established
# usage; they are kept on the act but left out of alias_to_repealed.
REPEALED_ALIAS_EXCLUDED = {
    "LAG": "cantonal legal-aid act (TI)",
    "LAJ": "cantonal legal-aid acts (VD, VS, NE)",
    "OASI": "Italian short title of the AsylV (OAsi)",
    "TVA": "French for value-added tax (MWST)",
    "ALV": "Arbeitslosenversicherung",
    "OOA": "collides with established usage (2026-10-26 anomaly report)",
    "OGF": "collides with established usage (2026-10-26 anomaly report)",
    "OAJ": "collides with established usage (2026-10-26 anomaly report)",
    "BCO": "collides with established usage (2026-10-26 anomaly report)",
}


def _norm(a: str | None) -> str | None:
    a = (a or "").strip()
    return a or None


def federal(statutes_db: str) -> list[dict]:
    con = sqlite3.connect(f"file:{statutes_db}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(laws)")}
    work = "work_uri" if "work_uri" in cols else "NULL AS work_uri"
    out = []
    for r in con.execute(
            "SELECT sr_number, abbr_de, abbr_fr, abbr_it, "
            f"title_de, title_fr, title_it, {work} FROM laws ORDER BY sr_number"):
        de, fr, it = _norm(r["abbr_de"]), _norm(r["abbr_fr"]), _norm(r["abbr_it"])
        if not (de or fr or it):
            continue
        aliases = sorted({a.upper() for a in (de, fr, it) if a})
        out.append({
            "level": "federal",
            "sr_number": r["sr_number"],
            "abbr_de": de, "abbr_fr": fr, "abbr_it": it,
            "aliases": aliases,
            "title_de": r["title_de"], "title_fr": r["title_fr"],
            "title_it": r["title_it"],
            "eli": _norm(r["work_uri"]),
        })
    con.close()
    return out


def fetch_fedlex() -> dict:
    """Every SR work with its status and dates, plus the titles and short
    titles of the repealed ones (the in-force ones are in statutes.db).
    Network: two SPARQL passes against fedlex.data.admin.ch, ~10 minutes."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scrapers import fedlex
    works = fedlex.discover_laws()
    gone = sorted({w["work"] for w in works if fedlex._status_rank(w) == 2})
    return {"fetched": date.today().isoformat(), "works": works,
            "repealed_meta": fedlex.get_law_metadata(gone)}


def _is_repealed(row: dict, today: str) -> bool:
    # Same rule as scrapers/fedlex._status_rank: a passed
    # dateNoLongerInForce wins over the inForceStatus flag, which lags.
    nlif = (row.get("dateNoLongerInForce") or "")[:10]
    if nlif and nlif <= today:
        return True
    return (row.get("inForceStatus") or "").rstrip("/").endswith("/3")


def repealed_acts(fedlex_data: dict, today: str | None = None) -> list[dict]:
    """Repealed acts that carry a short title, one row per Fedlex work."""
    today = today or date.today().isoformat()
    by_work: dict[str, list[dict]] = {}
    for w in fedlex_data.get("works") or []:
        by_work.setdefault(w["work"], []).append(w)
    meta = fedlex_data.get("repealed_meta") or {}
    out = []
    for work, rows in by_work.items():
        if not all(_is_repealed(r, today) for r in rows):
            continue
        m = meta.get(work) or {}
        de, fr, it = (_norm(m.get(f"abbr_{lg}")) for lg in ("de", "fr", "it"))
        if not (de or fr or it):
            continue
        aliases = sorted({a.upper() for a in (de, fr, it) if a}
                         - set(REPEALED_ALIAS_EXCLUDED))
        out.append({
            "level": "federal_repealed",
            "sr_number": sorted({r["srNumber"] for r in rows})[0],
            "abbr_de": de, "abbr_fr": fr, "abbr_it": it,
            "aliases": aliases,
            "title_de": m.get("title_de"), "title_fr": m.get("title_fr"),
            "title_it": m.get("title_it"),
            "eli": work,
            "in_force_from": (rows[0].get("dateEntryInForce") or "")[:10] or None,
            "in_force_until": (rows[0].get("dateNoLongerInForce") or "")[:10] or None,
        })
    return sorted(out, key=lambda r: (r["sr_number"], r["in_force_from"] or "",
                                      r["eli"]))


def entry_into_force(fedlex_data: dict) -> dict[str, str]:
    """ELI work -> date of entry into force, for the acts in statutes.db."""
    return {w["work"]: w["dateEntryInForce"][:10]
            for w in fedlex_data.get("works") or []
            if w.get("dateEntryInForce")}


def alias_notes(fed: list[dict], alias_index: dict[str, list[str]]) -> dict:
    """Why an alias does not mean what a plain lookup suggests, keyed by
    alias, then by kind: case_collision, usage_override, cantonal_homonym,
    repealed_alias_excluded."""
    notes: dict[str, dict] = {}
    by_sr = {r["sr_number"]: r for r in fed}
    for al, srs in alias_index.items():
        if len(srs) < 2:
            continue
        forms = []
        for sr in srs:
            r = by_sr[sr]
            spelled = sorted({a for a in (r["abbr_de"], r["abbr_fr"], r["abbr_it"])
                              if a and a.upper() == al})
            forms.append({"sr_number": sr, "abbr": spelled,
                          "in_force_from": r.get("in_force_from")})
        if len({a for f in forms for a in f["abbr"]}) > 1:
            notes.setdefault(al, {})["case_collision"] = {
                "candidates": forms,
                "note": "Several acts bear this code in different case and "
                        "upper-casing merges them; an act cannot be meant "
                        "before its in_force_from.",
            }
    for al, o in USAGE_OVERRIDES.items():
        notes.setdefault(al, {})["usage_override"] = dict(o)
    for al, evidence in CANTONAL_HOMONYMS.items():
        notes.setdefault(al, {})["cantonal_homonym"] = {
            "note": "In a cantonal decision this code usually names the "
                    "canton's own act; resolve it to the federal act only in "
                    "a federal decision. Not found in the federal act, by "
                    f"deciding canton: {evidence}.",
        }
    for al, why in REPEALED_ALIAS_EXCLUDED.items():
        notes.setdefault(al, {})["repealed_alias_excluded"] = {"note": why}
    return dict(sorted(notes.items()))


def cantonal(db: str | None) -> list[dict]:
    """Cantonal acts keyed (canton, systematic number), titles per language.

    NOTE for consumers: cantonal_laws.laws carries canton, sr_number,
    title and language but NO abbreviation column — we do not hold a
    curated cantonal abbreviation list, so none is published here rather
    than one being invented. What is published is the (canton, number,
    title) triple per language, which is the part that has no other
    machine-readable source.
    """
    if not db or not Path(db).exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(laws)")}
    if not {"canton", "sr_number"} <= cols:
        con.close()
        return []
    grouped: dict[tuple, dict] = {}
    for r in con.execute(
            "SELECT canton, sr_number, language, title, is_active, "
            "original_url FROM laws"):
        num = _norm(r["sr_number"])
        canton = _norm(r["canton"])
        if not (canton and num):
            continue
        key = (canton, num)
        row = grouped.setdefault(key, {
            "level": "cantonal", "canton": canton,
            "systematic_number": num, "titles": {},
            "is_active": bool(r["is_active"]) if r["is_active"] is not None
            else None,
            "url": r["original_url"],
        })
        lang = (r["language"] or "").lower()[:2]
        if lang and r["title"]:
            row["titles"][lang] = r["title"]
    con.close()
    return sorted(grouped.values(),
                  key=lambda x: (x["canton"], x["systematic_number"]))


def build_payload(fed: list[dict], can: list[dict],
                  fedlex_data: dict | None) -> dict:
    if fedlex_data:
        eif = entry_into_force(fedlex_data)
        for r in fed:
            r["in_force_from"] = eif.get(r["eli"] or "")
    gone = repealed_acts(fedlex_data) if fedlex_data else []

    # alias -> SR, the lookup a consumer of the extractor actually needs
    alias_index: dict[str, list[str]] = {}
    for row in fed:
        for al in row["aliases"]:
            alias_index.setdefault(al, [])
            if row["sr_number"] not in alias_index[al]:
                alias_index[al].append(row["sr_number"])
    alias_to_sr = {k: (v[0] if len(v) == 1 else v)
                   for k, v in sorted(alias_index.items())}
    for al, o in USAGE_OVERRIDES.items():
        alias_to_sr[al] = o["sr_number"]

    alias_to_repealed: dict[str, list[str]] = {}
    for row in gone:
        for al in row["aliases"]:
            alias_to_repealed.setdefault(al, []).append(row["eli"])

    multilingual = [r for r in fed if len(r["aliases"]) > 1]
    return {
        "_": "OpenCaseLaw law-code reference table. Derived from the "
             "statute mirror and Fedlex, plus the documented corrections in "
             "alias_notes; regenerate with scripts/build_law_code_table.py.",
        "_why": "The citation extractor emits language-specific law codes. "
                "A consumer that does not pair them (OR==CO, ZGB==CC) "
                "silently loses most French-language statute references.",
        "_alias_to_sr": "Code (upper-cased, as the extractor emits it) -> SR "
                        "number of the act in force, or a list where several "
                        "acts in force share it. Check alias_notes before "
                        "trusting a lookup.",
        "_alias_to_repealed": "Code -> Fedlex ELI of every repealed act that "
                              "bore it (rows in `repealed`, with "
                              "in_force_from / in_force_until). A code in "
                              "both indexes was borne by an earlier act too: "
                              "resolve it by the decision date. Never map a "
                              "repealed act by its SR number alone; the number "
                              "is often reused by its successor.",
        "generated": date.today().isoformat(),
        "fedlex_fetched": (fedlex_data or {}).get("fetched"),
        "license": "CC0-1.0",
        "counts": {
            "federal_acts": len(fed),
            "federal_acts_with_multilingual_abbr": len(multilingual),
            "distinct_aliases": len(alias_index),
            "cantonal_codes": len(can),
            "repealed_acts": len(gone),
            "repealed_aliases": len(alias_to_repealed),
            "repealed_aliases_also_in_force":
                len(set(alias_to_repealed) & set(alias_to_sr)),
        },
        "alias_to_sr": alias_to_sr,
        "alias_notes": alias_notes(fed, alias_index),
        "alias_to_repealed": dict(sorted(alias_to_repealed.items())),
        "federal": fed,
        "repealed": gone,
        "cantonal": can,
    }


def _load_fedlex(cache: str | None, refresh: bool) -> dict:
    if cache and Path(cache).exists() and not refresh:
        return json.loads(Path(cache).read_text(encoding="utf-8"))
    data = fetch_fedlex()
    if cache:
        Path(cache).write_text(json.dumps(data, ensure_ascii=False),
                               encoding="utf-8")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--statutes", required=True)
    ap.add_argument("--cantonal")
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv")
    ap.add_argument("--fedlex-cache",
                    help="JSON of an earlier Fedlex fetch; written if missing")
    ap.add_argument("--refresh-fedlex", action="store_true")
    ap.add_argument("--no-fedlex", action="store_true",
                    help="skip Fedlex: no repealed index, no in-force dates")
    a = ap.parse_args()

    fedlex_data = None if a.no_fedlex else _load_fedlex(a.fedlex_cache,
                                                         a.refresh_fedlex)
    payload = build_payload(federal(a.statutes), cantonal(a.cantonal),
                            fedlex_data)
    fed, gone, can = payload["federal"], payload["repealed"], payload["cantonal"]
    c = payload["counts"]
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"wrote {outp}: {c['federal_acts']:,} federal acts "
          f"({c['federal_acts_with_multilingual_abbr']:,} multilingual), "
          f"{c['distinct_aliases']:,} aliases, {c['repealed_acts']:,} repealed "
          f"acts ({c['repealed_aliases']:,} aliases), "
          f"{c['cantonal_codes']:,} cantonal codes")

    if a.csv:
        cp = Path(a.csv)
        with open(cp, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["level", "canton", "number", "abbr_de", "abbr_fr",
                        "abbr_it", "aliases", "title_de"])
            for r in fed + gone:
                w.writerow([r["level"], "", r["sr_number"], r["abbr_de"] or "",
                            r["abbr_fr"] or "", r["abbr_it"] or "",
                            "|".join(r["aliases"]), r["title_de"] or ""])
            for r in can:
                t = r.get("titles") or {}
                w.writerow(["cantonal", r["canton"],
                            r["systematic_number"], "", "", "", "",
                            t.get("de") or t.get("fr") or t.get("it") or ""])
        print(f"wrote {cp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
