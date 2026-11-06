"""A zero active count is not yet a native member-handle exit witness."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _windows_caller_owned as native


def _value(handle):
    return getattr(handle, "value", handle)


@pytest.fixture
def retirement(monkeypatch):
    kernel = Mock()
    closed = []
    kernel.GetCurrentProcess.return_value = -1
    kernel.CloseHandle.side_effect = lambda handle: closed.append(_value(handle)) or True
    kernel.OpenProcess.side_effect = lambda access, inherit, pid: pid + 1000
    kernel.WaitForSingleObject.return_value = 0

    def duplicate(source, handle, target, output, access, inherit, options):
        assert (source, target, access, inherit, options) == (-1, -1, 0, False, 2)
        output._obj.value = 900
        return True

    def inside(handle, job, output):
        assert job == 500
        output._obj.value = True
        return True

    kernel.DuplicateHandle.side_effect = duplicate
    kernel.IsProcessInJob.side_effect = inside
    job = SimpleNamespace(handle=500)
    terminated = []

    def terminate(timeout):
        assert 0 < timeout <= 2
        terminated.append(timeout)
        job.handle = None

    job.terminate_and_wait = terminate
    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(native, "_job_pids", lambda *args, **kwargs: {20})
    monkeypatch.setattr(native, "_job_total", lambda *args: 1)
    monkeypatch.setattr(native.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(native.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        native.ctypes, "WinError", lambda code: OSError(code, "native operation failed"), raising=False,
    )
    return SimpleNamespace(kernel=kernel, job=job, terminated=terminated, closed=closed)


def test_member_exit_is_waited_after_job_accounting_reaches_zero(retirement):
    retirement.kernel.WaitForSingleObject.side_effect = [0x102, 0]
    native._retire_job(retirement.job, 2)
    assert retirement.terminated == [2]
    assert retirement.kernel.WaitForSingleObject.call_args_list[0].args == (1020, 0)
    assert retirement.kernel.WaitForSingleObject.call_args_list[1].args == (1020, 2000)
    assert retirement.closed == [1020, 900]


def test_unconfirmed_member_exit_cannot_return_success(retirement):
    retirement.kernel.WaitForSingleObject.return_value = 0x102
    with pytest.raises(TimeoutError, match="member exit"):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == [2]
    assert retirement.closed == []
    assert retirement.job._native_retirement.pinned == {20: 1020}
    retirement.kernel.WaitForSingleObject.return_value = 0
    native._retire_job(retirement.job, 2)
    assert retirement.terminated == [2], "retry uses the retained native identities"
    assert retirement.closed == [1020, 900]
    assert not hasattr(retirement.job, "_native_retirement")


def test_failed_final_query_preserves_the_same_retryable_handles(retirement, monkeypatch):
    totals = Mock(side_effect=[1, 1, OSError("query unavailable"), 1])
    monkeypatch.setattr(native, "_job_total", totals)
    with pytest.raises(OSError, match="query unavailable"):
        native._retire_job(retirement.job, 2)
    assert retirement.closed == []
    assert retirement.job._native_retirement.pinned == {20: 1020}
    native._retire_job(retirement.job, 2)
    assert retirement.kernel.OpenProcess.call_count == 1
    assert retirement.kernel.DuplicateHandle.call_count == 1
    assert retirement.closed == [1020, 900]


def test_failed_job_termination_retains_original_and_member_custody(retirement):
    terminate = retirement.job.terminate_and_wait
    retirement.job.terminate_and_wait = Mock(side_effect=TimeoutError("job still active"))
    with pytest.raises(TimeoutError, match="job still active"):
        native._retire_job(retirement.job, 2)
    assert retirement.job.handle == 500
    assert retirement.closed == []
    retirement.job.terminate_and_wait = terminate
    native._retire_job(retirement.job, 2)
    assert retirement.kernel.OpenProcess.call_count == 1
    assert retirement.closed == [1020, 900]


def test_stale_pid_is_not_waited_or_terminated_as_an_owned_member(retirement):
    def unrelated(handle, job, output):
        output._obj.value = False
        return True

    retirement.kernel.IsProcessInJob.side_effect = unrelated
    native._retire_job(retirement.job, 2)
    retirement.kernel.WaitForSingleObject.assert_not_called()
    retirement.kernel.TerminateProcess.assert_not_called()
    assert retirement.closed == [1020, 900]


def test_new_birth_during_census_is_pinned_before_retirement(retirement, monkeypatch):
    totals = iter((1, 2, 2, 2, 2))
    populations = iter(({20}, {20, 21}))
    monkeypatch.setattr(native, "_job_total", lambda *args: next(totals))
    monkeypatch.setattr(native, "_job_pids", lambda *args, **kwargs: next(populations))
    native._retire_job(retirement.job, 2)
    assert retirement.kernel.OpenProcess.call_count == 2
    assert set(retirement.closed) == {900, 1020, 1021}


def test_census_deadline_terminates_before_opening_remaining_members(retirement, monkeypatch):
    clock = iter((10.0, 10.0, 10.0, 12.1, 12.1))
    monkeypatch.setattr(native.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(native, "_job_pids", lambda *args, **kwargs: {20, 21})
    with pytest.raises(TimeoutError, match="budget"):
        native._retire_job(retirement.job, 2)
    assert retirement.kernel.OpenProcess.call_count == 1
    retirement.kernel.TerminateJobObject.assert_called_once_with(500, 1)
    assert retirement.terminated == []
    assert retirement.closed == []
    assert retirement.job.handle == 500


def test_late_unpinned_birth_refuses_completion(retirement, monkeypatch):
    totals = iter((1, 1, 2))
    monkeypatch.setattr(native, "_job_total", lambda *args: next(totals))
    with pytest.raises(OSError, match="unpinned process"):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == [2]
    assert retirement.closed == []
    assert retirement.job._native_retirement.duplicate.value == 900


def test_continuous_births_still_terminate_then_report_unstable_ownership(retirement, monkeypatch):
    totals = iter(range(6))
    monkeypatch.setattr(native, "_job_total", lambda *args: next(totals))
    with pytest.raises(OSError, match="stabilize"):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == []
    retirement.kernel.TerminateJobObject.assert_called_once_with(500, 1)
    assert retirement.closed == []
    assert retirement.job.handle == 500


@pytest.mark.parametrize("failure", ["duplicate", "membership", "process"])
def test_census_failure_still_terminates_and_preserves_failure(retirement, monkeypatch, failure):
    if failure == "duplicate":
        retirement.kernel.DuplicateHandle.side_effect = None
        retirement.kernel.DuplicateHandle.return_value = False
    elif failure == "membership":
        monkeypatch.setattr(native, "_job_pids", Mock(side_effect=OSError("unknown membership")))
    else:
        retirement.kernel.OpenProcess.side_effect = None
        retirement.kernel.OpenProcess.return_value = None
    with pytest.raises(OSError):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == []
    retirement.kernel.TerminateJobObject.assert_called_once_with(500, 1)
    assert retirement.job.handle == 500


def test_missing_duplicate_identity_is_not_accepted(retirement):
    retirement.kernel.DuplicateHandle.side_effect = lambda *args: True
    with pytest.raises(OSError, match="duplicate identity"):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == []
    retirement.kernel.TerminateJobObject.assert_called_once_with(500, 1)
    assert retirement.job.handle == 500


def test_incomplete_accounting_is_not_a_completion_witness(retirement, monkeypatch):
    monkeypatch.setattr(native, "_job_total", lambda *args: 0)
    with pytest.raises(OSError, match="omitted pinned members"):
        native._retire_job(retirement.job, 2)
    assert retirement.terminated == []
    retirement.kernel.TerminateJobObject.assert_called_once_with(500, 1)
    assert retirement.closed == []
    assert retirement.job.handle == 500


def test_every_handle_close_is_attempted_after_a_close_failure(retirement):
    def close(handle):
        retirement.closed.append(_value(handle))
        return False

    retirement.kernel.CloseHandle.side_effect = close
    with pytest.raises(OSError, match="native operation"):
        native._retire_job(retirement.job, 2)
    assert retirement.closed == [1020, 900]
    assert len(retirement.job._native_retirement.handles) == 2
    retirement.kernel.CloseHandle.side_effect = None
    retirement.kernel.CloseHandle.return_value = True
    native._retire_job(retirement.job, 2)
    assert not hasattr(retirement.job, "_native_retirement")


@pytest.mark.parametrize("timeout", [False, 0, -1, float("inf"), float("nan")])
def test_invalid_retirement_does_not_mutate_job(retirement, timeout):
    with pytest.raises(ValueError):
        native._retire_job(retirement.job, timeout)
    assert retirement.terminated == []
    retirement.kernel.DuplicateHandle.assert_not_called()
