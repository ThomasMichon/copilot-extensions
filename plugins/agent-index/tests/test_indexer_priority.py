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


def test_engine_config_default_nice(monkeypatch) -> None:
    """The engine's own throttle defaults gentler than the worker's (5 vs 10):
    it also serves live interactive search embeddings, not only background
    reindexing."""
    from agent_index.index_config import IndexConfig

    monkeypatch.delenv("AGENT_INDEX_ENGINE_NICE", raising=False)
    assert IndexConfig().engine_nice == 5


def test_engine_config_nice_env_override(monkeypatch) -> None:
    from agent_index.index_config import IndexConfig

    monkeypatch.setenv("AGENT_INDEX_ENGINE_NICE", "0")
    assert IndexConfig().engine_nice == 0


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


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only priority-class path")
def test_windows_lowers_priority_class_in_child() -> None:
    """In a child process, the Windows priority class must actually change to
    BELOW_NORMAL, verified out-of-process so the test runner's own priority is
    unaffected.

    Regression guard: ``GetCurrentProcess()`` returns a pseudo-HANDLE (the
    64-bit all-ones bit pattern on Win64); calling it and ``SetPriorityClass``
    through ctypes WITHOUT declared ``argtypes``/``restype`` truncates/
    mis-sign-extends that value under the untyped ``c_int`` default, so
    ``SetPriorityClass`` silently fails (returns 0) on every real run --
    observed directly in production: a live engine process queried via
    ``GetPriorityClass`` after `agent-index` applied this throttle still
    reported ``Normal``, not ``BelowNormal``.
    """
    import subprocess
    import sys as _sys

    script = (
        "import ctypes, json\n"
        "from agent_index.indexing import priority\n"
        "k = ctypes.windll.kernel32\n"
        "k.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
        "k.GetPriorityClass.restype = ctypes.c_uint32\n"
        "k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]\n"
        "k.SetPriorityClass.restype = ctypes.c_int\n"
        "h = k.GetCurrentProcess()\n"
        "before = k.GetPriorityClass(h)\n"
        # Force back to NORMAL first so the throttle's own effect is
        # unambiguous regardless of whatever class this interpreter
        # inherited from its parent (observed to already be BelowNormal
        # in some launch contexts).
        "reset_ok = k.SetPriorityClass(h, 0x00000020)\n"
        "priority.lower_current_process_priority(10)\n"
        "after = k.GetPriorityClass(h)\n"
        "print(json.dumps({'before': before, 'reset_ok': reset_ok, 'after': after}))\n"
    )
    completed = subprocess.run(  # noqa: S603
        [_sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    import json

    result = json.loads(completed.stdout.strip().splitlines()[-1])
    below_normal_priority_class = 0x00004000
    assert result["reset_ok"], "could not reset to NORMAL_PRIORITY_CLASS before the test"
    assert result["after"] == below_normal_priority_class
