# Pinpoint semantic-rescue rollout

Adding multilingual embedding-based semantic similarity as a fallback
to the lexical pinpoint resolver. Closes the lexical ceiling identified
in the deep empirical investigation: **even the official Regeste of a
decision doesn't lexically match its own Erwägungen** (vocabularies
diverge between summary and reasoning), so BM25 alone has a hard
recall ceiling on Regeste-style claims.

## Architecture (already shipped, commit `97790c5`)

```
                ┌──────────────────────────────────────────────┐
                │ User claim                                   │
                └──────────────────────────────────────────────┘
                                  │
                                  ▼
                ┌──────────────────────────────────────────────┐
                │ _compute_pinpoint(decision_id, claim)        │
                │                                              │
                │   1. Two-pass FTS5 (phrase → OR fallback)    │
                │   2. Shared scorer (gap + coverage + abs)    │
                │   3. If lexical confident → return           │
                │      source="lexical" + "high"|"medium"      │
                │                                              │
                │   ──── lexical returned None ────            │
                │                                              │
                │   4. _compute_pinpoint_semantic_rescue:      │
                │      • encode claim (~30 ms CPU)             │
                │      • cosine vs ALL paragraphs of decision  │
                │      • if cosine ≥ 0.70 → high               │
                │        if cosine ≥ 0.55 → medium             │
                │        else → None                           │
                │      • returns source="semantic"             │
                └──────────────────────────────────────────────┘
```

**Safety properties** (pinned by `tests/test_pinpoint_semantic_rescue.py`):
- Semantic NEVER overrides confident lexical match (lexical-winner test)
- Semantic suppressed below cos 0.55 floor (no spurious matches)
- Graceful no-op when feature flag off / model load fails / DB missing
- Source-tagged so callers can hedge differently for semantic-only matches

## Empirical proof (2026-05-10, on real production data)

Encoded 19 paragraphs across 3 known-FN BGE decisions. The same claims
that returned `(none)` from lexical alone now surface via semantic:

| BGE | Lexical | Semantic Rescue |
|---|---|---|
| BGE 134 V 231 (UVG/fMRT)        | `(none)` | **E.5.2 medium cos=0.69** |
| BGE 140 III 86 (FR procedure)   | `(none)` | `(none)` (cross-lang gap) |
| BGE 133 III 121 (civil)         | `(none)` | **E.4.1.2 medium cos=0.70** |

E.5.2 in BGE 134 V 231 IS the paragraph beginning "Bei der funktionellen
Magnetresonanztomographie (fMRT; englisch: functional magnetic resonance
imaging…)" — a perfect semantic match for "Beweiswert diagnostischer
Methoden funktionelle Magnetresonanztomographie".

## Encoding the corpus (historical; started 2026-05-10 07:51 UTC)

**Superseded.** The commands in this section encode directly against the
live structure DB with no build-window gate and are kept only as the
record of the initial run. The current procedure is
`tools/embeddings_topup.sh`: it waits for the 21:00–03:00 UTC window,
copies the live DB to a working copy first, encodes the copy at idle
priority and swaps it in. Do not run the `nohup` lines below as-is.

```bash
# Started on VPS (2026-05-10, before the build-window rule; see note above):
nohup nice -n 19 ionice -c 3 \
  env OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 PYTHONUNBUFFERED=1 \
  python3 -m search_stack.build_paragraph_embeddings \
    --structure-db /opt/caselaw/repo/output/decision_structure.db \
    --output-db   /mnt/HC_Volume_104655575/output/paragraph_embeddings.db \
    --batch-size 128 --verbose \
  >> /var/log/encode_paragraphs.log 2>&1 &
```

- **Model**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (117M params, 384-dim, supports DE/FR/IT/RM + 47 other languages)
- **Throughput**: ~33 docs/sec on shared CPU (will speed up post-publish)
- **ETA**: ~73 h (3 days) for 8.8 M paragraphs
- **Output size**: ~17 GB (BLOB + minor index overhead)
- **Resume-safe**: writes a watermark to `encode_progress` table every
  batch; restart from same offset on interruption.

