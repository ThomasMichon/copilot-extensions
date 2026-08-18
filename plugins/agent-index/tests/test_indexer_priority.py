"""Tests for the background-indexer host-politeness throttle (host good citizen)."""

from __future__ import annotations

import os
import sys

import pytest

from agent_index.indexing import priority


def test_config_default_nice(monkeypatch) -> None:
    from agent_index.index_config import IndexConfig

    monkeypatch.delenv("AGENT_INDEX_INDEXER_NICE", raising=False)
    assert IndexConfig().indexer_nice == 10


def test_config_nice_env_override(monkeypatch) -> None:
    from agent_index.index_config import IndexConfig

    monkeypatch.setenv("AGENT_INDEX_INDEXER_NICE", "0")
    assert IndexConfig().indexer_nice == 0


def test_disabled_is_noop(monkeypatch) -> None:
    """nice <= 0 must not touch priority (explicit full-speed run)."""
    called = False

    def _fail(_n: int) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(priority, "_lower_cpu_priority", _fail)
    monkeypatch.setattr(priority, "_lower_io_priority", lambda: None)
    priority.lower_current_process_priority(0)
    priority.lower_current_process_priority(-5)
    assert called is False


def test_never_raises_on_any_platform() -> None:
    """The throttle is best-effort: it must never propagate an error."""
    priority.lower_current_process_priority(10)  # must not raise


@pytest.mark.skipif(not hasattr(os, "nice"), reason="POSIX nice only")
def test_posix_lowers_priority_in_child() -> None:
    """In a forked child, the nice value must actually increase (lower priority),
    verified out-of-process so the test runner's own priority is unaffected."""
    if not hasattr(os, "fork"):
        pytest.skip("fork required")
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child
        os.close(r)
        try:
            before = os.nice(0)
            priority.lower_current_process_priority(10)
            after = os.nice(0)
            os.write(w, f"{before},{after}".encode())
        finally:
            os._exit(0)
    os.close(w)
    os.waitpid(pid, 0)
    before_s, after_s = os.read(r, 64).decode().split(",")
    os.close(r)
    before, after = int(before_s), int(after_s)
    # Relative nice, clamped at the kernel max of 19. Priority is strictly
    # lowered (or already floored), never raised.
    assert after == min(19, before + 10)
    assert after >= before


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-only IO path")
def test_io_priority_best_effort_no_ionice(monkeypatch) -> None:
    """Absent the `ionice` binary, the IO step is a silent no-op (not an error)."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    priority._lower_io_priority()  # must not raise
