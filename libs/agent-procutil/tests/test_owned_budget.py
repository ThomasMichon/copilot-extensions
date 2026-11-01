"""Aggregate execution and settlement clocks, without production effects."""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from agent_procutil import owned_process


@pytest.mark.parametrize("timeout,reserve", [(0, 1), (3, 3), (4, float("nan")), (4, -1)])
def test_invalid_budget_refuses_before_launch(timeout, reserve):
    with pytest.raises(ValueError):
        OwnedExecutionBudget.from_timeout(timeout, cleanup_reserve=reserve)


def test_expired_execution_budget_refuses_without_starting_child(tmp_path):
    target = tmp_path / "never-created"
    budget = OwnedExecutionBudget(time.monotonic() + 1, 2)
    settled = []
    with pytest.raises(subprocess.TimeoutExpired):
        run_caller_owned(
            [sys.executable, "-c", f"open({str(target)!r},'w').close()"],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=budget, on_settled=lambda: settled.append(True),
        )
    assert not target.exists()
    assert settled == [True]


@pytest.mark.skipif(os.name != "nt" and sys.platform not in ("linux", "darwin"), reason="native custody")
def test_sequential_native_children_share_deadline_and_context(tmp_path):
    budget = OwnedExecutionBudget.from_timeout(5, cleanup_reserve=2)
    settled = []
    before = time.monotonic()
    for index in range(2):
        result = run_caller_owned(
            [sys.executable, "-c", "import os,time;time.sleep(.1);print(os.environ['BUDGET_FIXTURE'])"],
            cwd=tmp_path, env={**os.environ, "BUDGET_FIXTURE": str(index)},
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=budget, on_settled=lambda: settled.append(time.monotonic()),
        )
        assert result.stdout.strip() == str(index)
    with pytest.raises(subprocess.TimeoutExpired):
        run_caller_owned(
            [sys.executable, "-c", "import time;time.sleep(60)"],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=budget, on_settled=lambda: settled.append(time.monotonic()),
        )
    assert len(settled) == 3
    assert time.monotonic() < budget.deadline
    assert time.monotonic() - before < 5


def test_windows_cleanup_waits_use_one_remaining_deadline_and_retain_custody(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    budget = OwnedExecutionBudget(101.0, .25)
    waits = []

    def retire(job, timeout):
        waits.append(timeout)
        clock[0] = 101.1

    process = SimpleNamespace(
        stdin=None, stdout=None, stderr=None, poll=lambda: 0,
        wait=lambda **kwargs: pytest.fail("expired cleanup must not renew process wait"),
        retire_job=retire,
    )
    job = object()
    with pytest.raises(OwnedCleanupError) as caught:
        owned_process._communicate_windows_bounded(
            process, job, input_text=None, deadline=100.75,
            argv=["fixture"], timeout=10, capture_limits=(32, 32), budget=budget,
        )
    assert waits == [1.0]
    assert caught.value.process is process and caught.value.job is job


def test_borrowed_scope_refuses_unrelated_parent_before_creation(monkeypatch):
    from agent_procutil import _caller_owned

    monkeypatch.setattr(_caller_owned, "_require_posix_support", lambda: None)
    monkeypatch.setattr(owned_process, "_require_posix_execution_support", lambda: None)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getppid", lambda: 41)
    monkeypatch.setattr(os, "getpgrp", lambda: 42, raising=False)
    monkeypatch.setattr(os, "getsid", lambda pid: 41, raising=False)
    monkeypatch.setattr(owned_process, "spawn_sync_in_kill_on_close_job",
                        lambda *a, **k: pytest.fail("foreign scope must refuse before launch"))
    with pytest.raises(RuntimeError, match="enclosing parent's native group"):
        run_caller_owned(
            ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            budget=OwnedExecutionBudget.from_timeout(3, cleanup_reserve=1),
            borrow_parent_scope=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="native Windows suspended startup")
@pytest.mark.parametrize("phase", ["before-create", "before-resume"])
def test_native_setup_cannot_admit_execution_after_individual_cutoff(tmp_path, monkeypatch, phase):
    from agent_procutil import _windows_caller_owned as windows

    kernel = windows._api()
    calls = []
    create, resume = kernel.CreateProcessW, kernel.ResumeThread

    def observed_create(*args):
        calls.append("create")
        return create(*args)

    def observed_resume(*args):
        calls.append("resume")
        return resume(*args)

    monkeypatch.setattr(kernel, "CreateProcessW", observed_create)
    monkeypatch.setattr(kernel, "ResumeThread", observed_resume)
    monkeypatch.setattr(windows, "_api", lambda: kernel)
    if phase == "before-create":
        original = windows._environment_block

        def delayed(environment):
            value = original(environment)
            time.sleep(.6)
            return value

        monkeypatch.setattr(windows, "_environment_block", delayed)
    else:
        original = windows._Process.__init__

        def delayed(process, *args):
            original(process, *args)
            time.sleep(.6)

        monkeypatch.setattr(windows._Process, "__init__", delayed)
    target = tmp_path / "must-not-execute"
    settled = []
    command = [sys.executable, "-c", f"open({str(target)!r},'w').close()"]
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_caller_owned(
            command, timeout=.5, max_stdout_bytes=4096, max_stderr_bytes=4096,
            budget=OwnedExecutionBudget.from_timeout(5, cleanup_reserve=2),
            on_settled=lambda: settled.append(True),
        )
    assert caught.value.cmd == command and caught.value.timeout == .5
    assert not target.exists()
    assert calls == ([] if phase == "before-create" else ["create"])
    assert settled == [True]
