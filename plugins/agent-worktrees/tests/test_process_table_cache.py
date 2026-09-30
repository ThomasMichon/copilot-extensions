"""Tests for :mod:`process_table_cache` -- the short-TTL memoization layer
for read-only process-table resolution (copilot-extensions#4716, a #3751
follow-up).

Covers the module directly (``cached``/``clear``); the
``reclaim.resolve_bound_copilots`` integration lives in
``test_reclaim.py::TestResolveBoundCopilotsReadCache``, mirroring how
``record_cache``'s own integration tests live alongside ``tracking``'s.
"""

from __future__ import annotations

import pytest

from agent_worktrees import process_table_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    process_table_cache.clear()
    yield
    process_table_cache.clear()


class TestCached:
    def test_coalesces_calls_within_ttl(self):
        calls = {"n": 0}

        def _build():
            calls["n"] += 1
            return {calls["n"]: {"ppid": 1, "name": "copilot"}}

        first = process_table_cache.cached(_build)
        second = process_table_cache.cached(_build)
        assert first == second
        assert calls["n"] == 1

    def test_rebuilds_after_ttl_expires(self, monkeypatch):
        calls = {"n": 0}
        clock = {"t": 0.0}

        def _build():
            calls["n"] += 1
            return {calls["n"]: {"ppid": 1, "name": "copilot"}}

        monkeypatch.setattr(process_table_cache.time, "monotonic", lambda: clock["t"])
        process_table_cache.cached(_build, ttl=1.0)
        clock["t"] = 2.0
        process_table_cache.cached(_build, ttl=1.0)
        assert calls["n"] == 2

    def test_stays_within_ttl_without_rebuilding(self, monkeypatch):
        calls = {"n": 0}
        clock = {"t": 0.0}

        def _build():
            calls["n"] += 1
            return {calls["n"]: {"ppid": 1, "name": "copilot"}}

        monkeypatch.setattr(process_table_cache.time, "monotonic", lambda: clock["t"])
        process_table_cache.cached(_build, ttl=2.0)
        clock["t"] = 1.0
        process_table_cache.cached(_build, ttl=2.0)
        assert calls["n"] == 1

    def test_clear_forces_a_fresh_build(self):
        calls = {"n": 0}

        def _build():
            calls["n"] += 1
            return {calls["n"]: {"ppid": 1, "name": "copilot"}}

        process_table_cache.cached(_build)
        process_table_cache.clear()
        process_table_cache.cached(_build)
        assert calls["n"] == 2
