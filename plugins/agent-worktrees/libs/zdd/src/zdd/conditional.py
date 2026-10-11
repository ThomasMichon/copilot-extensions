"""Opt-in, owner-checked cutover using an adopter's effective runtime context.

Snapshot callbacks close over credentials and resolved effective configuration;
neither caller environment nor public health payloads are a snapshot source.
"""

from __future__ import annotations

import logging
import math
import os
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, NoReturn

from . import breadcrumb, diagnostics, routing
from .cutover import CutoverError
from .routing import Endpoint

if TYPE_CHECKING:
    from .cutover import CutoverOrchestrator, CutoverResult, _Client, _Handle

log = logging.getLogger("zdd")


@dataclass(frozen=True)
class RuntimeAttestation:
    """An adopter-verified health response, including only nonsecret identity."""

    pid: int | None
    process_start_time: str | None
    version: str | None
    config_identity: str
    ready: bool


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
    retire: Callable[[Endpoint], None] = field(repr=False)
    confirm_retired: Callable[[Endpoint, float], bool] = field(repr=False)


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
            not self.old.pid
            or self.old.pid <= 0
            or self.old.generation <= 0
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
            lock_deadline=self.deadline(),
        )
        if (
            not orch.version
            or not self.old.version
            or orch.version == self.old.version
            or request.selected_is_newer(orch.version, self.old.version) is not True
        ):
            raise CutoverError("selected runtime is not provably newer than serving runtime")
        snapshot = request.capture_runtime(self.old)
        if snapshot is None or not snapshot.identity:
            raise CutoverError("authoritative effective runtime snapshot unavailable")
        self.snapshot = snapshot
        if not self.matches(self.old):
            raise CutoverError("serving runtime configuration attestation mismatch")
        routing.validate_active(
            orch.config_dir,
            self.old,
            refuse_current=self.refuse_current,
            lock_deadline=self.deadline(),
        )

    def deadline(self) -> float:
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
        attested = self.snapshot.attest(endpoint)
        return (
            (not require_ready or attested.ready is True)
            and attested.pid == endpoint.pid
            and attested.process_start_time == endpoint.process_start_time
            and attested.version == endpoint.version
            and attested.config_identity == self.snapshot.identity
            and diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
        )

    def spawn(self, port: int) -> _Handle:
        handle = self.snapshot.spawn_passive(port)
        self.new = Endpoint(
            bind=self.orch.bind,
            port=port,
            pid=handle.pid,
            version=self.orch.version,
            process_start_time=diagnostics.process_start_time(handle.pid),
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
        return self.matches(self.new)

    def verify_before_retire(self) -> None:
        if self.new is None or not self.matches(self.new):
            raise CutoverError("successor effective configuration/identity attestation mismatch")
        if diagnostics.process_start_time(self.old_pid) != self.old.process_start_time:
            raise CutoverError("predecessor identity changed before retirement")
        routing.validate_active(
            self.orch.config_dir,
            self.new,
            refuse_current=self.refuse_current,
            lock_deadline=self.deadline(),
        )

    def retire_predecessor(self) -> None:
        """The adopter must condition shutdown on identity at the receiver."""
        self.verify_before_retire()
        self.snapshot.retire(self.old)

    def confirm_retired(self) -> None:
        if self.snapshot.confirm_retired(self.old, self.request.retirement_timeout) is not True:
            raise CutoverError("predecessor exit unconfirmed; owner-led recovery required")

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
                    lock_deadline=self.deadline(),
                    demote_existing=True,
                )
            except (routing.ActivePublicationRefused, routing.RoutingLockTimeout, CutoverError):
                result.steps.append(
                    "rollback pending: owner restoration refused; generations retained"
                )
                result.serving_endpoint = None
                log.error("Conditional cutover rollback cannot safely restore its predecessor")
                return
            try:
                self.snapshot.make_client(restored.base_url).undrain()
                if not self.matches(restored):
                    raise CutoverError("restored predecessor is not ready")
                routing.validate_active(
                    self.orch.config_dir, restored, lock_deadline=self.deadline()
                )
            except (OSError, CutoverError, routing.ActivePublicationRefused):
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
            with routing._routing_lock(self.orch.config_dir, deadline=self.deadline()):
                table = routing.read_table(self.orch.config_dir, strict=True) or {}
                active_raw = table.get("active")
                active = Endpoint.from_dict(active_raw) if isinstance(active_raw, dict) else None
                if restored is not None and active != restored:
                    raise CutoverError("restored route changed before candidate disposal")
                for key in ("active", "previous"):
                    raw = table.get(key)
                    endpoint = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                    if endpoint is not None and endpoint.pid == handle.pid:
                        raise CutoverError("candidate has been published; refusing disposal")
                if handle.poll() is None:
                    if (
                        self.new is None
                        or not self.new.process_start_time
                        or (
                            diagnostics.process_start_time(handle.pid)
                            != self.new.process_start_time
                        )
                    ):
                        raise CutoverError("candidate identity changed before disposal")
                    handle.terminate()
        except (OSError, CutoverError):
            result.steps.append("rollback pending: candidate disposal refused or failed")
            log.error("Conditional cutover candidate disposal refused or failed")
            return
        if self.orch._await_exit(handle):
            result.rolled_back = True
            result.steps.append("rollback: new daemon exit confirmed")
        else:
            result.steps.append("rollback pending: candidate exit unconfirmed")
            log.error("Conditional cutover candidate exit remains unconfirmed")


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
        host = request.expected_active.client_host
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
