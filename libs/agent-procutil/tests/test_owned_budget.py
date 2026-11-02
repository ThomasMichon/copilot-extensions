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


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_between_check_expiry_notifies_after_closing_lifetime_descriptors(monkeypatch, platform):
    from agent_procutil import _caller_owned as protocol

    before = set(protocol._lifetime_writers)
    fds = []
    settled = []
    monkeypatch.setattr(os, "name", platform)
    monkeypatch.setattr(protocol, "_require_posix_support", lambda: None)
    monkeypatch.setattr(protocol, "Path", lambda value: SimpleNamespace(
        resolve=lambda: SimpleNamespace(parents=["source", "root"]),
    ))

    def refuse(argv, **kwargs):
        fds.extend(kwargs.get("pass_fds", ()))
        fds.extend(protocol._lifetime_writers - before)
        raise owned_process._PreparationTimeout(argv, kwargs["timeout"])

    def observe():
        assert protocol._lifetime_writers == before
        for fd in fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        settled.append(True)

    monkeypatch.setattr(protocol, "_run_owned", refuse)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_caller_owned(
            ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            budget=OwnedExecutionBudget(time.monotonic() + 5, 1), on_settled=observe,
        )
    assert caught.value.cmd == ["fixture"] and caught.value.timeout == 5
    assert settled == [True]


@pytest.mark.parametrize("failed_side", ["reader", "writer"])
@pytest.mark.parametrize("outcome", ["preparation", "settled"])
def test_between_check_expiry_retains_failed_lifetime_close_without_settlement(monkeypatch, failed_side, outcome):
    from agent_procutil import _caller_owned as protocol

    fds = []
    original_pipe, original_close = os.pipe, os.close
    before_writers = set(protocol._lifetime_writers)
    local_os = SimpleNamespace(**vars(os))
    local_os.name = "posix"
    monkeypatch.setattr(protocol, "os", local_os)
    monkeypatch.setattr(protocol, "_require_posix_support", lambda: None)
    monkeypatch.setattr(protocol, "Path", lambda value: SimpleNamespace(
        resolve=lambda: SimpleNamespace(parents=["source", "root"]),
    ))
    def pipe():
        values = original_pipe()
        fds.extend(values)
        return values
    def close(fd):
        if fd == fds[0 if failed_side == "reader" else 1]:
            raise OSError("fixture lifetime close failed")
        original_close(fd)
    def refuse(argv, **kwargs):
        if outcome == "settled":
            kwargs["on_settled"]()
            return subprocess.CompletedProcess(argv, 0, '{"kind":"exit","returncode":0}', "")
        raise owned_process._PreparationTimeout(argv, kwargs["timeout"])
    monkeypatch.setattr(local_os, "pipe", pipe)
    monkeypatch.setattr(local_os, "close", close)
    monkeypatch.setattr(protocol, "_run_owned", refuse)
    settled = []
    try:
        with pytest.raises(OwnedCleanupError) as caught:
            run_caller_owned(
                ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
                budget=OwnedExecutionBudget(time.monotonic() + 5, 1),
                on_settled=lambda: settled.append(True),
            )
        failed = fds[0 if failed_side == "reader" else 1]
        assert settled == []
        assert os.fstat(failed)
        assert ("descriptor", failed) in caught.value.cleanup_resources
        if failed_side == "writer":
            assert failed in protocol._lifetime_writers
        else:
            assert failed in protocol._lifetime_readers
        with pytest.raises(OSError):
            os.fstat(fds[1 if failed_side == "reader" else 0])
    finally:
        for fd in fds:
            try:
                original_close(fd)
            except OSError:
                pass
        protocol._lifetime_writers.difference_update(set(fds) - before_writers)
        getattr(protocol, "_lifetime_readers", set()).difference_update(fds)


