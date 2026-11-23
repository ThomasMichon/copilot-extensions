"""Controls for the independent, report-only native diagnostic entry point."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from agent_procutil import no_window_kwargs
import _native_failure_report_pytest as observer


class Poison:
    def __repr__(self) -> str:
        raise AssertionError("must not represent payloads")

    def __float__(self) -> float:
        raise AssertionError("must not coerce values")

    def __len__(self) -> int:
        raise AssertionError("must not count arbitrary objects")


def original_item(
    request: pytest.FixtureRequest, function: Callable[..., object],
) -> pytest.Function:
    assert request.node.parent is not None
    return pytest.Function.from_parent(
        request.node.parent, name="synthetic-original", callobj=function,
    )


def finish_report(
    item: pytest.Function, call: pytest.CallInfo[None], report: pytest.TestReport,
) -> pytest.TestReport:
    wrapper = observer.pytest_runtest_makereport(item, call)
    assert next(wrapper) is None
    with pytest.raises(StopIteration) as done:
        wrapper.send(report)
    assert done.value.value is report
    return report


def test_failure_metadata_filters_locals_and_preserves_original_report(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def original() -> None:
        started = 10.0
        started_ms = 10_000
        native_seconds = 0.125
        observed = [Poison(), {"private": "synthetic-payload-canary"}]
        seen = Poison()
        launch = {"cpu_ticks": 1250, "qpc": Poison(), "private": Poison()}
        assert started and started_ms and native_seconds and observed and launch
        assert type(seen) is Poison
        raise AssertionError("synthetic-original-failure")

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    report = pytest.TestReport.from_item_and_call(item, call)
    longrepr = report.longrepr
    exception = call.excinfo
    monkeypatch.setattr(observer, "time", SimpleNamespace(
        monotonic=lambda: 20.0, time_ns=lambda: 20_001_000_000,
    ))
    finish_report(item, call, report)
    assert report.failed and report.longrepr is longrepr and call.excinfo is exception
    text = report.sections[-1][1]
    assert "synthetic-payload-canary" not in text
    assert "synthetic-original-failure" not in text and str(item.path) not in text
    body = json.loads(text.partition("=")[2])
    assert body["sampling_mode"] == "report-only" and body["context_available"]
    row = body["contexts"][0]
    assert row == {
        "role": "test", "started": 10.0, "started_ms": 10_000,
        "native_seconds": 0.125, "observed_count": 2, "launch": {"cpu_ticks": 1250},
        "elapsed_at_report_ms": 10_000.0, "utc_elapsed_at_report_ms": 10_001.0,
        "utc_minus_monotonic_elapsed_ms": 1.0,
    }


def test_timeout_stderr_context_preserves_report_without_exporting_payloads(
    request: pytest.FixtureRequest,
) -> None:
    stderr = (
        b"synthetic-private-stderr\nNATURAL_SHELL_BUILD 5.1.0\n"
        b"NATURAL_POLICY_START\nNATURAL_POLICY_COMPLETE-extra\n"
    )
    error = subprocess.TimeoutExpired(
        ["synthetic-command-argument"], 10,
        output=b"synthetic-private-stdout", stderr=stderr,
    )

    def original() -> None:
        raise error

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    report = pytest.TestReport.from_item_and_call(item, call)
    longrepr = report.longrepr
    exception = call.excinfo
    finish_report(item, call, report)
    assert report.failed and report.longrepr is longrepr and call.excinfo is exception
    assert exception is not None and exception.value is error and error.stderr is stderr
    text = report.sections[-1][1]
    assert "synthetic-private-stderr" not in text
    assert "synthetic-private-stdout" not in text
    assert "synthetic-command-argument" not in text and str(item.path) not in text
    body = json.loads(text.partition("=")[2])
    assert body["context_available"] is True
    assert body["timeout_stderr"] == {
        "available": True, "truncated": False, "shell_build": True,
        "policy_start": True, "policy_complete": False,
    }


def test_registered_write_helper_retains_bounded_failure_context(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = AssertionError("synthetic-original-write-failure")

    def original() -> None:
        def qualify_registered_file_local_write() -> None:
            started = 10.0
            started_ms = 10_000
            native_seconds = 0.125
            observed = [Poison()]
            launch = {"cpu_ticks": 1250, "private": Poison()}
            event = Poison()
            assert started and started_ms and native_seconds and observed and launch
            assert type(event) is Poison
            raise error
        qualify_registered_file_local_write()

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    report = pytest.TestReport.from_item_and_call(item, call)
    longrepr = report.longrepr
    monkeypatch.setattr(observer, "time", SimpleNamespace(
        monotonic=lambda: 20.0, time_ns=lambda: 20_001_000_000,
    ))
    finish_report(item, call, report)
    assert report.failed and report.longrepr is longrepr
    assert call.excinfo is not None and call.excinfo.value is error
    text = report.sections[-1][1]
    assert "private" not in text and "synthetic-original-write-failure" not in text
    assert json.loads(text.partition("=")[2])["contexts"] == [
        {"role": "test"},
        {
            "role": "registered-write-helper", "started": 10.0, "started_ms": 10_000,
            "native_seconds": 0.125, "observed_count": 1, "launch": {"cpu_ticks": 1250},
            "elapsed_at_report_ms": 10_000.0, "utc_elapsed_at_report_ms": 10_001.0,
            "utc_minus_monotonic_elapsed_ms": 1.0,
        },
    ]


def test_completed_stderr_context_preserves_report_without_exporting_payloads(
    request: pytest.FixtureRequest,
) -> None:
    stderr = (
        b"synthetic-private-stderr\nNATURAL_SHELL_BUILD 5.1.0\n"
        b"NATURAL_POLICY_START\nNATURAL_POLICY_COMPLETE-extra\n"
    )
    error = AssertionError("synthetic-original-failure")

    def original() -> None:
        def invoke() -> None:
            result = subprocess.CompletedProcess(
                ["synthetic-command-argument"], 0,
                stdout=b"synthetic-private-stdout", stderr=stderr,
            )
            assert result.returncode == 0
            raise error
        invoke()

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    report = pytest.TestReport.from_item_and_call(item, call)
    longrepr = report.longrepr
    exception = call.excinfo
    finish_report(item, call, report)
    assert report.failed and report.longrepr is longrepr and call.excinfo is exception
    assert exception is not None and exception.value is error
    text = report.sections[-1][1]
    for private in (
        "synthetic-private-stderr", "synthetic-private-stdout",
        "synthetic-command-argument", "synthetic-original-failure", str(item.path),
    ):
        assert private not in text
    body = json.loads(text.partition("=")[2])
    assert body["context_available"] is True and "timeout_stderr" not in body
    assert body["contexts"] == [
        {"role": "test"},
        {"role": "invoke-helper", "completed_stderr": {
            "available": True, "truncated": False, "shell_build": True,
            "policy_start": True, "policy_complete": False,
        }},
    ]


@pytest.mark.parametrize(
    "location", ["outer", "nonhelper", "arbitrary", "timeout", "chained-timeout"],
)
def test_completed_stderr_context_rejects_unrelated_or_stale_results(
    request: pytest.FixtureRequest, location: str,
) -> None:
    def completed() -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            ["synthetic-private-argument"], 0,
            stdout=b"synthetic-private-stdout", stderr=b"NATURAL_POLICY_COMPLETE\n",
        )

    def original() -> None:
        result = completed()

        def unrelated() -> None:
            result = completed()
            assert result.returncode == 0
            raise AssertionError("synthetic-private-error")

        def invoke() -> None:
            result = Poison() if location == "arbitrary" else completed()
            assert result is not None
            if location == "timeout":
                raise subprocess.TimeoutExpired(["synthetic-private-timeout"], 10)
            if location == "chained-timeout":
                try:
                    raise subprocess.TimeoutExpired(["synthetic-private-timeout"], 10)
                except subprocess.TimeoutExpired:
                    raise AssertionError("synthetic-private-error") from None
            raise AssertionError("synthetic-private-error")

        assert result.returncode == 0
        if location == "outer":
            raise AssertionError("synthetic-private-error")
        if location == "nonhelper":
            unrelated()
        invoke()

    body = observer.failure_context(
        original_item(request, original), pytest.CallInfo.from_call(original, "call"),
    )
    assert "completed_stderr" not in json.dumps(body)
    assert "synthetic-private" not in json.dumps(body)
    if location == "timeout":
        assert body["timeout_stderr"] == {
            "available": False, "truncated": False, "shell_build": False,
            "policy_start": False, "policy_complete": False,
        }


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 10**200, Poison()])
def test_numeric_filter_rejects_nonfinite_huge_or_coercible_values(value: object) -> None:
    assert observer._number(value) is None


def test_frame_selection_is_bounded_and_ignores_foreign_code(
    request: pytest.FixtureRequest,
) -> None:
    def original() -> None:
        def invoke(depth: int) -> None:
            started = 1.0
            assert started
            if depth:
                invoke(depth - 1)
            raise AssertionError("synthetic")
        invoke(90)

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    body = observer.failure_context(item, call)
    assert len(cast(list[object], body["contexts"])) == 8
    assert body["contexts_truncated"] is True
    foreign = original_item(request, os.path.abspath)
    assert observer.failure_context(foreign, call)["context_available"] is False


def test_traceback_walk_has_a_frame_bound(request: pytest.FixtureRequest) -> None:
    def original() -> None:
        def unrelated(depth: int) -> None:
            if depth:
                unrelated(depth - 1)
            raise AssertionError("synthetic")
        unrelated(90)

    body = observer.failure_context(
        original_item(request, original), pytest.CallInfo.from_call(original, "call"),
    )
    assert body["frames_truncated"] is True
    assert body["contexts"] == [{"role": "test"}]
    assert body["context_available"] is False


def test_reporting_errors_and_nonfailure_reports_preserve_outcomes(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def original() -> None:
        raise AssertionError("synthetic")

    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")

    def unavailable(*args: object) -> dict[str, object]:
        raise RuntimeError("private-error-canary")

    monkeypatch.setattr(observer, "failure_context", unavailable)
    report = pytest.TestReport.from_item_and_call(item, call)
    finish_report(item, call, report)
    assert report.failed
    assert report.sections[-1][1] == "native_failure_context_report_error=true"
    cases: tuple[
        tuple[Literal["setup", "call"], Literal["failed", "passed", "skipped"]], ...,
    ] = (("setup", "failed"), ("call", "passed"), ("call", "skipped"))
    for when, outcome in cases:
        other = pytest.TestReport("synthetic", ("synthetic.py", 0, "synthetic"), {},
                                  outcome, None, when)
        finish_report(item, call, other)
        assert other.outcome == outcome and other.sections == []


@pytest.mark.parametrize("mode", ["late", "post-exit"])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("report_error", [False, True])
def test_full_post_exit_reuses_bounded_failure_context_without_plugin_registration(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
    mode: str, fails: bool, report_error: bool,
) -> None:
    from hook_native_portfolio import FullNativeChildObserver

    def original() -> None:
        started = 10.0
        event = Poison()
        assert started and type(event) is Poison
        if fails:
            raise subprocess.TimeoutExpired(["synthetic-private-command"], 10)

    def unavailable(*args: object) -> dict[str, object]:
        raise RuntimeError("synthetic-private-error")

    monkeypatch.setenv("AGENT_WORKTREES_NATIVE_OBSERVATION_MODE", mode)
    if report_error:
        monkeypatch.setattr(observer, "failure_context", unavailable)
    full = FullNativeChildObserver()
    item = original_item(request, original)
    call = pytest.CallInfo.from_call(original, "call")
    report = pytest.TestReport.from_item_and_call(item, call)
    longrepr = report.longrepr
    exception = call.excinfo
    plugins = tuple(item.config.pluginmanager.get_plugins())
    wrapper = full.pytest_runtest_makereport(item, call)
    assert next(wrapper) is None
    with pytest.raises(StopIteration) as done:
        wrapper.send(report)
    assert done.value.value is report
    assert report.longrepr is longrepr and call.excinfo is exception
    assert tuple(item.config.pluginmanager.get_plugins()) == plugins
    if mode == "post-exit" and fails:
        assert len(report.sections) == 1
        text = report.sections[0][1]
        assert "synthetic-private" not in text and str(item.path) not in text
        if report_error:
            assert text == "native_failure_context_report_error=true"
        else:
            body = json.loads(text.partition("=")[2])
            assert body["sampling_mode"] == "post-exit" and body["test_outcome"] == "failed"
            assert body["contexts"][0]["started"] == 10.0
            assert body["context_available"] is True
    else:
        assert report.sections == []


@pytest.mark.parametrize("original", [0, 1, 2, 3, 4, 5])
def test_diagnostic_exit_preserves_original_nonzero_categories(
    original: int, capsys: pytest.CaptureFixture[str],
) -> None:
    config = SimpleNamespace(
        stash=pytest.Stash(), pluginmanager=SimpleNamespace(getplugin=lambda name: None),
    )
    session = cast(pytest.Session, SimpleNamespace(exitstatus=original, config=config))
    observer.pytest_sessionfinish(session, original)
    body = json.loads(capsys.readouterr().out.partition("=")[2])
    assert body["original_exitstatus"] == original and body["observed_calls"] == 0
    assert body["sampling_mode"] == "report-only" and body["diagnostic_only"]
    assert session.exitstatus == (1 if original == 0 else original)


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
def test_fresh_entry_import_and_configure_do_not_intercept_or_import_fixtures() -> None:
    root = Path(__file__).parent.parent
    script = """
