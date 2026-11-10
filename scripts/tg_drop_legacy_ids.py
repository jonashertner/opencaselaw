"""Thurgau yearbook rows held twice in tg_gerichte.jsonl (2026-11-09).

25 entries are held twice in the shard since 2026-02: once under an id the old
scraper minted from a ruling the entry cites (tg_gerichte_ZBR.2010.70) and once
under the yearbook id (tg_gerichte_RBOG-2017-15). The repair of 11-08 gave both
the same docket, date and text. At insert the build keeps the first (court,
docket, date) row it meets, the legacy one, so the cited ruling's id is served
for the yearbook entry and the TG yearbook fold never sees the yearbook-id row.

This drops each legacy row and records its id as previous_decision_id on the
yearbook-id row; the build turns that into an alias (legacy id -> yearbook id).

A pair qualifies only when: same source_url, exactly two rows, one id is
tg_gerichte_<docket_number>, the other is not, and date, docket and full_text
are identical, and the keeper has no previous_decision_id yet. Anything else
-> refusing.

DRY RUN by default. --apply hard-links <shard>.bak-tgids-<UTC date>, writes
<shard>.tmp (outside the build's *.jsonl glob), checks the line count and that
the shard did not change while it ran, and swaps with os.replace.
"""
import argparse, collections, datetime, json, os, sys


def plan(lines):
    rows = [json.loads(l) for l in lines]
    by = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r.get("source_url"):
            by[r["source_url"]].append(i)
    drop, alias, refusing = set(), {}, []
    for url, idx in by.items():
        if len(idx) < 2:
            continue
        if len(idx) != 2:
            refusing.append(f"{url}: {len(idx)} rows")
            continue
        a, b = (rows[i] for i in idx)
        yb = [i for i in idx if rows[i]["decision_id"] == f"tg_gerichte_{rows[i].get('docket_number')}"]
        if len(yb) != 1:
            refusing.append(f"{url}: no single yearbook-id row ({a['decision_id']}, {b['decision_id']})")
            continue
        keep = yb[0]
        old = idx[0] if idx[1] == keep else idx[1]
        k, o = rows[keep], rows[old]
        for f in ("decision_date", "docket_number", "full_text", "court"):
            if (k.get(f) or "") != (o.get(f) or ""):
                refusing.append(f"{url}: {f} differs ({o['decision_id']} vs {k['decision_id']})")
                break
        else:
            if (k.get("previous_decision_id") or "").strip():
                refusing.append(f"{url}: {k['decision_id']} already has previous_decision_id")
                continue
            drop.add(old)
            alias[keep] = o["decision_id"]
    return rows, drop, alias, refusing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", required=True)
    ap.add_argument("--expect", type=int, default=25)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    st0 = os.stat(a.shard)
    with open(a.shard, encoding="utf-8") as fh:
        lines = fh.readlines()
    rows, drop, alias, refusing = plan(lines)
    print(f"rows {len(rows)}  pairs {len(alias)}  drop {len(drop)}  refusing {len(refusing)}")
    for keep, old in sorted(alias.items()):
        print(f"  {old} -> {rows[keep]['decision_id']}")
    for r in refusing:
        print("  refusing:", r)
    if refusing or len(alias) != a.expect:
        sys.exit(f"stop: refusing {len(refusing)}, pairs {len(alias)} (expected {a.expect})")
    if not a.apply:
        print("dry run, nothing written")
        return
    out = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        if i in alias:
            r = rows[i]
            r["previous_decision_id"] = alias[i]
            r["previous_id_source"] = "tg_legacy_id"
            line = json.dumps(r, ensure_ascii=False) + "\n"
        out.append(line)
    assert len(out) == len(lines) - len(drop)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    bak = f"{a.shard}.bak-tgids-{day}"
    if os.path.exists(bak):
        sys.exit(f"refusing: {bak} exists")
    tmp = a.shard + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    st1 = os.stat(a.shard)
    if (st1.st_size, st1.st_mtime_ns, st1.st_ino) != (st0.st_size, st0.st_mtime_ns, st0.st_ino):
        os.unlink(tmp)
        sys.exit("refusing: shard changed while running")
    os.link(a.shard, bak)
    os.replace(tmp, a.shard)
    print(f"applied: {len(lines)} -> {len(out)} lines; backup {bak}")


if __name__ == "__main__":
    main()
