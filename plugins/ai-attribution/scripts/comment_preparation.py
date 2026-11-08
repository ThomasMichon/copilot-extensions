#!/usr/bin/env python3
"""Pure compact comment preparation; never allocation, approval or publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, TypedDict, cast

if TYPE_CHECKING:
    from attribution_profile import (
        Profile, ProfileError, Signature, _object, decorate, load_profile, resolve,
    )
elif __package__:
    from .attribution_profile import (
        Profile, ProfileError, Signature, _object, decorate, load_profile, resolve,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from attribution_profile import (
        Profile, ProfileError, Signature, _object, decorate, load_profile, resolve,
    )

PROTOCOL = "agent-forge-comment.preparation/v1"
SOURCE_PROTOCOL = "agent-worktrees.comment-origin/v1"
PREFIX = "<!-- agent-comment:"
_MARKER_CANDIDATE = re.compile(r"<!--\s*agent-comment(?=\s|:)")
_FENCE_START = re.compile(r" {0,3}(`{3,}|~{3,})")
MAX_BODY_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 192 * 1024


class Identity(TypedDict):
    comment_id: str
    write_id: str
    source: dict[str, object]


class Prepared(TypedDict):
    protocol: str
    identity: Identity
    body: str
    body_id: str
    effects: str


def _fields(value: object, names: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise ProfileError("invalid comment preparation fields")
    return cast(dict[str, object], value)


def _guid(value: object) -> str:
    if not isinstance(value, str):
        raise ProfileError("comment identities must be canonical version-4 GUIDs")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ProfileError("comment identities must be canonical version-4 GUIDs") from None
    if not parsed.int or parsed.version != 4 or parsed.variant != uuid.RFC_4122 or str(parsed) != value:
        raise ProfileError("comment identities must be canonical version-4 GUIDs")
    return value


def _source(value: object, visibility: str, allow_anonymous: bool) -> dict[str, object]:
    data = _fields(value, {"protocol", "privacy", "origin", "effects"})
    if data["protocol"] != SOURCE_PROTOCOL or data["effects"] != "none":
        raise ProfileError("unsupported executing comment-origin contract")
    privacy = data["privacy"]
    if privacy == "anonymous":
        if data["origin"] is not None or not allow_anonymous:
            raise ProfileError("required comment source identity is unavailable")
        return dict(data)
    if privacy not in ("public", "internal") or privacy == "internal" and visibility != "internal":
        raise ProfileError("comment source privacy does not permit this destination")
    origin = _fields(data["origin"], {"codename", "worktree"} if privacy == "internal" else {"codename"})
    name = origin["codename"]
    if (
        not isinstance(name, str) or not 1 <= len(name) <= 64
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
    ):
        raise ProfileError("invalid comment source codename")
    result: dict[str, str] = {"codename": name}
    if privacy == "internal":
        stamp = origin["worktree"]
        if not isinstance(stamp, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", stamp):
            raise ProfileError("invalid comment source stamp")
        result["worktree"] = stamp
    return {"protocol": SOURCE_PROTOCOL, "privacy": privacy, "origin": result, "effects": "none"}


def _marker(identity: Identity) -> str:
    fields = [f"c={identity['comment_id']}", f"w={identity['write_id']}"]
    origin = identity["source"]["origin"]
    if isinstance(origin, dict):
        fields.append(f"cn={origin['codename']}")
        if "worktree" in origin:
            fields.append(f"wt={origin['worktree']}")
    marker = PREFIX + "v1 " + " ".join(fields) + " -->"
    if len(marker.encode("utf-8")) > 512:
        raise ProfileError("comment metadata exceeds its byte bound")
    return marker


def _mask_inline_code(line: str, *, in_comment: bool = False) -> str:
    runs = list(re.finditer(r"<!--|-->|`+", line))
    result = list(line)
    index = 0
    while index < len(runs):
        opening = runs[index]
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
            (i for i in range(index + 1, len(runs)) if runs[i][0] == opening[0]),
            None,
        )
        if closing is None:
            index += 1
            continue
        result[opening.start():runs[closing].end()] = " " * (runs[closing].end() - opening.start())
        index = closing + 1
    return "".join(result)


def _outer_markers(draft: str) -> list[str]:
    """Conservative literal boundaries, not a provider renderer or trust parser."""
    fence: tuple[str, int] | None = None
    html_comment: list[str] | None = None
    markers: list[str] = []
    for line in draft.splitlines():
        if fence is not None:
            close = re.fullmatch(r" {0,3}([`~]+)\s*", line)
            if close and set(close[1]) == {fence[0]} and len(close[1]) >= fence[1]:
                fence = None
            continue
        if html_comment is None:
            if re.match(r" {0,3}>", line):
                continue
            opening = _FENCE_START.match(line)
            if opening:
                if opening[1][0] == "`" and "`" in line[opening.end():]:
                    raise ProfileError("invalid backtick fence info string")
                fence = opening[1][0], len(opening[1])
                continue
        literal = _mask_inline_code(line, in_comment=html_comment is not None)
        position = 0
        for token in re.finditer(r"<!--|-->", literal):
            if html_comment is not None:
                html_comment.append(literal[position:token.start()])
            if token[0] == "<!--":
                if html_comment is not None:
                    raise ProfileError("ambiguous nested HTML comment")
                html_comment = ["<!--"]
            elif html_comment is not None:
                content = "".join(html_comment)
                if _MARKER_CANDIDATE.match(content):
                    if "\n" in content or not line.startswith(PREFIX) or line != content + "-->":
                        raise ProfileError("noncanonical outer comment metadata")
                    markers.append(line)
                html_comment = None
            position = token.end()
        if html_comment is not None:
            html_comment.extend((literal[position:], "\n"))
    if fence is not None or html_comment is not None:
        raise ProfileError("unfinished or ambiguous literal comment boundary")
    return markers


def _visible_body(draft: str, signature: Signature) -> str:
    if signature["required"]:
        boundaries: list[int] = []
        for current, variants, at_boundary in (
            (signature["header"], signature["header_variants"], draft.startswith),
            (signature["footer"], signature["footer_variants"], draft.endswith),
        ):
            owned = next(
                (text for text in sorted(set(variants), key=len, reverse=True) if text and at_boundary(text)),
                "",
            ) if current and not at_boundary(current) else current
            boundaries.append(len(owned) if owned and at_boundary(owned) else 0)
        if sum(boundaries) > len(draft):
            raise ProfileError("ambiguous overlapping signature boundaries")
    visible = decorate(draft, signature)
    header = len(signature["header"]) if signature["header"] and visible.startswith(signature["header"]) else 0
    footer = len(signature["footer"]) if signature["footer"] and visible.endswith(signature["footer"]) else 0
    if header + footer > len(visible):
        raise ProfileError("ambiguous overlapping signature boundaries")
    core = visible[header:len(visible) - footer]
    if not core.strip():
        raise ProfileError("metadata cannot create an empty comment")
    if not signature["required"]:
        return visible
    prefix, suffix = visible[:header], visible[len(visible) - footer:]
    if prefix and (_FENCE_START.match(core) or re.match(r" {0,3}>", core)) and not prefix.endswith(("\n", "\r")):
        prefix += "\n\n"
    if (
        suffix and not core.endswith(("\n", "\r")) and not suffix.startswith(("\n", "\r"))
        and re.fullmatch(r" {0,3}(`{3,}|~{3,})[ \t]*", core.splitlines()[-1])
    ):
        core += "\n\n"
    return prefix + core + suffix


def _previous(value: object) -> Prepared:
    data = _fields(value, {"protocol", "identity", "body", "body_id", "effects"})
    if data["protocol"] != PROTOCOL or data["effects"] != "none":
        raise ProfileError("unsupported previous comment preparation")
    raw = _fields(data["identity"], {"comment_id", "write_id", "source"})
    identity: Identity = {
        "comment_id": _guid(raw["comment_id"]), "write_id": _guid(raw["write_id"]),
        "source": _source(raw["source"], "internal", True),
    }
    body = data["body"]
    if (
        not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES
        or hashlib.sha256(body.encode("utf-8")).hexdigest() != data["body_id"]
        or not body.endswith("\n\n" + _marker(identity))
        or _outer_markers(body) != [_marker(identity)]
        or identity["comment_id"] == identity["write_id"]
    ):
        raise ProfileError("previous prepared content is inconsistent")
    return cast(Prepared, {**data, "identity": identity})


def prepare(
    draft: str, profile: Profile, context: Mapping[str, object], source: object, *,
    comment_id: object, write_id: object, previous: object = None,
    allow_anonymous: bool = False, max_body_bytes: int = MAX_BODY_BYTES,
) -> Prepared:
    """Caller owns policy facts, IDs, retained intent and all actual effects."""
    if (
        not isinstance(draft, str) or len(draft.encode("utf-8")) > MAX_BODY_BYTES
        or type(allow_anonymous) is not bool or type(max_body_bytes) is not int
        or not 1 <= max_body_bytes <= MAX_BODY_BYTES
    ):
        raise ProfileError("invalid comment text or byte bound")
    signature = resolve(profile, dict(context))
    if context["domain"] != "forge" or context["activity"] not in ("comment", "reply", "review", "inline-comment"):
        raise ProfileError("comment preparation requires a forge comment context")
    visibility = cast(str, context["visibility"])
    identity: Identity = {
        "comment_id": _guid(comment_id), "write_id": _guid(write_id),
        "source": _source(source, visibility, allow_anonymous),
    }
    if identity["comment_id"] == identity["write_id"]:
        raise ProfileError("comment and write identities must be distinct")
    old = _previous(previous) if previous is not None else None
    if old is not None and old["identity"]["comment_id"] != identity["comment_id"]:
        raise ProfileError("an edit cannot change logical comment identity")
    marker = _marker(identity)
    markers = _outer_markers(draft)
    if markers:
        allowed = {marker}
        if old is not None:
            allowed.add(_marker(old["identity"]))
        if len(markers) != 1 or markers[0] not in allowed or not draft.endswith("\n\n" + markers[0]):
            raise ProfileError("foreign, duplicate or unowned comment metadata")
        draft = draft[:-len(markers[0]) - 2]
    if not draft.strip():
        raise ProfileError("metadata cannot create an empty comment")
    visible = _visible_body(draft, signature)
    if old is not None and visibility != "internal":
        prior_origin = old["identity"]["source"]["origin"]
        if isinstance(prior_origin, dict) and "worktree" in prior_origin and prior_origin["worktree"] in visible:
            raise ProfileError("private source stamp remains in a public draft")
    body = visible + "\n\n" + marker
    if len(body.encode("utf-8")) > max_body_bytes or _outer_markers(body) != [marker]:
        raise ProfileError("prepared comment exceeds its bound or literal boundary")
    result: Prepared = {
        "protocol": PROTOCOL, "identity": identity, "body": body,
        "body_id": hashlib.sha256(body.encode("utf-8")).hexdigest(), "effects": "none",
    }
    if old is not None and old["identity"]["write_id"] == identity["write_id"] and result != old:
        raise ProfileError("a retry cannot change prepared write content")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo", type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ProfileError("comment request exceeds byte bound")
        request = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(request, dict) or set(request) - {
            "draft", "context", "source", "comment_id", "write_id", "previous",
            "allow_anonymous", "max_body_bytes",
        }:
            raise ProfileError("invalid comment request")
        profile, errors = load_profile(Path.home(), args.repo)
        if errors:
            raise ProfileError("comment signature profile unavailable")
        result = prepare(profile=profile, **request)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, UnicodeError):
        print(json.dumps({"error": "comment_preparation_unavailable", "effects": "none"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
