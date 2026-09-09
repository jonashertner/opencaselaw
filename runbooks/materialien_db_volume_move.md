# Moving materialien.db to the data volume

`output/materialien.db` is a plain 1.2 GB file on `/` (30 GB free). The
historical Botschaft backfill takes it to ~3.5-4 GB, and Step 2f needs the
live file plus a `.db.tmp` of the same size, ~8 GB peak. The volume
(`/mnt/HC_Volume_104655575`, 92 GB free) already holds `decisions.db` and
`reference_graph.db` behind symlinks from `output/`, per the CLAUDE.md rule
for DBs over 500 MB. This note moves materialien.db the same way.

## Prerequisite: the EXDEV fix must be deployed first

Until commit `wk/mat-2f-prereqs` is live, `build_materialien_db.py` creates
its `.db.tmp` next to the *symlink* (on `/`) and renames it onto the
*realpath* (on the volume). That rename crosses filesystems, raises
`EXDEV`, and Step 2f fails on the first night after the move. Check on the
VPS before doing anything:

```
grep -n 'os.path.realpath(str(output_path))).with_suffix' /opt/caselaw/repo/search_stack/build_materialien_db.py
```

One hit at the top of `build_db` means the fix is in place. No hit: stop.

## Window

materialien.db has three writers, and none may be running during the copy:

| writer | when |
|---|---|
| `opencaselaw-publish.service` Step 2f (rebuild + `os.replace`) | inside the nightly build, 03:30 UTC until the unit exits (mid-afternoon on weekdays) |
| `opencaselaw-materialien.service` (04:30 UTC botschaft ingest) | 04:30 UTC, hours |
| ad-hoc backfills (`scripts/backfill_botschaft_metadata.py`, historical) | only in the window below, by hand |

The quiet window on weeknights is **00:20 to 03:15 UTC**: after the 20:00
incremental has exited (it queues behind a late full build, so it can end
well after midnight) and before the 03:30 full. Check the units, not the
clock:

```
systemctl is-active opencaselaw-publish.service opencaselaw-materialien.service
```

Both must print `inactive`. Workers are fine: they open materialien.db
per call with `?mode=ro&immutable=1` and close it in `finally`, so a call
in flight keeps the old inode and the next call opens the new path.

## Steps

```
cd /opt/caselaw/repo/output
VOL=/mnt/HC_Volume_104655575/output

# 1. Copy while nothing writes (about 20 s at 1.2 GB; longer once it grows).
cp -p materialien.db "$VOL/materialien.db"
sync

# 2. Verify the copy before pointing anything at it.
cmp materialien.db "$VOL/materialien.db"
sqlite3 "file:$VOL/materialien.db?mode=ro&immutable=1" 'PRAGMA quick_check;'   # ok

# 3. Swap the path: keep the old file until the first green 2f.
mv materialien.db materialien.db.pre-volume
ln -sfn "$VOL/materialien.db" materialien.db
ls -l materialien.db          # -> /mnt/HC_Volume_104655575/output/materialien.db

# 4. Read through the symlink exactly as a worker does.
python3 -c "
import sqlite3
c = sqlite3.connect('file:/opt/caselaw/repo/output/materialien.db?mode=ro&immutable=1', uri=True)
print(c.execute('SELECT COUNT(*) FROM botschaft_paragraphs').fetchone())
print(c.execute(\"SELECT value FROM meta WHERE key='built_at'\").fetchone())
"
```

Do not run step 1 while `opencaselaw-materialien.service` writes: the file
is `journal_mode=delete`, and a copy taken mid-commit is a torn database.

## The morning after

- `logs/publish.log` for the 2f line `Preserved N table(s) from live
  materialien.db: … botschaft_paragraphs(…)` and `Rebuilt FTS5 index
  botschaft_paragraphs_fts from botschaft_paragraphs (N rows)`, with no
  `Could not preserve table` warning. The counts must match the night
  before, not reset.
- `ls -l /mnt/HC_Volume_104655575/output/materialien.db*` shows one file
  and no leftover `.db.tmp`.
- Then `rm /opt/caselaw/repo/output/materialien.db.pre-volume`.

## Rollback

While `materialien.db.pre-volume` still exists, in the same window:

```
cd /opt/caselaw/repo/output
rm materialien.db && mv materialien.db.pre-volume materialien.db
```

The old file is at most one 04:30 run behind the volume copy; the next
materialien.service run catches it up.

## What is not covered

- `systemd/opencaselaw-materialien.service` passes
  `--db /opt/caselaw/repo/output/materialien.db`; every writer opens that
  path with a plain `sqlite3.connect`, which follows the symlink. No unit
  change is needed.
- The ~8 GB peak during 2f now lands on the volume. Keep an eye on its
  free space once the historical rows arrive; 92 GB free as of 2026-09-09.