### Monitoring

```bash
# Progress log (every batch)
tail -f /var/log/encode_paragraphs.log

# Encoded row count
ssh root@46.225.212.40 'python3 -c "
import sqlite3
c = sqlite3.connect(\"file:/mnt/HC_Volume_104655575/output/paragraph_embeddings.db?immutable=1\", uri=True)
n = c.execute(\"SELECT COUNT(*) FROM paragraph_embeddings\").fetchone()[0]
total = 8_832_631
print(f\"{n:,} / {total:,} = {n/total:.1%}\")"'

# DB size
ls -lh /mnt/HC_Volume_104655575/output/paragraph_embeddings.db

# Process state
ps -p $(pgrep -f build_paragraph_embeddings) -o pid,ni,%cpu,%mem,stat,etime,cmd
```

### Resume after interruption

The encoder is idempotent. Re-running the same command picks up from
the last watermark. To restart from offset 0, add `--restart`.

```bash
# Resume:
nohup nice -n 19 ionice -c 3 \
  env OMP_NUM_THREADS=4 PYTHONUNBUFFERED=1 \
  python3 -m search_stack.build_paragraph_embeddings \
    --structure-db /opt/caselaw/repo/output/decision_structure.db \
    --output-db   /mnt/HC_Volume_104655575/output/paragraph_embeddings.db \
    --batch-size 128 --verbose \
  >> /var/log/encode_paragraphs.log 2>&1 &
```

## Activation (after encoding completes)

Rewritten 2026-09-03 after the flag sat dead in production for eleven
days (incident note below). Every restart here is subject to the
build-window rule: no worker restarts (rolling-restart rule in this doc)
and no full-table scans of
`decisions.db` (CLAUDE.md invariant 9) from 03:30 UTC until the publish
pipeline exits (weekdays ~19:35–19:45 UTC); the incremental publish
runs 20:00 to ~22:00–22:47 UTC Mon–Sat. `publish.py`'s own post-swap
recycle (`_recycle_mcp_workers`, ~10:07 UTC daily) is part of the
pipeline; everything manual is not. Gate at the moment of action, not
earlier.

### Prerequisites

1. `paragraph_embeddings.db` complete (~8.8 M rows) at the path
   `SWISS_CASELAW_PARAGRAPH_EMBEDDINGS_DB` resolves to, readable by uid
   `mcp`. `_get_paragraph_embeddings_conn` opens it `immutable=1` and
   returns `None` without logging when the file is missing, so a wrong
   path shows up only as a rescue that never fires. In production the
   file was encoded to `/mnt/HC_Volume_104655575/output/paragraph_embeddings.db`
   (see the encode command above); the workers reach it either through
   the `output/` symlink or through an explicit
   `SWISS_CASELAW_PARAGRAPH_EMBEDDINGS_DB`. Check which with
   `ls -l $(readlink -f /opt/caselaw/repo/output/paragraph_embeddings.db)`
   and `grep -n PARAGRAPH_EMBEDDINGS /opt/caselaw/repo/.env.mcp`.

2. An offline HuggingFace cache the workers can read. Production runs
   8 workers `mcp-server@8770..8777` from the template
   `/etc/systemd/system/mcp-server@.service`: `User=mcp` (uid 999, no
   home directory; `/home/mcp` does not exist), `ProtectSystem=strict`,
   `PrivateTmp=true`, `NoNewPrivileges=true`,
   `ReadWritePaths=/opt/caselaw/repo/output /opt/caselaw/repo/logs
   /mnt/HC_Volume_104655575`, `EnvironmentFile=-/opt/caselaw/repo/.env.mcp`,
   `ExecStart=/usr/bin/python3 /opt/caselaw/repo/mcp_server.py --remote
   --host 127.0.0.1 --port %i`. Without `HF_HOME`, huggingface_hub
   resolves its cache to `/home/mcp/.cache`, cannot create it, and
   `SentenceTransformer(...)` raises `PermissionError`.
   `_get_semantic_model` catches it, logs one warning, and sets
   `_SEMANTIC_MODEL_TRIED`, which pins the rescue off for the process
   lifetime. The snapshot under `/root/.cache/huggingface/hub` is
   unreadable by `mcp` (`/root` is 0700).

   Layout in production since 2026-09-03 (458 MB, `chown -R mcp:mcp`):

   ```
   /opt/caselaw/hf_cache/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/
     refs/main         = e8f8c211226b894fcb81acc59f3b34ba3efd5f42
     snapshots/<hash>/ = relative symlinks into blobs/ (cp -a keeps them)
   ```

   The workers need READ access only. The cache is not in
   `ReadWritePaths` and must not be added there. `HF_HUB_OFFLINE=1`
   makes huggingface_hub load from the cache without touching the
   network. Offline mode affects nothing else live: the only other
   huggingface_hub use in `mcp_server.py` is the parquet download in
   `update_database`, which is refused in `REMOTE_MODE`.

