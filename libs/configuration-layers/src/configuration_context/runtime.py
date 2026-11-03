"""Read a caller-qualified deployed resolver without provisioning a runtime."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from configuration_layers import ConfigurationError

from . import _ProcessOptions

READ_ONLY_CONTRACT = b"# Trace contract: normal/read-only/ready-v1"
MAX_RESOLVER_BYTES = 64 * 1024


def deployed_runtime_python(
    runtime_root: Path, *, resolver: Path, environment: Mapping[str, str],
) -> Path | None:
    """Ask the explicit canonical shell resolver for a completed interpreter.

    The caller qualifies the resolver and runtime root and removes inherited
    routing. This transport neither discovers peers nor selects version slots.
    """
    if not runtime_root.is_absolute() or not resolver.is_absolute():
        raise ConfigurationError("configuration resolver paths must be absolute")
    try:
        with resolver.open("rb") as stream:
            header = stream.read(MAX_RESOLVER_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ConfigurationError("configuration runtime resolver could not be read") from error
    if len(header) > MAX_RESOLVER_BYTES or READ_ONLY_CONTRACT not in header.splitlines():
        raise ConfigurationError("configuration runtime resolver lacks its read-only contract")
    rebound = dict(environment)
    rebound.update({
        "AGENT_RT_ROOT": str(runtime_root),
        "AGENT_RT_RESOLVE_TRACE_MODE": "read-only",
        "AGENT_CONFIGURATION_RESOLVER": str(resolver),
    })
    if os.name == "nt":
        system_root = Path(environment.get("SystemRoot", ""))
        if not system_root.is_absolute():
            raise ConfigurationError("configuration resolver requires an absolute SystemRoot")
        command = [
            str(system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
            ". $env:AGENT_CONFIGURATION_RESOLVER -TraceMode read-only; "
            "if ($AgentRtPy) { [Console]::Out.Write($AgentRtPy) }",
        ]
    else:
        command = [
            "/bin/sh", "-c",
            '. "$1" || exit $?; printf %s "${AGENT_RT_PY:-}"',
            "configuration-runtime", str(resolver),
        ]
    options: _ProcessOptions = (
        {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    )
    try:
        result = subprocess.run(
            command, env=rebound, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=15, check=False, **options,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfigurationError("configuration runtime resolver read failed") from error
    if result.returncode:
        raise ConfigurationError("configuration runtime resolver refused readiness")
    if len(result.stdout) > MAX_RESOLVER_BYTES:
        raise ConfigurationError("configuration runtime resolver exceeds the read bound")
    try:
        selected = result.stdout.decode("utf-8")
    except UnicodeError as error:
        raise ConfigurationError("configuration runtime resolver returned invalid UTF-8") from error
    if not selected:
        return None
    python = Path(selected)
    versions = runtime_root / "versions"
    # Resolve the slot directory, not the leaf: POSIX venv Python is a symlink.
    if (
        not python.is_absolute() or not python.is_file()
        or python.name not in {"python", "python.exe"}
        or python.parent.name not in {"bin", "Scripts"}
        or python.parent.parent.parent != versions
        or python.parent.resolve().parent.parent != versions.resolve()
    ):
        raise ConfigurationError("configuration runtime resolver escaped its version slots")
    return python
