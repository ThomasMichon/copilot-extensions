"""Tests for the stale-version reaper in scripts/versioned_runtime.py.

Covers the pure slot-attribution helper, the reap policy (only NON-current
slots, never self/current), and a real process-tree terminate. The process
*enumeration* is monkeypatched so the policy can be exercised deterministically
without standing up real per-version venvs; `_terminate_tree` is exercised
against a genuinely spawned child.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

_VR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime.py"


def _load():
    spec = importlib.util.spec_from_file_location("versioned_runtime_under_test", _VR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load()


# --- pure slot attribution ------------------------------------------------

def test_slot_of_path_attributes_and_rejects(tmp_path):
    versions_abs = os.path.abspath(str(tmp_path / "versions"))
    versions = {"0.2.0-dev44", "0.2.0-dev49"}
    inside = os.path.join(versions_abs, "0.2.0-dev44", "Scripts", "python.exe")
    assert vr._slot_of_path(inside, versions_abs, versions) == "0.2.0-dev44"
    # A path under an unknown version dir is not attributed.
    unknown = os.path.join(versions_abs, "0.2.0-dev01", "bin", "python")
    assert vr._slot_of_path(unknown, versions_abs, versions) is None
    # A path outside the versions root is not attributed.
    assert vr._slot_of_path("/somewhere/else/python", versions_abs, versions) is None
    assert vr._slot_of_path(None, versions_abs, versions) is None


# --- reap policy ----------------------------------------------------------

def test_reap_targets_only_noncurrent_slots(tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(vr, "current_version", lambda *a, **k: "0.2.0-dev49")
    monkeypatch.setattr(vr, "_pids_by_slot",
                        lambda root: {"0.2.0-dev44": {111, 112}, "0.2.0-dev49": {222}})
    monkeypatch.setattr(vr, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    reaped = vr.reap_stale(tmp_path)
    assert sorted(killed) == [111, 112], "only non-current-slot pids should be reaped"
    assert {r["pid"] for r in reaped} == {111, 112}
    assert all(r["version"] == "0.2.0-dev44" for r in reaped)
    assert all(r["terminated"] for r in reaped)


def test_reap_excludes_self_and_explicit_pids(tmp_path, monkeypatch):
    killed: list[int] = []
    me = os.getpid()
    monkeypatch.setattr(vr, "current_version", lambda *a, **k: "0.2.0-dev49")
    monkeypatch.setattr(vr, "_pids_by_slot",
                        lambda root: {"0.2.0-dev44": {me, 111, 999}})
    monkeypatch.setattr(vr, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    vr.reap_stale(tmp_path, exclude_pids={999})
    assert killed == [111], "self pid and excluded pid must be spared"


def test_reap_matches_current_across_version_normalization(tmp_path, monkeypatch):
    # current-version marker is PEP 440 (dots); the dir name uses a dash.
    killed: list[int] = []
    monkeypatch.setattr(vr, "current_version", lambda *a, **k: "0.2.0.dev49")
    monkeypatch.setattr(vr, "_pids_by_slot", lambda root: {"0.2.0-dev49": {333}})
    monkeypatch.setattr(vr, "_terminate_tree",
                        lambda pid: (killed.append(pid), True)[1])

    reaped = vr.reap_stale(tmp_path)
    assert killed == [], "the current slot must be spared despite dash/dot form"
    assert reaped == []


# --- real terminate -------------------------------------------------------

def test_terminate_tree_kills_real_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert vr._pid_alive(proc.pid)
        assert vr._terminate_tree(proc.pid) is True
        deadline = time.time() + 15
        while time.time() < deadline and vr._pid_alive(proc.pid):
            time.sleep(0.1)
        assert not vr._pid_alive(proc.pid), "process should be terminated"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_terminate_tree_noop_on_dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    # Already exited -> nothing to terminate.
    assert vr._terminate_tree(proc.pid) is False