Staging is scripted in `scripts/hf_cache_stage.sh` (copy, sandboxed
load test, backup, env append). The manual form:

```bash
# Run as root on the VPS. Touches no running service.
# 1. Copy the snapshot as-is (cp -a keeps the relative snapshots/ -> blobs/ symlinks)
mkdir -p /opt/caselaw/hf_cache/hub
cp -a /root/.cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2 \
      /opt/caselaw/hf_cache/hub/
chown -R mcp:mcp /opt/caselaw/hf_cache
find /opt/caselaw/hf_cache -xtype l      # broken symlinks: must print nothing

# 2. Prove it loads as uid mcp under the SAME sandbox as mcp-server@.service
systemd-run --quiet --wait --pipe --collect --uid=mcp --gid=mcp \
  -p ProtectSystem=strict -p PrivateTmp=true -p NoNewPrivileges=true \
  -p ReadWritePaths=/opt/caselaw/repo/output \
  -p ReadWritePaths=/opt/caselaw/repo/logs \
  -p ReadWritePaths=/mnt/HC_Volume_104655575 \
  --setenv=HF_HOME=/opt/caselaw/hf_cache --setenv=HF_HUB_OFFLINE=1 \
  /usr/bin/python3 -c 'from sentence_transformers import SentenceTransformer as S
m = S("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
print("dim", m.get_sentence_embedding_dimension())'
# 2026-09-03: dim 384, load 7.8 s, 1.2 GB RSS
```

Adding another model later (a `PINPOINT_SEMANTIC_MODEL` change, or
flipping `SWISS_CASELAW_VECTOR_SEARCH` on, which needs `BAAI/bge-m3`
and a `vectors.db`): download or copy it as root into
`/root/.cache/huggingface/hub` (or anywhere), `cp -a` the
`models--<org>--<name>` directory into `/opt/caselaw/hf_cache/hub/`,
`chown -R mcp:mcp /opt/caselaw/hf_cache`, then run the sandboxed load
test above before any restart. Under `HF_HUB_OFFLINE=1` a model that
is not in the cache fails to load; nothing is fetched on demand.

Do NOT copy `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` into the cache
without an MRR benchmark first. `SWISS_CASELAW_CROSS_ENCODER=true` is
set in `.env.mcp` but has never been live in the workers (same
`PermissionError`, invisible because `_get_cross_encoder` logs at
DEBUG); that model exists only in `/root`'s cache. Making it loadable
would silently change ranking at the next restart. The no-regression
rule applies; a comment saying so was appended to `.env.mcp`.

### Enable

Applied in production 2026-09-03 (`PINPOINT_SEMANTIC_ENABLED=true` has
been there since 2026-08-23). Run this only on a rebuilt host or after
rollback (a)/(b) below; each step refuses to add a line that already
exists, so re-running it never creates duplicate keys.