import os, subprocess, sys
from types import SimpleNamespace
import pytest
before = (subprocess.run, subprocess.Popen, tuple(sys.path))
import _native_failure_report_pytest as observer
os.environ['PYTEST_PLUGINS'] = observer.__name__ + ',unrelated_plugin'
config = SimpleNamespace(stash=pytest.Stash())
observer.pytest_configure(config)
assert os.environ['PYTEST_PLUGINS'] == 'unrelated_plugin'
assert before == (subprocess.run, subprocess.Popen, tuple(sys.path))
assert not any(name.startswith(('test_managed_session', 'test_post_tool',
    'test_hook_packet_receiver', 'hook_native_portfolio', 'hook_child_observer'))
    for name in sys.modules)
assert not any(hasattr(observer, name) for name in (
    'pytest_runtest_call', 'pytest_runtest_setup', 'pytest_collection_modifyitems'))
print('report-only-import-control=passed')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True,
        timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "report-only-import-control=passed"


@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_REPORT_ONLY_CONTROL") != "1",
    reason="explicit deliberately failing report-path control",
)
def test_report_only_real_failure_control() -> None:
    started = time.monotonic()
    started_ms = time.time_ns() // 1_000_000
    observed = [Poison()]
    launch = {"cpu_ticks": 123, "private": "synthetic-payload-canary"}
    assert started > 0 and started_ms > 0 and observed and launch
    raise AssertionError("deliberate report-only control; not native acceptance")
