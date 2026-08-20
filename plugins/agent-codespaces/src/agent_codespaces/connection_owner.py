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

import asyncio
import json
import logging
import os
import platform
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Protocol

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


# ---------------------------------------------------------------------------
# Liveness beacon (tenant-defer prerequisite, dotfiles#1345)
# ---------------------------------------------------------------------------
#
# The registry above records *intent* (which CodeSpaces should be held). The
# beacon below records that a Connection Owner daemon is actually **running** and
# reconciling on this machine. A tenant (a one-off ``agent-codespaces ssh``, an
# agent-bridge dispatch) may only defer its relay to the Owner when the Owner is
# live -- otherwise it would skip standing up its own relay and be left with no
# credential auth. The daemon refreshes the beacon every reconcile cycle and
# removes it on clean shutdown; a crashed daemon leaves a beacon that ages out
# within a few intervals, so :func:`is_owner_live` fails safe (the tenant falls
# back to owning its own relay). Nothing consumes this yet -- it is additive; the
# consumer rewire (making ssh/dispatch defer) is the next increment.

LIVE_FILE = RUNTIME_DIR / "connection-owner.live.json"

# Treat the Owner as live only if its beacon was refreshed within this many
# reconcile intervals (with a floor so a very fast interval can't make liveness
# flap on scheduler jitter).
_LIVE_STALE_INTERVALS = 3
_LIVE_STALE_FLOOR = 45.0
# A heartbeat timestamp this far in the future is treated as NOT fresh: a bogus
# future beacon or a backward clock jump must fail safe (a tenant falls back to
# owning its own relay), while a sub-second skew is tolerated so liveness does not
# flap on ordinary clock jitter.
_LIVE_FUTURE_TOLERANCE = 5.0


@dataclass
class OwnerLiveness:
    """A snapshot of the running Connection Owner daemon's liveness beacon."""

    pid: int
    host: str
    heartbeat_at: float
    interval: float
    # CodeSpaces the Owner currently has a *live* relay channel for (published so
    # a tenant can tell the relay it wants to defer to is actually up before it
    # skips standing up its own -- the timing seam the ssh/dispatch rewire needs).
    active: tuple[str, ...] = ()

    def staleness_threshold(self) -> float:
        return max(_LIVE_STALE_FLOOR, _LIVE_STALE_INTERVALS * max(self.interval, 0.0))

    def is_fresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        age = now - self.heartbeat_at
        if age < 0:
            # Future heartbeat (bogus beacon / backward clock jump): fail safe --
            # only a sub-second skew within tolerance still counts as fresh.
            return age >= -_LIVE_FUTURE_TOLERANCE
        return age <= self.staleness_threshold()


def _write_liveness(interval: float, active: Iterable[str] | None = None) -> None:
    """Refresh the daemon liveness beacon (best-effort; never raises).

    ``active`` is the set of CodeSpaces the Owner currently has a live relay
    channel for; it is published so a tenant can wait for the relay it wants
    before deferring.
    """
    try:
        ensure_runtime_dir()
        payload = {
            "pid": os.getpid(),
            "host": _this_host(),
            "heartbeat_at": time.time(),
            "interval": float(interval),
            "active": sorted(active or ()),
        }
        tmp = LIVE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, LIVE_FILE)
    except Exception as exc:  # a beacon write must never crash the daemon
        log.debug("Connection Owner liveness beacon write failed: %s", exc)


def _clear_liveness() -> None:
    """Remove the daemon liveness beacon (best-effort; never raises)."""
    try:
        LIVE_FILE.unlink(missing_ok=True)
    except Exception as exc:
        log.debug("Connection Owner liveness beacon clear failed: %s", exc)


