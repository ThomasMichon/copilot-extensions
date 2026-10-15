"""``update`` records phases/targets through the shared update-telemetry lib
and maps its result onto the shared exit-code table."""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
from pathlib import Path

import pytest
import update_telemetry as ut

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import installer
from agent_worktrees import reconcile, update_cli, update_events, update_gate, update_options
from agent_worktrees import update_runtime as runtime

pytestmark = pytest.mark.guard


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
        "payload_refresh", "engine_runtime", "module_update", "runtime_reconcile", "binstubs"]
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


def test_required_binstub_failure_blocks_the_update(wired, monkeypatch, _update_home):
    def boom():
        raise RuntimeError("binstub dir unwritable")

    monkeypatch.setattr(installer, "reconcile_binstubs", boom)
    assert m.cmd_update(_args()) != 0
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.COMMAND_FAILED
    assert _phase_outcomes(_update_home)["binstubs"] == ut.COMMAND_FAILED
    assert "binstub dir unwritable" in status["last_failure"]["summary"]
    assert "project-commands" not in _advisories(_update_home)


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


def _payload(root: Path, name: str, scope: str | None) -> Path:
    path = root / name
    path.mkdir(parents=True)
    manifest = {"name": name} if scope is None else {"name": name, "runtimeScope": scope}
    (path / "plugin.json").write_text(json.dumps(manifest))
    return path


@pytest.fixture
def eligibility(tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    modules = ["mod-active", "mod-inactive", "mod-noscope", "mod-payload-only", "mod-gated"]
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": n, "source": n} for n in modules]}))
    for name, scope in (("mod-active", "universal"), ("mod-inactive", "universal"),
                        ("mod-noscope", None), ("mod-payload-only", "none"),
                        ("mod-gated", "machine-gated")):
        _payload(tmp_path / "plugins", name, scope)
    installed = {name: _payload(tmp_path / "installed", name, scope)
                 for name, scope in (("reg-active", "universal"), ("reg-payload-only", "none"),
                                     ("reg-inactive", "universal"))}
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", installed.get)
    monkeypatch.setattr(update_gate, "_machine_gate_blocks",
                        lambda name, scope, context: scope == "machine-gated")
    active = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)
    inactive = runtime._RegisteredPluginTarget(None, runtime._PluginActivation.INACTIVE)
    targets = {
        "mod-active": active, "mod-inactive": inactive, "mod-noscope": active,
        "mod-payload-only": active, "mod-gated": active, "reg-active": active,
        "reg-payload-only": active, "reg-inactive": inactive, "reg-missing": active,
    }
    return plugin, targets


def test_eligible_runtimes_follow_the_reconcile_plan(eligibility):
    plugin, targets = eligibility
    assert update_options.eligible_runtimes(_args(), targets, plugin) == {
        "agent-worktrees", "mod-active", "reg-active"}
    skipped = _args(skip_modules=["mod-active"])
    assert "mod-active" not in update_options.eligible_runtimes(skipped, targets, plugin)
    assert update_options.eligible_runtimes(_args(skip_modules=[]), targets, plugin) == {
        "agent-worktrees"}


@pytest.mark.parametrize("name", [
    "mod-inactive", "mod-noscope", "mod-payload-only", "mod-gated",
    "reg-payload-only", "reg-inactive", "reg-missing",
])
def test_force_runtime_that_would_be_skipped_is_exit_3(eligibility, _update_home, name):
    plugin, targets = eligibility
    update_events.start("engine")
    assert not update_options.check_forced_runtimes(
        _args(force_runtime=[name]), targets, plugin)
    assert update_events.finish(1) == 3
    assert name in _status(_update_home)["last_failure"]["summary"]


def test_force_runtime_for_a_deployable_target_passes(eligibility):
    plugin, targets = eligibility
    assert update_options.check_forced_runtimes(
        _args(force_runtime=["mod-active", "reg-active"]), targets, plugin)


