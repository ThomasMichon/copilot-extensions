"""Bind a venue's process-only native executable without manager secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import installer


def apply_process_launcher(command, args, error_type):
    launcher = getattr(args, "copilot_process_launcher", None)
    if not launcher:
        return command
    if not os.path.isabs(launcher) or not os.path.isfile(launcher) or not os.access(launcher, os.X_OK):
        raise error_type("The requested Copilot process launcher is not executable")
    result = list(command)
    if result and result[0] == "copilot":
        result[0] = launcher
        return result
    scripts = installer.install_dir() / "scripts"
    normalized = None
    if len(result) > 1 and result[0] == "bash" and Path(result[1]) == scripts / "default-setup.sh":
        normalized = 1
    elif result and result[0] in {"pwsh", "pwsh.exe", "powershell.exe"} and "-File" in result:
        index = result.index("-File")
        if (
            index + 1 < len(result)
            and all(arg in {"-NoProfile", "-NoLogo"} for arg in result[1:index])
            and Path(result[index + 1]) == scripts / "default-setup.ps1"
        ):
            normalized = index + 1
    if normalized is None:
        raise error_type(
            "A process-only Copilot launcher requires a direct copilot "
            "launch template or normalized setup"
        )
    if any(
        arg == "--copilot-path" or arg.startswith("--copilot-path=")
        or arg.lower() == "-copilotpath" or arg.lower().startswith("-copilotpath:")
        for arg in result
    ):
        raise error_type(
            "A process-only Copilot launcher cannot replace a configured copilot_path"
        )
    flag = "-CopilotPath" if result[normalized].endswith(".ps1") else "--copilot-path"
    return [*result[:normalized + 1], flag, launcher, *result[normalized + 1:]]


def write_launch_result(args, result) -> None:
    target = getattr(args, "launch_result_file", None)
    if not target:
        return
    launcher = getattr(args, "copilot_process_launcher", None)
    path = Path(target)
    if (
        not launcher or not path.is_absolute() or path.name != "result.json"
        or path.parent != Path(launcher).parent
        or type(result.get("created")) is not bool
        or type(result.get("resumed")) is not bool
        or result["created"] == result["resumed"]
    ):
        raise ValueError("invalid process-launch result destination or create-or-resume receipt")
    descriptor = {"version": 1, "created": result["created"], "resumed": result["resumed"]}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(descriptor, stream)
        stream.flush()
        os.fsync(stream.fileno())