def test_typed_no_target_guardian_refusal_settles_after_lifetime_closure_and_retains_ask(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    from agent_procutil import SyncProcessLaunchError, _caller_owned as protocol

    scripts = Path(__file__).resolve().parents[3] / "plugins" / "agent-worktrees" / "scripts"
    loaded = {}
    for name in ("hook_client", "hook_pre_policy", "hook_policy_process"):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, name, module)
        loaded[name] = module
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(loaded["hook_policy_process"], "_watch_legacy_parent",
                        lambda stop: SimpleNamespace(join=lambda **kwargs: None))
    local_os = SimpleNamespace(**vars(os))
    local_os.name = "posix"
    monkeypatch.setattr(protocol, "os", local_os)
    monkeypatch.setattr(protocol, "_require_posix_support", lambda: None)
    failure = SyncProcessLaunchError(FileNotFoundError(2, "fixture missing cwd"))
    descriptors = []
    original_pipe = os.pipe
    before_writers, before_readers = set(protocol._lifetime_writers), set(protocol._lifetime_readers)
    def pipe():
        values = original_pipe()
        descriptors.extend(values)
        return values
    def refuse(argv, **kwargs):
        raise failure
    monkeypatch.setattr(local_os, "pipe", pipe)
    monkeypatch.setattr(protocol, "_run_owned", refuse)
    settled = []
    real_run = owned_process.run_caller_owned
    def observe(*args, **kwargs):
        original = kwargs["on_settled"]
        def closed():
            assert protocol._lifetime_writers == before_writers
            assert protocol._lifetime_readers == before_readers
            for descriptor in descriptors:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            settled.append(True)
            original()
        kwargs["on_settled"] = closed
        try:
            return real_run(*args, **kwargs)
        except SyncProcessLaunchError as error:
            assert error is failure
            raise
    monkeypatch.setattr(owned_process, "run_caller_owned", observe)
    assert loaded["hook_policy_process"].execute(
        b"{}", legacy=True,
        initial_decision={"permissionDecision": "ask", "permissionDecisionReason": "confirmed fixture"},
    ) == 0
    assert settled == [True] and len(descriptors) == 2
    assert __import__("json").loads(capsys.readouterr().out)["permissionDecision"] == "ask"


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job preparation")
@pytest.mark.parametrize("phase", ["environment", "allocation", "configuration"])
def test_native_precreation_refusal_notifies_once_after_releasing_job(monkeypatch, phase):
    from agent_procutil import _windows_caller_owned as windows

    kernel = windows._api()
    closed = []
    original_close = kernel.CloseHandle

    def close(handle):
        result = original_close(handle)
        assert result
        closed.append(True)
        return result

    def refuse(*args):
        raise OSError("fixture precreation refusal")

    monkeypatch.setattr(kernel, "CloseHandle", close)
    monkeypatch.setattr(kernel, "CreateProcessW", lambda *args: pytest.fail("refusal must not launch"))
    if phase == "environment":
        monkeypatch.setattr(windows, "_environment_block", refuse)
    elif phase == "allocation":
        monkeypatch.setattr(kernel, "CreateJobObjectW", refuse)
    else:
        monkeypatch.setattr(kernel, "SetInformationJobObject", refuse)
    settled = []

    def observe():
        assert closed == ([True] if phase == "configuration" else [])
        settled.append(True)

    with pytest.raises(OSError, match="fixture precreation refusal"):
        run_caller_owned(
            ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            budget=OwnedExecutionBudget.from_timeout(5, cleanup_reserve=1),
            on_settled=observe,
        )
    assert settled == [True]


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job preparation")
def test_precreation_failed_job_close_retains_custody_without_notifying(monkeypatch):
    from agent_procutil import _windows_caller_owned as windows

    kernel = windows._api()
    original_close = kernel.CloseHandle
    monkeypatch.setattr(kernel, "SetInformationJobObject", lambda *args: False)
    monkeypatch.setattr(kernel, "CloseHandle", lambda handle: False)
    settled = []
    with pytest.raises(OwnedCleanupError) as caught:
        run_caller_owned(
            ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            budget=OwnedExecutionBudget.from_timeout(5, cleanup_reserve=1),
            on_settled=lambda: settled.append(True),
        )
    job = caught.value.job
    try:
        assert job.handle
        assert caught.value.process is None
        assert settled == []
    finally:
        assert original_close(job.handle)
        job._handle = None


@pytest.mark.skipif(os.name != "nt", reason="native Windows preparation descriptor")
def test_precreation_failed_descriptor_close_retains_custody_without_notifying(monkeypatch):
    from agent_procutil import _windows_caller_owned as windows

    descriptors = []
    original_pipe, original_close = os.pipe, os.close
    kernel = windows._api()
    def pipe():
        values = original_pipe()
        descriptors.extend(values)
        return values
    def close(fd):
        if fd == descriptors[1]:
            raise OSError("fixture preparation descriptor close failed")
        original_close(fd)
    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(kernel, "InitializeProcThreadAttributeList", lambda *args: False)
    monkeypatch.setattr(kernel, "CreateProcessW", lambda *args: pytest.fail("preparation must not launch"))
    settled = []
    error = None
    try:
        with pytest.raises(OwnedCleanupError) as caught:
            run_caller_owned(
                ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
                on_settled=lambda: settled.append(True),
            )
        error = caught.value
        assert error.process is None and error.job.handle
        assert ("descriptor", descriptors[1]) in error.cleanup_resources
        assert settled == [] and os.fstat(descriptors[1])
        for fd in descriptors[:1] + descriptors[2:]:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        for fd in descriptors:
            try:
                original_close(fd)
            except OSError:
                pass
        if error is not None and error.job.handle:
            error.job.close()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CreateProcessW refusal")
def test_failed_native_creation_notifies_after_closing_all_preparation(tmp_path, monkeypatch):
    descriptors = []
    original_pipe = os.pipe

    def pipe():
        values = original_pipe()
        descriptors.extend(values)
        return values

    monkeypatch.setattr(os, "pipe", pipe)
    settled = []

    def observe():
        assert len(descriptors) == 6
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        settled.append(True)

    with pytest.raises(OSError) as caught:
        run_caller_owned(
            [str(tmp_path / "missing-native-fixture.exe")],
            timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            input_text="{}",
            budget=OwnedExecutionBudget.from_timeout(5, cleanup_reserve=1),
            on_settled=observe,
        )
    assert caught.value.winerror == 2
    assert settled == [True]


@pytest.mark.skipif(os.name != "nt", reason="native Windows interrupted creation")
def test_interrupted_creation_cannot_claim_known_no_target(monkeypatch):
    from agent_procutil import _windows_caller_owned as windows

    def interrupt(*args):
        raise OSError("fixture creation interrupted without native result")

    monkeypatch.setattr(windows._api(), "CreateProcessW", interrupt)
    settled = []
    with pytest.raises(OSError, match="creation interrupted"):
        run_caller_owned(
            ["fixture"], timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            budget=OwnedExecutionBudget.from_timeout(5, cleanup_reserve=1),
            on_settled=lambda: settled.append(True),
        )
    assert settled == []
