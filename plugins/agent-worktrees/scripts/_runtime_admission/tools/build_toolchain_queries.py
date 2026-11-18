"""Bounded subprocess options and installed-version probes for build toolchains."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, cast

from governed_feed_trust import ArtifactBuildError
from installer_dependency_config import (
    native_retained_path as _native_retained_path,
    remaining_timeout as _remaining_timeout,
)

native_retained_path = cast(Callable[[Path], str], _native_retained_path)
remaining_timeout = cast(Callable[..., float], _remaining_timeout)


def execution_options(deadline: float | None) -> dict[str, Any]:
    """Use the caller's remaining budget without introducing another deadline."""
    options: dict[str, Any] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    }
    if deadline is not None:
        options["timeout"] = remaining_timeout(deadline, error_type=ArtifactBuildError)
    return options


def query_versions(
    python: Path, packages: tuple[str, ...], environment: dict[str, str], *,
    deadline: float | None = None,
) -> dict[str, str]:
    """Read required distributions from the selected interpreter before publication."""
    query = run_interpreter(
        python,
        [
            "-I", "-c",
            "import importlib.metadata as m, json, sys\n"
            "print(json.dumps({p: m.version(p) for p in sys.argv[1:]}))",
            *packages,
        ],
        environment, deadline=deadline,
    )
    if query.returncode != 0:
        raise ArtifactBuildError(
            f"could not read installed toolchain versions from {python}:\n"
            f"{query.stdout}\n{query.stderr}"
        )
    try:
        versions = json.loads(query.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{python}: toolchain version query produced non-JSON output: {exc}"
        ) from exc
    missing = [package for package in packages if not versions.get(package)]
    if missing:
        raise ArtifactBuildError(
            f"{python}: locked toolchain venv is missing required "
            f"package(s) {missing} -- refusing to record an incomplete toolchain lock"
        )
    return cast(dict[str, str], versions)


def run_interpreter(
    python: Path, arguments: list[str], environment: dict[str, str], *,
    deadline: float | None = None, input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded read-only probe through its explicitly selected interpreter."""
    executable = native_retained_path(python)
    return subprocess.run(
        [str(python), *arguments], executable=executable, input=input_text,
        capture_output=True, text=True, env=environment, **execution_options(deadline),
    )
