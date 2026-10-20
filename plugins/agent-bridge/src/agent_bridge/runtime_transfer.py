"""Opt-in private candidate transfer; acknowledgement is not adoption authority."""

from __future__ import annotations

import os
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from zdd.diagnostics import process_start_time
from zdd.private_child import OwnedChild
from zdd.private_protocol import (
    Channel, Deadline, PeerIdentity, PrivateIPCError, Refusal, read_frame, write_frame,
)
from zdd.private_windows_native import Native
from zdd.routing import Endpoint

from .effective_runtime import CapturedRuntime, ServingRuntimeCapture
from .db import Database, SCHEMA_VERSION
from .db_adoption import open_existing_writable
from .events import EventLog
from .install_paths import effective_config_dir, install_dir
from .launch_runtime import executing_executable
from .predecessor_runtime import PredecessorBinding, RetainedChannel
from .relay_runtime import capture_relay
from .resolver_runtime import _encode, capture_resolver
from .runtime_restore import RestoredRuntime, restore_runtime

if TYPE_CHECKING:
    from .runtime_idle import PendingIdleSession
    from .serving_runtime import CandidateServing
    from .session_host.private_receipt import VerifiedHostReceipt
    from .staged_relay import StagedRelay


class AuthenticatedChannel(Channel, RetainedChannel, Protocol):
    """A channel that can revalidate its held native peer before transfer."""

    def check_peer(self, deadline: Deadline) -> None: ...


