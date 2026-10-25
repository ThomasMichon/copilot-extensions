"""Environment isolation and standard streams for independent engine processes."""

from __future__ import annotations

import os
import sys

_PYTHON_PARENT_ENV = {
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "VIRTUAL_ENV",
    "UV_INTERNAL__PYTHONHOME",
    "__PYVENV_LAUNCHER__",
}


def _is_parent_python_variable(name: str, *, windows: bool) -> bool:
    candidate = name.upper() if windows else name
    return candidate in _PYTHON_PARENT_ENV


def _engine_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    windows = os.name == "nt"
    env = {
        key: value
        for key, value in (os.environ if environment is None else environment).items()
        if not _is_parent_python_variable(key, windows=windows)
    }
    env["PYTHONUTF8"] = "1"
    env["PYTHONSAFEPATH"] = "1"
    return env


def _terminal_aware_launch_options() -> dict[str, object]:
    if os.name != "nt":
        return {}
    # Explicit descriptors preserve caller pipes across a headless Windows launch.
    options: dict[str, object] = {
        "stdin": 0, "stdout": 1, "stderr": 2,
    }
    if (
        sys.stdin is not None and sys.stdin.isatty()
        and sys.stdout is not None and sys.stdout.isatty()
    ):
        import ctypes

        if ctypes.windll.kernel32.GetConsoleCP():
            return options
    from agent_procutil import no_window_kwargs

    options.update(no_window_kwargs())
    return options
