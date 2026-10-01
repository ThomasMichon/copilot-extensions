"""Connection Owner support for detached venue CLI-mode sessions.

A detached ``agent-codespaces copilot <name> --detach`` session keeps running
in a CodeSpace mux session after its launcher exits. To stay registered with,
heartbeating to, and messageable through the *host* agent-bridge daemon it
needs two reverse forwards to outlive the launcher: the credential relay
(already the Connection Owner's job) and the host bridge daemon's own API port.

:class:`SessionForwards` is the Owner's optional extension for that:

* it keeps one self-healing reverse forward of the host bridge daemon per hold
  that asks for one (``OwnerHold.daemon_port``) -- the CodeSpace-side listen
  port stays fixed while the host side follows the daemon's *live* port, so a
  host bridge restart (which rebinds a fresh ephemeral port) does not strand
  the session; and
* it renews or releases **session tenants** from the Owner's own venue probe
  (does the recorded mux session still exist? is the CodeSpace still
  Available?) instead of any bridge/session state -- the Owner stays
  transport-only, and never wakes a CodeSpace that was stopped; and
* on that same probe, while the session is running, it checks the bridge
  forward actually serves (an authenticated round trip from the CodeSpace) and
  rebuilds one whose ssh process is alive but no longer forwards.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .connection_owner import (
    DEFAULT_TTL,
    OwnerHold,
    RelayChannel,
    _live_snapshot,
    heartbeat,
    release,
)
from ._ssh_retry import exec_with_retry

log = logging.getLogger("agent-codespaces")

# Build a (not-yet-started) reverse forward into a CodeSpace:
# ``(codespace, codespace_listen_port[, host_port]) -> channel``. Without
# ``host_port`` the host side follows the bridge daemon's live port; with it,
# the host side is that fixed loopback port. Same channel shape as the relay
# (``ssh_manager.SupervisedRelayForward`` is port-generic).
DaemonForwardFactory = Callable[..., RelayChannel]

# Build a (not-yet-started) local forward from this host into a CodeSpace:
# ``(codespace, host_port, venue_port) -> channel`` that listens on host
# ``127.0.0.1:host_port`` and connects to the CodeSpace's ``127.0.0.1:venue_port``
# (for example a worker's dev server that a host browser must load).
LocalForwardFactory = Callable[[str, int, int], RelayChannel]

# Probe a CodeSpace for its session tenants' mux sessions:
# ``(codespace, [mux_session, ...]) -> {mux_session: True|False|None}`` where
# True = still running (renew), False = provably gone or the CodeSpace is no
# longer Available (release -- never wake a stopped box), None = unknown
# (neither renew nor release; the tenant TTL is the backstop).
SessionProbe = Callable[[str, list[str]], Awaitable[dict[str, bool | None]]]

# Check a CodeSpace's host-bridge forward end to end:
# ``(codespace, codespace_listen_port) -> True`` (it serves), ``False`` (it
# doesn't connect or answer: rebuild it), or ``None`` (unknown: leave it).
BridgeProbe = Callable[[str, int], Awaitable["bool | None"]]

# Mirror a CodeSpace's running transcripts to this host (``transcript_mirror``).
TranscriptMirrorFn = Callable[[str], Awaitable[Any]]

# How often (seconds) the Owner probes a CodeSpace's session tenants.
DEFAULT_SESSION_PROBE_INTERVAL = 120.0
# An otherwise idle Owner stays up this long to retry an owed transcript push.
OWED_PUSH_GRACE_SECONDS = 3600.0
LOCAL_REBIND_FAILURES_BEFORE_REASSIGN = 2


class SessionForwards:
    """Host-bridge daemon forwards + session-tenant renewal for the Owner."""

    def __init__(
        self,
        daemon_factory: DaemonForwardFactory,
        session_probe: SessionProbe | None = None,
        *,
        ttl: float = DEFAULT_TTL,
        probe_interval: float = DEFAULT_SESSION_PROBE_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        local_factory: LocalForwardFactory | None = None,
        bridge_probe: BridgeProbe | None = None,
        transcript_mirror: TranscriptMirrorFn | None = None,
    ) -> None:
        self._daemon_factory = daemon_factory
        self._bridge_probe = bridge_probe
        self._mirror = transcript_mirror
        self._mirroring: dict[str, asyncio.Task[Any]] = {}
        self._last_owed_push: dict[str, float] = {}
        # CodeSpaces a probe last saw running (when): their full passes push
        # anything owed, so owed-only pushes leave them alone.
        self._live_seen: dict[str, float] = {}
        self._local_factory = local_factory
        self._probe = session_probe
        self._ttl = ttl
        self._probe_interval = probe_interval
        self._clock = clock
        self._channels: dict[str, tuple[int, RelayChannel]] = {}
        self._extra: dict[tuple[str, int], tuple[int, RelayChannel]] = {}
        self._local: dict[tuple[str, int], tuple[int, RelayChannel]] = {}
        self._local_failures: dict[tuple[str, int], int] = {}
        self._last_probe: dict[str, float] = {}

    def active(self) -> dict[str, int]:
        """CodeSpace -> listen port of each currently-live daemon forward."""
        return {cs: port for cs, (port, ch) in self._channels.items() if ch.is_alive}

    def active_reverse_forwards(self) -> dict[str, dict[int, int]]:
        """CodeSpace -> {venue port: host port} of each live extra reverse forward."""
        out: dict[str, dict[int, int]] = {}
        for (cs, venue), (host, ch) in self._extra.items():
            if ch.is_alive:
                out.setdefault(cs, {})[venue] = host
        return out

    def active_local_forwards(self) -> dict[str, dict[int, int]]:
        """CodeSpace -> {host port: venue port} of each live local forward."""
        out: dict[str, dict[int, int]] = {}
        for (cs, host), (venue, ch) in self._local.items():
            if ch.is_alive:
                out.setdefault(cs, {})[host] = venue
        return out

    async def _ensure(self, label: str, channel: RelayChannel) -> bool:
        """Start ``channel`` if it is not alive; False when the start failed (retried next cycle)."""
        if channel.is_alive:
            return True
        try:
            await channel.start()
            return True
        except Exception as exc:
            log.warning("Connection Owner: failed to ensure %s: %s", label, exc)
            try:
                await channel.stop()
            except Exception:
                log.debug("%s stop after failed start also failed", label)
            return False

    async def reconcile(self, holds: dict[str, OwnerHold]) -> None:
        """Start/stop forwards so each hold has exactly its daemon + extra reverse forwards."""
        self._start_owed_pushes()
        for codespace, (port, channel) in list(self._channels.items()):
            hold = holds.get(codespace)
            if hold is None or hold.daemon_port != port:
                self._channels.pop(codespace, None)
                await channel.stop()
        for codespace, hold in holds.items():
            if not hold.daemon_port:
                continue
            entry = self._channels.get(codespace)
            if entry is None:
                entry = (hold.daemon_port, self._daemon_factory(codespace, hold.daemon_port))
                self._channels[codespace] = entry
            if not await self._ensure(f"bridge forward for {codespace}", entry[1]):
                self._channels.pop(codespace, None)
        await self._reconcile_extra(holds)
        await self._reconcile_local(holds)

    async def _reconcile_local(self, holds: dict[str, OwnerHold]) -> None:
        wanted = {
            (cs, int(host)): venue
            for cs, hold in holds.items()
            for host, venue in (getattr(hold, "local_forwards", None) or {}).items()
        }
        dynamic = {
            (cs, int(host))
            for cs, hold in holds.items()
            for host, venue in (getattr(hold, "assigned_local_forwards", None) or {}).items()
            if wanted.get((cs, int(host))) == venue
        }
        fixed_hosts = {
            int(host)
            for cs, hold in holds.items()
            for host in (getattr(hold, "local_forwards", None) or {})
            if (cs, int(host)) not in dynamic and int(host) != 0
        }
        for key, (venue, channel) in list(self._local.items()):
            if wanted.get(key) != venue or self._local_factory is None:
                self._local.pop(key, None)
                self._local_failures.pop(key, None)
                await channel.stop()
        if self._local_factory is None:
            from .owner_local_forwards import write_active_local_forwards

            write_active_local_forwards({})
            return
        for key, venue in wanted.items():
            entry = self._local.get(key)
            if entry is None:
                entry = (venue, self._local_factory(key[0], key[1], venue))
                self._local[key] = entry
            if not await self._ensure(f"local forward {key[1]}->{venue} for {key[0]}", entry[1]):
                self._local.pop(key, None)
                if key in dynamic and self._note_local_failure(key):
                    await self._reassign_dynamic_local_forward(key, venue, entry[1], fixed_hosts)
                continue
            self._local_failures.pop(key, None)
            if not self._record_assigned_local_port(key, venue, entry[1], fixed_hosts):
                self._local.pop(key, None)
                await self._stop_untracked_local_forward(entry[1])
        from .owner_local_forwards import write_active_local_forwards

        write_active_local_forwards(self.active_local_forwards())

    def _note_local_failure(self, key: tuple[str, int]) -> bool:
        count = self._local_failures.get(key, 0) + 1
        self._local_failures[key] = count
        return count >= LOCAL_REBIND_FAILURES_BEFORE_REASSIGN

    async def _reassign_dynamic_local_forward(
        self, key: tuple[str, int], venue: int, old_channel: RelayChannel, fixed_hosts: set[int],
    ) -> None:
        if self._local_factory is None:
            return
        try:
            await old_channel.stop()
        except Exception:
            log.debug("old dynamic local forward stop failed for %s:%s", key[0], key[1])
        entry = (venue, self._local_factory(key[0], 0, venue))
        if not await self._ensure(f"replacement local forward 0->{venue} for {key[0]}", entry[1]):
            await self._stop_untracked_local_forward(entry[1])
            return
        assigned = _bound_local_port(entry[1])
        if assigned is None:
            await self._stop_untracked_local_forward(entry[1])
            return
        if assigned in fixed_hosts:
            await self._stop_untracked_local_forward(entry[1])
            return
        from .owner_local_forwards import reassign_dynamic_local_forward

        if reassign_dynamic_local_forward(
            key[0], old_host_port=key[1], assigned_host_port=assigned, venue_port=venue,
        ):
            log.warning(
                "Connection Owner: dynamic local forward %s:%s was unavailable; reassigned to %s",
                key[0], key[1], assigned,
            )
            self._local_failures.pop(key, None)
            self._local[(key[0], assigned)] = entry
        else:
            await self._stop_untracked_local_forward(entry[1])

    async def _stop_untracked_local_forward(self, channel: RelayChannel) -> None:
        try:
            await channel.stop()
        except Exception:
            log.debug("untracked local forward stop failed")

    def _record_assigned_local_port(
        self, key: tuple[str, int], venue: int, channel: RelayChannel, fixed_hosts: set[int],
    ) -> bool:
        if key[1] != 0:
            return True
        assigned = _bound_local_port(channel)
        if assigned is None:
            return False
        if assigned in fixed_hosts:
            return False
        from .owner_local_forwards import record_assigned_local_forward

        if record_assigned_local_forward(
            key[0], requested_host_port=0, assigned_host_port=assigned, venue_port=venue,
        ):
            self._local.pop(key, None)
            self._local[(key[0], assigned)] = (venue, channel)
            return True
        return False

    async def _reconcile_extra(self, holds: dict[str, OwnerHold]) -> None:
        wanted = {
            (cs, int(venue)): host
            for cs, hold in holds.items()
            for venue, host in (hold.reverse_forwards or {}).items()
        }
        for key, (host, channel) in list(self._extra.items()):
            if wanted.get(key) != host:
                self._extra.pop(key, None)
                await channel.stop()
        for key, host in wanted.items():
            entry = self._extra.get(key)
            if entry is None:
                entry = (host, self._daemon_factory(key[0], key[1], host))
                self._extra[key] = entry
            if not await self._ensure(f"reverse forward {key[1]}->{host} for {key[0]}", entry[1]):
                self._extra.pop(key, None)

    async def probe(self, holds: list[OwnerHold]) -> None:
        """Renew/release session tenants from the venue probe (rate-limited per CodeSpace)."""
        if self._probe is None:
            self._start_owed_pushes()
            return
        now = self._clock()
        for hold in holds:
            if not hold.sessions:
                self._last_probe.pop(hold.codespace, None)
                continue
            last = self._last_probe.get(hold.codespace)
            if last is not None and now - last < self._probe_interval:
                continue
            self._last_probe[hold.codespace] = now
            by_mux: dict[str, list[tuple[str, bool, str | None]]] = {}
            for tenant, meta in hold.sessions.items():
                by_mux.setdefault(meta["mux_session"], []).append(
                    (tenant, bool(meta.get("confirmed")), meta.get("generation")),
                )
            try:
                verdicts = await self._probe(hold.codespace, sorted(by_mux))
            except Exception as exc:
                log.warning("Connection Owner: session probe for %s failed: %s", hold.codespace, exc)
                continue
            if any(v is True for v in verdicts.values()):
                # Only while a session provably runs there: the CodeSpace is
                # Available, so this never wakes a stopped box.
                await self._check_bridge(hold.codespace)
                self._live_seen[hold.codespace] = now
                if not self._start_mirror(hold.codespace):
                    # Its slot was busy: probe again next tick, so the pass only
                    # ever starts on a fresh proof that the session runs (a
                    # remote read must never wake a box that has since stopped).
                    self._last_probe.pop(hold.codespace, None)
            for mux, tenants in by_mux.items():
                verdict = verdicts.get(mux)
                for tenant, confirmed, generation in tenants:
                    if verdict is True:
                        heartbeat(hold.codespace, tenant, self._ttl)
                    elif verdict is False and confirmed:
                        log.info(
                            "Connection Owner: session %s on %s is gone (or the "
                            "CodeSpace is no longer Available); releasing tenant %s",
                            mux, hold.codespace, tenant,
                        )
                        release(hold.codespace, tenant, ttl=self._ttl, generation=generation)
        self._start_owed_pushes()

    def _start_mirror(self, codespace: str) -> bool:
        """Mirror ``codespace``'s transcripts in the background (one pass at a
        time). False when another task holds its slot (nothing started)."""
        if self._mirror is None:
            return True
        return self._start_mirror_task(codespace, self._mirror, "transcript mirror")

    def owed_grace(self) -> float:
        """How long an otherwise idle Owner stays up to retry owed transcript
        pushes (and prunes); 0 when none is owed. Bounded, so a hub that stays
        down (or a disabled sync) never pins it resident: the markers persist,
        and the next Owner start resumes them."""
        owed = getattr(self._mirror, "owed_codespaces", None)
        try:
            return OWED_PUSH_GRACE_SECONDS if callable(owed) and owed() else 0.0
        except Exception:
            return 0.0

    def _start_owed_pushes(self) -> None:
        """Retry dirty host-side transcript pushes without probing any CodeSpace."""
        mirror = self._mirror
        owed = getattr(mirror, "owed_codespaces", None)
        push_owed = getattr(mirror, "push_owed", None)
        if not callable(owed) or not callable(push_owed):
            return
        try:
            codespaces = owed()
        except Exception as exc:
            log.debug("transcript mirror owed-push listing failed: %s", exc)
            return
        now = self._clock()
        for codespace in codespaces:
            seen = self._live_seen.get(codespace)
            if seen is not None and now - seen < 2 * self._probe_interval:
                continue  # its full passes push what's owed (even when a read fails)
            last = self._last_owed_push.get(codespace)
            if last is not None and now - last < self._probe_interval:
                continue
            if self._start_mirror_task(codespace, push_owed, "transcript mirror owed push"):
                self._last_owed_push[codespace] = now

    def _start_mirror_task(
        self,
        codespace: str,
        runner: Callable[[str], Awaitable[Any]],
        label: str,
    ) -> bool:
        """Start one mirror-related background task for ``codespace`` if none is running."""
        running = self._mirroring.get(codespace)
        if running is not None and not running.done():
            return False

        async def run() -> None:
            try:
                await runner(codespace)
            except Exception as exc:
                log.debug("%s on %s failed: %s", label, codespace, exc)

        self._mirroring[codespace] = asyncio.get_running_loop().create_task(run())
        return True

    async def _check_bridge(self, codespace: str) -> None:
        """Rebuild ``codespace``'s bridge forward when it no longer serves.

        Its ssh process can outlive the forward (a transport reset that leaves
        the connection up): the channel still reads alive, so nothing restarts
        it, and every session there loses the bridge. Dropping it here lets the
        next reconcile build a fresh one."""
        entry = self._channels.get(codespace)
        if self._bridge_probe is None or entry is None or not entry[1].is_alive:
            return
        port, channel = entry
        try:
            serving = await self._bridge_probe(codespace, port)
        except Exception as exc:
            log.debug("bridge probe on %s failed: %s", codespace, exc)
            return
        if serving is False and self._channels.get(codespace) is entry:
            log.warning(
                "Connection Owner: the bridge forward for %s is up but not serving; rebuilding it",
                codespace,
            )
            self._channels.pop(codespace, None)
            await channel.stop()

    async def shutdown(self) -> None:
        """Stop every daemon and extra forward (Owner shutdown). The registry is untouched."""
        for task in self._mirroring.values():
            task.cancel()
        self._mirroring.clear()
        for codespace, (_port, channel) in list(self._channels.items()):
            self._channels.pop(codespace, None)
            await channel.stop()
        for key, (_host, channel) in list(self._extra.items()):
            self._extra.pop(key, None)
            await channel.stop()
        for key, (_venue, channel) in list(self._local.items()):
            self._local.pop(key, None)
            await channel.stop()
        from .owner_local_forwards import clear_active_local_forwards

        clear_active_local_forwards()


def owner_serves_bridge(codespace: str, now: float | None = None) -> bool:
    """True iff a live Owner currently has a host-bridge forward for ``codespace``."""
    live = _live_snapshot(now)
    return bool(codespace) and bool(live) and codespace in live.bridge_forwards


async def await_owner_bridge_forward(
    codespace: str, *, timeout: float = 60.0, poll: float = 0.5
) -> bool:
    """Wait up to ``timeout`` s for the live Owner's bridge forward to ``codespace``."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if owner_serves_bridge(codespace):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll)


