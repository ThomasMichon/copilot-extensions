"""Connection Owner registry -- durable, ref-counted ownership of a CodeSpace's
persistent connection (the credential relay + the exclusive claim), independent
of any single agent-bridge dispatch.

Background
----------
Three consumers share a machine's link to a CodeSpace and must coexist without
breaking each other: agent-bridge **dispatch** (ACP / Session-Host), the
**credential-relay**, and one-off ``agent-codespaces ssh`` commands. They do not
conflict over connectivity -- they conflict over two *single-owner* resources:
the relay ``-R`` bind (dotfiles#561) and the exclusive claim (dotfiles#897). The
fix is to give those a single durable **Connection Owner** and make the three
consumers non-owning **tenants**.

This module is **increment 1** of that design (dotfiles#1333): the registry +
lease / ref-count lifecycle *only*. It records, per (machine, CodeSpace), which
tenants currently need the durable connection and whether the relay is *pinned*,
and answers :func:`should_hold` ("keep the connection up?"). It does **not** open
or own a connection yet -- a later increment attaches the self-healing
``SupervisedRelayForward`` under this registry. Because nothing consumes it yet,
this module is purely additive and safe to land without a daemon cutover.

State of record mirrors :mod:`agent_codespaces.lease`: a host-side JSON file
(``~/.agent-codespaces/connection-owner.json``) guarded by an exclusive lock file
for race-safety across parallel worktree agents on the same machine, written
atomically.

Model
-----
A Connection Owner keeps one durable connection per CodeSpace on this machine
alive while there is any reason to:

* the credential relay is **pinned** (``pin=True`` -- the relay is the Owner's
  own always-on service while the CodeSpace should be able to authenticate), or
* at least one **tenant** currently needs it (a dispatch, a one-off ssh, ...).
  Tenants are ref-counted by id and heartbeated, so a forgotten tenant is
  reclaimed by TTL.

:func:`should_hold` = pinned **or** >= 1 live tenant. Only when it is ``False``
may a later increment tear the connection down.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields

from .config import RUNTIME_DIR, ensure_runtime_dir

log = logging.getLogger("agent-codespaces")

OWNER_FILE = RUNTIME_DIR / "connection-owner.json"
_LOCK_FILE = RUNTIME_DIR / "connection-owner.lock"

# A tenant is held by a *logical* consumer (a dispatch, a one-off ssh), not by
# the short-lived CLI process that registered it, so reclamation is TTL-based: a
# live tenant refreshes via :func:`heartbeat`; a forgotten one expires after the
# TTL. A **pinned** relay has no TTL -- it persists until explicitly unpinned.
DEFAULT_TTL = 3600.0


@dataclass
class OwnerHold:
    """The Connection Owner's durable hold on one CodeSpace.

    ``pinned`` marks the credential relay as the Owner's always-on service.
    ``tenants`` maps a tenant id -> its last heartbeat epoch; the ref-count is
    the number of *live* (non-stale) tenants. A hold is kept while ``pinned`` or
    any tenant is live (see :meth:`should_hold`).
    """

    codespace: str
    host: str
    created_at: float
    heartbeat_at: float
    pinned: bool = False
    tenants: dict[str, float] = field(default_factory=dict)

    def live_tenants(self, ttl: float = DEFAULT_TTL) -> dict[str, float]:
        """Tenants whose heartbeat is within ``ttl``."""
        now = time.time()
        return {t: hb for t, hb in self.tenants.items() if now - hb <= ttl}

    def should_hold(self, ttl: float = DEFAULT_TTL) -> bool:
        """Whether the durable connection should be kept up."""
        return self.pinned or bool(self.live_tenants(ttl))


def _this_host() -> str:
    return platform.node()


@contextmanager
def _owner_lock(timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file.

    Mirrors :func:`agent_codespaces.lease._lease_lock`, with its own lock file so
    the two stores never serialize against each other.
    """
    ensure_runtime_dir()
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # Stale lock recovery: if older than timeout*3, steal it.
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > timeout * 3:
                        _LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    "Could not acquire connection-owner lock (held by another process)"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        _LOCK_FILE.unlink(missing_ok=True)


def _read_holds() -> dict[str, OwnerHold]:
    """Read connection-owner.json -> {codespace: OwnerHold}. {} if absent/corrupt."""
    if not OWNER_FILE.exists():
        return {}
    try:
        raw = json.loads(OWNER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("connection-owner.json unreadable; treating as empty")
        return {}
    if not isinstance(raw, dict):
        log.warning("connection-owner.json is not an object; treating as empty")
        return {}
    holds: dict[str, OwnerHold] = {}
    known_fields = {f.name for f in fields(OwnerHold)}
    for codespace, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        # Tolerate forward-compat extra keys (a newer writer may add fields):
        # filter to this dataclass's fields rather than dropping the whole record
        # on an unknown key, and force ``codespace`` to the map key.
        fields_in = {k: v for k, v in rec.items() if k in known_fields}
        fields_in["codespace"] = codespace
        try:
            hold = OwnerHold(**fields_in)
        except TypeError:
            continue
        # Sanitize the tenants map: must be {str: float}. Drop any entry whose
        # heartbeat isn't numeric so live_tenants() can't crash on ``now - hb``.
        tenants: dict[str, float] = {}
        if isinstance(hold.tenants, dict):
            for tenant, hb in hold.tenants.items():
                try:
                    tenants[str(tenant)] = float(hb)
                except (TypeError, ValueError):
                    continue
        hold.tenants = tenants
        holds[codespace] = hold
    return holds


def _write_holds(holds: dict[str, OwnerHold]) -> None:
    """Atomically write connection-owner.json."""
    ensure_runtime_dir()
    tmp = OWNER_FILE.with_suffix(".json.tmp")
    payload = {c: asdict(hold) for c, hold in holds.items()}
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, OWNER_FILE)


