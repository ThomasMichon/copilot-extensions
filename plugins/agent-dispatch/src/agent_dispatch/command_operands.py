"""Inert command operand roles shared by source qualification and validation."""

from __future__ import annotations

import re
from pathlib import Path

from .registrar import RegistrarError


def _executable_name(executable: str) -> str:
    if executable.startswith("repo:"):
        from .repository_resources import split_repository_resource

        _, executable = split_repository_resource(executable)
    return Path(executable).name.casefold().removesuffix(".exe")


def is_python_interpreter(executable: str) -> bool:
    return re.fullmatch(
        r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?(?:_d)?(?:\.exe)?",
        _executable_name(executable), re.IGNORECASE,
    ) is not None


def _posix_qualification_operand(command: list[str], *, include_inline: bool) -> int | None:
    index = 1
    while index < len(command):
        value = command[index]
        if value in {"-", "-s"}:
            return None
        if value == "--":
            return index + 1 if index + 1 < len(command) and command[index + 1] != "-" else None
        if re.fullmatch(r"-[a-zA-Z]*c", value):
            return index + 1 if include_inline and index + 1 < len(command) else None
        if value in {"-o", "+o"}:
            index += 1
            if index >= len(command):
                raise RegistrarError("missing shell option value")
        elif re.fullmatch(r"[-+][abefhkmnptuvxBCEHPTilr]+", value) or value in {
            "--norc", "--noprofile", "--posix", "--login",
        }:
            pass
        elif value.startswith(("-", "+")):
            raise RegistrarError("shell option roles cannot yet be qualified safely")
        else:
            return index
        index += 1
    return None


def script_operand_index(
    command: list[str], *, include_inline: bool = False, strict: bool = True,
) -> int | None:
    suffixes = {".py", ".sh", ".ps1"}
    if not is_python_interpreter(command[0]):
        if _executable_name(command[0]) in {"pwsh", "powershell"}:
            for index, value in enumerate(command[1:], start=1):
                if value.casefold() in {"-command", "-c", "-commandwithargs", "-encodedcommand", "-ec"}:
                    break
                if value.casefold() in {"-file", "-f"}:
                    if strict:
                        raise RegistrarError("script dependencies after non-Python options cannot yet be validated safely")
                    return index + 1 if index + 1 < len(command) else None
        if _executable_name(command[0]) in {"sh", "bash", "dash", "ash", "zsh", "ksh", "fish"}:
            if not strict and _executable_name(command[0]) != "fish":
                return _posix_qualification_operand(command, include_inline=include_inline)
            if len(command) > 1 and command[1] != "-" and not command[1].startswith("-"):
                return 1
            if len(command) > 2 and command[1] == "-c":
                return 2 if include_inline else None
        if len(command) > 1 and Path(command[1]).suffix.lower() in suffixes:
            return 1
        if len(command) > 1 and command[1].startswith("-") and any(
            Path(value).suffix.lower() in suffixes for value in command[2:]
        ):
            raise RegistrarError("script dependencies after non-Python options cannot yet be validated safely")
        return None
    index = 1
    while index < len(command):
        value = command[index]
        if value == "-":
            return None
        if value.startswith(("-c", "-m")):
            # Inline/module operands select code, but are not script files.
            return (
                index + 1 if value in {"-c", "-m"} and index + 1 < len(command) else index
            ) if include_inline else None
        if value == "--":
            return index + 1 if index + 1 < len(command) and command[index + 1] != "-" else None
        if value in {"-W", "-X", "--check-hash-based-pycs"}:
            index += 1
            if index >= len(command):
                raise RegistrarError("missing Python interpreter option value")
        elif value.startswith(("-W", "-X", "--check-hash-based-pycs=")):
            pass
        elif value in {"--help", "--help-env", "--help-xoptions", "--help-all", "--version"}:
            return None
        elif value.startswith("-"):
            if not re.fullmatch(r"-[bBdEhiIOPqRsSuvVx]+", value):
                raise RegistrarError("Python interpreter options cannot yet be validated safely")
            if "h" in value or "V" in value:
                return None
        else:
            return index
        index += 1
    return None


def script_operand(command: list[str]) -> str | None:
    index = script_operand_index(command)
    return command[index] if index is not None else None


def execution_operand_indices(command: list[str]) -> set[int]:
    """Include shell program operands while keeping script arguments as data."""
    name = _executable_name(command[0])
    if name in {"sh", "bash", "dash", "ash", "zsh", "ksh", "fish"}:
        if len(command) > 1 and command[1] in {"-", "-s"}:
            return {0}
        if len(command) > 2 and command[1] == "--":
            return {0, 2}
        if len(command) > 2 and re.fullmatch(r"-[a-zA-Z]*c", command[1]):
            return {0, 2}
        if len(command) > 1 and not command[1].startswith("-"):
            return {0, 1}
    elif name in {"pwsh", "powershell", "cmd"}:
        for index, value in enumerate(command[1:], start=1):
            mode = value.casefold()
            if mode in {"-file", "-f"} and name != "cmd" and index + 1 < len(command):
                return {0, index + 1}
            if mode in {"-command", "-c", "-commandwithargs", "-encodedcommand", "-ec", "/c", "/k"}:
                return {0, *range(index + 1, len(command))}
    if name in {"sh", "bash", "dash", "ash", "zsh", "ksh", "fish", "pwsh", "powershell", "cmd"} and any(
        "{change_ref}" in value for value in command[1:]
    ):
        raise RegistrarError("dynamic shell execution operands cannot be validated safely")
    operand = script_operand_index(command, include_inline=True)
    return {0, operand} if operand is not None else {0}
