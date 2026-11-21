"""Controls for the opt-in original-child diagnostic, not native acceptance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from agent_procutil import no_window_kwargs

from hook_child_observer import (
    ChildObserver,
    CommunicationObservation,
    PostExitChildObserver,
    Sample,
    finish_observations,
    observe_communication,
    observed_subprocess,
)


@pytest.mark.guard
@pytest.mark.parametrize("observer_failure", ["none", "factory", "emit"])
@pytest.mark.parametrize("outcome", ["returned", "timeout", "error"])
def test_communication_preserves_result_or_exception_identity(
    observer_failure: str, outcome: str,
) -> None:
    process = cast(subprocess.Popen[Any], object())
    value = object()
    failure = (
        subprocess.TimeoutExpired(["private-command"], 10)
        if outcome == "timeout" else OSError("private-error")
    )
    observations: list[Sample] = []

    def call() -> object:
        if outcome != "returned":
            raise failure
        return value

    def factory(selected: subprocess.Popen[Any]) -> ChildObserver:
        assert selected is process
        if observer_failure == "factory":
            raise RuntimeError("private-factory-error")
        observer = ChildObserver(process)
        observer.stop.set()
        return observer

    def emit(row: Sample) -> None:
        observations.append(row)
        if observer_failure == "emit":
            raise RuntimeError("private-emit-error")

    pending: list[CommunicationObservation] = []
    try:
        if outcome == "returned":
            assert observe_communication(process, call, pending, factory=factory) is value
        else:
            with pytest.raises(type(failure)) as caught:
                observe_communication(process, call, pending, factory=factory)
            assert caught.value is failure
        assert not observations
        assert all(record.observer is None or record.observer.stop.is_set() for record in pending)
    finally:
        finish_observations(pending, emit)
    assert len(observations) == 1
    assert observations[0]["outcome"] == outcome
    assert "private" not in json.dumps(observations)


@pytest.mark.guard
@pytest.mark.parametrize("failure_stage", ["none", "sample", "close"])
def test_child_observer_preserves_errors_and_closes_exact_sampler(failure_stage: str) -> None:
    closed: list[bool] = []
    ready = threading.Event()

    class Native:
        def sample(self) -> Sample:
            ready.set()
            if failure_stage == "sample":
                raise OSError("private-error")
            return {"process_state": "live", "kernel_ticks": 1, "user_ticks": 2}

        def close(self) -> None:
            closed.append(True)
            if failure_stage == "close":
                raise OSError("private-close-error")

    native = Native()
    observer = ChildObserver(cast(subprocess.Popen[Any], object()), factory=lambda _: native)
    observer.start()
    assert ready.wait(2)
    result = observer.finish("timeout")
    assert not observer.worker.is_alive()
    assert closed == [True]
    assert result["outcome"] == "timeout"
    assert result["collector_settled"] is (failure_stage != "close")
    assert "private" not in json.dumps(result)
    assert observer.unsettled == ([native] if failure_stage == "close" else [])


@pytest.mark.guard
def test_child_observer_does_not_wait_indefinitely_for_a_stuck_collector() -> None:
    entered, release = threading.Event(), threading.Event()

    class Native:
        def sample(self) -> Sample:
            entered.set()
            assert release.wait(3)
            return {"process_state": "unknown"}

        def close(self) -> None:
            pass

    observer = ChildObserver(cast(subprocess.Popen[Any], object()), factory=lambda _: Native())
    observer.JOIN_SECONDS = .01
    observer.start()
    try:
        assert entered.wait(2)
        assert observer.finish("timeout")["collector_settled"] is False
        assert observer.worker.is_alive()
    finally:
        release.set()
        observer.worker.join(2)
    assert not observer.worker.is_alive()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Actual native child handle and wait APIs")
@pytest.mark.parametrize("timeout", [False, True])
@pytest.mark.parametrize("observer_failure", [False, True])
def test_native_child_observer_preserves_original_run_and_settles_child(
    timeout: bool, observer_failure: bool, tmp_path: Path,
) -> None:
    from hook_child_observer import WindowsChildSample

    rows: list[Sample] = []
    originals: list[subprocess.Popen[Any]] = []

    def factory(process: subprocess.Popen[Any]) -> ChildObserver:
        originals.append(process)

        def sample(selected: subprocess.Popen[Any]) -> WindowsChildSample:
            assert selected is process
            if observer_failure:
                raise OSError("private-native-error")
            return WindowsChildSample(selected)

        return ChildObserver(process, factory=sample)

    def emit(row: Sample) -> None:
        rows.append(row)
        if observer_failure:
            raise RuntimeError("private-emitter-error")

    proxy = observed_subprocess(emit, factory=factory)
    assert subprocess.run is not proxy.run
    command = [
        str(getattr(sys, "_base_executable", sys.executable)), "-c",
        "import os,sys,time; sys.stderr.write(str(os.getpid())); sys.stderr.flush(); "
        "data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data); sys.stdout.buffer.flush(); time.sleep(.8)",
    ]
    kwargs = {
        "input": b"private-payload", "capture_output": True, "timeout": .6 if timeout else 3,
        "cwd": tmp_path, **no_window_kwargs(),
    }
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            proxy.run(command, **kwargs)
        assert caught.value.cmd is command
        assert caught.value.timeout == .6
        # A deadline can expire before the child's first write.
        if caught.value.output is not None:
            assert b"private-payload".startswith(caught.value.output)
    else:
        result = proxy.run(command, **kwargs)
        assert result.returncode == 0
        assert result.stdout == b"private-payload"
        assert result.stderr == str(originals[0].pid).encode("ascii")
    assert len(originals) == len(rows) == 1
    assert originals[0].poll() is not None
    assert rows[0]["outcome"] == ("timeout" if timeout else "returned")
    assert rows[0]["collector_settled"] is True
    assert "private" not in json.dumps(rows)
    assert str(tmp_path) not in json.dumps(rows)
    if not observer_failure:
        assert rows[0]["errors"] == []
        samples = rows[0]["samples"]
        assert isinstance(samples, list) and samples
        assert any(row.get("threads_observed", 0) > 0 for row in samples)
        assert all(
            row.get("wait_callbacks_settled", True) for row in samples
        )
        waits = [wait for row in samples for wait in row.get("waits", [])]
        assert any(wait.get("status_code") == 0 for wait in waits), json.dumps(rows)


@pytest.mark.guard
def test_native_packet_observer_ignores_unrelated_nonparametrized_item() -> None:
    from unittest.mock import Mock

    from test_hook_packet_receiver_diagnostics import NativeChildPacketObserver

    item = cast(pytest.Function, Mock(
        spec=pytest.Function, path=Path("test_unrelated.py"), originalname="test_unrelated",
    ))
    wrapper = NativeChildPacketObserver().pytest_runtest_call(item)
    assert next(wrapper) is None
    with pytest.raises(StopIteration):
        next(wrapper)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Actual post-cleanup Windows process CPU counters")
@pytest.mark.parametrize("timeout", [False, True])
def test_native_post_exit_observer_never_queries_a_live_child(
    timeout: bool, tmp_path: Path,
) -> None:
    from hook_child_observer import WindowsChildSample

    rows: list[Sample] = []
    queried: list[bool] = []
    processes: list[subprocess.Popen[Any]] = []
    observers: list[PostExitChildObserver] = []

    def observer(process: subprocess.Popen[Any]) -> ChildObserver:
        processes.append(process)

        def sample(selected: subprocess.Popen[Any]) -> WindowsChildSample:
            assert selected is process
            queried.append(process.poll() is not None)
            assert queried[-1], "Metadata was requested before original child cleanup"
            return WindowsChildSample(process)

        observer = PostExitChildObserver(process, factory=sample)
        observers.append(observer)
        return observer

    proxy = observed_subprocess(rows.append, factory=observer)
    command = [
        str(getattr(sys, "_base_executable", sys.executable)), "-c",
        "import time; time.sleep(60)" if timeout else "print('fixture')",
    ]
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            proxy.run(command, capture_output=True, timeout=.2, cwd=tmp_path, **no_window_kwargs())
        assert caught.value.timeout == .2
    else:
        result = proxy.run(command, capture_output=True, timeout=3, cwd=tmp_path, **no_window_kwargs())
        assert result.returncode == 0 and result.stdout.strip() == b"fixture"
    assert len(rows) == len(processes) == 1
    assert processes[0].poll() is not None
    assert rows[0]["collector_settled"] is True and rows[0]["errors"] == []
    assert not observers[0].worker.is_alive()
    assert (observers[0].worker.ident is not None) is timeout
    samples = cast(list[Sample], rows[0]["samples"])
    assert queried == ([True] if timeout else [])
    if timeout:
        assert len(samples) == 1 and samples[0]["process_state"] == "exited"
        assert samples[0]["kernel_ticks"] is not None and samples[0]["user_ticks"] is not None
        assert "waits" not in samples[0]
    else:
        assert samples == []


@pytest.mark.guard
def test_post_exit_observer_refuses_a_live_child_without_querying_it() -> None:
    from types import SimpleNamespace

    def unexpected(process: subprocess.Popen[Any]) -> Any:
        pytest.fail("A live child must not receive post-exit queries")

    observer = PostExitChildObserver(
        cast(subprocess.Popen[Any], SimpleNamespace(poll=lambda: None)), factory=unexpected,
    )
    result = observer.finish("timeout")
    assert result["collector_settled"] is True and result["samples"] == []
    assert result["errors"] == [{"stage": "post-exit", "process_state": "live"}]


@pytest.mark.guard
def test_post_exit_observer_reports_failure_to_start_its_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> None:
        raise RuntimeError("private-start-error")

    observer = PostExitChildObserver(cast(subprocess.Popen[Any], object()))
    monkeypatch.setattr(observer.worker, "start", unavailable)
    result = observer.finish("timeout")
    assert result["collector_settled"] is True and result["samples"] == []
    assert result["errors"] == [{"stage": "start", "winerror": None}]


@pytest.mark.guard
def test_observed_namespace_preserves_an_explicit_popen_prohibition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = subprocess.Popen
    proxy = observed_subprocess(lambda row: pytest.fail("A prohibited child was observed"))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic process prohibition")

    with monkeypatch.context() as patch:
        patch.setattr(proxy, "Popen", forbidden)
        assert subprocess.Popen is forbidden
        assert proxy.Popen is forbidden
    assert subprocess.Popen is original and proxy.Popen is original


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.skipif(os.name != "nt", reason="Actual native timeout termination ordering")
def test_native_timeout_kills_before_joining_a_blocked_collector(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    exit_before_join: list[bool] = []
    rows: list[Sample] = []
    observers: list[ChildObserver] = []

    class Native:
        def sample(self) -> Sample:
            entered.set()
            assert release.wait(5)
            return {"process_state": "unknown"}

        def close(self) -> None:
            pass

    class Observer(ChildObserver):
        def start(self) -> None:
            super().start()
            assert entered.wait(2)

        def finish(self, outcome: str) -> Sample:
            exit_before_join.append(self.process.poll() is not None)
            release.set()
            return super().finish(outcome)

    def factory(process: subprocess.Popen[Any]) -> ChildObserver:
        observer = Observer(process, factory=lambda _: Native())
        observers.append(observer)
        return observer

    try:
        proxy = observed_subprocess(rows.append, factory=factory)
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            proxy.run(
                [str(getattr(sys, "_base_executable", sys.executable)),
                 "-c", "import time; time.sleep(60)"],
                capture_output=True, timeout=.2, cwd=tmp_path, **no_window_kwargs(),
            )
        assert caught.value.timeout == .2
        assert exit_before_join == [True]
        assert len(rows) == 1 and rows[0]["outcome"] == "timeout"
        assert rows[0]["collector_settled"] is True
    finally:
        release.set()
        for observer in observers:
            observer.stop.set()
            observer.worker.join(2)
    assert all(not observer.worker.is_alive() for observer in observers)


@pytest.mark.guard
@pytest.mark.parametrize(("filename", "originalname", "case"), [
    ("test_post_tool_shell.py",
     "test_registered_prepared_cell_post_tool_uses_selected_original_owners", "read"),
    ("test_managed_session.py",
     "test_registered_file_local_write_reuses_original_guard_facts", "patch-input"),
    ("test_managed_session.py",
     "test_registered_file_local_write_reuses_original_guard_facts", "structured"),
])
def test_native_packet_observer_changes_only_the_selected_original_shell_call(
    filename: str, originalname: str, case: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    import test_hook_packet_receiver_diagnostics as packet

    native_calls: list[tuple[object, object]] = []
    observed_calls: list[tuple[object, object]] = []
    sentinel = object()

    def native(*args: object, **kwargs: object) -> object:
        native_calls.append((args, kwargs))
        return sentinel

    def observed(*args: object, **kwargs: object) -> object:
        observed_calls.append((args, kwargs))
        return sentinel

    native_namespace = SimpleNamespace(run=native)
    monkeypatch.setattr(
        packet, "observed_subprocess", lambda emit: SimpleNamespace(run=observed),
    )
    module = SimpleNamespace(subprocess=native_namespace)
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, path=Path(filename), originalname=originalname,
        callspec=SimpleNamespace(params={"case": case}), module=module,
        funcargs={"monkeypatch": monkeypatch},
    ))
    wrapper = packet.NativeChildPacketObserver().pytest_runtest_call(item)
    assert next(wrapper) is None
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "private-command"]
    assert module.subprocess.run(command, input=b"private-input", timeout=10) is sentinel
    assert module.subprocess.run(["git.exe", "status"], timeout=10) is sentinel
    assert module.subprocess.run(command, timeout=9) is sentinel
    assert observed_calls == [((command,), {"input": b"private-input", "timeout": 10})]
    assert native_calls == [
        ((["git.exe", "status"],), {"timeout": 10}), ((command,), {"timeout": 9}),
    ]
    failure = AssertionError("private-original-failure")
    with pytest.raises(AssertionError) as caught:
        wrapper.throw(failure)
    assert caught.value is failure
