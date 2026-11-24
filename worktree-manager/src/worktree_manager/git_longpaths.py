"""Enable Git long paths for the user on Windows during suite installation.

Git for Windows refuses checkout paths of 260 characters or more unless
``core.longpaths`` is true; the OS ``LongPathsEnabled`` value does not change
that. Managed worktrees add a folder prefix to every repository path, so the
installer sets the user-global value when it is unset. An explicit value,
including ``false``, is never changed; ``false`` is reported instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence

KEY = "core.longpaths"
Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    # Runs in the bootstrap interpreter, before the runtime's shared libraries exist.
    return subprocess.run(
        list(arguments), capture_output=True, text=True, timeout=30, check=False,
        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ensure_global(
    *, windows: bool | None = None, git: str | None = None, run: Runner | None = None,
    report: Callable[[str], None] | None = None,
) -> str:
    """Set ``git config --global core.longpaths true`` on Windows when unset.

    Returns ``not-windows``, ``no-git``, ``set``, ``present`` (already true),
    ``explicit-false`` (preserved and reported) or ``failed``. Never raises.
    """
    if not (os.name == "nt" if windows is None else windows):
        return "not-windows"
    executable = git or shutil.which("git")
    if not executable:
        return "no-git"
    run = run or _run
    report = report or (lambda message: print(message, file=sys.stderr))
    try:
        current = run([executable, "config", "--global", "--get", KEY])
        value = current.stdout.strip().lower() if current.returncode == 0 else ""
        if current.returncode == 0 and value:
            if value in ("false", "no", "off", "0"):
                report(
                    f"Git {KEY} is explicitly '{current.stdout.strip()}' in the global configuration; "
                    "left unchanged. Long repository paths in managed worktrees can fail with "
                    "'Filename too long'."
                )
                return "explicit-false"
            return "present"
        if current.returncode != 1:
            return "failed"
        stored = run([executable, "config", "--global", KEY, "true"])
    except (OSError, subprocess.SubprocessError):
        return "failed"
    if stored.returncode != 0:
        report(f"Could not set global Git {KEY}=true: {stored.stderr.strip()}")
        return "failed"
    report(f"Enabled global Git {KEY}=true for long Windows paths.")
    return "set"
