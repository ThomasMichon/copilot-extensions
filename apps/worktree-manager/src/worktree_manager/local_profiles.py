"""Narrow Windows Terminal and shortcut reconciliation, without an installer."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
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


def _input_roots(value: object) -> harness_state.ProfileInputRoots:
    """Validate attribution, leaving project identity resolution with the caller."""
    from . import _installation_context as ic

    if (not isinstance(value, dict)
            or value.get("schema") != "copilot-extensions.terminal-profile-input-roots"
            or type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("Invalid terminal profile input-roots request")
    for key in ("context", "payloadRoot", "registryRoot"):
        if not isinstance(value.get(key), str) or not Path(value[key]).is_absolute():
            raise ValueError(f"Profile input {key} must be an absolute path")
    pointer = Path(value["context"])
    if len(pointer.parents) < 5:
        raise ValueError("Profile input context is outside the installation-cell layout")
    context = ic.resolve_context(
        context=pointer, plugin_id="agent-worktrees", payload_root=value["payloadRoot"],
        durable_home=pointer.parents[4], environment=os.environ,
    )
    registry = Path(value["registryRoot"])
    if registry.resolve() != Path(context["pluginRoot"]).resolve():
        raise ValueError("Profile registry does not belong to the selected installation")
    projects = value.get("projectRoots")
    if not isinstance(projects, dict):
        raise ValueError("Profile input projectRoots must be a mapping")
    resolved = {}
    repos_root = Path(context["cellRoot"]) / "repos"
    for name, raw in projects.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
                or name in {".", ".."} or not isinstance(raw, str)
                or not Path(raw).is_absolute()):
            raise ValueError("Invalid project root in profile inputs")
        root = Path(raw)
        relative = root.resolve().relative_to(repos_root.resolve())
        if (len(relative.parts) != 2 or relative.parts[1] != "agent-worktrees"
                or not re.fullmatch(r"repo-[0-9a-f]{24}", relative.parts[0])):
            raise ValueError("Profile project root is outside the selected repository state")
        resolved[name] = root
    return harness_state.ProfileInputRoots(registry, resolved)


def _validate_profile_inputs(
    project: str | None, input_roots: harness_state.ProfileInputRoots | None = None,
) -> None:
    """Writing must not turn an unreadable registry or selection into defaults."""
    home = harness_state.home()
    root_kwargs = {"input_roots": input_roots} if input_roots else {}
    registry = harness_state.projects_registry(home, strict=True, **root_kwargs)
    entries = registry.get("projects", {})
    if not isinstance(entries, dict) or any(
        value is not None and not isinstance(value, dict) for value in entries.values()
    ):
        raise ValueError("Projects registry must contain a projects mapping")
    harness_state.repos_registry(home, strict=True, **root_kwargs)
    names = set(entries) | ({project} if project is not None else set())
    for name in names:
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
                or name in {".", ".."}):
            raise ValueError("Invalid project name in profile inputs")
        directory = input_roots.project_dir(name) if input_roots else home / f".{name}"
        config = _read_profile_input(directory / "config.yaml")
        selection = config.get("terminal_profiles", [])
        try:
            terminal_profiles._coerce(selection, strict=True)
        except ValueError as error:
            raise ValueError(f"Invalid terminal_profiles for project {name}: {error}") from error


def _shortcut_rows(projects: list[terminal_fragment.ProjectInput]) -> list[dict]:
    rows = []
    for project in projects:
        if (not isinstance(project.display, str) or not project.display.strip()
                or any(c in project.display for c in '<>:"/\\|?*\r\n')):
            raise ValueError(f"Invalid shortcut display name for project {project.name}")
        rows.append({
            "display": project.display,
            "wsl": bool(project.wsl_distro and project.wsl_state),
        })
    return rows


def refresh_shortcuts(projects: list[terminal_fragment.ProjectInput]) -> int:
    """Keep the existing native/WSL shortcuts, writing only changed properties."""
    rows = _shortcut_rows(projects)
    home = harness_state.home()
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell is required to reconcile Windows shortcuts")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is required to resolve Windows Terminal")
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


def refresh(
    project: str | None, machine: str | None = None, *,
    input_roots: harness_state.ProfileInputRoots | None = None,
) -> dict:
    """Collect once and reconcile the Manager's local outputs."""
    if project is not None and (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", project) or project in {".", ".."}
    ):
        raise ValueError("Invalid project name")
    if platform.system() != "Windows":
        raise RuntimeError("Windows Terminal profile refresh requires Windows")
    _validate_profile_inputs(project, input_roots)
    directory = (input_roots.project_dir(project) if input_roots and project is not None
                 else harness_state.home() / f".{project}")
    selected_machine = machine or (_read_profile_input(
        directory / "config.yaml",
    ).get("machine") if project is not None else None)
    if not isinstance(selected_machine, str) or not selected_machine:
        raise ValueError("Cannot resolve machine key; pass --machine or repair project config")
    root_kwargs = {"input_roots": input_roots} if input_roots else {}
    projects = terminal_fragment.collect_local_projects(
        current_project=project, strict=True, **root_kwargs,
    )
    _shortcut_rows(projects)
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
    parser.add_argument("--input-roots-stdin", action="store_true")
    args = parser.parse_args(argv)
    try:
        roots = _input_roots(json.load(sys.stdin)) if args.input_roots_stdin else None
        result = (refresh(args.project, args.machine, input_roots=roots) if roots
                  else refresh(args.project, args.machine))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({
            "schema": SCHEMA, "version": 1, "status": "blocked", "error": str(error),
        }))
        return 1
    print(json.dumps(result))
    return 0
