"""Session tenants and separately durable pending owned-delivery records."""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
import time
import uuid

from ssh_manager.forward_keeper import KeeperStore
from venue_copilot.portable_mux import MuxEndpoint

DEFAULT_TTL = 3600.0


@dataclass
class OwnerHold:
    """The Connection Owner's durable hold on one CodeSpace.

    ``pinned`` marks the credential relay as the Owner's always-on service.
    ``tenants`` maps a tenant id -> its last heartbeat epoch; the ref-count is
    the number of live, non-stale tenants. A hold is kept while pinned or any
    tenant is live.

    ``daemon_port`` asks the Owner to keep a reverse forward of the host Bridge
    daemon into the CodeSpace, following the host's live port. ``sessions``
    records each tenant's remote ``mux_session`` and absolute ``expires_at``
    lease cap. Portable sessions also retain a versioned ``mux_endpoint``;
    missing native proof is unknown, never permission to probe the default
    server. Extra forwards live as long as ``daemon_port``; assigned local
    forwards record ports allocated from ``0:VENUE_PORT``.
    """

    codespace: str
    host: str
    created_at: float
    heartbeat_at: float
    pinned: bool = False
    tenants: dict[str, float] = field(default_factory=dict)
    daemon_port: int | None = None
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    reverse_forwards: dict[str, int] = field(default_factory=dict)
    local_forwards: dict[str, int] = field(default_factory=dict)
    assigned_local_forwards: dict[str, int] = field(default_factory=dict)

    def live_tenants(self, ttl: float = DEFAULT_TTL) -> dict[str, float]:
        """Tenants whose heartbeat is within ``ttl``."""
        now = time.time()
        return {t: hb for t, hb in self.tenants.items() if now - hb <= ttl}

    def should_hold(self, ttl: float = DEFAULT_TTL) -> bool:
        """Whether the durable connection should be kept up."""
        return self.pinned or bool(self.live_tenants(ttl))


def selected_endpoint(mux_session: str | None, value: dict | None, confirmed: bool) -> MuxEndpoint | None:
    """Validate a requested portable identity before any Owner-store operation."""
    if value is None:
        return None
    endpoint = MuxEndpoint.parse(value)
    if endpoint.mux_session != mux_session or (confirmed and not endpoint.confirmed):
        raise ValueError("Session hold differs from its confirmed portable mux endpoint")
    return endpoint


def session_record(
    current: dict, *, mux_session: str, mux_endpoint: dict | None, endpoint: MuxEndpoint | None,
    confirmed: bool, fresh: bool, restore: dict | None, now: float, max_lease: float,
) -> dict[str, Any]:
    """Retain a session generation while allowing only its pending native proof to advance."""
    prior = restore if restore is not None else ({} if fresh else current)
    retained_endpoint = prior.get("mux_endpoint")
    if "mux_endpoint" in prior:
        previous = MuxEndpoint.parse(retained_endpoint)
        if previous.mux_session != mux_session or (
            confirmed and mux_endpoint is None and not previous.confirmed
        ):
            raise ValueError("Session hold cannot downgrade its retained portable mux endpoint")
    if endpoint is not None and retained_endpoint is not None and mux_endpoint != retained_endpoint:
        previous = MuxEndpoint.parse(retained_endpoint)
        if previous.confirmed or (
            previous.attempt, previous.session_id, previous.mux_socket,
        ) != (endpoint.attempt, endpoint.session_id, endpoint.mux_socket):
            raise ValueError("Session hold cannot replace its retained portable mux attempt")
    return {
        "mux_session": mux_session,
        "expires_at": prior.get("expires_at", now + max_lease),
        "confirmed": bool(confirmed or prior.get("confirmed", False)),
        "generation": prior.get("generation") or uuid.uuid4().hex,
        **({"mux_endpoint": mux_endpoint if mux_endpoint is not None else retained_endpoint}
           if mux_endpoint is not None or "mux_endpoint" in prior else {}),
        **({"pending_delivery": prior["pending_delivery"]} if prior.get("pending_delivery") else {}),
    }


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
                    **({"mux_endpoint": meta["mux_endpoint"]} if "mux_endpoint" in meta else {}),
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
                    **({"mux_endpoint": meta["mux_endpoint"]} if "mux_endpoint" in meta else {}),
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
