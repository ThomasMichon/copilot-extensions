"""Inert command operand roles shared by source qualification and validation."""

from __future__ import annotations

import re
from pathlib import Path

from .registrar import RegistrarError


def is_python_interpreter(executable: str) -> bool:
    if executable.startswith("repo:"):
        from .repository_resources import split_repository_resource

        _, executable = split_repository_resource(executable)
    return re.fullmatch(
        r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?(?:_d)?(?:\.exe)?",
        Path(executable).name, re.IGNORECASE,
    ) is not None


def script_operand_index(command: list[str], *, include_inline: bool = False) -> int | None:
    suffixes = {".py", ".sh", ".ps1"}
    if not is_python_interpreter(command[0]):
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
