"""Opt-in re-issue detection in the practice framework (2026-07-29).

PracticeScraper dedup was doc_id-only and append-only. That is correct for
sources that mint a new id per edition, and wrong for sources that
re-publish a revised edition at a STABLE id — SECO overwrites
'Weisung AVIG ALE.pdf' in place; BSV bumps a version behind the same
document id. There, a doc_id hit meant "skip forever" and the corpus would
silently freeze at whichever edition we happened to fetch first.

REVISION_FIELD is opt-in precisely so the four shipped scrapers keep their
exact previous behaviour.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scrapers.practice.base import PracticeScraper  # noqa: E402


class _Stub(PracticeScraper):
    SOURCE_KEY = "unit_test_src"
    ISSUING_AUTHORITY = "TEST"
    DEFAULT_DOC_TYPE = "weisung"
    REQUEST_DELAY = 0

    def __init__(self, tmp, stubs, revision_field=None):
        self.OUTPUT_DIR = Path(tmp)
        type(self).REVISION_FIELD = revision_field
        self._stubs = stubs
        self.fetched: list[str] = []
        super().__init__()

    def discover_documents(self):
        yield from self._stubs

    def fetch_pdf_text(self, pdf_url):       # no network
        self.fetched.append(pdf_url)
        return f"body for {pdf_url}"


def _stub(pdf_url, num="W 1"):
    return {"pdf_url": pdf_url, "title": "Weisung", "doc_number": num,
            "date": "2026-01-01", "language": "de"}


def test_second_run_skips_unchanged_document(tmp_path):
    s1 = _Stub(tmp_path, [_stub("https://x/w.pdf")], revision_field="pdf_url")
    assert s1.run()["new"] == 1
    s2 = _Stub(tmp_path, [_stub("https://x/w.pdf")], revision_field="pdf_url")
    r = s2.run()
    assert r["new"] == 0 and r["skipped"] == 1
    assert s2.fetched == []


def test_reissued_document_is_refetched(tmp_path):
    """Same doc_id, new PDF URL = a revised edition. Must NOT be skipped."""
    s1 = _Stub(tmp_path, [_stub("https://x/hash-A/w.pdf")], revision_field="pdf_url")
    assert s1.run()["new"] == 1
    s2 = _Stub(tmp_path, [_stub("https://x/hash-B/w.pdf")], revision_field="pdf_url")
    r = s2.run()
    assert r["new"] == 1, "re-issue was silently skipped"
    assert r["skipped"] == 0
    assert s2.fetched == ["https://x/hash-B/w.pdf"]
    # the newest record wins for downstream upsert
    lines = [json.loads(x) for x in
             (tmp_path / "unit_test_src.jsonl").read_text().splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[-1]["pdf_url"].endswith("hash-B/w.pdf")
    assert lines[0]["doc_id"] == lines[-1]["doc_id"]   # upsert target, not a dupe


def test_without_revision_field_behaviour_is_unchanged(tmp_path):
    """The four shipped scrapers must keep skipping on doc_id alone."""
    s1 = _Stub(tmp_path, [_stub("https://x/hash-A/w.pdf")], revision_field=None)
    assert s1.run()["new"] == 1
    s2 = _Stub(tmp_path, [_stub("https://x/hash-B/w.pdf")], revision_field=None)
    r = s2.run()
    assert r["new"] == 0 and r["skipped"] == 1
    assert s2.fetched == []


def test_shipped_scrapers_keep_default(tmp_path):
    from scrapers.practice.estv_kreisschreiben import EstvKreisschreibenScraper
    from scrapers.practice.bafu_vollzugshilfen import BafuVollzugshilfenScraper
    # SEM opted in 2026-11-27 (revised Weisungen keep their file name);
    # tests/test_sem_weisungen_relaunch.py covers it.
    for cls in (EstvKreisschreibenScraper, BafuVollzugshilfenScraper):
        assert getattr(cls, "REVISION_FIELD", None) is None, cls.__name__
