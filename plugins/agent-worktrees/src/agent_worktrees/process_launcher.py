"""Bind a venue's process-only native executable without manager secrets."""

from __future__ import annotations

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
    normalized = next(
        (index for index, arg in enumerate(result)
         if Path(arg) in {scripts / "default-setup.sh", scripts / "default-setup.ps1"}),
        None,
    )
    if normalized is None:
        raise error_type(
            "A process-only Copilot launcher requires a direct copilot "
            "launch template or normalized setup"
        )
    if "--copilot-path" in result or "-CopilotPath" in result:
        raise error_type(
            "A process-only Copilot launcher cannot replace a configured copilot_path"
        )
    flag = "-CopilotPath" if result[normalized].endswith(".ps1") else "--copilot-path"
    return [*result[:normalized + 1], flag, launcher, *result[normalized + 1:]]
