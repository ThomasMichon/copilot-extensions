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
