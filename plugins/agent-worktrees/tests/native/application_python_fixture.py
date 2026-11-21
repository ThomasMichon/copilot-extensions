"""Private managed-interpreter preparation for real application fixtures."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class ApplicationCommand(Protocol):
    def __call__(
        self,
        arguments: Sequence[str | Path | None],
        *,
        cwd: Path,
        env: dict[str, str],
        deadline: float,
    ) -> subprocess.CompletedProcess[str]: ...


def managed_python_environment(
    environment: dict[str, str],
    *,
    directory: Path,
    uv: str,
    deadline: float,
    run: ApplicationCommand,
) -> dict[str, str]:
    if time.monotonic() >= deadline:
        raise TimeoutError("Application managed-Python preparation deadline expired")
    directory.mkdir()
    prepared = {
        **environment,
        "UV_PYTHON_INSTALL_DIR": str(directory),
    }
    prepared.pop("UV_PYTHON_DOWNLOADS", None)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    run(
        [uv, "python", "install", "--no-bin", "--no-registry", f"cpython@{version}"],
        cwd=directory,
        env=prepared,
        deadline=deadline,
    )
    return {**environment, "UV_PYTHON_INSTALL_DIR": str(directory), "UV_PYTHON_DOWNLOADS": "never"}
