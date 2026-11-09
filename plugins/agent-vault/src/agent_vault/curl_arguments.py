"""A deliberately closed curl argument surface for authenticated requests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .http_bindings import BindingError, destination

_VALUES = {
    "-X": "--request",
    "--request": "--request",
    "-H": "--header",
    "--header": "--header",
    "-d": "--data",
    "--data": "--data",
    "--data-raw": "--data-raw",
    "--data-binary": "--data-binary",
    "-m": "--max-time",
    "--max-time": "--max-time",
    "--connect-timeout": "--connect-timeout",
    "--url": "--url",
    "-w": "--write-out",
    "--write-out": "--write-out",
}
_FLAGS = {
    "-s": "",
    "--silent": "",
    "-S": "",
    "--show-error": "",
    "-f": "fail",
    "--fail": "fail",
    "--fail-with-body": "",
    "-I": "--head",
    "--head": "--head",
    "-i": "--include",
    "--include": "--include",
}
_AUTH_HEADERS = {
    "authorization",
    "proxy-authorization",
    "host",
    "cookie",
    "x-api-key",
    "x-auth-token",
    "private-token",
    "x-access-token",
}


@dataclass(frozen=True)
class CurlArguments:
    url: str
    argv: list[str]
    discard_errors: bool = False
    write_out: str = ""


def _header(value: str) -> None:
    name, separator, _ = value.partition(":")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z0-9-]+", name)
        or name.lower() in _AUTH_HEADERS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BindingError("Caller authentication, Host and file-based headers are not allowed")


def _seconds(value: str) -> None:
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0
    if not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise BindingError("Request timeouts must be greater than zero and at most 60 seconds")


def parse_arguments(arguments: list[str]) -> CurlArguments:
    if not arguments or arguments[0] != "--":
        raise BindingError("Use agent-vault curl -- <supported curl arguments and URL>")
    values = list(arguments[1:])
    forwarded: list[str] = []
    urls: list[str] = []
    discard = False
    write_out = ""
    index = 0
    while index < len(values):
        word = values[index]
        index += 1
        if word.lower().startswith(("http://", "https://")):
            urls.append(word)
            continue
        if word in _FLAGS or (
            word.startswith("-")
            and not word.startswith("--")
            and len(word) > 2
            and set(word[1:]) <= set("sSfIi")
        ):
            flags = [word] if word in _FLAGS else [f"-{letter}" for letter in word[1:]]
            for flag in flags:
                operation = _FLAGS[flag]
                if operation == "fail":
                    discard = True
                elif operation:
                    forwarded.append(operation)
            continue
        option, equals, value = word.partition("=") if word.startswith("--") else (word, "", "")
        if word[:2] in {"-X", "-H", "-d", "-m", "-w"} and len(word) > 2:
            option, equals, value = word[:2], "=", word[2:]
        if option not in _VALUES:
            raise BindingError(
                "Unsupported curl option; config, redirects, tracing and routing are forbidden"
            )
        if not equals:
            if index >= len(values):
                raise BindingError("Curl option is missing its value")
            value = values[index]
            index += 1
        operation = _VALUES[option]
        if operation == "--url":
            urls.append(value)
        elif operation == "--write-out":
            if "%" in value.replace("%{http_code}", ""):
                raise BindingError("Only %{http_code} is supported in --write-out")
            write_out = value
        else:
            if operation == "--header":
                _header(value)
            elif operation == "--request" and value.upper() not in {
                "GET",
                "HEAD",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "OPTIONS",
            }:
                raise BindingError("Unsupported HTTP request method")
            elif operation in {"--max-time", "--connect-timeout"}:
                _seconds(value)
            elif operation in {"--data", "--data-binary"} and value.startswith("@"):
                raise BindingError(
                    "File or stdin request data is not supported; use inline --data-raw"
                )
            if "\0" in value:
                raise BindingError("Curl argument contains NUL")
            forwarded.extend((operation, value))
    if len(urls) != 1:
        raise BindingError("Exactly one target URL is required")
    destination(urls[0])
    return CurlArguments(urls[0], forwarded, discard, write_out)
