"""Tests for the running-version boot marker (dotfiles #533)."""

from __future__ import annotations

import json
import os

import pytest

from agent_bridge import __version__
from agent_bridge.runtime_version import (
    PENDING_GENERATION_IDS_FILE,
    RUNNING_VERSION_FILE,
    consume_pending_generation_id,
    set_running_generation_id,
    stage_pending_generation_id,
    write_running_version,
)


@pytest.fixture
def fake_identity(monkeypatch):
    """Pending-id entries are identity-bound (``zdd.diagnostics.
    process_start_time``) to close a real PID-reuse hazard (an abandoned
    passive's pid could later be reused by an unrelated process) -- real
    fake/synthetic test pids (111, 222, ...) don't correspond to actual OS
    processes, so ``process_start_time`` would genuinely return ``None``
    for them. Patch it to a deterministic, per-pid value so these
    behavioral tests (keying, pruning, non-dict recovery) stay focused on
    what they're actually testing, not on real-process plumbing -- the
    identity-verification contract itself is covered separately by
    ``test_concurrent_staging_and_consuming_never_loses_an_update``, which
    uses real subprocesses end to end.
    """
    import zdd.diagnostics

    monkeypatch.setattr(
        zdd.diagnostics, "process_start_time", lambda pid: f"faketime-{pid}"
    )


