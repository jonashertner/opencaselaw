"""Step 6 must never leave the checkout mid-rebase.

2026-11-02 22:51: the incremental's stats commit rewrote the homepage's
#f-laws count while a hand push had changed the line below it. The pull
--rebase stopped on docs/index.html, the stash pop failed behind it, and
every later git step on the VPS failed until a hand repair. These tests
replay that race against real throwaway repositories.
"""
import json
import subprocess

import pytest

import publish

COUNT = '<div id="f-laws">{n} laws</div>\n'
TEXT = '<div data-t="f_laws_t">{t}</div>\n'


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True).stdout.strip()


def write_index(repo, n, t):
    (repo / "docs" / "index.html").write_text(
        "<main>\n" + COUNT.format(n=n) + TEXT.format(t=t) + "</main>\n")


def write_stats(repo, n):
    (repo / "docs" / "stats.json").write_text(json.dumps({"laws": n}) + "\n")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n"
                   "[init]\n\tdefaultBranch = main\n[pull]\n\trebase = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    (seed / "docs").mkdir()
    write_index(seed, 100, "old text")
    write_stats(seed, 100)
    (seed / "README.md").write_text("readme\n")
    # Step 6 adds every published path; production always has them all.
    for d in ("feeds", "integrity", "stats", "sources"):
        (seed / "docs" / d).mkdir()
    for f in ("feed.xml", "feeds/de.xml", "quality.json", "quality.html",
              "integrity/root", "stats/history.json", "coverage.json",
              "sources/status.json"):
        (seed / "docs" / f).write_text("x\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "main")
    vps, dev = tmp_path / "vps", tmp_path / "dev"
    git(tmp_path, "clone", "-q", str(remote), str(vps))
    git(tmp_path, "clone", "-q", str(remote), str(dev))

    monkeypatch.setattr(publish, "REPO_DIR", vps)
    monkeypatch.setattr(publish, "DOCS_DIR", vps / "docs")
    monkeypatch.setattr(publish, "_append_stats_history", lambda dry_run=False: None)

    def fake_sync(dry_run=False):
        # The real script rewrites only the count line, from stats.json.
        n = json.loads((vps / "docs" / "stats.json").read_text())["laws"]
        p = vps / "docs" / "index.html"
        lines = p.read_text().splitlines(keepends=True)
        p.write_text("".join(COUNT.format(n=n) if 'id="f-laws"' in l else l
                             for l in lines))
    monkeypatch.setattr(publish, "_sync_homepage_fallbacks", fake_sync)
    return remote, vps, dev


def remote_file(remote, path):
    return git(remote, "show", f"main:{path}")


def test_adjacent_hand_edit_is_resolved_and_pushed(repos):
    remote, vps, dev = repos
    # The hand push: new text under the count, and a stats.json of its own.
    write_index(dev, 100, "new text")
    write_stats(dev, 150)
    git(dev, "commit", "-q", "-am", "hand edit")
    git(dev, "push", "-q", "origin", "main")
    # The build: fresh stats, an untracked artefact the stash must keep.
    write_stats(vps, 200)
    (vps / "releases").mkdir()
    (vps / "releases" / "manifest.json").write_text("{}\n")

    assert publish.step_6_git_push() is True

    assert not publish._rebase_in_progress()
    index = remote_file(remote, "docs/index.html")
    assert "new text" in index and "200 laws" in index
    assert json.loads(remote_file(remote, "docs/stats.json")) == {"laws": 200}
    assert git(remote, "log", "-1", "--format=%s", "main").startswith("Update stats.json")
    assert git(remote, "log", "-1", "--format=%s", "main~1") == "hand edit"
    assert (vps / "releases" / "manifest.json").exists()
    assert git(vps, "diff", "--name-only", "--diff-filter=U") == ""


def test_conflict_outside_generated_files_aborts_cleanly(repos):
    remote, vps, dev = repos
    (dev / "README.md").write_text("dev readme\n")
    git(dev, "commit", "-q", "-am", "dev readme")
    git(dev, "push", "-q", "origin", "main")
    # An unpushed hand commit on the VPS that conflicts with it.
    (vps / "README.md").write_text("vps readme\n")
    git(vps, "commit", "-q", "-am", "vps readme")
    write_stats(vps, 200)
    (vps / "scratch.txt").write_text("keep me\n")
    before = git(remote, "rev-parse", "main")

    assert publish.step_6_git_push() is False

    assert not publish._rebase_in_progress()
    assert git(vps, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(vps, "log", "-1", "--format=%s").startswith("Update stats.json")
    assert (vps / "README.md").read_text() == "vps readme\n"
    assert (vps / "scratch.txt").read_text() == "keep me\n"
    assert git(remote, "rev-parse", "main") == before


def test_failed_pull_without_rebase_returns_false(repos, tmp_path):
    _, vps, _ = repos
    git(vps, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    write_stats(vps, 200)

    assert publish.step_6_git_push() is False
    assert not publish._rebase_in_progress()
