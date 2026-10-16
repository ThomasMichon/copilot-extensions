"""Only confirmed Windows Job cleanup can license payload timeout replay."""

import os
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import installer_run, reconcile, update_acquisition

pytestmark = pytest.mark.guard


@pytest.mark.skipif(os.name != "nt", reason="Windows Job process contract")
@pytest.mark.real_installer_processes
@pytest.mark.parametrize("failure", [None, "termination", "drain", "read"])
def test_strict_job_return_requires_confirmation_and_reaping(monkeypatch, tmp_path, failure):
    seen, order = {}, []

    class Process:
        returncode = 0

        def communicate(self, timeout):
            order.append("communicate")
            if failure == "read":
                error = OSError("post-launch pipe sharing failure")
                error.winerror = 32
                raise error
            if failure == "drain" and order.count("communicate") == 2:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return "captured", None

    class Job:
        def terminate_and_wait(self, timeout):
            order.append("confirm")
            if failure == "termination":
                raise OSError("cannot query owned Job")

        def close(self):
            order.append("close")

    def spawn(argv, **kwargs):
        seen.update(kwargs)
        return Process(), Job()

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", spawn)
    call = lambda: installer_run.run_installer(
        ["fixture"], cwd=tmp_path, env={}, deadline_s=1, log=lambda line: None,
        require_tree_exit=True,
    )
    if failure:
        with pytest.raises(installer_run.InstallerCleanupError, match="cleanup unverified"):
            call()
    else:
        assert call().status == "OK"
    assert seen["require_job"] is True
    assert seen["allow_breakaway"] is False
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert order[:2] == (["communicate", "close"] if failure == "read" else
                         ["communicate", "confirm"])
    assert ("close" in order) is (failure in ("termination", "read"))


def test_cleanup_failure_never_retries_the_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    calls = []

    def fail(argv, **kwargs):
        calls.append(argv)
        raise installer_run.InstallerCleanupError("owned Job still has live processes")

    monkeypatch.setattr(update_acquisition, "_run_update", fail)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert status.startswith("payload cleanup failed")
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows native spawn errors")
@pytest.mark.real_installer_processes
@pytest.mark.parametrize("code", [5, 32, 193])
def test_strict_spawn_retains_the_native_error(monkeypatch, tmp_path, code):
    error = OSError("localized spawn failure")
    error.winerror = code

    def fail(argv, **kwargs):
        raise error

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fail)
    with pytest.raises(OSError) as raised:
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )
    assert raised.value is error


@pytest.mark.skipif(os.name != "nt", reason="Windows strict launch refusal")
@pytest.mark.real_installer_processes
def test_containment_refusal_is_not_reported_as_an_executed_child(monkeypatch, tmp_path):
    error = RuntimeError("Job assignment refused before child resume")

    def fail(argv, **kwargs):
        raise error

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fail)
    with pytest.raises(installer_run.InstallerLaunchError) as raised:
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("environment", [{}, {"AUTH_CONTEXT_FIXTURE": "child-only"}])
def test_windows_update_keeps_the_exact_supplied_environment(monkeypatch, tmp_path, environment):
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)

    def run(argv, **kwargs):
        assert kwargs["env"] is environment
        return installer_run.InstallerRun("OK", 0, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    assert update_acquisition._run_update(
        ["fixture"], cwd=tmp_path, timeout=1, env=environment,
    ).returncode == 0


@pytest.mark.parametrize("refusal", ["", "HTTP 403 authentication required", "[WinError 5] localized"])
def test_only_quiescent_nonrefused_timeout_retries(monkeypatch, tmp_path, refusal):
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise update_acquisition._QuiescentPayloadTimeout(argv, 1, output=refusal)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert status.startswith("OK") is (not refusal)
    assert len(calls) == (1 if refusal else 2)


@pytest.mark.parametrize("elapsed,expected", [(209.5, [120, 0.5]), (211, [120])])
def test_windows_budget_reserves_the_cleanup_grace(monkeypatch, tmp_path, elapsed, expected):
    clock, timeouts = iter([0, 0, elapsed]), []
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: next(clock))
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)

    def run(argv, **kwargs):
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1 if len(timeouts) == 1 else 0, "", "ECONNRESET")

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert timeouts == expected
    assert status.startswith("OK") is (len(expected) == 2)


@pytest.mark.skipif(os.name == "nt", reason="unsupported strict POSIX lane")
def test_posix_cannot_claim_confirmed_tree_exit(tmp_path):
    with pytest.raises(ValueError, match="requires a Windows Job"):
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows adapter")
def test_a_cli_exit_124_is_not_a_proven_owner_timeout(monkeypatch, tmp_path):
    def run(argv, **kwargs):
        assert kwargs["require_tree_exit"] is True
        return installer_run.InstallerRun("timed out (deadline hit during build)", 124, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    result = update_acquisition._run_update(["fixture"], cwd=Path(tmp_path), timeout=1)
    assert result.returncode == 124
