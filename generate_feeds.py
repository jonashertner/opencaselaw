#!/usr/bin/env python3
"""Generate RSS 2.0 feeds for the OpenCaseLaw dashboard.

Static XML feeds for the newest decisions, generated from decisions.db and
written to docs/feed.xml + docs/feeds/*.xml. Hooked into publish.py Step 5b
(after stats are generated, before the early git push). GitHub Pages serves
the resulting files at https://opencaselaw.ch/feed.xml etc.

Outputs:
  docs/feed.xml             — top-50 latest across all Swiss courts (global)
  docs/feeds/bger.xml       — top-50 latest Bundesgericht
  docs/feeds/bvger.xml      — top-50 latest Bundesverwaltungsgericht
  docs/feeds/bge.xml        — top-50 latest BGE (Leitentscheide)
  docs/feeds/de.xml         — top-50 latest German-language
  docs/feeds/fr.xml         — top-50 latest French-language
  docs/feeds/it.xml         — top-50 latest Italian-language

Each <item> includes:
  - title:        court + docket + date
  - link:         https://mcp.opencaselaw.ch/entscheid/<id>
  - description:  regeste (or first 250 chars of full_text)
  - pubDate:      RFC 2822 (parsed from decision_date)
  - guid:         ECLI where derivable for federal courts, else decision_id
  - category:     court code + legal_area when present

Query strategy (2026-09-04):
  Every feed wants the LIMIT newest rows for one predicate (all courts, one
  court, one language). decisions has single-column indexes only, so for the
  filtered feeds the planner picks the predicate's index (idx_decisions_court
  / idx_decisions_language) and then sorts EVERY row of that court or
  language to find the top 50 — one random table-page read per row on a
  ~70 GB table: ~1.4 M reads across the six filtered feeds, 200-240 s on a
  quiet day and >300 s under weekday IO contention (Step 5b hit its cap on
  2026-09-03). The 2026-07-07 substr() fix only shrank the sorter payload.

  We now pin idx_decisions_date (INDEXED BY) and walk it newest-first with
  the predicate evaluated per row, floored at RECENT_WINDOW_DAYS so the walk
  is bounded; SQLite stops as soon as LIMIT rows are out. For the shipped
  feeds that is a few hundred to ~25 k row reads instead of 90 k-500 k. A
  feed with fewer than LIMIT matches inside the window (a court that has
  gone quiet), or a DB without the index, falls back to the original
  unpinned query — so the output is identical to before in every case.

  Both shapes also cap decision_date at date('now'): build_fts5 tolerates
  decision dates up to 365 days in the future (pending publications), and
  a DESC sort would otherwise float those rows to the top of every feed.

Publication order (2026-10-17):
  A feed lists what was PUBLISHED most recently: rows rank by publication_date,
  or decision_date where the court publishes none. BGer released 4A_524/2025
  (decided 2026-05-11) on 2026-10-16; by decision date it never reached the
  top 50. Sorting a whole window by that key would read every row in it, so
  query_published() keeps the bounded walk above and adds two small reads:
  with X = the decision_date of the 50th row of the walk, every row of the
  true top 50 has an effective date >= X (an effective date is never earlier
  than the decision date, publication before decision aside), so the top 50
  is exactly the newest of: rows decided on or after X, plus rows published
  on or after X — one short range per court on PUB_INDEX
  (court, publication_date). Without that index the feed keeps decision-date
  order.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import ecthr_docket

REPO_DIR = Path(__file__).parent.resolve()

ITEMS_PER_FEED = 50
SITE_URL = "https://opencaselaw.ch"
MCP_URL = "https://mcp.opencaselaw.ch"

# Index the newest-first walk is pinned to (db_schema.SCHEMA_SQL). If a DB
# lacks it we silently use the unpinned query instead of erroring out.
DATE_INDEX = "idx_decisions_date"
# Lower bound of the pinned walk. Bounds the worst case (a predicate with no
# recent rows would otherwise walk the whole index) at "rows dated in the
# last year" — ~40 k on the 2026-09 corpus. The six shipped feeds all have
# >50 rows well inside that window; BGE is the laggard (published months
# after the decision date) and still has ~250 a year.
RECENT_WINDOW_DAYS = 365
# (court, publication_date) index (db_schema.SCHEMA_SQL) behind the
# publication-ordered feeds; absent → decision-date order.
PUB_INDEX = "idx_decisions_court_pub"

COURT_NAMES = {
    "bger": "Bundesgericht",
    "bvger": "Bundesverwaltungsgericht",
    "bge": "BGE (Leitentscheide)",
    "bstger": "Bundesstrafgericht",
    "bpatger": "Bundespatentgericht",
    "bge_egmr": "EGMR (Schweiz)",
    "ecthr_chamber": "EGMR (Kammer)",
    "ecthr_grand_chamber": "EGMR (Grosse Kammer)",
    "ecthr_committee": "EGMR (Ausschuss)",
    "untb_ccpr": "UNO-Menschenrechtsausschuss (CCPR)",
    "untb_cat": "UNO-Ausschuss gegen Folter (CAT)",
    "untb_cerd": "UNO-Ausschuss gegen Rassendiskriminierung (CERD)",
    "untb_cedaw": "UNO-Frauenrechtsausschuss (CEDAW)",
    "untb_crc": "UNO-Kinderrechtsausschuss (CRC)",
    "untb_crpd": "UNO-Behindertenrechtsausschuss (CRPD)",
    "untb_cescr": "UNO-Sozialrechtsausschuss (CESCR)",
    "untb_ced": "UNO-Ausschuss gegen das Verschwindenlassen (CED)",
    "unwgad": "UNO-Arbeitsgruppe über willkürliche Inhaftierung (WGAD)",
}

LANG_NAMES = {"de": "Deutsch", "fr": "Français", "it": "Italiano"}

# Federal courts publish official ECLIs; cantonal courts mostly do not. For
# courts in this map we synthesize an ECLI from docket + date.
ECLI_FEDERAL_COURTS = {
    "bger": "BGer",
    "bge": "BGer",  # BGE is published by BGer, ECLI prefix matches
    "bvger": "BVGer",
    "bstger": "BStGer",
    "bpatger": "BPatGer",
}


def derive_ecli(court: str, docket: str | None, date: str | None) -> str | None:
    if not docket or not date or len(date) < 4:
        return None
    year = date[:4]
    if not year.isdigit():
        return None
    prefix = ECLI_FEDERAL_COURTS.get(court)
    if not prefix:
        return None
    docket_norm = docket.replace("/", ".").replace(" ", "")
    return f"ECLI:CH:{prefix}:{year}:{docket_norm}"


def parse_iso_date(date: str | None) -> datetime | None:
    if not date:
        return None
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(date[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def make_item(row: dict) -> ET.Element:
    item = ET.Element("item")

    court = row.get("court") or ""
    court_label = COURT_NAMES.get(court, court)
    # Strip the ECtHR _yyyymmdd key suffix — the RSS title is reader-facing.
    docket = ecthr_docket.display_docket(
        court, row.get("docket_number")) or row.get("decision_id", "")
    date = row.get("decision_date") or ""
    # pubDate is when the court published the ruling; the title keeps the
    # decision date, which is how a ruling is cited.
    published = (row.get("publication_date") or "")[:10] or date

    title = f"{court_label} {docket}".strip()
    if date:
        title += f" vom {date}"
    ET.SubElement(item, "title").text = title

    decision_id = row["decision_id"]
    # decision_ids may contain spaces or other URL-unsafe chars (cantonal data
    # can carry the source docket-number verbatim). Encode the path component
    # so RSS readers and HTTP clients don't choke.
    safe_id = urllib.parse.quote(decision_id, safe="")
    ET.SubElement(item, "link").text = f"{MCP_URL}/entscheid/{safe_id}"

    desc = (row.get("regeste") or "").strip()
    if not desc:
        ft = (row.get("full_text") or "").strip()
        desc = (ft[:250] + "…") if len(ft) > 250 else ft
    if desc:
        ET.SubElement(item, "description").text = desc

    dt = parse_iso_date(published)
    if dt is not None:
        ET.SubElement(item, "pubDate").text = format_datetime(dt)

    ecli = derive_ecli(court, docket, date)
    guid = ET.SubElement(item, "guid", isPermaLink="false")
    guid.text = ecli or decision_id

    if court:
        ET.SubElement(item, "category").text = court
    legal_area = (row.get("legal_area") or "").strip()
    if legal_area:
        ET.SubElement(item, "category").text = legal_area

    return item


def make_feed(title: str, description: str, channel_link: str, items: list[dict]) -> str:
    ATOM = "http://www.w3.org/2005/Atom"
    ET.register_namespace("atom", ATOM)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = channel_link
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "language").text = "de-CH"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, "generator").text = "OpenCaseLaw publish.py"

    atom_link = ET.SubElement(channel, f"{{{ATOM}}}link")
    atom_link.set("href", channel_link)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for row in items:
        channel.append(make_item(row))

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")


# full_text is a large blob in scattered overflow pages; make_item only ever
# uses its first 250 chars (regeste fallback). substr keeps the sorter payload
# small (2026-07-07: ~146x less spill IO); with the pinned walk the row's
# overflow chain is only read for rows that reach the result at all.
_SELECT_COLS = (
    "SELECT decision_id, court, docket_number, decision_date, language, "
    "regeste, substr(full_text, 1, 300) AS full_text, legal_area "
)
# The publication-ordered path (PUB_INDEX present, so the column exists).
_PUB_SELECT_COLS = _SELECT_COLS.replace(
    "decision_date, language", "decision_date, publication_date, language")


def feed_sql(where: str, *, pinned: bool = False, floored: bool = False,
             cols: str = _SELECT_COLS) -> str:
    """Build one feed query.

    ``pinned`` forces the newest-first walk of DATE_INDEX (INDEXED BY);
    ``floored`` appends ``decision_date >= ?`` — bind its value right after
    the predicate's own parameters, before LIMIT. Without either flag this is
    the original query (predicate index + full sort), kept as the fallback.

    Both shapes carry the same future-date ceiling, so the pinned walk and
    the fallback agree row for row.
    """
    hint = f" INDEXED BY {DATE_INDEX}" if pinned else ""
    floor = " AND decision_date >= ?" if floored else ""
    return (
        cols
        + f"FROM decisions{hint} "
        + f"WHERE {where} AND decision_date IS NOT NULL AND decision_date != '' "
        # decision_date is stored as ISO YYYY-MM-DD (build_fts5._normalize_dates
        # tolerates near-future typos up to 365 days out so legitimate pending
        # publications survive the corpus). Feeds are ordered by decision_date
        # DESC, so without this clause those future-dated rows sort to the top
        # of every feed. date('now') is UTC and, since both sides are ISO
        # YYYY-MM-DD, the lexical comparison matches a real date comparison.
        # On the pinned walk this is the upper bound of the index range, so
        # the future rows are never visited at all.
        + f"AND decision_date <= date('now'){floor} "
        + "ORDER BY decision_date DESC, decision_id "
        + "LIMIT ?"
    )


def has_index(conn: sqlite3.Connection, name: str = DATE_INDEX) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def recent_floor(today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    return (today - timedelta(days=RECENT_WINDOW_DAYS)).isoformat()


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_decisions(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    limit: int = ITEMS_PER_FEED,
    *,
    since: str | None = None,
    use_date_index: bool | None = None,
    stats: dict | None = None,
    cols: str = _SELECT_COLS,
) -> list[dict]:
    """Newest ``limit`` decisions matching ``where``.

    Tries the pinned, floored walk first (see module docstring); falls back
    to the original query when DATE_INDEX is absent or the window holds
    fewer than ``limit`` matches. ``stats``, when given, receives which path
    produced the result so the publish log shows a feed that went slow.
    """
    if use_date_index is None:
        use_date_index = has_index(conn)
    if use_date_index:
        floor = since or recent_floor()
        rows = _rows(conn.execute(
            feed_sql(where, pinned=True, floored=True, cols=cols), params + (floor, limit)))
        if len(rows) >= limit:
            if stats is not None:
                stats.update({"path": "date-walk", "window_rows": len(rows)})
            return rows
        window_rows = len(rows)
    else:
        window_rows = None
    rows = _rows(conn.execute(feed_sql(where, cols=cols), params + (limit,)))
    if stats is not None:
        stats.update({"path": "fallback", "window_rows": window_rows})
    return rows


def effective_date(row: dict) -> str:
    """Publication day, else decision date: the key a feed ranks by."""
    return (row.get("publication_date") or "")[:10] or (row.get("decision_date") or "")


def _feed_order(rows: list[dict]) -> list[dict]:
    # Newest effective date first; same tie-breaks as feed_sql (decision_date
    # DESC, decision_id ASC). Two stable sorts: id ascending, then the dates.
    rows = sorted(rows, key=lambda r: r["decision_id"])
    return sorted(rows, key=lambda r: (effective_date(r), r.get("decision_date") or ""),
                  reverse=True)


def query_published(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    limit: int = ITEMS_PER_FEED,
    *,
    courts: list[str],
    use_date_index: bool | None = None,
    stats: dict | None = None,
) -> list[dict]:
    """Newest ``limit`` rows matching ``where`` by effective date (see module
    docstring). ``courts`` are the court codes to probe on PUB_INDEX: the one
    court of a court feed, every court otherwise. Same exclusions as
    feed_sql: no NULL / '' / future decision_date, and no future
    publication_date either."""
    today = datetime.now(timezone.utc).date().isoformat()  # = date('now')

    def drop_future(rows: list[dict]) -> list[dict]:
        # A publication_date after today ranks by its decision date instead.
        for r in rows:
            if (r.get("publication_date") or "")[:10] > today:
                r["publication_date"] = None
        return rows

    if use_date_index is None:
        use_date_index = has_index(conn)
    seed = drop_future(query_decisions(conn, where, params, limit,
                                       use_date_index=use_date_index, stats=stats,
                                       cols=_PUB_SELECT_COLS))
    if len(seed) < limit:
        # The walk returned every matching row there is.
        return _feed_order(seed)[:limit]
    # `limit` rows rank at or above the oldest effective date among them, so
    # every row of the true top `limit` does too: rows decided since `cut`
    # plus rows published since `cut` hold all of them.
    cut = min(effective_date(r) for r in seed)
    hint = f" INDEXED BY {DATE_INDEX}" if use_date_index else ""
    decided = drop_future(_rows(conn.execute(
        _PUB_SELECT_COLS + f"FROM decisions{hint} "
        f"WHERE {where} AND decision_date >= ? AND decision_date <= date('now')",
        params + (cut,))))
    published: list[dict] = []
    for court in courts:
        published += _rows(conn.execute(
            _PUB_SELECT_COLS + f"FROM decisions INDEXED BY {PUB_INDEX} "
            f"WHERE court = ? AND publication_date >= ? AND ({where}) "
            "AND substr(publication_date, 1, 10) <= date('now') "
            "AND decision_date IS NOT NULL AND decision_date != '' "
            "AND decision_date <= date('now')",
            (court, cut) + params))
    if stats is not None:
        stats.update({"path": f"{stats.get('path')}+published",
                      "published_rows": len(published), "decided_rows": len(decided)})
    by_id = {r["decision_id"]: r for r in seed + decided + published}
    return _feed_order(list(by_id.values()))[:limit]


def write_atomic(path: Path, text: str) -> None:
    """Write via a sibling temp file + rename so a watchdog kill mid-write
    (publish.py SIGTERMs the whole process group at the step cap) can never
    leave a truncated feed for GitHub Pages to serve."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_DIR / "output" / "decisions.db"))
    ap.add_argument("--out", default=str(REPO_DIR / "docs"))
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")

    out = Path(args.out)
    feeds_dir = out / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    conn.execute("PRAGMA busy_timeout=30000")
    use_date_index = has_index(conn)
    if not use_date_index:
        print(f"note: {DATE_INDEX} missing — using unpinned queries")
    by_publication = has_index(conn, PUB_INDEX)
    if not by_publication:
        print(f"note: {PUB_INDEX} missing — feeds in decision-date order")
    all_courts: list[str] | None = None

    written = []

    def emit(rel: str, path: Path, title: str, description: str, link: str,
             where: str, params: tuple) -> None:
        nonlocal all_courts
        stats: dict = {}
        t0 = time.monotonic()
        if by_publication:
            if where == "court = ?":
                courts = [params[0]]
            else:
                if all_courts is None:
                    all_courts = [r[0] for r in conn.execute(
                        "SELECT DISTINCT court FROM decisions")]
                courts = all_courts
            items = query_published(conn, where, params, courts=courts,
                                    use_date_index=use_date_index, stats=stats)
        else:
            items = query_decisions(conn, where, params,
                                    use_date_index=use_date_index, stats=stats)
        write_atomic(path, make_feed(title, description, link, items))
        written.append((rel, len(items), time.monotonic() - t0, stats))

    emit(
        "feed.xml", out / "feed.xml",
        "OpenCaseLaw — Latest Swiss Court Decisions",
        "Latest published decisions across all Swiss courts indexed by OpenCaseLaw.",
        f"{SITE_URL}/feed.xml",
        "1=1", (),
    )

    for court, label in [
        ("bger", "Bundesgericht"),
        ("bvger", "Bundesverwaltungsgericht"),
        ("bge", "BGE (Leitentscheide)"),
    ]:
        emit(
            f"feeds/{court}.xml", feeds_dir / f"{court}.xml",
            f"OpenCaseLaw — {label} (Latest)",
            f"Latest published {label} decisions.",
            f"{SITE_URL}/feeds/{court}.xml",
            "court = ?", (court,),
        )

    for lang, label in LANG_NAMES.items():
        emit(
            f"feeds/{lang}.xml", feeds_dir / f"{lang}.xml",
            f"OpenCaseLaw — {label} (Latest)",
            f"Latest published Swiss court decisions in {label}.",
            f"{SITE_URL}/feeds/{lang}.xml",
            "language = ?", (lang,),
        )

    conn.close()

    print(f"Generated {len(written)} feeds:")
    for path, count, elapsed, stats in written:
        note = ""
        if str(stats.get("path", "")).startswith("fallback"):
            note = f", fallback: {stats.get('window_rows')} rows in the last {RECENT_WINDOW_DAYS}d"
        print(f"  ✓ {path} ({count} items, {elapsed:.1f}s{note})")


if __name__ == "__main__":
    main()
