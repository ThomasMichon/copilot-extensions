"""Repair the engine from fresh bootstrap inputs before Manager orchestration."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from agent_procutil import no_window_kwargs


def repair_engine(source: Path) -> int:
    """Run the supported, ownership-admitted installer, never a broken engine."""
    from .core_install import install_script
    script = install_script(source)
    if script is None:
        print("Fresh bootstrap engine installer is unavailable.", file=sys.stderr)
        return 1
    if os.name == "nt":
        from shutil import which

        host = which("pwsh") or which("powershell.exe")
        if host is None:
            print("PowerShell is required for engine repair.", file=sys.stderr)
            return 1
        command = [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "update"]
    else:
        command = ["bash", str(script), "update"]
    # The fresh Manager's base Python satisfies the installer's bootstrap
    # admission without consulting an installed Worktrees virtual environment.
    environment = dict(os.environ)
    base = Path(getattr(sys, "_base_executable", sys.executable)).parent
    environment["PATH"] = str(base) + os.pathsep + environment.get("PATH", "")
    return subprocess.run(command, env=environment, **no_window_kwargs()).returncode
