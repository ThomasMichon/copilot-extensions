"""Opt-in, in-memory capture from a serving application's actual state.

This module creates no listener and is not wired into automatic repair. Launch
origins must be recorded by the daemon before passive activation overrides.
Legacy daemons lacking that record cannot be upgraded by guessing from disk.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from zdd.private_protocol import MAX_FRAME_BYTES, Deadline, PrivateIPCError, Refusal

from .models import ServiceConfig
from .session_host.private_receipt import VerifiedHostReceipt


@dataclass(frozen=True)
class RuntimeOrigins:
    """Startup-resolved paths and policy, supplied by the serving launch owner.

    Required environment names are an explicit adopter contract, not permission
    to capture every variable or to read the coordinating process's environment.
    Empty means the adopter explicitly requires none.
    """

    config_root: Path
    install_root: Path
    executable: Path
    relay_policy: bool
    port_policy: int
    required_environment: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            any(not isinstance(path, Path) or not path.is_absolute()
                for path in (self.config_root, self.install_root, self.executable))
            or type(self.relay_policy) is not bool
            or type(self.port_policy) is not int or not 0 <= self.port_policy <= 65535
            or type(self.required_environment) is not tuple
            or any(not isinstance(name, str) or not name or "=" in name or "\0" in name
                   for name in self.required_environment)
            or len(set(self.required_environment)) != len(self.required_environment)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)


@dataclass(frozen=True)
class CapturedRuntime:
    """Opaque binding plus immutable private bytes; repr never contains secrets."""

    identity: str
    _payload: bytes = field(repr=False)

    def private_payload(self) -> dict[str, object]:
        """Copy sensitive content only for an already authenticated private frame."""
        return {"identity": self.identity, "runtime": json.loads(self._payload)}


class ServingRuntimeCapture:
    """Capture actual config/token/database/manager and declared serving env.

    The application thread owns capture and comparison. Identity is an opaque
    random binding, not a public hash of low-entropy credential material. A
    private byte-for-byte comparison covers the captured config/manager state;
    any change yields a new binding. Passive bind and draining state are excluded;
    persistent port/relay policy remains part of the captured configuration.
    """

    def __init__(
        self, app: FastAPI, origins: RuntimeOrigins, *, include_resolver: bool = False,
        include_relay: bool = False, include_roster: bool = False,
        include_sessions: bool = False,
        include_worktrees: bool = False,
        include_live_events: bool = False,
        include_authentication: bool = False,
        host_receipts: Mapping[str, VerifiedHostReceipt] | None = None,
        session_deadline: Deadline | None = None,
    ) -> None:
        if not isinstance(origins, RuntimeOrigins):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if (
            any(type(flag) is not bool for flag in (
                include_resolver, include_relay, include_roster, include_sessions,
                include_worktrees,
                include_live_events,
                include_authentication,
            ))
            or (include_roster and not (include_resolver and include_relay))
            or (include_sessions and (
                not include_roster or type(session_deadline) is not Deadline
            ))
            or (not include_sessions and (host_receipts is not None or session_deadline is not None))
            or (include_worktrees and not include_sessions)
            or (include_live_events and not include_worktrees)
            or (include_authentication and not include_live_events)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._app = app
        self._origins = origins
        self._include_resolver = include_resolver
        self._include_relay = include_relay
        self._include_roster = include_roster
        self._include_sessions = include_sessions
        self._include_worktrees = include_worktrees
        self._include_live_events = include_live_events
        self._include_authentication = include_authentication
        self._host_receipts = dict(host_receipts or {})
        self._session_deadline = session_deadline
        self._thread = threading.current_thread()
        self._pid = os.getpid()
        self._last: CapturedRuntime | None = None

    def _gather(self) -> bytes:
        if threading.current_thread() is not self._thread or os.getpid() != self._pid:
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            state = self._app.state
            if not isinstance(state.config, ServiceConfig):
                raise PrivateIPCError(Refusal.AUTHORITY)
            if not isinstance(state.auth_token, str) or not state.auth_token:
                raise PrivateIPCError(Refusal.AUTHORITY)
            if state.session_manager._db is not state.db:
                raise PrivateIPCError(Refusal.AUTHORITY)
            if state.config.remote_acp or state.session_manager._remote_acp_targets:
                raise PrivateIPCError(Refusal.AUTHORITY)
            config = state.config.model_dump(mode="json")
            # The bound passive port is activation state. Durable relay policy
            # was recorded before the old CLI's passive flag overwrote it.
            if getattr(state, "passive", False):
                config["enable_credential_relay"] = self._origins.relay_policy
                config["port"] = self._origins.port_policy
            config["db_path"] = str(Path(state.db.db_path).expanduser().absolute())
            manager = state.session_manager.effective_runtime_settings()
            for key in set(manager) & set(config):
                config[key] = manager[key]
            environment = {
                name: os.environ[name] for name in self._origins.required_environment
                if name in os.environ
            }
            value = {
                "schema": 1,
                "coverage": "config-manager-v1",
                "config": config,
                "manager": manager,
                "auth_token": state.auth_token,
                "config_root": str(self._origins.config_root),
                "install_root": str(self._origins.install_root),
                "predecessor_executable": str(self._origins.executable),
                "relay_policy": self._origins.relay_policy,
                "port_policy": self._origins.port_policy,
                "required_environment": list(self._origins.required_environment),
                "environment": environment,
            }
            if self._include_resolver:
                from .resolver_runtime import capture_resolver

                if (
                    state.topology_ready is not True
                    or state.session_manager._resolver is not state.resolver
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                value["resolver"] = capture_resolver(state.resolver)
                value["coverage"] = "config-manager-resolver-v1"
            if self._include_relay:
                from .relay_runtime import capture_relay

                if state.ready is not True:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                relay = getattr(state, "credential_relay", None)
                if relay is None:
                    if self._origins.relay_policy:
                        raise PrivateIPCError(Refusal.AUTHORITY)
                    value["relay"] = None
                else:
                    value["relay"] = capture_relay(relay)
                    for source in value["relay"]["sources"]:
                        if (
                            source["env_var"] is not None
                            and source["env_var"] not in self._origins.required_environment
                        ):
                            raise PrivateIPCError(Refusal.AUTHORITY)
                value["coverage"] = (
                    "config-manager-resolver-relay-v1" if self._include_resolver
                    else "config-manager-relay-v1"
                )
            if self._include_roster:
                from .roster_runtime import capture_roster

                value["roster"] = capture_roster(state.agent_roster_cache, state.resolver)
                value["coverage"] = "config-manager-resolver-relay-roster-v1"
            if self._include_sessions:
                from .session_snapshot import capture_sessions

                if self._session_deadline is None:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                value["sessions"] = capture_sessions(
                    state.session_manager, self._host_receipts, self._session_deadline,
                )
                value["coverage"] = "config-manager-resolver-relay-roster-session-metadata-v1"
            if self._include_worktrees:
                from .worktree_runtime import capture_worktrees

                value["worktrees"] = capture_worktrees(
                    state.worktree_discovery_cache, state.resolver, state.loop_governance,
                )
                value["coverage"] = "config-manager-resolver-relay-roster-session-metadata-worktrees-v1"
            if self._include_live_events:
                from .live_runtime import capture_live_events

                value["live_events"] = capture_live_events(state.live_event_store)
                value["coverage"] = "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-v1"
            if self._include_authentication:
                from .auth_runtime import capture_authentication

                value["authentication"] = capture_authentication(self._app)
                value["coverage"] = "config-manager-resolver-relay-roster-session-metadata-worktrees-live-events-authentication-v1"
            payload = json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            # Reserve space for the outer private protocol envelope.
            if len(payload) > MAX_FRAME_BYTES - 4096:
                raise PrivateIPCError(Refusal.SIZE)
            return payload
        except PrivateIPCError:
            raise
        except Exception:
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def capture(self) -> CapturedRuntime:
        """Freeze authoritative in-memory state without disk, argv or env export."""
        payload = self._gather()
        if self._last is None or self._last._payload != payload:
            self._last = CapturedRuntime(secrets.token_hex(32), payload)
        return self._last

    def matches(self, captured: CapturedRuntime) -> bool:
        """Privately compare current serving state without exposing its values."""
        return (
            isinstance(captured, CapturedRuntime)
            and self._last is captured
            and self._gather() == captured._payload
        )
