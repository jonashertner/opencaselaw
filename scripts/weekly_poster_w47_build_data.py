#!/usr/bin/env python3
"""Week 47 (13 - 19 November 2026): count the week's usual arrivals from the public nightly deltas.

    python3 scripts/weekly_poster_w47_build_data.py <dir with the HF delta parquet files>

Reads artifacts/parquet/deltas/<day>.parquet of voilaj/swiss-caselaw (download them first; the
week needs 2026-11-12 .. 2026-11-19), keeps the rows first scraped between the build cut-offs of
Thursday 12 and Thursday 19 November (03:30 UTC), drops the international sources and the batch
ingestions listed in BATCHES, and writes weekly_poster_w47_data.json. No load on the server.

The weekly figure counts usual arrivals only (Jonas, 13 November): what the courts published and the
nightly scrapers picked up. First loads, backfills and catch-ups are left out and not shown.
Find them with --table: rows per (court, scrape day) with the range of decision dates; a batch shows
as one day with hundreds of rows or with old decision dates.
"""
from __future__ import annotations

import collections
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

LO, HI = "2026-11-12T03:30", "2026-11-19T03:30"
END = date(2026, 11, 19)
OUT = Path(__file__).with_name("weekly_poster_w47_data.json")
COLS = ["decision_id", "court", "canton", "decision_date", "language", "scraped_at"]
INTERNATIONAL = ("untb_", "unwgad", "cjeu", "icj", "ecthr", "hudoc", "bge_egmr")

# court -> reason, or (court, scrape day) -> reason. Rows matched here are batches, not the week.
BATCHES: dict = {
    "bge": "Federal Supreme Court official collection: early volumes (1877, 1909)",
    "bpatger": "Federal Patent Court: same-docket second rulings recovered (2012-2026)",
}

# (court, scrape day) -> (cut-off date, reason): on that night rows decided BEFORE the cut-off are the
# same-docket recovery run (second rulings under a known docket, decided 2014-2026), the rest are the
# usual nightly pickup. bvger 19.11: 236 rows, 18 decided in October/November 2026.
OLDER_THAN: dict = {
    ("bvger", "2026-11-19"): ("2026-10-01", "Federal Administrative Court: same-docket second rulings recovered"),
}

# Court seats, LV03 km (E - 2000 000, N - 1000 000 from LV95 metres, rounded): where a decision is
# placed for the strip's DIR dial. Cantons by their capital, federal courts by their seat.
SEAT = {
    "ZH": (683, 248), "BE": (600, 200), "LU": (666, 211), "UR": (691, 193), "SZ": (692, 208),
    "OW": (661, 194), "NW": (670, 201), "GL": (724, 211), "ZG": (681, 224), "FR": (578, 184),
    "SO": (607, 228), "BS": (611, 267), "BL": (622, 259), "SH": (690, 284), "AR": (739, 250),
    "AI": (749, 244), "SG": (746, 254), "GR": (759, 191), "AG": (646, 249), "TG": (709, 268),
    "TI": (722, 117), "VD": (538, 152), "VS": (593, 120), "NE": (561, 204), "GE": (500, 118),
    "JU": (593, 245),
    "bger@VD": (538, 152), "bger@LU": (666, 211), "bvger": (746, 254), "bpatger": (746, 254),
    "bstger": (722, 117), "bge": (538, 152), "CH": (600, 200),
}
CENTRE = (660, 183)  # Älggi-Alp, the geographic centre of Switzerland


def court_key(r: dict) -> str:
    """bger by seat: the social-law divisions (8C, 9C) sit in Lucerne, the others in Lausanne."""
    if r["court"] == "bger":
        docket = r["decision_id"].split("_", 1)[1]
        return "bger@LU" if docket[:2] in ("8C", "9C") else "bger@VD"
    return r["court"]


def is_batch(r: dict) -> str | None:
    day = str(r["scraped_at"])[:10]
    cut = OLDER_THAN.get((r["court"], day))
    if cut and str(r["decision_date"])[:10] < cut[0]:
        return cut[1]
    return BATCHES.get((r["court"], day)) or BATCHES.get(r["court"])


