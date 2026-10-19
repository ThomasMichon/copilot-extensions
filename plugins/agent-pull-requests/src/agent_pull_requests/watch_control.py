"""Authenticated TCP lifecycle adapter for the shared ZDD client seam.

The existing coalescing protocol carries both ordinary watch requests and
exact-transaction lifecycle controls. Neither health nor TCP authentication
grants authority to transfer the writer or enable effects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from uuid import UUID, uuid4

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton.client import request
from zdd import diagnostics
from zdd.process_exit import ProcessExitReference

from . import _installation_context as api
from .watch_daemon import Fetch, NativeFetch, Notify
from .watch_installation import WatchInstallation
from .watch_invocation import WatchInvocation
from .watch_journal import _passive_health, authorize_cleanup, authorize_control
from .watch_notification import default_notify, positive_seconds
from .watch_runtime import WatchInstance
from .watch_writer import WatchWriter

log = logging.getLogger(__name__)
_CONTROL = "owner-control"
_OPERATIONS = {"drain", "adopt", "activate", "resume", "retire", "withdraw"}
_CLEANUP_OPERATIONS = {"cleanup-health", "cleanup-stop"}


class WatchControlRefused(RuntimeError):
    """A lifecycle miss or refusal must reach the driver, never inline fallback."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("watch lifecycle completion deadline expired")
    return remaining


