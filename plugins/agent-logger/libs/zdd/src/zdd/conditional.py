"""Opt-in, owner-checked cutover using an adopter's effective runtime context.

Snapshot callbacks close over credentials and resolved effective configuration;
neither caller environment nor public health payloads are a snapshot source.
"""

from __future__ import annotations

import logging
import inspect
import math
import os
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, NoReturn, ParamSpec, TypeVar

from . import breadcrumb, diagnostics, publication_history, routing
from .cutover import CutoverError
from .routing import Endpoint

if TYPE_CHECKING:
    from .cutover import CutoverOrchestrator, CutoverResult, _Client, _Handle

log = logging.getLogger("zdd")
_P = ParamSpec("_P")
_T = TypeVar("_T")


def _captured_call(
    operation: str, callback: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs,
) -> _T:
    try:
        result = callback(*args, **kwargs)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("captured runtime callbacks must be synchronous")
        return result
    except Exception as exc:
        raise CutoverError(
            f"captured runtime {operation} failed ({type(exc).__name__})"
        ) from None


@dataclass(frozen=True)
class RuntimeAttestation:
    """An adopter-verified health response, including only nonsecret identity."""

    pid: int | None
    process_start_time: str | None
    version: str | None
    config_identity: str
    ready: bool


@dataclass(frozen=True)
class RuntimePreparation:
    """Verified passive resources, not serving readiness or route authority."""

    pid: int | None
    process_start_time: str | None
    version: str | None
    config_identity: str
    prepared: bool


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Authoritative effective config and sensitive context held in closures.

    The adopter captures database/config/install roots, executable and persistent
    settings from the serving owner's effective state, not just service.env or
    /proc. Its identity must cover all compatibility-relevant nonsecret state.
    The same context drives spawn, authenticated attestation and control clients.
    Sensitive state is never included in repr, routing, breadcrumbs or results.
    """

    identity: str
    spawn_passive: Callable[[int], _Handle] = field(repr=False)
    attest: Callable[[Endpoint], RuntimeAttestation] = field(repr=False)
    make_client: Callable[[str], _Client] = field(repr=False)
    retire: Callable[[Endpoint, Endpoint], None] = field(repr=False)
    confirm_retired: Callable[[Endpoint, float], bool] = field(repr=False)
    drain_predecessor: Callable[[Endpoint, Endpoint, float], dict] = field(repr=False)
    undrain_predecessor: Callable[[Endpoint], None] = field(repr=False)
    adopt_successor: Callable[[Endpoint], dict] = field(repr=False)
    attest_prepared: Callable[[Endpoint], RuntimePreparation] | None = field(
        default=None, repr=False,
    )
    activate_successor: Callable[[Endpoint], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.attest_prepared is None) != (self.activate_successor is None):
            raise ValueError("passive preparation and active activation must be paired")
        if self.attest_prepared is not None and (
            not callable(self.attest_prepared) or not callable(self.activate_successor)
            or any(
                inspect.iscoroutinefunction(callback)
                or inspect.iscoroutinefunction(getattr(callback, "__call__", None))
                for callback in (self.attest_prepared, self.activate_successor)
            )
        ):
            raise ValueError("two-phase runtime callbacks must be synchronous and callable")


@dataclass(frozen=True)
class ConditionalCutover:
    """Expected raw owner and trusted adopter policy for stale-runtime repair.

    Version comparison belongs to the adopter's version scheme and must refuse
    unknown ordering. Capture returns None when authoritative config cannot be
    established (including legacy daemons without effective-state attestation).
    """

    expected_active: Endpoint
    selected_is_newer: Callable[[str, str], bool] = field(repr=False)
    capture_runtime: Callable[[Endpoint], RuntimeSnapshot | None] = field(repr=False)
    routing_lock_timeout: float = 5.0
    retirement_timeout: float = 30.0


class PreparedCutover:
    """Per-attempt context created only while native cutover authority is held."""

    def __init__(self, orch: CutoverOrchestrator, request: ConditionalCutover) -> None:
        self.orch = orch
        self.request = request
        self.old = request.expected_active
        self.new: Endpoint | None = None
        self.publication_witness: str | None = None
        if not math.isfinite(request.routing_lock_timeout) or request.routing_lock_timeout < 0:
            raise ValueError("routing_lock_timeout must be finite and nonnegative")
        if not math.isfinite(request.retirement_timeout) or request.retirement_timeout < 0:
            raise ValueError("retirement_timeout must be finite and nonnegative")
        if orch.routing is not routing:
            raise CutoverError("conditional cutover requires native guarded routing")
        record = breadcrumb.read_breadcrumb(orch.config_dir)
        if record is None and breadcrumb.breadcrumb_path(orch.config_dir).exists():
            raise CutoverError("unreadable cutover state requires owner-led recovery")
        if record is not None and record.get("state") not in ("committed", "rolled_back"):
            raise CutoverError(
                "unfinished cutover requires owner-led recovery before conditional repair"
            )
        if (
            type(self.old.pid) is not int
            or self.old.pid <= 0
            or type(self.old.generation) is not int
            or self.old.generation <= 0
            or not isinstance(self.old.process_start_time, str)
            or not self.old.process_start_time
            or not self.old.process_start_time.isdigit()
        ):
            raise CutoverError(
                "conditional cutover requires published PID/start identity/generation"
            )
        self.old_pid = self.old.pid
        routing.validate_active(
            orch.config_dir,
            self.old,
            refuse_current=self.refuse_current,
            lock_deadline=self.routing_deadline(),
        )
        if (
            not orch.version
            or not self.old.version
            or orch.version == self.old.version
            or _captured_call(
                "version comparison", request.selected_is_newer, orch.version, self.old.version
            ) is not True
        ):
            raise CutoverError("selected runtime is not provably newer than serving runtime")
        snapshot = _captured_call("capture", request.capture_runtime, self.old)
        if snapshot is None or not snapshot.identity:
            raise CutoverError("authoritative effective runtime snapshot unavailable")
        self.snapshot = snapshot
        self._activated = False
        if not self.matches(self.old):
            raise CutoverError("serving runtime configuration attestation mismatch")
        routing.validate_active(
            orch.config_dir,
            self.old,
            refuse_current=self.refuse_current,
            lock_deadline=self.routing_deadline(),
        )

    def routing_deadline(self) -> float:
        """Bound one routing acquisition, independently of capture/health/drain."""
        return time.monotonic() + self.request.routing_lock_timeout

    def refuse_current(self, raw: dict | None) -> str | None:
        if self.orch.refuse_old:
            reason = self.orch.refuse_old(raw)
            if reason:
                return reason
        if diagnostics.process_start_time(self.old_pid) != self.old.process_start_time:
            return "serving process identity changed or cannot be verified"
        return None

    def matches(self, endpoint: Endpoint, *, require_ready: bool = True) -> bool:
        if not endpoint.pid or not endpoint.process_start_time:
            return False
        attested = _captured_call("attest", self.snapshot.attest, endpoint)
        if not isinstance(attested, RuntimeAttestation):
            raise CutoverError("captured runtime attestation has an invalid shape")
        return (
            (not require_ready or attested.ready is True)
            and attested.pid == endpoint.pid
            and attested.process_start_time == endpoint.process_start_time
            and attested.version == endpoint.version
            and attested.config_identity == self.snapshot.identity
            and diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
        )

    def spawn(self, port: int) -> _Handle:
        self.publication_witness = routing.publication_witness(
            self.orch.config_dir, deadline=self.routing_deadline(),
        )
        handle = self.snapshot.spawn_passive(port)
        raw_pid = getattr(handle, "pid", None)
        pid = raw_pid if type(raw_pid) is int and raw_pid > 0 else None
        self.new = Endpoint(
            bind=self.orch.bind,
            port=port,
            pid=pid,
            version=self.orch.version,
            process_start_time=diagnostics.process_start_time(pid) if pid is not None else None,
        )
        return handle

    def healthy(self, host: str, port: int) -> bool:
        if (
            self.new is None
            or not self.new.process_start_time
            or not self.new.pid
            or self.new.pid <= 0
            or self.new.pid == self.old_pid
        ):
            return False
        if self.snapshot.attest_prepared is None or self._activated:
            return self.matches(self.new)
        attested = _captured_call(
            "preparation attestation", self.snapshot.attest_prepared, self.new,
        )
        if type(attested) is not RuntimePreparation:
            raise CutoverError("captured runtime preparation has an invalid shape")
        return (
            attested.prepared is True
            and type(attested.pid) is int and attested.pid == self.new.pid
            and attested.process_start_time == self.new.process_start_time
            and attested.version == self.new.version
            and attested.config_identity == self.snapshot.identity
            and diagnostics.process_start_time(self.new.pid) == self.new.process_start_time
        )

    def activate_successor(self) -> None:
        """Activate only after publication; the receiver fences every mutation.

        Do not hold the coordinator's routing lock across this callback: the
        receiver acquires its own native authority at each resource transition.
        Preparation cannot authorize predecessor drain or retirement.
        """
        callback = self.snapshot.activate_successor
        if callback is None:
            return
        if self._activated or self.new is None:
            raise CutoverError("successor activation state is invalid")
        routing.validate_active(
            self.orch.config_dir, self.new, lock_deadline=self.routing_deadline(),
        )
        if _captured_call("activation", callback, self.new) is not None:
            raise CutoverError("captured runtime activation has an invalid shape")
        if not self.matches(self.new):
            raise CutoverError("activated successor readiness attestation mismatch")
        routing.validate_active(
            self.orch.config_dir, self.new, lock_deadline=self.routing_deadline(),
        )
        self._activated = True

    def verify_before_retire(self) -> None:
        if self.new is None or not self.matches(self.new):
            raise CutoverError("successor effective configuration/identity attestation mismatch")
        if diagnostics.process_start_time(self.old_pid) != self.old.process_start_time:
            raise CutoverError("predecessor identity changed before retirement")
        routing.validate_active(
            self.orch.config_dir,
            self.new,
            refuse_current=self.refuse_current,
            lock_deadline=self.routing_deadline(),
        )

    def retire_predecessor(self) -> None:
        """The adopter must condition shutdown on identity at the receiver."""
        self.verify_before_retire()
        if self.new is None:
            raise CutoverError("successor identity unavailable before predecessor retirement")
        self.snapshot.retire(self.old, self.new)

    def drain_predecessor(self, timeout: float) -> dict:
        """The receiver must atomically validate both identities before drain."""
        self.verify_before_retire()
        if self.new is None:
            raise CutoverError("successor identity unavailable before predecessor drain")
        response = _captured_call(
            "drain", self.snapshot.drain_predecessor, self.old, self.new, timeout
        )
        if not isinstance(response, dict) or any(
            type(response.get(key)) is not bool for key in ("drained", "clean", "forced")
        ):
            raise CutoverError("conditional drain response has invalid boolean evidence")
        if response.get("mode") == "incompatible-contract-cold":
            raise CutoverError("conditional drain selected cold mode; retirement refused")
        if response["forced"]:
            raise CutoverError("conditional drain was forced; retirement refused")
        if response["drained"] and not response["clean"]:
            raise CutoverError("conditional drain did not prove clean completion")
        busy = response.get("busy_sessions")
        if busy is not None and not isinstance(busy, list):
            raise CutoverError("conditional drain response has invalid busy-work evidence")
        if response["drained"] and busy:
            raise CutoverError("conditional drain response contradicts busy-work evidence")
        return {
            "drained": response["drained"], "clean": response["clean"],
            "forced": False, "busy_sessions_count": len(busy) if busy is not None else None,
        }

    def confirm_retired(self) -> None:
        if self.snapshot.confirm_retired(self.old, self.request.retirement_timeout) is not True:
            raise CutoverError("predecessor exit unconfirmed; owner-led recovery required")

    def adopt_successor(self) -> dict:
        if self.new is None or not self.matches(self.new):
            raise CutoverError("successor identity unavailable before relay adoption")
        routing.validate_active(
            self.orch.config_dir, self.new, lock_deadline=self.routing_deadline()
        )
        response = _captured_call("adopt", self.snapshot.adopt_successor, self.new)
        if self.snapshot.activate_successor is not None and (
            type(response) is not dict or response.get("adopted") is not True
        ):
            raise CutoverError("successor resource adoption unconfirmed")
        return response

    def complete(self, result: CutoverResult) -> None:
        """Publish success and clear recovery only for the verified routed owner."""
        with routing.routing_lock(self.orch.config_dir, deadline=self.routing_deadline()):
            attested = self.new is not None and self.matches(self.new)
            table = routing.read_table(self.orch.config_dir, strict=True) or {}
            raw = table.get("active")
            active = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
            if self.new is None or active != self.new or not attested:
                result.serving_endpoint = None
                raise CutoverError("successor ownership changed before completion")
            if not breadcrumb.clear_breadcrumb(self.orch.config_dir):
                raise CutoverError("conditional completion could not clear recovery state")
            result.ok = True
            result.serving_endpoint = self.new

    def rollback(
        self,
        handle: _Handle,
        result: CutoverResult,
        *,
        flipped: bool,
    ) -> None:
        """CAS-restore only our own publication; never overwrite a later winner.

        If restoration cannot be established, leave both generations alive and
        preserve a nonterminal breadcrumb for owner-led recovery. A failed repair
        is never converted to success merely because some endpoint is healthy.
        """
        restored = None
        if flipped:
            try:
                if not self.matches(self.old, require_ready=False):
                    raise CutoverError("predecessor configuration/identity no longer verified")
                restored, _ = routing.publish_active_with_previous_guarded(
                    self.orch.config_dir,
                    bind=self.old.bind,
                    port=self.old.port,
                    pid=self.old.pid,
                    version=self.old.version,
                    process_start_time=self.old.process_start_time,
                    expected_active=self.new,
                    refuse_current=self.refuse_current,
                    lock_deadline=self.routing_deadline(),
                    demote_existing=True,
                )
            except (
                routing.ActivePublicationRefused, routing.RoutingLockTimeout,
                CutoverError, OSError, ValueError,
            ):
                result.steps.append(
                    "rollback pending: owner restoration refused; generations retained"
                )
                result.serving_endpoint = None
                log.error("Conditional cutover rollback cannot safely restore its predecessor")
                return
            try:
                _captured_call(
                    "undrain", self.snapshot.undrain_predecessor, restored,
                )
                if not self.matches(restored):
                    raise CutoverError("restored predecessor is not ready")
                routing.validate_active(
                    self.orch.config_dir, restored, lock_deadline=self.routing_deadline()
                )
            except (OSError, ValueError, CutoverError, routing.ActivePublicationRefused):
                result.serving_endpoint = None
                result.steps.append("rollback pending: predecessor readiness unverified")
                log.error("Conditional cutover restored predecessor readiness unverified")
                return
            result.serving_endpoint = restored
            result.steps.append("rollback: old daemon undrained")
            result.steps.append("rollback: restored verified predecessor")
            result.steps.append("rollback pending: promoted successor retained for safe drain")
            log.error("Conditional cutover retains promoted successor and its admitted work")
            return
        try:
            if self.new is None or self.new.pid is None:
                raise CutoverError("candidate process identity unavailable; refusing disposal")
            with routing.routing_lock(self.orch.config_dir, deadline=self.routing_deadline()):
                table = routing.read_table(self.orch.config_dir, strict=True) or {}
                active_raw = table.get("active")
                active = Endpoint.from_dict(active_raw) if isinstance(active_raw, dict) else None
                if restored is not None and active != restored:
                    raise CutoverError("restored route changed before candidate disposal")
                for key in ("active", "previous"):
                    raw = table.get(key)
                    if raw is not None and not isinstance(raw, dict):
                        raise CutoverError("publication cannot be verified; refusing disposal")
                    endpoint = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                    if raw and endpoint is None:
                        raise CutoverError("publication identity invalid; refusing disposal")
                    if endpoint is not None and (
                        endpoint.pid == self.new.pid or endpoint.port == self.new.port
                    ):
                        raise CutoverError("candidate has been published; refusing disposal")
                if not publication_history.never_published(
                    table, self.publication_witness, pid=self.new.pid, port=self.new.port,
                ):
                    raise CutoverError("candidate publication history unverified; refusing disposal")
                if handle.poll() is None:
                    if (
                        self.new is None
                        or not self.new.process_start_time
                        or (
                            diagnostics.process_start_time(self.new.pid)
                            != self.new.process_start_time
                        )
                    ):
                        raise CutoverError("candidate identity changed before disposal")
                    termination = diagnostics.terminate_pid_if_identity(
                        self.new.pid, self.new.process_start_time,
                        exit_timeout=min(5.0, self.request.retirement_timeout),
                    )
                    if (
                        termination.get("killed") is not True
                        or termination.get("identity_verified") is not True
                        or termination.get("exit_confirmed") is not True
                    ):
                        raise CutoverError("identity-bound candidate disposal refused")
                result.rolled_back = True
                result.steps.append("rollback: new daemon exit confirmed")
        except (OSError, ValueError, CutoverError):
            result.steps.append("rollback pending: candidate disposal refused or failed")
            log.error("Conditional cutover candidate disposal refused or failed")
            return


def run_conditional_cutover(
    config_dir: str | os.PathLike[str],
    *,
    version: str,
    request: ConditionalCutover,
    health_timeout: float = 60.0,
    drain_timeout: float = 300.0,
    lock_timeout: float | None = None,
    refuse_old: Callable[[dict | None], str | None] | None = None,
) -> CutoverResult:
    """Supported adopter operation; it never derives context from caller env.

    Only a captured RuntimeSnapshot can supply spawn/health/control behavior.
    It intentionally skips legacy recovery/reaping preludes and forced process
    retirement. Success requires adopter-confirmed exact predecessor exit.
    """
    from .cutover import CutoverOrchestrator

    def unavailable(*args: object) -> NoReturn:
        raise RuntimeError("conditional operation used uncaptured runtime context")

    def pick_free_port() -> int:
        host = request.expected_active.bind
        if host == "[::]":
            host = "::"
        if not host:
            host = "0.0.0.0"
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    orch = CutoverOrchestrator(
        config_dir,
        bind=request.expected_active.bind,
        version=version,
        spawn_passive=unavailable,
        health_check=unavailable,
        make_client=unavailable,
        pick_free_port=pick_free_port,
        refuse_old=refuse_old,
    )
    return orch.run(
        conditional=request,
        health_timeout=health_timeout,
        drain_timeout=drain_timeout,
        lock_timeout=lock_timeout,
    )
