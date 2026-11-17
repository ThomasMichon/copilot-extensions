"""Reconcile legacy status mappings through Manager's existing CAS owner."""

from __future__ import annotations

from typing import Any

from . import mux_discovery, mux_mapping_registry
from .mux_mapping_registry import MuxMappingRegistry


def handle_discovery(
    registry: MuxMappingRegistry, payload: dict[str, Any],
    runtime: Any = None, handler_tracker: Any = None,
) -> dict[str, Any]:
    """Keep normal admission; recover only a live, same-owner legacy mapping."""
    result: dict[str, Any] = mux_discovery.handle_discovery(
        registry, payload, runtime, handler_tracker,
    )
    if result.get("reason") != "incumbent-mapping":
        return result
    if runtime is not None and not runtime.accepting_requests():
        return {"applied": False, "reason": "draining"}
    if handler_tracker is not None:
        handler_tracker.enter()
    try:
        incumbent = registry.get(payload["project"], payload["worktree_id"])
        if (
            incumbent is None or incumbent.get("live") is not True
            or incumbent.get("native_session_incarnation")
            or mux_discovery._valid_native_incarnation(incumbent.get("session_incarnation"))
            or incumbent.get("mux_session") != payload["mux_session"]
            or not mux_mapping_registry._same_worktree_path(
                incumbent.get("worktree_path"), payload["worktree_path"],
            )
            or not mux_mapping_registry._same_mux_executable(
                incumbent["mux_bin"], payload["mux_bin"],
            )
        ):
            return result
        confirmed: dict[str, Any] = mux_discovery.confirm_native_session(
            payload, connected_only=True,
        )
        if not confirmed.get("applied"):
            return confirmed
        replacement = {
            **payload, "attached_clients": confirmed["attached_clients"],
            "native_session_incarnation": payload["session_incarnation"], "panes": [],
        }
        admitted: dict[str, Any] = registry.register_next(replacement, expected=incumbent)
        if not admitted.get("applied"):
            return admitted
        current = registry.get(payload["project"], payload["worktree_id"])
        if (
            current is None or current.get("live") is not True
            or current.get("mapping_revision") != admitted["revision"]
            or any(current.get(field) != replacement[field] for field in (
                "mux_session", "session_incarnation", "native_session_incarnation",
            ))
            or not mux_mapping_registry._same_worktree_path(
                current.get("worktree_path"), replacement["worktree_path"],
            )
            or not mux_mapping_registry._same_mux_executable(
                current["mux_bin"], replacement["mux_bin"],
            )
        ):
            return {"applied": False, "reason": "mapping-changed"}
        from .mux_daemon_live import _mapping_to_observation

        return {"applied": True, "entry": _mapping_to_observation(current)}
    finally:
        if handler_tracker is not None:
            handler_tracker.exit()
