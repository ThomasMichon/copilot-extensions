"""Bind executable lookup to the execution directory without changing process cwd."""

from __future__ import annotations

import errno
import os
from pathlib import Path

BATCH_EXECUTABLE_ENV = "COPILOT_VENUE_BATCH_EXECUTABLE"


def windows_batch_command(arguments: list[str], environment: dict[str, str]) -> list[str]:
    """Encode a batch path as data, expanded once by the existing cmd transport."""
    executable = arguments[0]
    if any(character in executable for character in ('"', "\r", "\n", "\0")):
        raise ValueError("Batch executable path contains invalid characters")
    environment[BATCH_EXECUTABLE_ENV] = '"' + executable + '"'
    return [
        environment.get("COMSPEC", os.environ.get("COMSPEC", "cmd.exe")),
        "/d", "/v:off", "/s", "/c", "%" + BATCH_EXECUTABLE_ENV + "%", *arguments[1:],
    ]


def execution_environment(environment: dict[str, str], cwd: str | Path) -> dict[str, str]:
    base = Path(cwd).resolve()
    entries = environment.get("PATH", os.defpath).split(os.pathsep)
    if os.name == "nt":
        entries = [
            entry[1:-1] if entry.startswith('"') and entry.endswith('"') else entry
            for entry in entries
        ]
    return {
        **environment,
        "PATH": os.pathsep.join(
            str(base / entry) for entry in entries
        ),
    }


def execution_executable(
    executable: str, environment: dict[str, str], cwd: str | Path, *, required: bool = True,
) -> str:
    base = Path(cwd).resolve()
    environment = execution_environment(environment, base)
    names = [executable]
    if os.name == "nt" and not os.path.splitext(executable)[1]:
        extensions = [
            extension.rstrip(".")
            for extension in (environment.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
            if extension.rstrip(".")
        ]
        if not extensions:
            raise ValueError("Execution environment PATHEXT has no executable extensions")
        names = [executable + extension for extension in extensions]
    if os.path.dirname(executable):
        directories = [str(base)]
    else:
        directories = environment["PATH"].split(os.pathsep)
        if os.name == "nt" and not any(
            name.upper() == "NODEFAULTCURRENTDIRECTORYINEXEPATH" for name in environment
        ):
            directories.insert(0, str(base))
    candidates = [str(Path(directory) / name) for directory in directories for name in names]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.F_OK | os.X_OK):
            return candidate
    if required:
        raise FileNotFoundError(errno.ENOENT, "Executable is unavailable in the execution checkout", executable)
    # An optional installer is unused for local-only preparation. Keep its
    # missing command bound here rather than re-searching the installer's cwd.
    return candidates[0]
