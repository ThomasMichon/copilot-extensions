"""Tests for the Picker/mux-daemon Windows priority-class boost."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from worktree_manager import process_priority


def test_never_raises_on_any_platform() -> None:
    """Best-effort: must never propagate an error on any platform.

    Run out-of-process: on Windows this actually mutates the calling
    process's priority class, and doing that in-process would permanently
    change the test runner's own class for every subsequent test (the same
    hazard the Windows regression test below guards against).
    """
    script = (
        "from worktree_manager import process_priority\n"
        "process_priority.raise_current_process_priority()\n"
        "process_priority.restore_normal_process_priority()\n"
    )
    subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="non-Windows no-op path")
def test_noop_on_non_windows(monkeypatch) -> None:
    """Off Windows, neither helper must touch ctypes/os at all."""
    import ctypes

    called = False

    class _ExplodingKernel32:
        def __getattr__(self, _name: str):
            nonlocal called
            called = True
            raise AssertionError("ctypes.windll must not be touched off Windows")

    monkeypatch.setattr(ctypes, "windll", _ExplodingKernel32(), raising=False)
    process_priority.raise_current_process_priority()
    process_priority.restore_normal_process_priority()
    assert called is False


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only priority-class path")
def test_windows_raises_then_restores_priority_class_in_child() -> None:
    """In a child process, the Windows priority class must actually change to
    AboveNormal and then back to Normal, verified out-of-process so the test
    runner's own priority is unaffected.

    Regression guard: ``GetCurrentProcess()`` returns a pseudo-HANDLE (the
    64-bit all-ones bit pattern on Win64); calling it and ``SetPriorityClass``
    through ctypes WITHOUT declared ``argtypes``/``restype`` truncates/
    mis-sign-extends that value under the untyped ``c_int`` default, so
    ``SetPriorityClass`` silently fails on every real run (see
    ``plugins/agent-index/tests/test_indexer_priority.py``'s analogous
    regression guard -- this module repeats the same bug class, caught the
    same way: an in-process ctypes return value looked fine, but an
    externally-inspected real process never actually changed class).
    """
    script = (
        "import ctypes, json\n"
        "from worktree_manager import process_priority\n"
        "k = ctypes.windll.kernel32\n"
        "k.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
        "k.GetPriorityClass.restype = ctypes.c_uint32\n"
        "h = k.GetCurrentProcess()\n"
        "before = k.GetPriorityClass(h)\n"
        "process_priority.raise_current_process_priority()\n"
        "boosted = k.GetPriorityClass(h)\n"
        "process_priority.restore_normal_process_priority()\n"
        "restored = k.GetPriorityClass(h)\n"
        "print(json.dumps({'before': before, 'boosted': boosted, 'restored': restored}))\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    above_normal_priority_class = 0x00008000
    normal_priority_class = 0x00000020
    assert result["boosted"] == above_normal_priority_class
    assert result["restored"] == normal_priority_class
