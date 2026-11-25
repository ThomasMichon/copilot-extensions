"""Prepared-only UV admission for an already selected application interpreter."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from .owned_process import run_caller_owned

_SELECTION_ENV = frozenset({
    "UV_PYTHON", "UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
    "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC",
    "UV_NO_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_ISOLATED",
    "UV_ENV_FILE", "UV_CONFIG_FILE", "UV_NO_CONFIG", "VIRTUAL_ENV", "CONDA_PREFIX",
    "PYTHONPATH", "PYTHONHOME", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__",
})


def resolve_runtime_python(
    expected_python: str | os.PathLike[str], *, uv: str | os.PathLike[str] | None = None,
) -> str:
    """Ask UV to retain the selected environment without setup or reselection.

    The application chooses its completed generation first. This operation is
    the Python counterpart of ``Resolve-UvRuntimePython``/``resolve_uv_runtime_python``:
    one bounded lookup, no interpreter acquisition and exact lexical agreement.
    """
    expected = os.fspath(expected_python)
    if (
        not os.path.isabs(expected) or any(c in expected for c in "\r\n\0")
        or not Path(expected).is_file()
    ):
        raise FileNotFoundError(
            "The selected application runtime is unavailable; use explicit installation management."
        )
    selected_uv = os.fspath(uv) if uv is not None else shutil.which("uv")
    if not selected_uv:
        raise FileNotFoundError(
            "UV is unavailable; prepare the command prerequisites through explicit installation management."
        )
    if (
        not os.path.isabs(selected_uv) or any(c in selected_uv for c in "\r\n\0")
        or not Path(selected_uv).is_file()
        or (os.name == "nt" and (
            "windowsapps" in {part.casefold() for part in Path(selected_uv).parts}
            or Path(selected_uv).suffix.casefold() != ".exe"
        ))
        or (os.name != "nt" and not os.access(selected_uv, os.X_OK))
    ):
        raise FileNotFoundError(
            "Selected UV must be an existing absolute executable, not an execution alias; "
            "use explicit installation management."
        )
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in _SELECTION_ENV
    }
    environment.update(UV_NO_CONFIG="1", UV_OFFLINE="1")
    result = run_caller_owned(
        [selected_uv, "python", "find", "--no-project", "--managed-python",
         "--no-python-downloads", expected],
        timeout=10.0, input_text="", max_stdout_bytes=65536, max_stderr_bytes=65536,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError(
            "Application runtime managed Python lookup failed; use explicit installation management."
        )
    if result.stdout.rstrip("\r\n") != expected:
        raise RuntimeError(
            "UV did not select the addressed application runtime; use explicit installation management."
        )
    return expected
