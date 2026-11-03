"""Independent user scopes for residents launched from systemd cleanup units."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping


def _native_paths(text: str) -> dict[str, PurePosixPath]:
    paths = {}
    for line in text.splitlines():
        hierarchy, controllers, path = line.split(":", 2)
        if hierarchy == "0" and not controllers:
            key = "unified"
        elif "name=systemd" in controllers.split(","):
            key = "systemd"
        else:
            continue
        if not path.startswith("/"):
            raise ValueError("invalid native caller cgroup")
        paths[key] = PurePosixPath(path)
    return paths


def caller_unit() -> str | None:
    if sys.platform != "linux":
        return None
    paths = _native_paths(Path("/proc/self/cgroup").read_text(encoding="utf-8"))
    boundaries = _native_paths(Path("/proc/1/cgroup").read_text(encoding="utf-8"))
    for key, path in paths.items():
        boundary = boundaries.get(key)
        # A container's host-visible scope belongs to its PID namespace, not
        # the caller's service manager. Keep any local units below that boundary.
        if boundary is not None and path.is_relative_to(boundary):
            path = path.relative_to(boundary)
        for component in reversed(path.parts):
            if component.endswith((".service", ".scope")) and not (
                component.startswith("user@") and component.endswith(".service")
            ):
                return component
    return None


def runner_version(runner: str, environment: Mapping[str, str], *, cwd: str) -> int:
    try:
        result = subprocess.run(
            [runner, "--version"], cwd=cwd, env=dict(environment), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("resident systemd scope compatibility probe timed out") from error
    match = re.match(rb"systemd (\d+)(?:\s|$)", result.stdout)
    if result.returncode or match is None:
        raise RuntimeError("cannot determine resident systemd scope compatibility")
    return int(match[1])


def resolve_runner(environment: Mapping[str, str], *, cwd: str) -> str:
    search_path = os.pathsep.join(
        entry if os.path.isabs(entry) else os.path.join(cwd, entry)
        for entry in os.get_exec_path(environment)
    )
    runner = shutil.which("systemd-run", path=search_path)
    if runner is None:
        raise RuntimeError("resident systemd lifetime requires systemd-run")
    return os.path.abspath(runner)


def scope_command(
    command: list[str], instance_id: str, environment: Mapping[str, str], *, cwd: str,
) -> list[str]:
    """systemd-run's scope mode execs the target in its own PID after admission."""
    if caller_unit() is None:
        return command
    runner = resolve_runner(environment, cwd=cwd)
    version = runner_version(runner, environment, cwd=cwd)
    prefix = [
        runner, "--user", "--scope", "--quiet", "--collect", "--no-ask-password",
        "--unit=agent-daemon-" + instance_id + ".scope",
        "--description=Attributed resident daemon",
    ]
    # Earlier versions never expanded scope argv; 254 introduced expansion.
    if version >= 254:
        prefix.append("--expand-environment=no")
    return [*prefix, "--", *command]
