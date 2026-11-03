"""Admit the actor-free write-accounting and finite-clock controls."""

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if "agent_procutil" not in sys.modules:
    _load("agent_procutil", ROOT / "libs/agent-procutil/src/agent_procutil/__init__.py")
import agent_procutil as pu
from agent_procutil import job_activity

_stream = _load(
    "promotion_original_stream_activity",
    ROOT / "libs/agent-procutil/tests/test_stream_activity.py",
)


@pytest.fixture
def job():
    value = pu.JobHandle(123)
    yield value
    value._handle = None


def test_only_completed_write_growth_renews(job, monkeypatch):
    values = iter([100, 100, 120, 120, 140])
    monkeypatch.setattr(job_activity, "_write_count", lambda value: next(values))
    observe = job_activity.JobWriteActivity(job)
    assert [observe() for _ in range(4)] == [False, True, False, True]


def test_decreasing_native_accounting_refuses(job, monkeypatch):
    values = iter([100, 99])
    monkeypatch.setattr(job_activity, "_write_count", lambda value: next(values))
    observe = job_activity.JobWriteActivity(job)
    with pytest.raises(job_activity.JobActivityError, match="decreased"):
        observe()


def test_native_query_error_keeps_cause(job, monkeypatch):
    error = OSError(5, "native accounting refused")

    def fail(_job):
        raise error

    monkeypatch.setattr(job_activity, "_write_count", fail)
    with pytest.raises(job_activity.JobActivityError) as raised:
        job_activity.JobWriteActivity(job)
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("closed", [False, True])
def test_missing_or_closed_custody_refuses(job, closed):
    if closed:
        job._handle = None
    with pytest.raises(job_activity.JobActivityError, match="owned|closed"):
        job_activity.JobWriteActivity(job if closed else None)


stream = _stream.stream
test_activity_renews_quiet_but_never_absolute_cap = (
    _stream.test_activity_renews_quiet_but_never_absolute_cap
)
test_later_stall_expires_and_startup_grace_ends = (
    _stream.test_later_stall_expires_and_startup_grace_ends
)
test_activity_requires_strict_bool = _stream.test_activity_requires_strict_bool
test_observation_error_retains_original_failure = (
    _stream.test_observation_error_retains_original_failure
)
test_output_bytes_and_default_quiet_behavior_are_unchanged = (
    _stream.test_output_bytes_and_default_quiet_behavior_are_unchanged
)
