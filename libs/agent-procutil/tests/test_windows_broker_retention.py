"""Installer publication retains both action runtimes until recovery settles."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
from unittest.mock import Mock

import pytest

if os.name != "nt":
    pytest.skip("Windows broker installation paths", allow_module_level=True)

from agent_procutil import windows_task_broker as task
from agent_procutil import DaemonSpawnError

LIBRARY = Path(__file__).resolve().parents[2] / "versioned-runtime"
spec = importlib.util.spec_from_file_location("broker_reference_runtime", LIBRARY / "versioned_runtime.py")
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def case(tmp_path, monkeypatch):
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    root = tmp_path / "installation"
    registrations = []
    for generation in ("old", "new"):
        slot = root / "versions" / generation
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir(parents=True)
        shutil.copyfile(LIBRARY / "versioned_runtime_leases.py", bootstrap / "versioned_runtime_leases.py")
        registrations.append(task.Registration(
            installation="fixture/component", root=str(root),
            pythonw=str(slot / "Scripts" / "pythonw.exe"),
            entrypoint=str(slot / "Lib" / "site-packages" / "agent_procutil" / "windows_broker_entry.py"),
            principal="S-1-5-21-123",
        ))
    (root / "versions" / "spare").mkdir()
    return root, *registrations


def references(root, config):
    return runtime._leases.read_runtime_references(root).get(config.owner, [])


def test_update_retains_both_generations_before_external_mutation(tmp_path, monkeypatch):
    root, old, new = case(tmp_path, monkeypatch)
    task._publish(old)

    def operation(action, config, deadline, **kwargs):
        if action == "inspect":
            return {"installed": True, "accepting": True}
        assert action == "update"
        assert references(root, new) == ["new", "old"]
        assert runtime.gc(root) == ["spare"]
        return {}

    monkeypatch.setattr(task, "task_operation", operation)
    task.reconcile(new, previous=old)
    assert references(root, new) == ["new"]
    assert task._published(new) == new
    assert not new.pending_path.exists()
    assert runtime.gc(root) == ["old"]


@pytest.mark.parametrize("metadata_committed", [False, True])
def test_interrupted_recovery_keeps_both_until_task_and_metadata_restore(
    tmp_path, monkeypatch, metadata_committed,
):
    root, old, new = case(tmp_path, monkeypatch)
    task._retain(new, [old, new])
    task._publish(new if metadata_committed else old)
    task._write_metadata(new.pending_path, {
        "schema": task._SCHEMA + ".publication", "version": 1,
        "desired": new.frame(), "previous": old.frame(),
        "published": old.frame(), "accepting": True,
    })
    assert runtime.gc(root) == ["spare"]
    monkeypatch.setattr(task, "task_operation", Mock(side_effect=OSError("unsettled recovery")))
    with pytest.raises(OSError, match="unsettled"):
        task.recover(new)
    assert references(root, new) == ["new", "old"]
    assert new.pending_path.exists()
    monkeypatch.setattr(task, "task_operation", Mock(return_value={"recovered": True}))
    assert task.recover(new)
    assert task._published(new) == old
    assert references(root, new) == ["old"]
    assert runtime.gc(root) == ["new"]


def test_uninstall_releases_only_after_owned_external_removal(tmp_path, monkeypatch):
    root, old, _ = case(tmp_path, monkeypatch)
    task._publish(old)
    task._retain(old, [old])
    monkeypatch.setattr(task, "task_operation", Mock(side_effect=OSError("live transactions")))
    with pytest.raises(OSError, match="live transactions"):
        task.uninstall(old)
    assert references(root, old) == ["old"]
    assert old.config_path.exists()
    monkeypatch.setattr(task, "task_operation", Mock(side_effect=[
        {"installed": True, "accepting": True},
        {"phase": "drained", "installed": False, "child_control": "retained-by-consumer"},
    ]))
    task.uninstall(old)
    assert not old.config_path.exists()
    assert references(root, old) == []


def test_uninstall_retry_settles_metadata_after_external_removal(tmp_path, monkeypatch):
    root, old, _ = case(tmp_path, monkeypatch)
    task._publish(old)
    task._retain(old, [old])
    operation = Mock(return_value={"installed": False, "accepting": False})
    monkeypatch.setattr(task, "task_operation", operation)
    task.uninstall(old)
    assert [call.args[0] for call in operation.call_args_list] == ["inspect"]
    assert not old.config_path.exists()
    assert references(root, old) == []


@pytest.mark.parametrize("response", [
    {}, {"phase": "drained", "installed": 0, "child_control": "retained-by-consumer"},
])
def test_unconfirmed_removal_preserves_registration_and_references(tmp_path, monkeypatch, response):
    root, old, _ = case(tmp_path, monkeypatch)
    task._publish(old)
    task._retain(old, [old])
    monkeypatch.setattr(task, "task_operation", Mock(side_effect=[
        {"installed": True, "accepting": True}, response,
    ]))
    with pytest.raises(DaemonSpawnError, match="removal unconfirmed"):
        task.uninstall(old)
    assert old.config_path.exists()
    assert references(root, old) == ["old"]


def test_missing_canonical_retention_is_not_process_lifetime_fallback(tmp_path, monkeypatch):
    root, old, new = case(tmp_path, monkeypatch)
    helper = root / "versions" / "new" / ".runtime-bootstrap" / "versioned_runtime_leases.py"
    helper.unlink()
    operation = Mock(return_value={"installed": False, "accepting": False})
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(FileNotFoundError):
        task.reconcile(new)
    assert [call.args[0] for call in operation.call_args_list] == ["inspect"]
    assert not new.pending_path.exists()
    assert references(root, old) == []
