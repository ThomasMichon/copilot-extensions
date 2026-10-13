"""``update`` records phases/targets through the shared update-telemetry lib
and maps its result onto the shared exit-code table."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest
import update_telemetry as ut

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import reconcile, update_cli, update_events, update_options
from agent_worktrees import update_runtime as runtime


@pytest.fixture(autouse=True)
def _update_home(tmp_path, monkeypatch):
    for key in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "update-home"
    monkeypatch.setenv(ut.ENV_HOME, str(home))
    yield home
    assert update_events._run is None, "every update run is finished"


def _status(home: Path) -> dict:
    return json.loads((home / "update-status.json").read_text(encoding="utf-8"))


def _events(home: Path) -> list[dict]:
    status = _status(home)
    return ut.read_events(Path(status["run"]["events_path"]))[0]


def _args(**overrides) -> argparse.Namespace:
    values = dict(recreate_venv=False, skip_modules=None, no_anchor_sync=True,
                  no_prune_pivots=True, force=False, no_manager=True)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "agent-worktrees"
    (plugin_dir / "scripts").mkdir(parents=True)
    (plugin_dir / "scripts" / "install.sh").write_text("#!/usr/bin/env bash\n")
    installer_rc = {"value": 0}

    def run(argv, *a, **k):
        rc = installer_rc["value"] if "install.sh" in " ".join(map(str, argv)) else 0
        if rc == "timeout":
            raise subprocess.TimeoutExpired(argv, k.get("timeout"))
        return subprocess.CompletedProcess(argv, rc, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(m, "_registered_plugin_targets", lambda: {})
    monkeypatch.setattr(m, "_update_registered_plugins", lambda targets: None)
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: plugin_dir)
    monkeypatch.setattr(m, "_project_update_context", lambda: plugin_dir)
    monkeypatch.setattr(m, "_invocation_update_context", lambda: None)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(reconcile, "payload_version", lambda d: "1.0.1")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1.0.0")
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *a, **k: (dict(os.environ), tmp_path / "aw-runtime"),
    )
    return installer_rc


def test_phase_events_emitted(wired, _update_home):
    assert m.cmd_update(_args()) == 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.VERIFIED_CURRENT
    assert status["run"]["exit_code"] == 0
    phases = [p["phase"] for p in status["run"]["phases"]]
    assert phases == [
        "payload_refresh", "engine_runtime", "binstubs", "module_update", "runtime_reconcile"]
    events = _events(_update_home)
    assert {e["proc"] for e in events} == {"engine"}
    starts = [e["phase"] for e in events if e["component"] == "run" and e["event"] == "start"]
    ends = [e["phase"] for e in events if e["component"] == "run" and e["event"] == "end"]
    assert sorted(starts) == sorted(ends)
    aw = status["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.VERIFIED_CURRENT and aw["changed"] is True
    assert not any(key in os.environ for key in (ut.ENV_RUN_ID, ut.ENV_EVENTS))


def test_installer_75_is_blocked_by_lock(wired, _update_home):
    wired["value"] = 75
    assert m.cmd_update(_args()) == 5
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.BLOCKED_BY_LOCK
    assert status["plugins"]["agent-worktrees@copilot-extensions"]["notes"] == [
        "installer_exit_75"]
    assert update_events.classify_status("installer exited 76")[0] == ut.COMMAND_FAILED


def test_failed_step_keeps_legacy_failure_exit(wired, monkeypatch, _update_home):
    monkeypatch.setattr(m, "_update_modules", lambda *a, **k: False)
    assert m.cmd_update(_args()) == 1
    assert _status(_update_home)["last_failure"]["outcome"] == ut.COMMAND_FAILED


def test_auth_required_exit_4(_update_home):
    update_events.start("engine")
    update_events._run.target("auth_report", "source:example.com/owner", ut.AUTH_REQUIRED)
    assert update_events.finish(1) == 4
    assert _status(_update_home)["run"]["outcome"] == ut.AUTH_REQUIRED


def test_telemetry_failure_never_changes_result(wired, monkeypatch, tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    monkeypatch.setenv(ut.ENV_HOME, str(blocker / "home"))
    assert m.cmd_update(_args()) == 0


@pytest.fixture
def registered_runtime(tmp_path, monkeypatch):
    payload = tmp_path / "agent-example"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (payload / "plugin.json").write_text(json.dumps({"name": "agent-example"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    root = tmp_path / "runtime"
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "universal")
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, root))

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    return root


_SLOT_STATES = [None, "complete-active", "complete-not-active"]


def _write_slot(root: Path, version: str, state: str | None) -> None:
    """A pre-existing completed slot proves nothing about this attempt."""
    if state is None:
        return
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps(
        {"version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1}))
    active = version if state == "complete-active" else "0.0.1"
    (root / "current-version").write_text(active)


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_registered_runtime_timeout_is_timeout_whatever_the_slot(
    registered_runtime, _update_home, slot,
):
    _write_slot(registered_runtime, "2.0.0", slot)
    update_events.start("engine")
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=False)
    assert status == "timed out"
    assert update_events.finish(1) == 6
    target = _status(_update_home)["plugins"]["agent-example@copilot-extensions"]
    assert target["outcome"] == ut.TIMEOUT


def test_timeout_without_slot_exits_6(registered_runtime, _update_home):
    update_events.start("engine")
    assert runtime._reconcile_one_runtime("agent-example", "linux", force=False) == "timed out"
    assert update_events.finish(1) == 6


def test_force_runtime_option_parses_forwards_and_selects(monkeypatch, tmp_path):
    parser = argparse.ArgumentParser()
    update_cli.add_parsers(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(
        ["update", "--force-runtime", "agent-a", "--force-runtime", "agent-b"])
    assert update_options.forced_runtimes(args) == {"agent-a", "agent-b"}
    assert update_cli._update_flags(args) == [
        "--force-runtime", "agent-a", "--force-runtime", "agent-b"]

    seen = {}
    monkeypatch.setattr(m, "_module_names", lambda plugin_dir: set())
    monkeypatch.setattr(
        m, "_reconcile_one_runtime",
        lambda name, plat, *, force: (seen.__setitem__(name, force), "OK")[1],
    )
    targets = {
        name: runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)
        for name in ("agent-a", "agent-c")
    }
    assert runtime._reconcile_registered_runtimes(
        tmp_path, "linux", targets=targets, force_runtimes=frozenset({"agent-a"}))
    assert seen == {"agent-a": True, "agent-c": False}


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_engine_installer_timeout_exits_6(wired, tmp_path, _update_home, slot):
    wired["value"] = "timeout"
    _write_slot(tmp_path / "aw-runtime", "1.0.1", slot)
    assert m.cmd_update(_args()) == 6
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.TIMEOUT
    phase = {p["phase"]: p for p in status["run"]["phases"]}["engine_runtime"]
    assert phase["outcome"] == ut.TIMEOUT
    aw = status["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.TIMEOUT


@pytest.mark.parametrize("slot", _SLOT_STATES)
def test_module_installer_timeout_is_timeout_whatever_the_slot(
    tmp_path, monkeypatch, _update_home, slot,
):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    module = tmp_path / "agent-example"
    (module / "scripts").mkdir(parents=True)
    (module / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    root = tmp_path / "module-runtime"
    _write_slot(root, "3.0.0", slot)
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "3.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, root))
    monkeypatch.setattr(reconcile, "_zero_downtime_update", lambda path: False)

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(runtime.subprocess, "run", timeout)
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        None, runtime._PluginActivation.ACTIVE)}
    update_events.start("engine")
    ok = runtime._update_modules(plugin, "linux", None, targets=targets)
    code = update_events.finish(0 if ok else 1)
    target = _status(_update_home)["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and code == 6
    assert target["outcome"] == ut.TIMEOUT


@pytest.mark.parametrize(("status", "expected"), [
    ("plugin update failed: Access is denied. (os error 5)",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Permission denied", (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: The process cannot access the file because it is being "
     "used by another process. (os error 32)", (ut.BLOCKED_BY_LOCK, ut.SHARING_VIOLATION)),
    ("installer exited 75", (ut.BLOCKED_BY_LOCK, "installer_exit_75")),
    ("timed out", (ut.TIMEOUT, "timeout")),
])
def test_status_classification_needs_contention_evidence(status, expected):
    assert update_events.classify_status(status) == expected
