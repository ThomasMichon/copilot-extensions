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
        lambda name, plat, *, force, gate_context=None: (seen.__setitem__(name, force), "OK")[1],
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
    (module / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "runtimeScope": "universal"}))
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
    ("plugin update failed: [Errno 5] Input/output error", (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: [WinError 33] The process cannot access the file because "
     "another process has locked a portion of the file", (ut.BLOCKED_BY_LOCK, "lock_violation")),
    ("plugin update failed: (os error 33)", (ut.BLOCKED_BY_LOCK, "lock_violation")),
    ("plugin update failed: [WinError 32] sharing", (ut.BLOCKED_BY_LOCK, ut.SHARING_VIOLATION)),
    ("plugin update failed: [WinError 5] Zugriff verweigert",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Zugriff verweigert (os error 5)",
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [WinError 50] Anfrage nicht unterstuetzt", (ut.COMMAND_FAILED, None)),
    ("plugin update failed: (os error 330)", (ut.COMMAND_FAILED, None)),
])
def test_status_classification_needs_contention_evidence(monkeypatch, status, expected):
    monkeypatch.setattr(update_events, "_WINDOWS", True)  # Windows-shaped messages
    assert update_events.classify_status(status) == expected


@pytest.mark.parametrize(("status", "windows", "expected"), [
    ("plugin update failed: [Errno 13] Zugriff verweigert", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [Errno 1] Vorgang nicht zulaessig", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: [Errno 5] Ein-/Ausgabefehler", False,
     (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: [Errno 130] Andere", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: Fehler (os error 5)", False, (ut.COMMAND_FAILED, ut.IO_ERROR)),
    ("plugin update failed: Fehler (os error 5)", True,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Fehler (os error 13)", False,
     (ut.COMMAND_FAILED, ut.PERMISSION_DENIED)),
    ("plugin update failed: Fehler (os error 33)", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: child process exited 75", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: rpc deadline exceeded", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: package not found", False, (ut.COMMAND_FAILED, None)),
    ("plugin update failed: 'copilot' CLI not found on PATH (x)", False,
     (ut.CONFIGURATION_LIMITATION, "configuration")),
])
def test_status_classification_is_locale_and_host_aware(monkeypatch, status, windows, expected):
    monkeypatch.setattr(update_events, "_WINDOWS", windows)
    assert update_events.classify_status(status) == expected


def test_inactive_module_stays_advisory(tmp_path, monkeypatch, _update_home):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    inactive = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.INACTIVE)
    update_events.start("engine")
    update_events.enter("payload_refresh")
    update_events.record("payload_refresh", "agent-example@copilot-extensions",
                         "plugin state error: boom", required=False)
    update_events.enter("module_update")
    assert runtime._update_modules(plugin, "linux", None, targets={"agent-example": inactive})
    assert update_events.finish(0) == 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.VERIFIED_CURRENT
    entry = status["plugins"]["agent-example@copilot-extensions"]
    assert entry["required"] is False
    assert [a["component"] for a in status["advisories"]] == ["agent-example@copilot-extensions"]


def _phase_outcomes(home: Path) -> dict[str, str]:
    return {p["phase"]: p["outcome"] for p in _status(home)["run"]["phases"]}


def test_missing_engine_installer_is_configuration_exit_3(wired, tmp_path, _update_home):
    (tmp_path / "agent-worktrees" / "scripts" / "install.sh").unlink()
    assert m.cmd_update(_args()) == 3
    assert _phase_outcomes(_update_home)["engine_runtime"] == ut.CONFIGURATION_LIMITATION


def test_invalid_engine_deadline_is_configuration_exit_3(wired, monkeypatch, _update_home):
    monkeypatch.setenv("AGENT_WORKTREES_INSTALL_DEADLINE_SEC", "not-a-number")
    assert m.cmd_update(_args()) == 3
    assert _phase_outcomes(_update_home)["engine_runtime"] == ut.CONFIGURATION_LIMITATION


def test_missing_installed_payload_fails_its_phase(wired, monkeypatch, _update_home):
    monkeypatch.setattr(m, "_find_installed_plugin_dir", lambda: None)
    assert m.cmd_update(_args()) == 1
    assert _phase_outcomes(_update_home)["payload_refresh"] == ut.COMMAND_FAILED


def test_unreadable_modules_manifest_fails_module_update(wired, monkeypatch, tmp_path,
                                                         _update_home):
    (tmp_path / "agent-worktrees" / "modules.json").write_text("{not json")
    monkeypatch.setattr(m, "_update_modules", runtime._update_modules)
    assert m.cmd_update(_args()) == 1
    phases = _phase_outcomes(_update_home)
    assert phases["module_update"] == ut.COMMAND_FAILED
    assert phases["engine_runtime"] == ut.VERIFIED_CURRENT
    assert _status(_update_home)["last_failure"]["summary"].startswith("modules.json")


def _advisories(home: Path) -> dict[str, str]:
    return {a["component"]: a["summary"] for a in _status(home)["advisories"]}


def test_binstub_failure_is_an_advisory(wired, monkeypatch, _update_home):
    def boom():
        raise RuntimeError("binstub dir unwritable")

    monkeypatch.setattr(update_cli.inst, "reconcile_binstubs", boom)
    assert m.cmd_update(_args()) == 0
    assert "binstub dir unwritable" in _advisories(_update_home)["binstubs"]


@pytest.mark.parametrize("failure", ["import", "scan"])
def test_prune_failure_is_an_advisory(wired, monkeypatch, _update_home, failure):
    import sys

    from agent_worktrees.picker_support import pivots

    if failure == "import":
        monkeypatch.setitem(sys.modules, "agent_worktrees.picker_support.pivots", None)
        monkeypatch.delattr("agent_worktrees.picker_support.pivots", raising=False)
    else:
        def boom(**_kw):
            raise RuntimeError("registry scan failed")

        monkeypatch.setattr(pivots, "scan_pivot_registry", boom)
    monkeypatch.setattr(m, "_prune_stale_pivots_after_update",
                        update_cli._prune_stale_pivots_after_update)
    assert m.cmd_update(_args(no_prune_pivots=False)) == 0
    assert "pivot prune failed" in _advisories(_update_home)["pivots"]


def test_unknown_force_runtime_is_a_configuration_error(wired, monkeypatch, _update_home):
    ran = []
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda argv, *a, **k: (ran.append(argv),
                                                                   real_run(argv, *a, **k))[1])
    assert m.cmd_update(_args(force_runtime=["agent-typo", "agent-worktrees"])) == 3
    assert not any("install.sh" in " ".join(map(str, argv)) for argv in ran)
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.CONFIGURATION_LIMITATION
    assert "agent-typo" in status["last_failure"]["summary"]
    assert "agent-worktrees" not in status["last_failure"]["summary"].split("not found:")[1]


def test_eligible_force_runtime_redeploys(wired, monkeypatch, _update_home):
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "1.0.1")
    assert m.cmd_update(_args(force_runtime=["agent-worktrees"])) == 0
    aw = _status(_update_home)["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["changed"] is True


def test_eligible_runtimes_cover_modules_and_active_registrations():
    active = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)
    inactive = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.INACTIVE)
    targets = {"agent-a": active, "agent-off": inactive}
    args = _args(force_runtime=["agent-a"])
    assert update_options.eligible_runtimes(args, targets, {"agent-mod"}) == {
        "agent-worktrees", "agent-a", "agent-mod"}
    skipped = _args(skip_modules=["agent-mod"])
    assert "agent-mod" not in update_options.eligible_runtimes(skipped, targets, {"agent-mod"})
    assert update_options.eligible_runtimes(_args(skip_modules=[]), targets, {"agent-mod"}) == {
        "agent-worktrees"}
