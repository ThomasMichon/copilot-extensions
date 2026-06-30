"""Regression guard: the Windows installer must not let the agent-bridge
supervisor pin its plugin install folder as CWD (#1376).

On Windows a process holding a directory as its current directory locks it, so
a supervisor (the launcher pwsh) sitting in
``~/.copilot/installed-plugins/copilot-extensions/agent-bridge`` blocks
``copilot plugin update agent-bridge`` -- the payload replace fails and the
folder is left emptied ("installer not found" on the next update). These are
file-shape assertions over ``scripts/install.ps1`` so a future edit that drops
either guard trips a test.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _text() -> str:
    return _INSTALL_PS1.read_text(encoding="utf-8")


def test_install_ps1_exists():
    assert _INSTALL_PS1.is_file(), "Windows installer must ship"


def test_launcher_spawns_worker_in_runtime_home():
    """The generated launcher must spawn the worker python with its OS working
    directory set to the runtime home -- via ``-WorkingDirectory`` on
    Start-Process, the only thing that actually moves a spawned child's cwd.

    ``Set-Location`` alone is NOT sufficient: it moves PowerShell's ``$PWD``
    provider path, not the OS working directory a child inherits, so the
    supervisor python would still pin the plugin dir (#1376).
    """
    text = _text()
    # The operative guard: Start-Process must pin -WorkingDirectory.
    assert "-WorkingDirectory `$runtimeHome" in text, (
        "launcher Start-Process must set -WorkingDirectory `$runtimeHome (#1376)"
    )
    # ...and it must precede the actual spawn arguments of that call.
    workdir_at = text.index("-WorkingDirectory `$runtimeHome")
    spawn_at = text.index("Start-Process -FilePath `$launchPy")
    assert spawn_at < workdir_at, "WorkingDirectory must belong to the Start-Process call"


def test_scheduled_task_action_sets_working_directory():
    """The task action must pin the runtime home as its working directory, so a
    task-launched supervisor (conhost) also starts off the plugin dir."""
    text = _text()
    assert "-WorkingDirectory $InstallDir" in text, (
        "New-ScheduledTaskAction must set -WorkingDirectory $InstallDir (#1376)"
    )


def test_invoke_start_pins_working_directory():
    """Invoke-Start's non-headless path (the at-logon mode) must launch BOTH the
    inner python AND its hosting conhost with a working directory off the plugin
    dir. -NoNewWindow keeps that conhost alive hosting the long-lived daemon, so
    without an explicit working dir it holds the installed-plugins payload
    folder open and a later ``copilot plugin update`` empties it (#1376).
    """
    text = _text()
    # The inner python Start-Process carries an explicit -WorkingDirectory.
    assert "-ArgumentList '-m','agent_bridge','start' -WorkingDirectory '" in text, (
        "Invoke-Start inner python Start-Process must pin -WorkingDirectory (#1376)"
    )
    # Both the scheduled-task action AND the Invoke-Start conhost pin
    # -WorkingDirectory $InstallDir (so this token appears at least twice).
    assert text.count("-WorkingDirectory $InstallDir") >= 2, (
        "Invoke-Start conhost must also pin -WorkingDirectory $InstallDir (#1376)"
    )
