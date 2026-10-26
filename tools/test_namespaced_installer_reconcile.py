"""Run real native entrypoints with private context responses and cleanup witnesses."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_installer_instance_reconcile import ENGINE, REPO, _bash


def _native_path(path: Path, style: str) -> str:
    value = path.as_posix()
    if style == "sh" and os.name == "nt":
        value = "/" + value[0].lower() + value[2:]
    return value


@pytest.mark.parametrize("style", ("sh", "ps1"))
@pytest.mark.parametrize(
    "state", ("fresh", "healthy", "missing-context", "wrong-mode", "denied-receipt", "bad-cutover"),
)
def test_actual_machines_cell_provision_reconciles_only_after_admission(tmp_path, style, state):
    shell = _bash() if style == "sh" else (
        str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        if os.name == "nt" else shutil.which("pwsh")
    )
    if not shell:
        pytest.skip("native shell unavailable; never substitute WSL")
    cell = tmp_path / "cell"
    snapshot = cell / "snapshots" / "9.9.9"
    scripts = snapshot / "scripts"
    context_scripts = scripts / "installation-context"
    context_scripts.mkdir(parents=True)
    (cell / "versions").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (snapshot / "pyproject.toml").write_text(
        '[project]\nname = "agent-machines"\nversion = "9.9.9"\n', encoding="utf-8",
    )
    shutil.copyfile(REPO / "plugins/agent-machines/scripts" / f"init.{style}", scripts / f"init.{style}")
    shutil.copyfile(ENGINE / f"installer-instance-reconcile.{style}", scripts / f"installer-instance-reconcile.{style}")
    if style == "ps1":
        shutil.copyfile(
            REPO / "libs/versioned-runtime/versioned_runtime_native.ps1",
            scripts / "versioned_runtime_native.ps1",
        )
    shutil.copyfile(
        REPO / "plugins/agent-machines/scripts/installation-context/json-query.awk",
        context_scripts / "json-query.awk",
    )
    if state in {"healthy", "bad-cutover"}:
        (cell / "current-version").write_text("9.9.9\n", encoding="utf-8")
    context = cell / "install.json"
    context.write_text("private synthetic receipt\n", encoding="utf-8")
    calls = tmp_path / "calls"
    native = lambda path: _native_path(path, style)
    validated = {
        "pluginRoot": native(cell), "snapshotsRoot": native(cell / "snapshots"),
        "versionsRoot": native(cell / "versions"), "namespaceGeneration": 1, "generation": 1,
    }
    env = dict(os.environ)
    for key in (
        "PYTEST_CURRENT_TEST", "COPILOT_EXTENSIONS_TEST_CONTAINED",
        "COPILOT_PLUGIN_STAGED_FROM", "AGENT_MACHINES_EXPECTED_SOURCE_IDENTITY",
        "AGENT_MACHINES_CELL_PROVISION_LOCK_HELD", "AGENT_MACHINES_CELL_PROVISION_LOCK_SMOKE",
    ):
        env.pop(key, None)
    env.update({
        "HOME": native(home), "USERPROFILE": str(home), "COPILOT_PLUGIN_INSTALL_STAGED": "fixture",
        # Exercise the locked native transaction, not cross-process lock contention.
        "AGENT_MACHINES_CELL_PROVISION_LOCK_HELD": "1",
        "FIXTURE_CALLS": native(calls), "FIXTURE_VALIDATED": json.dumps(validated),
        "FIXTURE_STATE": state,
    })
    if style == "sh":
        runner = """#!/usr/bin/env bash
printf '%s\\n' "$1" >> "$FIXTURE_CALLS"
case "$1" in
    validate) [[ "$FIXTURE_STATE" != denied-receipt ]] || exit 7
        printf '%s\\n' "$FIXTURE_VALIDATED" ;;
    status) mode=namespaced; [[ "$FIXTURE_STATE" != wrong-mode ]] || mode=legacy
        printf '{"status":"ready","reason":"namespaced-active","actualMode":"%s"}\\n' "$mode" ;;
    slot-cutover) [[ "$FIXTURE_STATE" != bad-cutover ]] || exit 8
        printf '{"status":"ready"}\\n' ;;
    slot-provision) printf '{"status":"ready"}\\n' ;;
    *) exit 91 ;;
esac
"""
        backend = """reconcile_daemon_instances() {
    printf 'gc:%s:%s\\n' "$1" "$2" >> "$FIXTURE_CALLS"
}
"""
        command = [shell, str(scripts / "init.sh"), "cell-provision"]
        options = ["--expected-marketplace-id", "fixture--0123456789abcdef",
                   "--durable-home", native(tmp_path)]
        if state != "missing-context":
            options += ["--context", native(context)]
    else:
        runner = """
$action = $args[0]
[IO.File]::AppendAllText($env:FIXTURE_CALLS, $action + "`n")
switch ($action) {
    validate {
        if ($env:FIXTURE_STATE -eq 'denied-receipt') { exit 7 }
        [Console]::Out.WriteLine($env:FIXTURE_VALIDATED)
    }
    status {
        $mode = if ($env:FIXTURE_STATE -eq 'wrong-mode') { 'legacy' } else { 'namespaced' }
        @{status='ready'; reason='namespaced-active'; actualMode=$mode} | ConvertTo-Json -Compress
    }
    slot-cutover {
        if ($env:FIXTURE_STATE -eq 'bad-cutover') { exit 8 }
        '{"status":"ready"}'
    }
    slot-provision { '{"status":"ready"}' }
    default { exit 91 }
}
exit 0
"""
        backend = """
function Invoke-DaemonInstanceReconcile {
    param($RegistryRoot, $Limit, $MaxSeconds)
    [IO.File]::AppendAllText($env:FIXTURE_CALLS, "gc:${Limit}:${MaxSeconds}`n")
}
"""
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(scripts / "init.ps1"), "-Action", "cell-provision"]
        options = ["-ExpectedMarketplaceId", "fixture--0123456789abcdef",
                   "-DurableHome", str(tmp_path)]
        if state != "missing-context":
            options += ["-Context", str(context)]
    (context_scripts / f"installation-context.{style}").write_bytes(runner.encode())
    (scripts / f"daemon_instances.{style}").write_bytes(backend.encode())
    original_receipt = context.read_bytes()
    for _ in range(2):
        calls.unlink(missing_ok=True)
        result = subprocess.run(
            command + options, env=env, text=True, capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        events = calls.read_text(encoding="utf-8-sig").splitlines() if calls.exists() else []
        if state in {"fresh", "healthy"}:
            assert events.count("gc:128:5") == 1, f"{events}\n{result.stdout}\n{result.stderr}"
            assert events.index("status") < events.index("gc:128:5")
            predecessor = "slot-provision" if state == "fresh" else "slot-cutover"
            assert events.index(predecessor) < events.index("gc:128:5")
            if state == "fresh":
                assert result.returncode != 0
                assert "Package source not found" in result.stdout + result.stderr
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                assert "already selected in installation cell" in result.stdout
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert not any(event.startswith("gc:") for event in events), events
        assert context.read_bytes() == original_receipt
