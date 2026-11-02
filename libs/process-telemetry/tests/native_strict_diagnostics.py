"""Explicitly selected Linux local-file checks, not persistent-volume qualification."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from process_telemetry import append, diagnostic_logs as logs
from single_instance_lease import OperationLock

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux strict sync protocol")
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
EVENT = {"ts": NOW.isoformat(), "operation_id": "synthetic", "message": "\u96ea\u00e9"}


def store(directory: Path) -> logs.DiagnosticLog:
    return logs.DiagnosticLog(
        "synthetic", directory, service="synthetic", custody=OperationLock,
        initial_maintenance=False, clock=lambda: NOW,
    )


def test_actual_local_append_and_caller_readback(tmp_path: Path) -> None:
    writer = store(tmp_path)
    writer.write_event_strict(EVENT)
    assert json.loads(writer.path.read_bytes()) == EVENT
    writer.write_event_strict(dict(EVENT, operation_id="second"))
    assert [json.loads(line)["operation_id"] for line in writer.path.read_bytes().splitlines()] == [
        "synthetic", "second",
    ]
    assert writer.path.stat().st_mode & 0o777 == 0o600


def test_actual_interrupted_tail_rolls_under_existing_operation_owner(tmp_path: Path) -> None:
    writer = store(tmp_path)
    writer.path.write_bytes(b'{"interrupted":')
    writer.write_event_strict(EVENT)
    assert json.loads(writer.path.read_bytes()) == EVENT
    archives = [path for path in writer._files() if path != writer.path]
    assert len(archives) == 1 and archives[0].read_bytes() == b'{"interrupted":'


def test_actual_lock_contention_prevents_record_and_effect(tmp_path: Path) -> None:
    writer = store(tmp_path)
    called = []
    with OperationLock(tmp_path / ".synthetic.lock"):
        with pytest.raises(RuntimeError):
            writer.write_event_strict(EVENT)
            called.append("backend")
    assert not called and not writer.path.exists()


@pytest.mark.parametrize("which", ["file", "directory"])
def test_actual_append_with_sync_failure_is_unknown_not_permission_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str,
) -> None:
    writer = store(tmp_path)
    sync = os.fsync
    count = 0

    def fail_sync(fd: int) -> None:
        nonlocal count
        count += 1
        if count == (1 if which == "file" else 2):
            raise OSError("synthetic sync failure")
        sync(fd)

    monkeypatch.setattr(append.os, "fsync", fail_sync)
    called = []
    with pytest.raises(OSError, match="synthetic sync"):
        writer.write_event_strict(EVENT)
        called.append("backend")
    assert not called
    assert writer.path.read_bytes().splitlines() == [writer.encoder(EVENT).rstrip(b"\n")]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_actual_nonordinary_target_refuses_without_modifying_other_file(
    tmp_path: Path, kind: str,
) -> None:
    other = tmp_path / "other.jsonl"
    other.write_bytes(b"original")
    target = tmp_path / "target.jsonl"
    if kind == "symlink":
        target.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, target)
    else:
        os.mkfifo(target)
    with pytest.raises(OSError):
        append.append_bytes_strict(target, b"{}\n")
    assert other.read_bytes() == b"original"


def test_actual_missing_directory_is_not_provisioned(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    writer = store(missing)
    with pytest.raises(FileNotFoundError):
        writer.write_event_strict(EVENT)
    assert not missing.exists()


def test_actual_ordinary_append_and_handler_still_do_not_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(append.os, "fsync", lambda fd: pytest.fail("ordinary sync"))
    path = tmp_path / "legacy.jsonl"
    append.append_bytes(path, b'{"legacy":true}\n')
    assert json.loads(path.read_bytes()) == {"legacy": True}
    writer = store(tmp_path / "ordinary-created-parent")
    writer.write_event(EVENT)
    assert json.loads(writer.path.read_bytes()) == EVENT