```bash
# 1. Back up .env.mcp OUTSIDE the repo tree. The nightly pipeline runs
#    `git stash --include-untracked` in /opt/caselaw/repo and would carry
#    a backup left there away with it.
cp -p /opt/caselaw/repo/.env.mcp /root/.env.mcp.bak-$(date -u +%F)-pre-hfcache

# 2. Append. Never sed -i: it recreates the file as root; the live file is mcp:mcp 0600.
#    Flag (skipped when already present):
grep -q '^PINPOINT_SEMANTIC_ENABLED=' /opt/caselaw/repo/.env.mcp \
  || echo 'PINPOINT_SEMANTIC_ENABLED=true' >> /opt/caselaw/repo/.env.mcp
#    Cache lines (this is what scripts/hf_cache_stage.sh appends; refuse if already set):
grep -qE '^HF_HOME=' /opt/caselaw/repo/.env.mcp && { echo "HF_HOME already set"; exit 1; }
cat >> /opt/caselaw/repo/.env.mcp <<'EOT'
HF_HOME=/opt/caselaw/hf_cache
HF_HUB_OFFLINE=1
EOT
grep -nE '^(PINPOINT_SEMANTIC_ENABLED|HF_HOME|HF_HUB_OFFLINE)=' /opt/caselaw/repo/.env.mcp   # exactly one line each
ls -l /opt/caselaw/repo/.env.mcp        # still mcp:mcp 0600
```

The env change is inert until a worker restarts. systemd re-reads
`.env.mcp` only at unit start, so it takes effect at the next restart
of any kind: `publish.py`'s post-swap recycle or the manual rolling
restart below. Until then each worker keeps the environment it booted
with, and a half-restarted pool serves two behaviours.

### Restart (rolling, off the build window)

```bash
# 3. Gate, AT THE MOMENT OF ACTION. opencaselaw-publish.service is a Type=oneshot
#    unit, so `is-active` is useless: ActiveState must not be "activating".
systemctl show -p ActiveState --value opencaselaw-publish.service
systemctl show -p ActiveState --value opencaselaw-publish-incremental.service
tail -1 /opt/caselaw/repo/logs/publish.log               # 03:30 full run (weekdays until ~19:35-19:45)
tail -1 /opt/caselaw/repo/logs/incremental_nightly.log   # 20:00 incremental run (Mon-Sat until ~22:00-22:47)

# 4. One worker at a time, health-gated on /health before the next goes down.
#    Never pass several units to one `systemctl restart`, and never hard-code
#    the port list: the pool is 8 workers (8770..8777) and lives in systemd.
bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh      # continues past an unhealthy worker
# Unattended variant: re-checks the gate itself before EVERY worker, aborts if
# a pipeline is running or a publish timer fires within 10 min, STOPS on the
# first worker that does not come back healthy, then runs the verification
# below and posts an ntfy summary.
bash /opt/caselaw/repo/scripts/hf_cache_restart_verify.sh restart
```

### Verify

The model loads lazily on the first rescue (~8 s), not at boot, and
the pinpoint threads of that first request race on
`_SEMANTIC_MODEL_TRIED`, so warm each worker once before checking it.
Check every worker on its loopback port: one green answer through the
public hostname says nothing about the other seven.

