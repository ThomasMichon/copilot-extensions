"""Prepared-comment wire binding; never source policy, allocation or native proof."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid

_MARKER_CANDIDATE = re.compile(r"<!--\s*agent-comment(?=\s|:)")
_FENCE_START = re.compile(r" {0,3}(`{3,}|~{3,})")


def _mask_inline_code(line: str, *, in_comment: bool = False) -> str:
    tokens = list(re.finditer(r"<!--|-->|`+", line))
    result = list(line)
    index = 0
    while index < len(tokens):
        opening = tokens[index]
        if opening[0] in ("<!--", "-->"):
            in_comment = opening[0] == "<!--"
            index += 1
            continue
        if in_comment:
            index += 1
            continue
        before = line[:opening.start()]
        if (len(before) - len(before.rstrip("\\"))) % 2:
            index += 1
            continue
        closing = next(
            (i for i in range(index + 1, len(tokens)) if tokens[i][0] == opening[0]),
            None,
        )
        if closing is None:
            index += 1
            continue
        result[opening.start():tokens[closing].end()] = " " * (tokens[closing].end() - opening.start())
        index = closing + 1
    return "".join(result)


def _outer_markers(body: str) -> list[str]:
    # Validate the wire independently of an installed producer, with its same literal grammar.
    prefix = "<!-- agent-comment:"
    fence: tuple[str, int] | None = None
    html_comment: list[str] | None = None
    markers: list[str] = []
    for line in body.splitlines():
        if fence is not None:
            closing = re.fullmatch(r" {0,3}([`~]+)\s*", line)
            if closing and set(closing[1]) == {fence[0]} and len(closing[1]) >= fence[1]:
                fence = None
            continue
        if html_comment is None:
            if re.match(r" {0,3}>", line):
                continue
            opening = _FENCE_START.match(line)
            if opening:
                if opening[1][0] == "`" and "`" in line[opening.end():]:
                    raise ValueError("invalid prepared backtick fence")
                fence = opening[1][0], len(opening[1])
                continue
        literal = _mask_inline_code(line, in_comment=html_comment is not None)
        position = 0
        for token in re.finditer(r"<!--|-->", literal):
            if html_comment is not None:
                html_comment.append(literal[position:token.start()])
            if token[0] == "<!--":
                if html_comment is not None:
                    raise ValueError("nested prepared HTML comment")
                html_comment = ["<!--"]
            elif html_comment is not None:
                content = "".join(html_comment)
                if _MARKER_CANDIDATE.match(content):
                    if "\n" in content or not line.startswith(prefix) or line != content + "-->":
                        raise ValueError("noncanonical prepared outer marker")
                    markers.append(line)
                html_comment = None
            position = token.end()
        if html_comment is not None:
            html_comment.extend((literal[position:], "\n"))
    if fence is not None or html_comment is not None:
        raise ValueError("unfinished prepared literal boundary")
    return markers


def _guid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("prepared comment requires canonical public GUIDs")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValueError("prepared comment requires canonical public GUIDs") from None
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122 or str(parsed) != value:
        raise ValueError("prepared comment requires canonical public GUIDs")
    return value


def validate(value: object, body: str) -> dict[str, object]:
    """Validate retained declared data, not who produced it or permission to send."""
    if (
        not isinstance(value, dict) or set(value) != {"identity", "body_id"}
        or not isinstance(body, str) or len(body.encode("utf-8")) > 64 * 1024
    ):
        raise ValueError("invalid prepared comment binding")
    identity = value["identity"]
    if not isinstance(identity, dict) or set(identity) != {"comment_id", "write_id", "source"}:
        raise ValueError("invalid prepared comment identity")
    comment, write = _guid(identity["comment_id"]), _guid(identity["write_id"])
    if comment == write:
        raise ValueError("prepared comment and write GUIDs must differ")
    source = identity["source"]
    if (
        not isinstance(source, dict) or set(source) != {"protocol", "privacy", "origin", "effects"}
        or source["protocol"] != "agent-worktrees.comment-origin/v1" or source["effects"] != "none"
        or source["privacy"] not in ("anonymous", "public", "internal")
    ):
        raise ValueError("invalid prepared source projection")
    fields = [f"c={comment}", f"w={write}"]
    origin = source["origin"]
    if source["privacy"] == "anonymous":
        if origin is not None:
            raise ValueError("anonymous prepared source must omit origin")
    else:
        expected = {"codename", "worktree"} if source["privacy"] == "internal" else {"codename"}
        if (
            not isinstance(origin, dict) or set(origin) != expected
            or not isinstance(origin["codename"], str) or not 1 <= len(origin["codename"]) <= 64
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", origin["codename"]) is None
        ):
            raise ValueError("invalid prepared source origin")
        fields.append(f"cn={origin['codename']}")
        if source["privacy"] == "internal":
            stamp = origin["worktree"]
            if not isinstance(stamp, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", stamp) is None:
                raise ValueError("invalid prepared internal source stamp")
            fields.append(f"wt={stamp}")
    marker = "<!-- agent-comment:v1 " + " ".join(fields) + " -->"
    if (
        len(marker.encode("utf-8")) > 512
        or hashlib.sha256(body.encode("utf-8")).hexdigest() != value["body_id"]
        or not body.endswith("\n\n" + marker)
        or not body[:-len(marker) - 2].strip()
        or _outer_markers(body) != [marker]
        or len(json.dumps(value, ensure_ascii=True).encode("utf-8")) > 2048
    ):
        raise ValueError("prepared identity and exact body differ")
    return copy.deepcopy(value)
