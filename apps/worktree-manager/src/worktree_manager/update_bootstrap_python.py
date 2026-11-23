"""Prepare declared helper Python only inside explicit engine installation."""
from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shutil


def prepare(
    payload: Path, environment: dict[str, str],
    run: Callable[[list[str], dict[str, str], Path], int],
) -> int:
    scripts = payload / "scripts"
    marker = scripts / "bootstrap-python-launcher.txt"
    if not marker.exists() and not marker.is_symlink():
        return 0
    if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="ascii") != "uv\n":
        raise RuntimeError("Engine bootstrap launcher declaration is invalid")
    windows = os.name == "nt"
    selector = scripts / "_bootstrap_python" / ("select.ps1" if windows else "select.sh")
    if selector.resolve() != selector.absolute() or not selector.is_file():
        raise RuntimeError("Declared engine bootstrap preparation entry is unavailable")
    host = (
        shutil.which("pwsh") or shutil.which("powershell.exe")
        if windows else shutil.which("bash")
    )
    if host is None:
        raise RuntimeError("Engine bootstrap preparation requires its native shell")
    command = (
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(selector),
         "-Operation", "Prepare"]
        if windows else [host, str(selector), "Prepare"]
    )
    return run(command, environment, payload)
