"""Minimal in-memory auth state, with versioned expiration-preserving adoption.

The negotiated functions use the private native channels in service_handoff:
only compatible, unexpired master passwords cross generations, never a file,
log, command-line argument or environment carrier. Credential-value caches
re-warm lazily.

The older build/apply/export/stdin helpers remain for the legacy POSIX restart
path and explicit compatibility callers. They are not the negotiated protocol
or evidence of incoming-first replacement. In particular, ordinary named pipes
and TCP remain excluded from their unversioned secret-export action.
"""

from __future__ import annotations

import json
import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with service.py
    from .service import VaultService

# The handoff-export action may only be answered over a transport we can *prove*
# is access-gated to the owner. Today that is exactly the AF_UNIX socket, which
# run_server binds and then ``os.chmod(..., 0o600)`` -- single-user by filesystem
# permission. The Windows named pipe is deliberately NOT included: the current
# pipe server (winpipe.start_pipe_server) binds via ``loop.start_serving_pipe``
# with the default DACL, which is not proven to be single-user-restricted, so the
# master password must not cross it until the pipe carries a hardened, owner-only
# security descriptor. A host without a qualifying transport (Windows today, or a
# TCP-only WSL layout) simply can't hand off and safely degrades to the existing
# re-unlock (invariant #3). Plain loopback TCP is never gated and is always
# refused.
OWNER_GATED_TRANSPORTS = frozenset({"unix"})
AUTH_STATE_CONTRACT = "agent-vault.auth-state"
AUTH_STATE_VERSIONS = (1,)


def negotiate_auth_state(versions: object) -> int | None:
    if not isinstance(versions, (list, tuple)):
        return None
    overlap = set(AUTH_STATE_VERSIONS) & {
        value for value in versions if type(value) is int
    }
    return max(overlap) if overlap else None


