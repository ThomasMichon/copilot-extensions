"""Read-only selection of one source-aware serving peer for CLI operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from typing import TYPE_CHECKING, Any, Protocol

from .cache_warm_selection import FIELD, VERSION, WarmSelection, acknowledged
from .command_transport import CommandRoute, Reply
from .invocation import CACHE_TARGET_CAPABILITY, use_configuration_root

if TYPE_CHECKING:
    from .config import ResolvedVault


class ServiceSender(Protocol):
    def __call__(
        self, request: dict[str, Any], timeout: float | None, *,
        _context: ResolvedVault, _route: CommandRoute,
    ) -> Reply: ...


def supported(response: dict[str, Any]) -> bool:
    """Qualify the serving peer, not the installed package version."""
    versions = response.get("cache_warm_selection_versions")
    return bool(
        response.get("ok") is True and response.get("accepting_work") is True
        and isinstance(versions, list) and any(
            type(version) is int and version == VERSION for version in versions
        )
    )


@dataclass(frozen=True)
class SelectedService:
    sender: ServiceSender
    context: ResolvedVault
    route: CommandRoute
    selection: WarmSelection
    health: dict[str, Any]
    source_supported: bool


@contextmanager
def selected_service() -> Iterator[SelectedService | None]:
    """Keep the existing root and transport owners selected through an operation."""
    from . import cli, config

    root, native_default = config.configuration_selection(strict=True)
    selection = WarmSelection(root or os.getcwd(), native_default)
    with use_configuration_root(selection.root, native_default=selection.native_default):
        context = config.resolve_context(strict=True)
        route = CommandRoute()
        health = cli.send_command({"action": "ping"}, timeout=5.0, _context=context, _route=route)
        if not cli._ready_for_work(health) or route.sender is None:
            yield None
            return
        assert health is not None
        yield SelectedService(cli.send_command, context, route, selection, health, supported(health))


def scoped_status() -> Reply:
    """Never render resident-source progress as the selected caller's warming."""
    from . import cli

    with selected_service() as selected:
        if selected is None:
            return None
        response: Reply = selected.health
        if selected.source_supported:
            response = selected.sender(
                {"action": "ping", FIELD: selected.selection.describe()}, 5.0,
                _context=selected.context, _route=selected.route,
            )
            if not cli._ready_for_work(response):
                return None
        assert response is not None
        response = dict(response)
        warm = response.get("cache_warm")
        if (
            not supported(response) or not acknowledged(warm) or not isinstance(warm, dict)
            or warm.get("target_policy") != CACHE_TARGET_CAPABILITY
        ):
            response["cache_warm"] = None
        return response
