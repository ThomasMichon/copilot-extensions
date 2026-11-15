"""Original-source binding across hook and resident process boundaries."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import native_lifecycle_source as source
from agent_worktrees import native_source_context as context
from agent_worktrees import tracking


SID = "00000000-0000-4000-8000-000000000001"
OTHER_SID = "00000000-0000-4000-8000-000000000002"
EPOCH = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}


@pytest.fixture
def witness(monkeypatch):
    monkeypatch.setattr(context, "epoch", lambda: dict(EPOCH))
    return {
        "version": 1, "qualified": True, **EPOCH,
        "ancestors": [{"pid": 101, "start_time": "100"}],
    }


def test_resident_never_recaptures_missing_original_source(monkeypatch):
    monkeypatch.setattr(
        context, "capture", lambda: pytest.fail("resident source must not be recaptured"),
    )
    assert source.witness({}, resident=True) is None
    assert source.witness(
        {"_agentWorktrees": {"nativeSource": {"qualified": False}}}, resident=True,
    ) == {"qualified": False}


def test_exact_uuid_hold_qualifies_start_not_a_different_process(witness, monkeypatch):
    monkeypatch.setattr(
        source, "session_process_owns_hold",
        lambda sid, pid, birth: sid == SID and pid == 101 and birth == "100",
    )
    expected = context.identity(SID, 101, "100", EPOCH)
    assert source.start_identity(SID, witness) == expected
    assert source.start_identity(OTHER_SID, witness) is None
    changed = {**witness, "ancestors": [{"pid": 101, "start_time": "200"}]}
    assert source.start_identity(SID, changed) is None


def test_zero_boot_tick_is_a_valid_native_birth_token(witness):
    witness["ancestors"][0]["start_time"] = "0"
    assert context.candidates(SID, witness) == [
        (context.identity(SID, 101, "0", EPOCH), 101, "0"),
    ]


@pytest.mark.parametrize("change", [
    {"boot_id": OTHER_SID}, {"environment_id": "f" * 32}, {"platform": "wsl"},
    {"ancestors": []}, {"ancestors": [{"pid": True, "start_time": "100"}]},
    {"ancestors": [{"pid": 101, "start_time": "100"}] * 17},
    {"qualified": False}, {"version": True},
])
def test_invalid_original_source_stays_unconfirmed(witness, change):
    assert source.start_identity(SID, {**witness, **change}) is None


def test_delayed_end_matches_history_not_current_head(witness):
    token = context.identity(SID, 101, "100", EPOCH)
    entry = tracking.SessionEntry(SID, "first", activations=[
        tracking.SessionActivation(
            1, "first", "first", source_identity=token, ended_at="replacement",
        ),
        tracking.SessionActivation(
            2, "replacement", "replacement", source_identity="native:replacement",
        ),
    ])
    assert source.end_identity(SID, witness, entry) == token
    assert source.end_identity(OTHER_SID, witness, entry) is None
    entry.activations[-1].source_identity = token
    assert source.end_identity(SID, witness, entry) is None


def test_capture_is_bounded_metadata_only(witness, monkeypatch):
    monkeypatch.setattr(context, "process", lambda pid: {
        "pid": pid, "start_time": str(pid), "parent_pid": pid + 1,
    })
    value = context.capture()
    assert value["qualified"] is True
    assert len(value["ancestors"]) == context.MAX_ANCESTORS
    assert set(value) == {"version", "qualified", "platform", "environment_id", "boot_id", "ancestors"}
    assert all(set(item) == {"pid", "start_time"} for item in value["ancestors"])


def test_capture_refuses_parent_reuse_or_reparenting(witness, monkeypatch):
    monkeypatch.setattr(os, "getpid", lambda: 1000)
    observations = iter([
        {"pid": 1000, "start_time": "999", "parent_pid": 101},
        {"pid": 101, "start_time": "200", "parent_pid": 1},
        {"pid": 1000, "start_time": "999", "parent_pid": 1},
    ])
    monkeypatch.setattr(context, "process", lambda pid: next(observations))
    value = context.capture()
    assert value["qualified"] is False
    assert "ancestors" not in value


def test_actual_held_file_start_binding(tmp_path, monkeypatch, witness):
    if not sys.platform.startswith("linux"):
        pytest.skip("Exact native hold proof requires Linux procfs")
    from agent_worktrees import locks, session_liveness, sessions

    pid = os.getpid()
    birth = locks.process_start_time(pid)
    assert birth
    witness["ancestors"] = [{"pid": pid, "start_time": birth}]
    root = tmp_path / "sessions"
    directory = root / SID
    directory.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    monkeypatch.setattr(source, "session_process_owns_hold", session_liveness.session_process_owns_hold)
    hold = directory / f"inuse.{pid}.hold"
    hold.touch()
    assert source.start_identity(SID, witness) is None
    with hold.open("r"):
        assert source.start_identity(SID, witness) == context.identity(SID, pid, birth, EPOCH)
        witness["ancestors"][0]["start_time"] = str(int(birth) + 1)
        assert source.start_identity(SID, witness) is None


@pytest.mark.parametrize("release,platform", [("6.8.0", "linux"), ("microsoft", "wsl")])
def test_host_epoch_retains_its_existing_identity(monkeypatch, release, platform):
    monkeypatch.setattr(context, "sys", SimpleNamespace(platform="linux"))
    values = {
        "/proc/sys/kernel/random/boot_id": SID,
        "/proc/1/cgroup": "0::/",
        "/proc/sys/kernel/osrelease": release,
        "/etc/machine-id": "e" * 32,
    }
    monkeypatch.setattr(context, "_text", values.__getitem__)
    monkeypatch.setattr(context.Path, "exists", lambda _: False)
    monkeypatch.setattr(context, "process", lambda _: {"start_time": "12"})
    assert context.epoch() == {
        "platform": platform,
        "environment_id": "e" * 32,
        "boot_id": SID + (":12" if platform == "wsl" else ""),
    }


@pytest.mark.parametrize("marker,cgroup", [
    ("/.dockerenv", "0::/"),
    ("/run/.containerenv", "0::/"),
    (None, "0::/docker/example"),
    (None, "0::/kubepods/example"),
    (None, "0::/containerd/example"),
    (None, "0::/lxc/example"),
    (None, "0::/libpod/example"),
])
def test_container_epoch_uses_native_namespace_not_image_machine_id(
    monkeypatch, marker, cgroup,
):
    monkeypatch.setattr(context, "sys", SimpleNamespace(platform="linux"))
    values = {"/proc/sys/kernel/random/boot_id": SID, "/proc/1/cgroup": cgroup}
    monkeypatch.setattr(context, "_text", values.__getitem__)
    monkeypatch.setattr(context.Path, "exists", lambda path: path.as_posix() == marker)
    scope = [("pid", 4, 101), ("mnt", 4, 102), ("user", 4, 103)]
    monkeypatch.setattr(context, "_namespace_scope", lambda: scope)
    init = {"pid": 1, "start_time": "10", "parent_pid": 0}
    monkeypatch.setattr(context, "process", lambda _: init)
    first = context.epoch()
    assert first["platform"] == "linux" and len(first["environment_id"]) == 32
    assert first["boot_id"] == SID
    assert context.epoch() == first
    scope = [("pid", 4, 201), ("mnt", 4, 102), ("user", 4, 103)]
    next_namespace = context.epoch()
    assert next_namespace["environment_id"] != first["environment_id"]
    init = {**init, "start_time": "11"}
    assert context.epoch()["environment_id"] != next_namespace["environment_id"]
    assert context.epoch()["boot_id"] == SID


@pytest.mark.parametrize("change", ["namespace", "init", "foreign-procfs"])
def test_container_epoch_refuses_changed_or_foreign_observation(monkeypatch, change):
    scopes = iter([[("pid", 4, 101)], [("pid", 4, 102 if change == "namespace" else 101)]])
    inits = iter([
        {"pid": 1, "start_time": "10", "parent_pid": 1 if change == "foreign-procfs" else 0},
        {"pid": 1, "start_time": "11" if change == "init" else "10", "parent_pid": 0},
    ])
    monkeypatch.setattr(context, "_namespace_scope", lambda: next(scopes))
    monkeypatch.setattr(context, "process", lambda _: next(inits))
    with pytest.raises(ValueError, match="epoch changed|namespace init"):
        context._container_epoch(SID)


@pytest.mark.parametrize(
    "failure", ["foreign-procfs", "foreign-namespace", "unreadable", "invalid-inode"],
)
def test_namespace_scope_requires_inspectable_current_procfs(monkeypatch, failure):
    def stat(path):
        if failure == "unreadable":
            raise PermissionError("namespace is inaccessible")
        inode = 0 if failure == "invalid-inode" else 100
        if failure == "foreign-namespace" and path.as_posix() == "/proc/1/ns/pid":
            inode = 101
        return SimpleNamespace(st_dev=4, st_ino=inode)

    monkeypatch.setattr(context, "os", SimpleNamespace(
        getpid=lambda: 100, readlink=lambda _: "101" if failure == "foreign-procfs" else "100",
    ))
    monkeypatch.setattr(context.Path, "stat", stat)
    with pytest.raises((ValueError, PermissionError)):
        context._namespace_scope()
