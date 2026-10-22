"""Bounded invocation of the caller's attributed Worktrees command."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

from agent_procutil.owned_process import run_caller_owned


def run_command(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    input_text = None
    if os.name == "nt" and Path(argv[0]).suffix.lower() == ".ps1":
        interpreter = shutil.which("pwsh") or shutil.which("powershell")
        if not interpreter:
            raise RuntimeError("PowerShell is required for the attributed Worktrees command")
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::InputEncoding = New-Object Text.UTF8Encoding($false); "
            "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); "
            "$v = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
            "$nativeArguments = @($v.arguments); "
            "& $v.command @nativeArguments; exit $LASTEXITCODE"
        )
        input_text = json.dumps({"command": argv[0], "arguments": argv[1:]})
        argv = [
            interpreter, "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ]
    elif os.name == "nt" and Path(argv[0]).suffix.lower() in (".cmd", ".bat"):
        if any(any(c in argument for c in '"\r\n&|<>^%!') for argument in argv[1:]):
            raise ValueError(
                "this Worktrees batch command cannot safely carry these arguments; "
                "use its attributed PowerShell command instead"
            )
    return run_caller_owned(
        argv, timeout=timeout, input_text=input_text,
        max_stdout_bytes=1_048_576, max_stderr_bytes=65_536,
    )
