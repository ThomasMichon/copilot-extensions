"""Explicit actual-owner SQLite/JSONL lane; synthetic guest custody, no Docker."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "plugins" / "agent-dispatch" / "src"))
sys.path.insert(0, str(ROOT / "plugins" / "agent-bridge" / "src"))

import initial_case


@pytest.fixture
def owners(tmp_path: Path, monkeypatch: Any) -> Any:
    from agent_bridge.acquisition_journal import AcquisitionJournal
    from agent_bridge.db import Database
    from agent_dispatch.queue import TaskQueue
    from process_telemetry.diagnostic_logs import DiagnosticLog
    from single_instance_lease import OperationLock

    dispatch_path = tmp_path / "dispatch" / "queue.db"
    dispatch_path.parent.mkdir(mode=0o700)
    queue = TaskQueue(dispatch_path)
    bridge_path = tmp_path / "bridge" / "bridge.db"
    writable = Database(bridge_path)
    writable.close()
    database = Database(bridge_path, read_only=True)
    journal = AcquisitionJournal(database)
    evidence = tmp_path / "diagnostics"
    evidence.mkdir(mode=0o700)
    monkeypatch.setattr(initial_case, "DIAGNOSTICS", evidence)
    log = DiagnosticLog(
        "reproducer", evidence, service="reproducer-lab", custody=OperationLock,
        create_parent=False, initial_maintenance=False,
    )
    # This lane exercises actual storage owners, not Linux 1000:1000/mount admission.
    monkeypatch.setattr(
        initial_case, "_journal_paths",
        lambda *_args: (initial_case.FileIdentity(1, 2),),
    )
    try:
        with OperationLock(tmp_path / "driver.lock") as holder:
            yield SimpleNamespace(
                queue=queue, journal=journal, log=log,
                custody=SimpleNamespace(assert_owned=holder.assert_owned),
            )
    finally:
        log.close()
        database.close()


def submit(owners: Any) -> Any:
    return initial_case.submit_initial_task(
        owners.custody, owners.queue, owners.journal, owners.log,
    )


def test_actual_queue_create_observes_the_synced_entry_first(owners: Any, monkeypatch: Any) -> None:
    from process_telemetry import append

    original = owners.queue.create_outcome
    entries: list[dict[str, object]] = []
    syncs: list[str] = []
    fsync = os.fsync

    def observe_sync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        fsync(descriptor)
        if os.path.samestat(info, owners.log.directory.stat()):
            syncs.append("directory")
        elif owners.log.path.exists() and os.path.samestat(info, owners.log.path.stat()):
            syncs.append("file")

    monkeypatch.setattr(append.os, "fsync", observe_sync)

    def create(title: str, **kwargs: object) -> Any:
        assert syncs == ["file", "directory"]
        rows = owners.log.path.read_bytes().splitlines()
        assert len(rows) == 1
        entry = json.loads(rows[0].decode("utf-8"))
        assert entry["event"] == "initial_task_create_entry"
        assert entry["case"] == initial_case.CASE and "prompt" not in entry
        entries.append(entry)
        return original(title, **kwargs)

    monkeypatch.setattr(owners.queue, "create_outcome", create)
    outcome = submit(owners)
    assert outcome.disposition == "created" and len(entries) == 1
    task = owners.queue.get(outcome.task.id)
    assert task is not None and task.prompt.endswith("offline-case-complete.")
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert len(owners.queue.list(limit=2)) == 1


@pytest.mark.parametrize("failure", ["file", "directory"])
def test_actual_sync_failure_never_creates_a_task(
    owners: Any, monkeypatch: Any, failure: str,
) -> None:
    import stat
    from process_telemetry import append

    fsync = os.fsync

    def fail(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory == (failure == "directory"):
            raise OSError("synthetic sync failure")
        fsync(descriptor)

    monkeypatch.setattr(append.os, "fsync", fail)
    with pytest.raises(OSError, match="sync failure"):
        submit(owners)
    assert owners.queue.list(limit=1) == []


def test_actual_lost_create_reply_uses_existing_task_history(owners: Any, monkeypatch: Any) -> None:
    original = owners.queue.create_outcome
    calls = 0

    def lose(title: str, **kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        original(title, **kwargs)
        raise OSError("synthetic lost create reply")

    monkeypatch.setattr(owners.queue, "create_outcome", lose)
    with pytest.raises(OSError, match="lost create reply"):
        submit(owners)
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert calls == 1 and len(owners.queue.list(limit=2)) == 1