def arrival_day(scraped: str) -> date:
    """The build that picked the row up: the 24 hours ending at 03:30 UTC on that day."""
    t = datetime.fromisoformat(scraped[:19])
    return (t - timedelta(hours=3, minutes=30)).date() + timedelta(days=1)


def main(src: Path, table: bool = False) -> None:
    seen, rows = set(), []
    for f in sorted(src.glob("2026-11-*.parquet")):
        for r in pq.read_table(f, columns=COLS).to_pylist():
            s = str(r["scraped_at"] or "")[:16].replace(" ", "T")
            if not (LO <= s < HI) or r["court"].startswith(INTERNATIONAL) or r["decision_id"] in seen:
                continue
            seen.add(r["decision_id"])
            r["scraped_at"] = s
            rows.append(r)
    if table:
        g = collections.defaultdict(list)
        for r in rows:
            g[(r["court"], r["scraped_at"][:10])].append(str(r["decision_date"] or ""))
        for (c, d), ds in sorted(g.items(), key=lambda kv: -len(kv[1])):
            print(f"{c:32s} {d} {len(ds):6d} {min(ds)}..{max(ds)}")
        return

    batch = collections.Counter()
    week = []
    for r in rows:
        if is_batch(r):
            batch[r["court"]] += 1
        else:
            week.append(r)
    n = len(week)
    by_canton = collections.Counter(r["canton"] for r in week)
    by_court = collections.Counter(court_key(r) for r in week)
    lang = collections.Counter(r["language"] for r in week)
    dd = [str(r["decision_date"])[:10] for r in week]
    by_year = collections.Counter(d[:4] for d in dd)
    by_month = collections.Counter(d[:7] for d in dd)
    dates = {"CH": collections.Counter(), "KT": collections.Counter()}
    for r, d in zip(week, dd):
        dates["CH" if r["canton"] == "CH" else "KT"][d] += 1
    by_arrival = collections.Counter(arrival_day(r["scraped_at"]).isoformat() for r in week)
    cutoff = (END - timedelta(days=30)).isoformat()
    fresh = sum(1 for d in dd if d >= cutoff)

    sx = sy = 0.0
    for r in week:
        k = court_key(r) if r["canton"] == "CH" else r["canton"]
        x, y = SEAT.get(k, SEAT["CH"] if r["canton"] == "CH" else SEAT[r["canton"]])
        sx, sy = sx + x, sy + y
    cx, cy = sx / n, sy / n

    data = {
        "_note": ("Week 47, counted row by row from the public nightly deltas (HF voilaj/swiss-caselaw, "
                  "artifacts/parquet/deltas/) by scripts/weekly_poster_w47_build_data.py: rows first scraped "
                  f"between the build cut-offs {LO} and {HI} UTC, without the international sources. "
                  "Usual arrivals only; batches are listed under 'batches' and not shown. "
                  f"Delta files read: {', '.join(sorted(f.stem for f in src.glob('2026-11-*.parquet')))}."),
        "preliminary": "2026-11-19" not in {f.stem for f in src.glob("*.parquet")},
        "verified": {
            "added": n,
            "by_canton": dict(by_canton.most_common()),
            "by_court": dict(by_court.most_common()),
            "languages": dict(lang.most_common()),
            "by_year": dict(sorted(by_year.items())),
            "by_month": dict(sorted(by_month.items())),
            "dates": {k: dict(sorted(v.items())) for k, v in dates.items()},
            "by_arrival_day": {(END - timedelta(days=6 - i)).isoformat(): by_arrival.get((END - timedelta(days=6 - i)).isoformat(), 0)
                               for i in range(7)},
            "fresh_30d": fresh,
            "centroid_lv03": [round(cx, 1), round(cy, 1)],
        },
        "batches": {"total": sum(batch.values()), "by_court": dict(batch.most_common()),
                    "why": {**{str(k): v for k, v in BATCHES.items()},
                            **{str(k): f"{v[1]} (decided before {v[0]})" for k, v in OLDER_THAN.items()}}},
    }
    assert sum(data["verified"]["by_arrival_day"].values()) == n, data["verified"]["by_arrival_day"]
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(n, "usual arrivals;", sum(batch.values()), "batch rows;", dict(lang), "centroid", data["verified"]["centroid_lv03"])


if __name__ == "__main__":
    main(Path(sys.argv[1]), table="--table" in sys.argv)
