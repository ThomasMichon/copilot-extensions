"""Tests for the running-version boot marker (dotfiles #533)."""

from __future__ import annotations

import json
import os

from agent_bridge import __version__
from agent_bridge.runtime_version import (
    PENDING_GENERATION_IDS_FILE,
    RUNNING_VERSION_FILE,
    consume_pending_generation_id,
    set_running_generation_id,
    stage_pending_generation_id,
    write_running_version,
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


def test_set_running_generation_id_merges_onto_existing_marker(tmp_path):
    # The follow-up call must preserve the earlier write's pid/version/
    # started_at verbatim, only adding generation_id.
    write_running_version(tmp_path, pid=4242, version="9.9.9")
    set_running_generation_id("9.9.9-4242-1700000000.500000", tmp_path)
    data = json.loads((tmp_path / RUNNING_VERSION_FILE).read_text(encoding="utf-8"))
    assert data["pid"] == 4242
    assert data["version"] == "9.9.9"
    assert data["generation_id"] == "9.9.9-4242-1700000000.500000"


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


def test_stage_pending_generation_id_recovers_from_non_dict_pending_file(tmp_path):
    (tmp_path / PENDING_GENERATION_IDS_FILE).write_text("[1, 2, 3]", encoding="utf-8")
    stage_pending_generation_id(111, "fresh-gen", tmp_path)
    data = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert data == {"111": "fresh-gen"}


def test_read_running_generation_id_roundtrip(tmp_path):
    assert consume_pending_generation_id(555, tmp_path) is None  # nothing staged
    stage_pending_generation_id(555, "roundtrip-gen-id", tmp_path)
    assert consume_pending_generation_id(555, tmp_path) == "roundtrip-gen-id"
    # Consumed exactly once -- a second pop finds nothing.
    assert consume_pending_generation_id(555, tmp_path) is None


def test_stage_pending_generation_id_keys_by_pid(tmp_path, monkeypatch):
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


def test_stage_pending_generation_id_prunes_dead_pids(tmp_path, monkeypatch):
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
    assert data["111"] == "fresh-gen"


def test_consume_pending_generation_id_none_when_never_staged(tmp_path):
    assert consume_pending_generation_id(12345, tmp_path) is None


def test_consume_pending_generation_id_none_on_missing_file(tmp_path):
    assert consume_pending_generation_id(1, tmp_path / "nonexistent") is None


def test_stage_pending_generation_id_never_raises(tmp_path):
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    stage_pending_generation_id(1, "whatever", afile / "sub")  # must not raise
    assert not (afile / "sub" / PENDING_GENERATION_IDS_FILE).exists()


def test_concurrent_staging_and_consuming_never_loses_an_update(
    tmp_path, monkeypatch
):
    """The exact race the review flagged: concurrent unlocked read-modify-
    write on the pending-ids file could silently erase another writer's
    entry. Drive real threads through stage/consume concurrently and
    assert every staged entry is accounted for (either consumed exactly
    once, or still present) -- never silently lost.
    """
    import threading

    from agent_bridge.session_host import osutil

    monkeypatch.setattr(osutil, "pid_alive", lambda pid: True)

    n = 20
    consumed: list[str | None] = [None] * n
    barrier = threading.Barrier(n)

    def stage_then_consume(i: int) -> None:
        barrier.wait()  # maximize actual overlap
        stage_pending_generation_id(1000 + i, f"gen-{i}", tmp_path)
        consumed[i] = consume_pending_generation_id(1000 + i, tmp_path)

    threads = [
        threading.Thread(target=stage_then_consume, args=(i,)) for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Every one of this thread's own stage->consume round trips must see
    # its OWN entry -- never None (lost to a concurrent writer) and never
    # someone else's value (cross-talk).
    assert consumed == [f"gen-{i}" for i in range(n)]
    # Nothing left dangling in the pending file afterward.
    remaining = json.loads(
        (tmp_path / PENDING_GENERATION_IDS_FILE).read_text(encoding="utf-8")
    )
    assert remaining == {}
