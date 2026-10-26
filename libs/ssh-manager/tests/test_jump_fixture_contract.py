"""Inert admission/rendering checks for the hosted-only native witness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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


def test_native_jump_profile_has_two_hops_without_credentials():
    source = _fixture().profile_text("synthetic-console --stdio")
    assert "ProxyJump jump-first.invalid,jump-last.invalid" in source
    assert "ProxyCommand synthetic-console --stdio" in source
    assert "IdentityFile none" in source
    assert "IdentityAgent none" in source
    assert "StrictHostKeyChecking yes" in source
