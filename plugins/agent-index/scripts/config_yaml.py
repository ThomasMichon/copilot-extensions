"""Dependency-free YAML tokens for the configuration bootstrap reader."""
from __future__ import annotations

from collections.abc import Callable
import json
import re


def tokenize(
    text: str, strip_comment: Callable[[str], str], error_type: type[ValueError],
) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    lines = text.splitlines()
    line_index = 0
    while line_index < len(lines):
        raw = lines[line_index]
        line_index += 1
        if "\t" in raw:
            raise error_type("tabs are not supported in YAML indentation")
        content = strip_comment(raw).rstrip()
        if not content.strip():
            continue
        stripped = content.lstrip(" ")
        if stripped.startswith(("%", "---", "...")):
            raise error_type("YAML directives and document markers are unsupported")
        indent = len(content) - len(stripped)
        block = re.fullmatch(r"(.*:\s+|-\s+)([|>])([-+]?)", stripped)
        if block:
            body: list[str] = []
            body_indent: int | None = None
            while line_index < len(lines):
                following = lines[line_index]
                following_indent = len(following) - len(following.lstrip(" "))
                if following.strip():
                    if following_indent <= indent:
                        break
                    if body_indent is None:
                        body_indent = following_indent
                    if following_indent < body_indent:
                        raise error_type("invalid block scalar indentation")
                body.append(following)
                line_index += 1
            body = [line[body_indent:] if line.strip() else "" for line in body]
            pieces: list[str] = []
            for index, line in enumerate(body):
                pieces.append(line)
                if index + 1 == len(body):
                    if line_index < len(lines) or text.endswith(("\n", "\r")):
                        pieces.append("\n")
                elif block[2] == "|" or line.startswith(" ") or body[index + 1].startswith(" "):
                    pieces.append("\n")
                elif not line:
                    pieces.append("\n")
                elif body[index + 1]:
                    pieces.append(" ")
            value = "".join(pieces)
            if block[3] == "-":
                value = value.rstrip("\n")
            elif block[3] != "+":
                value = value.rstrip("\n")
                if value:
                    value += "\n"
            stripped = block[1] + json.dumps(value)
        tokens.append((indent, stripped))
    return tokens
