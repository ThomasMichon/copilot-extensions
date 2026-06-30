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
