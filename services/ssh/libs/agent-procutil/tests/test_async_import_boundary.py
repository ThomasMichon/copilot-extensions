"""Async dependency admission and compatibility, without native Job experiments."""

from __future__ import annotations

import asyncio
import inspect
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from unittest.mock import ANY, AsyncMock, Mock

import pytest

import agent_procutil as pu


def test_fresh_synchronous_import_does_not_load_asyncio():
    source = Path(pu.__file__).resolve().parent.parent
    code = """
import json
import sys
sys.path.insert(0, sys.argv[1])
assert 'asyncio' not in sys.modules
import agent_procutil as pu
from agent_procutil import (
    JobHandle, direct_python, no_window_kwargs, spawn_daemon,
    spawn_sync_in_kill_on_close_job,
)
assert callable(direct_python) and callable(spawn_daemon)
assert isinstance(no_window_kwargs(), dict)
assert callable(spawn_sync_in_kill_on_close_job)
assert set(pu.__all__).issubset(dir(pu))
assert 'asyncio' in dir(pu)
assert not any(n == 'asyncio' or n.startswith('asyncio.') for n in sys.modules)
assert 'agent_procutil._async' not in sys.modules
print(json.dumps({'asyncio_loaded': False, 'exports_discoverable': True}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(source)],
        capture_output=True, text=True, timeout=30, check=True,
        **pu.no_window_kwargs(),
    )
    assert json.loads(result.stdout) == {
        "asyncio_loaded": False, "exports_discoverable": True,
    }


def test_async_export_reflection_and_identity():
    helper = pu.spawn_in_kill_on_close_job
    assert helper is pu.spawn_in_kill_on_close_job
    assert inspect.iscoroutinefunction(helper)
    assert helper.__module__ == "agent_procutil"
    assert helper.__name__ == helper.__qualname__ == "spawn_in_kill_on_close_job"
    assert str(inspect.signature(helper)) == (
        "(*args: 'str', **kwargs: 'Any') -> "
        "'tuple[asyncio.subprocess.Process, JobHandle | None]'"
    )
    expected = {
        "args": str, "kwargs": Any,
        "return": tuple[asyncio.subprocess.Process, pu.JobHandle | None],
    }
    assert get_type_hints(helper) == expected
    assert get_type_hints(helper, vars(pu)) == expected
    assert pickle.loads(pickle.dumps(helper)) is helper
    assert pu.asyncio is asyncio
    namespace = {}
    exec("from agent_procutil import *", namespace)
    assert namespace["spawn_in_kill_on_close_job"] is helper
    assert namespace["JobHandle"] is pu.JobHandle
    with pytest.raises(AttributeError, match="has no attribute 'not_an_export'"):
        getattr(pu, "not_an_export")


def test_async_export_retains_module_replacement_seam(monkeypatch):
    process = Mock(pid=123)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pu, "asyncio", SimpleNamespace(create_subprocess_exec=spawn))
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    result, job = asyncio.run(pu.spawn_in_kill_on_close_job("fixture", stdin=17))
    assert result is process and job is None
    spawn.assert_awaited_once_with("fixture", stdin=17)


def test_async_export_retains_job_class_and_parent_patch_seams(monkeypatch):
    calls = []
    process = Mock(pid=123)
    spawn = AsyncMock(return_value=process)
    job = pu.JobHandle(42)
    monkeypatch.setattr(pu, "asyncio", SimpleNamespace(create_subprocess_exec=spawn))
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(
        pu, "_assign_suspended_to_kill_on_close_job",
        lambda pid, **kw: calls.append(("assign", pid)) or job,
    )
    monkeypatch.setattr(
        pu, "_resume_suspended_process", lambda pid: calls.append(("resume", pid)) or True,
    )
    result, actual_job = asyncio.run(pu.spawn_in_kill_on_close_job("fixture", creationflags=7))
    assert result is process and actual_job is job
    assert type(actual_job) is pu.JobHandle
    assert calls == [("assign", 123), ("resume", 123)]
    spawn.assert_awaited_once_with("fixture", creationflags=7 | pu._CREATE_SUSPENDED, env=ANY)
    # No native handle was acquired by this fixture.
    job._handle = None
