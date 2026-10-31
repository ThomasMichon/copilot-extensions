"""Negotiated full-snapshot pages over the existing external discovery verb."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SnapshotPage
from agent_index.sources.provider_delta import DeltaDeclaration, parse_declaration, validate_scope_echo


def validate_cli_tokens(*values: str | None) -> None:
    """Reject opaque values the subprocess argv boundary cannot represent."""
    if any(value is not None and "\0" in value for value in values):
        raise ValueError("Provider tokens, cursors and checkpoints must not contain NUL characters")


def parse_snapshot(
    data: object,
    declaration: DeltaDeclaration,
    parse_entries: Callable[[Any, str], list[FileEntry]],
) -> SnapshotPage:
    """Validate the executing scope and complete page envelope before entries."""
    fields = {"version", "snapshot", "upserts", "commit", "cursor", "source_delta"}
    if not isinstance(data, dict) or not fields.issubset(data):
        raise ValueError("Snapshot page requires version/snapshot/upserts/commit/cursor/source_delta")
    validate_scope_echo(data, declaration)
    echo = parse_declaration(data, declaration.family)
    if not declaration.snapshot_available or echo is None or not echo.snapshot_available:
        raise ValueError("Snapshot page requires negotiated snapshot v1 scope echo")
    if not isinstance(data["upserts"], list) or len(data["upserts"]) > 256:
        raise ValueError("Snapshot page must contain at most 256 items")
    page = SnapshotPage(
        snapshot=data["snapshot"],
        upserts=parse_entries({"entries": data["upserts"]}, "content-discover"),
        commit=data["commit"], cursor=data["cursor"], version=data["version"],
    )
    page.validate(declaration.family)
    validate_cli_tokens(page.snapshot, page.cursor, page.commit)
    return page
