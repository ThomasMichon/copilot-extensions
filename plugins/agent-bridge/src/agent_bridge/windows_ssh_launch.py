"""Windows OpenSSH launch helpers: encoded PowerShell and staged launch scripts."""

from __future__ import annotations

import base64
import uuid
from typing import Any

from .spawn_target import SpawnTarget

_WINDOWS_SSH_SHELLS = frozenset({"pwsh", "powershell", "cmd"})

# Windows OpenSSH hands the remote command to its DefaultShell -- ``cmd.exe``
# unless reconfigured -- whose command-line limit is 8191 characters. A longer
# launch (a full repository plugin stack adds one ``--plugin-dir`` pair per
# plugin) is staged to a remote file instead of being sent inline.
_WINDOWS_INLINE_COMMAND_LIMIT = 8000


def _is_windows_ssh_shell(target: SpawnTarget) -> bool:
    return (target.ssh_shell or "bash").lower() in _WINDOWS_SSH_SHELLS


def _encoded_powershell(shell: str, script: str) -> str:
    exe = "pwsh" if shell == "pwsh" else "powershell"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"{exe} -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"


async def _stage_windows_launch(manager: Any, target: SpawnTarget, script: str) -> str:
    """Write a long Windows launch script to the receiver; return a short launcher.

    The script travels over a separate SSH exec channel's stdin, so its length
    is not bounded by any command line. The launcher reads it, deletes the
    file before running it, and executes it in-process so the ACP stdio
    channel stays attached to the launched agent.
    """
    shell = (target.ssh_shell or "bash").lower()
    name = f"agent-bridge-launch-{uuid.uuid4().hex}.ps1"
    path = f"$path = Join-Path ([IO.Path]::GetTempPath()) '{name}'; "
    # A raw stdin stream never reaches EOF through Windows OpenSSH, so read the
    # text reader to its end; base64 keeps the payload independent of the
    # receiver's console input code page.
    writer = (
        "$ErrorActionPreference = 'Stop'; " + path
        + "$payload = [Console]::In.ReadToEnd(); "
        "[IO.File]::WriteAllBytes($path, [Convert]::FromBase64String($payload.Trim()))"
    )
    result = await manager.exec_command(
        target.host, _encoded_powershell(shell, writer),
        input_bytes=base64.b64encode(script.encode("utf-8")), timeout=60,
    )
    if result.timed_out or result.exit_code:
        detail = "timed out" if result.timed_out else f"exit {result.exit_code}"
        raise RuntimeError(
            f"Staging the Windows launch script on {target.host} failed "
            f"({detail}): {(result.stderr or '').strip()}",
        )
    launcher = (
        path
        + "$text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8); "
        "Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue; "
        "& ([ScriptBlock]::Create($text)); exit $LASTEXITCODE"
    )
    return _encoded_powershell(shell, launcher)
