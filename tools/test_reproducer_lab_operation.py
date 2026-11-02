"""Synthetic guest-state custody controls; no Docker or host mount inspection."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

import guest_operation
import mounts

TABLE = (
    "1 9 0:1 / / ro,relatime - overlay overlay ro\n"
    "2 1 8:1 /volumes/state/_data /lab/state rw,relatime - ext4 /dev/synthetic rw\n"
)
STATE = guest_operation.GuestState(
    123, 456, mounts.StateMount(2, "8:1", "/volumes/state/_data", "ext4"),
)


def test_state_mount_is_only_local_mount_evidence() -> None:
    result = mounts.inspect_state_mount(TABLE, "8:1")
    assert result == STATE.mount
    assert not hasattr(result, "ready")


@pytest.mark.parametrize("old,new", [
    ("/ ro,relatime", "/ rw,relatime"),
    ("/ ro,relatime", "/ ro,rw"),
    ("/lab/state rw,relatime", "/lab/state ro,relatime"),
    ("/lab/state rw,relatime", "/lab/state rw,ro"),
    ("/lab/state", "/lab/foreign"),
    ("8:1", "0:1"),
    ("ext4", "tmpfs"),
    ("ext4", "ramfs"),
    ("ext4", "overlay"),
    ("ext4", "proc"),
    ("ext4", "unknown"),
])
def test_unavailable_or_volatile_state_mount_refuses(old: str, new: str) -> None:
    with pytest.raises(ValueError):
        mounts.inspect_state_mount(TABLE.replace(old, new), "8:1")


@pytest.mark.parametrize("suffix", [
    TABLE.splitlines(keepends=True)[1],
    "3 2 8:1 /other /lab/state/sub rw - ext4 /dev/synthetic rw\n",
])
def test_shadowed_or_nested_state_mount_refuses(suffix: str) -> None:
    with pytest.raises(ValueError):
        mounts.inspect_state_mount(TABLE + suffix, "8:1")


@pytest.mark.parametrize("device", ["8:1", "8:2"])
def test_ancestor_overmount_refuses_even_on_same_device(device: str) -> None:
    table = TABLE + f"3 1 {device} /other /lab rw - ext4 /dev/synthetic rw\n"
    with pytest.raises(ValueError, match="state mount"):
        mounts.inspect_state_mount(table, "8:1")


@pytest.mark.parametrize("filesystem", ["ext4", "xfs", "btrfs", "zfs"])
def test_supported_nonvolatile_filesystems(filesystem: str) -> None:
    assert mounts.inspect_state_mount(TABLE.replace("ext4", filesystem), "8:1").filesystem == filesystem


def test_state_device_must_match_open_directory() -> None:
    with pytest.raises(ValueError):
        mounts.inspect_state_mount(TABLE, "8:2")


def test_state_lock_posture_accepts_only_expected_plain_file(monkeypatch: Any) -> None:
    fields = {"st_mode": 0o100600, "st_uid": 1000, "st_gid": 1000, "st_dev": 123, "st_nlink": 1}
    monkeypatch.setattr(guest_operation, "LOCK", SimpleNamespace(lstat=lambda: SimpleNamespace(**fields)))
    guest_operation._check_lock(STATE, required=True)


class FakeLock:
    def __init__(self) -> None:
        self.held = False
        self.assertions = 0
        self.enters = 0
        self.exits = 0
        self.fail_acquire = False
        self.fail_assert = False

    def __enter__(self) -> FakeLock:
        self.enters += 1
        if self.fail_acquire or self.held:
            raise RuntimeError("synthetic contention")
        self.held = True
        return self

    def assert_owned(self) -> None:
        self.assertions += 1
        if not self.held or self.fail_assert:
            raise RuntimeError("synthetic custody lost")

    def __exit__(self, *_args: object) -> None:
        self.exits += 1
        self.held = False


@pytest.fixture
def operation_seams(monkeypatch: Any) -> tuple[FakeLock, list[bool]]:
    holder = FakeLock()
    checks: list[bool] = []
    monkeypatch.setattr(guest_operation, "observe_state", lambda: STATE)
    monkeypatch.setattr(guest_operation, "_check_lock", lambda _state, *, required: checks.append(required))
    monkeypatch.setattr(guest_operation, "_new_lock", lambda: holder)
    return holder, checks


def test_operation_checks_before_yield_each_effect_and_exit(
    operation_seams: tuple[FakeLock, list[bool]],
) -> None:
    holder, checks = operation_seams
    with guest_operation.operation() as custody:
        assert holder.held
        assert not hasattr(custody, "ready")
        custody.assert_owned()
    assert checks == [False, True, True, True]
    assert (holder.enters, holder.exits, holder.assertions) == (1, 1, 3)
    assert not holder.held
    with pytest.raises(RuntimeError, match="custody lost"):
        custody.assert_owned()


def test_same_guest_selection_cannot_enter_twice(
    operation_seams: tuple[FakeLock, list[bool]],
) -> None:
    holder, _checks = operation_seams
    with guest_operation.operation():
        with pytest.raises(RuntimeError, match="contention"):
            with guest_operation.operation():
                pytest.fail("contending operation entered")
        assert holder.held
    assert holder.exits == 1


@pytest.mark.parametrize("failure", ["acquire", "assert", "body", "changed", "read"])
def test_failed_operation_never_replays_or_retains_its_lock(
    operation_seams: tuple[FakeLock, list[bool]], monkeypatch: Any, failure: str,
) -> None:
    holder, _checks = operation_seams
    holder.fail_acquire = failure == "acquire"
    holder.fail_assert = failure == "assert"
    calls = 0

    def observe() -> guest_operation.GuestState:
        nonlocal calls
        calls += 1
        if calls > 1 and failure == "read":
            raise OSError("synthetic unavailable")
        return replace(STATE, inode=999) if calls > 1 and failure == "changed" else STATE

    monkeypatch.setattr(guest_operation, "observe_state", observe)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        with guest_operation.operation():
            if failure == "body":
                raise RuntimeError("synthetic body failure")
            pytest.fail("failed prerequisite entered body")
    assert not holder.held
    assert holder.enters == 1
    assert holder.exits == (0 if failure == "acquire" else 1)


def test_state_replacement_at_exit_refuses_and_releases(
    operation_seams: tuple[FakeLock, list[bool]], monkeypatch: Any,
) -> None:
    holder, _checks = operation_seams
    with pytest.raises(ValueError, match="state changed"):
        with guest_operation.operation():
            monkeypatch.setattr(guest_operation, "observe_state", lambda: replace(STATE, device=999))
    assert holder.exits == 1 and not holder.held


@pytest.fixture
def state_probe(tmp_path: Path, monkeypatch: Any) -> tuple[list[int], Path]:
    if os.name != "posix":
        pytest.skip("directory descriptor fixtures require POSIX")
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    opened: list[int] = []
    original_open, original_fstat = os.open, os.fstat
    original_lstat = Path.lstat

    def synthetic_info(info: os.stat_result) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=info.st_mode, st_uid=1000, st_gid=1000, st_dev=info.st_dev, st_ino=info.st_ino,
        )

    def open_root(selected: Path, flags: int) -> int:
        assert selected == path
        assert flags == os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = original_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def fstat(descriptor: int) -> SimpleNamespace:
        return synthetic_info(original_fstat(descriptor))

    def lstat(selected: Path) -> object:
        info = original_lstat(selected)
        return synthetic_info(info) if selected == path else info

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(guest_operation, "STATE", path)
    monkeypatch.setattr(guest_operation, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(guest_operation, "read_mount_table", lambda: TABLE)
    monkeypatch.setattr(guest_operation, "os", SimpleNamespace(
        open=open_root, fstat=fstat, fstatvfs=lambda _fd: SimpleNamespace(f_flag=0),
        close=os.close, major=lambda _dev: 8, minor=lambda _dev: 1,
        geteuid=lambda: 1000, getegid=lambda: 1000,
        O_RDONLY=os.O_RDONLY, O_DIRECTORY=os.O_DIRECTORY, O_NOFOLLOW=os.O_NOFOLLOW,
        ST_RDONLY=os.ST_RDONLY, path=os.path,
    ))
    return opened, path


def test_observe_existing_state_closes_fixed_descriptor(
    state_probe: tuple[list[int], Path],
) -> None:
    opened, path = state_probe
    result = guest_operation.observe_state()
    assert result.inode == path.stat().st_ino and result.mount == STATE.mount
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_late_directory_posture_change_refuses(
    state_probe: tuple[list[int], Path], monkeypatch: Any,
) -> None:
    opened, path = state_probe
    calls = 0

    def table() -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.chmod(0o755)
        return TABLE

    monkeypatch.setattr(guest_operation, "read_mount_table", table)
    with pytest.raises(ValueError, match="directory changed"):
        guest_operation.observe_state()
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_hidden_state_record_refuses_before_lock_creation(
    state_probe: tuple[list[int], Path], monkeypatch: Any,
) -> None:
    opened, path = state_probe
    table = TABLE + "3 1 8:1 /other /lab rw - ext4 /dev/synthetic rw\n"
    monkeypatch.setattr(guest_operation, "read_mount_table", lambda: table)
    monkeypatch.setattr(
        guest_operation, "_new_lock", lambda: pytest.fail("hidden state created a lock"),
    )
    with pytest.raises(ValueError, match="state mount"):
        with guest_operation.operation():
            pytest.fail("hidden state entered an operation")
    assert list(path.iterdir()) == []
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("cause", ["uid", "gid", "platform", "mode", "readonly", "table", "expiry", "stat"])
def test_guest_observation_failure_closes_descriptors_without_initialization(
    state_probe: tuple[list[int], Path], monkeypatch: Any, cause: str,
) -> None:
    opened, path = state_probe
    if cause in {"uid", "gid"}:
        monkeypatch.setattr(guest_operation.os, "geteuid" if cause == "uid" else "getegid", lambda: 0)
    elif cause == "platform":
        monkeypatch.setattr(guest_operation.sys, "platform", "win32")
    elif cause == "mode":
        path.chmod(0o755)
    elif cause == "readonly":
        monkeypatch.setattr(guest_operation.os, "fstatvfs", lambda _fd: SimpleNamespace(f_flag=os.ST_RDONLY))
    elif cause == "table":
        tables = iter([TABLE, TABLE + "changed"])
        monkeypatch.setattr(guest_operation, "read_mount_table", lambda: next(tables))
    elif cause == "expiry":
        clock = iter([0, guest_operation.MAX_SECONDS])
        monkeypatch.setattr(guest_operation, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    else:
        def fail(_fd: int) -> object:
            raise OSError("synthetic stat failure")
        monkeypatch.setattr(guest_operation.os, "fstat", fail)
    with pytest.raises((ValueError, OSError)):
        guest_operation.observe_state()
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert list(path.iterdir()) == []


def test_missing_or_linked_state_is_never_created(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(guest_operation, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(guest_operation, "os", SimpleNamespace(geteuid=lambda: 1000, getegid=lambda: 1000))
    monkeypatch.setattr(guest_operation, "read_mount_table", lambda: TABLE)
    missing = tmp_path / "missing"
    monkeypatch.setattr(guest_operation, "STATE", missing)
    with pytest.raises(FileNotFoundError):
        guest_operation.observe_state()
    assert not missing.exists()
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(guest_operation, "STATE", link)
    with pytest.raises(ValueError, match="linked"):
        guest_operation.observe_state()


@pytest.mark.parametrize("field,value", [
    ("st_mode", 0o100644), ("st_mode", 0o120600), ("st_uid", 0),
    ("st_gid", 0), ("st_dev", 999), ("st_nlink", 2),
])
def test_existing_lock_posture_refuses(field: str, value: int, monkeypatch: Any) -> None:
    fields = {"st_mode": 0o100600, "st_uid": 1000, "st_gid": 1000, "st_dev": 123, "st_nlink": 1}
    fields[field] = value
    monkeypatch.setattr(guest_operation, "LOCK", SimpleNamespace(lstat=lambda: SimpleNamespace(**fields)))
    with pytest.raises(ValueError, match="lock identity"):
        guest_operation._check_lock(STATE, required=False)


def test_missing_lock_is_only_allowed_before_acquisition(monkeypatch: Any) -> None:
    def missing() -> object:
        raise FileNotFoundError
    monkeypatch.setattr(guest_operation, "LOCK", SimpleNamespace(lstat=missing))
    guest_operation._check_lock(STATE, required=False)
    with pytest.raises(ValueError, match="disappeared"):
        guest_operation._check_lock(STATE, required=True)


def test_source_adapter_reuses_shared_lock_at_fixed_key(monkeypatch: Any) -> None:
    selected: list[Path] = []
    sentinel = object()
    monkeypatch.setitem(sys.modules, "single_instance_lease", SimpleNamespace(
        OperationLock=lambda path: selected.append(path) or sentinel,
    ))
    assert guest_operation._new_lock() is sentinel
    assert selected == [Path("/lab/state/reproducer-driver.lock")]
    assert str(LAB.parents[1] / "libs" / "single-instance-lease" / "src") in sys.path
