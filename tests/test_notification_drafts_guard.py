"""Drafted court notifications must never reach GitHub.

A notification draft names a decision and the personal data a court left in
it. Drafts live in the mailbox's drafts folder. Three guards: this checkout
tracks no draft-shaped file, .gitignore refuses the shapes, and the
anonymization check commits only its own two files to the private report
repository (which is also hosted on GitHub).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import anonymization_check as ac  # noqa: E402

DRAFT_SHAPED = re.compile(
    r"(^|/)(court-)?notifications/|(^|/)(notification|meldung|entwurf|draft)_[^/]*$", re.I)

IGNORE_PROBES = (
    "court-notifications/zh_svger.md",
    "docs/notifications/vd.md",
    "notification_be_obergericht.md",
    "notes/meldung_zg.txt",
    "notes/entwurf_bger.docx",
    "draft_vd.html",
)


def test_no_draft_shaped_file_is_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout.splitlines()
    assert [p for p in tracked if DRAFT_SHAPED.search(p)] == []


def test_draft_shapes_are_ignored():
    not_ignored = [
        p for p in IGNORE_PROBES
        if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", p],
                          cwd=REPO, check=False).returncode != 0
    ]
    assert not_ignored == []


def test_check_commits_only_its_own_files_to_the_private_repo(tmp_path, monkeypatch):
    import datetime
    import json

    git_calls: list[list[str]] = []
    monkeypatch.setattr(ac, "_git", git_calls.append)
    monkeypatch.setattr(ac, "_ntfy", lambda n: None)
    private = tmp_path / "private"
    private.mkdir()
    (private / "meldung_zh.md").write_text("Entwurf")      # a stray draft in the tree
    monkeypatch.setattr(ac, "PRIVATE_REPO", private)
    shards = tmp_path / "decisions"
    shards.mkdir()
    pad = "Die Vorinstanz hat den Sachverhalt willkürfrei festgestellt. " * 40
    (shards / "xx.jsonl").write_text(json.dumps({
        "decision_id": "xx_1", "court": "xx", "docket_number": "X 1",
        "full_text": pad + "Telefonnummer +41 79 555 01 23 des Beschuldigten. " + pad,
        "scraped_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}) + "\n")

    assert ac.main(["--shard-dir", str(shards)]) == 0
    staged = [c for c in git_calls if c and c[0] == "add"]
    assert len(staged) == 1 and "-A" not in staged[0] and "." not in staged[0]
    paths = staged[0][staged[0].index("--") + 1:]
    assert sorted(paths) == sorted(
        ["anonymization/.seen.json",
         f"anonymization/{datetime.date.today().isoformat()}.md"])
    commit = next(c for c in git_calls if "commit" in c)
    assert commit[commit.index("--") + 1:] == paths