def _prune_hold(hold: OwnerHold, ttl: float) -> None:
    """Drop stale tenants from ``hold`` in place."""
    live = hold.live_tenants(ttl)
    if len(live) != len(hold.tenants):
        for tenant in list(hold.tenants):
            if tenant not in live:
                log.info(
                    "Reclaiming stale connection tenant %s on %s",
                    tenant, hold.codespace,
                )
        hold.tenants = live


def _prune(holds: dict[str, OwnerHold], ttl: float) -> dict[str, OwnerHold]:
    """Prune stale tenants, then drop holds with no reason to persist."""
    live: dict[str, OwnerHold] = {}
    for codespace, hold in holds.items():
        _prune_hold(hold, ttl)
        if hold.should_hold(ttl):
            live[codespace] = hold
        else:
            log.info("Releasing connection hold on %s (no tenants, not pinned)", codespace)
    return live


def get_hold(codespace: str, ttl: float = DEFAULT_TTL) -> OwnerHold | None:
    """Return the (pruned) hold for ``codespace``, or ``None``."""
    with _owner_lock():
        holds = _prune(_read_holds(), ttl)
        _write_holds(holds)
        return holds.get(codespace)


def list_holds(ttl: float = DEFAULT_TTL, prune: bool = True) -> list[OwnerHold]:
    """Return current holds (optionally pruned).

    When pruning, the cleaned state is written back **unconditionally** -- ``_prune``
    mutates ``OwnerHold`` objects in place (dropping stale tenants), so a value-wise
    dict comparison can't detect that change; writing always keeps the on-disk store
    from retaining stale tenants.
    """
    with _owner_lock():
        holds = _read_holds()
        if prune:
            holds = _prune(holds, ttl)
            _write_holds(holds)
        return list(holds.values())


def should_hold(codespace: str, ttl: float = DEFAULT_TTL) -> bool:
    """Whether the durable connection to ``codespace`` should be kept up."""
    hold = get_hold(codespace, ttl)
    return hold.should_hold(ttl) if hold else False


def hold(
    codespace: str,
    tenant: str,
    *,
    pin: bool = False,
    ttl: float = DEFAULT_TTL,
) -> OwnerHold:
    """Register (or refresh) ``tenant`` on the connection to ``codespace``.

    Creates the hold if absent. Idempotent per tenant -- re-registering the same
    tenant just refreshes its heartbeat and preserves ``created_at``. ``pin=True``
    marks the credential relay as pinned (the Owner's always-on service); pin is
    sticky and only cleared by :func:`release` with ``unpin=True``.
    """
    if not codespace:
        raise RuntimeError("hold requires a CodeSpace name")
    if not tenant:
        raise RuntimeError("hold requires a tenant id")
    now = time.time()
    with _owner_lock():
        holds = _prune(_read_holds(), ttl)
        existing = holds.get(codespace)
        if existing is None:
            existing = OwnerHold(
                codespace=codespace,
                host=_this_host(),
                created_at=now,
                heartbeat_at=now,
            )
            holds[codespace] = existing
        existing.tenants[tenant] = now
        existing.heartbeat_at = now
        if pin:
            existing.pinned = True
        _write_holds(holds)
        return existing


def heartbeat(
    codespace: str, tenant: str, ttl: float = DEFAULT_TTL,
) -> OwnerHold | None:
    """Refresh ``tenant``'s heartbeat. Returns the hold, or ``None`` if absent.

    Unlike :func:`hold`, does not create a missing hold or a missing tenant --
    heartbeating a tenant that was already reclaimed is a no-op (returns the hold
    if it still exists for other reasons, else ``None``).
    """
    now = time.time()
    with _owner_lock():
        holds = _prune(_read_holds(), ttl)
        existing = holds.get(codespace)
        if existing is None:
            return None
        if tenant in existing.tenants:
            existing.tenants[tenant] = now
            existing.heartbeat_at = now
            _write_holds(holds)
        return existing


def release(
    codespace: str,
    tenant: str | None = None,
    *,
    unpin: bool = False,
    ttl: float = DEFAULT_TTL,
) -> OwnerHold | None:
    """Drop ``tenant`` (and/or unpin) from the hold on ``codespace``.

    Returns the updated hold, or ``None`` if the hold no longer has any reason to
    persist (no live tenants and not pinned) and was removed -- the signal a
    later increment uses to tear the connection down. Releasing an unknown
    tenant / codespace is a no-op.
    """
    with _owner_lock():
        holds = _prune(_read_holds(), ttl)
        existing = holds.get(codespace)
        if existing is None:
            return None
        if tenant is not None:
            existing.tenants.pop(tenant, None)
        if unpin:
            existing.pinned = False
        existing.heartbeat_at = time.time()
        if existing.should_hold(ttl):
            _write_holds(holds)
            return existing
        del holds[codespace]
        _write_holds(holds)
        return None
