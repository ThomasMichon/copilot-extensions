"""Subprocess-boundary routing for the existing Docker-shaped call sites.

``docker exec`` / ``docker cp`` argv aimed at a member owned by an external
runtime driver is carried out through that driver instead of the Docker CLI.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _owner_of(target: str):
    from . import runtime_drivers  # late: runtime_drivers re-exports this module

    return runtime_drivers.owner_of(target)




@dataclass
class _ExecArgs:
    target: str
    command: list[str]
    user: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    inherit_env: list[str] = field(default_factory=list)
    workdir: str | None = None
    interactive: bool = False
    unsupported: list[str] = field(default_factory=list)


_VALUE_OPTIONS = {"-u", "--user", "-e", "--env", "-w", "--workdir"}


def _parse_docker_exec(argv: list[str]) -> _ExecArgs | None:
    if len(argv) < 3 or argv[0] != "docker" or argv[1] != "exec":
        return None
    parsed = _ExecArgs(target="", command=[])
    index = 2
    while index < len(argv):
        option = argv[index]
        if option in _VALUE_OPTIONS and index + 1 < len(argv):
            value = argv[index + 1]
            if option in {"-u", "--user"}:
                parsed.user = value
            elif option in {"-w", "--workdir"}:
                parsed.workdir = value
            elif "=" in value:
                key, val = value.split("=", 1)
                parsed.env[key] = val
            else:
                parsed.inherit_env.append(value)
            index += 2
            continue
        if option.startswith("-"):
            letters = option[1:] if not option.startswith("--") else ""
            if option == "--interactive" or (letters and set(letters) <= {"i", "t"}):
                parsed.interactive = parsed.interactive or "i" in letters or option == "--interactive"
            elif option != "--tty":
                parsed.unsupported.append(option)
            index += 1
            continue
        break
    if index >= len(argv):
        return None
    parsed.target = argv[index]
    parsed.command = list(argv[index + 1:])
    return parsed


def _parse_docker_cp(argv: list[str]) -> tuple[str, str, str] | None:
    if len(argv) != 4 or argv[0] != "docker" or argv[1] != "cp":
        return None
    target, sep, path = argv[3].partition(":")
    if not sep or not target or not path:
        return None
    return argv[2], target, path


def run_routed(
    argv: list[str], *, input_text: str | None = None, timeout: float = 30.0,
) -> subprocess.CompletedProcess | None:
    """Run a Docker-shaped exec/cp through the owning external driver.

    Returns ``None`` (the caller runs ``argv`` unchanged) unless ``argv``
    targets a member owned by an external driver.
    """
    parsed = _parse_docker_exec(argv)
    if parsed is not None:
        route = _owner_of(parsed.target)
        if route is None:
            return None
        driver, name = route.driver, route.name
        if parsed.unsupported:
            raise RuntimeError(
                f"runtime driver '{driver.name}' cannot express docker exec "
                f"options {parsed.unsupported}"
            )
        env = dict(parsed.env)
        for key in parsed.inherit_env:
            if key in os.environ:
                env[key] = os.environ[key]
        result = driver.exec(
            name, parsed.command, user=parsed.user, env=env,
            workdir=parsed.workdir,
            stdin=input_text if parsed.interactive else None,
            timeout=timeout, instance_id=route.instance_id,
        )
        return subprocess.CompletedProcess(
            [driver.name, "exec", name], result.exit_code, result.stdout, result.stderr,
        )
    copied = _parse_docker_cp(argv)
    if copied is not None:
        source, target, path = copied
        route = _owner_of(target)
        if route is None:
            return None
        route.driver.copy_in(
            route.name, Path(source), path, instance_id=route.instance_id,
        )
        return subprocess.CompletedProcess(
            [route.driver.name, "copy-in", route.name], 0, "", "",
        )
    return None


def stream_routed(argv: list[str]) -> list[str]:
    """Re-target an interactive ``docker exec`` at its owning external driver."""
    parsed = _parse_docker_exec(argv)
    if parsed is None:
        return argv
    route = _owner_of(parsed.target)
    if route is None:
        return argv
    if parsed.unsupported:
        raise RuntimeError(
            f"runtime driver '{route.driver.name}' cannot express docker exec "
            f"options {parsed.unsupported}"
        )
    return route.driver.stream_exec_argv(
        route.name, parsed.command, user=parsed.user, env=parsed.env,
        inherit_env=parsed.inherit_env, workdir=parsed.workdir,
        instance_id=route.instance_id,
    )
