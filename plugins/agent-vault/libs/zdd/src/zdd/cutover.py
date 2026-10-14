"""Active/passive cutover orchestration for zero-downtime redeploys.

This is the headline of the zero-downtime effort: stand the **new** daemon up
beside the **old** one on a fresh port, confirm it is healthy, flip the routing
table so clients follow it, drain the old daemon's in-flight work, then retire
the old daemon -- with no client ever dialing a dead port and no active turn
hard-killed.

The orchestration is **app-level and OS-agnostic** (the effort's deliberate
conclusion: systemd and Windows Scheduled Tasks share almost no lifecycle
surface, so the drain/handoff logic must not live in the service manager). All
side-effecting collaborators -- spawning the passive daemon, health probing,
the HTTP client, free-port selection -- are injected, so the sequence and its
rollback are exercised by unit tests without real subprocesses. The thin CLI
(`agent-bridge deploy`) wires the real implementations.

Sequence (each step before the commit point is reversible)::

    1. resolve the current active endpoint (routing table)         [reversible]
    2. pick a free port for the new daemon                          [reversible]
    3. spawn the passive daemon (--passive: no self-route, no relay)[reversible]
    4. wait until the new daemon is healthy                         [reversible]
    5. flip the routing table -> new active, old demoted to previous[reversible]
    6. drain the old daemon (busy-oracle wait, optional force)      [reversible]
    -- COMMIT POINT --
    7. shut the old daemon down (clean exit; systemd won't resurrect)
    8. adopt the credential relay on the new daemon (best effort)

A failure anywhere before the commit point rolls back: re-publish the old
endpoint as active, undrain the old daemon, and terminate the freshly spawned
passive. After the commit point the new daemon is the sole survivor, so
remaining steps are best-effort and never roll back.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from zdd import breadcrumb, routing
from zdd.routing import Endpoint
from . import routing_persistence

log = logging.getLogger("zdd")

if TYPE_CHECKING:
    from .conditional import ConditionalCutover, PreparedCutover


class CutoverError(Exception):
    """A recoverable cutover failure that triggers rollback."""


class _Handle(Protocol):
    """Minimal surface the orchestrator needs from a spawned daemon process."""

    pid: int

    def terminate(self) -> None: ...

    def poll(self) -> int | None: ...


class _Client(Protocol):
    """Minimal HTTP surface used against the old/new daemons."""

    def health(self) -> dict[str, Any]: ...

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict[str, Any]: ...

    def undrain(self) -> dict[str, Any]: ...

    def shutdown(self) -> dict[str, Any]: ...

    def adopt_relay(self) -> dict[str, Any]: ...


@dataclass
class CutoverResult:
    """Outcome of a cutover attempt."""

    ok: bool
    new_port: int | None = None
    old_endpoint: Endpoint | None = None
    steps: list[str] = field(default_factory=list)
    rolled_back: bool = False
    committed: bool = False
    error: str | None = None
    drain: dict[str, Any] | None = None
    serving_endpoint: Endpoint | None = None
    retirement_requested: bool = False
    retirement_confirmed: bool = False
    conditional: bool = False
    publication_uncertain: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "new_port": self.new_port,
            "old_port": self.old_endpoint.port if self.old_endpoint else None,
            "steps": self.steps,
            "rolled_back": self.rolled_back,
            "committed": self.committed,
            "error": self.error,
            "drain": self.drain,
        }
        if self.conditional:
            data["retirement_confirmed"] = self.retirement_confirmed
            data["serving_endpoint"] = (
                self.serving_endpoint.to_dict() if self.serving_endpoint else None
            )
            data["retirement_requested"] = self.retirement_requested
        if self.publication_uncertain:
            data["publication_uncertain"] = True
        return data


class _NeverRaised(Exception):
    """Stands in for a refusal a routing module can't raise."""


class _UnguardedRouting(Exception):
    """A refusal hook was given, but the routing module can't publish guarded."""


