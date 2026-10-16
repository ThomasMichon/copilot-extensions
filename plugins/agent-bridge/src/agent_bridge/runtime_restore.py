"""Strict private successor-state preparation, without application activation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from credential_relay.server import CredentialRelayServer
from credential_relay.sources.injected_token import InjectedTokenSource
from pydantic import TypeAdapter
from zdd.private_protocol import MAX_FRAME_BYTES, PrivateIPCError, Refusal

from .agent_registry_resolver import AgentResolver
from .auth_runtime import RestoredAuthentication, restore_authentication
from .db import Database
from .effective_runtime import RuntimeOrigins
from .install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV
from .live_representation import LiveEventStore
from .live_runtime import capture_live_events, restore_live_events
from .models import (
    AutoHandoffPolicy, ContextThresholds, PhasedTimeouts, RetentionConfig, ServiceConfig,
)
from .relay_runtime import restore_relay
from .resolver_runtime import _encode, restore_resolver
from .roster_runtime import FrozenRoster, restore_roster
from .session_manager import SessionManager, session_manager_from_config
from .session_snapshot import SessionSnapshot, decode_sessions
from .worktree_runtime import FrozenWorktrees, restore_worktrees


@dataclass(frozen=True)
class _ManagerState:
    context_thresholds: ContextThresholds
    auto_handoff: AutoHandoffPolicy
    timeouts: PhasedTimeouts
    retention: RetentionConfig
    session_host_state_dir: str
    session_host_stale_reap_seconds: int
    graceful_cancel_settle_seconds: int
    cancel_turns_on_redeploy: bool
    idle_reap_ttl_seconds: int
    live_stall_interrupt_after_s: int
    session_host_unexpected_reap_seconds: int
    session_host_active_reap_seconds: int
    drain_auto_release_s: float
    drain_warn_interval_s: float


@dataclass
class _RuntimeState:
    schema: Literal[1]
    coverage: Literal[
        "config-manager-resolver-relay-v1", "config-manager-resolver-relay-roster-v1",
        "config-manager-resolver-relay-roster-session-metadata-v1",
        "config-manager-resolver-relay-roster-session-metadata-worktrees-v1",
        "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-v1",
        "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-authentication-v1",
    ]
    config: ServiceConfig
    manager: _ManagerState
    auth_token: str
    config_root: str
    install_root: str
    predecessor_executable: str
    relay_policy: bool
    port_policy: int
    required_environment: list[str]
    environment: dict[str, str]
    resolver: dict[str, object]
    relay: dict[str, object] | None
    roster: dict[str, object] | None = None
    sessions: dict[str, object] | None = None
    worktrees: dict[str, object] | None = None
    live_events: dict[str, object] | None = None
    authentication: dict[str, object] | None = None


@dataclass
class _Envelope:
    identity: str
    runtime: _RuntimeState


_ADAPTER = TypeAdapter(_Envelope)
_MANAGER_ADAPTER = TypeAdapter(_ManagerState)
_MANAGER_ONLY = {
    "session_host_state_dir", "drain_auto_release_s", "drain_warn_interval_s",
}


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not value or "\0" in value or not path.is_absolute() or ".." in path.parts:
        raise PrivateIPCError(Refusal.AUTHORITY)
    return path


@dataclass(frozen=True)
class RestoredRuntime:
    """Prepared, detached state for a separately authorized passive successor.

    Mutable runtime objects are owned by the recipient. This is not readiness,
    custody or permission to bind/publish/adopt; those require native checks.
    """

    identity: str
    config: ServiceConfig = field(repr=False)
    auth_token: str = field(repr=False)
    origins: RuntimeOrigins = field(repr=False)
    resolver: AgentResolver = field(repr=False)
    relay: CredentialRelayServer | None = field(repr=False)
    _manager: _ManagerState = field(repr=False)
    _environment: dict[str, str] = field(repr=False)
    _config_wire: bytes = field(repr=False)
    roster: FrozenRoster | None = field(default=None, repr=False)
    sessions: SessionSnapshot | None = field(default=None, repr=False)
    _session_wire: bytes | None = field(default=None, repr=False)
    worktrees: FrozenWorktrees | None = field(default=None, repr=False)
    live_events: LiveEventStore | None = field(default=None, repr=False)
    _live_wire: bytes | None = field(default=None, repr=False)
    authentication: RestoredAuthentication | None = field(default=None, repr=False)

    def environment_for_child(self, base: Mapping[str, str]) -> dict[str, str]:
        """Return a child-only overlay, preserving required-variable absence."""
        environment = dict(base)
        for name in self.origins.required_environment:
            environment.pop(name, None)
        environment.update(self._environment)
        environment[CONFIG_DIR_ENV] = str(self.origins.config_root)
        environment[INSTALL_DIR_ENV] = str(self.origins.install_root)
        return environment

    def build_manager(self, db: Database) -> SessionManager:
        """Construct through the existing owner only for the captured database."""
        try:
            return self._build_manager(db)
        except PrivateIPCError:
            raise
        except (TypeError, ValueError, AttributeError, OSError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def validate_configuration(self) -> None:
        """Refuse modified configuration before opening any serving resource."""
        try:
            if _encode(self.config.model_dump(mode="json")) != self._config_wire:
                raise PrivateIPCError(Refusal.AUTHORITY)
            if (
                (_encode(asdict(self.sessions)) if self.sessions is not None else None)
                != self._session_wire
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if self.worktrees is not None:
                self.worktrees.private_payload()
            if self.authentication is not None:
                self.authentication.private_payload()
            if (
                (capture_live_events(self.live_events) if self.live_events is not None else None)
                != (json.loads(self._live_wire) if self._live_wire is not None else None)
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        except (TypeError, ValueError, AttributeError, RecursionError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def _build_manager(self, db: Database) -> SessionManager:
        self.validate_configuration()
        if (
            type(db) is not Database
            or Path(db.db_path).expanduser().absolute() != _absolute_path(self.config.db_path)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        manager = session_manager_from_config(
            db, self.config, session_host_state_dir=self._manager.session_host_state_dir,
            drain_auto_release_s=self._manager.drain_auto_release_s,
            drain_warn_interval_s=self._manager.drain_warn_interval_s,
            read_only_rehydrate=True,
            captured_sessions=self.sessions,
        )
        manager.set_resolver(self.resolver)
        expected = json.loads(_MANAGER_ADAPTER.dump_json(self._manager))
        if _encode(manager.effective_runtime_settings()) != _encode(expected):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return manager


def restore_runtime(payload: object) -> RestoredRuntime:
    """Validate complete private coverage before creating any serving resources.

    Unknown/missing fields, coercions, config/manager disagreement, unsupported
    providers and partial captures refuse with a fixed, secret-redacted reason.
    No database, listener, task, provider process or environment export occurs.
    """
    try:
        wire = _encode(payload)
        if len(wire) > MAX_FRAME_BYTES - 2048:
            raise PrivateIPCError(Refusal.SIZE)
        envelope = _ADAPTER.validate_json(wire, strict=True)
        decoded = json.loads(_ADAPTER.dump_json(envelope))
        if envelope.runtime.coverage == "config-manager-resolver-relay-v1":
            decoded["runtime"].pop("roster")
        with_authentication = envelope.runtime.coverage.endswith("-live-events-authentication-v1")
        with_live_events = with_authentication or envelope.runtime.coverage.endswith("-worktrees-live-events-v1")
        with_worktrees = with_live_events or (
            envelope.runtime.coverage == "config-manager-resolver-relay-roster-session-metadata-worktrees-v1"
        )
        if not with_live_events:
            decoded["runtime"].pop("live_events")
        if not with_authentication:
            decoded["runtime"].pop("authentication")
        if not with_worktrees:
            decoded["runtime"].pop("worktrees")
        if envelope.runtime.coverage not in (
            "config-manager-resolver-relay-roster-session-metadata-v1",
            "config-manager-resolver-relay-roster-session-metadata-worktrees-v1",
            "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-v1",
            "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-authentication-v1",
        ):
            decoded["runtime"].pop("sessions")
        if (
            _encode(decoded) != wire
            or (envelope.runtime.coverage == "config-manager-resolver-relay-roster-v1"
                and envelope.runtime.roster is None)
            or (envelope.runtime.coverage in (
                "config-manager-resolver-relay-roster-session-metadata-v1",
                "config-manager-resolver-relay-roster-session-metadata-worktrees-v1",
                "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-v1",
                "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-authentication-v1",
            )
                and (envelope.runtime.roster is None or envelope.runtime.sessions is None))
            or (with_worktrees and envelope.runtime.worktrees is None)
            or (with_live_events and envelope.runtime.live_events is None)
            or (with_authentication and envelope.runtime.authentication is None)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if re.fullmatch(r"[0-9a-f]{64}", envelope.identity) is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        state = envelope.runtime
        origins = RuntimeOrigins(
            _absolute_path(state.config_root), _absolute_path(state.install_root),
            _absolute_path(state.predecessor_executable), state.relay_policy,
            state.port_policy, tuple(state.required_environment),
        )
        _absolute_path(state.config.db_path)
        _absolute_path(state.manager.session_host_state_dir)
        for name, root in (
            (CONFIG_DIR_ENV, origins.config_root), (INSTALL_DIR_ENV, origins.install_root),
        ):
            if name in state.required_environment and state.environment.get(name) != str(root):
                raise PrivateIPCError(Refusal.AUTHORITY)
        if (
            not state.auth_token or "\0" in state.auth_token
            or not set(state.environment) <= set(state.required_environment)
            or any("\0" in value for value in state.environment.values())
            or not 0 <= state.config.port <= 65535
            or not state.config.bind or "\0" in state.config.bind
            or (state.relay is None and state.relay_policy)
            or state.manager.drain_auto_release_s < 0
            or state.manager.drain_warn_interval_s < 0
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        manager = json.loads(_MANAGER_ADAPTER.dump_json(state.manager))
        config = state.config.model_dump(mode="json")
        if any(_encode(value) != _encode(config[key])
               for key, value in manager.items() if key not in _MANAGER_ONLY):
            raise PrivateIPCError(Refusal.AUTHORITY)
        resolver = restore_resolver(state.resolver)
        relay = restore_relay(state.relay) if state.relay is not None else None
        roster = restore_roster(state.roster, resolver) if state.roster is not None else None
        sessions = decode_sessions(state.sessions) if state.sessions is not None else None
        worktrees = restore_worktrees(state.worktrees, resolver) if state.worktrees is not None else None
        live_events = restore_live_events(state.live_events) if state.live_events is not None else None
        authentication = (
            restore_authentication(state.authentication) if state.authentication is not None else None
        )
        if relay is not None:
            for source in relay.sources:
                if (
                    type(source) is InjectedTokenSource
                    and source._env_var not in state.required_environment
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
        return RestoredRuntime(
            envelope.identity, state.config, state.auth_token, origins, resolver,
            relay, state.manager, dict(state.environment), _encode(config), roster, sessions,
            _encode(asdict(sessions)) if sessions is not None else None,
            worktrees, live_events,
            _encode(state.live_events) if state.live_events is not None else None,
            authentication,
        )
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
