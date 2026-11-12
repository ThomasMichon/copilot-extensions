"""Common native tool shapes; path extraction does not interpret edited content."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable

MAX_CALLS = 128
PATH_KEYS = (
    "path", "file_path", "filePath", "filename", "fileName",
    "target_file", "targetFile",
)
_PATCH_TARGET = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)\r?$",
    re.MULTILINE,
)


def tool_events(payload: dict, *, strict: bool = False) -> list[dict]:
    if "toolCalls" not in payload:
        return [payload]
    calls = payload["toolCalls"]
    if not isinstance(calls, list) or len(calls) > MAX_CALLS:
        raise ValueError("unsupported tool batch")
    if strict and (not calls or any(
        not isinstance(call, dict)
        or not isinstance(call.get("name"), str)
        or not call["name"]
        or set(call) - {"name", "args", "id"}
        for call in calls
    )):
        raise ValueError("incomplete tool batch")
    events = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            continue
        events.append({
            **{key: value for key, value in payload.items() if key != "toolCalls"},
            "toolName": call["name"],
            "toolArgs": call.get("args", {}),
            "toolCallId": call.get("id"),
            "cwd": payload.get("cwd", payload.get("workingDirectory", "")),
        })
    return events


def write_targets(tool: str, args: object) -> list[str]:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        return []
    targets = []

    def add(value):
        if isinstance(value, str) and value and value not in targets:
            if len(targets) >= MAX_CALLS:
                raise ValueError("too many write targets")
            targets.append(value)

    for key in PATH_KEYS:
        add(args.get(key))
    for key in ("edits", "files", "changes"):
        entries = args.get(key)
        if isinstance(entries, list):
            if len(entries) > MAX_CALLS:
                raise ValueError("too many structured write entries")
            for entry in entries:
                if isinstance(entry, dict):
                    for path_key in PATH_KEYS:
                        add(entry.get(path_key))
    if tool == "apply_patch":
        for key in ("input", "patch", "command"):
            patch = args.get(key)
            if isinstance(patch, str):
                for match in _PATCH_TARGET.finditer(patch):
                    add(match[1].removesuffix("\r"))
    return targets


def native_mutation_targets(payload: dict, *, write_tools: frozenset[str], cwd: str) -> list[str] | None:
    """Collect a complete native event's targets before invalidating anything."""
    events = tool_events(payload, strict=True)
    if any(
        not isinstance(event.get("toolName") or event.get("tool_name"), str)
        or (event.get("toolName") or event.get("tool_name")).lower()
        not in write_tools | {"view", "rg", "glob"}
        for event in events
    ):
        raise ValueError("unsupported native tool")
    targets = []
    for event in events:
        tool = (event.get("toolName") or event.get("tool_name")).lower()
        if tool not in write_tools:
            continue
        args = event.get("toolArgs") or event.get("tool_input") or {}
        try:
            paths = write_targets(tool, args)
        except ValueError:
            return None
        for path in paths or [cwd]:
            path = path if os.path.isabs(path) else os.path.join(cwd, path)
            if path not in targets:
                if len(targets) >= MAX_CALLS:
                    return None
                targets.append(path)
    return targets


def mixed_mutation_targets(
    payload: dict, *, allowed_tools: frozenset[str], classify: Callable[[dict], list[str] | None],
) -> list[str] | None:
    """Validate a complete mixed batch before invoking its original classifiers."""
    events = tool_events(payload, strict=True)
    if any(event["toolName"].lower() not in allowed_tools for event in events):
        raise ValueError("unsupported mixed tool")
    targets = []
    for event in events:
        selected = classify(event)
        if selected is None:
            return None
        for target in selected:
            if target not in targets:
                if len(targets) >= MAX_CALLS:
                    return None
                targets.append(target)
    return targets
