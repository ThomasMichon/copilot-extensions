"""Actor-free finite-writer outcomes; actual native APIs stay boundary doubles."""

from __future__ import annotations

import importlib.util
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
