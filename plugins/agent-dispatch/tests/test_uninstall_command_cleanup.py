"""Exercise real Windows uninstall file cleanup only inside disposable homes."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_installer_required_cutover import _run, ps_function


@pytest.mark.parametrize(
    ("dry_run", "purge"), [(True, False), (True, True), (False, False), (False, True)],
)
def test_windows_uninstall_owns_both_board_launchers(
    tmp_path: Path, dry_run: bool, purge: bool,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-dispatch"
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    commands = [
        local_bin / name for name in (
            "agent-dispatch", "agent-dispatch.ps1", "agent-dispatch.cmd",
            "agent-dispatch-board.ps1", "agent-dispatch-board.cmd",
        )
    ]
    data = runtime / "data/retained.db"
    data.parent.mkdir(parents=True)
    data.write_bytes(b"retained data")
    version = runtime / "versions/test/runtime"
    version.parent.mkdir(parents=True)
    version.write_bytes(b"runtime")
    sentinel = local_bin / "unrelated.ps1"
    sentinel.write_bytes(b"unrelated")
    for path in commands:
        path.write_bytes(b"owned command")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [*commands, data, version, sentinel]
    }

    def quote(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$env:USERPROFILE = {quote(home)}",
        f"$InstallDir = {quote(runtime)}",
        f"$LocalBin = {quote(local_bin)}",
        f"$LinkDir = {quote(runtime / '.venv')}",
        "$VenvDir = $LinkDir; $VersionedRuntime = $true",
        f"$DryRun = ${str(dry_run).lower()}; $Purge = ${str(purge).lower()}",
        "$TaskName = 'fixture-only'; $SupervisorTaskName = 'fixture-supervisor'",
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) }",
        "function Get-ItemProperty { param($Path,$Name,$ErrorAction) }",
        "function Get-NetFirewallRule { param($DisplayName,$ErrorAction) }",
        "function Invoke-SupervisorsStop {}",
        "function Retire-SupervisorProcesses { return 0 }",
        "function Remove-AllSupervisorTasks {}",
        "function Test-VenvIsLink { param($Path) return $false }",
        "function Write-Ok { param($Message) }",
        *[
            f"function {name} {{ throw 'unexpected system mutation: {name}' }}"
            for name in (
                "Stop-ScheduledTask", "Unregister-ScheduledTask",
                "Remove-CoordinatorAutostart", "Remove-NetFirewallRule",
            )
        ],
        ps_function("Invoke-Uninstall"),
        "Invoke-Uninstall",
    ])
    result = _run(tmp_path, "ps1", script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == before[sentinel]
    if dry_run:
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before
        for command in commands:
            assert f"would remove binstub: {command}" in result.stdout
    else:
        assert not any(command.exists() for command in commands)
        assert not version.exists()
        if purge:
            assert not runtime.exists()
        else:
            assert (data.read_bytes(), data.stat().st_mtime_ns) == before[data]