```bash
# The pool lives in systemd; never hard-code the port list.
PORTS=$(systemctl list-units 'mcp-server@*.service' --state=active --no-legend --plain | awk '{print $1}' | sed 's/mcp-server@//; s/\.service//')
echo "$PORTS"    # 8770 .. 8777 today

# 5a. Env actually seen by each running worker
for p in $PORTS; do
  pid=$(systemctl show -p MainPID --value mcp-server@$p)
  echo "$p HF_HOME=$(tr '\0' '\n' < /proc/$pid/environ | grep -c '^HF_HOME=/opt/caselaw/hf_cache$')"
done
# Expect: HF_HOME=1 on every line

# 5b. Live check. French paraphrase of E. 3.2 of BGE 146 III 25 against the
#     German judgment: lexical finds nothing usable, the rescue scores 0.82 on
#     E. 3.2 (measured against the stored vectors and verified through a
#     worker on 2026-09-04). Score any replacement claim first: the former
#     keyword-bag claim scored 0.35 (< 0.55) and reported a live fleet as dead.
Q='q=Le%20Conseil%20des%20Etats%20avait%20propos%C3%A9%20une%20solution%20transitoire%20sp%C3%A9ciale%20pour%20les%20dommages%20corporels%20caus%C3%A9s%20par%20l%27amiante%2C%20supprim%C3%A9e%20ensuite%20en%20raison%20de%20la%20cr%C3%A9ation%20du%20fonds%20d%27indemnisation%20des%20victimes%20de%20l%27amiante.&court=bge&date_from=2019-11-06&date_to=2019-11-06&limit=5&include_pinpoint=true'
for p in $PORTS; do
  curl -s --max-time 120 "http://127.0.0.1:$p/api/decisions?$Q" >/dev/null     # warm: lazy load
  curl -s --max-time 120 "http://127.0.0.1:$p/api/decisions?$Q" | python3 -c '
import sys, json
rs = [r for r in (json.load(sys.stdin).get("results") or []) if r.get("decision_id") == "bge_BGE_146_III_25"]
pp = (rs[0].get("pinpoint") if rs else None) or {}
print(sys.argv[1], pp.get("source"), pp.get("e_number"), pp.get("confidence"))' $p
done
# Expect: "<port> semantic 3.2 high" on every line. "None None None" means the
# pinpoint stayed null on that worker: either the model did not load (5c shows
# failed=1 / loaded=0), or paragraph_embeddings.db has no rows for
# bge_BGE_146_III_25 (check `ls -l $(readlink -f /opt/caselaw/repo/output/paragraph_embeddings.db)`
# and the row count), or the best cosine fell below 0.55.

# 5c. Journal since each worker's own boot: exactly one "loaded" line, zero "load failed"
#     (epoch --since: a bare timestamp is parsed in the host TZ by journalctl)
for p in $PORTS; do
  since=$(date -d "$(systemctl show -p ActiveEnterTimestamp --value mcp-server@$p)" +%s)
  j=$(journalctl -u mcp-server@$p --since "@$since" --no-pager -q)
  echo "$p loaded=$(grep -c 'loaded pinpoint semantic model .*(dim=384)' <<<"$j") failed=$(grep -c 'pinpoint semantic model load failed' <<<"$j")"
done
# Expect: loaded=1 failed=0 on every line

# 5d. Memory: ~0.5 GB RSS extra per worker once loaded (torch was already resident)
#     MemoryCurrent prints "[not set]" for a unit without memory accounting; guard the arithmetic.
for p in $PORTS; do
  m=$(systemctl show -p MemoryCurrent --value mcp-server@$p)
  case $m in ''|*[!0-9]*) echo "$p n/a";; *) echo "$p $((m/1048576)) MB";; esac
done
```

`bash /opt/caselaw/repo/scripts/hf_cache_restart_verify.sh verify` runs
5a–5d without a restart, e.g. the morning after `publish.py`'s recycle
has picked up the env on its own.

**Latency cost, recorded (no-regression rule, not yet benchmarked).**
The model loads lazily: the first rescue after every worker boot stalls
that one request by ~8 s, and the sibling pinpoint threads of that same
request get no semantic result (they race on `_SEMANTIC_MODEL_TRIED`).
`publish.py`'s recycle reboots all 8 workers daily at ~10:07 UTC, so
about 8 user requests per day pay the stall unless the workers are
warmed. Open items: (1) warm each worker's loopback port right after
any restart on the daily path (a `systemd-run --on-active=60` one-shot
curl loop launched by the verify script, or a warm-up hook after
`_recycle_mcp_workers` once a `publish.py` edit is approved); (2) take a
before/after p95 from `search_traces` for `/api/decisions` with
`include_pinpoint` so the rule is actually satisfied.

Why `/api/decisions` and not `/api/relevant-erwaegung`: the flag
serves `_compute_pinpoint` only, i.e. the `pinpoint` object that
`_pinpoint_enrich_results` attaches to `search_decisions` /
`GET /api/decisions` (top 5 full-fields results, `include_pinpoint`)
and to `find_leading_cases` / `GET /api/leading-cases` (top 3, free-text
query only). `find_relevant_erwaegung` (MCP tool and
`GET /api/relevant-erwaegung/{decision_id}`) is
`_handle_find_relevant_erwaegung`, a separate FTS5+BM25 flow that never
calls `_get_semantic_model`; it answers the same whether the flag is
on, off, or dead. The previous recipe here verified through it and
would have passed in a fully broken deployment.

