"""The monthly LexFind harvest behind JU, SZ, VD and ZH.

The April 2026 harvest had no timer, so these cantons stood still for seven
months while the build stamped their rows with each new build date. These
tests pin what a refresh must do: download only what changed, never lose a
law to a failed request or a short enumeration, and keep the date of the text
itself. Offline: the client is a stub.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scrapers.lexfind_cantonal import REPORT_NAME, Law, scrape_canton
from scrape_cantonal_laws import check_report, write_report


def _law(lid, sr="1", since="01.01.2026", active=True, canton="JU", lang="fr"):
    return Law(lexfind_id=lid, canton=canton, sr_number=sr, title=f"Loi {lid}",
               language=lang, is_active=active, category="Loi",
               original_url=None, version_active_since=since)


class Stub:
    def __init__(self, laws, failing=()):
        self.laws = {law.lexfind_id: law for law in laws}
        self.failing = set(failing)
        self.downloads = []

    def enumerate_canton(self, canton, language):
        return self.laws

    def _sleep(self):
        pass

    def fetch_law_text(self, lid, language):
        self.downloads.append(lid)
        if lid in self.failing:
            return None
        return {"full_text": f"new text {lid}", "text_source": "lexfind_pdf",
                "articles": [{"article_num": "1", "heading": None, "text": "new"}]}


def _shard(tmp_path, rows, mtime=None):
    path = tmp_path / "JU.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def _row(lid, since="01.01.2026", **extra):
    return {"lexfind_id": lid, "canton": "JU", "sr_number": "1", "title": f"Loi {lid}",
            "language": "fr", "is_active": True, "category": "Loi", "original_url": None,
            "version_active_since": since, "text_source": "lexfind_pdf",
            "full_text": f"old text {lid}", "articles": [], **extra}


def _rows(path: Path) -> dict:
    return {r["lexfind_id"]: r for r in map(json.loads, path.read_text().splitlines())}


def _run(stub, tmp_path, **kw):
    return scrape_canton(stub, "JU", None, False, output_dir=tmp_path, **kw)


def test_only_a_changed_version_is_downloaded(tmp_path):
    path = _shard(tmp_path, [_row(1, fetched_at="2026-04-11T00:00:00+00:00"), _row(2)])
    stub = Stub([_law(1), _law(2, since="01.08.2026")])

    report = _run(stub, tmp_path)

    assert stub.downloads == [2]
    assert (report["fetched"], report["reused"], report["ok"]) == (1, 1, True)
    rows = _rows(path)
    assert rows[1]["full_text"] == "old text 1"
    assert rows[1]["fetched_at"] == "2026-04-11T00:00:00+00:00"
    assert rows[1]["checked_at"] > "2026-04-11"
    assert rows[2]["full_text"] == "new text 2"
    assert rows[2]["version_active_since"] == "01.08.2026"
    assert rows[2]["fetched_at"] > "2026-04-11"


def test_a_row_without_a_date_takes_the_date_of_its_shard(tmp_path):
    path = _shard(tmp_path, [_row(1)], mtime=1775865600)  # 2026-04-11
    _run(Stub([_law(1)]), tmp_path)
    assert _rows(path)[1]["fetched_at"].startswith("2026-04-11")


def test_a_repeal_reaches_the_shard_without_a_download(tmp_path):
    path = _shard(tmp_path, [_row(1)])
    stub = Stub([_law(1, active=False)])
    _run(stub, tmp_path)
    assert stub.downloads == []
    assert _rows(path)[1]["is_active"] is False


def test_a_new_law_is_added(tmp_path):
    path = _shard(tmp_path, [_row(1)])
    _run(Stub([_law(1), _law(2)]), tmp_path)
    assert set(_rows(path)) == {1, 2}


def test_a_failed_download_keeps_the_old_row(tmp_path):
    path = _shard(tmp_path, [_row(1)])
    stub = Stub([_law(1, since="01.08.2026")], failing=[1])

    report = _run(stub, tmp_path)

    row = _rows(path)[1]
    assert row["full_text"] == "old text 1"
    # The old date stays, so the next run tries the download again, and the
    # row claims no check that did not happen.
    assert row["version_active_since"] == "01.01.2026"
    assert "checked_at" not in row
    assert report["kept_stale"] == 1


def test_a_law_the_enumeration_missed_stays_in_the_shard(tmp_path):
    path = _shard(tmp_path, [_row(i) for i in range(1, 12)])
    report = _run(Stub([_law(i) for i in range(1, 11)]), tmp_path)
    assert set(_rows(path)) == set(range(1, 12))
    assert report["carried"] == 1


def test_a_short_enumeration_is_not_promoted(tmp_path):
    rows = [_row(i) for i in range(1, 21)]
    path = _shard(tmp_path, rows)
    before = path.read_text()

    report = _run(Stub([_law(1)]), tmp_path)

    assert path.read_text() == before
    assert (tmp_path / "JU.jsonl.failed").exists()
    assert (report["ok"], report["promoted"], report["kept_previous"]) == (False, False, True)


def test_an_outage_during_downloads_is_reported_but_keeps_what_arrived(tmp_path):
    laws = [_law(i, since="01.08.2026") for i in range(1, 21)]
    path = _shard(tmp_path, [_row(i) for i in range(1, 21)])
    stub = Stub(laws, failing=range(1, 16))

    report = _run(stub, tmp_path)

    assert report["promoted"] is True
    assert report["ok"] is False
    assert _rows(path)[20]["full_text"] == "new text 20"


def test_a_law_that_always_fails_does_not_turn_the_run_red(tmp_path):
    _shard(tmp_path, [_row(i) for i in range(1, 21)])
    report = _run(Stub([_law(i) for i in range(1, 21)] + [_law(99)], failing=[99]), tmp_path)
    assert (report["ok"], report["failed"]) == (True, 1)


def test_refetch_all_ignores_the_version_check(tmp_path):
    _shard(tmp_path, [_row(1)])
    stub = Stub([_law(1)])
    _run(stub, tmp_path, refetch_all=True)
    assert stub.downloads == [1]


def test_a_pilot_never_touches_the_shard(tmp_path):
    path = _shard(tmp_path, [_row(1), _row(2)])
    before = path.read_text()
    scrape_canton(Stub([_law(1), _law(2)]), "JU", 1, False, output_dir=tmp_path)
    assert path.read_text() == before
    assert (tmp_path / "JU.jsonl.pilot").exists()


@pytest.mark.parametrize("ok, expected", [(True, 0), (False, 1)])
def test_the_report_turns_the_unit_red(tmp_path, ok, expected):
    write_report(tmp_path, [{"canton": "JU", "ok": ok, "fetched": 3, "errors": 0,
                             "kept_previous": True, "previous": 3}], REPORT_NAME)
    assert check_report(tmp_path, REPORT_NAME) == expected


def test_a_harvest_that_never_finished_is_exit_2(tmp_path):
    assert check_report(tmp_path, REPORT_NAME) == 2
