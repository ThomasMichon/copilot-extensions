"""Actor-free finite-writer outcomes; actual native APIs stay boundary doubles."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/src/agent_procutil/_native_owned.py"


class UnsettledChild(Exception):
    def __init__(self, process, job) -> None:
        self.process, self.job = process, job


@pytest.fixture
def owned(monkeypatch: pytest.MonkeyPatch):
    package = ModuleType("agent_procutil")
    package.JobHandle = object
    package._job_scopes = SimpleNamespace(
        ENVIRONMENT_KEY="COPILOT_EXTENSIONS_OWNED_JOB_SCOPES",
        child_environment=lambda environment, **kwargs: dict(environment),
        scope_names=lambda environment: (), new_name=lambda: "fixture-scope",
    )
    process_boundary = ModuleType("agent_procutil.owned_process")
    process_boundary.OwnedCleanupError = UnsettledChild
    name = "agent_procutil._unit_native_owned"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"agent_procutil": package, "agent_procutil.owned_process": process_boundary}):
        sys.modules[name] = module
        spec.loader.exec_module(module)
    process, job = Mock(), SimpleNamespace(handle=123)
    process.wait.return_value = 7
    monkeypatch.setattr(module, "_spawn", Mock(return_value=(process, job)))
    monkeypatch.setattr(module, "_job_empty", Mock(side_effect=[False, False, True]))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 1.0, sleep=Mock()))
    return SimpleNamespace(module=module, process=process, job=job)


@pytest.fixture
def parameters() -> dict:
    return {
        "executable": r"C:\Python\python.exe", "arguments": ["", "a b", 'x"y'],
        "cwd": "C:\\fixture\\", "log": r"C:\fixture\owned.log", "timeout_ms": 2000,
        "environment": {"PATH": r"C:\Python"},
    }


@pytest.mark.parametrize("field,value", [
    ("executable", "C:python.exe"), ("cwd", "\\fixture"), ("log", "relative.log"),
    ("timeout_ms", True), ("timeout_ms", 0), ("timeout_ms", 7200001),
    ("arguments", ["a\0b"]), ("environment", {"PATH": "a\0b"}),
], ids=[
    "drive-relative-executable", "root-relative-cwd", "relative-log", "boolean-deadline",
    "zero-deadline", "over-cap-deadline", "nul-argument", "nul-environment",
])
def test_invalid_request_refuses_before_log_or_native_creation(
    owned, parameters: dict, field: str, value: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned.module, "open", lambda *args, **kwargs: pytest.fail("log opened"), raising=False)
    parameters[field] = value
    with pytest.raises(owned.module.NativeOwnedFailure) as failure:
        owned.module.run(parameters)
    assert failure.value.cleanup_confirmed is True
    owned.module._spawn.assert_not_called()


@pytest.mark.parametrize("cwd", [r"\\server\share", "\\\\server\\share\\"],
                         ids=["without-separator", "with-separator"])
def test_unc_share_root_cwd_reaches_target_unchanged(owned, parameters: dict, cwd: str) -> None:
    parameters["cwd"] = cwd
    request = owned.module.validate_request(parameters)
    assert request.cwd == cwd
    assert request.argv == [parameters["executable"], *parameters["arguments"]]
    assert request.environment == parameters["environment"]
    output = Mock()
    assert owned.module._run_target(request, output) == 7
    owned.module._spawn.assert_called_once_with(request, output)
    owned.process.retire_job.assert_called_once_with(owned.job, 10)


@pytest.mark.parametrize("field,path", [
    ("executable", r"\\server\share"), ("log", r"\\server\share"),
    ("cwd", r"\\server"), ("cwd", "\\\\server\\"), ("cwd", r"\\?\C:"),
    ("cwd", "C:fixture"), ("cwd", r"C:\fixture\..\other"),
], ids=[
    "share-root-executable", "share-root-log", "missing-share", "empty-share",
    "device-root", "drive-relative-cwd", "unnormalized-cwd",
])
def test_unc_cwd_exception_does_not_relax_other_path_refusals(
    owned, parameters: dict, field: str, path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned.module, "open", lambda *args, **kwargs: pytest.fail("log opened"), raising=False)
    parameters[field] = path
    with pytest.raises(owned.module.NativeOwnedFailure, match="fully qualified") as failure:
        owned.module.run(parameters)
    assert failure.value.cleanup_confirmed is True
    owned.module._spawn.assert_not_called()


@pytest.mark.parametrize("code", [0, 7, 0xC0000005, 0xFFFFFFFF])
def test_root_status_waits_for_every_job_writer_and_exact_retirement(
    owned, parameters: dict, code: int,
) -> None:
    owned.process.wait.return_value = code
    result = owned.module._run_target(owned.module.validate_request(parameters), Mock())
    assert result == (code if code < 2**31 else code - 2**32)
    assert owned.module._job_empty.call_count == 3
    owned.process.retire_job.assert_called_once_with(owned.job, 10)
    owned.process.close.assert_called_once_with()


@pytest.mark.parametrize("empty", [False, True])
def test_expired_query_never_publishes_success_and_still_retires(
    owned, parameters: dict, empty: bool,
) -> None:
    owned.module._job_empty.side_effect = None
    owned.module._job_empty.return_value = empty
    ticks = iter((1.0, 3.0))
    owned.module.time.monotonic = lambda: next(ticks)
    with pytest.raises(owned.module.NativeOwnedFailure, match="deadline") as failure:
        owned.module._run_target(owned.module.validate_request(parameters), Mock())
    assert failure.value.cleanup_confirmed is True
    owned.process.retire_job.assert_called_once_with(owned.job, 10)
    owned.process.close.assert_called_once_with()


def test_target_creation_cannot_rearm_the_requested_execution_limit(
    owned, parameters: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1.0}
    owned.module.time.monotonic = lambda: clock["now"]

    def delayed_spawn(*_args):
        clock["now"] = 3.5
        return owned.process, owned.job

    owned.module._spawn.side_effect = delayed_spawn
    owned.module._job_empty.side_effect = None
    owned.module._job_empty.return_value = True
    with pytest.raises(owned.module.NativeOwnedFailure, match="deadline") as failure:
        owned.module._run_target(owned.module.validate_request(parameters), Mock())
    assert failure.value.cleanup_confirmed is True
    owned.process.retire_job.assert_called_once_with(owned.job, 10)
    owned.process.close.assert_called_once_with()


@pytest.mark.parametrize("stage", ["spawn", "retirement", "close"])
def test_unknown_cleanup_keeps_exact_resources_and_refuses_automatic_retry(
    owned, parameters: dict, stage: str,
) -> None:
    if stage == "spawn":
        owned.module._spawn.side_effect = UnsettledChild(owned.process, owned.job)
    elif stage == "retirement":
        owned.process.retire_job.side_effect = OSError("fixture uncertainty")
    else:
        owned.process.close.side_effect = OSError("fixture uncertainty")
    with pytest.raises(owned.module.NativeOwnedFailure, match="cleanup") as failure:
        owned.module._run_target(owned.module.validate_request(parameters), Mock())
    assert failure.value.cleanup_confirmed is False
    assert failure.value.process is owned.process and failure.value.job is owned.job
    assert owned.process.retire_job.call_count == (0 if stage == "spawn" else 1)


def _failing_log(owned, monkeypatch: pytest.MonkeyPatch):
    output = MagicMock()
    error = OSError("fixture log close failure")
    output.close.side_effect = error
    output.__enter__.return_value = output
    output.__exit__.side_effect = lambda *args: output.close()
    monkeypatch.setattr(owned.module, "open", Mock(return_value=output), raising=False)
    return output, error


@pytest.mark.parametrize("stage", ["spawn", "retirement"])
def test_log_close_cannot_upgrade_uncertain_native_cleanup(
    owned, parameters: dict, stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, log_error = _failing_log(owned, monkeypatch)
    if stage == "spawn":
        owned.module._spawn.side_effect = UnsettledChild(owned.process, owned.job)
    else:
        owned.process.retire_job.side_effect = OSError("fixture retirement uncertainty")
    with pytest.raises(owned.module.NativeOwnedFailure) as failure:
        owned.module.run(parameters)
    assert failure.value.cleanup_confirmed is False
    assert failure.value.process is owned.process and failure.value.job is owned.job
    assert failure.value.output is output and failure.value.log_error is log_error
    output.close.assert_called_once_with()


def test_log_close_preserves_settled_failure(
    owned, parameters: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, log_error = _failing_log(owned, monkeypatch)
    ticks = iter((1.0, 3.0))
    owned.module.time.monotonic = lambda: next(ticks)
    with pytest.raises(owned.module.NativeOwnedFailure, match="deadline") as failure:
        owned.module.run(parameters)
    assert failure.value.cleanup_confirmed is True
    assert failure.value.output is output and failure.value.log_error is log_error
    assert "log close" in str(failure.value)
    output.close.assert_called_once_with()


def test_log_close_after_success_is_an_explicit_retained_failure(
    owned, parameters: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, log_error = _failing_log(owned, monkeypatch)
    with pytest.raises(owned.module.NativeOwnedFailure, match="log close") as failure:
        owned.module.run(parameters)
    assert failure.value.cleanup_confirmed is True
    assert failure.value.output is output and failure.value.log_error is log_error
    owned.process.retire_job.assert_called_once_with(owned.job, 10)
    output.close.assert_called_once_with()


@pytest.fixture
def shell(owned, monkeypatch: pytest.MonkeyPatch):
    package = ModuleType("agent_procutil")
    package.__path__ = []
    package._native_owned = owned.module
    store = ModuleType("agent_procutil._daemon_instance_store")
    store.registry_root = lambda: "fixture-registry"
    clock = ModuleType("agent_procutil._daemon_instance_clock")
    clock.WallLeaseObservation = object
    records = ModuleType("agent_procutil._daemon_instance_records")
    records.ProcessIdentity = object
    for name in ("admit_for_process", "bind_for_process", "finish_for_process", "reserve_for_process"):
        setattr(records, name, Mock(side_effect=AssertionError("unselected daemon operation")))
    instances = ModuleType("agent_procutil.daemon_instances")
    instances.INSTANCE_ENV, instances.REGISTRY_ENV, instances.TEST_CONTEXT_ENV = (
        "fixture-instance", "fixture-registry", "fixture-context",
    )
    instances.DaemonInstanceReceipt = instances.DaemonInstanceSpec = instances.DaemonLeasePolicy = object
    for module in (package, store, clock, records, instances):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, "agent_procutil._native_owned", owned.module)
    name = "agent_procutil._unit_finite_shell"
    spec = importlib.util.spec_from_file_location(name, SOURCE.with_name("_daemon_instance_shell.py"))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["unconfirmed", "retained-log", "settled"])
def test_wire_finite_fence_preserves_custody_and_correlated_responses(
    owned, shell, parameters: dict, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    failure = owned.module.NativeOwnedFailure(
        "fixture finite failure", cleanup_confirmed=kind != "unconfirmed",
        process=owned.process, job=owned.job,
    )
    output = Mock() if kind == "retained-log" else None
    failure.output = output
    failure.log_error = OSError("fixture close failure") if output else None
    run = Mock(side_effect=failure)
    monkeypatch.setattr(owned.module, "run", run)
    owner = Mock()
    session = shell.ShellInstanceSession(owner)
    nonce = "a" * 32
    requests = [
        {"command": "run-owned", "parameters": parameters},
        {"command": "root"},
        {"command": "run-owned", "parameters": parameters},
    ]
    payload = b"".join(base64.b64encode(json.dumps({
        **request, "nonce": nonce, "sequence": sequence,
    }).encode()) + b"\n" for sequence, request in enumerate(requests, 1))
    monkeypatch.setattr(shell.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(raw=io.BytesIO(payload))))
    stream = io.StringIO()
    monkeypatch.setattr(shell.sys, "stdout", stream)
    assert shell._serve_requests(session, nonce) == 0
    responses = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [value["sequence"] for value in responses] == [1, 2, 3]
    assert all(value["nonce"] == nonce for value in responses)
    assert responses[0]["cleanup_confirmed"] is failure.cleanup_confirmed
    if kind == "settled":
        assert responses[1]["ok"] is True and responses[1]["result"] == "fixture-registry"
        assert run.call_count == 2 and session._owned_cleanup is None and not session._failed
    else:
        assert all(value["ok"] is False for value in responses)
        assert all("replay is refused" in value["error"] for value in responses[1:])
        assert responses[2]["cleanup_confirmed"] is False
        assert session._owned_cleanup is failure and session._failed
        assert failure.process is owned.process and failure.job is owned.job and failure.output is output
        assert run.call_count == owner.require_live.call_count == 1
    owned.process.retire_job.assert_not_called()
    owned.process.close.assert_not_called()
    if output is not None:
        output.close.assert_not_called()


def test_wire_unrelated_fatal_admission_is_not_changed_into_replay_response(
    shell, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = shell.ShellInstanceSession(Mock())
    session._failed = True
    nonce = "b" * 32
    raw = base64.b64encode(json.dumps({
        "command": "root", "nonce": nonce, "sequence": 1,
    }).encode()) + b"\n"
    monkeypatch.setattr(shell.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(raw=io.BytesIO(raw))))
    output = io.StringIO()
    monkeypatch.setattr(shell.sys, "stdout", output)
    with pytest.raises(RuntimeError, match="original shell lifetime admission failed"):
        shell._serve_requests(session, nonce)
    assert not output.getvalue() and session._owned_cleanup is None
