"""Executable lookup and bounded Git reads for standalone configuration."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def path_command(name: str) -> str | None:
    names = [name]
    if os.name == "nt":
        names = [
            name + extension
            for extension in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
            if re.fullmatch(r"\.[a-zA-Z0-9]+", extension)
        ]
    for entry in os.get_exec_path():
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        # A directory-qualified candidate also bypasses implicit Windows cwd lookup.
        for candidate in names:
            command = shutil.which(str(directory / candidate))
            if command:
                return command
    return None


def run_git(git: str, start: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            # Only the explicitly selected -C root is eligible for bare inspection.
            [git, "-c", "safe.bareRepository=all", "-C", str(start), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
