"""Finite interpreter selection and participant environment for Manager installation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from typing import TextIO, TypedDict

BOOTSTRAP_PYTHON = "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"


def bootstrap_hold_path() -> str:
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE", "")
    return os.path.abspath(os.path.expanduser(override)) if override else ""


@contextmanager
def bootstrap_python_environment() -> Iterator[None]:
    previous = os.environ.get(BOOTSTRAP_PYTHON)
    base = getattr(sys, "_base_executable", None)
    os.environ[BOOTSTRAP_PYTHON] = base if isinstance(base, str) and base else sys.executable
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(BOOTSTRAP_PYTHON, None)
        else:
            os.environ[BOOTSTRAP_PYTHON] = previous


class ChildOptions(TypedDict):
    env: dict[str, str]


class ApplicationOptions(TypedDict, total=False):
    application: Path


class StageOptions(TypedDict, total=False):
    stage_only: bool


def installation_child_options() -> ChildOptions:
    from update_driver import participant_environment

    return {"env": {**os.environ, **participant_environment()}}


def windows_console_stream(stream: TextIO | None) -> bool:
    if stream is None:
        return False
    import ctypes
    from ctypes import wintypes
    import msvcrt

    mode = wintypes.DWORD()
    probe = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True).GetConsoleMode
    probe.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    probe.restype = wintypes.BOOL
    return bool(probe(getattr(msvcrt, "get_osfhandle")(stream.fileno()), ctypes.byref(mode)))
