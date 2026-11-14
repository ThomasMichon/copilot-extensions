"""Live reporter naming outside cached worktree rows and their revisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

import yaml

from . import config as cfg, record_reporting
from .worktrees_projection_index import ProjectionError, Selection

CAPABILITY = "worktrees-reporting-context-v1"
VERSION = 1
_NAMES = frozenset({"hostname", "machine", "execution_machine"})
_ROW_NAMES = frozenset({
    "machine", "platform", "machine_identity_version", "environment", "execution_machine",
})


def publication_row(row: dict) -> dict:
    """Drop naming projections, not original caller/claim allocation locators."""
    if not isinstance(row, dict):
        raise ProjectionError("current row must be an object")
    result = {key: value for key, value in row.items() if key not in _ROW_NAMES}
    caller = result.get("caller_state")
    if isinstance(caller, dict):
        caller = {key: value for key, value in caller.items() if key not in _ROW_NAMES}
        candidates = caller.get("candidates")
        if isinstance(candidates, list):
            caller["candidates"] = [
                {key: value for key, value in item.items() if key not in _ROW_NAMES}
                if isinstance(item, dict) else item
                for item in candidates
            ]
        result["caller_state"] = caller
    return result


def platform_selection(selection: Selection, platform: str) -> Selection:
    """Filter the reporting process before resolving any row alias."""
    return replace(
        selection, platform_filter=None,
        empty=selection.empty or (
            selection.platform_filter is not None and selection.platform_filter != platform
        ),
    )


def read_publication(
    read: Callable[..., dict], project: str, *, contract_version: int = 1,
    platform: str | None = None, **selection: Any,
) -> dict:
    """Keep OS wire compatibility outside persisted rows and naming lookups."""
    platform = cfg.detect_platform() if platform is None else platform
    return read(
        project, **asdict(platform_selection(Selection(**selection), platform)),
        contract_version=contract_version, wire_platform=platform,
    )


def validate(value: dict) -> None:
    """Validate reporting facts, which grant no record or transport authority."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or (
        value["version"] != VERSION
    ):
        raise ProjectionError("unsupported current-roster reporting context")
    if value.get("status") == "unavailable" and set(value) == {"version", "status"}:
        return
    if value.get("status") != "resolved" or set(value) != {
        "version", "status", "environment", *_NAMES,
    }:
        raise ProjectionError("invalid current-roster reporting context fields")
    if not isinstance(value["environment"], str) or value["environment"] not in {
        "windows", "wsl", "linux",
    }:
        raise ProjectionError("invalid current-roster reporting environment")
    for name in _NAMES:
        text = value[name]
        if (
            not isinstance(text, str) or not text.strip()
            or len(text.encode("utf-8")) > 256 or any(char in text for char in "\r\n\0")
        ):
            raise ProjectionError("invalid current-roster reporting name")


def current(project: str) -> dict:
    """Read current host/configuration facts without records, Git or a CLI."""
    try:
        context = record_reporting.current_context(project)
        value = {
            "version": VERSION, "status": "resolved",
            "hostname": context.hostname, "machine": context.machine,
            "environment": context.environment,
            "execution_machine": context.execution_machine,
        }
        validate(value)
        return value
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return {"version": VERSION, "status": "unavailable"}
