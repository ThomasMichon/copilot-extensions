"""Only confirmed Windows Job cleanup can license payload timeout replay."""

import os
import subprocess
from pathlib import Path
from typing import Any, NoReturn

import pytest

from agent_worktrees import installer_run, reconcile, update_acquisition

pytestmark = pytest.mark.guard


@pytest.mark.skipif(os.name != "nt", reason="Windows Job process contract")
@pytest.mark.real_installer_processes
@pytest.mark.parametrize("failure", [None, "termination", "drain", "read"])
def test_strict_job_return_requires_confirmation_and_reaping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None,
) -> None:
    seen: dict[str, Any] = {}
    order: list[str] = []

    class Process:
        returncode = 0

    class Job:
        handle: int | None = 123

        def terminate_and_wait(self, timeout: float) -> None:
            order.append("confirm")
            if failure == "termination":
                raise OSError("cannot query owned Job")
            self.handle = None

        def close(self) -> None:
            order.append("close")

    def spawn(argv: list[str], **kwargs: Any) -> tuple[Process, Job]:
        seen.update(kwargs)
        return Process(), Job()

    class Capture:
        def __init__(self, process: Process, **kwargs: Any) -> None:
            pass

        def wait(self, timeout: float, **kwargs: Any) -> None:
            order.append("wait")
            assert kwargs["wait_for_eof"] is False
            if failure == "read":
                raise OSError("post-launch pipe sharing failure")

        def finish(self, timeout: float, *, stop_at_idle: bool = False) -> None:
            order.append("finish")
            assert stop_at_idle, "the native Job is already confirmed empty"
            if failure == "drain":
                raise subprocess.TimeoutExpired("fixture", timeout)

        def deliver(self) -> None:
            order.append("deliver")

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(installer_run, "SyncProcessOutput", Capture)
    def call() -> installer_run.InstallerRun:
        return installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, log=lambda line: None,
            require_tree_exit=True,
        )
    if failure:
        expected = "verified cleanup" if failure == "read" else "cleanup unverified"
        with pytest.raises(installer_run.InstallerCleanupError, match=expected):
            call()
    else:
        assert call().status == "OK"
    assert seen["require_job"] is True
    assert seen["allow_breakaway"] is False
    assert seen["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW")
    assert order[:2] == ["wait", "confirm"]
    assert "close" not in order
    assert ("finish" in order) is (failure != "termination")
    assert ("deliver" in order) is (failure not in ("termination", "drain"))


def test_cleanup_failure_never_retries_the_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    calls: list[list[str]] = []

    def fail(argv: list[str], **kwargs: Any) -> NoReturn:
        calls.append(argv)
        raise installer_run.InstallerCleanupError("owned Job still has live processes")

    monkeypatch.setattr(update_acquisition, "_run_update", fail)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert status.startswith("payload cleanup failed")
    assert len(calls) == 1


def test_activity_observation_failure_never_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    calls: list[dict[str, Any]] = []

    def fail(argv: list[str], **kwargs: Any) -> NoReturn:
        calls.append(kwargs)
        raise installer_run.InstallerActivityError("native accounting unavailable after cleanup")

    monkeypatch.setattr(update_acquisition, "_run_update", fail)
    assert update_acquisition.acquire("agent-example", "m", "fixture").startswith(
        "payload activity observation failed",
    )
    assert len(calls) == 1


@pytest.mark.parametrize("windows,installed", [(True, True), (True, False), (False, True)])
def test_native_activity_is_only_installed_windows_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, windows: bool, installed: bool,
) -> None:
    monkeypatch.setattr(update_acquisition, "_WINDOWS", windows)
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: 10)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path if installed else None)
    calls: list[dict[str, Any]] = []

    def run(argv: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    monkeypatch.setattr(update_acquisition, "_run_install", run)
    assert update_acquisition.acquire("agent-example", "m", "fixture").startswith("OK")
    assert calls[0].get("native_write_deadline") == (220 if windows and installed else None)
    assert calls[0]["timeout"] == 120


@pytest.mark.skipif(os.name != "nt", reason="Windows native spawn errors")
@pytest.mark.real_installer_processes
@pytest.mark.parametrize("code", [5, 32, 193])
def test_strict_spawn_retains_the_native_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int,
) -> None:
    error = OSError("localized spawn failure")
    setattr(error, "winerror", code)

    def fail(argv: list[str], **kwargs: Any) -> NoReturn:
        raise error

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fail)
    with pytest.raises(OSError) as raised:
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )
    assert raised.value is error


