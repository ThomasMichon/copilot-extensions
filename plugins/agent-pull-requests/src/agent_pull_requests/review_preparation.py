"""Prepared-comment wire binding; never source policy, allocation or native proof."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid


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
        or len(json.dumps(value, ensure_ascii=True).encode("utf-8")) > 2048
    ):
        raise ValueError("prepared identity and exact body differ")
    return copy.deepcopy(value)
