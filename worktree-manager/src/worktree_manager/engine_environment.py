"""Environment isolation for independently installed engine processes."""

from __future__ import annotations

import os

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
