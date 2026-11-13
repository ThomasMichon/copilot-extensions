"""Dependency command grammar and native launch projection."""

from __future__ import annotations

import os
from pathlib import Path


def argument_pairs(arguments, value_options, *, error_type):
    iterator = iter(arguments)
    positional = False
    for argument in iterator:
        if positional or not argument.startswith("-"):
            yield "", argument
        elif argument == "--":
            positional = True
        else:
            option, separator, value = argument.partition("=")
            if (
                not argument.startswith("--")
                and len(argument) > 2
                and argument[:2] in value_options
            ):
                option, separator, value = argument[:2], "=", argument[2:]
            if option in value_options:
                if not separator:
                    value = next(iterator, "")
                if not value or value.startswith("--"):
                    raise error_type("dependency installation option has no value")
                yield option, value
            else:
                yield option, value


def balanced(text, start, opening, closing, *, error_type):
    depth = 0
    quoted = escaped = False
    for offset in range(start, len(text)):
        char = text[offset]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : offset], offset + 1
    raise error_type("UV settings projection is incomplete")


def uv_command(
    uv,
    arguments,
    *,
    cwd,
    directory,
    operation,
    windows,
    argument_pairs,
    value_options,
):
    if not windows or len(os.path.abspath(cwd or Path.cwd())) < 260:
        return [uv, "pip", operation, *arguments], cwd
    retained = []
    directory_supplied = False
    for option, value in argument_pairs(arguments):
        if option == "--directory":
            directory_supplied = True
            value = str(directory)
        if option and value:
            retained.extend(
                [option, value] if option in value_options else [option + "=" + value]
            )
        else:
            retained.append(option or value)
    if not directory_supplied:
        retained[:0] = ["--directory", str(directory)]
    return [uv, "pip", operation, *retained], Path(directory.anchor)
