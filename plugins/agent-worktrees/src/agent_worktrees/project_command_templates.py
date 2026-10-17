"""Exact project-launcher families, including the pre-receipt generators."""

from __future__ import annotations

import shlex
from pathlib import Path


def render(
    project: str, payload: Path, *, windows: bool, family: str = "current",
    user_bin: Path | None = None,
) -> dict[str, str]:
    """Render current bytes or a historical family from #1901/#1909."""
    historical = family in ("traced", "ambient")
    ambient = family == "ambient"
    installed = family == "current" and user_bin is not None
    command_dir = user_bin if installed else payload / "bin" / "payload"
    comment = (
        "This project entry point delegates to the installed user-side command."
        if installed else "This attributable project entry point is pinned to its owning payload."
    )
    p = project.replace("'", "''")
    if windows:
        cmd_path = str(command_dir / "agent-worktrees.cmd").replace("%", "%%")
        ps1_path = str(command_dir / "agent-worktrees.ps1").replace("'", "''")
        cmd = ["@echo off"]
        ps1 = []
        if not ambient:
            cmd.append("rem agent-worktrees project binstub")
            ps1.append("# agent-worktrees project binstub")
        cmd += ['set "PYTHONUTF8=1"', f'set "AGENT_WORKTREES_LAUNCH_ID={project}-%RANDOM%-%RANDOM%"']
        ps1 += ["$env:PYTHONUTF8 = '1'",
                f"$env:AGENT_WORKTREES_LAUNCH_ID = '{p}-' + [guid]::NewGuid().ToString('N')"]
        if historical:
            cmd += [
                'set "AGENT_WORKTREES_BINSTUB_STARTED=%DATE% %TIME%"',
                'set "AGENT_WORKTREES_LAUNCH_TRACE=%USERPROFILE%\\.agent-worktrees\\logs\\picker-launches.jsonl"',  # marketplace-isolation: allow historical-template-comparison
                'if not exist "%USERPROFILE%\\.agent-worktrees\\logs" mkdir "%USERPROFILE%\\.agent-worktrees\\logs" >nul 2>&1',  # marketplace-isolation: allow historical-template-comparison
                f'(>>"%AGENT_WORKTREES_LAUNCH_TRACE%" echo {{"event":"binstub_start","timestamp":"%AGENT_WORKTREES_BINSTUB_STARTED%","launch_id":"%AGENT_WORKTREES_LAUNCH_ID%","project":"{project}"}}) 2>nul',
                'if "%~1"=="" (',
            ]
            if ambient:
                cmd += [f'  set "WORKTREE_PROJECT={project}"',
                        '  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd"']  # marketplace-isolation: allow historical-template-comparison
            else:
                cmd.append(f'  call "%USERPROFILE%\\.agent-worktrees\\bin\\launch-session.cmd" --project {project}')  # marketplace-isolation: allow historical-template-comparison
            cmd += ["  exit /b %ERRORLEVEL%", ")"]
            ps1 += [
                "$env:AGENT_WORKTREES_BINSTUB_STARTED = [DateTime]::UtcNow.ToString('o')",
                "$_awTraceDir = Join-Path $env:USERPROFILE '.agent-worktrees\\logs'",  # marketplace-isolation: allow historical-template-comparison
                "$env:AGENT_WORKTREES_LAUNCH_TRACE = Join-Path $_awTraceDir 'picker-launches.jsonl'",
                "try {",
                "    [IO.Directory]::CreateDirectory($_awTraceDir) | Out-Null",
                f"    $_awEvent = [ordered]@{{ event = 'binstub_start'; timestamp = $env:AGENT_WORKTREES_BINSTUB_STARTED; launch_id = $env:AGENT_WORKTREES_LAUNCH_ID; project = '{p}' }}",
                "    [IO.File]::AppendAllText($env:AGENT_WORKTREES_LAUNCH_TRACE, ($_awEvent | ConvertTo-Json -Compress) + [Environment]::NewLine)",
                "} catch {}",
                "if ($args.Count -eq 0) {",
            ]
            if ambient:
                ps1 += [
                    "    $_savedProj = $env:WORKTREE_PROJECT",
                    f"    $env:WORKTREE_PROJECT = '{p}'",
                    "    try {",
                    '        & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1"',  # marketplace-isolation: allow historical-template-comparison
                    "        $_rc = $LASTEXITCODE",
                    "    } finally {",
                    "        if ($null -eq $_savedProj) { Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue } else { $env:WORKTREE_PROJECT = $_savedProj }",
                    "    }",
                    "    exit $_rc",
                ]
            else:
                ps1 += [f'    & "$env:USERPROFILE\\.agent-worktrees\\bin\\launch-session.ps1" --project \'{p}\'',  # marketplace-isolation: allow historical-template-comparison
                        "    exit $LASTEXITCODE"]
            ps1.append("}")
        cmd += [f"rem {comment}",
                f'"{cmd_path}" --project {project} %*', "exit /b %ERRORLEVEL%"]
        ps1 += [f"# {comment}",
                f"& '{ps1_path}' --project '{p}' @args", "exit $LASTEXITCODE"]
        return {f"{project}.ps1": "\r\n".join(ps1), f"{project}.cmd": "\r\n".join(cmd)}
    lines = ["#!/usr/bin/env bash"]
    if not ambient:
        lines.append("# agent-worktrees project binstub")
    lines += ["export PYTHONUTF8=1",
              f"export AGENT_WORKTREES_LAUNCH_ID={shlex.quote(project)}-$$-$RANDOM-$(date +%s)"]
    if historical:
        lines += [
            'export AGENT_WORKTREES_BINSTUB_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"',
            'export AGENT_WORKTREES_LAUNCH_TRACE="$HOME/.agent-worktrees/logs/picker-launches.jsonl"',  # marketplace-isolation: allow historical-template-comparison
            'mkdir -p "$(dirname "$AGENT_WORKTREES_LAUNCH_TRACE")" 2>/dev/null || true',
            f"""printf '%s\\n' '{{"event":"binstub_start","timestamp":"'"$AGENT_WORKTREES_BINSTUB_STARTED"'","launch_id":"'"$AGENT_WORKTREES_LAUNCH_ID"'","project":"{project}"}}' >>"$AGENT_WORKTREES_LAUNCH_TRACE" 2>/dev/null || true""",
            "if [[ $# -eq 0 ]]; then",
        ]
        if ambient:
            lines += [f"  export WORKTREE_PROJECT={shlex.quote(project)}",
                      '  exec "$HOME/.agent-worktrees/bin/launch-session.sh"']  # marketplace-isolation: allow historical-template-comparison
        else:
            lines.append(f'  exec "$HOME/.agent-worktrees/bin/launch-session.sh" --project {shlex.quote(project)}')  # marketplace-isolation: allow historical-template-comparison
        lines.append("fi")
    lines += [f"# {comment}",
              f'exec {shlex.quote(str(command_dir / "agent-worktrees"))} --project {shlex.quote(project)} "$@"']
    return {project: "\n".join(lines) + "\n"}


def matches(data: bytes, content: str) -> bool:
    """Permit the generator's historical LF/CRLF variants, nothing else."""
    return data in (content.encode("utf-8"), content.replace("\r\n", "\n").encode("utf-8"))
