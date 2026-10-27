#!/usr/bin/env python3
"""Source-coverage audit — the durable "find all Swiss court decision sources" engine.

entscheidsuche.ch's Spiderliste.xml is the authoritative, maintained enumeration of Swiss court
decision sources (79 spiders / 433 chamber entries, each with a source URL + legal basis). This
audit fetches it and diffs the live universe against our coverage:
  - our active es ingest:   scrapers.entscheidsuche_ingest.SPIDER_MAP
  - our direct scrapers:    run_scraper.SCRAPERS
  - a curated spider->court map (below) for the spiders we cover directly (retired from SPIDER_MAP
    once a direct scraper reached parity — these are NOT name-normalizable, e.g. ZH_Obergericht->zh_gerichte).

It classifies every live spider, generates/refreshes the source catalogue (data/swiss_court_sources.json),
and reports the gap. NEW spiders (es added a court we don't know) and newly-uncovered sources fire an
ntfy alert (deduped on the residual digest); always exit 0. This is read-only and loop-safe — wire it
into /maintain + /review + a weekly timer. It does NOT ingest; ingestion is the gated /feature backlog.

Coverage classes:
  direct      our own scraper keeps it current (independent of es)
  es-active   we ingest it from es daily but have NO direct scraper (freshness/independence gap — Tier-a)
  es-frozen   covered only by the frozen es archive shard (retired feed, no direct scraper)
  uncovered   an es source we do not cover at all (the gap)
  es-meta     es internal bucket (XX_Upload per-canton manual upload) — inspect once, not a court
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SPIDERLISTE_URL = "https://entscheidsuche.ch/docs/Spiderliste.xml"
CATALOGUE = REPO / "docs" / "sources" / "swiss_court_sources.json"
STATE_FILE = REPO / "state" / "source_coverage_last_dispatched.json"

META_SPIDERS = {"XX_Upload"}  # es per-canton manual-upload bucket — one logical entry, not a court

# Curated es-spider -> our court_code. None = an es source we do NOT cover directly (gap or es-only).
# Built from the 2026-06-21 coverage census; the audit validates each value against run_scraper.SCRAPERS.
SPIDER_TO_COURT: dict[str, str | None] = {
    # Federal
    "CH_BGer": "bger", "CH_BGE": "bge", "CH_BVGer": "bvger", "CH_BPatG": "bpatger",
    "CH_BSTG": "bstger", "CH_WEKO": "weko", "CH_EDOEB": "edoeb", "CH_Bundesrat": "ch_bundesrat",
    "CH_UNIBE": "bge_historical",   # servat.unibe.ch / DFR historical BGE — verify overlap
    "CH_VB": None,                  # not refreshed since 2026-09-11 (removed from SPIDER_MAP): the spider
                                    # is an amtsdruckschriften portal crawl, 73% non-VPB pages; its VPB
                                    # PDFs live on as the frozen ch_vb shard. Classifies as "uncovered".
    "TA_SST": "ta_sst",
    # Aargau
    "AG_Gerichte": "ag_gerichte", "AG_Baugesetzgebung": None, "AG_Weitere": None,
    # Appenzell
    "AI_Aktuell": "ai_gerichte", "AI_Bericht": "ai_gerichte", "AR_Gerichte": "ar_gerichte",
    # Bern
    "BE_ZivilStraf": "be_zivilstraf", "BE_Anwaltsaufsicht": "be_anwaltsaufsicht",
    "BE_Verwaltungsgericht": "be_verwaltungsgericht", "BE_Steuerrekurs": "be_steuerrekurs",
    # BE_BVD: a be_bvd scraper exists in the working tree but is not yet committed/registered
    # upstream; map to None until it is (keeps this map consistent with the committed SCRAPERS).
    "BE_BVD": None, "BE_Weitere": None,
    # Basel
    "BL_Gerichte": "bl_gerichte", "BS_Omni": "bs_gerichte",
    # Rest of cantons
    "FR_Gerichte": "fr_gerichte", "GE_Gerichte": "ge_gerichte", "GL_Omni": "gl_gerichte",
    "GR_Gerichte": "gr_gerichte", "JU_Gerichte": "ju_gerichte", "LU_Gerichte": "lu_gerichte",
    "NE_Omni": "ne_gerichte", "NW_Gerichte": "nw_gerichte", "OW_Gerichte": "ow_gerichte",
    "SG_Gerichte": "sg_publikationen", "SG_Publikationen": "sg_publikationen", "SH_OG": "sh_gerichte",
    "SO_Omni": "so_gerichte", "SZ_Gerichte": "sz_gerichte", "SZ_Verwaltungsgericht": "sz_verwaltungsgericht",
    "TI_Gerichte": "ti_gerichte", "TG_OG": "tg_gerichte", "UR_Gerichte": "ur_gerichte",
    "VD_FindInfo": "vd_gerichte", "VD_Omni": "vd_gerichte", "VS_Gerichte": "vs_gerichte",
    "ZG_Verwaltungsgericht": "zg_verwaltungsgericht", "ZG_Obergericht": "zg_obergericht",
    "ZH_Obergericht": "zh_gerichte", "ZH_Verwaltungsgericht": "zh_verwaltungsgericht",
    "ZH_Steuerrekurs": "zh_steuerrekursgericht", "ZH_Baurekurs": "zh_baurekursgericht",
    "ZH_Sozialversicherungsgericht": "zh_sozialversicherungsgericht",
}


def fetch_spiderliste(text: str | None = None) -> list[dict]:
    """Parse Spiderliste.xml into [{canton, spider}] (dedups the per-canton XX_Upload). Pass `text`
    (a fixture) to stay offline; otherwise fetch live."""
    if text is None:
        text = urllib.request.urlopen(SPIDERLISTE_URL, timeout=25).read().decode("utf-8", "ignore")
    out, canton, seen = [], None, set()
    for m in re.finditer(r'<Kanton[^>]*Kurz="([^"]+)"|<Spider[^>]*Name="([^"]+)"', text):
        if m.group(1):
            canton = m.group(1)
        else:
            spider = m.group(2)
            key = spider if spider in META_SPIDERS else (canton, spider)
            if key in seen:
                continue
            seen.add(key)
            out.append({"canton": canton, "spider": spider})
    return out


def classify(spider: str, *, spider_map: set, scrapers: set) -> tuple[str, str | None]:
    """Return (coverage_class, court_code) for one spider."""
    if spider in META_SPIDERS:
        return "es-meta", None
    if spider not in SPIDER_TO_COURT:
        return "NEW", None  # es added a spider we have never mapped — drift alarm
    court = SPIDER_TO_COURT[spider]
    if court and court in scrapers:
        return "direct", court
    if spider in spider_map:
        return "es-active", court        # ingested from es daily, no direct scraper (Tier-a)
    if court:
        return "es-frozen", court         # retired feed, frozen archive only
    return "uncovered", None              # an es source we do not cover at all


def audit(*, spiderliste_text: str | None = None) -> dict:
    from scrapers.entscheidsuche_ingest import SPIDER_MAP
    import run_scraper
    spider_map = set(SPIDER_MAP)
    scrapers = set(run_scraper.SCRAPERS)

    rows, by_class = [], {}
    for entry in fetch_spiderliste(spiderliste_text):
        cov, court = classify(entry["spider"], spider_map=spider_map, scrapers=scrapers)
        rows.append({"canton": entry["canton"], "es_spider": entry["spider"],
                     "coverage": cov, "our_court": court})
        by_class.setdefault(cov, []).append(entry["spider"])

    # Mapping sanity: any curated court that isn't actually a registered scraper.
    bad_map = sorted(c for c in SPIDER_TO_COURT.values() if c and c not in scrapers)

    residual = sorted(set(by_class.get("uncovered", []) + by_class.get("NEW", [])))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_total": len(rows),
        "by_class": {k: sorted(set(v)) for k, v in by_class.items()},
        "counts": {k: len(set(v)) for k, v in by_class.items()},
        "residual_uncovered_or_new": residual,
        "es_active_no_direct": sorted(set(by_class.get("es-active", []))),
        "mapping_errors": bad_map,
        "rows": rows,
    }


def write_catalogue(report: dict) -> None:
    CATALOGUE.parent.mkdir(parents=True, exist_ok=True)
    CATALOGUE.write_text(json.dumps({
        "_doc": "Swiss court decision source catalogue — es universe (Spiderliste) + our coverage. "
                "Regenerated by scripts/source_coverage_audit.py. Beyond-es sources (regulators, "
                "recoverable gaps, cantonal long tail) are added as the ingestion program progresses.",
        "generated_at": report["generated_at"],
        "counts": report["counts"],
        "sources": sorted(report["rows"], key=lambda r: (r["canton"] or "", r["es_spider"])),
    }, indent=2, ensure_ascii=False))


def build_alerts(report: dict) -> list[str]:
    alerts = []
    for sp in report["by_class"].get("NEW", []):
        alerts.append(f"NEW es source {sp}: entscheidsuche added a court we don't map — investigate + add to the catalogue/scraper backlog")
    for sp in report["by_class"].get("uncovered", []):
        alerts.append(f"UNCOVERED es source {sp}: no direct scraper and not ingested — gap")
    if report["mapping_errors"]:
        alerts.append(f"MAPPING ERROR: curated courts not in SCRAPERS: {report['mapping_errors']}")
    return alerts


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Audit Swiss court source coverage vs the entscheidsuche universe.")
    p.add_argument("--no-ntfy", action="store_true")
    p.add_argument("--no-write", action="store_true", help="don't (re)write the catalogue")
    args = p.parse_args(argv)

    report = audit()
    if not args.no_write:
        write_catalogue(report)
    alerts = build_alerts(report)

    print(f"  es universe: {report['universe_total']} spider-entries")
    for cls in ("direct", "es-active", "es-frozen", "uncovered", "NEW", "es-meta"):
        n = report["counts"].get(cls, 0)
        if n:
            print(f"    {cls:10} {n}")
    if report["es_active_no_direct"]:
        print("  es-active (no direct scraper — independence/freshness gap):", report["es_active_no_direct"])
    if report["residual_uncovered_or_new"]:
        print("  RESIDUAL (uncovered / new):", report["residual_uncovered_or_new"])
    if report["mapping_errors"]:
        print("  MAPPING ERRORS:", report["mapping_errors"])
    for a in alerts:
        print("  ALERT:", a)

    if alerts and not args.no_ntfy:
        try:
            from scripts.check_scraper_freshness import post_ntfy, _alert_set_digest
            digest = _alert_set_digest(alerts)
            if digest != _load_state().get("digest"):
                if post_ntfy(alerts, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                             priority="default", title=f"opencaselaw source-coverage — {len(alerts)} item(s)"):
                    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    STATE_FILE.write_text(json.dumps({"digest": digest, "alerts": alerts}, indent=2))
        except Exception as e:
            print("  (ntfy skipped:", e, ")")
    return 0  # always exit 0


if __name__ == "__main__":
    sys.exit(main())