def read_liveness() -> OwnerLiveness | None:
    """Read the liveness beacon, or ``None`` if absent / malformed."""
    try:
        raw = json.loads(LIVE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    active_raw = raw.get("active")
    if not isinstance(active_raw, list):
        active_raw = []
    try:
        return OwnerLiveness(
            pid=int(raw.get("pid", 0)),
            host=str(raw.get("host", "")),
            heartbeat_at=float(raw.get("heartbeat_at", 0.0)),
            interval=float(raw.get("interval", 0.0)),
            active=tuple(str(cs) for cs in active_raw if isinstance(cs, str)),
        )
    except (TypeError, ValueError):
        return None


def _pid_alive(pid: int) -> bool | None:
    """Best-effort: is ``pid`` a live process? ``None`` when undeterminable.

    POSIX uses ``os.kill(pid, 0)``. On Windows ``os.kill`` with a non-control
    signal calls ``TerminateProcess`` (it would KILL the pid), so we never probe
    there -- return ``None`` and let heartbeat freshness be the sole signal.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return None


def is_owner_live(now: float | None = None) -> bool:
    """True if a Connection Owner daemon is running + reconciling on this machine.

    Fails safe: a missing / stale beacon, or a beacon whose pid is provably dead,
    reads as **not live** so a tenant falls back to owning its own relay.
    """
    live = read_liveness()
    if live is None:
        return False
    if not live.is_fresh(now):
        return False
    return _pid_alive(live.pid) is not False


def owner_active_codespaces(now: float | None = None) -> set[str]:
    """CodeSpaces the live Owner currently has a relay channel for.

    Empty when the Owner is not live (a stale/absent beacon's ``active`` list is
    meaningless), so callers fail safe.
    """
    if not is_owner_live(now):
        return set()
    live = read_liveness()
    return set(live.active) if live else set()


def owner_serves_relay(codespace: str, now: float | None = None) -> bool:
    """True iff a live Owner is currently serving ``codespace``'s relay.

    This is the gate a tenant checks before deferring: only when the Owner is
    live **and** already owns this CodeSpace's relay may the tenant skip standing
    up its own ``-R``.
    """
    return bool(codespace) and codespace in owner_active_codespaces(now)


# ---------------------------------------------------------------------------
# Connection Owner reconciler (increment 2)
# ---------------------------------------------------------------------------
#
# The registry above records *intent* (which CodeSpaces should be held). The
# reconciler below turns that intent into *live* credential-relay channels: it
# keeps exactly one relay channel per held CodeSpace and tears down channels for
# CodeSpaces no longer held. The relay transport is supplied by an injected
# ``factory`` so this stays additive + unit-testable; a later increment wires the
# real ``ssh_manager.SupervisedRelayForward`` and a daemon loop that periodically
# (and on hold/release) calls :meth:`ConnectionOwner.reconcile`.


class RelayChannel(Protocol):
    """The subset of ``ssh_manager.SupervisedRelayForward`` the Owner drives.

    ``start`` establishes the reverse-forward and starts the self-healing
    monitor; ``stop`` tears it down (idempotent); ``is_alive`` (a **property**,
    matching ``SupervisedRelayForward``) reports whether the underlying
    ``ssh -N -R`` process is currently running.
    """

    @property
    def is_alive(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


# Build a (not-yet-started) relay channel for a CodeSpace by name.
RelayFactory = Callable[[str], RelayChannel]


class ConnectionOwner:
    """Reconciles live credential-relay channels against the registry.

    One :class:`ConnectionOwner` per machine owns the set of relay channels. It
    is the single owner of each CodeSpace's relay (so the ``-R`` bind is
    single-owner by construction, dotfiles#561); agent-bridge dispatch and one-off
    ``agent-codespaces ssh`` are non-owning tenants that only ``hold``/``release``
    in the registry. Nothing in production constructs this yet (increment 2 is
    additive); a later increment supplies the real transport factory and runs the
    reconcile loop in a persistent daemon.
    """

    def __init__(self, factory: RelayFactory, *, ttl: float = DEFAULT_TTL) -> None:
        self._factory = factory
        self._ttl = ttl
        self._channels: dict[str, RelayChannel] = {}

    def active_codespaces(self) -> set[str]:
        """CodeSpaces with a currently-live relay channel under this Owner."""
        return {cs for cs, channel in self._channels.items() if channel.is_alive}

    async def ensure(self, codespace: str) -> bool:
        """Ensure a started relay channel for ``codespace`` iff it is held.

        Returns ``True`` when a channel is present (already running or freshly
        started), ``False`` when the CodeSpace is not held (any existing channel
        is stopped and dropped). If starting the channel fails, the half-created
        channel is dropped (so the next reconcile builds a fresh one) and the
        error propagates to the caller.
        """
        if not should_hold(codespace, self._ttl):
            await self.drop(codespace)
            return False
        channel = self._channels.get(codespace)
        if channel is None:
            channel = self._factory(codespace)
            self._channels[codespace] = channel
        if not channel.is_alive:
            try:
                await channel.start()
            except Exception:
                # start() may have spawned the ssh process before failing; stop it
                # best-effort so we don't leak a relay we've stopped tracking.
                try:
                    await channel.stop()
                except Exception:
                    log.debug("relay stop after failed start also failed for %s", codespace)
                self._channels.pop(codespace, None)
                raise
        return True

    async def drop(self, codespace: str) -> None:
        """Stop and forget the relay channel for ``codespace`` (idempotent)."""
        channel = self._channels.pop(codespace, None)
        if channel is not None:
            await channel.stop()

    async def reconcile(self) -> None:
        """Bring live channels in line with the registry: start held, stop unheld.

        Resilient per CodeSpace: a channel that fails to start is logged and
        dropped (retried next cycle) without aborting reconciliation of the rest.
        """
        held = {h.codespace for h in list_holds(self._ttl)}
        for codespace in list(self._channels):
            if codespace not in held:
                await self.drop(codespace)
        for codespace in held:
            try:
                await self.ensure(codespace)
            except Exception as exc:  # one bad CS must not stall the rest
                log.warning(
                    "Connection Owner: failed to ensure relay for %s: %s",
                    codespace, exc,
                )
                self._channels.pop(codespace, None)

    async def shutdown(self) -> None:
        """Stop all relay channels (daemon shutdown). The registry is untouched."""
        for codespace in list(self._channels):
            await self.drop(codespace)


# ---------------------------------------------------------------------------
# Real transport factory + daemon runner (increment 3)
# ---------------------------------------------------------------------------
#
# Increment 2 left the relay transport injected. Here we supply the real factory
# (backed by ssh_manager.SupervisedRelayForward) and the reconcile loop a
# persistent daemon runs. Both are additive + opt-in: nothing starts the daemon
# by default. Wiring a CLI entrypoint and actually running it on a machine
# (making the ssh/dispatch paths defer to the Owner) is the deploy-gated
# increment that follows (dotfiles#1345).


def make_supervised_relay_factory(
    config: Any,
    *,
    gh_env: dict | None = None,
    relay_cls: type | None = None,
    config_source_cls: type | None = None,
    port_resolver: Callable[[Any], int] | None = None,
) -> RelayFactory:
    """Build a :data:`RelayFactory` backed by ``ssh_manager.SupervisedRelayForward``.

    Per CodeSpace the factory resolves the CodeSpace SSH config
    (``CodespaceConfigSource`` -> ``gh codespace ssh --config``) and the host
    relay port (``relay_launch.effective_relay_port``) and constructs an
    **unstarted** ``SupervisedRelayForward`` -- mirroring the per-dispatch relay
    setup in ``__main__._start_supervised_relay``, but owned by the persistent
    Connection Owner rather than a single dispatch. The ``host_port_resolver``
    lets the ``-R`` target follow a relay that rebinds a new host port after a
    daemon restart (dotfiles#855). The transport / config-source classes and the
    port resolver are injectable so this is unit-testable without a real
    CodeSpace or an SSH subprocess.
    """
    if relay_cls is None:
        from ssh_manager import SupervisedRelayForward

        relay_cls = SupervisedRelayForward
    if config_source_cls is None:
        from ssh_manager.codespace_source import CodespaceConfigSource

        config_source_cls = CodespaceConfigSource
    if port_resolver is None:
        from .relay_launch import effective_relay_port

        port_resolver = effective_relay_port

    def factory(codespace: str) -> RelayChannel:
        ssh_config = config_source_cls(codespace, gh_env=gh_env).get_ssh_config()
        relay_port = port_resolver(config)
        return relay_cls(
            ssh_config,
            relay_port,
            host_port_resolver=lambda: port_resolver(config),
        )

    return factory


async def run_owner_daemon(
    owner: ConnectionOwner,
    *,
    interval: float = 15.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the Connection Owner reconcile loop until ``stop_event`` is set.

    Reconciles immediately, then every ``interval`` seconds, so the set of live
    relay channels tracks the registry (a tenant hold/release is reflected within
    one interval). A failed reconcile cycle is logged and the loop continues (a
    transient error must not kill the daemon). On exit -- normal stop or an
    unexpected error -- all channels are stopped (registry intent is untouched).
    Additive + opt-in: nothing starts this by default.
    """
    stop = stop_event if stop_event is not None else asyncio.Event()
    try:
        _write_liveness(interval, active=owner.active_codespaces())
        while not stop.is_set():
            try:
                await owner.reconcile()
            except Exception as exc:  # a bad cycle must not kill the daemon
                log.warning("Connection Owner reconcile cycle failed: %s", exc)
            # Refresh the beacon each cycle, publishing which CodeSpaces now have
            # a live relay channel so tenants can defer to them.
            _write_liveness(interval, active=owner.active_codespaces())
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except (TimeoutError, asyncio.TimeoutError):
                pass
    finally:
        _clear_liveness()
        await owner.shutdown()
