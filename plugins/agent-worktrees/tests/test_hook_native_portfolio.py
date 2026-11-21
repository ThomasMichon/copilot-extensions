"""Focused controls for the explicitly loaded full-portfolio test plugin."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import Mock

import pytest

from hook_child_observer import PostExitChildObserver, Sample, is_native_subprocess
from hook_native_portfolio import (
    FullNativeChildObserver, LateChildObserver, observe_policy_dispatch,
)


def original_item(monkeypatch: pytest.MonkeyPatch) -> pytest.Function:
    item = Mock(
        spec=pytest.Function, module=SimpleNamespace(subprocess=subprocess),
        funcargs={"monkeypatch": monkeypatch},
    )
    item.iter_markers.return_value = []
    return cast(pytest.Function, item)


def finish_report(
    observer: FullNativeChildObserver, item: pytest.Function,
    outcome: Literal["passed", "failed", "skipped"] = "passed",
) -> pytest.TestReport:
    report = pytest.TestReport(
        "synthetic-case", ("test_example.py", 0, "synthetic-case"), {},
        outcome, None, "call",
    )
    wrapper = observer.pytest_runtest_makereport(item)
    assert next(wrapper) is None
    with pytest.raises(StopIteration) as done:
        wrapper.send(report)
    assert done.value.value is report
    return report


@pytest.mark.guard
def test_full_observer_leaves_subprocess_doubles_unobserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert is_native_subprocess(subprocess)
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    assert observer.accepts(item)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", Mock())
        assert not is_native_subprocess(subprocess)
        assert not observer.accepts(item)
    assert observer.accepts(item)


@pytest.mark.guard
def test_full_observer_keeps_the_original_no_process_control_unwrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import test_native_tool_hook_transport as original

    observer = FullNativeChildObserver()
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, module=original, path=Path(original.__file__),
        originalname="test_original_guards_execute_without_any_process",
        funcargs={"monkeypatch": monkeypatch},
    ))
    wrapper = observer.pytest_runtest_call(item)
    assert next(wrapper) is None
    namespace = vars(original)["subprocess"]
    assert namespace is subprocess
    native_popen = subprocess.Popen
    original.test_original_guards_execute_without_any_process(monkeypatch, tmp_path)
    assert subprocess.Popen is namespace.Popen
    assert subprocess.Popen is not native_popen
    assert observer.rows == {}
    with pytest.raises(StopIteration):
        next(wrapper)


@pytest.mark.guard
def test_full_post_exit_mode_selects_passive_sampling_and_reports_its_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hook_native_portfolio as full

    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "post-exit")
    observer = FullNativeChildObserver()
    sentinel = SimpleNamespace(run=object())

    def proxy(emit: object, *, factory: object) -> SimpleNamespace:
        assert factory is PostExitChildObserver
        return sentinel

    monkeypatch.setattr(full, "observed_subprocess", proxy)
    assert observer.subprocess_proxy(lambda row: None) is sentinel
    item = original_item(monkeypatch)
    observer.emit(item, {
        "outcome": "timeout", "collector_settled": True,
        "samples": [{"process_state": "exited"}], "errors": [],
    })
    report = finish_report(observer, item, "failed")
    row = json.loads(report.sections[0][1].partition("=")[2])
    assert row["sampling_mode"] == "post-exit" and row["sampling_delay_ms"] is None
    assert row["test_outcome"] == "failed"


@pytest.mark.guard
def test_full_observer_refuses_an_unknown_sampling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "unknown")
    with pytest.raises(ValueError, match="Invalid native observation mode"):
        FullNativeChildObserver()


@pytest.mark.guard
def test_full_observer_rechecks_the_native_seam_after_body_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hook_native_portfolio as full

    original_result, observed_result, double_result = object(), object(), object()
    original = Mock(return_value=original_result)
    observed = Mock(return_value=observed_result)
    double = Mock(return_value=double_result)
    namespace = SimpleNamespace(run=original)
    monkeypatch.setattr(
        full, "is_native_subprocess",
        lambda value: value is namespace and namespace.run is original,
    )
    def proxy(emit: object, *, factory: object) -> SimpleNamespace:
        assert factory is LateChildObserver
        return SimpleNamespace(run=observed)

    monkeypatch.setattr(full, "observed_subprocess", proxy)
    module = SimpleNamespace(subprocess=namespace)
    item = cast(pytest.Function, Mock(
        spec=pytest.Function, module=module,
        funcargs={"monkeypatch": monkeypatch},
    ))
    observer = FullNativeChildObserver()
    wrapper = observer.pytest_runtest_call(item)
    assert next(wrapper) is None
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "synthetic"]
    assert module.subprocess.run(command, timeout=10) is observed_result
    namespace.run = double
    assert module.subprocess.run(command, timeout=10) is double_result
    namespace.run = original
    assert module.subprocess.run(command, timeout=10) is observed_result
    double.assert_called_once_with(command, timeout=10)
    assert observed.call_count == 2 and original.call_count == 0
    with pytest.raises(StopIteration):
        next(wrapper)


@pytest.mark.guard
def test_full_observer_reports_after_original_capture_and_keeps_test_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    observer.emit(item, {
        "outcome": "timeout", "collector_settled": True, "samples": [], "errors": [],
    })
    assert capsys.readouterr().out == ""
    report = finish_report(observer, item)
    assert report.outcome == "passed"
    assert len(report.sections) == 1
    value = json.loads(report.sections[0][1].partition("=")[2])
    assert value["outcome"] == "timeout"
    assert value["sampling_delay_ms"] == 8000
    assert value["test_outcome"] == "passed"
    assert observer.rows == {}


@pytest.mark.guard
def test_full_observer_cancels_before_any_native_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = threading.Event()
    waits: list[float | None] = []

    def unexpected(process: subprocess.Popen[Any]) -> Any:
        pytest.fail("A child completed before arming must not receive native queries")

    observer = LateChildObserver(cast(subprocess.Popen[Any], object()), factory=unexpected)
    original_wait = observer.stop.wait

    def wait(seconds: float | None = None) -> bool:
        waits.append(seconds)
        waiting.set()
        return original_wait(seconds)

    monkeypatch.setattr(observer.stop, "wait", wait)
    observer.start()
    try:
        assert waiting.wait(2)
    finally:
        result = observer.finish("returned")
    assert waits == [8.0]
    assert result == {
        "outcome": "returned", "collector_settled": True, "samples": [], "errors": [],
    }


@pytest.mark.guard
def test_full_observer_samples_a_child_that_outlives_the_arm_delay() -> None:
    entered = threading.Event()
    closed: list[bool] = []

    class Native:
        def sample(self) -> Sample:
            entered.set()
            return {"process_state": "live"}

        def close(self) -> None:
            closed.append(True)

    observer = LateChildObserver(
        cast(subprocess.Popen[Any], object()), factory=lambda process: Native(),
    )
    observer.INITIAL_DELAY = .02
    observer.start()
    try:
        assert entered.wait(2)
    finally:
        result = observer.finish("timeout")
    assert result["collector_settled"] is True and result["errors"] == []
    assert closed == [True]
    samples = cast(list[Sample], result["samples"])
    assert len(samples) == 1 and samples[0]["process_state"] == "live"


@pytest.mark.guard
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("guard_calls", [1, 17])
def test_policy_observer_preserves_original_calls_results_and_exceptions(
    fails: bool, guard_calls: int,
) -> None:
    event = {"private-payload": object()}
    result: dict[str, Any] = {"permissionDecision": [], "permissionDecisionReason": "private-reason"}
    error = RuntimeError("private-error")
    calls: list[object] = []
    rows: list[Sample] = []
    client, helper, pre = object(), object(), object()

    def decide(selected: object, *, flag: object) -> object:
        calls.append(selected)
        assert selected is event and flag is helper
        return helper

    guard = SimpleNamespace(decide=decide)
    policy = SimpleNamespace(
        hook_client=client, pre=pre,
        _module=lambda name: guard if name == "cross_repo_guard.py" else helper,
    )
    deadline = time.time() + .5

    def original(selected: Any, payload: dict[str, Any], limit: float | None) -> dict[str, Any]:
        assert selected.hook_client is client and selected.pre is pre
        assert payload is event and limit == deadline
        assert selected._module("hook_pre_policy.py") is helper
        for _ in range(guard_calls):
            assert selected._module("cross_repo_guard.py").decide(event, flag=helper) is helper
        if fails:
            raise error
        return result

    def emit(row: Sample) -> None:
        rows.append(row)
        raise RuntimeError("private-report-error")

    if fails:
        with pytest.raises(RuntimeError) as caught:
            observe_policy_dispatch(original, policy, event, deadline, emit)
        assert caught.value is error
    else:
        assert observe_policy_dispatch(original, policy, event, deadline, emit) is result
        assert rows[0]["decision"] == "other"
    assert calls == [event] * guard_calls
    assert len(rows) == 1 and rows[0]["outcome"] == ("error" if fails else "returned")
    assert len(cast(list[Sample], rows[0]["guards"])) == min(guard_calls, 16)
    assert rows[0].get("guards_truncated", False) is (guard_calls > 16)
    assert "private" not in json.dumps(rows)


@pytest.mark.guard
def test_full_policy_observer_reports_original_failure_without_child_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import native_tool_policy

    result: dict[str, Any] = {"permissionDecision": "ask"}
    original = Mock(return_value=result)
    monkeypatch.setattr(native_tool_policy, "dispatch_pre", original)
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    cast(Any, item).path = SimpleNamespace(name="test_native_pre_policy.py")
    cast(Any, item).originalname = "test_registered_full_policy_composition"
    observer.prepare(item, monkeypatch)
    policy = SimpleNamespace(hook_client=object(), pre=object(), _module=lambda name: None)
    assert native_tool_policy.dispatch_pre(policy, {}, time.time() + .5) is result
    report = finish_report(observer, item, "failed")
    assert original.call_count == 1
    assert len(report.sections) == 1
    row = json.loads(report.sections[0][1].partition("=")[2])
    assert row["test_outcome"] == "failed" and row["decision"] == "ask"
    assert row["outcome"] == "returned"
    assert observer.policy_rows == {}


@pytest.mark.guard
@pytest.mark.parametrize("invalid", [False, True])
def test_full_observer_bounds_reporting_without_masking_original_failure(
    invalid: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = FullNativeChildObserver()
    observer.MAX_ROWS = 2
    item = original_item(monkeypatch)
    for _ in range(3):
        observer.emit(item, {"outcome": "returned", **({"private": object()} if invalid else {})})
    report = finish_report(observer, item, "failed")
    assert report.outcome == "failed"
    assert observer.rows == {} and observer.truncated == set()
    assert "private" not in report.sections[0][1]
    if invalid:
        assert report.sections[0][1] == "native_child_observation_report_error=true"
    else:
        assert report.sections[0][1].count("native_child_observation=") == 2
        assert report.sections[0][1].endswith("native_child_observation_truncated=true")


@pytest.mark.guard
@pytest.mark.parametrize("original", [0, 1, 3])
def test_full_observation_never_accepts_or_masks_an_original_error(original: int) -> None:
    lines: list[str] = []
    session = cast(pytest.Session, SimpleNamespace(
        exitstatus=original,
        config=SimpleNamespace(pluginmanager=SimpleNamespace(
            getplugin=lambda name: SimpleNamespace(write_line=lines.append),
        )),
    ))
    observer = FullNativeChildObserver()
    observer.pytest_sessionfinish(session, original)
    assert session.exitstatus == (1 if original == 0 else original)
    value = json.loads(lines[0].partition("=")[2])
    assert value == {
        "diagnostic_only": True, "observed_calls": 0, "original_exitstatus": original,
    }


@pytest.mark.guard
def test_full_plugin_is_explicit_and_does_not_propagate_to_nested_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import _native_portfolio_pytest as entrypoint

    registrations: list[str] = []

    def register(plugin: object, *, name: str) -> None:
        registrations.append(name)

    config = cast(pytest.Config, SimpleNamespace(
        pluginmanager=SimpleNamespace(register=register),
        option=SimpleNamespace(reportchars="f"),
    ))
    monkeypatch.setenv("PYTEST_PLUGINS", "_native_portfolio_pytest,unrelated_plugin")
    entrypoint.pytest_configure(config)
    assert os.environ["PYTEST_PLUGINS"] == "unrelated_plugin"
    assert config.option.reportchars == "fP"
    assert registrations == ["full-native-child-observer", "full-native-receiver-observer"]
