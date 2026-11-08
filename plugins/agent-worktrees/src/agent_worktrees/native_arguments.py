"""Add native arguments to managed launch commands without importing runtime state."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence, Set
from pathlib import PureWindowsPath

VALUE_OPTIONS = {
    "-i", "--interactive", "-p", "--prompt", "--model", "--reasoning-effort",
    "--context", "--auto-tier", "--agent", "-n", "--name", "--session-id",
    "--log-dir", "--extension-sdk-path", "--log-level", "--stream", "--output-format",
    "--add-dir", "--attachment", "--disable-mcp-server", "--add-github-mcp-toolset",
    "--add-github-mcp-tool", "--plugin-dir", "--additional-mcp-config", "--mcp-github-auth",
    "--max-autopilot-continues", "--mode", "--environment-host", "--environment-compute-id",
    "--dynamic-retrieval", "--enable-mcp-server", "--max-ai-credits", "--usage-output-file",
}
SETUP_VALUE_OPTIONS = {
    "-machine", "-setuphook", "-configroot", "-runtimepython", "-sessionpath",
    "-envscript", "-copilotpath", "-launchseedrecord", "-launchseedid",
    "-launchseedruntimepython", "--machine", "--setup-hook", "--config-root",
    "--runtime-python", "--session-path", "--env-script", "--copilot-path",
    "--launch-seed-record", "--launch-seed-id", "--launch-seed-runtime-python",
    "-launchseedpreparation", "-launchseednativeroot", "-launchseedpreserveid",
    "-launchnativeresumesession", "-launchattemptid", "-launchresolutionid", "-launchparentid",
    "--launch-seed-preparation", "--launch-seed-native-root", "--launch-seed-preserve-id",
    "--launch-native-resume-session", "--launch-attempt-id", "--launch-resolution-id", "--launch-parent-id",
}


def powershell_invocation(arguments: Sequence[str]) -> str:
    return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in arguments)


def bash_command_index(command, *, start=0, limit=None):
    index = start + 1
    end = len(command) if limit is None else limit
    while index < end:
        arg = command[index]
        if not arg.startswith("-") or arg == "--":
            break
        if arg in {"-o", "-O", "--rcfile", "--init-file"}:
            index += 2
            continue
        if not arg.startswith("--") and "c" in arg[1:]:
            return index + 1
        index += 1
    return None


def _insert(
    arguments: Sequence[str], additions: Sequence[str], value_options: Set[str] = VALUE_OPTIONS,
) -> list[str]:
    index = 0
    while index < len(arguments):
        if arguments[index] == "--":
            break
        takes_value = arguments[index] in value_options or arguments[index] == "-C"
        if takes_value and index + 1 >= len(arguments):
            raise ValueError(f"Prepared native option {arguments[index]} requires a value")
        index += 2 if takes_value else 1
    return [*arguments[:index], *additions, *arguments[index:]]


def append_native_arguments(command: Sequence[str], arguments: Sequence[str]) -> list[str]:
    """Append at the prepared native boundary, not after its transport."""
    result = list(command)
    if not arguments:
        return result
    setup = any(
        index and result[index - 1].casefold() == "-file"
        and PureWindowsPath(arg).name.casefold() == "default-setup.ps1"
        for index, arg in enumerate(result)
    )
    if setup and "-NativeArgumentsJson" in result:
        index = result.index("-NativeArgumentsJson") + 1
        if index >= len(result):
            raise ValueError("Prepared native argument envelope is missing its payload")
        envelope = json.loads(result[index])
        native = envelope.get("args") if isinstance(envelope, dict) else None
        if not isinstance(native, list) or any(not isinstance(arg, str) for arg in native):
            raise ValueError("Invalid prepared native argument envelope")
        envelope["args"] = _insert(native, arguments)
        result[index] = json.dumps(envelope)
        return result
    for index, arg in enumerate(result):
        if PureWindowsPath(arg).stem.casefold() not in {"pwsh", "powershell"}:
            continue
        for target in range(index + 1, len(result) - 1):
            if result[target].casefold() == "-file":
                break
            if result[target].casefold() in {"-c", "-command"}:
                if result[target + 1].startswith("& "):
                    source = result[target + 1]
                    values = [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", source)]
                    if not values or powershell_invocation(values) != source:
                        raise ValueError("Invalid prepared PowerShell invocation")
                    result[target + 1] = powershell_invocation(
                        [values[0], *_insert(values[1:], arguments)]
                    )
                    return result
                break
    start = 0
    for index, arg in enumerate(result):
        if not index or PureWindowsPath(arg).name.casefold() not in {
            "launch-command.ps1", "launch-command.sh",
        }:
            continue
        if result[index - 1].casefold() != "-file" and PureWindowsPath(
            result[index - 1]
        ).stem.casefold() != "bash":
            continue
        delimiter = index + 1
        if delimiter < len(result) and result[delimiter] == "--recovery":
            delimiter += 1
        if delimiter < len(result) and result[delimiter] == "--":
            start = delimiter + 1
    if start >= len(result):
        raise ValueError("Prepared launch command is missing its native invocation")
    executable = PureWindowsPath(result[start]).stem.casefold()
    if executable == "copilot":
        return [*result[:start + 1], *_insert(result[start + 1:], arguments)]
    if executable == "bash" and start + 1 < len(result):
        script = bash_command_index(result, start=start)
        if script is not None:
            boundary = script + 2
            return [*result[:boundary], *_insert(result[boundary:], arguments)]
        if PureWindowsPath(result[start + 1]).name == "default-setup.sh":
            values = VALUE_OPTIONS | SETUP_VALUE_OPTIONS | {"--execution-directory"}
            return [*result[:start + 2], *_insert(result[start + 2:], arguments, values)]
    return [*result, *arguments]
