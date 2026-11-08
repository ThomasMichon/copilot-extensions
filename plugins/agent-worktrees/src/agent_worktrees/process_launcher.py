"""Bind a venue's process-only native executable without manager secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath

from . import installer
from .native_arguments import SETUP_VALUE_OPTIONS as _SETUP_VALUE_OPTIONS
from .native_arguments import VALUE_OPTIONS as _NATIVE_VALUE_OPTIONS
from .native_arguments import bash_command_index, powershell_invocation


def _normalized_setup_index(command) -> int | None:
    scripts = installer.install_dir() / "scripts"
    shell = PureWindowsPath(command[0]).stem.casefold() if command else ""
    if len(command) > 1 and shell == "bash" and Path(command[1]) == scripts / "default-setup.sh":
        return 1
    flags = [arg.casefold() for arg in command]
    if shell in {"pwsh", "powershell"} and "-file" in flags:
        index = flags.index("-file")
        if (
            index + 1 < len(command)
            and all(arg in {"-noprofile", "-nologo"} for arg in flags[1:index])
            and Path(command[index + 1]) == scripts / "default-setup.ps1"
        ):
            return index + 1
    return None


def _bash_argument_boundary(command, passthrough_start: int, error_type):
    if not command or PureWindowsPath(command[0]).stem.casefold() != "bash":
        return command, None
    script_index = bash_command_index(command, limit=passthrough_start)
    if script_index is None:
        return command, None
    script = command[script_index].strip() if script_index < passthrough_start else ""
    if script in {"copilot", "exec copilot"}:
        command = [*command[:script_index], 'exec copilot "$@"', *command[script_index + 1:]]
    elif script not in {'copilot "$@"', 'exec copilot "$@"'}:
        raise error_type(
            "A Bash command-string launch must directly forward Copilot arguments; "
            "use a launcher script for a compound command"
        )
    if passthrough_start == script_index + 1:
        command = [*command[:passthrough_start], "copilot", *command[passthrough_start:]]
    return command, script_index + 2


def native_option_tokens(command, *, passthrough_start: int):
    """Return native option names, excluding values, setup fields and shell slots."""
    normalized = _normalized_setup_index(command)
    if normalized is not None:
        index = normalized + 1
    elif command and PureWindowsPath(command[0]).stem.casefold() == "bash" and (
        script := bash_command_index(command, limit=passthrough_start)
    ) is not None:
        index = script + 2 if passthrough_start > script + 1 else script + 1
    else:
        index = next(
            (i + 1 for i, arg in enumerate(command[:passthrough_start])
             if PureWindowsPath(arg).stem.casefold() == "copilot"), 1,
        )
    result = []
    while index < len(command) and command[index] != "--":
        arg = command[index]
        if normalized is not None and (
            arg.casefold() in _SETUP_VALUE_OPTIONS or arg.casefold() == "-executiondirectory"
        ):
            index += 2
            continue
        if arg.startswith("-"):
            result.append(arg.split("=", 1)[0])
        index += 2 if arg in _NATIVE_VALUE_OPTIONS or arg == "-C" else 1
    return result


def bind_execution_directory(
    command, work_dir: str, error_type, *, passthrough_start: int = 1, native_suffix=(),
):
    """Keep native resume in the selected workspace, not its historical cwd."""
    selected = os.path.normcase(os.path.abspath(work_dir))
    command, bash_start = _bash_argument_boundary(command, passthrough_start, error_type)
    normalized = _normalized_setup_index(command)
    # Shell options belong to the launcher, not to the native Copilot argv.
    if bash_start is not None:
        index = bash_start
    elif normalized is not None:
        index = normalized + 1
    else:
        index = next(
            (i + 1 for i, arg in enumerate(command[:passthrough_start])
             if PureWindowsPath(arg).stem.casefold() == "copilot"),
            passthrough_start,
        )
    result = list(command[:index])
    while index < len(command):
        arg = command[index]
        if arg == "--":
            break
        if arg in _NATIVE_VALUE_OPTIONS or (
            normalized is not None and arg.casefold() in _SETUP_VALUE_OPTIONS
        ):
            if index + 1 >= len(command):
                raise error_type(f"Copilot launch option {arg} requires a value")
            result.extend(command[index:index + 2])
            index += 2
            continue
        setup_flag = normalized is not None and (
            (command[normalized].endswith(".ps1") and arg.casefold() == "-executiondirectory")
            or (command[normalized].endswith(".sh") and arg == "--execution-directory")
        )
        if arg == "-C" or arg.startswith("-C=") or setup_flag:
            if arg == "-C" or setup_flag:
                index += 1
                value = command[index] if index < len(command) else ""
            else:
                value = arg[3:]
            if (
                not value
                or os.path.normcase(os.path.abspath(os.path.join(work_dir, value))) != selected
            ):
                raise error_type("Copilot launch directory must match the selected execution directory")
        else:
            result.append(arg)
        index += 1
    literal_tail = command[index:]
    normalized = _normalized_setup_index(result)
    if normalized is not None:
        flag = "-ExecutionDirectory" if result[normalized].endswith(".ps1") else "--execution-directory"
        return [*result[:normalized + 1], flag, work_dir,
                *result[normalized + 1:], *native_suffix, *literal_tail]
    return [*result, f"-C={work_dir}", *native_suffix, *literal_tail]


def _pack_powershell_setup(command, setup_index: int, error_type):
    setup = list(command[:setup_index + 1])
    native = []
    index = setup_index + 1
    while index < len(command):
        arg = command[index]
        key = arg.casefold()
        if arg == "--":
            native.extend(command[index:])
            break
        if key == "-nativeargumentsjson":
            raise error_type("Launch templates cannot override the native argument envelope")
        if arg in _NATIVE_VALUE_OPTIONS:
            native.extend(command[index:index + 2])
            index += 2
        elif (not arg.startswith("--") and key in _SETUP_VALUE_OPTIONS) or key == "-executiondirectory":
            setup.extend(command[index:index + 2])
            index += 2
        elif key == "-recovery":
            setup.append(arg)
            index += 1
        else:
            native.append(arg)
            index += 1
    return [*setup, "-NativeArgumentsJson", json.dumps({"args": native})]


def quote_powershell_command(command, error_type):
    if not command or PureWindowsPath(command[0]).stem.casefold() not in {"pwsh", "powershell"}:
        return command
    setup = _normalized_setup_index(command)
    if setup is not None:
        return _pack_powershell_setup(command, setup, error_type)
    for index, arg in enumerate(command[1:], 1):
        if arg.casefold() == "-file":
            return command
        if arg.casefold() not in {"-c", "-command"}:
            continue
        target = index + 1
        if target >= len(command) or PureWindowsPath(command[target]).stem.casefold() != "copilot":
            raise error_type(
                "A PowerShell command-string launch must directly invoke Copilot; "
                "use a launcher script for a compound command"
            )
        return [*command[:target], powershell_invocation(command[target:])]
    return command


def apply_process_launcher(command, args, error_type):
    launcher = getattr(args, "copilot_process_launcher", None)
    if not launcher:
        return command
    if not os.path.isabs(launcher) or not os.path.isfile(launcher) or not os.access(launcher, os.X_OK):
        raise error_type("The requested Copilot process launcher is not executable")
    result = list(command)
    if result and result[0] == "copilot":
        result[0] = launcher
        return result
    normalized = _normalized_setup_index(result)
    if normalized is None:
        raise error_type(
            "A process-only Copilot launcher requires a direct copilot "
            "launch template or normalized setup"
        )
    if any(
        arg == "--copilot-path" or arg.startswith("--copilot-path=")
        or arg.lower() == "-copilotpath" or arg.lower().startswith("-copilotpath:")
        for arg in result
    ):
        raise error_type(
            "A process-only Copilot launcher cannot replace a configured copilot_path"
        )
    flag = "-CopilotPath" if result[normalized].endswith(".ps1") else "--copilot-path"
    return [*result[:normalized + 1], flag, launcher, *result[normalized + 1:]]


def write_launch_result(args, result) -> None:
    target = getattr(args, "launch_result_file", None)
    if not target:
        return
    launcher = getattr(args, "copilot_process_launcher", None)
    path = Path(target)
    if (
        not launcher or not path.is_absolute() or path.name != "result.json"
        or path.parent != Path(launcher).parent
        or type(result.get("created")) is not bool
        or type(result.get("resumed")) is not bool
        or result["created"] == result["resumed"]
    ):
        raise ValueError("invalid process-launch result destination or create-or-resume receipt")
    descriptor = {"version": 1, "created": result["created"], "resumed": result["resumed"]}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(descriptor, stream)
        stream.flush()
        os.fsync(stream.fileno())
