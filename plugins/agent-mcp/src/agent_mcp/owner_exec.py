"""Opt-in native exec ownership with a session-only, anonymous MCP overlay."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys


def _provider(entry: dict) -> list[str] | None:
    command, arguments = entry.get("command"), entry.get("args")
    if (
        entry.get("type", "local") not in {"local", "stdio"}
        or not isinstance(command, str) or not os.path.isabs(command)
        or "${" in command or "\0" in command
        or not isinstance(arguments, list)
        or not all(
            isinstance(value, str) and "\0" not in value and "${" not in value
            for value in arguments
        )
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


def overlay(
    configuration: dict, names: list[str], *, pid: int, birth: str,
    environment: dict[str, str],
) -> dict:
    """Carry selected entries to MCP start without querying a mutable provider."""
    return _overlay(configuration, names, {"pid": pid, "birth": birth})


def windows_overlay(configuration: dict, names: list[str], *, binding: str) -> dict:
    """Prepare a snapshot-bound overlay; no Windows client is enrolled here."""
    from .owner_snapshot import section_name

    section_name(binding)
    snapshot = Path(__file__).with_name("owner_snapshot.py").read_text(encoding="utf-8")
    return _overlay(configuration, names, {"binding": binding}, snapshot=snapshot)


def _overlay(configuration: dict, names: list[str], owner: dict, *, snapshot: str = "") -> dict:
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
        query = _provider(entry)
        extra = entry.get("env", {})
        if (
            query is not None and isinstance(extra, dict)
            and all(isinstance(key, str) and isinstance(value, str) for key, value in extra.items())
            and not any("${" in value or "\0" in value for value in extra.values())
        ):
            replacement = copy.deepcopy(entry)
            original = [entry["command"], *entry["args"]]
            remaining = entry["args"] if len(query) == 2 else entry["args"][len(query) - 2:]
            request = {
                "original": original, "arguments": remaining, "query": query,
                **owner,
            }
            replacement["command"] = str(Path(sys._base_executable).absolute())
            replacement["args"] = [
                "-I", "-S", "-c", snapshot + "\n" + Path(__file__).with_name("owner_bootstrap.py").read_text(
                    encoding="utf-8",
                ), json.dumps(request, ensure_ascii=True),
            ]
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


def _sealed_overlay(configuration: dict) -> int:
    import fcntl

    # Linux ABI flags remain valid when standalone Python omits their bindings.
    cloexec_allow_sealing = 0x0001 | 0x0002
    create = getattr(os, "memfd_create", None)
    if create is not None:
        descriptor = create("agent-mcp-session-overlay", cloexec_allow_sealing)
    else:
        import ctypes

        library = ctypes.CDLL(None, use_errno=True)
        try:
            create = library.memfd_create
        except AttributeError as error:
            raise ValueError("Linux anonymous-file creation is unavailable") from error
        create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
        create.restype = ctypes.c_int
        descriptor = create(b"agent-mcp-session-overlay", cloexec_allow_sealing)
        if descriptor < 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))
    try:
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(json.dumps(configuration, ensure_ascii=True).encode("utf-8"))
        # Standalone Python builds can omit bindings for these stable Linux ABI values.
        seals = 1 | 2 | 4 | 8
        fcntl.fcntl(descriptor, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        if fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034)) & seals != seals:
            raise ValueError("Anonymous MCP overlay sealing was not confirmed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def execute(configuration: dict, names: list[str], argv: list[str]) -> None:
    """Become the native Linux client; no parent waiter or inherited owner env."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Native owner exec currently requires Linux")
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
    descriptor = _sealed_overlay(replacement)
    try:
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