def test_write_running_version_content(tmp_path):
    write_running_version(tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["version"] == __version__
    assert data["pid"] == os.getpid()
    assert data["started_at"]  # ISO-8601 boot timestamp


def test_write_running_version_creates_dir(tmp_path):
    d = tmp_path / "nested" / ".agent-bridge"
    write_running_version(d)
    assert (d / RUNNING_VERSION_FILE).is_file()


def test_write_running_version_explicit_pid_and_version(tmp_path):
    # The cutover reconciler records the *new* daemon's pid + version, not the
    # deploy process's (dotfiles #533 caveat #1).
    write_running_version(tmp_path, pid=98765, version="9.9.9")
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 98765
    assert data["version"] == "9.9.9"
    assert data["pid"] != os.getpid()


def test_write_running_version_never_raises(tmp_path):
    # A directory path that cannot be created (a file sits where a parent dir is
    # expected) must be swallowed -- the marker is best-effort, never fatal.
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    write_running_version(afile / "sub")  # must not raise
    assert not (afile / "sub" / RUNNING_VERSION_FILE).exists()


def test_write_running_version_includes_generation_id_when_given(tmp_path):
    write_running_version(tmp_path, generation_id="0.4.1-1234-1700000000.000000")
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "0.4.1-1234-1700000000.000000"


def test_write_running_version_omits_generation_id_by_default(tmp_path):
    # The boot-time caller (app.py's lifespan) runs BEFORE the SessionManager
    # that computes the real id exists -- must not fabricate one.
    write_running_version(tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert "generation_id" not in data


def test_set_running_generation_id_merges_onto_existing_marker_of_same_pid(
    tmp_path, monkeypatch
):
    # The follow-up call must preserve the earlier write's pid/version/
    # started_at verbatim, only adding generation_id -- but ONLY when that
    # marker already belongs to THIS process (pid match).
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    write_running_version(tmp_path, pid=4242, version="9.9.9")
    set_running_generation_id("9.9.9-4242-1700000000.500000", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 4242
    assert data["version"] == "9.9.9"
    assert data["generation_id"] == "9.9.9-4242-1700000000.500000"


def test_set_running_generation_id_resets_marker_owned_by_a_different_pid(
    tmp_path, monkeypatch
):
    # A relay-disabled normal primary skips the earlier boot-time
    # write_running_version() call, so on restart the on-disk marker can
    # still be a PREVIOUS, now-dead daemon's own still-valid record.
    # Merging onto it would attach THIS process's real id to someone
    # else's stale pid/version -- must reset fresh instead.
    write_running_version(tmp_path, pid=111, version="1.0.0")  # a prior daemon
    monkeypatch.setattr(os, "getpid", lambda: 222)  # this (different) process
    set_running_generation_id("2.0.0-222-1700000001.000000", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 222  # reset to THIS process, not the stale 111
    assert data["version"] == __version__  # not the stale "1.0.0"
    assert data["generation_id"] == "2.0.0-222-1700000001.000000"


def test_set_running_generation_id_starts_fresh_marker_if_absent(tmp_path):
    # Unusual ordering (or an earlier write failure) -- still best-effort,
    # never raises, and the generation_id is recorded regardless.
    d = tmp_path / ".agent-bridge"
    set_running_generation_id("1.0.0-1-1700000000.000000", d)
    data = json.loads((d / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "1.0.0-1-1700000000.000000"
    assert data["pid"] == os.getpid()


def test_set_running_generation_id_never_raises(tmp_path):
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    set_running_generation_id("whatever", afile / "sub")  # must not raise
    assert not (afile / "sub" / RUNNING_VERSION_FILE).exists()


def test_set_running_generation_id_recovers_from_non_dict_marker(tmp_path):
    # A malformed/legacy marker that parses as JSON but isn't an object (a
    # bare list, in this case) must not raise on payload["generation_id"] =
    # ... -- fall back to a fresh dict instead of assuming dict-shape.
    (tmp_path / RUNNING_VERSION_FILE).write_text("[1, 2, 3]", encoding="utf-8")
    set_running_generation_id("recovered-gen-id", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["generation_id"] == "recovered-gen-id"
    assert data["pid"] == os.getpid()


def test_stage_pending_generation_id_recovers_from_non_dict_pending_file(
    tmp_path, fake_identity
):
    (tmp_path / PENDING_GENERATION_IDS_FILE).write_text("[1, 2, 3]", encoding="utf-8")
    stage_pending_generation_id(111, "fresh-gen", tmp_path)
    data = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert data == {"111": {"generation_id": "fresh-gen", "start_time": "faketime-111"}}


def test_pending_generation_id_roundtrip(tmp_path, fake_identity):
    assert consume_pending_generation_id(555, tmp_path) is None  # nothing staged
    stage_pending_generation_id(555, "roundtrip-gen-id", tmp_path)
    assert consume_pending_generation_id(555, tmp_path) == "roundtrip-gen-id"
    # Consumed exactly once -- a second pop finds nothing.
    assert consume_pending_generation_id(555, tmp_path) is None


def test_stage_pending_generation_id_keys_by_pid(tmp_path, fake_identity, monkeypatch):
    from agent_bridge.session_host import osutil

    # Keep both fake pids "alive" from the pruning step's perspective --
    # this test is about keying by pid, not pruning (see the dedicated
    # pruning test below).
    monkeypatch.setattr(osutil, "pid_alive", lambda pid: True)
    stage_pending_generation_id(111, "gen-for-111", tmp_path)
    stage_pending_generation_id(222, "gen-for-222", tmp_path)
    assert consume_pending_generation_id(111, tmp_path) == "gen-for-111"
    # 222's own entry survives consuming a DIFFERENT pid's.
    assert consume_pending_generation_id(222, tmp_path) == "gen-for-222"


def test_stage_pending_generation_id_prunes_dead_pids(
    tmp_path, fake_identity, monkeypatch
):
    from agent_bridge.session_host import osutil

    # A dead pid's stale entry (an earlier aborted/retired passive) must be
    # pruned the next time anything stages a new entry, so this file never
    # grows unbounded across many cutover attempts.
    monkeypatch.setattr(osutil, "pid_alive", lambda pid: pid != 999999)
    stage_pending_generation_id(999999, "abandoned-gen", tmp_path)
    stage_pending_generation_id(111, "fresh-gen", tmp_path)
    data = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert "999999" not in data
    assert data["111"]["generation_id"] == "fresh-gen"


def test_consume_pending_generation_id_none_when_never_staged(tmp_path):
    assert consume_pending_generation_id(12345, tmp_path) is None


def test_consume_pending_generation_id_none_on_missing_file(tmp_path):
    assert consume_pending_generation_id(1, tmp_path / "nonexistent") is None


def test_consume_pending_generation_id_none_on_pid_reuse(tmp_path, monkeypatch):
    # The real identity-verification contract: an entry staged for a pid
    # whose process identity has since changed (the pid was recycled by an
    # unrelated process) must never be trusted, even though the bare pid
    # number still matches.
    import zdd.diagnostics as diag

    monkeypatch.setattr(diag, "process_start_time", lambda pid: "original-start-time")
    stage_pending_generation_id(111, "original-owner-gen", tmp_path)
    # A different process now holds the same pid number.
    monkeypatch.setattr(diag, "process_start_time", lambda pid: "different-start-time")
    assert consume_pending_generation_id(111, tmp_path) is None
    # Still consumed/removed (cleanup), not left dangling for a future
    # (also wrong) consumer to pick up.
    data = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert "111" not in data


def test_stage_pending_generation_id_never_raises(tmp_path):
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    stage_pending_generation_id(1, "whatever", afile / "sub")  # must not raise
    assert not (afile / "sub" / PENDING_GENERATION_IDS_FILE).exists()


def test_concurrent_staging_and_consuming_never_loses_an_update(tmp_path):
    """Concurrent, unlocked read-modify-write on the pending-ids file could
    silently erase another writer's entry. The locking guarantee here is
    explicitly cross-process (POSIX ``fcntl.flock`` / Windows
    ``msvcrt.locking``), so this drives REAL separate subprocesses (not
    threads -- thread overlap within one process cannot validate separate-
    process lock ownership) through concurrent stage-then-consume round
    trips, each against its OWN real, live pid (a real subprocess, not a
    synthetic pid number, so the identity-verification start-time check
    also matches for real). Every round trip must see its own entry --
    never lost to a concurrent writer, never someone else's value.
    """
    import subprocess
    import sys

    n = 8
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from agent_bridge.runtime_version import ("
        "stage_pending_generation_id, consume_pending_generation_id)\n"
        "directory, gen_id = Path(sys.argv[1]), sys.argv[2]\n"
        "pid = os.getpid()\n"
        "stage_pending_generation_id(pid, gen_id, directory)\n"
        "got = consume_pending_generation_id(pid, directory)\n"
        "print(got or '')\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), f"gen-{i}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(n)
    ]
    outputs = []
    for i, p in enumerate(procs):
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, f"subprocess {i} failed: {err}"
        outputs.append(out.strip())

    assert outputs == [f"gen-{i}" for i in range(n)]
    # Nothing left dangling in the pending file afterward.
    remaining = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert remaining == {}


def test_cold_liveness_import_precedes_pending_lock(tmp_path):
    import subprocess
    import sys

    script = """
import importlib.abc
import os
import sys
from pathlib import Path
from agent_bridge import runtime_version as rv
holding = False
observed = []
class ImportObserver(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith("agent_bridge.session_host"):
            assert not holding, "cold liveness import consumed the pending-lock budget"
            observed.append(fullname)
        return None
sys.meta_path.insert(0, ImportObserver())
original = rv._with_pending_lock
def checked(directory, fn):
    def action():
        global holding
        holding = True
        try:
            return fn()
        finally:
            holding = False
    return original(directory, action)
rv._with_pending_lock = checked
directory = Path(sys.argv[1])
rv.stage_pending_generation_id(os.getpid(), "cold-generation", directory)
assert observed, "fresh process did not exercise the cold liveness import"
assert rv.consume_pending_generation_id(os.getpid(), directory) == "cold-generation"
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
