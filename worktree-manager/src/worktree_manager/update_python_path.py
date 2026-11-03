"""Publish the running interpreter's physical prerequisite after a waited update."""

from __future__ import annotations

import os
from pathlib import Path
import sys

def _prepend(path: str, directory: str) -> str:
    if not Path(directory).is_absolute() or any(
        character == ";" or ord(character) < 32 for character in directory
    ):
        raise ValueError("Python directory cannot be represented safely on PATH")
    # Nonidentical aliases may name distinct Windows case-sensitive directories.
    remaining = [entry for entry in path.split(";") if entry != directory] if path else []
    return ";".join([directory, *remaining])


def _notify_environment_change() -> None:
    import ctypes
    from ctypes import wintypes

    send = ctypes.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
    send.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    send.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    if not send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)):
        raise OSError("User Python PATH was written, but environment-change notification failed")


def _publish_user_path(directory: str) -> bool:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            path, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            path, kind = "", winreg.REG_EXPAND_SZ
        if not isinstance(path, str) or kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            raise ValueError("User PATH is not a registry string; it was not changed")
        if kind == winreg.REG_EXPAND_SZ and "%" in directory:
            raise ValueError("Physical Python directory contains '%' and cannot be published on expandable user PATH")
        preferred = _prepend(path, directory)
        changed = preferred != path
        if changed:
            winreg.SetValueEx(key, "Path", 0, kind, preferred)
        if winreg.QueryValueEx(key, "Path") != (preferred, kind):
            raise OSError("User Python PATH changed before verification; reconciliation is incomplete")
    # An earlier write may have succeeded while its notification failed.
    _notify_environment_change()
    return changed


def finish_update(command: list[str], exit_code: int) -> int:
    excluded = {
        "--auto", "--detach", "--auth-report", "--request-status", "--recovery-status",
        "--recover-after-restart", "--recover-for-repair", "--target", "--help", "-h",
    }
    if (
        exit_code != 0 or sys.platform != "win32" or command[:1] != ["update"]
        or any(argument.partition("=")[0] in excluded for argument in command[1:])
    ):
        return exit_code
    try:
        python = Path(sys._base_executable).resolve(strict=True)
        if (
            sys.version_info < (3, 11) or python.name.casefold() != "python.exe"
            or not python.is_file() or "windowsapps" in {part.casefold() for part in python.parts}
        ):
            raise ValueError("Running Python cannot supply a supported physical public python command")
        directory = str(python.parent)
        preferred = _prepend(os.environ.get("PATH", ""), directory)
        contained = (
            os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
            or bool(os.environ.get("PYTEST_CURRENT_TEST"))
        )
        changed = not contained and _publish_user_path(directory)
        os.environ["PATH"] = preferred
        if changed:
            print(
                "Supported Python is on the user PATH. "
                "Restart existing terminals to refresh their inherited environment.",
                file=sys.stderr,
            )
    except (OSError, ValueError) as error:
        print(f"Python PATH: {error}", file=sys.stderr)
        return 1
    return 0
