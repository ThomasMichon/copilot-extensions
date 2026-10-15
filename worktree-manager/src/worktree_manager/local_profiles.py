"""Narrow Windows Terminal and shortcut reconciliation, without an installer."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import harness_state, terminal_fragment, terminal_profiles

SCHEMA = "copilot-extensions.terminal-profile-refresh"

_SHORTCUTS = r"""
$ErrorActionPreference = 'Stop'
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
$changed = 0
try {
    foreach ($project in $data.projects) {
        $display = $project.display
        $wanted = @(@{ name = $display; suffix = '' })
        if ($project.wsl) {
            $wanted += @{ name = "$display (WSL)"; suffix = ' (WSL)' }
        }
        foreach ($item in $wanted) {
            $path = Join-Path $data.bin ($item.name + '.lnk')
            $link = $shell.CreateShortcut($path)
            $arguments = '-p "' + $item.name + '"'
            $description = "$display - Worktree Session Manager" + $item.suffix
            if ($link.TargetPath -ne $data.terminal -or
                $link.Arguments -ne $arguments -or
                $link.WorkingDirectory -ne $data.home -or
                $link.Description -ne $description) {
                $link.TargetPath = $data.terminal
                $link.Arguments = $arguments
                $link.WorkingDirectory = $data.home
                $link.Description = $description
                $link.Save()
                $changed++
            }
        }
        foreach ($file in Get-ChildItem -LiteralPath $data.bin -File) {
            $oldLabel = $file.Name
            if ($file.Length -eq 0 -and -not $file.Extension -and
                $oldLabel -eq "$display (WSL") {
                Remove-Item -LiteralPath $file.FullName
                $changed++
                continue
            }
            $oldWsl = $oldLabel -eq "$display (WSL).lnk" -or
                ($oldLabel.StartsWith("$display (WSL: ") -and $oldLabel.EndsWith(').lnk'))
            if (-not $oldWsl -or
                ($project.wsl -and $oldLabel -eq "$display (WSL).lnk")) { continue }
            $link = $shell.CreateShortcut($file.FullName)
            if ($link.TargetPath -eq $data.terminal -and
                $link.Description -eq "$display - Worktree Session Manager (WSL)") {
                Remove-Item -LiteralPath $file.FullName
                $changed++
            } else {
                throw "Cannot retire an unattributed WSL shortcut for $display"
            }
        }
    }
} finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
@{ changed = $changed } | ConvertTo-Json -Compress
"""


def _read_profile_input(path: Path) -> dict:
    return harness_state._read_yaml(path, strict=True)


def _validate_profile_inputs(project: str | None) -> None:
    """Writing must not turn an unreadable registry or selection into defaults."""
    home = harness_state.home()
    registry = _read_profile_input(home / ".agent-worktrees" / "projects.yaml")
    entries = registry.get("projects", {})
    if not isinstance(entries, dict) or any(
        value is not None and not isinstance(value, dict) for value in entries.values()
    ):
        raise ValueError("Projects registry must contain a projects mapping")
    repos = _read_profile_input(home / ".agent-worktrees" / "repos.yaml")
    if not isinstance(repos.get("repos", {}), dict):
        raise ValueError("Repos registry must contain a repos mapping")
    names = set(entries) | ({project} if project is not None else set())
    for name in names:
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
                or name in {".", ".."}):
            raise ValueError("Invalid project name in profile inputs")
        config = _read_profile_input(home / f".{name}" / "config.yaml")
        selection = config.get("terminal_profiles", [])
        try:
            terminal_profiles._coerce(selection, strict=True)
        except ValueError as error:
            raise ValueError(f"Invalid terminal_profiles for project {name}: {error}") from error


def refresh_shortcuts(projects: list[terminal_fragment.ProjectInput]) -> int:
    """Keep the existing native/WSL shortcuts, writing only changed properties."""
    home = harness_state.home()
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell is required to reconcile Windows shortcuts")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is required to resolve Windows Terminal")
    rows = []
    for project in projects:
        if not project.display or any(c in project.display for c in '<>:"/\\|?*\r\n'):
            raise ValueError(f"Invalid shortcut display name for project {project.name}")
        rows.append({
            "display": project.display,
            "wsl": bool(project.wsl_distro and project.wsl_state),
        })
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _SHORTCUTS],
        input=json.dumps({
            "projects": rows,
            "bin": str(local_bin),
            "home": str(home),
            "terminal": str(Path(local_app_data) / "Microsoft" / "WindowsApps" / "wt.exe"),
        }),
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        **no_window_kwargs(),
    )
    if result.returncode:
        raise RuntimeError(f"Windows shortcut reconciliation failed: {result.stderr.strip()}")
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or type(response.get("changed")) is not int:
        raise ValueError("Invalid Windows shortcut reconciliation response")
    return response["changed"]


def refresh(project: str | None, machine: str | None = None) -> dict:
    """Collect once and reconcile the Manager's local outputs."""
    if project is not None and (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", project) or project in {".", ".."}
    ):
        raise ValueError("Invalid project name")
    if platform.system() != "Windows":
        raise RuntimeError("Windows Terminal profile refresh requires Windows")
    _validate_profile_inputs(project)
    selected_machine = machine or (_read_profile_input(
        harness_state.home() / f".{project}" / "config.yaml",
    ).get("machine") if project is not None else None)
    if not isinstance(selected_machine, str) or not selected_machine:
        raise ValueError("Cannot resolve machine key; pass --machine or repair project config")
    projects = terminal_fragment.collect_local_projects(current_project=project, strict=True)
    plan = terminal_fragment.deploy_fragment(
        selected_machine, current_project=project, apply=True, projects=projects,
    )
    if not plan.applied:
        raise RuntimeError("Windows Terminal fragment destination is unavailable")
    changed_shortcuts = refresh_shortcuts(projects)
    return {
        "schema": SCHEMA, "version": 1, "status": "verified_current",
        "fragment": str(plan.fragment_path),
        "profiles": len(plan.new_guids), "shortcuts_changed": changed_shortcuts,
        "notes": plan.notes,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="worktree-manager refresh-profiles")
    parser.add_argument("project", nargs="?")
    parser.add_argument("--machine")
    args = parser.parse_args(argv)
    try:
        result = refresh(args.project, args.machine)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({
            "schema": SCHEMA, "version": 1, "status": "blocked", "error": str(error),
        }))
        return 1
    print(json.dumps(result))
    return 0
