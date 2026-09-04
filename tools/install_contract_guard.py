"""Lexical PowerShell checks used by the repository install-contract guard."""

from __future__ import annotations

import re

PERSISTENT_ENV_START = (
    "# === install-contract:test-persistent-environment "
    "-- keep byte-identical across installers ==="
)
PERSISTENT_ENV_END = "# === end install-contract:test-persistent-environment ==="

_ENV_CALL = re.compile(
    r"\[(?:System\.)?Environment\]\s*::\s*"
    r"(?P<method>Get|Set)EnvironmentVariable\s*\(",
    re.IGNORECASE,
)
_PROCESS_TARGET = re.compile(
    r"^\s*(?:['\"]Process['\"]|"
    r"\[(?:System\.)?EnvironmentVariableTarget\]\s*::\s*Process)\s*$",
    re.IGNORECASE,
)
_REGISTRY_ENVIRONMENT_PATH = re.compile(
    r"(?:"
    r"(?:HKCU|Registry::HKEY_CURRENT_USER|HKEY_CURRENT_USER)\s*:"
    r"?[\\/]+Environment\b"
    r"|"
    r"(?:HKLM|Registry::HKEY_LOCAL_MACHINE|HKEY_LOCAL_MACHINE)\s*:"
    r"?[\\/]+SYSTEM[\\/]+CurrentControlSet[\\/]+Control[\\/]+"
    r"Session Manager[\\/]+Environment\b"
    r")",
    re.IGNORECASE,
)
_REGISTRY_API_ENVIRONMENT = re.compile(
    r"\[(?:Microsoft\.)?Win32\.Registry\]\s*::\s*"
    r"(?P<hive>CurrentUser|LocalMachine)"
    r"(?:(?!\n\s*\n).){0,500}?"
    r"(?:Environment|Session Manager[\\/]+Environment)",
    re.IGNORECASE | re.DOTALL,
)


def _without_canonical_adapter(text: str) -> str:
    start = text.find(PERSISTENT_ENV_START)
    if start < 0:
        return text
    end = text.find(PERSISTENT_ENV_END, start)
    if end < 0:
        return text
    end += len(PERSISTENT_ENV_END)
    return text[:start] + ("\n" * text[start:end].count("\n")) + text[end:]


def _without_comments(text: str, *, mask_strings: bool = False) -> str:
    result = list(text)
    index = 0
    quote: str | None = None
    block_comment = False
    while index < len(text):
        if block_comment:
            if text.startswith("#>", index):
                result[index : index + 2] = "  "
                block_comment = False
                index += 2
            else:
                if text[index] != "\n":
                    result[index] = " "
                index += 1
            continue
        if quote:
            if quote == "'" and text[index] == "'" and text[index : index + 2] == "''":
                if mask_strings:
                    result[index : index + 2] = "  "
                index += 2
                continue
            if text[index] == "`" and quote == '"':
                if mask_strings:
                    result[index : index + 2] = "  "
                index += 2
                continue
            if text[index] == quote:
                quote = None
            if mask_strings and text[index] != "\n":
                result[index] = " "
            index += 1
            continue
        if text.startswith("<#", index):
            result[index : index + 2] = "  "
            block_comment = True
            index += 2
            continue
        if text[index] == "#":
            while index < len(text) and text[index] != "\n":
                result[index] = " "
                index += 1
            continue
        if text[index] in {"'", '"'}:
            quote = text[index]
            if mask_strings:
                result[index] = " "
        index += 1
    return "".join(result)


def _call_arguments(text: str, open_paren: int) -> tuple[str | None, int]:
    depth = 1
    index = open_paren + 1
    quote: str | None = None
    block_comment = False
    while index < len(text):
        if block_comment:
            if text.startswith("#>", index):
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if quote == "'" and text[index : index + 2] == "''":
                index += 2
                continue
            if quote == '"' and text[index] == "`":
                index += 2
                continue
            if text[index] == quote:
                quote = None
            index += 1
            continue
        if text.startswith("<#", index):
            block_comment = True
            index += 2
            continue
        if text[index] == "#":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text[index] in {"'", '"'}:
            quote = text[index]
        elif text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : index], index + 1
        index += 1
    return None, len(text)


def _split_arguments(arguments: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if quote:
            if quote == "'" and arguments[index : index + 2] == "''":
                index += 2
                continue
            if quote == '"' and char == "`":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in closing:
            opener = closing[char]
            depths[opener] = max(0, depths[opener] - 1)
        elif char == "," and not any(depths.values()):
            parts.append(arguments[start:index].strip())
            start = index + 1
        index += 1
    parts.append(arguments[start:].strip())
    return parts


def persistent_environment_violations(text: str) -> list[str]:
    """Return direct persistent Windows environment access outside the adapter."""
    source = _without_comments(_without_canonical_adapter(text))
    call_scan = _without_comments(
        _without_canonical_adapter(text),
        mask_strings=True,
    )
    violations: list[str] = []
    for match in _ENV_CALL.finditer(call_scan):
        arguments, _ = _call_arguments(source, match.end() - 1)
        line = source.count("\n", 0, match.start()) + 1
        if arguments is None:
            violations.append(f"line {line}: unterminated .NET environment call")
            continue
        parts = _split_arguments(arguments)
        required_process_arity = 1 if match.group("method").lower() == "get" else 2
        if len(parts) <= required_process_arity:
            continue
        target = parts[-1]
        if not _PROCESS_TARGET.fullmatch(target):
            violations.append(
                f"line {line}: direct .NET environment target {target!r}"
            )

    for match in _REGISTRY_ENVIRONMENT_PATH.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        violations.append(
            f"line {line}: direct persistent environment registry path"
        )
    for match in _REGISTRY_API_ENVIRONMENT.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        violations.append(
            f"line {line}: direct {match.group('hive')} environment registry API"
        )
    return violations