def _namespace(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PrivateIPCError(Refusal.AUTHORITY)


def _peer(channel: AuthenticatedChannel, expected: PeerIdentity, deadline: Deadline) -> None:
    if not isinstance(expected, PeerIdentity):
        raise PrivateIPCError(Refusal.IDENTITY)
    channel.check_peer(deadline)
    if channel.peer != expected:
        raise PrivateIPCError(Refusal.IDENTITY)


def _self() -> PeerIdentity:
    token = process_start_time(os.getpid())
    if token is None:
        raise PrivateIPCError(Refusal.IDENTITY)
    principal = Native().current_sid() if os.name == "nt" else str(os.geteuid())
    return PeerIdentity(os.getpid(), token, principal)


def _ack(
    runtime: RestoredRuntime, namespace: str, executable: Path, peer: PeerIdentity,
) -> dict[str, object]:
    return {
        "operation": "candidate-prepared", "namespace": namespace,
        "identity": runtime.identity, "pid": peer.pid, "start_time": peer.start_time,
        "principal": peer.principal, "executable": str(executable),
        "config_root": str(runtime.origins.config_root),
        "install_root": str(runtime.origins.install_root),
        "database": runtime.config.db_path, "candidate_ready": True, "ready": False,
    }


def transfer_runtime(
    channel: AuthenticatedChannel, child: OwnedChild, capture: ServingRuntimeCapture,
    captured: CapturedRuntime, *, namespace: str, deadline: Deadline,
) -> None:
    """Send only to a native-pinned child and verify its exact passive receipt.

    A stale capture, changed peer/image, incomplete acknowledgement or deadline
    refuses. No routing, drain, retirement or app-activation permission follows.
    """
    _namespace(namespace)
    if type(child) is not OwnedChild:
        raise PrivateIPCError(Refusal.IDENTITY)
    child.check(deadline)
    _peer(channel, child.peer, deadline)
    if not capture.matches(captured):
        raise PrivateIPCError(Refusal.AUTHORITY)
    runtime = restore_runtime(captured.private_payload())
    write_frame(channel, {
        "operation": "prepare-candidate", "namespace": namespace,
        "executable": str(child.executable), "capture": captured.private_payload(),
    }, deadline)
    reply = read_frame(channel, deadline)
    child.check(deadline)
    _peer(channel, child.peer, deadline)
    expected = _ack(runtime, namespace, child.executable, child.peer)
    # JSON booleans are not interchangeable with 0/1 acknowledgement fields.
    if (
        set(reply) != set(expected)
        or any(type(reply[key]) is not type(value) or reply[key] != value
               for key, value in expected.items())
        or not capture.matches(captured)
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    deadline.remaining()


@dataclass(frozen=True)
class PendingRuntime:
    """Recipient-owned preparation, bound to one channel and process birth."""

    runtime: RestoredRuntime = field(repr=False)
    _namespace: str = field(repr=False)
    _executable: Path = field(repr=False)
    _self_peer: PeerIdentity = field(repr=False)
    _parent: PeerIdentity = field(repr=False)
    _channel: AuthenticatedChannel = field(repr=False)
    _thread: threading.Thread = field(repr=False)
    _deadline: Deadline = field(repr=False)
    _state_wire: bytes = field(repr=False)
    _acknowledged: bool = field(default=False, repr=False, init=False)
    _receipt_sent: bool = field(default=False, repr=False, init=False)
    _passive_database: Database | None = field(default=None, repr=False, init=False)
    _writable_database: Database | None = field(default=None, repr=False, init=False)
    _serving: CandidateServing | None = field(default=None, repr=False, init=False)
    _predecessor: PredecessorBinding | None = field(default=None, repr=False, init=False)
    _binding_attempted: bool = field(default=False, repr=False, init=False)
    _relay_stage: StagedRelay | None = field(default=None, repr=False, init=False)
    _relay_attempted: bool = field(default=False, repr=False, init=False)
    _event_logs: tuple[tuple[str, EventLog], ...] = field(
        default=(), repr=False, init=False,
    )
    _idle_sessions: dict[str, PendingIdleSession] = field(
        default_factory=dict, repr=False, init=False,
    )
    _idle_writer: threading.Thread | None = field(default=None, repr=False, init=False)

    @property
    def prepared(self) -> bool:
        """Whether the one-use passive receipt was completely written."""
        return self._receipt_sent

    @property
    def serving(self) -> bool:
        """Foreground serving is distinct from singleton and host adoption."""
        return self._serving is not None and self._serving.active

    def bind_predecessor(self, app: FastAPI, endpoint: Endpoint) -> None:
        """Opt in before acknowledgement using independent publication evidence.

        The candidate lifespan owns successful custody; failed acquisition
        releases its duplicate immediately. Neither outcome grants ADOPT.
        """
        if (
            self._binding_attempted or self._acknowledged
            or threading.current_thread() is not self._thread
            or _self() != self._self_peer
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        object.__setattr__(self, "_binding_attempted", True)
        self.verify_candidate(app)
        if getattr(app.state, "pending_runtime", None) is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        binding = PredecessorBinding.acquire(
            self._channel, self._parent, self._self_peer, endpoint=endpoint,
            identity=self.runtime.identity, root=self.runtime.origins.config_root,
            deadline=self._deadline,
        )
        try:
            self.verify_candidate(app)
            self._deadline.remaining()
        except BaseException:
            binding.close()
            raise
        object.__setattr__(self, "_predecessor", binding)
        app.state.pending_runtime = self

    def require_predecessor_exit(self, endpoint: Endpoint, deadline: Deadline) -> None:
        """Check exact exit evidence, not a coordinator's claim or channel loss."""
        if (
            not self.prepared or self._predecessor is None
            or threading.current_thread() is not self._thread or _self() != self._self_peer
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._predecessor.require_exit(endpoint, self.runtime.identity, deadline)

    def close(self) -> None:
        """Release candidate-owned exit custody without signalling the parent."""
        if threading.current_thread() is not self._thread or _self() != self._self_peer:
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            if self._relay_stage is not None:
                self._relay_stage.close()
        finally:
            if self._predecessor is not None:
                self._predecessor.close()

    async def close_relay(self, deadline: Deadline) -> None:
        """Finish only this candidate's relay cleanup outside routing authority."""
        if threading.current_thread() is not self._thread or _self() != self._self_peer:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if self._relay_stage is not None:
            await self._relay_stage.wait_closed(deadline)

    async def close_idle_sessions(self) -> None:
        """Detach only registered idle owners before closing shared resources."""
        for binding in self._idle_sessions.values():
            await binding.aclose()

    def acknowledge(self, app: FastAPI) -> None:
        """Release retained custody if passive acknowledgement fails."""
        try:
            self._acknowledge(app)
        except BaseException:
            if threading.current_thread() is self._thread and _self() == self._self_peer:
                if self._binding_attempted:
                    object.__setattr__(self, "_acknowledged", True)
                    object.__setattr__(self, "_receipt_sent", False)
                self.close()
            raise

    def _acknowledge(self, app: FastAPI) -> None:
        """Acknowledge only a healthy read-only app on the receiving owner thread."""
        if (
            self._acknowledged or threading.current_thread() is not self._thread
            or _self() != self._self_peer
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        if self._binding_attempted and self._predecessor is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        _peer(self._channel, self._parent, self._deadline)
        self.verify_candidate(app)
        if self._predecessor is not None:
            self._predecessor.verify_preparation(
                self._channel, self.runtime.identity, self.runtime.origins.config_root,
                self._deadline,
            )
        object.__setattr__(self, "_passive_database", app.state.db)
        logs = []
        for session_id, session in app.state.session_manager._sessions.items():
            if type(session.event_log) is not EventLog or session.event_log._db is not app.state.db:
                raise PrivateIPCError(Refusal.AUTHORITY)
            logs.append((session_id, session.event_log))
        object.__setattr__(self, "_event_logs", tuple(logs))
        # Consume before writing: an uncertain write must not permit replay.
        object.__setattr__(self, "_acknowledged", True)
        write_frame(
            self._channel,
            _ack(self.runtime, self._namespace, self._executable, self._self_peer),
            self._deadline,
        )
        object.__setattr__(self, "_receipt_sent", True)

    def verify_candidate(self, app: FastAPI) -> None:
        """Recheck passive state without granting mutation or renewing a receipt.

        Later receiver grants have their own original deadlines and native peer
        checks; they must not depend on the expired preparation channel.
        """
        if self._writable_database is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.verify_resources(app)

    def verify_resources(self, app: FastAPI) -> None:
        """Verify immutable state at passive, writable and foreground stages."""
        if threading.current_thread() is not self._thread or _self() != self._self_peer:
            raise PrivateIPCError(Refusal.IDENTITY)
        self.runtime.validate_configuration()
        try:
            state = app.state
            if (
                state.restored_runtime is not self.runtime
                or state.candidate_ready is not True or state.ready is not self.serving
                or state.passive is not (not self.serving) or state.publish_on_ready is not False
                or state.runtime_executable != self._executable
                or executing_executable() != self._executable
                or type(state.db) is not Database
                or (
                    self._passive_database is not None and self._writable_database is None
                    and state.db is not self._passive_database
                )
                or state.db.read_only is not (self._writable_database is None)
                or (
                    self._writable_database is not None
                    and state.db is not self._writable_database
                )
                or (self._serving is None and self._idle_writer is None and (
                    state.db._writer_thread is not None or state.db._event_write_q.unfinished_tasks
                ))
                or (self._idle_writer is not None and (
                    state.db._writer_thread is not self._idle_writer
                    or not self._idle_writer.is_alive() or state.db._writer_error is not None
                ))
                or state.session_manager._db is not state.db
                or Path(state.db.db_path).resolve() != Path(self.runtime.config.db_path).resolve()
                or state.resolver is not self.runtime.resolver
                or state.session_manager._resolver is not state.resolver
                or state.credential_relay is not self.runtime.relay
                or state.credential_relay_ready is not (
                    self._relay_stage is not None and self._relay_stage.active
                )
                or (self.runtime.relay is not None and (
                    self.runtime.relay.running is not (
                        self._relay_stage is not None and self._relay_stage.active
                    )
                ))
                or effective_config_dir().resolve() != self.runtime.origins.config_root
                or install_dir().resolve() != self.runtime.origins.install_root
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self._serving is not None:
                self._serving.verify()
            if self._relay_stage is not None:
                self._relay_stage.verify()
            for session_id, event_log in self._event_logs:
                session = state.session_manager._sessions.get(session_id)
                if (
                    event_log._db is not state.db
                    or (not self.serving and (
                        session is None or session.event_log is not event_log
                    ))
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
            stat = state.db.db_path.stat()
            if (stat.st_dev, stat.st_ino) != state.db._read_only_file_id:
                raise PrivateIPCError(Refusal.AUTHORITY)
            schema = state.db.execute_read("SELECT version FROM schema_version")
            if len(schema) != 1 or schema[0]["version"] != SCHEMA_VERSION:
                raise PrivateIPCError(Refusal.AUTHORITY)
            expected = json.loads(self._state_wire)
            actual = {
                "config": state.config.model_dump(mode="json"),
                "manager": state.session_manager.effective_runtime_settings(),
                "auth_token": state.auth_token,
                "resolver": capture_resolver(state.resolver),
                "relay": capture_relay(state.credential_relay)
                if state.credential_relay is not None else None,
                "environment": {
                    name: os.environ[name] for name in self.runtime.origins.required_environment
                    if name in os.environ
                },
            }
            if any(_encode(value) != _encode(expected[key]) for key, value in actual.items()):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self.runtime.roster is not None and (
                _encode(self.runtime.roster.private_payload()) != _encode(expected["roster"])
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self.runtime.worktrees is not None and (
                state.worktree_discovery_cache is not self.runtime.worktrees
                or _encode(self.runtime.worktrees.private_payload()) != _encode(expected["worktrees"])
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self.runtime.live_events is not None:
                from .live_runtime import capture_live_events

                if (
                    state.live_event_store is not self.runtime.live_events
                    or _encode(capture_live_events(self.runtime.live_events)) != _encode(expected["live_events"])
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
            if self.runtime.authentication is not None:
                self.runtime.authentication.verify(app)
                if (
                    _encode(self.runtime.authentication.private_payload())
                    != _encode(expected["authentication"])
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
        except (
            AttributeError, OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error,
        ):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def activate_resources(self, app: FastAPI, deadline: Deadline) -> None:
        """Adopt a writable owner only inside the receiver's active-route fence."""
        if not self.prepared:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.verify_candidate(app)
        deadline.remaining()
        db = open_existing_writable(app.state.db)
        try:
            deadline.remaining()
            self.verify_candidate(app)
        except BaseException:
            db.close()
            raise
        app.state.db = db
        app.state.session_manager._db = db
        for _session_id, event_log in self._event_logs:
            event_log._db = db
        object.__setattr__(self, "_writable_database", db)

    def prepare_idle_session(
        self, app: FastAPI, frontend: Endpoint, original: VerifiedHostReceipt,
        continuation: object, durability: object, deadline: Deadline,
    ) -> PendingIdleSession:
        """Bind one native idle owner to already granted runtime resources.

        The passive app still exposes no session routes. This is not nonempty
        serving admission, host-index ownership or full maintenance ADOPT.
        """
        from .acp_continuation import decode_idle_continuation
        from .runtime_idle import PendingIdleSession

        self.verify_resources(app)
        session_id = decode_idle_continuation(continuation).host.session_id
        bound = getattr(app.state, "pending_runtime", None)
        if (
            session_id in self._idle_sessions or self._serving is not None
            or (bound is not None and bound is not self)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        binding = PendingIdleSession(
            self, app, frontend, original, continuation, durability, deadline,
        )
        self._idle_sessions[session_id] = binding
        app.state.pending_runtime = self
        if self._idle_writer is None:
            binding.database.start_writer()
            object.__setattr__(self, "_idle_writer", binding.database._writer_thread)
        binding.verify(deadline)
        return binding

    def prepare_serving(self, app: FastAPI, deadline: Deadline) -> None:
        """Prepare an unreachable serving graph outside the receiver routing fence.

        The previously granted writable resource owner is mandatory. The worker
        remains idle: no API traffic, provider scans or queued writes exist.
        Failure retains cleanup ownership and never grants readiness or retry.
        """
        if not self.prepared or self._writable_database is None or self._serving is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.verify_resources(app)
        deadline.remaining()
        from .serving_runtime import CandidateServing

        serving = CandidateServing(app, self.runtime)
        object.__setattr__(self, "_serving", serving)
        serving.prepare_writer()
        deadline.remaining()
        self.verify_resources(app)
        deadline.remaining()
        serving.prepared = True

    def activate_serving(self, app: FastAPI, deadline: Deadline) -> None:
        """Commit actual prepared serving only inside a fresh active-route grant."""
        self.verify_resources(app)
        deadline.remaining()
        if self._serving is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._serving.activate()

    def reserve_relay(self, app: FastAPI, predecessor: Endpoint, deadline: Deadline) -> None:
        """Reserve the captured port under a fresh grant after exact native exit."""
        self.verify_resources(app)
        self.require_predecessor_exit(predecessor, deadline)
        if (
            not self.serving or self.runtime.relay is None
            or self._relay_attempted or self._relay_stage is not None
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        object.__setattr__(self, "_relay_attempted", True)
        from .staged_relay import StagedRelay

        stage = StagedRelay(self.runtime.relay, deadline)
        object.__setattr__(self, "_relay_stage", stage)
        app.state.pending_runtime = self

    async def prepare_relay(self, app: FastAPI) -> None:
        """Prepare the previously granted transport, never credential admission."""
        self.verify_resources(app)
        if self._relay_stage is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        await self._relay_stage.prepare()
        try:
            self.verify_resources(app)
        except BaseException:
            self._relay_stage.close()
            raise

    def activate_relay(self, app: FastAPI, predecessor: Endpoint, deadline: Deadline) -> None:
        """Commit this singleton only; full host/maintenance ADOPT stays absent."""
        self.verify_resources(app)
        self.require_predecessor_exit(predecessor, deadline)
        if self._relay_stage is None or not self.serving:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._relay_stage.activate(deadline)
        app.state.credential_relay_ready = True


def receive_runtime(
    channel: AuthenticatedChannel, expected_parent: PeerIdentity, *,
    namespace: str, deadline: Deadline,
) -> PendingRuntime:
    """Receive complete private state from an independently registered parent.

    Launch supplies the expected native parent and opaque namespace before any
    frame is read. The request's PID, roots or executable are never identity proof.
    The caller prepares the app and acknowledges on this same owner thread.
    """
    _namespace(namespace)
    _peer(channel, expected_parent, deadline)
    own = _self()
    request = read_frame(channel, deadline)
    if (
        set(request) != {"operation", "namespace", "executable", "capture"}
        or request["operation"] != "prepare-candidate"
        or request["namespace"] != namespace
        or type(request["executable"]) is not str
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    executable = executing_executable()
    if request["executable"] != str(executable):
        raise PrivateIPCError(Refusal.IDENTITY)
    captured_payload = request["capture"]
    if not isinstance(captured_payload, dict):
        raise PrivateIPCError(Refusal.AUTHORITY)
    runtime = restore_runtime(captured_payload)
    _peer(channel, expected_parent, deadline)
    deadline.remaining()
    return PendingRuntime(
        runtime, namespace, executable, own, expected_parent, channel,
        threading.current_thread(), deadline, _encode(captured_payload["runtime"]),
    )
