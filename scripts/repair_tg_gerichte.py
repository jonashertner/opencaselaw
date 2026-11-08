#!/usr/bin/env python3
"""Re-read the held tg_gerichte rows with the fixed Thurgau parser.

Background (runbooks/tg_ge_sh_repair_2026-11.md): rechtsprechung.tg.ch sends
``text/html`` without a charset, so 2,285 of 2,304 rows hold UTF-8 text read
as Latin-1 ("EntschÃ¤digung"). The yearbook entries (RBOG/TVR) carried no
regeste, no underlying docket, and 1 January of the volume as their date
whenever the date sat in a March signature or none was found (TVR 2008 Nr. 19
was dated from a birthday, 1928-06-14). The scraper now parses the bytes and
reads the entry's own head block (subject line, provisions, Leitsatz) and
signature line (date, docket, deciding body); this script applies the same
parser to the rows already in the shard.

Two stages:

  --fetch     re-download each inline entry page from rechtsprechung.tg.ch
              into --cache (one file per URL, skipped when present; 1.5 s
              between requests). PDF-only rulings are not re-downloaded: their
              text came from the PDF and is not affected.
  (default)   rebuild every row from the cached page (scrapers.cantonal.
              tg_gerichte.parse_entry_page) or, for a PDF ruling or a page that
              could not be fetched, from the stored text with the encoding
              undone (fix_mojibake + derive_entry_fields; no regeste then).

Changes full_text, regeste, decision_date, docket_number_2, chamber, title,
language and cited_decisions. A yearbook row whose docket_number the old
scraper replaced with a docket cited in its first 1,000 characters (25 rows:
tg_gerichte_RBOG-2017-15 carried "ZBR.2010.70", a ruling it cites) gets its
own yearbook docket back, the one its decision_id was minted from (or, for 25
rows whose id was minted from the overwritten docket, the one of the entry
page; build_fts5's TG yearbook fold then serves the yearbook-id row). Never
changes decision_id, court or source_url, and never drops a row. A row whose re-read text is less than
half the length of its stored text keeps the stored text (encoding undone).
A changed date is recorded in the row as ``tg_repair.date_from``.

DRY RUN by default. --apply hard-links <shard>.bak-tg-<UTC date> beside the
shard, writes <shard>.tmp (neither matches the build's *.jsonl glob), checks
the line count and that the shard did not change while it ran, and swaps the
new file in atomically. Run it outside the build window with no tg_gerichte
scrape running.

Usage:
  python3 scripts/repair_tg_gerichte.py --fetch --cache /tmp/tg-cache
  python3 scripts/repair_tg_gerichte.py --cache /tmp/tg-cache --report /tmp/tg.json
  python3 scripts/repair_tg_gerichte.py --cache /tmp/tg-cache --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bs4 import BeautifulSoup  # noqa: E402

from models import detect_language, extract_citations  # noqa: E402
from scrapers.cantonal.tg_gerichte import (  # noqa: E402
    RE_YEARBOOK_DOCKET,
    derive_entry_fields,
    fix_mojibake,
    parse_entry_page,
)

COURT = "tg_gerichte"
DEFAULT_SHARD = REPO / "output" / "decisions" / "tg_gerichte.jsonl"
UNCHANGED = ("decision_id", "court", "source_url", "canton")
_YEARBOOK_ID_RE = re.compile(r"^tg_gerichte_((?:RBOG|TVR)-\d{4}-\d+)$")
_YEARBOOK_URL_RE = re.compile(r"rechtsprechung\.tg\.ch/(?:og/(rbog)|vg/(tvr))-(\d{4})-nr-0*(\d+)/?$")


def own_yearbook_docket(rec: dict) -> str | None:
    """The yearbook docket a row stands for: the one its decision_id was minted
    from, else the one of the entry page it was read from (25 rows carry an id
    minted from an overwritten docket, tg_gerichte_ZR.2010.10 for RBOG 2010
    Nr. 8, next to tg_gerichte_RBOG-2010-8)."""
    m = _YEARBOOK_ID_RE.match(rec.get("decision_id") or "")
    if m:
        return m.group(1)
    m = _YEARBOOK_URL_RE.search(rec.get("source_url") or "")
    if m:
        return f"{(m.group(1) or m.group(2)).upper()}-{m.group(3)}-{int(m.group(4))}"
    return None
HOST = "https://rechtsprechung.tg.ch/"
FETCH_DELAY = 1.5
USER_AGENT = "OpenCaseLaw repair (opencaselaw.ch)"


def cache_path(cache: Path, url: str) -> Path:
    return cache / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def needs_page(rec: dict) -> bool:
    """An inline entry whose page we re-read (a PDF ruling's text is its PDF's)."""
    return (str(rec.get("source_url") or "").startswith(HOST) and not rec.get("pdf_url"))


def fetch(shard: Path, cache: Path, limit: int | None = None) -> Counter:
    import requests

    cache.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    with open(shard, "rb") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    for rec in recs:
        if not needs_page(rec):
            stats["skipped_pdf_or_foreign"] += 1
            continue
        url = rec["source_url"]
        path = cache_path(cache, url)
        if path.exists():
            stats["cached"] += 1
            continue
        if limit is not None and stats["fetched"] + stats["failed"] >= limit:
            break
        time.sleep(FETCH_DELAY)
        try:
            r = session.get(url, timeout=30)
        except Exception as e:  # network error: the row falls back to its stored text
            print(f"  fetch failed {url}: {e}", file=sys.stderr)
            stats["failed"] += 1
            continue
        if r.status_code != 200:
            print(f"  {r.status_code} {url}", file=sys.stderr)
            stats[f"http_{r.status_code}"] += 1
            stats["failed"] += 1
            continue
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)
        stats["fetched"] += 1
    return stats


def _iso(d) -> str | None:
    return d.isoformat() if isinstance(d, date) else (d or None)


def repair(rec: dict, cache: Path | None) -> tuple[dict, str]:
    """(the repaired record, how it was derived) for one shard record."""
    docket = rec.get("docket_number") or ""
    own = own_yearbook_docket(rec)
    if own and docket != own:
        docket = own
    stored = fix_mojibake(rec.get("full_text") or "")
    page = None
    how = "stored_text"
    if cache is not None and needs_page(rec):
        path = cache_path(cache, rec["source_url"])
        if path.exists():
            soup = BeautifulSoup(path.read_bytes(), "html.parser")
            page = parse_entry_page(soup, rec["source_url"], docket)  # no pdf_text_fn: inline only
            how = "page"
    if page is not None and len(page["full_text"]) >= 0.5 * len(stored):
        text, regeste = page["full_text"], page["regeste"]
        fields = page
    else:
        if page is not None:
            how = "page_short_kept_stored"
        text = stored
        regeste = page["regeste"] if page is not None else None
        fields = derive_entry_fields(text, docket, is_pdf=bool(rec.get("pdf_url")))
    new = dict(rec)
    if docket != rec.get("docket_number"):
        new["docket_number"] = docket
        new.setdefault("tg_repair", {})["docket_from"] = rec.get("docket_number")
    new["full_text"] = text
    new["regeste"] = regeste or rec.get("regeste") or None
    new["docket_number_2"] = fields["docket_number_2"]
    new["chamber"] = fields["chamber"]
    new["title"] = fix_mojibake(rec.get("title"))
    new_date = _iso(fields["decision_date"])
    if new_date != rec.get("decision_date"):
        new["decision_date"] = new_date
        new.setdefault("tg_repair", {})["date_from"] = rec.get("decision_date")
    if len(text) > 100:
        new["language"] = detect_language(text)
    new["cited_decisions"] = extract_citations(text) if len(text) > 200 else []
    return new, how


def check(before: dict, after: dict) -> None:
    for k in UNCHANGED:
        if before.get(k) != after.get(k):
            raise SystemExit(f"refusing: {before.get('decision_id')} would change {k}")
    if not after.get("full_text"):
        raise SystemExit(f"refusing: {before.get('decision_id')} would lose its text")
    if after.get("docket_number") != before.get("docket_number"):
        if after["docket_number"] != own_yearbook_docket(before):
            raise SystemExit(f"refusing: {before.get('decision_id')} would change docket_number "
                             f"to {after.get('docket_number')!r}")


def summarize(recs: list[dict]) -> dict:
    """Before/after counts for the runbook."""
    yb = [r for r in recs if RE_YEARBOOK_DOCKET.match(r.get("docket_number") or "")]
    return {
        "rows": len(recs),
        "yearbook_rows": len(yb),
        "mojibake_rows": sum(1 for r in recs if "Ã" in (r.get("full_text") or "")
                             or "Â§" in (r.get("full_text") or "")),
        "regeste_filled": sum(1 for r in recs if (r.get("regeste") or "").strip()),
        "docket_number_2_filled": sum(1 for r in recs if (r.get("docket_number_2") or "").strip()),
        "chamber_filled": sum(1 for r in recs if (r.get("chamber") or "").strip()),
        "date_null": sum(1 for r in recs if not r.get("decision_date")),
        "date_jan1": sum(1 for r in recs if str(r.get("decision_date") or "").endswith("-01-01")),
        "yearbook_docket_not_own": sum(1 for r in recs if (own := own_yearbook_docket(r))
                                       and r.get("docket_number") != own),
        "yearbook_date_jan1": sum(1 for r in yb if str(r.get("decision_date") or "").endswith("-01-01")),
        "date_before_1990": sum(1 for r in recs if r.get("decision_date") and r["decision_date"] < "1990"),
    }


def run(shard: Path, cache: Path | None, apply: bool, report: Path | None, examples: int) -> dict:
    st = os.stat(shard)
    with open(shard, "rb") as fh:
        raws = fh.readlines()
    before, after, out = [], [], []
    stats: Counter = Counter()
    shown = 0
    for raw in raws:
        stats["lines"] += 1
        line = raw.rstrip(b"\r\n")
        try:
            rec = json.loads(line) if line.strip() else None
        except ValueError:
            rec = None
        if not isinstance(rec, dict) or rec.get("court") != COURT:
            stats["passed_through"] += 1
            out.append(raw)
            continue
        new, how = repair(rec, cache)
        check(rec, new)
        stats[f"derived_from:{how}"] += 1
        before.append(rec)
        after.append(new)
        if new == rec:
            out.append(raw)
            continue
        stats["rows_changed"] += 1
        for k in ("full_text", "regeste", "decision_date", "docket_number", "docket_number_2", "chamber", "title"):
            if new.get(k) != rec.get(k):
                stats[f"changed:{k}"] += 1
        if shown < examples:
            shown += 1
            print(f"  {rec['decision_id']}: {rec.get('decision_date')} -> {new.get('decision_date')} "
                  f"d2={new.get('docket_number_2')!r} chamber={new.get('chamber')!r} "
                  f"regeste={(new.get('regeste') or '')[:60]!r}", file=sys.stderr)
        out.append(json.dumps(new, ensure_ascii=False).encode("utf-8") + b"\n")
    result = {"stats": dict(sorted(stats.items())),
              "before": summarize(before), "after": summarize(after)}
    if report:
        rows = [{"decision_id": a["decision_id"], "docket_number": a.get("docket_number"),
                 "date_before": b.get("decision_date"), "date_after": a.get("decision_date"),
                 "docket_number_2": a.get("docket_number_2"), "chamber": a.get("chamber"),
                 "regeste": a.get("regeste"), "source_url": a.get("source_url")}
                for b, a in zip(before, after)]
        report.write_text(json.dumps({**result, "rows": rows}, ensure_ascii=False, indent=1))
    if apply:
        tmp = shard.with_name(shard.name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.writelines(out)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            with open(tmp, "rb") as fh:
                written = sum(1 for _ in fh)
            if written != stats["lines"]:
                raise SystemExit(f"refusing: wrote {written} lines, read {stats['lines']}")
            now = os.stat(shard)
            if (now.st_size, now.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                raise SystemExit("refusing: the shard changed while the repair ran (a scrape?)")
            stamp = time.strftime("%Y%m%d", time.gmtime())
            backup = shard.with_name(f"{shard.name}.bak-tg-{stamp}")
            if backup.exists():
                raise SystemExit(f"refusing to overwrite existing backup {backup}")
            os.link(shard, backup)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.replace(tmp, shard)
            print(f"rewrote {shard}; backup {backup}", file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shard", type=Path, default=DEFAULT_SHARD)
    ap.add_argument("--cache", type=Path, help="directory of re-downloaded entry pages")
    ap.add_argument("--fetch", action="store_true", help="download missing pages into --cache, then stop")
    ap.add_argument("--fetch-limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--report", type=Path, help="write per-row before/after JSON here")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args(argv)
    if not args.shard.exists():
        print(f"shard not found: {args.shard}", file=sys.stderr)
        return 2
    if args.fetch:
        if not args.cache:
            ap.error("--fetch needs --cache")
        print(json.dumps(dict(fetch(args.shard, args.cache, args.fetch_limit)), indent=1))
        return 0
    print(("APPLY" if args.apply else "DRY RUN") + f": {args.shard}", file=sys.stderr)
    result = run(args.shard, args.cache, args.apply, args.report, args.examples)
    print(json.dumps({k: result[k] for k in ("stats", "before", "after")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
