"""Offline tests for scripts/check_hf_unmanaged_parquet.py (fake repo listings,
no Hub access)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import scripts.check_hf_unmanaged_parquet as guard

REPO = Path(__file__).resolve().parents[1]
STALE_LIST = REPO / "runbooks" / "hf_root_parquet_cleanup_2026-10-07.files.tsv"

# Shaped on the 2026-10-07 listing of voilaj/swiss-caselaw.
FAKE_LISTING = [
    ".gitattributes",
    "README.md",
    "bge.parquet",
    "bge_historical.parquet",
    "sg_gerichte.parquet",
    "data/bge.parquet",
    "data/sg_gerichte.parquet",
    "data/delta-2026-10-06.parquet",
    "data/daily/2026-10-06.parquet",
    "graph/citations.parquet",
    "structure/structure.parquet",
    "artifacts/parquet/deltas/2026-10-06.parquet",
    "artifacts/manifest.json",
    "artifacts/verification_pack/latest.sqlite.gz",
    # look like managed prefixes but are not
    "data.parquet",
    "database/x.parquet",
    "old/data/bge.parquet",
    "Data/BGE.PARQUET",
]

UNMANAGED = [
    "Data/BGE.PARQUET",
    "bge.parquet",
    "bge_historical.parquet",
    "data.parquet",
    "database/x.parquet",
    "old/data/bge.parquet",
    "sg_gerichte.parquet",
]


def test_reports_parquet_outside_managed_prefixes_only():
    assert guard.unmanaged_parquet(FAKE_LISTING) == UNMANAGED


def test_clean_listing_and_duplicates():
    managed = [p for p in FAKE_LISTING if p not in UNMANAGED]
    assert guard.unmanaged_parquet(managed) == []
    assert guard.unmanaged_parquet(["bge.parquet", "bge.parquet"]) == ["bge.parquet"]


def test_managed_prefixes_never_cover_the_root():
    # An empty or slash-less prefix ("data" would also match "database/...")
    # would hide stale files; the root itself must never count as managed.
    assert guard.MANAGED_PREFIXES
    for prefix in guard.MANAGED_PREFIXES:
        assert prefix.endswith("/") and prefix.strip("/") and not prefix.startswith("/")
    assert set(guard.MANAGED_PREFIXES) == {"data/", "graph/", "structure/", "artifacts/"}


def test_documented_deletion_list_is_root_only_and_matches_guard():
    # The proposed deletion takes exactly the runbook list; it must hold
    # nothing under a managed prefix and nothing but root-level parquet.
    with STALE_LIST.open(encoding="utf-8", newline="") as f:
        documented = [row["path"] for row in csv.DictReader(f, delimiter="\t")]
    assert len(documented) == len(set(documented)) == 99
    assert all("/" not in p and p.endswith(".parquet") for p in documented)
    listing = documented + [p for p in FAKE_LISTING if p.startswith(guard.MANAGED_PREFIXES)]
    assert guard.unmanaged_parquet(listing) == sorted(documented)


def test_cli_files_from_exit_codes_and_json(tmp_path, capsys):
    listing = tmp_path / "files.txt"
    listing.write_text("\n".join(FAKE_LISTING) + "\n\n", encoding="utf-8")
    assert guard.main(["--files-from", str(listing), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [x["path"] for x in out["unmanaged_parquet"]] == UNMANAGED
    assert out["files_listed"] == len(FAKE_LISTING)

    clean = tmp_path / "clean.txt"
    clean.write_text("README.md\ndata/bge.parquet\n", encoding="utf-8")
    assert guard.main(["--files-from", str(clean)]) == 0
    assert "0 parquet file(s) outside" in capsys.readouterr().out


def test_cli_listing_failure_is_exit_2(monkeypatch, capsys):
    def boom(repo_id, revision):
        raise OSError("offline")

    monkeypatch.setattr(guard, "list_remote_files", boom)
    assert guard.main([]) == 2
    assert "listing voilaj/swiss-caselaw@main failed" in capsys.readouterr().err


# ── the card's default load_dataset config ────────────────────────────────

OLD_CARD = """---
license: cc0-1.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/*.parquet
---

# Swiss Case Law Dataset
"""


def test_card_default_patterns_forms():
    assert guard.card_default_patterns(OLD_CARD) == ["data/*.parquet"]
    assert guard.card_default_patterns(guard.CARD_PATH.read_text(encoding="utf-8")) == [
        "data/*[!0-9].parquet"]
    as_str = "---\nconfigs:\n  - config_name: default\n    data_files: data/a*.parquet\n---\n"
    assert guard.card_default_patterns(as_str) == ["data/a*.parquet"]
    as_lists = ("---\nconfigs:\n  - config_name: other\n    data_files: x/*.parquet\n"
                "  - config_name: default\n    data_files:\n      - split: train\n"
                "        path: [data/a*.parquet, data/b*.parquet]\n---\n")
    assert guard.card_default_patterns(as_lists) == ["data/a*.parquet", "data/b*.parquet"]
    assert guard.card_default_patterns("# no front matter\n") == []


def test_old_pattern_reads_the_delta_new_one_only_courts():
    listing = ["data/bge.parquet", "data/zh_mietgericht.parquet",
               "data/delta-2026-10-08.parquet", "data/daily/2026-10-08.parquet",
               "artifacts/parquet/deltas/2026-10-08.parquet"]
    assert guard.card_config_gaps(listing, ["data/*.parquet"]) == {
        "courts_not_loaded": [], "non_courts_loaded": ["data/delta-2026-10-08.parquet"]}
    assert guard.card_config_gaps(listing, ["data/*[!0-9].parquet"]) == {
        "courts_not_loaded": [], "non_courts_loaded": []}
    # the pattern's one blind spot is caught, not hidden: a court ending in a digit
    assert guard.card_config_gaps(["data/zh_kreis2.parquet"], ["data/*[!0-9].parquet"]) == {
        "courts_not_loaded": ["data/zh_kreis2.parquet"], "non_courts_loaded": []}


def test_card_reads_every_court_of_the_committed_stats():
    # docs/stats.json is refreshed nightly; a court code ending in a digit would
    # be skipped by data/*[!0-9].parquet, so it fails here before it ships.
    courts = json.loads((REPO / "docs" / "stats.json").read_text(encoding="utf-8"))["by_court"]
    assert len(courts) > 100
    patterns = guard.card_default_patterns(guard.CARD_PATH.read_text(encoding="utf-8"))
    listing = [f"data/{c}.parquet" for c in courts] + ["data/delta-2026-10-08.parquet"]
    assert guard.card_config_gaps(listing, patterns) == {
        "courts_not_loaded": [], "non_courts_loaded": []}


def test_layout_findings_lines():
    listing = ["bge.parquet", "data/bge.parquet", "data/delta-2026-10-08.parquet"]
    old = guard.layout_findings(listing, OLD_CARD)
    assert len(old) == 2
    assert old[0].startswith("1 parquet file(s) outside data/")
    assert "also reads data/delta-2026-10-08.parquet" in old[1]
    new = guard.layout_findings(listing[1:], guard.CARD_PATH.read_text(encoding="utf-8"))
    assert new == []
    assert guard.layout_findings(listing[1:], "# no front matter\n") == [
        "dataset card has no default config data_files"]


def test_cli_reports_an_old_card(tmp_path, capsys):
    listing = tmp_path / "files.txt"
    listing.write_text("data/bge.parquet\ndata/delta-2026-10-08.parquet\n", encoding="utf-8")
    card = tmp_path / "README.md"
    card.write_text(OLD_CARD, encoding="utf-8")
    assert guard.main(["--files-from", str(listing), "--card", str(card), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["card"]["non_courts_loaded"] == ["data/delta-2026-10-08.parquet"]
    assert out["unmanaged_parquet"] == []
    assert guard.main(["--files-from", str(listing)]) == 0  # the repo's card
