"""Independent user scopes for residents launched from systemd cleanup units."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping


def caller_unit() -> str | None:
    if sys.platform != "linux":
        return None
    for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, path = line.split(":", 2)
        if not (hierarchy == "0" and not controllers or "name=systemd" in controllers.split(",")):
            continue
        if not path.startswith("/"):
            raise ValueError("invalid native caller cgroup")
        for component in reversed(PurePosixPath(path).parts):
            if component.endswith((".service", ".scope")) and not (
                component.startswith("user@") and component.endswith(".service")
            ):
                return component
    return None


def scope_command(
    command: list[str], instance_id: str, environment: Mapping[str, str],
) -> list[str]:
    """systemd-run's scope mode execs the target in its own PID after admission."""
    if caller_unit() is None:
        return command
    runner = shutil.which("systemd-run", path=environment.get("PATH", os.defpath))
    if runner is None:
        raise RuntimeError("resident systemd lifetime requires systemd-run")
    result = subprocess.run(
        [runner, "--version"], env=dict(environment), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False,
    )
    match = re.match(rb"systemd (\d+)(?:\s|$)", result.stdout)
    if result.returncode or match is None:
        raise RuntimeError("cannot determine resident systemd scope compatibility")
    prefix = [
        runner, "--user", "--scope", "--quiet", "--collect", "--no-ask-password",
        "--unit=agent-daemon-" + instance_id + ".scope",
        "--description=Attributed resident daemon",
    ]
    # Earlier versions never expanded scope argv; 254 introduced expansion.
    if int(match[1]) >= 254:
        prefix.append("--expand-environment=no")
    return [*prefix, "--", *command]
