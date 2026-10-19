"""Session-tenant normalization and pending owned-delivery records."""

from __future__ import annotations

from typing import Any


def normalize_sessions(value: Any) -> dict[str, dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}
    if isinstance(value, dict):
        for tenant, meta in value.items():
            if not isinstance(meta, dict):
                continue
            mux = meta.get("mux_session")
            try:
                expires_at = float(meta.get("expires_at"))
            except (TypeError, ValueError):
                continue
            if isinstance(mux, str) and mux:
                sessions[str(tenant)] = {
                    "mux_session": mux, "expires_at": expires_at,
                    "confirmed": bool(meta.get("confirmed", False)),
                    "generation": str(meta.get("generation") or ""),
                    **({"pending_delivery": meta["pending_delivery"]}
                       if isinstance(meta.get("pending_delivery"), dict) else {}),
                }
    return sessions


def record_pending_delivery(codespace: str, tenant: str, pending: dict | None, *,
                            lock, read_holds, write_holds) -> None:
    """Journal the owned send on its existing connection tenant."""
    with lock():
        holds = read_holds()
        hold = holds.get(codespace)
        if hold is None or tenant not in hold.sessions:
            raise RuntimeError("Pending delivery has no owning connection tenant")
        if pending is None:
            hold.sessions[tenant].pop("pending_delivery", None)
        else:
            hold.sessions[tenant]["pending_delivery"] = pending
        write_holds(holds)
