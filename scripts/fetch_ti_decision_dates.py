#!/usr/bin/env python3
"""Fetch the portal's decision date for every undated ti_gerichte row.

sentenze.ti.ch states each ruling's date on its document page ("Data
decisione, Autorità: 07.05.2012, CRPTI"). The scraper reads that field since
2af5a6e7 (2026-09-15); rows stored before then can lack a date, and for ~100
of them the ruling's own heading carries none either ("Incarto n. 60.2012.58
Lugano In nome della Repubblica…"). This script re-reads that field from each
such row's own source_url, politely (one request every --delay seconds), and
writes one JSON line per row:

  {"decision_id", "docket_number", "source_url", "data_decisione", "autorita",
   "numero", "http_status", "fetched_at"}

scripts/repair_source_dates.py --court ti_gerichte --evidence <file> applies
it. Read-only on the shards; writes only --out. sentenze.ti.ch refuses the
Hetzner range: run it where the scraper's tunnel runs (the Mac), then copy the
file to the host.

Usage:
  python3 scripts/fetch_ti_decision_dates.py output/decisions/ti_gerichte.jsonl \\
      output/decisions/es_ti_gerichte.jsonl --out ti_decision_dates.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import source_dates as sd

COURT = "ti_gerichte"
_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def parse_document_page(html: str) -> dict:
    """The "Data decisione, Autorità" and "Numero d'incarto" cells of a
    sentenze.ti.ch document page (label and value sit in adjacent cells)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: dict = {"data_decisione": None, "autorita": None, "numero": None}
    for td in soup.find_all("td"):
        label = td.get_text(" ", strip=True)
        low = label.lower()
        if "data decisione" in low and out["data_decisione"] is None:
            val = label
            if not _DATE.search(label):
                nxt = td.find_next("td")
                val = f"{label} {nxt.get_text(' ', strip=True)}" if nxt else label
            m = _DATE.search(val)
            if m:
                out["data_decisione"] = sd.iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                a = re.search(r"\d{4}\s*,\s*([A-Za-z]{2,8})", val)
                out["autorita"] = a.group(1) if a else None
        elif "numero d" in low and out["numero"] is None:
            nxt = td.find_next("td")
            if nxt:
                out["numero"] = nxt.get_text(" ", strip=True) or None
    return out


def undated_rows(shards: list[Path]):
    seen = set()
    for p in shards:
        with open(p, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("court") != COURT:
                    continue
                if not sd.is_placeholder(rec.get("decision_date"), rec.get("scraped_at")):
                    continue
                did = rec.get("decision_id")
                if did in seen or not rec.get("source_url"):
                    continue
                seen.add(did)
                yield rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    import requests

    done = set()
    if args.out.exists():  # resumable
        with open(args.out, encoding="utf-8") as fh:
            done = {json.loads(line)["decision_id"] for line in fh if line.strip()}
    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (compatible; OpenCaseLaw date check)"
    n = 0
    with open(args.out, "a", encoding="utf-8") as out:
        for rec in undated_rows(args.shards):
            if rec["decision_id"] in done:
                continue
            if args.limit and n >= args.limit:
                break
            n += 1
            row = {"decision_id": rec["decision_id"], "docket_number": rec.get("docket_number"),
                   "source_url": rec["source_url"], "data_decisione": None, "autorita": None,
                   "numero": None, "http_status": None}
            try:
                r = sess.get(rec["source_url"], timeout=60)
                row["http_status"] = r.status_code
                if r.ok:
                    r.encoding = r.encoding or "latin-1"
                    row.update(parse_document_page(r.text))
            except requests.RequestException as e:
                row["error"] = str(e)[:200]
            row["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{row['decision_id']}: {row['data_decisione']} ({row['http_status']})", file=sys.stderr)
            time.sleep(args.delay)
    print(f"fetched {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
