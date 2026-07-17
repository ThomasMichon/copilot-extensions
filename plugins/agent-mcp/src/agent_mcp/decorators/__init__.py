"""Decorator registry + pipeline construction.

A bridge's ``decorators:`` config is a list of ``{type: ..., <options>}`` mappings.
Decorators are listed **client -> upstream**: the first entry is closest to the
client (outermost), the last is closest to the upstream. Requests flow down the
list; responses bubble back up.

Recommended ordering: context-reducers that synthesize their own tools
(``defer``, ``code-mode``) go *first* (outermost), then cosmetic ``rename``, then
``filter``, with ``storage`` *last* (innermost) so it sees real payloads.
"""

from __future__ import annotations

from ..config import BridgeConfig, ConfigError
from .base import BridgeContext, Decorator
from .code_mode import CodeModeDecorator
from .defer import DeferDecorator
from .filter import FilterDecorator
from .gate import GateDecorator
from .rename import RenameDecorator
from .storage import StorageDecorator
from .transform import TransformDecorator

__all__ = [
    "REGISTRY",
    "BridgeContext",
    "Decorator",
    "build_decorator",
    "build_decorators",
    "known_types",
]

REGISTRY: dict[str, type[Decorator]] = {
    FilterDecorator.type: FilterDecorator,
    RenameDecorator.type: RenameDecorator,
    DeferDecorator.type: DeferDecorator,
    CodeModeDecorator.type: CodeModeDecorator,
    StorageDecorator.type: StorageDecorator,
    TransformDecorator.type: TransformDecorator,
    GateDecorator.type: GateDecorator,
}


def known_types() -> tuple[str, ...]:
    return tuple(REGISTRY)


def build_decorator(spec, ctx: BridgeContext) -> Decorator:
    """Build one decorator from a :class:`~agent_mcp.config.DecoratorSpec`."""
    cls = REGISTRY.get(spec.type)
    if cls is None:
        raise ConfigError(
            f"unknown decorator type '{spec.type}' (known: {', '.join(REGISTRY)})")
    return cls(spec.options, ctx)


def build_decorators(cfg: BridgeConfig, ctx: BridgeContext) -> list[Decorator]:
    """Build the full decorator stack for a bridge config.

    Explicit ``decorators:`` entries come first (client->upstream order); the
    legacy top-level ``tools:`` filter, if active, is appended at the upstream end
    for backward compatibility.
    """
    stack = [build_decorator(spec, ctx) for spec in cfg.decorators]
    if cfg.tools.active:
        stack.append(FilterDecorator(
            {"allow": cfg.tools.allow, "deny": cfg.tools.deny}, ctx))
    return stack