## Deactivation (rollback)

Two different rollbacks; pick the one you mean. This is the single
canonical public procedure; host-specific recovery notes remain private.

### (a) Turn the feature off (the normal rollback)

Removing `PINPOINT_SEMANTIC_ENABLED` is the whole rollback:
`_get_semantic_model` returns before touching the cache when the flag
is off. The cache and the two `HF_*` lines are harmless without it and
worth keeping for the next attempt.

```bash
# Gate first (step 3 above). Then drop the flag line. The rewrite goes
# through `cat >` INTO the existing file so the mcp:mcp 0600 inode is kept;
# never sed -i (it recreates the file as root).
cp -p /opt/caselaw/repo/.env.mcp /root/.env.mcp.bak-$(date -u +%F)-pre-flagoff
grep -v '^PINPOINT_SEMANTIC_ENABLED=' /opt/caselaw/repo/.env.mcp > /root/env.mcp.new \
  && cat /root/env.mcp.new > /opt/caselaw/repo/.env.mcp
rm -f /root/env.mcp.new                      # holds the API key; do not leave it around
ls -l /opt/caselaw/repo/.env.mcp             # still mcp:mcp 0600
grep -c '^PINPOINT_SEMANTIC_ENABLED=' /opt/caselaw/repo/.env.mcp   # 0
grep -nE '^HF_' /opt/caselaw/repo/.env.mcp   # the two HF_ lines stay
bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh   # gated, one worker at a time
```

`rm -rf /opt/caselaw/hf_cache` (458 MB) only after every worker has
restarted with the flag off, and only if you want the disk back; a
missing cache with the flag still on is the 08-23 state again.
(`hf_cache_restart_verify.sh` is the wrong tool here: its verification
expects `source=semantic` and would report CHECK.)

### (b) Revert only the 2026-09-03 cache change

Only useful if the cache itself misbehaves. This restores the
pre-hfcache backup, which still contains `PINPOINT_SEMANTIC_ENABLED=true`
(set 08-23): the flag stays ON, the model load fails again with
`PermissionError`, and `_compute_pinpoint` is lexical-only. That is
deliberately the 08-23 state, not "feature off"; for that use (a).

```bash
# Gate first (step 3 above), then restore the pre-change file:
cp -p /root/.env.mcp.bak-2026-09-03-pre-hfcache /opt/caselaw/repo/.env.mcp
ls -l /opt/caselaw/repo/.env.mcp             # mcp:mcp 0600
grep -nE '^(PINPOINT_SEMANTIC_ENABLED|HF_)' /opt/caselaw/repo/.env.mcp   # flag present, no HF_ lines
bash /opt/caselaw/repo/scripts/rolling_restart_workers.sh   # gated, one worker at a time
# `hf_cache_restart_verify.sh` aborts once the HF_ lines are gone; use
# rolling_restart_workers.sh and gate by hand. Keep the cache directory:
# rm -rf it only after the workers are back and you are sure you will not
# retry.
```

## Incident 2026-08-23 → 2026-09-03: flag on, model never loaded

`PINPOINT_SEMANTIC_ENABLED=true` went into `.env.mcp` on 2026-08-23.
For eleven days every worker booted with the flag on and failed the
model load on its first rescue: huggingface_hub resolved the cache to
`/home/mcp/.cache` (`User=mcp` has no home directory), could not
create it, raised `PermissionError`; `_get_semantic_model` caught it,
logged one warning, set `_SEMANTIC_MODEL_TRIED`, and the rescue stayed
off for the life of the process. Users got flag-OFF quality
throughout. The snapshot existed only under
`/root/.cache/huggingface/hub`, unreadable by uid 999.

