"""Live reporter naming outside cached worktree rows and their revisions."""

from __future__ import annotations

import yaml

from . import record_reporting
from .worktrees_projection_index import ProjectionError

CAPABILITY = "worktrees-reporting-context-v1"
VERSION = 1
_NAMES = frozenset({"hostname", "machine", "execution_machine"})


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
