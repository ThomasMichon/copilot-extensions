"""Compiler-free finite-writer contracts without native effects."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _native_owned as owned
from agent_procutil import _daemon_instance_shell as shell
from agent_procutil.owned_process import OwnedCleanupError


@pytest.fixture
def native_request() -> dict:
    return {
        "executable": r"C:\Python\python.exe", "arguments": ["", "a b", 'x"y', "tail\\"],
        "cwd": "C:\\fixture\\", "log": r"C:\fixture\owned.log", "timeout_ms": 2000,
        "environment": {"PATH": r"C:\Python", "UNCHANGED": "value"},
    }


@pytest.mark.parametrize("field", ["executable", "cwd", "log"])
@pytest.mark.parametrize("path", ["C:tool.exe", r"\owned.log", "relative", r"C:\a\..\b", "C:\\x\0y"])
def test_partially_qualified_paths_refuse_before_native_or_log(
    native_request: dict, field: str, path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned, "open", Mock(side_effect=AssertionError("log opened")), raising=False)
    native_request[field] = path
    with pytest.raises(owned.NativeOwnedFailure, match="fully qualified") as failure:
        owned.run(native_request)
    assert failure.value.cleanup_confirmed is True


@pytest.mark.parametrize("timeout", [True, False, 0, -1, 7200001, float("inf"), "1000"])
def test_invalid_deadlines_refuse_without_launch(native_request: dict, timeout: object) -> None:
    native_request["timeout_ms"] = timeout
    with pytest.raises(owned.NativeOwnedFailure, match="deadline") as failure:
        owned.run(native_request)
    assert failure.value.cleanup_confirmed is True


@pytest.mark.parametrize("scopes", [
    "", "invalid", ";".join([r"Local\ce-owned-job-" + "a" * 32] * 2),
    ";".join(r"Local\ce-owned-job-" + f"{value:032x}" for value in range(64)),
])
def test_scope_chain_is_validated_before_log_open(native_request: dict, scopes: str) -> None:
    native_request["environment"]["COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"] = scopes
    with pytest.raises(owned.NativeOwnedFailure, match="Job scope") as failure:
        owned.run(native_request)
    assert failure.value.cleanup_confirmed is True


def test_validation_preserves_original_arguments_environment_and_trailing_cwd(native_request: dict) -> None:
    parsed = owned.validate_request(native_request)
    assert parsed.argv == [native_request["executable"], *native_request["arguments"]]
    assert parsed.cwd == native_request["cwd"] and parsed.timeout == 2
    assert parsed.environment == native_request["environment"]
    assert parsed.environment is not native_request["environment"]


@pytest.mark.parametrize("cwd", [r"\\server\share", "\\\\server\\share\\"])
def test_validation_preserves_unc_share_root_cwd(native_request: dict, cwd: str) -> None:
    native_request["cwd"] = cwd
    parsed = owned.validate_request(native_request)
    assert parsed.cwd == cwd
    assert parsed.argv == [native_request["executable"], *native_request["arguments"]]
    assert parsed.environment == native_request["environment"]


@pytest.mark.parametrize("field", ["executable", "log"])
def test_unc_share_root_is_not_a_file_path(
    native_request: dict, field: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned, "open", Mock(side_effect=AssertionError("log opened")), raising=False)
    native_request[field] = r"\\server\share"
    with pytest.raises(owned.NativeOwnedFailure, match="fully qualified") as failure:
        owned.run(native_request)
    assert failure.value.cleanup_confirmed is True


@pytest.mark.parametrize("cwd", [r"\\server", "\\\\server\\", r"\\?\C:"])
def test_incomplete_unc_or_device_root_cwd_refuses_before_log_open(
    native_request: dict, cwd: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned, "open", Mock(side_effect=AssertionError("log opened")), raising=False)
    native_request["cwd"] = cwd
    with pytest.raises(owned.NativeOwnedFailure, match="fully qualified") as failure:
        owned.run(native_request)
    assert failure.value.cleanup_confirmed is True


def test_original_scope_boundary_excludes_helper_metadata_and_normalizes_key_case(
    native_request: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"
    helper = r"Local\ce-owned-job-" + "f" * 32
    names = [r"Local\ce-owned-job-" + f"{index:032x}" for index in range(63)]
    monkeypatch.setenv(key, helper)
    native_request["environment"][key.lower()] = ";".join(names)
    parsed = owned.validate_request(native_request)
    assert parsed.environment[key].split(";") == names
    assert key.lower() not in parsed.environment
    assert helper not in parsed.environment[key]


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch):
    process, job = Mock(), SimpleNamespace(handle=123)
    process.wait.return_value = 7
    spawn = Mock(return_value=(process, job))
    empty = Mock(side_effect=[False, False, True])
    monkeypatch.setattr(owned, "_spawn", spawn)
    monkeypatch.setattr(owned, "_job_empty", empty)
    monkeypatch.setattr(owned.time, "sleep", Mock())
    return SimpleNamespace(process=process, job=job, spawn=spawn, empty=empty)


@pytest.mark.parametrize("code", [0, 7, 0xC0000005, 0xFFFFFFFF])
def test_every_writer_settles_before_root_status_is_returned(native_request: dict, target, code: int) -> None:
    target.process.wait.return_value = code
    log = Mock()
    actual = owned._run_target(owned.validate_request(native_request), log)
    assert actual == (code if code < 2**31 else code - 2**32)
    assert target.empty.call_count == 3
    target.process.retire_job.assert_called_once_with(target.job, 10)
    target.process.close.assert_called_once_with()
    assert target.process.mock_calls == [
        ("wait", (), {"timeout": 0}), ("retire_job", (target.job, 10), {}),
        ("wait", (), {"timeout": 0}), ("close", (), {}),
    ]


def test_timeout_reports_failure_only_after_exact_job_retirement(
    native_request: dict, target, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owned.time, "monotonic", Mock(side_effect=[1.0, 3.0]))
    with pytest.raises(owned.NativeOwnedFailure, match="deadline") as failure:
        owned._run_target(owned.validate_request(native_request), Mock())
    assert failure.value.cleanup_confirmed is True
    target.process.retire_job.assert_called_once_with(target.job, 10)
    target.process.close.assert_called_once_with()


def test_late_empty_job_or_root_query_cannot_publish_success(
    native_request: dict, target, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target.empty.side_effect = None
    target.empty.return_value = True
    monkeypatch.setattr(owned.time, "monotonic", Mock(side_effect=[1.0, 3.0]))
    with pytest.raises(owned.NativeOwnedFailure, match="deadline") as failure:
        owned._run_target(owned.validate_request(native_request), Mock())
    assert failure.value.cleanup_confirmed is True
    target.process.retire_job.assert_called_once_with(target.job, 10)


def test_failed_retirement_keeps_exact_custody_and_does_not_retry(native_request: dict, target) -> None:
    target.process.retire_job.side_effect = OSError("unconfirmed")
    with pytest.raises(owned.NativeOwnedFailure, match="cleanup") as failure:
        owned._run_target(owned.validate_request(native_request), Mock())
    assert failure.value.cleanup_confirmed is False
    assert failure.value.process is target.process and failure.value.job is target.job
    target.process.retire_job.assert_called_once_with(target.job, 10)
    target.process.close.assert_not_called()


def test_failed_atomic_spawn_keeps_its_original_cleanup_uncertainty(native_request: dict, target) -> None:
    target.spawn.side_effect = OwnedCleanupError(target.process, target.job)
    with pytest.raises(owned.NativeOwnedFailure) as failure:
        owned._run_target(owned.validate_request(native_request), Mock())
    assert failure.value.cleanup_confirmed is False
    assert failure.value.process is target.process and failure.value.job is target.job
    target.process.retire_job.assert_not_called()
    target.process.close.assert_not_called()


def test_private_session_fences_only_unconfirmed_native_outcomes(
    native_request: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = Mock()
    session = shell.ShellInstanceSession(owner)
    failure = owned.NativeOwnedFailure("uncertain", cleanup_confirmed=False, process=Mock(), job=Mock())
    monkeypatch.setattr(owned, "run", Mock(side_effect=failure))
    with pytest.raises(owned.NativeOwnedFailure):
        session.dispatch({"command": "run-owned", "parameters": native_request})
    assert session._owned_cleanup is failure
    with pytest.raises(OSError, match="replay"):
        session.dispatch({"command": "run-owned", "parameters": native_request})
    assert session._owned_cleanup is failure


def test_private_session_retains_failed_log_closure_after_settled_target(
    native_request: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = shell.ShellInstanceSession(Mock())
    failure = owned.NativeOwnedFailure("log close failed", cleanup_confirmed=True)
    failure.output = Mock()
    failure.log_error = OSError("fixture closure")
    monkeypatch.setattr(owned, "run", Mock(side_effect=failure))
    with pytest.raises(owned.NativeOwnedFailure):
        session.dispatch({"command": "run-owned", "parameters": native_request})
    assert session._owned_cleanup is failure
    with pytest.raises(OSError, match="replay"):
        session.dispatch({"command": "run-owned", "parameters": native_request})
    assert session._owned_cleanup is failure
