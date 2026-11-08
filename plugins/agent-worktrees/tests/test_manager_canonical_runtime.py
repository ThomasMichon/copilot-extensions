"""The Manager consumer honors canonical installed slots without project resolution."""

from __future__ import annotations

import argparse
import os
import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import manager_launch_cli as manager
from agent_worktrees import update_cli


@pytest.fixture
def selected(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    slot = root / "versions" / "0.6.44-dev1"
    slot.mkdir(parents=True)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(manager, "_core", lambda: SimpleNamespace())
    monkeypatch.setattr(manager, "_worktree_manager_root", lambda: root)
    calls = []
    def probe(command):
        calls.append(command)
        return (0, 6, 44, 1), subprocess.CompletedProcess(command, 0, "worktree-manager 0.6.44-dev1")
    monkeypatch.setattr(manager, "_probe_worktree_manager_version", probe)
    return root, slot, calls


def test_canonical_launcher_probe_uses_retained_target_and_payload_assets(selected):
    _, slot, calls = selected
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.touch()
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.touch()
    assert manager._usable_worktree_manager_launcher_dir() == slot / "payload" / "bin"
    assert calls == [[
        str(python), "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
        "-m", "worktree_manager", "--version",
    ]]
    assert "uv" not in calls[0]


def test_canonical_slot_without_interpreter_is_not_probed_or_bootstrapped(selected):
    _, slot, calls = selected
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.touch()
    assert manager._usable_worktree_manager_launcher_dir() is None
    assert calls == []


def test_legacy_launcher_contract_remains_unchanged(selected):
    _, slot, calls = selected
    assert manager._usable_worktree_manager_launcher_dir() == slot / "bin"
    assert calls == [[
        "uv", "run", "--quiet", "--project", str(slot), "python", "-m",
        "worktree_manager", "--version",
    ]]


def test_normal_update_does_not_bootstrap_an_unusable_manager(monkeypatch):
    calls = []
    core = SimpleNamespace(
        _usable_worktree_manager=lambda: None,
        _cmd_update_in_plugin=lambda args: calls.append("plugin-only") or 0,
    )
    monkeypatch.setattr(update_cli, "_core", lambda: core)
    monkeypatch.setattr(
        manager, "cmd_manager_install_trigger",
        lambda *_: pytest.fail("normal update must not implicitly bootstrap Manager"),
    )
    result = update_cli.cmd_update(argparse.Namespace(no_manager=False, auth_report=False))
    assert result == 0
    assert calls == ["plugin-only"]
