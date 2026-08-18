"""Tests for the stale-version reaper (scripts/reap_versions.py).

Covers the pure slot-attribution helper, the reap policy (only NON-current
slots, never self/current), and a real process-tree terminate. The process
*enumeration* is monkeypatched so the policy can be exercised deterministically
without standing up real per-version venvs; `_terminate_tree` is exercised
against a genuinely spawned child.

`reap_versions` imports the (unmodified) shared primitive's attribution helpers
by name, so those names live on the `reap_versions` module and are patched there.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    # reap_versions imports `versioned_runtime` as a sibling; ensure scripts/ is
    # importable before the module executes its top-level import.
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "reap_versions_under_test", _SCRIPTS / "reap_versions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rv = _load()


# --- pure slot attribution ------------------------------------------------

def test_slot_of_path_attributes_and_rejects(tmp_path):
    versions_abs = os.path.abspath(str(tmp_path / "versions"))
    versions = {"0.2.0-dev44", "0.2.0-dev49"}
    inside = os.path.join(versions_abs, "0.2.0-dev44", "Scripts", "python.exe")
    assert rv._slot_of_path(inside, versions_abs, versions) == "0.2.0-dev44"
    unknown = os.path.join(versions_abs, "0.2.0-dev01", "bin", "python")
    assert rv._slot_of_path(unknown, versions_abs, versions) is None
    assert rv._slot_of_path("/somewhere/else/python", versions_abs, versions) is None
    assert rv._slot_of_path(None, versions_abs, versions) is None


# --- reap policy ----------------------------------------------------------

def test_reap_targets_only_noncurrent_slots(tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(rv, "current_version", lambda *a, **k: "0.2.0-dev49")
    monkeypatch.setattr(rv, "_pids_by_slot",
                        lambda root: {"0.2.0-dev44": {111, 112}, "0.2.0-dev49": {222}})
    monkeypatch.setattr(rv, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    reaped = rv.reap_stale(tmp_path)
    assert sorted(killed) == [111, 112], "only non-current-slot pids should be reaped"
    assert {r["pid"] for r in reaped} == {111, 112}
    assert all(r["version"] == "0.2.0-dev44" for r in reaped)
    assert all(r["terminated"] for r in reaped)


def test_reap_excludes_self_and_explicit_pids(tmp_path, monkeypatch):
    killed: list[int] = []
    me = os.getpid()
    monkeypatch.setattr(rv, "current_version", lambda *a, **k: "0.2.0-dev49")
    monkeypatch.setattr(rv, "_pids_by_slot",
                        lambda root: {"0.2.0-dev44": {me, 111, 999}})
    monkeypatch.setattr(rv, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    rv.reap_stale(tmp_path, exclude_pids={999})
    assert killed == [111], "self pid and excluded pid must be spared"


def test_reap_matches_current_across_version_normalization(tmp_path, monkeypatch):
    # current-version marker is PEP 440 (dots); the dir name uses a dash.
    killed: list[int] = []
    monkeypatch.setattr(rv, "current_version", lambda *a, **k: "0.2.0.dev49")
    monkeypatch.setattr(rv, "_pids_by_slot", lambda root: {"0.2.0-dev49": {333}})
    monkeypatch.setattr(rv, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    reaped = rv.reap_stale(tmp_path)
    assert killed == [], "the current slot must be spared despite dash/dot form"
    assert reaped == []


# --- real terminate -------------------------------------------------------

def test_terminate_tree_kills_real_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert rv._pid_alive(proc.pid)
        assert rv._terminate_tree(proc.pid) is True
        # proc.wait() reaps the child: on POSIX a killed-but-unwaited child lingers
        # as a zombie that os.kill(pid, 0) still reports alive, so assert via
        # wait() (a non-None return code == it exited) rather than _pid_alive.
        assert proc.wait(timeout=15) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_terminate_tree_noop_on_dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)  # reap it so the pid is fully gone (not a zombie)
    assert rv._terminate_tree(proc.pid) is False