def make_supervised_daemon_forward_factory(
    *,
    gh_env: dict | None = None,
    relay_cls: type | None = None,
    config_source_cls: type | None = None,
    port_resolver: Callable[[], int | None] | None = None,
) -> DaemonForwardFactory:
    """Build a :data:`DaemonForwardFactory` backed by ``SupervisedRelayForward``.

    The forward listens on the requested CodeSpace-side port (the port written
    into the CodeSpace's ``~/.agent-bridge/active.json`` at launch) and targets
    the host daemon's **live** port, re-resolved on every (re-)establish -- the
    daemon binds a fresh ephemeral port on each restart, so a session keeps
    registering across host bridge restarts. Same injectable seams as
    :func:`make_supervised_relay_factory`.
    """
    if relay_cls is None:
        from ssh_manager import SupervisedRelayForward

        relay_cls = SupervisedRelayForward
    if config_source_cls is None:
        from ssh_manager.codespace_source import CodespaceConfigSource

        config_source_cls = CodespaceConfigSource
    if port_resolver is None:
        from venue_copilot import resolve_daemon_port

        port_resolver = resolve_daemon_port

    def factory(codespace: str, listen_port: int, host_port: int | None = None) -> RelayChannel:
        ssh_config = config_source_cls(codespace, gh_env=gh_env).get_ssh_config()
        resolve = (lambda: host_port) if host_port else (lambda: port_resolver() or 0)
        return relay_cls(ssh_config, listen_port, host_port_resolver=resolve)

    return factory


