"""Tests for :mod:`agent_worktrees.procs`'s parent-pid ancestor walk
(#4454 follow-up).

``is_descendant_of`` is how a version-cutover reap recognizes a live
worktree-launcher's own subprocess calls as protected work rather than an
orphan on a superseded runtime slot -- these tests exercise it against real
OS process relationships (a real child, and a real grandchild spawned by that
child), not just mocks, since the whole point is walking the *actual* parent
chain correctly on this platform.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

from agent_worktrees import procs

_CHILD_SCRIPT = (
    "import subprocess, sys, time\n"
    "print('ready', flush=True)\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', "
    "'import sys,time; print(\"ready\", flush=True); time.sleep(5)'], "
    "stdout=subprocess.PIPE, text=True)\n"
    "print(grandchild.pid, flush=True)\n"
    "grandchild.stdout.readline()\n"
    "time.sleep(5)\n"
)


def _spawn_child_with_grandchild():
    """Start a real child that starts a real grandchild; return both pids."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdout=subprocess.PIPE, text=True,
    )
    proc.stdout.readline()  # "ready" from the child itself
    grandchild_pid = int(proc.stdout.readline().strip())
    return proc, grandchild_pid


def test_parent_pid_of_a_real_child_is_this_process(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(3)"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        proc.stdout.readline()
        assert procs.parent_pid(proc.pid) == os.getpid()
    finally:
        proc.kill()
        proc.wait()


def test_parent_pid_returns_none_for_an_implausible_pid():
    # A pid this large is never actually assigned -- avoids racing Windows'
    # fast pid-reuse (a just-terminated pid can be reassigned to an unrelated
    # live process within milliseconds on a busy machine).
    assert procs.parent_pid(999_999_999) is None


def test_is_descendant_of_true_for_a_direct_child():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(3)"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        proc.stdout.readline()
        assert procs.is_descendant_of(proc.pid, {os.getpid()})
    finally:
        proc.kill()
        proc.wait()


def test_is_descendant_of_true_for_a_grandchild_two_levels_up():
    proc, grandchild_pid = _spawn_child_with_grandchild()
    try:
        assert procs.is_descendant_of(grandchild_pid, {os.getpid()})
    finally:
        try:
            os.kill(grandchild_pid, signal.SIGTERM)
        except OSError:
            pass
        proc.kill()
        proc.wait()


def test_is_descendant_of_false_when_ancestor_is_unrelated():
    assert procs.is_descendant_of(os.getpid(), {999_999_999}) is False


def test_is_descendant_of_false_for_empty_ancestor_set():
    assert procs.is_descendant_of(os.getpid(), set()) is False


def test_processes_with_executable_under_skips_a_protected_descendant(monkeypatch):
    """Unit-level check of the wiring: a candidate under ``root`` is dropped
    when ``protect_ancestors`` proves descent AND it reads as recent,
    kept otherwise."""
    monkeypatch.setattr(
        procs, "_iter_processes_posix",
        lambda: iter([(555, "/tmp", "python"), (777, "/tmp", "python")]),
    )
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs, "process_executable_path",
        lambda pid: "/root/versions/1.0.0/bin/python",
    )
    monkeypatch.setattr(
        procs, "is_descendant_of",
        lambda pid, ancestors, **kw: pid == 555,
    )
    monkeypatch.setattr(procs, "process_age_seconds", lambda pid: 1.0)

    hits = procs.processes_with_executable_under(
        "/root/versions", protect_ancestors={42},
    )

    assert [h["pid"] for h in hits] == [777]


def test_processes_with_executable_under_still_reaps_an_old_protected_descendant(monkeypatch):
    """#4481 review: ancestor protection must not indefinitely shelter a
    genuinely wedged descendant (the exact #4268 case this sweep exists
    for) just because its parent is a live, registered launcher."""
    monkeypatch.setattr(
        procs, "_iter_processes_posix",
        lambda: iter([(555, "/tmp", "python")]),
    )
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs, "process_executable_path",
        lambda pid: "/root/versions/1.0.0/bin/python",
    )
    monkeypatch.setattr(procs, "is_descendant_of", lambda pid, ancestors, **kw: True)
    monkeypatch.setattr(
        procs, "process_age_seconds",
        lambda pid: procs._PROTECT_ANCESTOR_GRACE_SECONDS + 1,
    )

    hits = procs.processes_with_executable_under(
        "/root/versions", protect_ancestors={42},
    )

    assert [h["pid"] for h in hits] == [555]  # too old to protect -- stays reapable


def test_processes_with_executable_under_fails_closed_on_unmeasurable_age(monkeypatch):
    """An ancestor-descended candidate whose age can't be read is NOT
    protected -- protection is the exception path, so failing to prove
    'still fresh' must not grant it."""
    monkeypatch.setattr(
        procs, "_iter_processes_posix",
        lambda: iter([(555, "/tmp", "python")]),
    )
    monkeypatch.setattr(procs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        procs, "process_executable_path",
        lambda pid: "/root/versions/1.0.0/bin/python",
    )
    monkeypatch.setattr(procs, "is_descendant_of", lambda pid, ancestors, **kw: True)
    monkeypatch.setattr(procs, "process_age_seconds", lambda pid: None)

    hits = procs.processes_with_executable_under(
        "/root/versions", protect_ancestors={42},
    )

    assert [h["pid"] for h in hits] == [555]


def test_process_age_seconds_of_a_real_child_is_small_and_nonnegative():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(3)"],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        proc.stdout.readline()
        age = procs.process_age_seconds(proc.pid)
        assert age is not None
        assert 0 <= age < 30
    finally:
        proc.kill()
        proc.wait()


def test_process_age_seconds_returns_none_for_an_implausible_pid():
    assert procs.process_age_seconds(999_999_999) is None
