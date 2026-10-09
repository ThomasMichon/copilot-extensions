"""Unified update leaves the installer watchdog its deadline and cleanup grace."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import reconcile, update_runtime as runtime

pytestmark = pytest.mark.guard


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, 1080),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 930),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "600",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 630),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "0"}, 510),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "-1"}, 510),
    ],
)
def test_dispatch_effective_default_and_overrides(environment, expected):
    payload = Path(__file__).resolve().parents[2] / "agent-dispatch"
    default = json.loads((payload / "plugin.json").read_text())["installerDeadlineSeconds"]
    assert default == 1050
    for script, pattern in [
        ("install.ps1", r"AGENT_DISPATCH_INSTALL_DEADLINE_SEC = '(\d+)'"),
        ("install.sh", r"AGENT_DISPATCH_INSTALL_DEADLINE_SEC=(\d+)"),
    ]:
        match = re.search(pattern, (payload / "scripts" / script).read_text())
        assert match and int(match[1]) == default
    assert runtime._installer_timeout("agent-dispatch", environment, payload) == expected


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, 510),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 930),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "1"}, 31),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "600",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 630),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 930),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "0",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 510),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "-1"}, 510),
    ],
)
def test_watchdog_precedence_and_finite_disabled_fallback(environment, expected):
    assert runtime._installer_timeout("agent-example", environment) == expected


@pytest.mark.parametrize("raw", ["invalid", "1.5", "inf"])
def test_invalid_deadline_is_explicit(raw):
    with pytest.raises(ValueError, match="invalid installer deadline"):
        runtime._installer_timeout("agent-example", {"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": raw})


@pytest.fixture
def payloads(tmp_path, monkeypatch):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps({
        "modules": [{"name": "agent-example", "source": "agent-example"}],
    }))
    payload = tmp_path / "agent-example"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "install.ps1").write_text("param($Action)\nexit 124\n")
    (payload / "scripts" / "install.sh").write_text("#!/bin/sh\nexit 124\n")
    (payload / "plugin.json").write_text(json.dumps({"name": "agent-example"}))
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "900"
    environment.pop("AGENT_EXAMPLE_INSTALL_DEADLINE_SEC", None)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "universal")
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "2")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1")
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *a: (environment, tmp_path / "runtime"),
    )
    return plugin, environment


def test_registered_runtime_and_module_fallback_use_selected_deadline(payloads, monkeypatch):
    plugin, environment = payloads
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 2 else 0)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime._reconcile_one_runtime("agent-example", "linux", force=True) == "OK"
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert runtime._update_modules(plugin, "linux", None, force=True, targets=targets)
    assert len(calls) == 3
    assert all(call["timeout"] == 930 and call["env"] is environment for call in calls)


def test_declared_default_reaches_registered_and_module_installers(payloads, monkeypatch):
    plugin, environment = payloads
    environment.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    (plugin.parent / "agent-example" / "plugin.json").write_text(json.dumps({
        "name": "agent-example", "installerDeadlineSeconds": 840,
    }))
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 2 else 0)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime._reconcile_one_runtime("agent-example", "linux", force=True) == "OK"
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert runtime._update_modules(plugin, "linux", None, force=True, targets=targets)
    assert len(calls) == 3 and all(call["timeout"] == 870 for call in calls)


@pytest.mark.parametrize("value", [0, -1, True, "1050", 1.5, None])
def test_invalid_declared_deadline_is_explicit(payloads, value):
    plugin, environment = payloads
    environment.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    payload = plugin.parent / "agent-example"
    (payload / "plugin.json").write_text(json.dumps({"installerDeadlineSeconds": value}))
    with pytest.raises(ValueError, match="installerDeadlineSeconds must be a positive integer"):
        runtime._installer_timeout("agent-example", environment, payload)


def test_invalid_deadline_does_not_launch_runtime_or_module(payloads, monkeypatch):
    plugin, environment = payloads
    environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "invalid"
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: calls.append(a))
    assert "invalid installer deadline" in runtime._reconcile_one_runtime(
        "agent-example", "linux", force=True,
    )
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert not runtime._update_modules(plugin, "linux", None, force=True, targets=targets)
    assert calls == []


def test_current_runtime_skip_does_not_validate_unused_deadline(payloads, monkeypatch):
    plugin, environment = payloads
    environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "invalid"
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: calls.append(a))
    assert runtime._reconcile_one_runtime(
        "agent-example", "linux", force=False,
    ) == "SKIPPED (current)"
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert runtime._update_modules(plugin, "linux", None, force=False, targets=targets)
    assert calls == []


@pytest.mark.skipif(os.name != "nt", reason="real Windows installer-process boundary")
def test_real_windows_installer_failure_survives_watchdog_cleanup_budget(payloads, monkeypatch):
    _, environment = payloads
    environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "1"
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("PowerShell unavailable")
    original_run = subprocess.run
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs)
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return original_run(argv, **kwargs)

    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime._reconcile_one_runtime(
        "agent-example", "windows", force=True,
    ) == "installer exited 124"
    assert calls[0]["timeout"] == 31
    assert calls[0]["env"]["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] == "1"