class _LocalForwardChannel:
    """``ssh_manager.LocalForward`` in the Owner's channel shape (start/stop/is_alive).

    The Owner's reconcile loop restarts a channel that is not alive, so the
    forward heals after a transport drop without a monitor of its own.
    """

    def __init__(self, forward: Any) -> None:
        self._forward = forward

    @property
    def is_alive(self) -> bool:
        return bool(self._forward.is_alive)

    @property
    def bound_port(self) -> int | None:
        value = getattr(self._forward, "local_port", None)
        try:
            port = int(value)
        except (TypeError, ValueError):
            return None
        return port if 0 < port < 65536 else None

    async def start(self) -> None:
        if self.bound_port is not None:
            await self._forward.refresh()
        else:
            await self._forward.establish()

    async def stop(self) -> None:
        await self._forward.cancel()


def make_local_forward_factory(
    *,
    gh_env: dict | None = None,
    forward_cls: type | None = None,
    config_source_cls: type | None = None,
) -> LocalForwardFactory:
    """Build a :data:`LocalForwardFactory` backed by ``ssh_manager.LocalForward``.

    Each forward listens on the fixed host loopback port (never a fallback
    port: the caller told the worker and the browser that port) and connects
    to the CodeSpace's ``127.0.0.1:<venue_port>``. Same seams as
    :func:`make_supervised_daemon_forward_factory`.
    """
    if forward_cls is None:
        from ssh_manager.forward import LocalForward

        forward_cls = LocalForward
    if config_source_cls is None:
        from ssh_manager.codespace_source import CodespaceConfigSource

        config_source_cls = CodespaceConfigSource

    def factory(codespace: str, host_port: int, venue_port: int) -> RelayChannel:
        ssh_config = config_source_cls(codespace, gh_env=gh_env).get_ssh_config()
        return _LocalForwardChannel(forward_cls(ssh_config, venue_port, local_port=host_port))

    return factory


