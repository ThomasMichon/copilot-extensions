"""Inert admission/rendering checks for the hosted-only native witness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import agent_procutil
import pytest


def _fixture():
    path = Path(__file__).parent / "fixtures" / "windows_jump_probe.py"
    spec = importlib.util.spec_from_file_location("native_jump_fixture_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_native_jump_fixture_refuses_shared_host_before_native_work(monkeypatch, tmp_path):
    module = _fixture()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub-hosted Windows ARM64"):
        await module.drive(tmp_path)
    assert not list(tmp_path.iterdir())


def test_visible_observer_control_also_refuses_shared_hosts(monkeypatch):
    module = _fixture()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub-hosted Windows ARM64"):
        module.observe_canary(Path("not-a-real-interpreter"))


def test_window_identity_query_refuses_shared_hosts(monkeypatch):
    module = _fixture()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub-hosted Windows ARM64"):
        module.window_identity(0)


async def test_cached_profile_control_refuses_shared_hosts_before_writing(monkeypatch, tmp_path):
    module = _fixture()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub-hosted Windows ARM64"):
        await module.verify_cached_profile_refresh(tmp_path, Path("not-a-real-client"))
    assert not list(tmp_path.iterdir())


def test_native_jump_profile_has_two_hops_without_credentials():
    source = _fixture().profile_text("synthetic-console --stdio")
    assert "ProxyJump jump-first.invalid,jump-last.invalid" in source
    assert "ProxyCommand synthetic-console --stdio" in source
    assert "IdentityFile none" in source
    assert "IdentityAgent none" in source
    assert "StrictHostKeyChecking yes" in source


def _canary_boundary(monkeypatch, frames):
    module = _fixture()
    monkeypatch.setattr(module, "require_hosted_windows", lambda: None)
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(
        CREATE_NEW_CONSOLE=0x10, PIPE=-1, DEVNULL=-3,
    ))
    clock = iter(range(100))
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: next(clock), sleep=lambda _: None,
    ))
    observations = iter(frames)
    observer = ModuleType("windows_proxy_probe")
    observer.terminal_windows = lambda: next(observations)
    monkeypatch.setitem(sys.modules, observer.__name__, observer)
    monkeypatch.setattr(module, "window_identity", lambda window: {
        "window": window, "pid": 42 if window == 22 else 99,
    })
    closed = []
    process = SimpleNamespace(
        pid=42, poll=lambda: None, wait=lambda **_: 0,
        stdin=SimpleNamespace(write=lambda _: None, close=lambda: None),
    )
    job = SimpleNamespace(
        terminate_and_wait=lambda _: None, close=lambda: closed.append(True),
    )
    monkeypatch.setattr(
        agent_procutil, "spawn_sync_in_kill_on_close_job", lambda *_, **__: (process, job),
    )
    return module, closed


def test_unrelated_window_cannot_validate_canary_sensitivity(monkeypatch):
    module, closed = _canary_boundary(monkeypatch, [{}, *[{11: False}] * 10])
    with pytest.raises(RuntimeError, match="did not detect its owned canary"):
        module.observe_canary(Path("synthetic-python"))
    assert closed == [True]


def test_canary_control_observes_and_retires_its_specific_window(monkeypatch):
    module, closed = _canary_boundary(monkeypatch, [
        {}, {11: False}, {11: False, 22: False}, {11: False},
    ])
    evidence = module.observe_canary(Path("synthetic-python"))
    assert evidence == {"process": 42, "windows": [{"window": 22, "pid": 42}]}
    assert closed == [True]
