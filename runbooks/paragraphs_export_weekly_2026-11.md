# Weekly erwaegungen_paragraphs.parquet export: deploy note (2026-11)

## What changed

- `publish.py` step 3 no longer passes `--structure-paragraphs` on Sundays.
  `OCL_PARAGRAPHS_IN_BUILD=1` in the publish unit's environment turns the old
  in-build export back on.
- New `scripts/export_paragraphs_weekly.py`, started after every successful build by
  `opencaselaw-paragraphs-export.service` (`OnSuccess=` drop-in on
  `opencaselaw-publish.service`). It exports only after the Sunday build,
  meaning the build slot that started at the last 03:30 UTC was a Sunday,
  or when the file is more than 7.5 days old (catch-up). It refuses while the
  publish unit is active and hard-stops at 03:00 UTC. The probe is logged but
  never gates. A skip or hard stop exits 1, which fails the unit and fires
  `ntfy-alert@`.
- `export_parquet.export_decision_structure` gains `include_meta` and
  `paragraphs_probe_gates`. `export_status.json` now keeps the entry of an export
  that did not run this time and records `ran_at` for each export. Every run
  logs actual time against projected time, so calibration data accumulates.
- HuggingFace: step 4 of the next build (Monday) uploads `structure/*.parquet` as
  before, so the new file lands on HF on Monday evening instead of Sunday.

Why: the in-build probe projected 119.9 min on 11-01 and 252.4 min on 11-08,
against a 45 min budget, and skipped both times. The same call after the build
on 11-08 took 48 min (227.5 projected). The probe over-projects by 1.6–5x.
`erwaegungen_paragraph` max(rowid) is 16.86M for about 11.09M rows since the
11-05 re-extract. Even an accurate projection would barely fit the ~70 min that
step 3 has left on a Sunday.

## VPS steps (outside the build window: after the build exits, before 03:30 UTC)

The order matters. The units must be in place before the `publish.py` change
is live, or no paragraphs export runs at all.

```bash
ssh -i ~/.ssh/caselaw root@46.225.212.40
cd /opt/caselaw/repo
systemctl is-active opencaselaw-publish.service   # must be inactive
git fetch origin && git merge --ff-only origin/main
cp systemd/opencaselaw-paragraphs-export.service /etc/systemd/system/
cp systemd/opencaselaw-publish.service.d/paragraphs-export.conf \
   /etc/systemd/system/opencaselaw-publish.service.d/
systemctl daemon-reload
systemctl show opencaselaw-publish.service -p OnSuccess        # -> opencaselaw-paragraphs-export.service
systemctl cat opencaselaw-paragraphs-export.service | head -3
python3 scripts/export_paragraphs_weekly.py --dry-run          # "not due" midweek, exit 0
```

Do not `systemctl enable` the new unit. It has no `[Install]` section;
`OnSuccess=` starts it.

## Verify

- After every build: `tail logs/paragraphs_export.log` shows one line, either
  "not due" or an export.
- After the Sunday 11-15 build:
  - the log shows "due (Sunday build)", "projected … (log only, not gating …)",
    then "N rows in X min (projected Y, Zx)" and "wrote …".
  - `ls -la output/dataset/structure/` shows a new `erwaegungen_paragraphs.parquet`.
  - `export_status.json` contains `ran_at.erwaegungen_paragraphs`.
- Monday 11-16: step 4 logs "Uploaded aux parquet to structure/".
- If the export fails, the ntfy alert names `opencaselaw-paragraphs-export.service`.
  The next successful build catches up once the file is more than 7.5 days old.
  To run one by hand: `systemctl start opencaselaw-paragraphs-export.service`,
  or `python3 scripts/export_paragraphs_weekly.py --force`.

## Rollback

```bash
rm /etc/systemd/system/opencaselaw-publish.service.d/paragraphs-export.conf
systemctl daemon-reload
```

Then either set `Environment=OCL_PARAGRAPHS_IN_BUILD=1` in a publish drop-in,
which restores the in-build export, or revert the commit.
