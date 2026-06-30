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


def test_launcher_chdirs_supervisor_to_runtime_home():
    """The generated launcher script must Set-Location off the plugin dir
    before spawning the worker, so the supervisor pwsh never pins the payload
    folder."""
    text = _text()
    assert "Set-Location -LiteralPath (Split-Path `$pidFile)" in text, (
        "launcher must chdir the supervisor to the runtime home (#1376)"
    )
    # And it must do so before launching the worker (Start-Process).
    chdir_at = text.index("Set-Location -LiteralPath (Split-Path `$pidFile)")
    spawn_at = text.index("Start-Process -FilePath `$launchPy")
    assert chdir_at < spawn_at, "supervisor must chdir before spawning the worker"


def test_scheduled_task_action_sets_working_directory():
    """The task action must pin the runtime home as its working directory, so a
    task-launched supervisor also starts off the plugin dir."""
    text = _text()
    assert "-WorkingDirectory $InstallDir" in text, (
        "New-ScheduledTaskAction must set -WorkingDirectory $InstallDir (#1376)"
    )
