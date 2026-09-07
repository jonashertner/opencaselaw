"""LLM input bounds + bounded caches (integration evaluation 2026-07).

Before: _parse_query_structured / _expand_query_with_llm / _apply_llm_rerank
forwarded the full query text to the Anthropic API verbatim (a pasted letter
= the whole letter leaves the box), and both LLM caches were unbounded plain
dicts keyed on raw lowercased query text — a memory leak that also retained
possibly privileged text in-process indefinitely.

Now: head+tail excerpt at LLM_INPUT_MAX_CHARS; _BoundedTTLCache with
sha256-hashed keys, LRU eviction and TTL; both caches wired into
_cache_clear (DB-generation swap hook). Persistence deliberately skipped —
see the class docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import mcp_server as m  # noqa: E402


# ------------------------------------------------------------- excerpt

def test_excerpt_identity_at_or_below_cap():
    assert m._llm_input_excerpt("") == ""
    assert m._llm_input_excerpt("kurze Anfrage") == "kurze Anfrage"
    exact = "x" * m.LLM_INPUT_MAX_CHARS
    assert m._llm_input_excerpt(exact) == exact


def test_excerpt_head_tail_composition_above_cap():
    text = "A" * 400 + "B" * 400 + "C" * 400  # 1,200 chars
    out = m._llm_input_excerpt(text)
    assert len(out) <= m.LLM_INPUT_MAX_CHARS + len(" … ")
    assert out.startswith("A" * 100)          # head preserved
    assert out.endswith("C" * 100)            # tail preserved — German letters
    assert " … " in out                       # put the question last


def test_excerpt_custom_cap():
    # cap=200 → head 50 + " … " + tail 150
    out = m._llm_input_excerpt("H" * 100 + "T" * 200, max_chars=200)
    assert out == "H" * 50 + " … " + "T" * 150


# ------------------------------------------------------------- cache

def test_cache_get_set_roundtrip():
    c = m._BoundedTTLCache(maxsize=4, ttl_s=60)
    k = c.key_for("Mietrecht Kündigung")
    assert c.get(k) is None
    c.set(k, ["a", "b"])
    assert c.get(k) == ["a", "b"]


def test_cache_lru_eviction_at_maxsize():
    c = m._BoundedTTLCache(maxsize=3, ttl_s=60)
    keys = [c.key_for(f"q{i}") for i in range(4)]
    for i, k in enumerate(keys[:3]):
        c.set(k, i)
    c.get(keys[0])          # touch q0 → q1 becomes LRU
    c.set(keys[3], 3)       # evicts q1
    assert c.get(keys[0]) == 0
    assert c.get(keys[1]) is None
    assert c.get(keys[3]) == 3
    assert len(c) == 3


def test_cache_ttl_expiry(monkeypatch):
    c = m._BoundedTTLCache(maxsize=4, ttl_s=10)
    k = c.key_for("q")
    base = 1000.0
    monkeypatch.setattr(m.time, "monotonic", lambda: base)
    c.set(k, "v")
    assert c.get(k) == "v"
    monkeypatch.setattr(m.time, "monotonic", lambda: base + 11)
    assert c.get(k) is None


def test_cache_keys_are_hashes_not_raw_text():
    c = m._BoundedTTLCache()
    secret = "Kündigungsschreiben Frau Meier Mutterschaftsurlaub"
    k = c.key_for(secret)
    c.set(k, ["term"])
    joined = " ".join(c._data.keys())
    assert secret not in joined
    assert "Meier" not in joined
    assert all(len(key) == 64 for key in c._data.keys())  # sha256 hex


def test_key_basis_is_normalized_and_bounded():
    c = m._BoundedTTLCache
    assert c.key_for("  Mietrecht  ") == c.key_for("mietrecht")
    # >1000-char inputs share the same key when their first 1000 chars match
    long_a = "x" * 1500
    long_b = "x" * 1000 + "y" * 500
    assert c.key_for(long_a) == c.key_for(long_b)


def test_module_caches_are_bounded_instances_and_cleared():
    assert isinstance(m._LLM_EXPANSION_CACHE, m._BoundedTTLCache)
    assert isinstance(m._STRUCTURED_PARSE_CACHE, m._BoundedTTLCache)
    k = m._BoundedTTLCache.key_for("probe")
    m._LLM_EXPANSION_CACHE.set(k, ["x"])
    m._STRUCTURED_PARSE_CACHE.set(k, {"domain": "civil"})
    m._cache_clear()
    assert m._LLM_EXPANSION_CACHE.get(k) is None
    assert m._STRUCTURED_PARSE_CACHE.get(k) is None


def test_no_disk_persistence_by_design():
    # The privacy posture forbids a disk cache of query-derived values; the
    # decision is recorded in the class docstring so a future "optimization"
    # has to argue with it.
    doc = m._BoundedTTLCache.__doc__ or ""
    assert "NOT persisted" in doc
