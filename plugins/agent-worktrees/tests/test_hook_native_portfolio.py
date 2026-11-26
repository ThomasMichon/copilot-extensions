"""Focused controls for the explicitly loaded full-portfolio test plugin."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

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
    wrapper = observer.pytest_runtest_makereport(
        item, pytest.CallInfo.from_call(lambda: None, "call"),
    )
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
    assert len(report.sections) == 2


@pytest.mark.guard
def test_full_observer_refuses_an_unknown_sampling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "unknown")
    with pytest.raises(ValueError, match="Invalid native observation mode"):
        FullNativeChildObserver()


@pytest.mark.guard
@pytest.mark.parametrize("enabled", [False, True])
def test_notification_observer_registration_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    import hook_native_portfolio as full

    monkeypatch.delenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", raising=False)
    if enabled:
        monkeypatch.setenv("AGENT_WORKTREES_NOTIFICATION_DIAGNOSTIC", "1")
    else:
        monkeypatch.delenv("AGENT_WORKTREES_NOTIFICATION_DIAGNOSTIC", raising=False)
    names: list[str] = []
    config = cast(pytest.Config, SimpleNamespace(pluginmanager=SimpleNamespace(
        register=lambda plugin, *, name: names.append(name),
    )))
    full.register(config)
    assert names == [
        "full-native-child-observer", "full-native-receiver-observer",
        *(["full-native-notification-observer"] if enabled else []),
    ]


@pytest.mark.guard
def test_code_mode_requires_a_runner_owned_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "code-stack")
    monkeypatch.delenv("AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM", raising=False)
    with pytest.raises(ValueError, match="runner-owned selection claim"):
        FullNativeChildObserver()


@pytest.mark.guard
def test_code_selection_claim_survives_real_suite_isolation_across_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "code_observer_environment_control", root / "tools" / "plugin_test_environment.py",
    )
    assert spec and spec.loader
    environment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(environment)
    selection = tmp_path / "selection"
    selection.mkdir()
    claim = selection / "selected"
    base = {
        "AGENT_WORKTREES_NATIVE_OBSERVATION_MODE": "code-stack",
        "AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM": str(claim),
        "PYTEST_PLUGINS": "_native_portfolio_pytest",
    }
    results: list[bool] = []
    for step in ("first", "second"):
        isolated = environment.isolated_environment(
            base, tmp_path / step, allow_explicit_tiers=True,
        )
        assert all(isolated[key] == value for key, value in base.items())
        with monkeypatch.context() as patch:
            for key, value in base.items():
                patch.setenv(key, isolated[key])
            observer = FullNativeChildObserver()
            assert observer.code_claim is not None
            results.append(observer.code_claim())
    assert results == [True, False]


def code_setup_steps() -> list[dict[str, Any]]:
    import yaml

    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "prepared-membership-native.yml").read_text(
            encoding="utf-8",
        ),
    )
    selected: list[dict[str, Any]] = []
    for name in ("prepared-write-windows", "prepared-membership-windows"):
        job = workflow["jobs"][name]
        assert all("runner." not in str(value) for value in job["env"].values())
        steps = [
            step for step in job["steps"]
            if step.get("name") == "Prepare the diagnostic-only code selection boundary"
        ]
        assert len(steps) == 1
        assert steps[0]["if"] == (
            "${{ inputs.full_native_diagnostics == true && inputs.native_observation_mode == 'code-stack' }}"
        )
        selected.extend(steps)
    assert selected[0]["run"] == selected[1]["run"]
    return selected


@pytest.mark.guard
def test_code_claim_uses_supported_step_context_not_runner_in_job_environment() -> None:
    code_setup_steps()


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name != "nt", reason="Actual PowerShell runner step and GITHUB_ENV")
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_native_code_claim_setup_exports_one_worker_selection(
    shell: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    output = tmp_path / "github-env"
    environment = dict(os.environ, RUNNER_TEMP=str(tmp_path), GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
                       GITHUB_ENV=str(output))
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", code_setup_steps()[0]["run"]],
        env=environment, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    key, separator, value = output.read_text(encoding="utf-8-sig").strip().partition("=")
    assert key == "AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM" and separator
    assert Path(value) == tmp_path / "prepared-native-code-123-1" / "selected"
    assert Path(value).parent.is_dir() and not Path(value).exists()
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "code-stack")
    monkeypatch.setenv(key, value)
    first, second = FullNativeChildObserver(), FullNativeChildObserver()
    assert first.code_claim is not None and second.code_claim is not None
    assert first.code_claim() is True and second.code_claim() is False


@pytest.mark.guard
@pytest.mark.parametrize("original", [
    "test_registered_file_local_write_reuses_original_guard_facts",
    "test_registered_prepared_cell_membership_read_requires_complete_native_proof",
    "test_native_timeout_kills_before_joining_a_blocked_collector",
])
def test_code_mode_only_wraps_the_declared_original_case_family(
    original: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hook_native_portfolio as full

    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "code-stack")
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM", str(tmp_path / "selected"))
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    item.path, item.originalname = Path("test_managed_session.py"), original
    observer.prepare(item, monkeypatch)
    calls: list[dict[str, Any]] = []

    def proxy(emit: object, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(full, "observed_subprocess", proxy)
    observer.subprocess_proxy(lambda row: None)
    if original in {
        "test_registered_file_local_write_reuses_original_guard_facts",
        "test_registered_prepared_cell_membership_read_requires_complete_native_proof",
    }:
        assert set(calls[0]) == {"deadline_factory"}
    else:
        assert calls == [{"factory": PostExitChildObserver}]
    assert not (tmp_path / "selected").exists()


@pytest.mark.guard
def test_code_mode_leaves_setup_children_passive_without_consuming_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hook_native_portfolio as full
    from hook_child_code_observer import CodeChildObserver

    claim = tmp_path / "selected"
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "code-stack")
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM", str(claim))
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    original_run = Mock(return_value="passive")
    original_namespace = SimpleNamespace(run=original_run)
    monkeypatch.setattr(item.module, "subprocess", original_namespace)
    item.path = Path("test_managed_session.py")
    item.originalname = "test_registered_prepared_cell_membership_read_requires_complete_native_proof"
    monkeypatch.setattr(full, "is_native_subprocess", lambda value: value is original_namespace)
    calls: list[dict[str, Any]] = []
    sampled: list[bool] = []
    selections = []

    def sample_once() -> bool:
        sampled.append(True)
        return True

    def observe(argv: Any, **kwargs: Any) -> str:
        select = calls[0]["deadline_factory"]
        candidate = select(SimpleNamespace(args=argv, poll=lambda: None),
                           time.monotonic() + 1)
        assert isinstance(candidate, CodeChildObserver)
        monkeypatch.setattr(candidate, "INITIAL_DELAY", 0)
        monkeypatch.setattr(candidate, "sample_once", sample_once)
        candidate.collect()
        selections.append(candidate.selection)
        return "observed"

    def proxy(emit: object, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(run=observe)

    monkeypatch.setattr(full, "observed_subprocess", proxy)
    wrapper = observer.pytest_runtest_call(item)
    assert next(wrapper) is None
    assert set(calls[0]) == {"deadline_factory"}
    run = item.module.subprocess.run
    for argv in (
        None, [], ["python.exe", "-NoProfile", "-NonInteractive", "stamp.py"],
        ["git.exe", "-NoProfile", "-NonInteractive", "status"],
        ["pwsh.exe.cmd", "-NoProfile", "-NonInteractive", "exit"],
        [b"pwsh.exe", "-NoProfile", "-NonInteractive", "exit"], "pwsh.exe -Command exit",
        [str(tmp_path / "cmd.exe"), "-NoProfile", "-NonInteractive", "powershell.exe"],
    ):
        assert run(argv, timeout=10) == "passive"
        assert not claim.exists()
    assert original_run.call_count == 8 and not sampled
    for executable in ("pwsh.EXE", "powershell.exe"):
        argv = [str(tmp_path / executable), "-NoProfile", "-NonInteractive", "-Command", "exit"]
        assert run(argv, timeout=10) == "observed"
    assert selections == ["selected", "already-selected"]
    assert sampled == [True] and claim.is_dir()
    with pytest.raises(StopIteration):
        next(wrapper)


@pytest.mark.guard
def test_code_summary_is_additional_bounded_and_not_hidden_in_old_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", "code-stack")
    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_CODE_SAMPLE_CLAIM", str(tmp_path / "selected"))
    observer = FullNativeChildObserver()
    item = original_item(monkeypatch)
    observer.rows[item] = [{"outcome": "returned"} for _ in range(observer.MAX_ROWS)]
    value: Sample = {
        "outcome": "timeout", "code_observation": {
            "selection": "selected", "collector_settled": True,
            "samples": [{"threads": [{"frames": [{"module": "ntdll.dll", "code_offset": 1}]}]}],
        },
    }
    observer.emit(item, value)
    observer.emit(item, value)
    report = finish_report(observer, item, "failed")
    lines = report.sections[0][1].splitlines()
    code = [line.partition("=")[2] for line in lines if line.startswith("native_code_observation=")]
    metadata = [line for line in lines if line.startswith("native_child_observation=")]
    assert len(code) == 1 and len(metadata) == observer.MAX_ROWS
    assert len(code[0].encode("ascii")) <= 32768
    assert all("code_observation" not in line for line in metadata)
    assert "code_observation" in value and observer.code_rows == {}


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
