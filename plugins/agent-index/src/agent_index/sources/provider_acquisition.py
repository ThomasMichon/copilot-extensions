"""Negotiated ordinary acquisition pages over the external changed-discovery verb."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_index.sources.base import FileEntry
from agent_index.sources.delta import AcquisitionPage
from agent_index.sources.provider_delta import DeltaDeclaration, parse_declaration, parse_delta
from agent_index.sources.provider_snapshot import validate_cli_tokens


def parse_acquisition_page(
    data: object,
    declaration: DeltaDeclaration,
    parse_entries: Callable[[Any, str], list[FileEntry]],
) -> AcquisitionPage:
    """Require bounded complete units and executing scope/capability before entries."""
    if not isinstance(data, dict) or not {"acquisition_page_version", "cursor"}.issubset(data):
        raise ValueError("Acquisition page requires acquisition_page_version/cursor")
    version = data["acquisition_page_version"]
    if type(version) is not int or version != 1:
        raise ValueError("Unsupported connector acquisition-page version")
    echo = parse_declaration(data, declaration.family)
    if (
        not declaration.acquisition_page_available
        or echo is None or not echo.acquisition_page_available
    ):
        raise ValueError("Acquisition page requires negotiated acquisition-page v1 scope echo")
    upserts, deletions = data.get("upserts"), data.get("deletions")
    if (
        not isinstance(upserts, list) or not isinstance(deletions, list)
        or len(upserts) + len(deletions) > 256
    ):
        raise ValueError("Acquisition page must contain at most 256 upserts and deletions combined")
    page = AcquisitionPage(
        delta=parse_delta(data, declaration, parse_entries),
        cursor=data["cursor"], version=version,
    )
    page.validate(declaration.family)
    validate_cli_tokens(page.cursor, page.delta.commit)
    return page