def _finite_time(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def build_auth_state(service: VaultService) -> dict:
    """Capture only unexpired unlocked masters, without resetting their clocks."""
    passwords = {}
    with service._credential_lock:
        now = time.time()
        for kpdb in service.cli.unlocked_vaults():
            password = service.cli.get_password(kpdb)
            stamp = service._password_set_at.get(kpdb)
            if not isinstance(password, str) or not _finite_time(stamp) or stamp > now:
                continue
            deadline = service._password_expiration(kpdb, stamp)
            if deadline is not None and deadline <= now:
                continue
            passwords[kpdb] = {"password": password, "set_at": stamp, "expires_at": deadline}
    return {"contract": AUTH_STATE_CONTRACT, "version": 1, "passwords": passwords}


def apply_auth_state(service: VaultService, payload: object) -> int:
    """Adopt a negotiated state in memory, retaining the original expiration."""
    if (
        not isinstance(payload, dict) or payload.get("contract") != AUTH_STATE_CONTRACT
        or type(payload.get("version")) is not int
        or payload["version"] not in AUTH_STATE_VERSIONS
        or not isinstance(payload.get("passwords"), dict)
    ):
        return 0
    warmed = 0
    with service._credential_lock:
        now = time.time()
        for kpdb, entry in payload["passwords"].items():
            if not isinstance(kpdb, str) or not kpdb or not isinstance(entry, dict):
                continue
            password, stamp, deadline = (
                entry.get("password"), entry.get("set_at"), entry.get("expires_at"),
            )
            if (
                not isinstance(password, str) or not _finite_time(stamp) or stamp > now
                or "expires_at" not in entry
                or (deadline is not None and (
                    not _finite_time(deadline) or deadline <= now or deadline < stamp
                ))
            ):
                continue
            service.cli.set_password(kpdb, password)
            service._password_set_at[kpdb] = stamp
            service._password_handoff_deadlines.pop(kpdb, None)
            if deadline is not None:
                service._password_handoff_deadlines[kpdb] = deadline
            warmed += 1
    return warmed


def build_handoff_payload(service: VaultService) -> dict:
    """Serialize the minimal warm state the new generation needs to avoid re-unlock.

    Returns a plaintext-secret-bearing dict: the unlocked master password per
    currently-unlocked vault, plus the password-TTL bookkeeping and the daemon's
    ``ttl_override``. NEVER log the result.
    """
    backend = service.cli
    master_passwords: dict[str, str] = {}
    for kpdb in backend.unlocked_vaults():
        pw = backend.get_password(kpdb)
        if isinstance(pw, str):
            master_passwords[kpdb] = pw
    return {
        "master_passwords": master_passwords,
        "password_set_at": {
            kpdb: service._password_set_at.get(kpdb)
            for kpdb in master_passwords
            if service._password_set_at.get(kpdb) is not None
        },
        "ttl_override": service.ttl_override,
    }


def apply_handoff_payload(service: VaultService, payload: dict | None) -> int:
    """Adopt a handoff payload into ``service``; return the number of vaults warmed.

    Idempotent and defensive: a ``None`` or otherwise malformed payload warms
    nothing (returns ``0``). Restores each unlocked master password (so reads
    succeed with no re-unlock) and its TTL clock, and adopts the ``ttl_override``
    when present.
    """
    if not isinstance(payload, dict):
        return 0
    master_passwords = payload.get("master_passwords") or {}
    set_at = payload.get("password_set_at") or {}
    warmed = 0
    if isinstance(master_passwords, dict):
        for kpdb, pw in master_passwords.items():
            if not (kpdb and isinstance(pw, str)):
                continue
            service.cli.set_password(kpdb, pw)
            ts = set_at.get(kpdb) if isinstance(set_at, dict) else None
            service._password_set_at[kpdb] = (
                float(ts) if (isinstance(ts, (int, float))
                              and not isinstance(ts, bool)) else time.time()
            )
            warmed += 1
    ttl_override = payload.get("ttl_override")
    # ttl_override is int|None with 0 meaning "persistent"; only adopt a real int
    # (not a bool) so a malformed payload can't corrupt the daemon's TTL policy.
    if isinstance(ttl_override, int) and not isinstance(ttl_override, bool):
        service.ttl_override = ttl_override
    return warmed


def handoff_export_response(service: VaultService, *, transport: str) -> dict:
    """Answer a ``handoff-export`` request, enforcing the owner-gated transport.

    Refused (``ok=False`` + ``refused=True``) unless the request arrived over an
    owner-gated local transport, so the master password never crosses an
    un-access-gated channel. On success returns the handoff payload under
    ``handoff`` (the caller applies it via :func:`apply_handoff_payload`).
    """
    if transport not in OWNER_GATED_TRANSPORTS:
        return {
            "ok": False,
            "refused": True,
            "error": (
                "handoff-export is served only over the owner-gated local "
                f"transport (got {transport!r}); the unlocked master secret is "
                "never exposed over loopback TCP"
            ),
        }
    return {"ok": True, "handoff": build_handoff_payload(service)}


def request_handoff_from_running_daemon() -> dict | None:
    """Ask a currently-running daemon (if any) to export its unlocked state.

    Client-side half of the drain-safe cutover, invoked by the installer's
    update path BEFORE it stops the outgoing generation. Returns the handoff
    payload dict on success, or ``None`` when there is nothing to hand off:
    no daemon running, the daemon refused (e.g. the reachable transport is not
    owner-gated -- Windows named pipe today, or plain TCP), or any transport
    failure. ``None`` is always a safe, silent degrade to the existing
    re-unlock path (invariant #3) -- never raises.

    Deliberately does not decide by platform here: it dials whatever
    transport :func:`send_command`'s rendezvous discovery actually reaches,
    so a POSIX host degrades the same way a Windows host does if, say, only a
    TCP endpoint is currently advertised.
    """
    from .service import send_command  # local import: avoid a service<->cutover cycle

    try:
        resp = send_command({"action": "handoff-export"})
    except Exception:
        return None
    if not (isinstance(resp, dict) and resp.get("ok")):
        return None
    handoff = resp.get("handoff")
    return handoff if isinstance(handoff, dict) else None


def consume_pending_handoff(*, environ, stdin) -> dict | None:
    """Read (and consume) a pending handoff payload for `--handoff-stdin`.

    Checks the ``AGENT_VAULT_HANDOFF_JSON`` env var first (the systemd-managed
    restart carrier) -- popping it so the secret does not linger in this
    process's own environ block a moment longer than necessary -- then falls
    back to reading ``stdin`` whole (the direct-invocation carrier). Returns
    ``None`` (never raises) when neither carrier has anything, or its content
    is not valid JSON. Call this BEFORE any fork/daemonize.
    """
    raw = environ.pop("AGENT_VAULT_HANDOFF_JSON", "")
    if not raw:
        try:
            raw = stdin.read()
        except Exception:
            raw = ""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None
