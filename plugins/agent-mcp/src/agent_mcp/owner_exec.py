"""Opt-in native exec ownership with a session-only, anonymous MCP overlay."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .owner_capabilities import CONTRACT


def _provider(entry: dict) -> list[str] | None:
    command, arguments = entry.get("command"), entry.get("args")
    if (
        entry.get("type", "local") not in {"local", "stdio"}
        or not isinstance(command, str) or not os.path.isabs(command)
        or not isinstance(arguments, list)
        or not all(isinstance(value, str) and "\0" not in value for value in arguments)
        or "--" in arguments
        or any(value.startswith("--session-owner-") for value in arguments)
    ):
        return None
    prefix: list[str] = []
    if Path(command).name in {"agent-mcp", "agent-mcp.exe"}:
        remaining = arguments
    else:
        index = 1 if arguments[:1] == ["-I"] else 0
        if arguments[index:index + 2] != ["-m", "agent_mcp"]:
            return None
        prefix = arguments[:index + 2]
        remaining = arguments[index + 2:]
    if not remaining or remaining[0] not in {"bridge", "forward"}:
        return None
    return [command, *prefix, "session-owner-capabilities"]


def _supports(entry: dict, environment: dict[str, str]) -> bool:
    from agent_procutil import no_window_kwargs

    query = _provider(entry)
    extra = entry.get("env", {})
    if (
        query is None or not isinstance(extra, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in extra.items())
        or any("${" in value or "\0" in value for value in extra.values())
    ):
        return False
    try:
        result = subprocess.run(
            query, env={**environment, **extra}, cwd=entry.get("cwd"),
            capture_output=True, text=True, timeout=5, **no_window_kwargs(),
        )
        report = json.loads(result.stdout)
        contract = report.get("contract", {})
        return bool(
            result.returncode == 0 and report.get("supported") is True
            and report.get("invocation_allowed") is True
            and contract.get("name") == CONTRACT["name"]
            and type(contract.get("minimum")) is int and contract["minimum"] == 1
            and type(contract.get("maximum")) is int and contract["maximum"] >= 1
            and isinstance(contract.get("capabilities"), list)
            and "explicit-native-owner-pair" in contract.get("capabilities", [])
        )
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return False


def overlay(
    configuration: dict, names: list[str], *, pid: int, birth: str,
    environment: dict[str, str],
) -> dict:
    """Override only explicit names whose actual provider reports pair support."""
    servers = configuration.get("mcpServers")
    if (
        not isinstance(servers, dict) or not names
        or any(not isinstance(name, str) or not name or name not in servers for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("An explicit, unique configured MCP server selection is required")
    selected = {}
    for name in names:
        entry = servers[name]
        if not isinstance(entry, dict):
            raise ValueError("Selected MCP server configuration must be an object")
        if _supports(entry, environment):
            replacement = copy.deepcopy(entry)
            replacement["args"].extend((
                "--session-owner-pid", str(pid), "--session-owner-birth", birth,
            ))
            selected[name] = replacement
    return {"mcpServers": selected}


def _client(argv: list[str], environment: dict[str, str]) -> list[str]:
    if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
        raise ValueError("A literal native client command is required")
    # This first caller does not reinterpret positional delimiters or layered overlays.
    if any(arg == "--" or arg.partition("=")[0] == "--additional-mcp-config" for arg in argv[1:]):
        raise ValueError("Native owner exec does not support delimiters or existing MCP overlays")
    executable = shutil.which(argv[0], path=environment.get("PATH"))
    if executable is None or Path(executable).name != "copilot":
        raise ValueError("Native owner exec requires the explicitly selected copilot executable")
    with open(executable, "rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("Native owner exec cannot attribute a script or forwarding launcher")
    return [executable, *argv[1:]]


def execute(configuration: dict, names: list[str], argv: list[str]) -> None:
    """Become the native Linux client; no parent waiter or inherited owner env."""
    if not sys.platform.startswith("linux") or not hasattr(os, "memfd_create"):
        raise ValueError("Native owner exec currently requires Linux anonymous-file support")
    from agent_procutil._daemon_instance_identity import birth_identity

    from .custody_lifetime import SessionOwner

    environment = os.environ.copy()
    command = _client(argv, environment)
    pid = os.getpid()
    birth = birth_identity(pid)
    with SessionOwner(pid, birth):
        replacement = overlay(configuration, names, pid=pid, birth=birth, environment=environment)
    if not replacement["mcpServers"]:
        os.execve(command[0], command, environment)
        raise RuntimeError("Native client exec unexpectedly returned")
    import fcntl

    descriptor = os.memfd_create("agent-mcp-session-overlay", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(json.dumps(replacement, ensure_ascii=True).encode("utf-8"))
        fcntl.fcntl(
            descriptor, fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE,
        )
        os.set_inheritable(descriptor, True)
        command.extend(("--additional-mcp-config", f"@/proc/self/fd/{descriptor}"))
        os.execve(command[0], command, environment)
        raise RuntimeError("Native client exec unexpectedly returned")
    finally:
        os.close(descriptor)


def command(args: argparse.Namespace) -> int:
    try:
        configuration = json.loads(Path(args.mcp_config).read_text(encoding="utf-8"))
        if not isinstance(configuration, dict):
            raise ValueError("MCP configuration must be an object")
        argv = args.client[1:] if args.client[:1] == ["--"] else args.client
        execute(configuration, args.server, argv)
    except (OSError, ValueError) as error:
        print(f"agent-mcp: native owner exec refused: {error}", file=sys.stderr)
        return 2
    return 0
