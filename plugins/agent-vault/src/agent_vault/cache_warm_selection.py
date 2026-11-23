"""Provider-side request selection for unlock-triggered cache work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import local
from typing import TYPE_CHECKING, Any

from .cache_warm_status import WarmState

if TYPE_CHECKING:
    from .service import VaultService

FIELD = "cache_warm_selection"
CONTRACT = "agent-vault.cache-warm-selection"
VERSION = 1
MAX_ROOT_LENGTH = 4096


@dataclass(frozen=True)
class WarmSelection:
    root: str
    native_default: bool


def decode(value: object) -> WarmSelection:
    """Admit native paths only; filesystem and policy checks belong to the worker."""
    if not isinstance(value, dict) or set(value) != {
        "contract", "version", "root", "native_default",
    }:
        raise ValueError("invalid warming selection")
    root = value["root"]
    if (
        value["contract"] != CONTRACT
        or type(value["version"]) is not int
        or value["version"] != VERSION
        or not isinstance(root, str)
        or not root or len(root) > MAX_ROOT_LENGTH
        or any(ord(char) < 32 for char in root)
        or not Path(root).is_absolute()
        or type(value["native_default"]) is not bool
    ):
        raise ValueError("invalid warming selection")
    return WarmSelection(str(Path(root)), value["native_default"])


def bind(context: local, request: dict[str, Any]) -> None:
    """Replace thread-local selection for every request, including legacy callers."""
    context.warm_selection = None
    context.invalid_warm_selection = False
    if FIELD in request:
        try:
            context.warm_selection = decode(request[FIELD])
        except ValueError:
            context.invalid_warm_selection = True


def enqueue(
    service: VaultService, kpdb: str, group: str | None, *, confirmed: bool = False,
) -> WarmState:
    """Use the same selection for explicit, provider and prompted unlock paths."""
    return service._cache_warm.enqueue(
        kpdb, group, confirmed=confirmed,
        selection=getattr(service._request_ctx, "warm_selection", None),
        invalid_selection=getattr(service._request_ctx, "invalid_warm_selection", False),
    )


def status(service: VaultService, kpdb: str | None, group: str | None) -> dict[str, object]:
    """Read retained progress without resolving configuration on the ping path."""
    if service._request_ctx.invalid_warm_selection:
        return WarmState("not-queued", "configuration-unavailable").describe()
    return service._cache_warm.status(
        kpdb, group, selection=service._request_ctx.warm_selection,
    )


def unlocked(request: dict[str, Any], warmed: WarmState) -> dict[str, Any]:
    """Keep legacy unlock replies unchanged while exposing explicit acceptance."""
    return {"ok": True, **({"cache_warm": warmed.describe()} if FIELD in request else {})}
