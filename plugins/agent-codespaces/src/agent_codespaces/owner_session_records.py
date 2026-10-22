"""Session tenants and separately durable pending owned-delivery records."""

from __future__ import annotations

from typing import Any

from ssh_manager.forward_keeper import KeeperStore


def normalize_tenants(value: Any) -> dict[str, float]:
    tenants = {}
    if isinstance(value, dict):
        for tenant, heartbeat in value.items():
            try:
                tenants[str(tenant)] = float(heartbeat)
            except (TypeError, ValueError):
                continue
    return tenants


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


def _read_pending(codespace, owner_file) -> dict[str, dict]:
    store = KeeperStore(owner_file.parent / "pending-deliveries")
    data = store.read(codespace)
    if data is None:
        if store.state_path(codespace).exists():
            raise RuntimeError("Pending delivery journal is unreadable; refusing to discard ownership")
        return {}
    if type(data.get("version")) is not int or data["version"] != 1 or not isinstance(
        data.get("pending_deliveries"), dict,
    ):
        raise RuntimeError("Unsupported pending delivery journal")
    return dict(data["pending_deliveries"])


def _write_pending(codespace, pending, owner_file) -> None:
    store = KeeperStore(owner_file.parent / "pending-deliveries")
    if pending:
        store.write(codespace, {"version": 1, "pending_deliveries": pending})
    else:
        store.remove(codespace)


def migrate_pending_deliveries(holds, owner_file) -> bool:
    """Persist the unreleased embedded format before a tenant can be pruned."""
    changed = False
    for codespace, hold in holds.items():
        embedded = {}
        for tenant, meta in hold.sessions.items():
            if isinstance(meta.get("pending_delivery"), dict):
                embedded[tenant] = {
                    "mux_session": meta["mux_session"], "daemon_port": hold.daemon_port,
                    **meta["pending_delivery"],
                }
        if embedded:
            _write_pending(codespace, {**embedded, **_read_pending(codespace, owner_file)}, owner_file)
            for tenant in embedded:
                hold.sessions[tenant].pop("pending_delivery")
            changed = True
    return changed


def get_pending_delivery(codespace, tenant, *, owner_file, lock, read_holds, write_holds):
    with lock():
        holds = read_holds()
        if migrate_pending_deliveries(holds, owner_file):
            write_holds(holds)
        return _read_pending(codespace, owner_file).get(tenant)


def record_pending_delivery(codespace: str, tenant: str, pending: dict | None, *,
                            owner_file, lock, read_holds, write_holds) -> None:
    """Journal workflow ownership without retaining or renewing a connection."""
    with lock():
        holds = read_holds()
        if migrate_pending_deliveries(holds, owner_file):
            write_holds(holds)
        ledger = _read_pending(codespace, owner_file)
        hold = holds.get(codespace)
        if tenant not in ledger and (hold is None or tenant not in hold.sessions):
            raise RuntimeError("Pending delivery has no owning connection tenant")
        if pending is None:
            ledger.pop(tenant, None)
        else:
            ledger[tenant] = pending
        _write_pending(codespace, ledger, owner_file)
