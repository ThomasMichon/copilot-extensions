"""_update_modules passes -ZeroDowntime for a zero-downtime module (Windows).

A module declaring ``"zeroDowntimeUpdate": true`` (e.g. agent-bridge) must
redeploy via its ZDD cutover on a version bump -- not a disruptive stop-and-swap
that drops live sessions -- so its ``install.ps1 update`` carries -ZeroDowntime,
matching the launch-path reconciler. A plain module gets no flag; the non-Windows
(install.sh) path never adds it (no such switch exists there).
"""

from __future__ import annotations

import json

import pytest

from agent_worktrees import __main__ as m


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_tree(tmp_path, *, zero_downtime: bool, unix: bool = False):
    ext_root = tmp_path / "installed-plugins" / "copilot-extensions"
    plugin_dir = ext_root / "agent-worktrees"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("modules.json").write_text(
        json.dumps({"modules": [{"name": "agent-bridge", "source": "agent-bridge"}]}))
    mod_dir = ext_root / "agent-bridge"
    (mod_dir / "scripts").mkdir(parents=True)
    (mod_dir / "scripts" / "install.ps1").write_text("# installer\n")
    if unix:
        (mod_dir / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    pj: dict = {"name": "agent-bridge"}
    if zero_downtime:
        pj["zeroDowntimeUpdate"] = True
    (mod_dir / "plugin.json").write_text(json.dumps(pj))
    return plugin_dir


@pytest.fixture
def recorded(monkeypatch):
    calls: list[list[str]] = []

    def _run(argv, *a, **k):
        calls.append([str(x) for x in argv])
        return _Completed(0)

    monkeypatch.setattr(m.subprocess, "run", _run)
    monkeypatch.setattr(
        m.shutil, "which",
        lambda x: "pwsh" if x in ("pwsh", "powershell") else "/bin/bash")
    return calls


def _installer_update_argv(calls):
    for c in calls:
        if any(x.endswith("install.ps1") or x.endswith("install.sh") for x in c) \
                and "update" in c:
            return c
    return None


def test_zero_downtime_module_gets_flag(tmp_path, recorded):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True)
    m._update_modules(plugin_dir, "windows", None, force=True)
    argv = _installer_update_argv(recorded)
    assert argv is not None
    assert "-ZeroDowntime" in argv


def test_plain_module_gets_no_flag(tmp_path, recorded):
    plugin_dir = _make_tree(tmp_path, zero_downtime=False)
    m._update_modules(plugin_dir, "windows", None, force=True)
    argv = _installer_update_argv(recorded)
    assert argv is not None
    assert "-ZeroDowntime" not in argv


def test_non_windows_never_adds_flag(tmp_path, recorded):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True, unix=True)
    m._update_modules(plugin_dir, "linux", None, force=True)
    for c in recorded:
        assert "-ZeroDowntime" not in c


def test_unified_update_skips_inactive_module_runtime(tmp_path, recorded):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True, unix=True)
    targets = {
        "agent-bridge": m._RegisteredPluginTarget(
            context=None,
            activation=m._PluginActivation.INACTIVE,
        ),
    }

    m._update_modules(
        plugin_dir,
        "linux",
        None,
        force=True,
        targets=targets,
    )

    assert recorded == []


def test_unified_update_does_not_refresh_active_module_payload_twice(
    tmp_path,
    recorded,
):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True, unix=True)
    targets = {
        "agent-bridge": m._RegisteredPluginTarget(
            context=None,
            activation=m._PluginActivation.ACTIVE,
        ),
    }

    m._update_modules(
        plugin_dir,
        "linux",
        None,
        force=True,
        targets=targets,
    )

    assert _installer_update_argv(recorded) is not None
    assert not any(call[:3] == ["copilot", "plugin", "update"] for call in recorded)


def test_unified_update_reconciles_activation_unknown_module(
    tmp_path,
    recorded,
):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True, unix=True)
    targets = {
        "agent-bridge": m._RegisteredPluginTarget(
            context=None,
            activation=m._PluginActivation.UNKNOWN,
        ),
    }

    m._update_modules(
        plugin_dir,
        "linux",
        None,
        force=True,
        targets=targets,
    )

    assert _installer_update_argv(recorded) is not None


def test_required_module_failure_returns_false(tmp_path, monkeypatch):
    plugin_dir = _make_tree(tmp_path, zero_downtime=True, unix=True)
    targets = {
        "agent-bridge": m._RegisteredPluginTarget(
            context=None,
            activation=m._PluginActivation.UNKNOWN,
        ),
    }
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *args, **kwargs: _Completed(1),
    )

    assert (
        m._update_modules(
            plugin_dir,
            "linux",
            None,
            force=True,
            targets=targets,
        )
        is False
    )
