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
        block = re.fullmatch(r"(.*:\s+|-\s+)([|>])([1-9][-+]?|[-+][1-9]?|)", stripped)
        if block:
            prefix = re.match(r"- +", stripped)
            boundary = indent + (prefix.end() if prefix and block[1].strip() != "-" else 0)
            body: list[str] = []
            indicator = next((int(char) for char in block[3] if char.isdigit()), 0)
            body_indent: int | None = boundary + indicator if indicator else None
            while line_index < len(lines):
                following = lines[line_index]
                following_indent = len(following) - len(following.lstrip(" "))
                if following.lstrip(" "):
                    if following_indent <= boundary:
                        break
                    if body_indent is None:
                        body_indent = following_indent
                    if following_indent < body_indent:
                        if following.lstrip(" ").startswith("#"):
                            break
                        raise error_type("invalid block scalar indentation")
                body.append(following)
                line_index += 1
            if body_indent is None:
                body_indent = max((len(line) for line in body), default=0)
            body = [line[body_indent:] for line in body]
            pieces: list[str] = []
            for index, line in enumerate(body):
                pieces.append(line)
                if index + 1 == len(body):
                    if line_index < len(lines) or text.endswith(("\n", "\r")):
                        pieces.append("\n")
                elif block[2] == "|" or line.startswith((" ", "\t")) or body[index + 1].startswith((" ", "\t")):
                    pieces.append("\n")
                elif not line:
                    pieces.append("\n")
                elif body[index + 1]:
                    pieces.append(" ")
                else:
                    following_text = next((body[i] for i in range(index + 2, len(body)) if body[i]), "")
                    if not following_text or following_text.startswith((" ", "\t")):
                        pieces.append("\n")
            value = "".join(pieces)
            if "-" in block[3]:
                value = value.rstrip("\n")
            elif "+" not in block[3]:
                ended = value.endswith("\n")
                value = value.rstrip("\n")
                if value and ended:
                    value += "\n"
            stripped = block[1] + json.dumps(value)
        tokens.append((indent, stripped))
    return tokens