@pytest.mark.skipif(os.name != "nt", reason="Windows strict launch refusal")
@pytest.mark.real_installer_processes
def test_containment_refusal_is_not_reported_as_an_executed_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    error = RuntimeError("Job assignment refused before child resume")

    def fail(argv: list[str], **kwargs: Any) -> NoReturn:
        raise error

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fail)
    with pytest.raises(installer_run.InstallerLaunchError) as raised:
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("environment", [{}, {"AUTH_CONTEXT_FIXTURE": "child-only"}])
def test_windows_update_keeps_the_exact_supplied_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: dict[str, str],
) -> None:
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)

    def run(argv: list[str], **kwargs: Any) -> installer_run.InstallerRun:
        assert kwargs["env"] is environment
        return installer_run.InstallerRun("OK", 0, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    assert update_acquisition._run_update(
        ["fixture"], cwd=tmp_path, timeout=1, env=environment,
    ).returncode == 0


@pytest.mark.parametrize("refusal", ["", "HTTP 403 authentication required", "[WinError 5] localized"])
def test_only_quiescent_nonrefused_timeout_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, refusal: str,
) -> None:
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) == 1:
            raise update_acquisition._QuiescentPayloadTimeout(argv, 1, output=refusal)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert status.startswith("OK") is (not refusal)
    assert len(calls) == (1 if refusal else 2)


@pytest.mark.parametrize("elapsed,expected", [(209.5, [120, 0.5]), (211, [120])])
def test_windows_budget_reserves_the_cleanup_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, elapsed: float, expected: list[float],
) -> None:
    clock = iter([0.0, 0.0, elapsed])
    timeouts: list[float] = []
    deadlines: list[float] = []
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: next(clock))
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        timeouts.append(kwargs["timeout"])
        deadlines.append(kwargs["native_write_deadline"])
        return subprocess.CompletedProcess(argv, 1 if len(timeouts) == 1 else 0, "", "ECONNRESET")

    monkeypatch.setattr(update_acquisition, "_run_update", run)
    status = update_acquisition.acquire("agent-example", "m", "fixture")
    assert timeouts == expected
    assert deadlines == [210] * len(expected)
    assert status.startswith("OK") is (len(expected) == 2)


@pytest.mark.skipif(os.name != "nt", reason="strict Windows write deadline")
def test_expired_absolute_budget_never_spawns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import time

    monkeypatch.setattr(
        installer_run, "_spawn",
        lambda *args, **kwargs: pytest.fail("expired budget launched a child"),
    )
    with pytest.raises(installer_run.InstallerLaunchError, match="before launch"):
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=120,
            require_tree_exit=True, native_write_deadline=time.monotonic() - 1,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX observation prerequisite")
def test_posix_strict_execution_requires_native_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import owned_process

    def unsupported() -> NoReturn:
        raise RuntimeError("native observation unavailable")

    monkeypatch.setattr(owned_process, "require_caller_owned_support", unsupported)
    monkeypatch.setattr(
        owned_process, "spawn_sync_in_kill_on_close_job",
        lambda *args, **kwargs: pytest.fail("unavailable observation launched a target"),
    )
    with pytest.raises(RuntimeError, match="native observation unavailable"):
        installer_run.run_installer(
            ["fixture"], cwd=tmp_path, env={}, deadline_s=1, require_tree_exit=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows adapter")
def test_a_cli_exit_124_is_not_a_proven_owner_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def run(argv: list[str], **kwargs: Any) -> installer_run.InstallerRun:
        assert kwargs["require_tree_exit"] is True
        return installer_run.InstallerRun("timed out (deadline hit during build)", 124, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    result = update_acquisition._run_update(["fixture"], cwd=Path(tmp_path), timeout=1)
    assert result.returncode == 124
