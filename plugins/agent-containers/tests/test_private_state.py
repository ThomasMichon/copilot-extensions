"""Backing-filesystem-aware private state permission tests."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agent_containers import private_state


def test_mountinfo_detection_prefers_longest_backing_mount():
    text = "\n".join(
        [
            "1 0 0:1 / / rw - ext4 /dev/root rw",
            "2 1 0:2 / /mnt/c rw - 9p drvfs rw",
        ]
    )

    assert private_state._filesystem_type_from_mountinfo(
        Path("/mnt/c/shared/state"),
        text,
    ) == "9p"
    assert private_state._filesystem_type_from_mountinfo(
        Path("/home/user/state"),
        text,
    ) == "ext4"


def test_real_posix_mode_failure_is_fatal(monkeypatch, tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    monkeypatch.setattr(private_state, "filesystem_type", lambda _path: "ext4")
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("denied")
        ),
    )

    with pytest.raises(RuntimeError, match="Could not enforce mode"):
        private_state.enforce_mode(path, 0o700)


@pytest.mark.parametrize("fs_type", ["9p", "drvfs", "cifs", "fuse.sshfs"])
def test_acl_backed_shared_state_chmod_is_best_effort(
    monkeypatch,
    tmp_path,
    fs_type,
):
    path = tmp_path / "state"
    path.mkdir()
    monkeypatch.setattr(
        private_state,
        "filesystem_type",
        lambda _path: fs_type,
    )
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("ACL-backed")
        ),
    )

    private_state.enforce_mode(path, 0o700)


def test_atomic_json_is_owner_only_and_directory_fsynced(monkeypatch, tmp_path):
    path = tmp_path / "state" / "record.json"
    calls = []
    monkeypatch.setattr(
        private_state,
        "fsync_directory",
        lambda directory: calls.append(directory),
    )
    previous = os.umask(0)
    try:
        private_state.atomic_write_json(path, {"value": 1})
    finally:
        os.umask(previous)

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}
    if os.name != "nt":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert calls == [path.parent]
    assert not list(path.parent.glob(".*.tmp"))


def test_drvfs_atomic_json_relies_on_acl_when_chmod_is_unrepresentable(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "shared" / "record.json"
    monkeypatch.setattr(
        private_state,
        "filesystem_type",
        lambda _path: "9p",
    )
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("DrvFS ACL")
        ),
    )

    private_state.atomic_write_json(path, {"value": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}


def test_exclusive_json_crash_never_exposes_partial_destination(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "state" / "pin.json"
    monkeypatch.setattr(
        private_state.json,
        "dump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated crash")
        ),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        private_state.write_json_exclusive(path, {"token": "value"})

    assert not path.exists()
    assert not list(path.parent.glob(".*.tmp"))


def test_exclusive_json_is_atomic_no_clobber_and_directory_fsynced(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "state" / "pin.json"
    calls = []
    monkeypatch.setattr(
        private_state,
        "fsync_directory",
        lambda directory: calls.append(directory),
    )
    private_state.write_json_exclusive(path, {"token": "first"})

    with pytest.raises(OSError):
        private_state.write_json_exclusive(path, {"token": "second"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "first"}
    assert calls == [path.parent]
