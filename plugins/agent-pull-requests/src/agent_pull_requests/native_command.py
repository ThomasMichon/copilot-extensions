"""Bounded invocation of the caller's attributed Worktrees command."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned


class NativeCommandUnavailable(ValueError):
    """A native read failed only after exact retained cleanup settled."""


def run_command(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    input_text = None
    if os.name == "nt" and Path(argv[0]).suffix.lower() == ".ps1":
        interpreter = shutil.which("pwsh")
        if not interpreter:
            raise ValueError(
                "the attributed Worktrees PowerShell command requires PowerShell 7.3 or newer"
            )
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "if ($PSVersionTable.PSVersion -lt [version]'7.3') { "
            "[Console]::Error.WriteLine('native PR commands require PowerShell 7.3 or newer'); "
            "exit 126 }; "
            "$PSNativeCommandArgumentPassing = 'Standard'; "
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
        raise ValueError(
            "native PR commands do not support Worktrees batch shims; "
            "use its attributed PowerShell command instead"
        )
    return run_caller_owned(
        argv, timeout=timeout, input_text=input_text,
        max_stdout_bytes=1_048_576, max_stderr_bytes=65_536,
    )


def run_observation_command(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Keep read-owner thread and writer custody until exact native cleanup joins."""
    try:
        return run_command(argv, timeout=timeout)
    except OwnedCleanupError as error:
        logging.getLogger(__name__).warning("native read cleanup delayed; original custody retained")
        while True:
            try:
                error.retry_cleanup(deadline=time.monotonic() + 2)
                break
            except Exception:
                time.sleep(1)
        raise NativeCommandUnavailable("native read cleanup was delayed") from None
