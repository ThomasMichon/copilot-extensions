"""Shared actual local Host/ACP effect checks; journals retain operation identity."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from zdd.diagnostics import process_start_time

from .models import SessionStatus
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer
from .spawn_target import REQUEST_OVERRIDES_KEY

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .db import Database
    from .session_manager import Session, SessionManager
    from .transport import SpawnTarget


def require_local(target: SpawnTarget) -> None:
    if target.type != "local" or any((
        target.spawn_command, target.codespace, target.container, target.host,
        target.elevated, target.remote_acp, target.remote_acp_capabilities,
        target.execution_space_key, target.user, target.ssh_shell,
    )) or set(target.venue or {}) - {REQUEST_OVERRIDES_KEY}:
        raise RuntimeCustodyError("Launch custody supports an exact local target only")


class LocalLaunchFrontend(Protocol):
    manager: SessionManager
    db: Database
    installation: RuntimeInstallation
    admission: RuntimeAdmission
    producer: RuntimeProducer
    database_identity: tuple[int, int]

    def require_current(self) -> None: ...


@dataclass
class LocalLaunchOwner(ABC):
    """One operation's retained handoff and its original live object references."""

    if TYPE_CHECKING:
        frontend: LocalLaunchFrontend
        session_id: str
        native_session_id: str | None
        handoff_id: str

    _session: Session | None = field(default=None, init=False)
    _target: SpawnTarget | None = field(default=None, init=False)
    _target_json: str | None = field(default=None, init=False)
    _client: AcpClient | None = field(default=None, init=False)
    _connection: Any = field(default=None, init=False)
    _host: Any = field(default=None, init=False)
    _host_record: Any = field(default=None, init=False)
    _socket: Any = field(default=None, init=False)
    _streams: Any = field(default=None, init=False)
    _state_file: str | None = field(default=None, init=False)
    _nonce: str | None = field(default=None, init=False)
    _host_process: Any = field(default=None, init=False)
    _host_pid: int | None = field(default=None, init=False)
    _child_pid: int | None = field(default=None, init=False)
    _host_birth: str | None = field(default=None, init=False)
    _child_birth: str | None = field(default=None, init=False)
    _native_started: bool = field(default=False, init=False)

    @property
    @abstractmethod
    def worktree_id(self) -> str: ...

    @abstractmethod
    def read_launch(self) -> dict: ...

    @abstractmethod
    def require_custody(self, *, accepted: bool) -> None: ...

    @abstractmethod
    def begin_launch(self) -> bool: ...

    @abstractmethod
    def report_launch(self, native_session_id: str) -> None: ...

    @abstractmethod
    def child_context(self, child_argv: list[str], cwd: str | None) -> str: ...

    @property
    def admission(self) -> RuntimeAdmission:
        return self.frontend.admission

    def require_current(self, *, accepted: bool = False) -> None:
        self.frontend.require_current()
        self.require_custody(accepted=accepted)
        if self._session is not None:
            if (
                self.frontend.manager.get_session(self.session_id) is not self._session
                or self._session._local_launch_owner is not self
                or self._session.target is not self._target
                or self._target.to_json() != self._target_json
                or self._session.status != SessionStatus.STARTING
                or self._session.acp_session_id != self.native_session_id
                or self._session._handoff_in_progress
                or self._session.client is not None and self._session.client is not self._client
            ):
                raise RuntimeCustodyError("Original local launch session or target changed")
            require_local(self._target)

    def bind(self, manager: SessionManager, session: Session, target: SpawnTarget) -> None:
        self.require_current()
        require_local(target)
        if (
            manager is not self.frontend.manager or manager.get_session(self.session_id) is not session
            or session.session_id != self.session_id or session.target is not target
            or target.worktree_id != self.worktree_id or not target.explicit_cwd
            or session.acp_session_id != self.native_session_id or self._session is not None
            or session._local_launch_owner is not None
            or target.native_resume_id not in (None, self.native_session_id)
        ):
            raise RuntimeCustodyError("Local launch requires its original keyed local session")
        self._session, self._target, self._target_json = session, target, target.to_json()
        session._local_launch_owner = self
        self.require_current()

    def retain_process(self, proc: Any, state_file: str, nonce: str) -> None:
        """Observe the actual Popen before readiness; this never grants new effects."""
        self._host_process, self._host_pid = proc, proc.pid
        self._host_birth = process_start_time(proc.pid)
        self._state_file, self._nonce = state_file, nonce

    def retain_host(self, host: Any) -> None:
        self._host, self._child_pid = host, host.child_pid
        self._child_birth = process_start_time(host.child_pid)
        self._host_record = self.frontend.manager._register_host_launch(host, self.session_id)

    def retain_client(self, client: AcpClient) -> None:
        self._client = client
        self._session.client = client
        client._acquisition_launch = self

    def retain_failed_start(self) -> None:
        """Keep unknown startup custody even if its admission epoch has closed."""
        if self._client is not None:
            self._connection = self._client._connection
        from .session_manager import log

        log.warning("Original local launch %s remains held after startup failure", self.session_id)
        self._session.event_log.append("original_launch_held", {
            "host_pid": self._host_pid, "child_pid": self._child_pid,
            "reason": "startup_outcome_unknown", "retryable": False,
        })

    def require_no_host(self) -> None:
        index = self.frontend.manager._host_index
        if index is None:
            raise RuntimeCustodyError("Local launch resume has no strict host inventory")
        inventory = index.locked_inventory()
        if not inventory.storage_complete or any(
            row.session_id == self.session_id for row in inventory.records
        ):
            raise RuntimeCustodyError("Local launch resume cannot replace unknown or retained host custody")

    def begin_host(self) -> None:
        """Runs in the launch worker, immediately before its actual Popen."""
        self.require_current()
        if self._session is None:
            raise RuntimeCustodyError("Local launch has no bound local session")
        if self.native_session_id is not None:
            self.require_no_host()
        if not self.begin_launch():
            raise RuntimeCustodyError("Original local launch remains held; do not respawn")
        self.require_current()

    def attach_transport(self, client: AcpClient, host: Any) -> None:
        from .acp_client import AcpClient

        self.require_current()
        if (
            type(client) is not AcpClient or self._client is not client or self._host is not host
            or host.boundary != "local"
            or host.proc is None or host.proc.pid != host.host_pid
            or host.proc.poll() is not None
            or client._connection is None or not client._host_mode or not client.is_running
            or client._host_child_pid != host.child_pid or client.acp_session_id is not None
        ):
            raise RuntimeCustodyError("Local launch requires its actual local host transport")
        if (
            not self._host_birth or not self._child_birth
            or process_start_time(host.host_pid) != self._host_birth
            or process_start_time(host.child_pid) != self._child_birth
        ):
            raise RuntimeCustodyError("Local launch host or child birth is unavailable")
        self._host, self._client, self._connection = host, client, client._connection
        self._host_process, self._host_pid, self._child_pid = host.proc, host.host_pid, host.child_pid
        client._acquisition_launch = self

    def _require_transport(self, client: AcpClient) -> None:
        host = self._host
        if (
            client is not self._client or client._connection is not self._connection
            or host is None or host.proc is not self._host_process
            or host.host_pid != self._host_pid or host.child_pid != self._child_pid
            or self._host_process.poll() is not None
            or not client._host_mode or not client.is_running
            or client._host_child_pid != self._child_pid
            or client._remote_transport is not None or client._idle_transport is not None
            or process_start_time(self._host_pid) != self._host_birth
            or process_start_time(self._child_pid) != self._child_birth
        ):
            raise RuntimeCustodyError("Original local launch native transport changed")

    def require_configuration(self, client: AcpClient) -> None:
        self.require_current(accepted=True)
        self._require_transport(client)
        if client.acp_session_id != self.read_launch()["result"]["native_session_id"]:
            raise RuntimeCustodyError("Original local launch native configuration identity changed")

    def native_method(
        self, client: AcpClient, mode: str, method: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        async def invoke(**kwargs: Any) -> Any:
            self.require_current()
            self._require_transport(client)
            if (
                self._native_started
                or mode != ("load" if self.native_session_id is not None else "new")
                or kwargs.get("cwd") != self._target.cwd
                or mode == "load" and kwargs.get("session_id") != self.native_session_id
            ):
                raise RuntimeCustodyError("Original local launch native effect changed or remains held")
            if self.read_launch()["started_at"] is None:
                raise RuntimeCustodyError("Local launch host never reached final admission")
            self._native_started = True
            result = await method(**kwargs)
            self.report_launch(self.native_session_id if mode == "load" else result.session_id)
            return result

        return invoke