Detected 2026-09-03 from the worker journal, not from any in-process
signal: since 08-23, 0 lines matching `loaded pinpoint semantic model`
and 152 matching `pinpoint semantic model load failed: PermissionError
... /home/mcp` (one per worker per boot). Nothing else surfaced it:
`/health` was green, the flag read true, `_compute_pinpoint` degrades
to lexical-only without an error, the failure is logged at WARNING
once per process, and the verification recipe that stood here went
through an endpoint the flag does not touch.

Fix (2026-09-03 ~11:52 UTC, inside the build window, so no restart):
snapshot copied with `cp -a` to `/opt/caselaw/hf_cache`, `HF_HOME` and
`HF_HUB_OFFLINE` appended to `.env.mcp` (backup
`/root/.env.mcp.bak-2026-09-03-pre-hfcache`), load-tested as uid `mcp`
in a transient unit with the worker sandbox (dim 384, 7.8 s, 1.2 GB
RSS). Live at the next worker restart. Cost: ~0.5 GB RSS per worker
(host had 54 GB available), 458 MB disk (33 GB free on `/`). Versions
on the VPS: python 3.12, huggingface_hub 0.36.2, sentence-transformers
5.2.3, transformers 4.57.6, torch 2.10 (CPU).

Same root cause, still open: `SWISS_CASELAW_CROSS_ENCODER=true` has
never been live either (see Prerequisites); do not fix it without a
benchmark.

Lessons:
- A lazily loaded model needs a per-worker journal check after every
  restart. The load failure is a WARNING, the fallback is silent by
  design, and `/health` does not cover it.
- Verify a feature through an endpoint the feature actually gates.
- Under the worker sandbox (`User=mcp`, `ProtectSystem=strict`, no
  `$HOME`) anything that defaults to a path under the home directory
  needs an explicit, mcp-readable location.

## Tunables (env vars)

| Var | Default | Purpose |
|---|---|---|
| `PINPOINT_SEMANTIC_ENABLED`            | `false` | Master flag |
| `PINPOINT_SEMANTIC_MODEL`              | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Encoder model |
| `PINPOINT_SEMANTIC_HYBRID`             | `false` | Also run semantic on confident lexical matches; agreement → `high` / `hybrid_agreement`, disagreement → `lexical_semantic_disagree` with a `semantic_alternative` (mcp_server.py ~12902–12930) |
| `PINPOINT_SEMANTIC_HIGH`               | `0.70` | Cosine threshold for "high" |
| `PINPOINT_SEMANTIC_MEDIUM`             | `0.55` | Cosine threshold for "medium" |
| `SWISS_CASELAW_PARAGRAPH_EMBEDDINGS_DB` | `<DATA_DIR>/paragraph_embeddings.db` | Embedding DB path |
| `HF_HOME`                              | huggingface_hub default (`~/.cache/huggingface`) | **Required in production**: `/opt/caselaw/hf_cache`. Workers run as `mcp` with no home directory; without it the model load raises `PermissionError` and the rescue is off for the process lifetime |
| `HF_HUB_OFFLINE`                       | unset | **Required in production**: `1`. Load from the cache only, never contact the Hub; a model absent from the cache fails to load instead of being downloaded |

The thresholds (0.70 / 0.55) were calibrated on a 3-decision empirical
sample. They may want re-tuning against a larger labeled bench once
encoding completes — see `tests/test_pinpoint_semantic_rescue.py` for
the threshold semantics.

## Open questions / future iterations

- **BGE-M3 upgrade**: 568M-param model with 8K context (vs MiniLM's 128
  token limit) would handle long Erwägungen without truncation and
  has stronger cross-lingual transfer. Worth re-encoding with BGE-M3
  if cross-language FNs (BGE 140 III 86 case) prove a real problem.
- **Paragraph-level result combination**: currently semantic and
  lexical compete; a true hybrid scorer (`α·BM25 + (1-α)·cosine`)
  could surface higher-confidence pinpoints when both agree.
- **Larger labeled bench**: 50–100 case Regeste-grounded test set
  would let us tune thresholds + measure FPR/recall properly.
- **Incremental encoding**: every nightly publish that adds new
  decisions should re-run the encoder (encoder is incremental — only
  encodes new paragraphs).