@pytest.mark.parametrize(("status", "expected"), [
    ("agent-x: invalid installer deadline 'x'; expected integer seconds",
     (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("installation context invalid: bad receipt", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("machine detection failed: no registry", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("machine detection failed", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("invalid runtimeScope", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("'copilot' CLI not found on PATH (x)", (ut.CONFIGURATION_LIMITATION, "configuration")),
    ("agent-x: something unexpected", (ut.COMMAND_FAILED, None)),
    ("uninstall timed out", (ut.TIMEOUT, "timeout")),
    ("install exited 75", (ut.BLOCKED_BY_LOCK, "installer_exit_75")),
    ("plugin install exited 75", (ut.COMMAND_FAILED, None)),
    ("update exited 3", (ut.COMMAND_FAILED, None)),
    ("uninstall exited 1", (ut.COMMAND_FAILED, None)),
    ("payload not installed", (ut.COMMAND_FAILED, None)),
    ("SKIPPED (not allowed on this machine)", (ut.SKIPPED, None)),
])
def test_every_produced_status_has_its_documented_outcome(status, expected):
    assert update_events.classify_status(status) == expected


def test_payload_install_retry_timeout_is_a_timeout(monkeypatch):
    from agent_worktrees import activation_preservation

    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: Path("/inst/x"))
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 3, "", "boom"))

    def timeout(argv, identity, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))

    monkeypatch.setattr(activation_preservation, "run_install_preserving_activation", timeout)
    status = m._update_one_plugin_payload("agent-x", "copilot-extensions")
    assert status == "timed out"
    assert update_events.classify_status(status) == (ut.TIMEOUT, "timeout")



def test_missing_copilot_cli_is_configuration_exit_3(wired, monkeypatch, _update_home):
    real_run = subprocess.run

    def run(argv, *a, **k):
        if "plugin" in map(str, argv):
            raise FileNotFoundError(2, "No such file or directory", str(argv[0]))
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    assert m.cmd_update(_args()) == 3
    status = _status(_update_home)
    assert status["run"]["outcome"] == ut.CONFIGURATION_LIMITATION
    assert _phase_outcomes(_update_home)["payload_refresh"] == ut.CONFIGURATION_LIMITATION
    assert "not found" in status["plugins"]["agent-worktrees@copilot-extensions"]["detail"]


def _module_payload(tmp_path, monkeypatch):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps(
        {"modules": [{"name": "agent-example", "source": "agent-example"}]}))
    module = tmp_path / "agent-example"
    (module / "scripts").mkdir(parents=True)
    (module / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (module / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "runtimeScope": "universal"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "3.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, tmp_path / "module-runtime"))
    monkeypatch.setattr(reconcile, "_zero_downtime_update", lambda path: False)
    launched = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: launched.append(a))
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        None, runtime._PluginActivation.ACTIVE)}
    return plugin, targets, launched


def _run_modules(plugin, targets, home):
    update_events.start("engine")
    ok = runtime._update_modules(plugin, "linux", None, targets=targets)
    code = update_events.finish(0 if ok else 1)
    return ok, code, _status(home)


@pytest.mark.parametrize(("error", "code"), [
    (PermissionError(errno.EACCES, "Permission denied", "plugin.json"), ut.PERMISSION_DENIED),
    (OSError(errno.EIO, "Input/output error", "plugin.json"), ut.IO_ERROR),
], ids=["permission", "io"])
def test_module_unreadable_deadline_manifest_keeps_its_os_error_class(
    tmp_path, monkeypatch, _update_home, error, code,
):
    plugin, targets, launched = _module_payload(tmp_path, monkeypatch)

    def unreadable(*a, **k):
        raise error

    monkeypatch.setattr(runtime, "_installer_timeout", unreadable)
    ok, exit_code, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and launched == []
    assert (exit_code, target["outcome"], target["error_code"]) == (1, ut.COMMAND_FAILED, code)
    assert "plugin.json" in target["detail"]


