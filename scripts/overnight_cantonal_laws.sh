#!/bin/bash
# overnight_cantonal_laws.sh — Run all cantonal law scrapers and build DB
# Designed to run unattended overnight on VPS.
#
# Usage: nohup bash scripts/overnight_cantonal_laws.sh >> logs/cantonal_laws_overnight.log 2>&1 &

set -e
cd /opt/caselaw/repo

# The two input corpora live on different filesystems and neither module
# default finds them: the direct shards are only on the volume, the LexFind
# fallback is only repo-local (the volume's lexfind_cantonal/ is empty, and
# pointing at it silently drops JU, SZ and VD — the three cantons that have
# no direct shard). Always pass all three paths explicitly.
OUTPUT=/mnt/HC_Volume_104655575/output/cantonal_laws_direct
LEXFIND=/opt/caselaw/repo/output/lexfind_cantonal
DB_OUT=/mnt/HC_Volume_104655575/output/cantonal_laws.db
LOG=logs/cantonal_laws_overnight.log

echo "=== Overnight cantonal laws scrape started: $(date -u) ==="

# Wait for any running LexWork scrape to finish (max 2h)
echo "Checking for running scrape..."
for i in $(seq 1 240); do
    if pgrep -f "scrape_cantonal_laws" > /dev/null 2>&1; then
        echo "  Scrape still running (check $i/240)..."
        sleep 30
    else
        echo "  No running scrape found."
        break
    fi
done

# Phase 1: Run remaining cantons that weren't in the initial batch
echo ""
echo "=== Phase 1: BE (LexWork) ==="
python3 scrape_cantonal_laws.py --canton BE --output "$OUTPUT" 2>&1

echo ""
echo "=== Phase 2: NE (SIL) ==="
python3 scrape_cantonal_laws.py --canton NE --output "$OUTPUT" 2>&1

echo ""
echo "=== Phase 3: ZH (PDF) ==="
python3 scrape_cantonal_laws.py --canton ZH --output "$OUTPUT" 2>&1

# Phase 2: Verify all expected cantons have data
echo ""
echo "=== Verification ==="
for canton in AG AI AR BE BL BS FR GE GL GR LU NE NW OW SG SH SO TG TI UR VS ZG ZH; do
    f="$OUTPUT/$canton.jsonl"
    if [ -f "$f" ] && [ -s "$f" ]; then
        lines=$(wc -l < "$f")
        size=$(du -h "$f" | cut -f1)
        echo "  OK $canton: $lines laws, $size"
    else
        echo "  MISSING $canton"
    fi
done

# Phase 3: Build the database
#
# _build_law_names reads cantonal_abbreviations.jsonl from beside the direct
# dir, so it has to be reachable on the volume or law_names comes out with
# titles and no abbreviations at all. Symlink rather than copy: the harvester
# writes repo-local, and a copy would go stale invisibly.
echo ""
echo "=== Building cantonal_laws.db ==="
ln -sfn /opt/caselaw/repo/output/cantonal_abbreviations.jsonl \
        /mnt/HC_Volume_104655575/output/cantonal_abbreviations.jsonl
python3 -m search_stack.build_cantonal_laws_db \
    --input-direct "$OUTPUT" \
    --input-lexfind "$LEXFIND" \
    --output "$DB_OUT" 2>&1

# Phase 4: Refuse to ship a corpus that lost or gained a canton's worth of
# laws. A wrong --input-direct reads as growth (28,957 on 2026-08-19), so the
# check is two-sided, and it runs before any worker sees the new inode.
echo ""
echo "=== Sanity check ==="
laws=$(python3 -c "import sqlite3;print(sqlite3.connect('file:$DB_OUT?mode=ro',uri=True).execute('SELECT count(*) FROM laws').fetchone()[0])")
echo "  laws: $laws"
if [ "$laws" -lt 14000 ] || [ "$laws" -gt 18000 ]; then
    echo "  ABORT: expected ~16,400 laws — not swapping workers onto this DB."
    exit 1
fi

# Phase 5: Symlink and restart MCP (all eight workers, 8770-8777)
echo ""
echo "=== Restarting MCP workers ==="
ln -sf "$DB_OUT" /opt/caselaw/repo/output/cantonal_laws.db
for p in 8770 8771 8772 8773 8774 8775 8776 8777; do
    systemctl restart "mcp-server@$p"
    sleep 3
done

echo ""
echo "=== Done: $(date -u) ==="
echo "DB: $(ls -lh "$DB_OUT")"
