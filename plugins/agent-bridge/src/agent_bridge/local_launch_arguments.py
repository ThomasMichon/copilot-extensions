"""Consume the native argument representation returned by a local workspace owner."""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

NATIVE_VALUE_OPTIONS = {
    "-i", "--interactive", "-p", "--prompt", "--model", "--reasoning-effort",
    "--context", "--auto-tier", "--agent", "-n", "--name", "--session-id",
    "--log-dir", "--extension-sdk-path", "--log-level", "--stream", "--output-format",
    "--add-dir", "--attachment", "--disable-mcp-server", "--add-github-mcp-toolset",
    "--add-github-mcp-tool", "--plugin-dir", "--additional-mcp-config", "--mcp-github-auth",
    "--max-autopilot-continues", "--mode", "--environment-host", "--environment-compute-id",
    "--dynamic-retrieval", "--enable-mcp-server", "--max-ai-credits", "--usage-output-file",
}


def _worktrees_setup_script() -> Path:
    from . import _peer_launch, session_lifecycle_cli
    from .install_paths import uses_legacy_install_dir

    own_root = session_lifecycle_cli._agent_bridge_owner_root()
    context = session_lifecycle_cli._agent_bridge_installation_context()
    if context:
        try:
            owner = _peer_launch.validate_owner("agent-bridge", own_root, context)
        except (OSError, ValueError, ImportError) as exc:
            raise _peer_launch.ContextRefused(f"agent-bridge installation context refused: {exc}") from exc
        root = Path(owner["cellRoot"]) / "plugins" / "agent-worktrees"
    else:
        if not uses_legacy_install_dir(own_root):
            raise _peer_launch.ContextRefused("non-legacy bridge requires an installation context")
        root = Path.home() / ".agent-worktrees"  # marketplace-isolation: allow legacy-compatibility
    return root / "scripts" / "default-setup.ps1"


def _powershell_script_index(command: list[str], start: int) -> int | None:
    if start >= len(command) or PureWindowsPath(command[start]).stem.casefold() not in {
        "pwsh", "powershell",
    }:
        return None
    index = start + 1
    while index < len(command) and command[index].casefold() in {"-noprofile", "-nologo"}:
        index += 1
    if index + 1 < len(command) and command[index].casefold() == "-file":
        return index + 1
    return None


def _deferred_command_start(command: list[str], record: Path | None) -> int | None:
    if not command or PureWindowsPath(command[0]).stem.casefold() not in {"python", "python3"}:
        return 0
    if (
        record is None or len(command) < 6
        or command[1:4] != ["-m", "agent_worktrees.launch_seed_exec", "--record"]
        or Path(command[4]) != record
    ):
        return None
    index = 5
    values = {
        "--seed-id", "--preserve-seed-id", "--native-resume-session",
        "--launch-attempt-id", "--launch-resolution-id", "--launch-parent-id",
    }
    while index < len(command):
        if command[index] == "--":
            return index + 1
        if command[index] == "--admission-only":
            index += 1
        elif command[index] in values and index + 1 < len(command):
            index += 2
        else:
            return None
    return None


def _prepared_script(script: Path, record: Path | None, name: str) -> bool:
    if record is None:
        return False
    try:
        parts = script.relative_to(record.parent / "launch-attempts").parts
    except ValueError:
        return False
    return (
        len(parts) == 3 and parts[2] == name
        and re.fullmatch(r"[0-9a-f]{32}", parts[0]) is not None
        and re.fullmatch(r"[0-9a-f]{32}\.shell", parts[1]) is not None
    )


def _owned_setup_index(command: list[str], record_path: str | None) -> int | None:
    if not any(value.casefold() == "-nativeargumentsjson" for value in command):
        return None
    record = Path(record_path) if record_path and Path(record_path).is_absolute() else None
    start = _deferred_command_start(command, record)
    index = _powershell_script_index(command, start) if start is not None else None
    if index is None:
        return None
    setup = _worktrees_setup_script()
    script = Path(command[index])
    prepared_wrapper = _prepared_script(script, record, "launch-command.ps1")
    if script == setup.with_name("launch-command.ps1") or prepared_wrapper:
        delimiter = index + 1
        if command[delimiter:delimiter + 1] == ["--recovery"]:
            delimiter += 1
        if command[delimiter:delimiter + 1] != ["--"]:
            return None
        index = _powershell_script_index(command, delimiter + 1)
        if index is None:
            return None
        inner = Path(command[index])
        if prepared_wrapper and inner.parent != script.parent:
            return None
        script = inner
    if script == setup:
        return index
    # Copied assets are bound to this resolver's record and selected runtime,
    # not recognized by a filename or a marker that a custom script can copy.
    if (
        _prepared_script(script, record, "default-setup.ps1")
        and index + 6 < len(command)
        and command[index + 1:index + 3] in (
            ["-LaunchSeedPreparation", "seed"], ["-LaunchSeedPreparation", "admission"],
        )
        and command[index + 3:index + 4] == ["-LaunchSeedNativeRoot"]
        and Path(command[index + 4]) == setup.parent.parent
        and command[index + 5:index + 6] == ["-LaunchSeedRecord"]
        and Path(command[index + 6]) == record
    ):
        return index
    return None


def _insert_native_options(native: list[str], arguments: list[str]) -> list[str]:
    index = 0
    while index < len(native) and native[index] != "--":
        takes_value = native[index] in NATIVE_VALUE_OPTIONS or native[index] == "-C"
        if takes_value and index + 1 >= len(native):
            raise RuntimeError(f"Invalid worktree native argument envelope: {native[index]} requires a value")
        index += 2 if takes_value else 1
    return [*native[:index], *arguments, *native[index:]]


def append_local_launch_arguments(
    command: list[str], arguments: list[str], *, record_path: str | None = None,
) -> list[str]:
    """Keep native arguments inside the Worktrees PowerShell setup envelope."""
    result = list(command)
    setup = _owned_setup_index(result, record_path)
    if setup is not None:
        envelopes = [
            index for index in range(setup + 1, len(result))
            if result[index].casefold() == "-nativeargumentsjson"
        ]
        if envelopes:
            index = envelopes[0] + 1
            if len(envelopes) != 1 or index >= len(result):
                raise RuntimeError("Invalid worktree native argument envelope")
            try:
                envelope = json.loads(result[index])
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Invalid worktree native argument envelope") from exc
            native = envelope.get("args") if isinstance(envelope, dict) else None
            if not isinstance(native, list) or any(not isinstance(value, str) for value in native):
                raise RuntimeError("Invalid worktree native argument envelope")
            envelope["args"] = _insert_native_options(native, arguments)
            result[index] = json.dumps(envelope)
            return result
    return [*result, *arguments]
