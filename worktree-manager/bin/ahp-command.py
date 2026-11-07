"""Rebind a prepared launch's native options without changing positional data."""

from __future__ import annotations

import getopt
import json
from pathlib import PureWindowsPath
import re
import shlex
import sys

from agent_worktrees.native_arguments import SETUP_VALUE_OPTIONS, VALUE_OPTIONS, bash_command_index


def _option_start(command: list[str]) -> int:
    start = 0
    while start < len(command):
        executable = PureWindowsPath(command[start]).stem.casefold()
        if executable == "env":
            try:
                _options, remaining = getopt.getopt(
                    command[start + 1:], "iu:C:v",
                    ["ignore-environment", "unset=", "chdir=", "debug"],
                )
            except getopt.GetoptError as error:
                raise ValueError(f"Unsupported prepared env arguments: {error}") from error
            start = len(command) - len(remaining)
            if command[start:start + 1] == ["-"]:
                start += 1
            while start < len(command) and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", command[start]):
                start += 1
            continue
        script = None
        if executable in {"pwsh", "powershell"}:
            index = start + 1
            while index < len(command) and command[index].casefold() in {
                "-noprofile", "-nologo", "-noninteractive",
            }:
                index += 1
            if command[index:index + 1] and command[index].casefold() == "-file":
                script = index + 1
        elif executable == "bash":
            if start + 1 < len(command) and not command[start + 1].startswith("-"):
                script = start + 1
            else:
                boundary = bash_command_index(command, start=start)
                if boundary is not None:
                    return boundary + 2
        if script is not None and script < len(command):
            name = PureWindowsPath(command[script]).name.casefold()
            if name in {"default-setup.ps1", "default-setup.sh"}:
                return script + 1
            if name in {"launch-command.ps1", "launch-command.sh"}:
                delimiter = script + 1
                if command[delimiter:delimiter + 1] == ["--recovery"]:
                    delimiter += 1
                if command[delimiter:delimiter + 1] != ["--"]:
                    raise ValueError("Copied launch command has no native boundary")
                start = delimiter + 1
                continue
        if executable.startswith("python") and command[start + 1:start + 3] == [
            "-m", "agent_worktrees.launch_seed_exec",
        ]:
            start = command.index("--", start + 3) + 1
            continue
        return start + 1
    raise ValueError("Prepared command has no native executable")


def rebind(command: list[str], endpoint: str, session: str) -> list[str]:
    index = _option_start(command)
    result = command[:index]
    experimental = False
    value_options = VALUE_OPTIONS | SETUP_VALUE_OPTIONS | {"-C", "--execution-directory"}
    while index < len(command) and command[index] != "--":
        argument = command[index]
        name, separator, _value = argument.partition("=")
        if name in {"--resume", "--ahp"}:
            index += 1
            if not separator and index < len(command) and not command[index].startswith("-"):
                index += 1
            continue
        result.append(argument)
        experimental = experimental or argument == "--experimental"
        index += 1
        if not separator and (argument in value_options or argument.casefold() in value_options):
            if index >= len(command):
                raise ValueError(f"Prepared native option {argument} requires a value")
            result.append(command[index])
            index += 1
    return [
        *result, *([] if experimental else ["--experimental"]),
        "--ahp", endpoint, "--resume=" + session, *command[index:],
    ]


if __name__ == "__main__":
    output, endpoint, session, delimiter, *command = sys.argv[1:]
    if output not in {"json", "bash"} or delimiter != "--":
        raise ValueError("Unsupported prepared AHP command format")
    rebound = rebind(command, endpoint, session)
    print(json.dumps(rebound) if output == "json" else shlex.join(rebound))
