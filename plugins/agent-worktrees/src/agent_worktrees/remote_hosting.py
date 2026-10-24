"""Intrinsic receiving-owner admission for remote Copilot launch commands."""

from __future__ import annotations

import base64
import shlex


_VALIDATE = """
import json,sys
from pathlib import PurePosixPath,PureWindowsPath
p=json.load(sys.stdin)
absolute=lambda v:isinstance(v,str) and bool(v.strip()) and (
 PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute())
if not (isinstance(p,dict) and type(p.get('version')) is int and p['version']==1
 and p.get('allowed') is True and absolute(p.get('repository'))
 and 'expose_agent' in p and (p['expose_agent'] is None or p['expose_agent'] is True)
 and isinstance(p.get('sources'),list) and all(absolute(v) for v in p['sources'])
 and p.get('denied_by')==[] and 'error' not in p and 'code' not in p):
 raise SystemExit('Remote owner returned invalid repository hosting admission')
""".strip()


def launches_copilot(args: list[str]) -> bool:
    if any(value in ("--help", "-h", "--version") for value in args):
        return False
    if any(value.split("=", 1)[0] == "--launch-request-status" for value in args):
        return False
    if args and args[0] == "handoff-cutover" and any(
        value.split("=", 1)[0] == "--retire-pane" for value in args
    ):
        return False
    return (
        not args or args[0].startswith("-")
        or args[0] in ("copilot", "embody", "resolve", "handoff-cutover")
        or (args[0] == "create" and any(
            value.split("=", 1)[0] == "--seed" for value in args
        ))
    )


def _selector(args: list[str]) -> list[str]:
    for index, value in enumerate(args):
        if value == "--worktree-id":
            if index + 1 >= len(args) or not args[index + 1]:
                raise ValueError("Remote hosting admission requires a worktree identity")
            return ["--worktree-id", args[index + 1]]
        if value.startswith("--worktree-id="):
            if not value.split("=", 1)[1]:
                raise ValueError("Remote hosting admission requires a worktree identity")
            return ["--worktree-id", value.split("=", 1)[1]]
    return ["--project-root"]


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def policy_arguments(project: str, selectors: list[str]) -> list[str]:
    return [project, "agent-policy", "check", *_selector(selectors), "--json"]


def shell_command(
    shell: str, project: str, args: list[str], *,
    selectors: list[str] | None = None,
) -> str:
    from .remote_seed_launch import shell_command as plain_command

    if not launches_copilot(args):
        return plain_command(shell, [project, *args])
    check = policy_arguments(project, args if selectors is None else selectors)
    if shell.lower() in ("pwsh", "powershell", "pwsh.exe", "powershell.exe"):
        probe = "& " + " ".join(_quote_powershell(value) for value in check)
        launch = "& " + " ".join(_quote_powershell(value) for value in [project, *args])
        script = (
            "$ErrorActionPreference='Stop'; "
            f"$raw={probe}; if($LASTEXITCODE -ne 0)"
            "{$status=$LASTEXITCODE; [Console]::Error.WriteLine("
            "($raw -join [Environment]::NewLine)); exit $status}; "
            "$p=$raw|ConvertFrom-Json; "
            "$absolute={param($v) $v -is [string] -and "
            "-not [string]::IsNullOrWhiteSpace($v) -and [IO.Path]::IsPathRooted($v)}; "
            "if(-not (($p.version -is [int] -or $p.version -is [long]) "
            "-and $p.version -eq 1 -and $p.allowed -is [bool] -and $p.allowed "
            "-and (& $absolute $p.repository) "
            "-and $p.PSObject.Properties.Name -contains 'expose_agent' "
            "-and ($null -eq $p.expose_agent -or "
            "($p.expose_agent -is [bool] -and $p.expose_agent)) "
            "-and $p.sources -is [array] "
            "-and @($p.sources|Where-Object {-not (& $absolute $_)}).Count -eq 0 "
            "-and $p.denied_by -is [array] -and $p.denied_by.Count -eq 0 "
            "-and $p.PSObject.Properties.Name -notcontains 'error' "
            "-and $p.PSObject.Properties.Name -notcontains 'code'))"
            "{throw 'Remote owner returned invalid repository hosting admission'}; "
            f"{launch}; exit $LASTEXITCODE"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return f"{shell} -NoProfile -EncodedCommand {encoded}"
    from machine_transport import wrap_remote_command

    if shell.lower() not in ("bash", "sh", "zsh"):
        raise ValueError("Remote launch requires a supported explicit shell")
    probe = " ".join(shlex.quote(value) for value in check)
    launch = " ".join(shlex.quote(value) for value in [project, *args])
    script = (
        f"HOSTING_RESULT=$({probe}) || "
        "{ HOSTING_STATUS=$?; printf '%s\\n' \"$HOSTING_RESULT\" >&2; exit \"$HOSTING_STATUS\"; }; "
        "HOSTING_PYTHON=$(command -v python3 || command -v python) || "
        "{ printf '%s\\n' 'Remote hosting validation requires Python on PATH' >&2; exit 3; }; "
        f"printf '%s\\n' \"$HOSTING_RESULT\" | \"$HOSTING_PYTHON\" -I -S -B -c {shlex.quote(_VALIDATE)} "
        f"&& {launch}"
    )
    return wrap_remote_command(shell, script)
