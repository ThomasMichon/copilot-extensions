"""Drain-safe cutover: hand off the unlocked master secret between generations.

A routine vault version bump replaces the running daemon. Today that hard-restart
drops the in-memory unlocked master password(s) and the warm credential cache,
forcing the operator to re-unlock. The drain-safe cutover (#743) instead stands up
the new generation, has it **inherit the unlocked state from the outgoing one**,
flips the routing record, and drains the old daemon -- so a version bump costs no
re-unlock.

This module is the *mechanism*: it builds and applies the minimal handoff payload.
The payload carries the unlocked master password(s) in plaintext, so it is
**security-critical** and bound by the invariants in ``docs/architecture.md``:

* it crosses generations ONLY over a transport we can prove is access-gated to the
  owner -- today the vault's AF_UNIX control socket (``0o600``) -- and **never**
  plain loopback TCP, the network, disk, an env var, or a log line. The
  ``handoff-export`` daemon action enforces that transport gate (see
  ``service.handle_request``); the Windows named pipe is excluded until it carries
  a hardened owner-only ACL, and a host without a qualifying transport (Windows
  today, or a TCP-only WSL layout) safely degrades to the existing re-unlock path;
* the secret is transferred, applied, and dropped -- this module never persists it
  and never logs its value.

Only what is needed to avoid a re-unlock is carried: the unlocked master
password(s) + their TTL bookkeeping. The credential-*value* cache is intentionally
left to re-warm lazily on the new generation, keeping the secret surface minimal.
"""

from __future__ import annotations

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
