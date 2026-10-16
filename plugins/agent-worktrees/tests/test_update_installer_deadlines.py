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
        ({}, 2580),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 2430),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "600",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 2130),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "0"}, 2010),
        ({"AGENT_DISPATCH_INSTALL_DEADLINE_SEC": "-1"}, 2010),
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


@pytest.mark.parametrize("name,minimum", [
    ("agent-dispatch", 450 + 420 + 60 + 300),
    ("agent-index", 420 + 60 + 300),
    ("agent-mcp", 420 + 60 + 300),
    ("agent-worktrees", 300 + 420 + 60 + 300),
    ("agent-bridge", 540 + 180 + 300),
])
def test_declared_daemon_cutover_and_outer_budget_cover_lifecycle(name, minimum):
    payload = Path(__file__).resolve().parents[2] / name
    manifest = json.loads((payload / "plugin.json").read_text())
    build = manifest.get("installerDeadlineSeconds", 480)
    cutover = manifest.get("cutoverDeadlineSeconds", 300)
    assert cutover >= minimum
    assert runtime._installer_timeout(name, {}, payload) == build + cutover + 30
    assert runtime._installer_timeout(
        name, {"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "1"}, payload,
    ) == 1 + cutover + 30
    assert runtime._installer_timeout(
        name, {"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "0"}, payload,
    ) == 480 + cutover + 30


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, 810),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 1230),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "1"}, 331),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "600",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 930),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 1230),
        ({"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "0",
          "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "900"}, 810),
        ({"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "-1"}, 810),
    ],
)
def test_watchdog_precedence_and_finite_disabled_fallback(environment, expected):
    assert runtime._installer_timeout("agent-example", environment) == expected


@pytest.mark.parametrize("raw", ["invalid", "1.5", "inf"])
def test_invalid_deadline_is_explicit(raw):
    with pytest.raises(ValueError, match="invalid installer deadline"):
        runtime._installer_timeout("agent-example", {"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": raw})


@pytest.mark.parametrize("raw", ["2147483648", "-2147483649", "18446744073709551616"])
def test_deadline_cannot_overflow_native_seconds(raw):
    with pytest.raises(ValueError, match="32-bit integer"):
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
    assert all(call["timeout"] == 1230 and call["env"] == environment for call in calls)


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
    assert len(calls) == 3 and all(call["timeout"] == 1170 for call in calls)


@pytest.mark.parametrize("value", [0, -1, True, "1050", 1.5, None, 2147483648])
def test_invalid_declared_deadline_is_explicit(payloads, value):
    plugin, environment = payloads
    environment.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    payload = plugin.parent / "agent-example"
    (payload / "plugin.json").write_text(json.dumps({"installerDeadlineSeconds": value}))
    with pytest.raises(ValueError, match="installerDeadlineSeconds must be a positive integer"):
        runtime._installer_timeout("agent-example", environment, payload)


@pytest.mark.parametrize("override, expected", [(None, 990), ("900", 1050), ("0", 630)])
def test_outer_deadline_includes_declared_cutover_even_with_build_override(
    payloads, override, expected,
):
    plugin, environment = payloads
    if override is None:
        environment.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    else:
        environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = override
    payload = plugin.parent / "agent-example"
    (payload / "plugin.json").write_text(json.dumps({
        "installerDeadlineSeconds": 840, "cutoverDeadlineSeconds": 120,
    }))
    assert runtime._installer_timeout("agent-example", environment, payload) == expected


@pytest.mark.parametrize("value", [0, -1, True, "120", 1.5, None, 18446744073709551616])
def test_invalid_cutover_budget_is_explicit_with_build_override(payloads, value):
    plugin, environment = payloads
    payload = plugin.parent / "agent-example"
    (payload / "plugin.json").write_text(json.dumps({"cutoverDeadlineSeconds": value}))
    with pytest.raises(ValueError, match="cutoverDeadlineSeconds must be a positive integer"):
        runtime._installer_timeout("agent-example", environment, payload)


def test_unreadable_manifest_does_not_silently_shorten_the_deadline(payloads, monkeypatch):
    plugin, environment = payloads
    environment.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    payload = plugin.parent / "agent-example"
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == payload / "plugin.json":
            raise PermissionError("installer default declaration unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError, match="default declaration unavailable"):
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
    # Exit 124 is the installer's own watchdog deadline; without a published
    # receipt that is a build-phase timeout.
    assert runtime._reconcile_one_runtime(
        "agent-example", "windows", force=True,
    ) == "timed out (deadline hit during build)"
    assert calls[0]["timeout"] == 331
    assert calls[0]["env"]["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] == "1"
