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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from zdd import breadcrumb, routing
from zdd.routing import Endpoint

log = logging.getLogger("zdd")


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "new_port": self.new_port,
            "old_port": self.old_endpoint.port if self.old_endpoint else None,
            "steps": self.steps,
            "rolled_back": self.rolled_back,
            "committed": self.committed,
            "error": self.error,
            "drain": self.drain,
        }


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
        self.sleep = sleep
        self.clock = clock
        self.routing = routing_mod

    # -- helpers -------------------------------------------------------------

    def _client_host(self) -> str:
        if self.bind in ("0.0.0.0", "", None):
            return "127.0.0.1"
        if self.bind == "::":
            return "::1"
        return self.bind

    def _base_url(self, port: int) -> str:
        return f"http://{self._client_host()}:{port}"

    def _await_health(self, port: int, timeout: float, poll: float) -> bool:
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                if self.health_check(self._client_host(), port):
                    return True
            except Exception:
                pass
            self.sleep(poll)
        # one last probe at the deadline
        try:
            return bool(self.health_check(self._client_host(), port))
        except Exception:
            return False

    # -- main ----------------------------------------------------------------

    def run(
        self,
        *,
        health_timeout: float = 60.0,
        drain_timeout: float = 300.0,
        force: bool = False,
        poll: float = 0.5,
    ) -> CutoverResult:
        result = CutoverResult(ok=False)
        old = self.routing.read_active_endpoint(self.config_dir)
        result.old_endpoint = old

        new_port = self.pick_free_port()
        result.new_port = new_port

        # Durable breadcrumb (#1756): written *before* the drain gate is ever
        # touched, so if this orchestrator dies mid-cutover the aborted attempt
        # leaves an attributable trace on disk (and recover_stale_cutover can
        # undrain the stranded old daemon). started_at is threaded through every
        # later update so the record keeps its original timestamp.
        old_dict = (
            {"bind": old.bind, "port": old.port} if old is not None else None
        )
        started_at = breadcrumb.write_breadcrumb(
            self.config_dir, state="started", old=old_dict, new_port=new_port,
        )["started_at"]

        handle = self.spawn_passive(new_port)
        result.steps.append(f"spawned passive pid={getattr(handle, 'pid', '?')} "
                            f"port={new_port}")

        flipped = False
        try:
            if not self._await_health(new_port, health_timeout, poll):
                raise CutoverError(
                    f"new daemon did not become healthy on port {new_port} "
                    f"within {health_timeout:.0f}s"
                )
            result.steps.append("new daemon healthy")

            # Flip the route: new active, old demoted to previous. From here a
            # new CLI resolution lands on the new daemon; long-lived sockets stay
            # on the old one until their turn completes (migrate at a breakpoint).
            self.routing.publish_active(
                self.config_dir, bind=self.bind, port=new_port,
                pid=getattr(handle, "pid", None), version=self.version,
                demote_existing=True,
            )
            flipped = True
            result.steps.append("routing table flipped -> new active")
            breadcrumb.write_breadcrumb(
                self.config_dir, state="flipped", old=old_dict,
                new_port=new_port, started_at=started_at,
            )

            if old is not None and old.port != new_port:
                old_client = self.make_client(old.base_url)
                # About to open the old daemon's drain gate -- record it first
                # so an abort during the (possibly long) drain is traceable.
                breadcrumb.write_breadcrumb(
                    self.config_dir, state="draining", old=old_dict,
                    new_port=new_port, started_at=started_at,
                )
                drain_res = old_client.drain(
                    timeout=drain_timeout, poll=1.0, force=force
                )
                result.drain = drain_res
                result.steps.append(
                    f"old drained clean={drain_res.get('clean')} "
                    f"forced={drain_res.get('forced')}"
                )
                if not drain_res.get("drained"):
                    raise CutoverError(
                        "old daemon did not drain "
                        f"(busy: {drain_res.get('busy_sessions')}); "
                        "rerun with force to proceed"
                    )

                # COMMIT POINT: retire the old daemon. Past here the new daemon
                # is the only one, so we never roll back.
                result.committed = True
                breadcrumb.write_breadcrumb(
                    self.config_dir, state="committed", old=old_dict,
                    new_port=new_port, started_at=started_at,
                )
                old_client.shutdown()
                result.steps.append("old daemon shutdown requested")
            else:
                result.committed = True
                breadcrumb.write_breadcrumb(
                    self.config_dir, state="committed", old=old_dict,
                    new_port=new_port, started_at=started_at,
                )
                result.steps.append("no prior active daemon -- nothing to retire")

            # Best-effort: hand the credential relay (9857) to the new daemon
            # once the old one has released it.
            self._adopt_relay(new_port, result)

            result.ok = True
            # Clean cutover: retire the breadcrumb so no stale trace lingers.
            breadcrumb.clear_breadcrumb(self.config_dir)
            return result

        except Exception as exc:  # noqa: BLE001 -- convert to a rollback
            if result.committed:
                # Should not happen (commit is the last fallible step), but if a
                # post-commit error escapes, the new daemon still owns the route.
                result.error = f"post-commit error (new daemon is live): {exc}"
                result.ok = True
                log.error("Cutover post-commit error: %s", exc)
                breadcrumb.clear_breadcrumb(self.config_dir)
                return result
            result.error = str(exc)
            self._rollback(old, handle, new_port, result, flipped=flipped)
            # Record the terminal outcome durably. On a clean rollback (or
            # commit-forward) no daemon is left stranded; the breadcrumb marks
            # the attempt as resolved rather than lingering as "aborted".
            if result.ok:
                breadcrumb.clear_breadcrumb(self.config_dir)
            else:
                breadcrumb.write_breadcrumb(
                    self.config_dir, state="rolled_back", old=old_dict,
                    new_port=new_port, error=result.error,
                    started_at=started_at,
                )
            return result

    def _adopt_relay(self, new_port: int, result: CutoverResult) -> None:
        """Best-effort: bind the credential relay on the new daemon (non-fatal)."""
        try:
            new_client = self.make_client(self._base_url(new_port))
            adopt = new_client.adopt_relay()
            result.steps.append(f"relay adopt: {adopt.get('adopted')}")
        except Exception as exc:  # noqa: BLE001 -- relay is non-fatal
            result.steps.append(f"relay adopt failed (non-fatal): {exc}")
            log.warning("Relay adoption failed after cutover: %s", exc)

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