def test_module_installation_context_error_records_its_cause(
    tmp_path, monkeypatch, _update_home,
):
    plugin, targets, _launched = _module_payload(tmp_path, monkeypatch)

    def invalid(*a, **k):
        raise ValueError("receipt names https://user:hunter2@example.com/missing")

    monkeypatch.setattr(reconcile, "runtime_installer_environment", invalid)
    ok, exit_code, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and exit_code == 3
    assert "example.com/missing" in target["detail"]
    assert "hunter2" not in json.dumps(status)
    assert "hunter2" not in Path(status["run"]["events_path"]).read_text(encoding="utf-8")


def test_engine_installation_context_error_records_its_cause(wired, monkeypatch, _update_home):
    def invalid(*a, **k):
        raise ValueError("receipt names https://user:hunter2@example.com/missing")

    monkeypatch.setattr(reconcile, "runtime_installer_environment", invalid)
    assert m.cmd_update(_args()) == 3
    status = _status(_update_home)
    assert "example.com/missing" in status["plugins"][
        "agent-worktrees@copilot-extensions"]["detail"]
    assert "example.com/missing" in status["last_failure"]["summary"]
    assert "hunter2" not in json.dumps(status)
    assert "hunter2" not in Path(status["run"]["events_path"]).read_text(encoding="utf-8")


def test_engine_unreadable_deadline_manifest_is_not_a_configuration_error(
    wired, monkeypatch, _update_home,
):
    def unreadable(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device", "plugin.json")

    monkeypatch.setattr(update_cli, "_installer_timeout", unreadable)
    assert m.cmd_update(_args()) == 1
    aw = _status(_update_home)["plugins"]["agent-worktrees@copilot-extensions"]
    assert aw["outcome"] == ut.COMMAND_FAILED and "No space left" in aw["detail"]


def test_registered_runtime_invalid_declared_deadline_is_configuration(
    registered_runtime, monkeypatch, tmp_path, _update_home,
):
    environment = {k: v for k, v in os.environ.items()
                   if k != "COPILOT_EXTENSIONS_CONTEXT" and not k.endswith("_DEADLINE_SEC")}
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, registered_runtime))
    (tmp_path / "agent-example" / "plugin.json").write_text(
        json.dumps({"name": "agent-example", "installerDeadlineSeconds": 0}))
    update_events.start("engine")
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=False)
    assert status.startswith("invalid installer deadline: ")
    assert update_events.finish(1) == 3


@pytest.mark.parametrize(("cause", "exit_code", "code"), [
    ("[Errno 13] Permission denied: 'machines.yaml'", 1, ut.PERMISSION_DENIED),
    ("an explicit execution space is required", 3, "configuration"),
], ids=["permission", "configuration"])
def test_module_machine_detection_failure_records_its_cause(
    tmp_path, monkeypatch, _update_home, cause, exit_code, code,
):
    plugin, targets, launched = _module_payload(tmp_path, monkeypatch)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "machine-gated")

    def undetectable(*a, **k):
        raise runtime.MachineDetectionError(cause)

    monkeypatch.setattr(runtime, "_machine_gate_blocks", undetectable)
    ok, actual, status = _run_modules(plugin, targets, _update_home)
    target = status["plugins"]["agent-example@copilot-extensions"]
    assert ok is False and launched == [] and actual == exit_code
    assert target["error_code"] == code and cause in target["detail"]


def test_force_runtime_with_unreadable_modules_manifest_keeps_the_os_error(
    tmp_path, monkeypatch, _update_home,
):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    manifest = plugin / "modules.json"
    manifest.write_text(json.dumps({"modules": [{"name": "mod-a"}]}))
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)
    targets = {"mod-a": runtime._RegisteredPluginTarget(None, runtime._PluginActivation.ACTIVE)}
    update_events.start("engine")
    assert not update_options.check_forced_runtimes(
        _args(force_runtime=["mod-a"]), targets, plugin)
    assert update_events.finish(1) == 1
    assert _status(_update_home)["run"]["outcome"] == ut.COMMAND_FAILED
    forced = [e for e in _events(_update_home) if e["component"] == "force-runtime"]
    assert forced[-1]["error"]["code"] == ut.PERMISSION_DENIED
