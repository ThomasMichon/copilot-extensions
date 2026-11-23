"""Opt-in, post-failure numeric context; no native execution interception."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from types import FunctionType, TracebackType
from typing import Protocol, cast

import pytest

_FAILURES = pytest.StashKey[int]()
_OWNED_FILES = {
    "test_managed_session.py", "test_native_pre_policy.py", "test_post_tool_shell.py",
    "test_pre_tool_bounded_reads.py", "test_native_tool_hook_transport.py",
    "test_native_shell_arguments.py", "test_hook_ipc.py", "test_status_monitor.py",
    "test_native_failure_report.py",
}
_HELPERS = {
    "qualify_complete_native_entry": "entry-helper", "invoke": "invoke-helper",
    "qualify_registered_file_local_write": "registered-write-helper",
}
_TIMINGS = ("started", "entry_started", "case_started", "native_seconds", "elapsed")
_COUNTS = ("observed", "seen", "observations")
_LAUNCH_FIELDS = ("birth_filetime", "entry_utc_ms", "qpc", "frequency", "cpu_ticks")


class _Reporter(Protocol):
    def write_line(self, line: str) -> None: ...


def _number(value: object) -> int | float | None:
    if type(value) not in (int, float):
        return None
    numeric = cast(int | float, value)
    if abs(numeric) > 10**20 or not math.isfinite(numeric):
        return None
    return numeric


def _lexical(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _timeout_stderr_markers(error: object) -> dict[str, bool] | None:
    if type(error) is not subprocess.TimeoutExpired:
        return None
    return _stderr_markers(error.stderr)


def _completed_stderr_markers(value: object) -> dict[str, bool] | None:
    if type(value) is not subprocess.CompletedProcess:
        return None
    return _stderr_markers(value.stderr)


def _stderr_markers(value: object) -> dict[str, bool]:
    result = {
        "available": False, "truncated": False, "shell_build": False,
        "policy_start": False, "policy_complete": False,
    }
    if type(value) is bytes:
        prefix = value[:16384]
        result["truncated"] = len(value) > len(prefix)
    elif type(value) is str:
        encoded = value[:16384].encode("utf-8", errors="replace")
        prefix = encoded[:16384]
        result["truncated"] = len(value) > 16384 or len(encoded) > len(prefix)
    else:
        return result
    result["available"] = True
    lines = prefix.splitlines(keepends=True)
    if result["truncated"] and lines and not lines[-1].endswith((b"\r", b"\n")):
        lines.pop()
    retained = [line.rstrip(b"\r\n") for line in lines]
    result["shell_build"] = any(
        line.startswith(b"NATURAL_SHELL_BUILD ") for line in retained
    )
    result["policy_start"] = b"NATURAL_POLICY_START" in retained
    result["policy_complete"] = b"NATURAL_POLICY_COMPLETE" in retained
    return result


def failure_context(item: pytest.Function, call: pytest.CallInfo[None]) -> dict[str, object]:
    """Read bounded numeric and marker context from this owned test's failure."""
    rows: list[dict[str, object]] = []
    body: dict[str, object] = {"contexts": rows, "context_available": False}
    function = item.function
    path = item.path
    if (
        type(function) is not FunctionType or path.name not in _OWNED_FILES
        or _lexical(path.parent) != _lexical(Path(__file__).parent / "tests")
        or _lexical(function.__code__.co_filename) != _lexical(path)
        or call.excinfo is None
    ):
        return body
    monotonic = time.monotonic()
    utc_ms = time.time_ns() / 1_000_000
    error = call.excinfo.value
    completed_failure = (
        type(error) is AssertionError
        and error.__cause__ is None and error.__context__ is None
    )
    traceback: TracebackType | None = call.excinfo.tb
    for _ in range(64):
        if traceback is None:
            break
        frame = traceback.tb_frame
        code = frame.f_code
        role = (
            "test" if code is function.__code__
            else _HELPERS.get(code.co_name)
        )
        if role is not None and _lexical(code.co_filename) == _lexical(path):
            if len(rows) == 8:
                body["contexts_truncated"] = True
                break
            values = frame.f_locals
            row: dict[str, object] = {"role": role}
            if role == "invoke-helper" and completed_failure:
                markers = _completed_stderr_markers(values.get("result"))
                if markers is not None:
                    row["completed_stderr"] = markers
            for name in _TIMINGS:
                value = _number(values.get(name))
                if value is not None:
                    row[name] = value
            started = _number(values.get("started"))
            started_ms = _number(values.get("started_ms"))
            if started is not None:
                row["elapsed_at_report_ms"] = round((monotonic - started) * 1000, 3)
            if started_ms is not None:
                row["started_ms"] = started_ms
                row["utc_elapsed_at_report_ms"] = round(utc_ms - started_ms, 3)
                if started is not None:
                    row["utc_minus_monotonic_elapsed_ms"] = round(
                        utc_ms - started_ms - (monotonic - started) * 1000, 3,
                    )
            for name in _COUNTS:
                value = values.get(name)
                if type(value) is list:
                    row[name + "_count"] = len(value)
            launch = values.get("launch")
            if type(launch) is dict:
                counters: dict[str, int | float] = {}
                for name in _LAUNCH_FIELDS:
                    value = _number(launch.get(name))
                    if value is not None:
                        counters[name] = value
                if counters:
                    row["launch"] = counters
            rows.append(row)
        traceback = traceback.tb_next
    else:
        if traceback is not None:
            body["frames_truncated"] = True
    timeout = _timeout_stderr_markers(call.excinfo.value) if rows else None
    if timeout is not None:
        body["timeout_stderr"] = timeout
    body["context_available"] = any(len(row) > 1 for row in rows) or timeout is not None
    duration = _number(call.duration)
    if duration is not None:
        body["call_duration_ms"] = round(duration * 1000, 3)
    return body


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    configured = os.environ.get("PYTEST_PLUGINS", "")
    retained = [name for name in configured.split(",") if name.strip() not in ("", __name__)]
    if retained:
        os.environ["PYTEST_PLUGINS"] = ",".join(retained)
    else:
        os.environ.pop("PYTEST_PLUGINS", None)
    config.stash[_FAILURES] = 0


def pytest_report_header() -> str:
    return "full_native_observation=report-only; post-failure numeric context, never acceptance"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None],
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when != "call" or not report.failed or not isinstance(item, pytest.Function):
        return report
    item.config.stash[_FAILURES] = item.config.stash.get(_FAILURES, 0) + 1
    try:
        body = failure_context(item, call)
        body["sampling_mode"] = "report-only"
        body["test_outcome"] = report.outcome
        text = "native_failure_context=" + json.dumps(body, sort_keys=True, allow_nan=False)
    except Exception:
        text = "native_failure_context_report_error=true"
    report.sections.append(("Native failure context", text))
    return report


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    original = int(session.exitstatus)
    line = "full_native_observation_end=" + json.dumps({
        "original_exitstatus": original, "observed_calls": 0,
        "failure_reports": session.config.stash.get(_FAILURES, 0),
        "sampling_mode": "report-only", "diagnostic_only": True,
    }, sort_keys=True)
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        cast(_Reporter, reporter).write_line(line)
    else:
        print(line)
    if original == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
