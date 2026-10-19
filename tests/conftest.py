"""Shared fixtures for tests/.

Kept deliberately minimal — most fixtures live in the test files that own
them (repo convention).
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_search_result_cache():
    """Isolate mcp_server._SEARCH_RESULT_CACHE between tests.

    In-memory fixture DBs all report user_version=0, so the db_generation
    hook that invalidates the cache in production never fires across tests:
    two tests calling search_fts5 with identical args but different fixture
    corpora would silently share results. Same collision class the
    generation-keyed _FTS_TOTAL_CACHE handles per-file in
    test_search_total_exact.py; the result cache is cleared globally here
    because any search test can hit it.
    """
    try:
        import mcp_server
        cache = mcp_server._SEARCH_RESULT_CACHE
    except Exception:
        yield
        return
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_real_ntfy_posts(monkeypatch):
    """Never post to ntfy.sh from the test suite (CLAUDE.md: tests stay offline).

    publish._notify and the alert scripts post to the operator's phone topics;
    until 2026-09-16 three publish tests pushed real "Publish OK" messages on
    every run. That guard was per-file, so a new test driving a failing step 2
    (which now pages at once) could page the operator. Tests that assert on
    notifications patch _notify or urlopen themselves; their patch wins."""
    import urllib.request
    real = urllib.request.urlopen

    def guarded(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "ntfy.sh" in url:
            raise OSError("ntfy.sh is blocked in tests")
        return real(req, *a, **k)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)
