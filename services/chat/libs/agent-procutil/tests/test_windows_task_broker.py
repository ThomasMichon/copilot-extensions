"""Adoption never becomes a cold installer on the ordinary spawn path."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from zdd.private_protocol import PeerIdentity
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

from agent_procutil import DaemonSpawnError
from agent_procutil import windows_broker_installation as installation
from agent_procutil import windows_task_broker as task
from agent_procutil import windows_broker_protocol as protocol
from agent_procutil.owned_process import run_caller_owned


@pytest.fixture(autouse=True)
def isolated_pending_custody(monkeypatch):
    monkeypatch.setattr(protocol, "_pending", [])
    monkeypatch.setattr(protocol, "_pending_closes", [])


def registration(**overrides):
    return task.Registration(**{
        "installation": "test-marketplace/plugin",
        "root": r"C:\runtime\broker",
        "pythonw": r"C:\managed-python\pythonw.exe",
        "entrypoint": r"C:\runtime\broker\immutable\agent_procutil\windows_broker_entry.py",
        "principal": "S-1-5-21-123",
        **overrides,
    })


def fixture_registration(tmp_path, **overrides):
    if os.name != "nt":
        pytest.skip("Windows installation metadata paths")
    return registration(**{
        "root": str(tmp_path),
        "entrypoint": str(tmp_path / "immutable" / "windows_broker_entry.py"),
        **overrides,
    })


def test_registration_names_are_installation_and_user_scoped():
    config = registration()
    other_installation = registration(installation="other/plugin")
    assert config.task()["task_name"] != other_installation.task()["task_name"]
    assert config.task()["task_name"] != registration(principal="S-1-5-21-456").task()["task_name"]
    assert len(config.key) == 64
    assert "$(Arg0)" in config.task()["arguments"]
    assert "$(Arg1)" in config.task()["arguments"]
    assert "$(Arg2)" in config.task()["arguments"]
    assert set(config.frame()) == {
        "schema", "version", "installation", "root", "pythonw", "entrypoint", "principal",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"installation": ""}, {"installation": "unsafe\nidentity"},
        {"principal": "not-a-sid"}, {"principal": False},
        {"root": r"C:relative"}, {"root": r"\\server\shared"},
        {"pythonw": r'C:\unsafe"\pythonw.exe'},
        {"pythonw": r"C:\runtime\python.exe"},
        {"entrypoint": r"C:\runtime\other.py"},
    ],
)
def test_unsafe_registration_is_refused(overrides):
    with pytest.raises(DaemonSpawnError):
        registration(**overrides)


def test_contained_spawn_does_not_read_adoption_or_activate(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    load, operation = Mock(), Mock()
    monkeypatch.setattr(installation, "load_current", load)
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="contained"):
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    load.assert_not_called()
    operation.assert_not_called()


@pytest.mark.parametrize("missing", [task.CONFIG_ENV, task.OWNER_ENV])
def test_missing_adoption_never_installs(monkeypatch, missing):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv(task.CONFIG_ENV, r"C:\state\windows-launch-broker.json")
    monkeypatch.setenv(task.OWNER_ENV, "test/plugin")
    monkeypatch.delenv(missing)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(
        installation, "load_current",
        Mock(side_effect=DaemonSpawnError("adoption required")),
    )
    with pytest.raises(DaemonSpawnError, match="adoption required") as caught:
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    assert caught.value.process_created is False
    assert caught.value.admission_sent is False
    operation.assert_not_called()


def test_source_import_cannot_select_an_adoption_with_ambient_pointers(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv(task.CONFIG_ENV, r"C:\state\windows-launch-broker.json")
    monkeypatch.setenv(task.OWNER_ENV, "test/plugin")
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="installed broker context required"):
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    operation.assert_not_called()


def test_installer_publish_failure_rolls_back_only_exact_registration(monkeypatch, tmp_path):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    operation = Mock(side_effect=[
        {"installed": False, "accepting": False}, {}, {"recovered": True},
    ])
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture publication failure")))
    with pytest.raises(OSError, match="publication"):
        task.reconcile(config)
    assert [call.args[0] for call in operation.call_args_list] == ["inspect", "install", "recover"]
    assert all(call.args[1] == config for call in operation.call_args_list)
    assert not config.pending_path.exists()


def test_healthy_repeat_keeps_registration_and_accepted_finite_work_untouched(monkeypatch, tmp_path):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(config)
    operation = Mock(return_value={"installed": True, "accepting": True})
    retained = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_retain", retained)
    task.reconcile(config, previous=config)
    assert [call.args[0] for call in operation.call_args_list] == ["inspect"]
    retained.assert_called_once_with(config, [config])
    assert not config.pending_path.exists()
    assert task._published(config) == config


def test_stopped_same_generation_is_explicitly_readopted_not_treated_as_healthy(monkeypatch, tmp_path):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(config)
    operation = Mock(side_effect=[{"installed": True, "accepting": False}, {}])
    monkeypatch.setattr(task, "task_operation", operation)
    task.reconcile(config, previous=config)
    assert [call.args[0] for call in operation.call_args_list] == ["inspect", "update"]
    assert operation.call_args_list[1].kwargs == {"previous": config.task()}
    assert not config.pending_path.exists()


def test_installer_update_publish_failure_restores_previous_generation(monkeypatch, tmp_path):
    old = fixture_registration(tmp_path)
    new = fixture_registration(
        tmp_path, entrypoint=str(tmp_path / "next" / "windows_broker_entry.py"),
    )
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(old)
    operation = Mock(side_effect=[
        {"installed": True, "accepting": True}, {}, {"recovered": True},
    ])
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture failure")))
    with pytest.raises(OSError, match="fixture"):
        task.reconcile(new, previous=old)
    inspect, first, rollback = operation.call_args_list
    assert inspect.args[:2] == ("inspect", old)
    assert first.args[:2] == ("update", new)
    assert first.kwargs == {"previous": old.task()}
    assert rollback.args[:2] == ("recover", new)
    assert rollback.kwargs == {"previous": old.task(), "accepting": True}
    assert not new.pending_path.exists()


def pending_record(desired, previous, *, published=None, accepting=False):
    task._write_metadata(desired.pending_path, {
        "schema": task._SCHEMA + ".publication", "version": 1,
        "desired": desired.frame(),
        "previous": None if previous is None else previous.frame(),
        "published": None if published is None else published.frame(),
        "accepting": accepting,
    })


@pytest.mark.parametrize("metadata_committed", [False, True])
@pytest.mark.parametrize("accepting", [False, True])
def test_interrupted_update_restores_original_task_and_metadata(
    monkeypatch, tmp_path, metadata_committed, accepting,
):
    old = fixture_registration(tmp_path)
    new = fixture_registration(
        tmp_path, entrypoint=str(tmp_path / "next" / "windows_broker_entry.py"),
    )
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(new if metadata_committed else old)
    pending_record(new, old, published=old, accepting=accepting)
    operation = Mock(return_value={"recovered": True})
    monkeypatch.setattr(task, "task_operation", operation)
    assert task.recover(new)
    assert task._published(new) == old
    assert not new.pending_path.exists()
    assert operation.call_args.args[:2] == ("recover", new)
    assert operation.call_args.kwargs == {"previous": old.task(), "accepting": accepting}
    assert not task.recover(new)
    assert operation.call_count == 1


@pytest.mark.parametrize("metadata_committed", [False, True])
def test_interrupted_first_adoption_restores_absence(monkeypatch, tmp_path, metadata_committed):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    if metadata_committed:
        task._publish(config)
    pending_record(config, None)
    operation = Mock(return_value={"recovered": True})
    monkeypatch.setattr(task, "task_operation", operation)
    assert task.recover(config)
    assert not config.config_path.exists()
    assert not config.pending_path.exists()
    assert operation.call_args.kwargs == {"previous": None, "accepting": False}


def test_stopped_repeat_rollback_preserves_preexisting_task_and_disabled_state(monkeypatch, tmp_path):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(config)
    operation = Mock(side_effect=[
        {"installed": True, "accepting": False}, {}, {"recovered": True},
    ])
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture publication failure")))
    with pytest.raises(OSError, match="fixture"):
        task.reconcile(config, previous=config)
    assert operation.call_args.kwargs == {"previous": config.task(), "accepting": False}
    assert task._published(config) == config
    assert not config.pending_path.exists()


def test_interrupted_recovery_retains_original_intent_and_can_repeat(monkeypatch, tmp_path):
    old = fixture_registration(tmp_path)
    new = fixture_registration(
        tmp_path, entrypoint=str(tmp_path / "next" / "windows_broker_entry.py"),
    )
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "_qualify", Mock())
    task._publish(new)
    pending_record(new, old, published=old, accepting=True)
    retained = new.pending_path.read_bytes()
    operation = Mock(return_value={"recovered": True})
    monkeypatch.setattr(task, "task_operation", operation)
    publish = task._publish
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture recovery failure")))
    with pytest.raises(OSError, match="recovery failure"):
        task.recover(new)
    assert new.pending_path.read_bytes() == retained
    assert task._published(new) == new
    monkeypatch.setattr(task, "_publish", publish)
    assert task.recover(new)
    assert task._published(new) == old
    assert operation.call_count == 2


@pytest.mark.parametrize("response", [
    {}, {"recovered": False}, {"recovered": 1}, {"recovered": True, "extra": 1},
])
def test_unconfirmed_native_recovery_preserves_metadata_and_intent(
    monkeypatch, tmp_path, response,
):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    task._publish(config)
    pending_record(config, None)
    monkeypatch.setattr(task, "task_operation", Mock(return_value=response))
    with pytest.raises(DaemonSpawnError, match="recovery unconfirmed") as caught:
        task.recover(config)
    assert not caught.value.cleanup_confirmed
    assert config.pending_path.exists()
    assert config.config_path.exists()


@pytest.mark.parametrize("failure", ["foreign-record", "foreign-publication", "invalid-record"])
def test_unowned_recovery_state_is_refused_before_native_mutation(monkeypatch, tmp_path, failure):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    foreign = fixture_registration(tmp_path, installation="foreign/plugin")
    pending_record(foreign if failure == "foreign-record" else config, None)
    if failure == "foreign-publication":
        task._publish(foreign)
    elif failure == "invalid-record":
        config.pending_path.write_text("{}", encoding="utf-8")
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError):
        task.recover(config)
    operation.assert_not_called()
    assert config.pending_path.exists()


def test_pending_publication_blocks_spawn_without_native_mutation(monkeypatch, tmp_path):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    task._publish(config)
    pending_record(config, None)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="recovery required"):
        task.load(config.config_path, config.installation)
    operation.assert_not_called()


@pytest.mark.parametrize("changed", ["record", "publication"])
def test_recovery_revalidates_state_after_native_effects(monkeypatch, tmp_path, changed):
    config = fixture_registration(tmp_path)
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    task._publish(config)
    pending_record(config, None)

    def native_effect(*args, **kwargs):
        if changed == "record":
            config.pending_path.write_text("{}", encoding="utf-8")
        else:
            task._publish(fixture_registration(tmp_path, installation="foreign/plugin"))
        return {"recovered": True}

    monkeypatch.setattr(task, "task_operation", native_effect)
    with pytest.raises(DaemonSpawnError):
        task.recover(config)
    assert config.pending_path.exists()
    assert config.config_path.exists()


def test_installer_refuses_foreign_generation_before_native_operation(monkeypatch):
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    qualify = Mock()
    monkeypatch.setattr(task, "_qualify", qualify)
    with pytest.raises(DaemonSpawnError, match="transition"):
        task.reconcile(registration(), previous=registration(installation="foreign/plugin"))
    operation.assert_not_called()
    qualify.assert_not_called()


def test_failed_runtime_health_preserves_registration_and_metadata(monkeypatch):
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    operation, publish = Mock(), Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", publish)
    monkeypatch.setattr(task, "_qualify", Mock(side_effect=RuntimeError("fixture health failure")))
    with pytest.raises(RuntimeError, match="health failure"):
        task.reconcile(registration(), previous=registration())
    operation.assert_not_called()
    publish.assert_not_called()


@pytest.mark.parametrize("returncode,stdout,stderr,ready", [
    (0, "agent-procutil.windows-launch-broker.runtime-ready.v1\n", "", True),
    (1, "agent-procutil.windows-launch-broker.runtime-ready.v1\n", "", False),
    (0, "ready", "", False),
    (0, "agent-procutil.windows-launch-broker.runtime-ready.v1\n", "private-value", False),
])
def test_runtime_health_uses_exact_isolated_action_and_strict_result(
    monkeypatch, returncode, stdout, stderr, ready,
):
    config = registration()
    runner = Mock(return_value=subprocess.CompletedProcess(
        ["fixture"], returncode, stdout, stderr,
    ))
    monkeypatch.setattr(task, "run_caller_owned", runner)
    if ready:
        task._qualify(config)
    else:
        with pytest.raises(DaemonSpawnError, match="health qualification") as caught:
            task._qualify(config)
        assert "private-value" not in str(caught.value)
    assert runner.call_args.args == (
        [config.pythonw, "-I", config.entrypoint, "--check-runtime"],
    )
    assert runner.call_args.kwargs == {
        "timeout": 15, "max_stdout_bytes": 4096, "max_stderr_bytes": 4096,
    }


@pytest.mark.skipif(os.name != "nt", reason="Native isolated pythonw runtime health")
def test_exact_copied_action_runtime_qualifies_without_scheduler_or_ambient_imports(tmp_path):
    import zdd

    packages = tmp_path / "immutable" / "site-packages"
    shutil.copytree(Path(task.__file__).parent, packages / "agent_procutil")
    shutil.copytree(Path(zdd.__file__).parent, packages / "zdd")
    config = registration(
        root=str(tmp_path),
        pythonw=str(Path(sys.base_prefix) / "pythonw.exe"),
        entrypoint=str(packages / "agent_procutil" / "windows_broker_entry.py"),
    )
    task._qualify(config)
    assert not config.config_path.exists()
    shutil.rmtree(packages / "zdd")
    with pytest.raises(DaemonSpawnError, match="health qualification"):
        task._qualify(config)


def test_malformed_adoption_metadata_never_reaches_scheduler(tmp_path):
    path = tmp_path / "windows-launch-broker.json"
    path.write_text(json.dumps({"schema": "wrong", "version": 1}), encoding="utf-8")
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        task.load(path, "test/plugin")
    path.write_bytes(b"x" * 16385)
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        task.load(path, "test/plugin")


def test_rendezvous_is_bounded_local_and_unique():
    config = registration()
    address = task.pipe_path(config, "a" * 64)
    assert address.startswith("\\\\.\\pipe\\")
    assert len(address) <= 256
    assert address != task.pipe_path(config, "b" * 64)
    assert address != task.pipe_path(registration(installation="other/plugin"), "a" * 64)
    with pytest.raises(DaemonSpawnError, match="rendezvous"):
        task.pipe_path(config, "a\\b")


@pytest.mark.parametrize("stderr,phase", [
    (b"broker-phase:run-requested\r\n", "run-requested"),
    (b"broker-phase:run-requested\r\nbroker-phase:run-returned\r\n", "run-returned"),
    (b"private-value\r\nbroker-phase:private-value\r\n", "unobserved"),
    (b"broker-phase:run-requested\r\nprivate-value\xff\r\n", "run-requested"),
    (None, "unobserved"),
])
def test_activation_timeout_discloses_only_last_fixed_phase(monkeypatch, stderr, phase):
    monkeypatch.setattr(task, "os", SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}))
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "run_caller_owned", Mock(side_effect=subprocess.TimeoutExpired(
        ["safe-command"], 30, b"private-value", stderr,
    )))
    with pytest.raises(DaemonSpawnError, match="timed out") as caught:
        task.task_operation("activate", registration(), Deadline(30))
    assert "phase=" + phase in str(caught.value)
    assert "private-value" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("suffix,elapsed", [
    ("broker-phase-time:input-read:10250", "250"),
    ("broker-phase-time:input:10250", "unobserved"),
    ("broker-phase-time:input-read:9999", "unobserved"),
    ("broker-phase-time:input-read:610001", "unobserved"),
    ("broker-phase-time:input-read:private-value", "unobserved"),
    ("broker-phase-time:input-read:10250 private-value", "unobserved"),
])
def test_phase_timing_reports_only_matching_bounded_clock_evidence(suffix, elapsed):
    stderr = "broker-phase:input-read\n" + suffix + "\n"
    assert task._activation_elapsed(stderr, 10.0) == elapsed
    assert task._activation_elapsed(stderr.encode("ascii"), 10.0) == elapsed


def test_valid_phase_time_does_not_replace_native_refusal_with_unknown_text(monkeypatch):
    monkeypatch.setattr(task, "os", SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}))
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task.time, "perf_counter", lambda: 10.0)
    monkeypatch.setattr(task, "run_caller_owned", Mock(return_value=subprocess.CompletedProcess(
        ["safe-command"], 1, "",
        "broker-phase:lookup\nbroker-phase-time:lookup:10250\nbroker-adoption-required\n",
    )))
    with pytest.raises(DaemonSpawnError, match="broker-adoption-required") as caught:
        task.task_operation("activate", registration(), Deadline(30))
    assert "phase=lookup; elapsed-ms=250" in str(caught.value)


def test_fixed_activation_phases_do_not_hide_native_refusal(monkeypatch):
    monkeypatch.setattr(task, "os", SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}))
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "run_caller_owned", Mock(return_value=subprocess.CompletedProcess(
        ["safe-command"], 1, "", "broker-phase:run-requested\nbroker-activation-deadline\n",
    )))
    with pytest.raises(DaemonSpawnError, match="broker-activation-deadline") as caught:
        task.task_operation("activate", registration(), Deadline(30))
    assert "phase=run-requested" in str(caught.value)


def test_nonzero_adapter_failure_discloses_phase_without_native_exception_text(monkeypatch):
    monkeypatch.setattr(task, "os", SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}))
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "run_caller_owned", Mock(return_value=subprocess.CompletedProcess(
        ["safe-command"], 1, "", "broker-phase:run-returned\nprivate-value\n",
    )))
    with pytest.raises(DaemonSpawnError, match="scheduler-operation-failed") as caught:
        task.task_operation("activate", registration(), Deadline(30))
    assert "phase=run-returned" in str(caught.value)
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize("line,code", [
    ("broker-native-hresult:80070005", "80070005"),
    ("broker-native-hresult:private-value", "unobserved"),
    ("broker-native-hresult:80070005 private-value", "unobserved"),
])
def test_native_error_code_is_fixed_width_not_arbitrary_text(monkeypatch, line, code):
    monkeypatch.setattr(task, "os", SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}))
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    monkeypatch.setattr(task, "run_caller_owned", Mock(return_value=subprocess.CompletedProcess(
        ["safe-command"], 1, "", "broker-phase:run-requested\n" + line + "\n",
    )))
    with pytest.raises(DaemonSpawnError) as caught:
        task.task_operation("activate", registration(), Deadline(30))
    assert "code=" + code in str(caught.value)
    assert "private-value" not in str(caught.value)


def spawn_fixture(monkeypatch, activation=None):
    config = registration()
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.delenv(task.CONFIG_ENV, raising=False)
    monkeypatch.delenv(task.OWNER_ENV, raising=False)
    monkeypatch.setattr(installation, "load_current", Mock(return_value=config))
    api = Mock()
    api.identity.return_value = PeerIdentity(91001, "300", config.principal)
    api.process_image.return_value = config.pythonw
    monkeypatch.setattr(task, "native", lambda: api)
    operation = Mock(return_value=activation or {
        "instance": "{12345678-1234-1234-1234-123456789012}",
        "pid": 92001, "birth": "400", "principal": config.principal,
    })
    monkeypatch.setattr(task, "task_operation", operation)
    return config, api, operation


def test_spawn_authenticates_native_instance_before_live_request(monkeypatch):
    config, _, operation = spawn_fixture(monkeypatch)
    peer = Mock()
    constructor, connect, launch = Mock(return_value=peer), Mock(), Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", constructor)
    monkeypatch.setattr(task, "connect", connect)
    monkeypatch.setattr(task, "launch", launch)
    value = task.spawn(
        ["python", "live-only-fixture"], cwd=r"C:\state",
        env={"VALUE": "live-only-fixture"}, creationflags=0x08000000,
        log_path=r"C:\state\out.log", stderr_log_path=None,
    )
    assert value is launch.return_value
    assert [call.args[0] for call in operation.call_args_list] == ["activate"]
    assert "live-only-fixture" not in repr(operation.call_args_list)
    expected = PeerIdentity(92001, "400", config.principal)
    constructor.assert_called_once_with(expected)
    assert connect.call_args.args[1] == expected
    assert launch.call_args.args[1] is peer
    assert launch.call_args.args[2].env == {"VALUE": "live-only-fixture"}
    peer.close.assert_called_once()


@pytest.mark.parametrize("failure", [
    RuntimeError("private native PID mismatch"),
    OSError("private native detail"),
    PrivateIPCError(Refusal.IDENTITY),
])
def test_engine_pipe_mismatch_sends_no_launch_frame(monkeypatch, failure):
    spawn_fixture(monkeypatch)
    peer = Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", Mock(return_value=peer))
    monkeypatch.setattr(task, "connect", Mock(side_effect=failure))
    launch = Mock()
    monkeypatch.setattr(task, "launch", launch)
    with pytest.raises(DaemonSpawnError, match="launch failed") as caught:
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    assert caught.value.process_created is False
    assert caught.value.admission_sent is False
    assert caught.value.cleanup_confirmed
    assert "private" not in str(caught.value)
    launch.assert_not_called()
    peer.close.assert_called_once()


def test_peer_close_failure_retires_returned_child_and_retains_native_custody(monkeypatch):
    from agent_procutil import _daemon_instance_identity

    spawn_fixture(monkeypatch)
    peer, channel, resident = Mock(), Mock(), Mock(pid=93001)
    monkeypatch.setattr(task, "NativeBrokerPeer", Mock(return_value=peer))
    monkeypatch.setattr(task, "connect", Mock(return_value=channel))
    monkeypatch.setattr(task, "launch", Mock(return_value=resident))
    identity = resident.pid, "windows:500"
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _: identity)
    peer.close.side_effect = OSError("private handle detail")
    with pytest.raises(DaemonSpawnError, match="handle cleanup unconfirmed") as caught:
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    assert caught.value.process_created is True
    assert caught.value.process_identity == identity
    assert caught.value.admission_sent is True
    assert not caught.value.cleanup_confirmed
    assert "private" not in str(caught.value)
    resident.retire.assert_called_once()
    resident.close.assert_called_once()
    peer.close.assert_called_once()
    channel.close.assert_not_called()
    assert protocol._pending_closes == [peer]
    with pytest.raises(DaemonSpawnError, match="cleanup unconfirmed"):
        protocol.retry_cleanup(Deadline(1))
    peer.close.side_effect = None
    protocol.retry_cleanup(Deadline(1))
    assert protocol._pending_closes == []


def test_peer_cleanup_failure_preserves_uncertain_protocol_evidence(monkeypatch):
    spawn_fixture(monkeypatch)
    peer, channel = Mock(), Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", Mock(return_value=peer))
    monkeypatch.setattr(task, "connect", Mock(return_value=channel))
    failure = DaemonSpawnError(
        "Windows launch broker: transaction failed", cleanup_confirmed=False,
        process_created=True, admission_sent=True, process_identity=(93001, "windows:500"),
    )
    monkeypatch.setattr(task, "launch", Mock(side_effect=failure))
    peer.close.side_effect = OSError("private native detail")
    with pytest.raises(DaemonSpawnError) as caught:
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    assert caught.value is failure
    assert caught.value.process_created is True
    assert caught.value.admission_sent is True
    assert caught.value.process_identity == (93001, "windows:500")
    assert not caught.value.cleanup_confirmed
    assert protocol._pending_closes == [peer]
    channel.close.assert_not_called()


@pytest.mark.parametrize("identity_available", [True, False])
def test_spawn_binds_receipt_to_pinned_child_before_protocol_admission(monkeypatch, identity_available):
    from agent_procutil import _daemon_instance_identity, _daemon_instance_store

    spawn_fixture(monkeypatch)
    peer, channel, resident, receipt = Mock(), Mock(), Mock(pid=93001), Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", Mock(return_value=peer))
    monkeypatch.setattr(task, "connect", Mock(return_value=channel))
    monkeypatch.setattr(
        _daemon_instance_identity, "created_process_identity",
        lambda value: (value.pid, "windows:500") if identity_available else None,
    )
    bind = Mock()
    monkeypatch.setattr(_daemon_instance_store, "bind_process", bind)

    def launch(actual_channel, actual_peer, request, deadline, *, before_admit):
        assert actual_channel is channel and actual_peer is peer
        before_admit(resident)
        bind.assert_called_once_with(receipt, resident.pid, observed_birth="windows:500")
        return resident

    monkeypatch.setattr(task, "launch", launch)
    kwargs = dict(
        cwd=r"C:\state", env={}, creationflags=0x08000000,
        log_path=r"C:\state\out.log", stderr_log_path=None, instance_receipt=receipt,
    )
    if identity_available:
        assert task.spawn(["python"], **kwargs) is resident
    else:
        with pytest.raises(DaemonSpawnError, match="birth identity unavailable"):
            task.spawn(["python"], **kwargs)
        bind.assert_not_called()
    peer.close.assert_called_once()


def test_serve_rejects_containment_before_native_actions(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    listener = Mock()
    monkeypatch.setattr(task, "WindowsListener", listener)
    assert task.serve_main(["config", "nonce", "pid", "birth"]) == 2
    listener.assert_not_called()


def test_packaged_scheduler_adapter_is_declared():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["dependencies"] == []
    assert "windows_broker_task.ps1" in manifest["tool"]["setuptools"]["package-data"][
        "agent_procutil"
    ]
    assert manifest["project"]["optional-dependencies"]["windows-broker"] == [
        "agent-zdd>=0.1.0.dev7; sys_platform == 'win32'",
    ]
    source = manifest["tool"]["uv"]["sources"]["agent-zdd"]
    dependency = root / source["path"] / "pyproject.toml"
    assert tomllib.loads(dependency.read_text(encoding="utf-8"))["project"]["name"] == "agent-zdd"


def native_function(name, program):
    command = r"""