class WatchControlServer(CoalescingServer):
    """Do not let a caller-chosen key merge different authority-bearing requests."""

    def handle_request(self, kind: str, key: str, payload: dict, deadline: float) -> dict:
        if kind == _CONTROL:
            try:
                key = hashlib.sha256(json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
            except (TypeError, ValueError):
                return {"error": "watch-owner-control-refused", "retryable": False}
        return super().handle_request(kind, key, payload, deadline)


class WatchControlOwner:
    """One physically attested process, initially passive and effect-free."""

    def __init__(
        self, invocation: WatchInvocation, *, fetch: Fetch,
        notify: Notify = default_notify, poll_interval: float = 30.0,
        instance_id: str | None = None,
        native_fetch: NativeFetch | None = None,
    ) -> None:
        self.invocation = invocation
        self.instance = invocation.instance(instance_id=instance_id)
        self._execution_pid = os.getpid()
        self._control_lock = threading.Lock()
        self._request_context = threading.local()
        self._terminal = threading.Event()
        self.writer = WatchWriter(
            root=invocation.installation.state_root, authorize=self._authorize,
            fetch=fetch, notify=notify, poll_interval=poll_interval,
            native_fetch=native_fetch,
        )

    def _installation(self) -> WatchInstallation:
        if os.getpid() != self._execution_pid:
            raise PermissionError("watch control owner belongs to another process")
        installation = self.invocation.fresh_installation()
        self.instance.runtime.require_current(installation, environment={})
        return installation

    def _authorize(self, operation: str) -> None:
        payload = getattr(self._request_context, "payload", None)
        if payload is None:
            raise PermissionError("watch writer transition has no authenticated control request")
        permitted = (
            {"drain", "release"} if payload["operation"] == "drain"
            else {payload["operation"]}
        )
        if operation not in permitted:
            raise PermissionError("watch writer transition differs from its control request")
        if operation in _CLEANUP_OPERATIONS:
            installation = self.invocation.fresh_identity()
            authorize_cleanup(
                installation, self.instance,
                transaction_id=payload["transactionId"], authorization=payload["authorization"],
                environment={},
            )
            self.writer.require_passive()
            return
        if operation == "withdraw":
            from .watch_withdrawal import authorize_withdrawal

            authorize_withdrawal(
                self._installation(), self.instance,
                transaction_id=payload["transactionId"], authorization=payload["authorization"],
            )
            return
        authorize_control(
            self.invocation.fresh_installation(), self.instance, operation=operation,
            transaction_id=payload["transactionId"], authorization=payload["authorization"],
            environment={},
        )

    def health(self) -> dict:
        self._installation()
        return self._health_snapshot()

    def _health_snapshot(self) -> dict:
        return {
            **self.writer.compute("health", {}), "instance": self.instance.evidence(),
            "writerHeld": self.writer.writer_held, "readReady": self.writer.read_ready,
            "effectsEnabled": self.writer.daemon.effects_enabled,
        }

    def compute(self, kind: str, payload: dict) -> dict:
        try:
            if kind == "health":
                return self.health()
            if kind == _CONTROL:
                return self._control(payload)
            self._installation()
            if kind == "shutdown":
                raise PermissionError("governed watch shutdown requires transaction authority")
            return self.writer.compute(kind, payload)
        except (PermissionError, api.InstallationContextError):
            return {"error": "watch-owner-control-refused", "retryable": False}
        except (OSError, ValueError, RuntimeError) as error:
            log.error("Watch owner request failed (%s)", type(error).__name__)
            return {"error": "watch-owner-operation-failed", "retryable": False}
        except Exception as error:
            # CoalescingServer otherwise drops unexpected compute exceptions
            # without a response. Keep this boundary explicit and secret-free.
            log.error("Watch owner internal failure (%s)", type(error).__name__)
            return {"error": "watch-owner-internal-error", "retryable": False}

    def _control(self, payload: dict) -> dict:
        if (
            set(payload) != {
                "operation", "instance", "transactionId", "authorization", "nonce", "timeout",
            }
            or not isinstance(payload["operation"], str)
            or payload["operation"] not in _OPERATIONS | _CLEANUP_OPERATIONS
            or payload["instance"] != self.instance.evidence()
            or not isinstance(payload["transactionId"], str)
            or not isinstance(payload["authorization"], str)
            or not isinstance(payload["nonce"], str)
            or str(UUID(payload["nonce"])) != payload["nonce"]
            or UUID(payload["nonce"]).version != 4
        ):
            raise PermissionError("watch lifecycle request lacks exact identity")
        timeout = positive_seconds(payload["timeout"])
        deadline = time.monotonic() + timeout
        if not self._control_lock.acquire(timeout=timeout):
            raise TimeoutError("watch lifecycle request is busy")
        try:
            self._request_context.payload = payload
            operation = payload["operation"]
            # Writer transitions authorize under their lifecycle lock, including
            # a second check after drain where required. Only these two paths
            # do not enter an authorizing writer transition.
            if operation in {"retire", "cleanup-health"}:
                self._authorize(operation)
            remaining = positive_seconds(deadline - time.monotonic())
            if operation == "drain":
                self.writer.drain_and_release(timeout=remaining)
            elif operation == "adopt":
                self.writer.adopt(timeout=remaining)
            elif operation == "activate":
                self.writer.activate(timeout=remaining)
            elif operation == "resume":
                self.writer.resume(timeout=remaining)
            elif operation == "withdraw":
                self.writer.withdraw(timeout=remaining)
                self._terminal.set()
            elif operation == "cleanup-stop":
                self.writer.close_passive(timeout=remaining)
                self._terminal.set()
            elif operation == "cleanup-health":
                health = self._health_snapshot()
                _passive_health(self.instance, health)
                return {
                    **health, "operation": operation, "nonce": payload["nonce"], "accepted": True,
                }
            else:
                # The core's quiescence event is not process-exit authority.
                # Only an authorized terminal request closes the serve loop.
                self.writer.close(timeout=remaining)
                self._terminal.set()
            return {
                **self._health_snapshot(),
                "operation": operation, "nonce": payload["nonce"],
                "instance": self.instance.evidence(), "accepted": True,
            }
        finally:
            self._request_context.payload = None
            self._control_lock.release()

    def wait_for_shutdown(self, timeout: float | None = None) -> bool:
        return self._terminal.wait(timeout)

    def publish_launch(self, server: WatchControlServer) -> None:
        """Persist private child custody before publishing any shared discovery."""
        from .watch_custody import publish_launch

        rendezvous = server.rendezvous()
        host, port = rendezvous["endpoint"].split(":")
        publish_launch(
            self._installation(), self.instance,
            endpoint=(host, int(port), rendezvous["token"]),
            read_health=self.health, environment={},
        )


class WatchControlClient:
    """ZDD's make_client(base_url) may return this TCP client; no HTTP listener."""

    def __init__(
        self, *, endpoint: tuple[str, int, str], instance: WatchInstance,
        installation: Callable[[], WatchInstallation],
        transaction: Callable[[], tuple[str, str]], request_timeout: float = 30.0,
    ) -> None:
        host, port, token = endpoint
        if (
            host != "127.0.0.1" or type(port) is not int or not 0 < port <= 65535
            or not isinstance(token, str) or not token
        ):
            raise ValueError("watch lifecycle requires an authenticated loopback endpoint")
        self._endpoint = endpoint
        self.instance = instance
        self._installation = installation
        self._transaction = transaction
        self._request_timeout = positive_seconds(request_timeout)

    def _request(self, kind: str, payload: dict, timeout: float) -> dict:
        result = request(
            *self._endpoint, kind=kind, key=uuid4().hex, payload=payload,
            request_deadline_s=positive_seconds(timeout),
            completion_timeout_s=positive_seconds(timeout),
        )
        if "error" in result:
            raise WatchControlRefused("watch owner rejected the authenticated request")
        return result

    def read_health_response(self, *, timeout: float | None = None) -> dict:
        """Authenticated transport only; the journal must bracket this RPC itself."""
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        health = self._request("health", {}, min(self._request_timeout, _remaining(deadline)))
        _remaining(deadline)
        return health

    def health(self, *, timeout: float | None = None) -> dict:
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        installation = self._installation()
        health = None

        def authenticated_health() -> dict:
            nonlocal health
            health = self._request(
                "health", {}, min(self._request_timeout, _remaining(deadline)),
            )
            return health

        self.instance.require_live(
            installation, read_health=authenticated_health, environment={},
        )
        if health is None:
            raise WatchControlRefused("watch health validation did not observe a response")
        if diagnostics.process_start_time(self.instance.pid) != self.instance.start_time:
            raise ProcessLookupError("watch owner changed during its health response")
        _remaining(deadline)
        return health

    def capture_exit(self, *, timeout: float | None = None) -> ProcessExitReference:
        """Native capture validates around the actual authenticated health exchange."""
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        reference = self.instance.capture_exit(
            self._installation(),
            read_health=lambda: self._request(
                "health", {}, min(self._request_timeout, _remaining(deadline)),
            ),
            environment={},
        )
        try:
            _remaining(deadline)
        except TimeoutError:
            reference.close()
            raise
        return reference

    def control(self, operation: str, *, timeout: float | None = None) -> dict:
        if operation not in _OPERATIONS:
            raise ValueError("unsupported watch lifecycle operation")
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        if operation in {"retire", "withdraw"}:
            self.health(timeout=_remaining(deadline))
            return self._control_request(operation, _remaining(deadline))
        response = None

        def authenticated_control():
            nonlocal response
            response = self._control_request(operation, _remaining(deadline))
            return response

        # Bracket the actual acknowledged control, not a separate preliminary
        # health RPC. Terminal controls instead use the pre-captured exit proof.
        self.instance.require_live(
            self._installation(), read_health=authenticated_control, environment={},
        )
        _remaining(deadline)
        if response is None:
            raise WatchControlRefused("watch control validation did not observe a response")
        return response

    def _control_request(self, operation: str, timeout: float) -> dict:
        deadline = time.monotonic() + positive_seconds(timeout)
        transaction_id, authorization = self._transaction()
        nonce = str(uuid4())
        remaining = _remaining(deadline)
        response = self._request(_CONTROL, {
            "operation": operation, "instance": self.instance.evidence(),
            "transactionId": transaction_id, "authorization": authorization,
            "nonce": nonce, "timeout": remaining,
        }, min(self._request_timeout, remaining))
        _remaining(deadline)
        if (
            response.get("accepted") is not True or response.get("operation") != operation
            or response.get("nonce") != nonce or response.get("instance") != self.instance.evidence()
        ):
            raise WatchControlRefused("watch owner returned ambiguous lifecycle acknowledgement")
        return response

    def cleanup_health(self, *, timeout: float | None = None) -> dict:
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        installation = self._installation()
        installation.require_cleanup(environment={})
        self.instance.runtime.require_identity(installation, environment={})
        health = self._control_request(
            "cleanup-health", _remaining(deadline),
        )
        _passive_health(self.instance, health)
        self.instance.require_live_identity(
            installation, read_health=lambda: health, environment={},
        )
        installation.require_cleanup(environment={})
        _remaining(deadline)
        return health

    def capture_cleanup_exit(self) -> ProcessExitReference:
        """Capture exact native exit before issuing the separately authorized stop."""
        installation = self._installation()
        installation.require_cleanup(environment={})
        return self.instance.capture_identity_exit(
            installation, read_health=self.cleanup_health, environment={},
        )

    def cleanup_stop(self, *, timeout: float | None = None) -> dict:
        budget = self._request_timeout if timeout is None else positive_seconds(timeout)
        deadline = time.monotonic() + budget
        self.cleanup_health(timeout=_remaining(deadline))
        return self._control_request(
            "cleanup-stop", _remaining(deadline),
        )

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict:
        if force:
            raise WatchControlRefused("watch writer transfer never permits forced drain")
        positive_seconds(poll)
        deadline = time.monotonic() + positive_seconds(timeout)
        health = self.control("drain", timeout=_remaining(deadline))
        if not all(field in health for field in ("writerHeld", "readReady", "effectsEnabled")):
            health = self.health(timeout=_remaining(deadline))
        if (
            health.get("writerHeld") is not False or health.get("readReady") is not False
            or health.get("effectsEnabled") is not False
        ):
            raise WatchControlRefused("watch predecessor writer release is unconfirmed")
        return {"drained": True, "clean": True, "forced": False, "busy_sessions": []}

    def undrain(self) -> dict:
        return self.control("resume")

    def shutdown(self) -> dict:
        self.control("retire")
        # Native exit confirmation belongs to the driver's pre-captured reference.
        return {"shutting_down": True}

    def adopt_relay(self) -> dict:
        # ZDD's post-commit adoption hook enables effects. The earlier final
        # state reload uses control("adopt") while the journal is released.
        self.control("activate")
        return {"adopted": True}