def _bound_local_port(channel: RelayChannel) -> int | None:
    for source in (channel, getattr(channel, "_forward", None)):
        if source is None:
            continue
        for attr in ("bound_port", "local_port"):
            value = getattr(source, attr, None)
            try:
                port = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < port < 65536:
                return port
    return None


async def _open_codespace(codespace: str) -> Any:
    """A connected ``ssh_manager.ConnectionManager`` for one short probe."""
    from ssh_manager import ConnectionManager

    from .codespace_config import CodespaceSource
    from .lifecycle import account_for_codespace

    manager = ConnectionManager()
    source = CodespaceSource(codespace, account=account_for_codespace(codespace))
    await manager.ensure_connected(codespace, source, [])
    return manager


#: ``curl`` exits meaning the forward didn't connect or answer (7 couldn't
#: connect, 28 timed out, 52 empty reply, 56 receive failure). An HTTP error
#: (22, e.g. a refused token) means it forwards fine: rebuilding won't help.
_FORWARD_BROKEN_EXITS = frozenset({7, 28, 52, 56})


def make_remote_bridge_probe(
    *, open_manager: Callable[[str], Awaitable[Any]] | None = None,
) -> BridgeProbe:
    """Build the Owner's default :data:`BridgeProbe`: the launch's own
    authenticated probe (``venue_copilot.bridge_probe_script``), run from the
    CodeSpace over a short-lived exec channel. Called only for a CodeSpace
    whose session the mux probe just saw running, so it never wakes one."""
    opener = open_manager or _open_codespace

    async def probe(codespace: str, port: int) -> bool | None:
        from venue_copilot import bridge_probe_script

        manager = None
        try:
            manager = await opener(codespace)
            result = await exec_with_retry(
                manager, codespace, "bash -lc " + shlex.quote(bridge_probe_script(port)),
                timeout=30.0, attempts=2,
            )
            code = getattr(result, "exit_code", None)
            if code == 0:
                return True
            return False if code in _FORWARD_BROKEN_EXITS else None
        except Exception as exc:
            log.debug("bridge probe on %s failed: %s", codespace, exc)
            return None
        finally:
            if manager is not None:
                try:
                    await manager.disconnect(codespace)
                except Exception:
                    pass

    return probe