$ErrorActionPreference = 'Stop'
$path = [Console]::In.ReadToEnd()
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors) { throw 'fixture parse failed' }
if ('FUNCTION_NAME') {
    $function = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'FUNCTION_NAME'
    }, $true)
    . ([scriptblock]::Create($function.Extent.Text))
}
"""
    powershell = Path(os.environ["SystemRoot"]) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    return run_caller_owned(
        [
            str(powershell), "-NoProfile", "-NonInteractive", "-Command",
            command.replace("FUNCTION_NAME", name) + program,
        ],
        input_text=str(Path(task.__file__).with_name("windows_broker_task.ps1")),
        timeout=15, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )


def test_activation_phase_calls_match_the_fixed_diagnostic_allowlist():
    source = Path(task.__file__).with_name("windows_broker_task.ps1").read_text(encoding="utf-8")
    assert set(re.findall(r"Write-ActivationPhase '([^']+)'", source)) == task._ACTIVATION_PHASES


@pytest.mark.skipif(os.name != "nt", reason="Native Windows exception code redaction")
def test_native_error_writer_reports_only_hresult_chain():
    result = native_function(
        "Write-NativeFailureCode",
        r"""
$inner = [Runtime.InteropServices.COMException]::new('private-value', -2147024891)
Write-NativeFailureCode ([Exception]::new('private-value', $inner))
""",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == "broker-native-hresult:80131500\nbroker-native-hresult:80070005\n"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows COM VARIANT argument contract")
def test_actual_activation_expression_marshals_bstr_array_not_variant_array():
    result = native_function(
        "",
        r"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public sealed class SyntheticRunEx {
    [DllImport("oleaut32.dll")]
    private static extern int VariantClear(IntPtr value);
    public int RunEx(object parameters, int flags, int session, object user) {
        var values = parameters as string[];
        if (values == null || values.Length != 3 ||
            values[0] != "nonce" || values[1] != "123" || values[2] != "456") {
            throw new ArgumentException("BSTR array required");
        }
        IntPtr variant = Marshal.AllocCoTaskMem(32);
        try {
            Marshal.GetNativeVariantForObject(parameters, variant);
            return Marshal.ReadInt16(variant);
        } finally {
            VariantClear(variant);
            Marshal.FreeCoTaskMem(variant);
        }
    }
}
'@
$task = New-Object SyntheticRunEx
$inputValue = [pscustomobject]@{nonce='nonce';client_pid=123;client_birth='456'}
$expression = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
        $node.Member.Value -eq 'RunEx'
}, $true)
$kind = & ([scriptblock]::Create($expression.Extent.Text))
if ($kind -ne 8200) { throw 'VT_ARRAY | VT_BSTR required' }
[Console]::Out.Write('bstr-array')
""",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "bstr-array"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell fixed phase diagnostics")
def test_activation_phase_writer_refuses_unlisted_data_and_is_inert_for_other_actions():
    started = time.perf_counter()
    result = native_function(
        "Write-ActivationPhase",
        r"""
$Action = 'status'
Write-ActivationPhase 'input'
$Action = 'activate'
Write-ActivationPhase 'run-requested'
try {
    Write-ActivationPhase 'private-value'
    throw 'unexpected admission'
} catch [System.Management.Automation.ParameterBindingException] {
    [Console]::Out.Write('refused')
}
""",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "refused"
    phase, timing = result.stderr.splitlines()
    assert phase == "broker-phase:run-requested"
    stage, stamp = task._phase_time(timing)
    assert stage == "run-requested"
    assert round(started * 1000) - 1 <= stamp <= round(time.perf_counter() * 1000) + 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell exception mapping")
@pytest.mark.parametrize("missing", [False, True])
def test_first_adoption_distinguishes_missing_task_from_access_failure(missing):
    # Execute only the parsed lookup function with a synthetic folder. No COM
    # connection, registration, activation or host task is needed by this test.
    program = r"""
Add-Type 'public sealed class SyntheticTaskFolder {
    public object GetTask(string name) {
        throw new EXCEPTION_TYPE("synthetic lookup failure");
    }
}'
$folder = New-Object SyntheticTaskFolder
try {
    $value = Find-OwnedTask $folder ([pscustomobject]@{task_name='synthetic'})
    if ($null -ne $value) { throw 'unexpected task' }
    [Console]::Out.Write('missing')
} catch [System.UnauthorizedAccessException] {
    [Console]::Out.Write('refused')
}
"""
    program = program.replace(
        "EXCEPTION_TYPE",
        "System.IO.FileNotFoundException" if missing else "System.UnauthorizedAccessException",
    )
    result = native_function("Find-OwnedTask", program)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("missing" if missing else "refused")


@pytest.mark.skipif(os.name != "nt", reason="Native Windows principal normalization")
@pytest.mark.parametrize("field", ["Name", "User.Value"])
def test_scheduler_principal_names_and_sids_identify_the_same_user(field):
    result = native_function(
        "Resolve-TaskPrincipalSid",
        r"""
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$actual = Resolve-TaskPrincipalSid $identity.FIELD
if ($actual -cne $identity.User.Value) { throw 'principal mismatch' }
[Console]::Out.Write('matched')
""".replace("FIELD", field),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "matched"
