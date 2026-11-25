"""Build the monitor's existing guarded discovery publication."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from .checkout_relocation_state import writer_capabilities


class _Rendezvous(Protocol):
    def rendezvous(self) -> dict[str, Any]: ...


class _LockFields(Protocol):
    def lock_extra(self) -> dict[str, Any]: ...


class _HookFacts(Protocol):
    @property
    def generation(self) -> str: ...


class _HookFactSource(Protocol):
    @property
    def hook_facts(self) -> _HookFacts: ...


_Classifier = TypeVar("_Classifier")
_Writer = TypeVar("_Writer")


def lock_extra(
    *, prefix: str, mux: str | None, generation: str | None, version: str | None,
    slot: str | os.PathLike[str], installation_context: dict[str, Any] | None,
    hook_server: _Rendezvous | None, classify_server: _Classifier | None,
    tracking_write_server: _Writer | None, published: bool,
    worktrees_runtime: _HookFactSource, worktree_status_runtime: _LockFields,
    managed_mux_runtime: _LockFields, configuration_ready: bool, control_port: int,
    classify_fields: Callable[[_Classifier], dict[str, Any]],
    tracking_fields: Callable[[_Writer], dict[str, Any]],
) -> dict[str, Any]:
    """Compose request-surface metadata without publishing it or changing custody."""
    extra: dict[str, Any] = {
        "prefix": prefix, "mux": bool(mux), "runtime_generation": generation,
        "version": version, "runtime_slot": os.path.basename(slot),
    }
    if isinstance(installation_context, dict):
        extra.update({
            "marketplaceId": str(installation_context.get("marketplaceId") or ""),
            "installReceipt": str(installation_context.get("installReceipt") or ""),
            "pluginRoot": str(installation_context.get("pluginRoot") or ""),
        })
    if hook_server is not None and published:
        extra.update(hook_server.rendezvous())
    if classify_server is not None and published:
        extra.update(classify_fields(classify_server))
    if tracking_write_server is not None and published:
        extra.update(tracking_fields(tracking_write_server), **writer_capabilities())
    if published:
        if configuration_ready:
            from .configuration_resident import discovery_fields

            extra.update(discovery_fields(control_port))
        extra.update(
            hook_facts_version=2, hook_facts_generation=worktrees_runtime.hook_facts.generation,
        )
        extra.update(worktree_status_runtime.lock_extra())
        extra.update(managed_mux_runtime.lock_extra())
    return extra