def make_remote_mux_probe(
    *,
    list_codespaces: Callable[[], Any] | None = None,
    open_manager: Callable[[str], Awaitable[Any]] | None = None,
) -> SessionProbe:
    """Build the Owner's default :data:`SessionProbe`.

    1. The CodeSpace must be listed as ``Available`` -- a stopped or
       shutting-down CodeSpace releases its session tenants (answering
       ``False``) *without* connecting, because an SSH connect would boot it
       back up. One missing from the listing is unknown (a per-account listing
       can fail partially); its tenants then lapse by TTL.
    2. Otherwise each mux session is checked with ``tmux has-session`` over a
       short-lived exec channel: exit 0 -> running, 1 -> gone, anything else /
       any transport error -> unknown (``None``).
    """
    if list_codespaces is None:
        from .lifecycle import list_codespaces as _list

        list_codespaces = _list

    opener = open_manager or _open_codespace

    async def probe(codespace: str, mux_sessions: list[str]) -> dict[str, bool | None]:
        import shlex

        unknown: dict[str, bool | None] = {m: None for m in mux_sessions}
        try:
            states = {cs.name: str(cs.state) for cs in list_codespaces()}
        except Exception as exc:
            log.debug("session probe: listing CodeSpaces failed: %s", exc)
            return unknown
        state = states.get(codespace)
        if state is None:
            # Absent from a (possibly partial, per-account) listing is not
            # proof it is gone; the tenant's TTL still bounds it.
            return unknown
        if state.lower() != "available":
            return {m: False for m in mux_sessions}
        manager = None
        try:
            manager = await opener(codespace)
            verdicts: dict[str, bool | None] = {}
            for mux in mux_sessions:
                result = await exec_with_retry(
                    manager,
                    codespace,
                    "bash -lc " + shlex.quote(f"tmux has-session -t {shlex.quote('=' + mux)}"),
                    timeout=30.0,
                    attempts=2,
                )
                code = getattr(result, "exit_code", None)
                verdicts[mux] = True if code == 0 else (False if code == 1 else None)
            return verdicts
        except Exception as exc:
            log.debug("session probe on %s failed: %s", codespace, exc)
            return unknown
        finally:
            if manager is not None:
                try:
                    await manager.disconnect(codespace)
                except Exception:
                    pass

    return probe