class CutoverOrchestrator:
    """Drive one active/passive cutover. See module docstring for the sequence."""

    def __init__(
        self,
        config_dir: str | Path,
        *,
        bind: str,
        version: str | None,
        spawn_passive: Callable[[int], _Handle],
        health_check: Callable[[str, int], bool],
        make_client: Callable[[str], _Client],
        pick_free_port: Callable[[], int],
        refuse_old: Callable[[dict[str, Any] | None], str | None] | None = None,
        service: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        routing_mod: Any = routing,
    ) -> None:
        self.config_dir = Path(config_dir)
        self.bind = bind
        self.version = version
        self.spawn_passive = spawn_passive
        self.health_check = health_check
        self.make_client = make_client
        self.pick_free_port = pick_free_port
        self.refuse_old = refuse_old
        self.sleep = sleep
        self.clock = clock
        self.routing = routing_mod
        # Uniform lifecycle log context. ``service`` defaults to the config-dir
        # convention (``~/.agent-bridge`` -> ``agent-bridge``); ``node`` is the
        # source machine. Both are captured once for every emitted record.
        from zdd import lifecycle

        self.service = service or lifecycle.service_from_config_dir(self.config_dir)
        self._node = socket.gethostname()

    # -- lifecycle logging ---------------------------------------------------

    def _emit(
        self,
        action: str,
        outcome: str,
        *,
        port: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one durable cutover lifecycle record (fail-open).

        Kept entirely separate from ``result.steps`` and the control flow: it
        records *what happened* to a durable log so an aborted cutover (the
        "new daemon never bound" incident) is diagnosable after the fact,
        without ever perturbing the cutover it observes.
        """
        from zdd import lifecycle

        lifecycle.record(
            self.config_dir,
            action,
            service=self.service,
            outcome=outcome,
            version=self.version,
            port=port,
            node=self._node,
            detail=detail,
        )

    # -- helpers -------------------------------------------------------------

    def _client_host(self) -> str:
        if self.bind in ("0.0.0.0", "", None):
            return "127.0.0.1"
        if self.bind == "::":
            return "::1"
        return self.bind

    def _base_url(self, port: int) -> str:
        return f"http://{routing.format_authority(self._client_host(), port)}"

    def _await_health(
        self, port: int, timeout: float, poll: float,
        check: Callable[[str, int], bool] | None = None,
    ) -> bool:
        check = check or self.health_check
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                if check(self._client_host(), port):
                    return True
            except Exception:
                pass
            self.sleep(poll)
        # one last probe at the deadline
        try:
            return bool(check(self._client_host(), port))
        except Exception:
            return False

    def _await_exit(self, handle: _Handle, timeout: float = 10.0, poll: float = 0.2) -> bool:
        """True once ``handle`` has provably exited; False on timeout or no ``poll()``."""
        poll_fn = getattr(handle, "poll", None)
        if not callable(poll_fn):
            return False
        deadline = self.clock() + timeout
        while True:
            try:
                if poll_fn() is not None:
                    return True
            except Exception:  # noqa: BLE001 -- an unreadable handle is unconfirmed
                return False
            if self.clock() >= deadline:
                return False
            self.sleep(poll)

    def _raw_active(self) -> dict | None:
        """The routing table's own ``active`` row -- never healed to ``previous``."""
        table = self.routing.read_table(self.config_dir)
        active_raw = table.get("active") if isinstance(table, dict) else None
        return active_raw if isinstance(active_raw, dict) else None

    def _refuse_current_old(self, result: CutoverResult) -> bool:
        if self.refuse_old is None:
            return False
        refusal = self.refuse_old(self._raw_active())
        if not refusal:
            return False
        result.error = refusal
        result.steps.append(f"refused: {refusal}")
        return True

    # -- main ----------------------------------------------------------------

    def run(
        self,
        *,
        health_timeout: float = 60.0,
        drain_timeout: float = 300.0,
        force: bool = False,
        poll: float = 0.5,
        lock_timeout: float | None = None,
        conditional: ConditionalCutover | None = None,
    ) -> CutoverResult:
        """Drive one cutover attempt, serialized against concurrent attempts.

        Two invocations against the same ``config_dir`` (two operators, a
        ``restart`` racing an installer-driven ``deploy``, or a fast second
        update triggered while a first is still draining) must never run this
        sequence at once -- both would read the same predecessor breadcrumb/
        routing state and race. A lock
        (:class:`zdd.cutover_lock.CutoverLock`) scoped to exactly this call
        serializes them -- but a *contended* lock is not refused outright:
        the second caller **waits** (bounded by ``lock_timeout``, which
        defaults to ``health_timeout + drain_timeout + 60`` -- generous
        enough to cover a full cutover it may be queued behind) so a
        legitimate back-to-back trigger (a fast second update superseding a
        first) still succeeds once the first completes, instead of being
        flatly rejected. Only exhausting the full wait without ever
        acquiring the lock is reported as a failure -- and even then the
        same way a caller already handles every other ``CutoverResult``
        failure (``ok=False``, a populated ``error``), not raised -- see the
        Phase 2 checklist in ``efforts/active/agent-bridge-unified-zdd-cutover``.
        """
        from zdd.cutover_lock import CutoverLock, CutoverLockedError

        if lock_timeout is None:
            lock_timeout = health_timeout + drain_timeout + 60.0

        try:
            lock = CutoverLock(self.config_dir)
            lock.acquire(timeout=lock_timeout)
            try:
                record = breadcrumb.read_breadcrumb(self.config_dir)
                reason = breadcrumb.recovery_refusal(self.config_dir, record)
                if conditional is None and reason is not None:
                    log.error("Ordinary cutover refused: %s", reason)
                    return CutoverResult(ok=False, error=reason, steps=[f"refused: {reason}"])
                prepared = None
                if conditional is not None:
                    from .conditional import PreparedCutover

                    try:
                        if force:
                            raise CutoverError("conditional repair cannot force predecessor retirement")
                        prepared = PreparedCutover(self, conditional)
                    except (CutoverError, routing.ActivePublicationRefused, routing.RoutingLockTimeout) as exc:
                        log.warning("Conditional cutover refused: %s", exc)
                        return CutoverResult(
                            ok=False, error=str(exc), steps=[f"refused: {exc}"], conditional=True,
                        )
                    except Exception as exc:
                        reason = f"conditional preparation failed ({type(exc).__name__})"
                        log.error(reason)
                        return CutoverResult(
                            ok=False, error=reason, steps=[reason], conditional=True,
                        )
                return self._run_locked(
                    health_timeout=health_timeout,
                    drain_timeout=drain_timeout,
                    force=force,
                    poll=poll,
                    conditional=prepared,
                )
            finally:
                lock.release()
        except CutoverLockedError as exc:
            result = CutoverResult(ok=False, error=str(exc), conditional=conditional is not None)
            result.steps.append(f"refused: {exc}")
            log.warning("Cutover refused -- already in progress: %s", exc)
            return result

    def _run_locked(
        self,
        *,
        health_timeout: float = 60.0,
        drain_timeout: float = 300.0,
        force: bool = False,
        poll: float = 0.5,
        conditional: PreparedCutover | None = None,
    ) -> CutoverResult:
        from zdd import lifecycle

        result = CutoverResult(ok=False, conditional=conditional is not None)
        publication_witness = None
        new_process_start_time = None
        def write_breadcrumb(**kwargs: Any) -> dict:
            if conditional:
                kwargs["state"] = "conditional-" + kwargs["state"]
            kwargs["publication_witness"] = publication_witness
            kwargs["new_process_start_time"] = new_process_start_time
            return breadcrumb.write_breadcrumb(self.config_dir, **kwargs)

        spawn_passive = conditional.spawn if conditional else self.spawn_passive
        health_check = conditional.healthy if conditional else self.health_check
        make_client = conditional.snapshot.make_client if conditional else self.make_client
        if self._refuse_current_old(result):
            return result
        # Dead-port watchdog: before standing up the new daemon, retire any
        # advertised-but-dead endpoint a previously-aborted cutover may have left
        # behind (the state that wedged the pipeline). Best-effort -- it only acts
        # when the active endpoint has no listener *and* a dead pid, so a healthy
        # or still-starting daemon is never disturbed. Logs its own reap.
        if conditional is None:
            try:
                self.routing.reap_stale_active(self.config_dir, service=self.service)
            except Exception:  # noqa: BLE001 -- watchdog is best-effort, never fatal
                pass
        if self._refuse_current_old(result):
            return result
        # The CAS expectation is the raw ``active`` row only: a clean shutdown
        # leaves active absent with the old claim demoted to ``previous``, and
        # the guarded publish compares against the raw row. Only the guarded
        # flip uses it, so an unguarded routing stand-in needs no read_table.
        old_for_cas = None
        if conditional is not None:
            old_for_cas = conditional.old
        elif self.refuse_old is not None:
            raw_active = self._raw_active()
            old_for_cas = Endpoint.from_dict(raw_active) if raw_active else None
        old = conditional.old if conditional else self.routing.read_active_endpoint(self.config_dir)
        result.old_endpoint = old

        new_port = self.pick_free_port()
        result.new_port = new_port
        if conditional and new_port == conditional.old.port:
            result.error = "candidate port must differ from predecessor"
            result.steps.append(f"refused: {result.error}")
            return result
        if self.routing is routing and conditional is None:
            try:
                publication_witness = routing.publication_witness(
                    self.config_dir, deadline=time.monotonic() + 5.0,
                )
            except (routing.ActivePublicationRefused, OSError, ValueError) as exc:
                result.error = f"native publication custody unavailable ({type(exc).__name__})"
                result.steps.append(f"refused: {result.error}")
                log.error(result.error)
                return result

        # Durable breadcrumb (#1756): written *before* the drain gate is ever
        # touched, so if this orchestrator dies mid-cutover the aborted attempt
        # leaves an attributable trace on disk (and recover_stale_cutover can
        # undrain the stranded old daemon). started_at is threaded through every
        # later update so the record keeps its original timestamp.
        old_dict = (
            old.to_dict() if conditional and old is not None else
            {"bind": old.bind, "port": old.port} if old is not None else None
        )
        started_at = write_breadcrumb(
            state="started", old=old_dict, new_port=new_port,
        )["started_at"]
        self._emit(
            lifecycle.CUTOVER_BEGIN, lifecycle.BEGIN, port=new_port,
            detail={"old_port": old.port if old is not None else None},
        )

        try:
            handle = spawn_passive(new_port)
            if conditional:
                publication_witness = conditional.publication_witness
        except Exception as exc:
            if conditional is None:
                raise
            result.error = f"conditional spawn failed ({type(exc).__name__})"
            result.steps.append(result.error)
            log.error(result.error)
            write_breadcrumb(
                state="started", old=old_dict, new_port=new_port,
                error=result.error, started_at=started_at,
            )
            return result
        new_pid = getattr(handle, "pid", None)
        if type(new_pid) is int and new_pid > 0:
            from .diagnostics import process_start_time

            new_process_start_time = process_start_time(new_pid)
        result.steps.append(f"spawned passive pid={new_pid if new_pid is not None else '?'} "
                            f"port={new_port}")
        # Record the passive's own pid the moment it is known -- before the
        # health gate, flip, or drain even begin -- so a crash anywhere past
        # this point (including one that never reaches the flip) leaves a
        # breadcrumb naming the passive to reap if it is never promoted
        # (#5195, reap_abandoned_passive). Threaded through every later update
        # like started_at.
        flipped = False
        try:
            if isinstance(new_pid, int):
                write_breadcrumb(
                    state="started", old=old_dict,
                    new_port=new_port, new_pid=new_pid, started_at=started_at,
                )
            if not self._await_health(new_port, health_timeout, poll, health_check):
                # The durable signal that is otherwise missing: a record that
                # the new daemon never bound its port -- the failure that, left
                # untraced, strands a client re-dialing a dead advertised port.
                self._emit(
                    lifecycle.CUTOVER_NEW_BOUND, lifecycle.FAIL, port=new_port,
                    detail={"health_timeout": health_timeout},
                )
                raise CutoverError(
                    f"new daemon did not become healthy on port {new_port} "
                    f"within {health_timeout:.0f}s"
                )
            result.steps.append("new daemon healthy")
            self._emit(
                lifecycle.CUTOVER_NEW_BOUND, lifecycle.OK, port=new_port,
                detail={"pid": getattr(handle, "pid", None)},
            )

            # Flip the route: new active, old demoted to previous. From here a
            # new CLI resolution lands on the new daemon; long-lived sockets stay
            # on the old one until their turn completes (migrate at a breakpoint).
            # Without ``refuse_old`` this is exactly the plain publish it always
            # was (a caller's routing stand-in may override ``publish_active``,
            # e.g. to promote its passive first). With it, the publish is
            # guarded: the current route is re-checked under the routing lock
            # that writes the new one -- and only a routing object that itself
            # defines the guarded publish can do that (``getattr_static``, so a
            # stand-in forwarding unknown names to zdd.routing doesn't count);
            # otherwise the cutover refuses rather than flip unguarded.
            refused = _NeverRaised
            try:
                if self.refuse_old is None and conditional is None:
                    self.routing.publish_active(
                        self.config_dir, bind=self.bind, port=new_port,
                        pid=getattr(handle, "pid", None), version=self.version,
                        demote_existing=True,
                    )
                else:
                    import inspect

                    if inspect.getattr_static(
                        self.routing, "publish_active_with_previous_guarded", None,
                    ) is None:
                        raise _UnguardedRouting(
                            "the routing module can't publish guarded; refusing to flip "
                            "a route that must be re-checked first"
                        )
                    refused = self.routing.ActivePublicationRefused
                    conditional_kwargs = {}
                    if conditional:
                        candidate = conditional.new
                        if candidate is None or not candidate.process_start_time:
                            raise CutoverError("candidate publication identity unavailable")
                        conditional_kwargs = {
                            "lock_deadline": conditional.routing_deadline(),
                            "process_start_time": candidate.process_start_time,
                        }
                    published, _ = self.routing.publish_active_with_previous_guarded(
                        self.config_dir, bind=self.bind, port=new_port,
                        pid=getattr(handle, "pid", None), version=self.version,
                        demote_existing=True, expected_active=old_for_cas,
                        refuse_current=conditional.refuse_current if conditional else self.refuse_old,
                        **conditional_kwargs,
                    )
                    if conditional:
                        conditional.new = published
                        result.serving_endpoint = published
            except (refused, _UnguardedRouting, routing.RoutingLockTimeout) as exc:
                result.error = str(exc)
                result.steps.append(f"refused: {exc}")
                if conditional:
                    conditional.rollback(handle, result, flipped=False)
                    if result.rolled_back:
                        breadcrumb.clear_breadcrumb(self.config_dir)
                    else:
                        write_breadcrumb(
                            state="started", old=old_dict,
                            new_port=new_port, new_pid=new_pid,
                            error=result.error, started_at=started_at,
                        )
                    return result
                terminated = False
                try:
                    handle.terminate()
                    result.steps.append("refusal: terminated new daemon")
                    # terminate() only requests exit; keep the breadcrumb (the
                    # durable PID reap_abandoned_passive needs) until it's confirmed.
                    terminated = self._await_exit(handle)
                    if not terminated:
                        result.steps.append(
                            "refusal: new daemon exit unconfirmed; breadcrumb kept"
                        )
                except Exception as term_exc:  # noqa: BLE001
                    result.steps.append(
                        f"refusal: new daemon termination failed: {term_exc}"
                    )
                    log.error(
                        "Cutover refusal could not terminate passive daemon: %s",
                        term_exc,
                    )
                if terminated:
                    breadcrumb.clear_breadcrumb(self.config_dir)
                else:
                    write_breadcrumb(
                        state="started", old=old_dict,
                        new_port=new_port, new_pid=new_pid,
                        error=result.error, started_at=started_at,
                    )
                return result
            flipped = True
            result.steps.append("routing table flipped -> new active")
            write_breadcrumb(
                state="flipped", old=old_dict,
                new_port=new_port, new_pid=new_pid, started_at=started_at,
            )
            self._emit(
                lifecycle.CUTOVER_FLIP, lifecycle.OK, port=new_port,
                detail={"old_port": old.port if old is not None else None},
            )

            if old is not None and old.port != new_port:
                if conditional is None:
                    old_client = make_client(old.base_url)
                # About to open the old daemon's drain gate -- record it first
                # so an abort during the (possibly long) drain is traceable.
                write_breadcrumb(
                    state="draining", old=old_dict,
                    new_port=new_port, new_pid=new_pid, started_at=started_at,
                )
                self._emit(lifecycle.DRAIN, lifecycle.BEGIN, port=old.port)
                drain_res = (
                    conditional.drain_predecessor(drain_timeout) if conditional else
                    old_client.drain(timeout=drain_timeout, poll=1.0, force=force)
                )
                result.drain = drain_res
                result.steps.append(
                    f"old drained clean={drain_res.get('clean')} "
                    f"forced={drain_res.get('forced')}"
                )
                if not drain_res.get("drained"):
                    busy_work = drain_res.get(
                        "busy_sessions_count" if conditional else "busy_sessions"
                    )
                    self._emit(
                        lifecycle.DRAIN, lifecycle.FAIL, port=old.port,
                        detail={"busy_sessions": busy_work},
                    )
                    if conditional:
                        raise CutoverError(
                            f"conditional predecessor drain incomplete (busy: {busy_work}); "
                            "retirement refused"
                        )
                    raise CutoverError(
                        "old daemon did not drain "
                        f"(busy: {busy_work}); "
                        "rerun with force to proceed"
                    )
                self._emit(
                    lifecycle.DRAIN, lifecycle.OK, port=old.port,
                    detail={"clean": drain_res.get("clean"),
                            "forced": drain_res.get("forced")},
                )

                # Verify-before-retire GATE: re-confirm the new daemon is still
                # live right before we retire the old one. If it died between the
                # flip and here, retiring the old daemon would strand every
                # client (active -> dead new, previous -> retired old). Raising
                # *before* the commit point routes into rollback, which restores
                # and undrains the old daemon -- so a cutover can never retire the
                # old daemon while the new one is already dead (#5322).
                new_live = False
                try:
                    new_live = bool(
                        health_check(self._client_host(), new_port)
                    )
                except Exception:  # noqa: BLE001 -- verification is observational
                    new_live = False
                self._emit(
                    lifecycle.CUTOVER_VERIFY,
                    lifecycle.OK if new_live else lifecycle.FAIL,
                    port=new_port,
                )
                if not new_live:
                    raise CutoverError(
                        f"new daemon unhealthy on port {new_port} at the "
                        f"verify-before-retire gate; refusing to retire the old "
                        f"daemon (rolling back to keep it serving)"
                    )
                if conditional:
                    conditional.verify_before_retire()

                # COMMIT POINT: retire the old daemon. Past here the new daemon
                # is the only one, so we never roll back.
                result.committed = True
                write_breadcrumb(
                    state="draining" if conditional else "committed", old=old_dict,
                    new_port=new_port, new_pid=new_pid, started_at=started_at,
                )
                if conditional:
                    conditional.retire_predecessor()
                else:
                    old_client.shutdown()
                result.retirement_requested = conditional is not None
                result.steps.append("old daemon shutdown requested")
                if conditional:
                    conditional.confirm_retired()
                    result.retirement_confirmed = True
                    result.steps.append("old daemon exit confirmed")
                # The retired daemon is the OLD one, so log its port (matching
                # `drain`); the new port is carried in detail for correlation.
                self._emit(
                    lifecycle.CUTOVER_RETIRE, lifecycle.OK, port=old.port,
                    detail={"new_port": new_port},
                )
            else:
                result.committed = True
                write_breadcrumb(
                    state="committed", old=old_dict,
                    new_port=new_port, new_pid=new_pid, started_at=started_at,
                )
                result.steps.append("no prior active daemon -- nothing to retire")
                # Cold start: nothing was retired; the new port is what now serves.
                self._emit(
                    lifecycle.CUTOVER_RETIRE, lifecycle.OK, port=new_port,
                    detail={"note": "no prior active daemon"},
                )

            # Best-effort: hand the credential relay (9857) to the new daemon
            # once the old one has released it.
            self._adopt_relay(
                new_port, result, make_client=make_client,
                adopt_relay=conditional.adopt_successor if conditional else None,
            )

            if conditional:
                conditional.complete(result)
            else:
                result.ok = True
                breadcrumb.clear_breadcrumb(self.config_dir)
            return result

        except Exception as exc:  # noqa: BLE001 -- convert to a rollback
            if isinstance(exc, routing_persistence.PublicationCommitUncertain):
                result.publication_uncertain = True
                result.error = "publication commit uncertain; generations retained"
                result.serving_endpoint = None
                result.steps.append(result.error)
                log.error(result.error)
                write_breadcrumb(
                    state="publication-uncertain", old=old_dict, new_port=new_port,
                    new_pid=new_pid, started_at=started_at, error=result.error,
                )
                return result
            if result.committed:
                # Should not happen (commit is the last fallible step), but if a
                # post-commit error escapes, the new daemon still owns the route.
                result.error = (
                    f"post-commit conditional error ({type(exc).__name__})" if conditional
                    else f"post-commit error (new daemon is live): {exc}"
                )
                result.ok = conditional is None
                if conditional:
                    result.serving_endpoint = None
                log.error("Cutover post-commit error: %s", result.error)
                if conditional:
                    write_breadcrumb(
                        state="draining", old=old_dict,
                        new_port=new_port, new_pid=new_pid, started_at=started_at,
                        error=result.error,
                    )
                else:
                    breadcrumb.clear_breadcrumb(self.config_dir)
                self._emit(
                    lifecycle.ROLLBACK, lifecycle.OK, port=new_port,
                    detail={"committed_forward": True, "error": result.error},
                )
                return result
            result.error = (
                str(exc) if not conditional or isinstance(exc, CutoverError)
                else f"conditional cutover failed ({type(exc).__name__})"
            )
            if conditional:
                conditional.rollback(handle, result, flipped=flipped)
            else:
                self._rollback(old, handle, new_port, result, flipped=flipped)
            # Record the terminal outcome durably. On a clean rollback (or
            # commit-forward) no daemon is left stranded; the breadcrumb marks
            # the attempt as resolved rather than lingering as "aborted".
            if result.ok or (conditional and result.rolled_back):
                breadcrumb.clear_breadcrumb(self.config_dir)
            else:
                state = "rolled_back"
                if result.publication_uncertain:
                    state = "publication-uncertain"
                if conditional and not result.rolled_back:
                    state = "flipped" if flipped else "started"
                write_breadcrumb(
                    state=state,
                    old=old_dict,
                    new_port=new_port, new_pid=new_pid, error=result.error,
                    started_at=started_at,
                )
            # A rollback outcome reflects service AVAILABILITY, not cutover
            # success. `result.ok` only marks a completed cutover (commit
            # forward); a normal rollback that *restored the old daemon* leaves
            # `result.ok` False even though service recovered. So confirm from
            # the live routing table + a health probe: `ok` when something is
            # serving again, `fail` only for the alertable case where the
            # rollback left nothing serving. Best-effort -- a probe failure is
            # treated as not-serving, never raised.
            serving = result.ok
            if not serving:
                try:
                    active = self.routing.read_active_endpoint(self.config_dir)
                    serving = bool(active and (
                        conditional.matches(active) if conditional else
                        self.health_check(active.client_host, active.port)
                    ))
                except Exception:  # noqa: BLE001 -- availability probe is best-effort
                    serving = False
            self._emit(
                lifecycle.ROLLBACK,
                lifecycle.OK if serving else lifecycle.FAIL,
                port=new_port,
                detail={"rolled_back": result.rolled_back, "serving": serving,
                        "error": result.error},
            )
            return result

    def _adopt_relay(
        self, new_port: int, result: CutoverResult,
        *, make_client: Callable[[str], _Client] | None = None,
        adopt_relay: Callable[[], dict] | None = None,
    ) -> None:
        """Best-effort: bind the credential relay on the new daemon (non-fatal)."""
        try:
            if adopt_relay is not None:
                adopt = adopt_relay()
            else:
                new_client = (make_client or self.make_client)(self._base_url(new_port))
                adopt = new_client.adopt_relay()
            adopted = adopt.get("adopted")
            if adopted is not None and type(adopted) is not bool:
                raise CutoverError("relay adoption response has an invalid boolean")
            result.steps.append(f"relay adopt: {adopted}")
        except Exception as exc:  # noqa: BLE001 -- relay is non-fatal
            failure = type(exc).__name__
            result.steps.append(f"relay adopt failed (non-fatal): {failure}")
            log.warning("Relay adoption failed after cutover (%s)", failure)

    def _undrain(self, old: Endpoint, result: CutoverResult) -> None:
        """Best-effort release of the old daemon's drain gate (#1756).

        A cutover that aborts after opening the drain gate must not leave the
        survivor drained. Records the outcome as a step either way so a rollback
        is traceable rather than silent.
        """
        try:
            self.make_client(old.base_url).undrain()
            result.steps.append("rollback: old daemon undrained")
        except Exception as exc:  # noqa: BLE001 -- undrain is best-effort
            result.steps.append(f"rollback: old undrain failed (non-fatal): {exc}")
            log.warning("Rollback could not undrain old daemon: %s", exc)

    def _rollback(
        self,
        old: Endpoint | None,
        handle: _Handle,
        new_port: int,
        result: CutoverResult,
        *,
        flipped: bool,
    ) -> None:
        log.warning("Rolling back cutover: %s", result.error)
        # 1. Try to restore the old endpoint as active (only if it is still alive).
        old_restored = False
        if old is not None:
            try:
                if self.health_check(old.client_host, old.port):
                    self.routing.publish_active(
                        self.config_dir, bind=old.bind, port=old.port,
                        pid=old.pid, version=old.version, demote_existing=True,
                    )
                    old_restored = True
                    result.steps.append("rollback: restored old as active")
                    # We may have opened the old daemon's drain gate before the
                    # failure -- release it so the survivor does not stay closed
                    # to new work (#1756). Recorded either way for traceability.
                    self._undrain(old, result)
            except routing_persistence.PublicationCommitUncertain:
                result.publication_uncertain = True
                result.serving_endpoint = None
                result.steps.append("rollback publication uncertain; generations retained")
                log.error("Rollback publication uncertain; generations retained")
                return
            except Exception as exc:  # noqa: BLE001
                log.error("Rollback could not restore old endpoint: %s", exc)

        # 2. If the route was already flipped to the new daemon and we could NOT
        #    restore the old one, terminating the new daemon would strand every
        #    client (active -> dead new, previous -> dead old, fallback -> nothing
        #    listening). Commit forward to the new daemon instead, as long as it is
        #    still healthy -- it is the only viable home for the route.
        if flipped and not old_restored:
            host = self._client_host()
            if self.health_check(host, new_port):
                # Re-assert the new daemon as active (it already is, but make the
                # intent explicit and bump the generation) and keep it alive.
                try:
                    self.routing.publish_active(
                        self.config_dir, bind=self.bind, port=new_port,
                        pid=getattr(handle, "pid", None), version=self.version,
                        demote_existing=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("Commit-forward could not re-assert route: %s", exc)
                result.steps.append(
                    "rollback: old unreachable -- committed forward to the "
                    "healthy new daemon (no rollback)"
                )
                self._adopt_relay(new_port, result)
                result.committed = True
                result.ok = True
                result.rolled_back = False
                return
            # Both old and new are unreachable: a double failure. Leave the route
            # as-is (readers heal to whatever recovers) and do not kill the new
            # daemon -- if it is merely slow it may still come back.
            result.steps.append(
                "rollback: both old and new unhealthy -- left route untouched"
            )
            result.rolled_back = True
            return

        # 3. Safe to terminate the freshly spawned passive daemon: either the old
        #    endpoint is serving again, or the route was never flipped (the new
        #    daemon never served any client).
        try:
            handle.terminate()
            result.steps.append("rollback: terminated new daemon")
        except Exception as exc:  # noqa: BLE001
            log.error("Rollback could not terminate new daemon: %s", exc)
        result.rolled_back = True
