"""Explicit original local acquisition owners, not automatic runtime enrollment."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any
import uuid

from zdd.diagnostics import process_start_time

from .acquisition_contract import AcquisitionRequest
from .frontend_process import frontend_producer, require_frontend_process
from .models import SessionStatus
from .spawn_target import REQUEST_OVERRIDES_KEY
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
)

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .acquisition_runtime import AcquisitionRuntime
    from .db import Database
    from .session_manager import Session, SessionManager
    from .transport import SpawnTarget


def require_local(target: SpawnTarget) -> None:
    if target.type != "local" or any((
        target.spawn_command, target.codespace, target.container, target.host,
        target.elevated, target.remote_acp, target.remote_acp_capabilities,
        target.execution_space_key, target.user, target.ssh_shell,
    )) or set(target.venue or {}) - {REQUEST_OVERRIDES_KEY}:
        raise RuntimeCustodyError("Acquisition custody supports an exact local target only")


@dataclass(frozen=True)
class AcquisitionFrontend:
    runtime: AcquisitionRuntime
    manager: SessionManager
    db: Database
    installation: RuntimeInstallation
    admission: RuntimeAdmission
    producer: RuntimeProducer
    database_identity: tuple[int, int]

    @classmethod
    def capture(
        cls, runtime: AcquisitionRuntime, installation: RuntimeInstallation,
        admission: RuntimeAdmission,
    ) -> AcquisitionFrontend:
        db = runtime.db
        if runtime.manager.db is not db or type(admission) is not RuntimeAdmission:
            raise RuntimeCustodyError("Acquisition custody requires its actual frontend owner")
        producer = frontend_producer(db, installation, admission)
        stat = Path(installation.database).stat()
        owner = cls(
            runtime, runtime.manager, db, installation, admission, producer, (stat.st_dev, stat.st_ino),
        )
        owner.require_current()
        return owner

    def require_current(self) -> None:
        db = self.db
        if (
            self.runtime.manager is not self.manager or self.manager.db is not db
            or self.runtime.db is not db or self.runtime.store.journal.db is not db
        ):
            raise RuntimeCustodyError("Original acquisition frontend changed")
        if self.manager.is_draining:
            raise RuntimeCustodyError("Original acquisition frontend is draining")
        require_frontend_process(
            db, self.installation, self.admission, self.producer, self.database_identity,
        )

    def prepare(
        self, request: AcquisitionRequest, *, session_id: str, native_session_id: str | None,
    ) -> AcquisitionLaunchOwner:
        self.require_current()
        original = request.model_copy(deep=True)
        journal = self.runtime.store.journal
        handoff = journal.prepare_launch(
            original, self.installation, self.admission, session_id=session_id,
            native_session_id=native_session_id, consumer_incarnation=uuid.uuid4().hex,
        )
        return AcquisitionLaunchOwner(self, original, session_id, native_session_id, handoff["id"])


@dataclass
class AcquisitionLaunchOwner:
    frontend: AcquisitionFrontend
    request: AcquisitionRequest
    session_id: str
    native_session_id: str | None
    handoff_id: str
    _session: Session | None = field(default=None, init=False)
    _target: SpawnTarget | None = field(default=None, init=False)
    _target_json: str | None = field(default=None, init=False)
    _client: AcpClient | None = field(default=None, init=False)
    _connection: Any = field(default=None, init=False)
    _host: Any = field(default=None, init=False)
    _host_process: Any = field(default=None, init=False)
    _host_pid: int | None = field(default=None, init=False)
    _child_pid: int | None = field(default=None, init=False)
    _host_birth: str | None = field(default=None, init=False)
    _child_birth: str | None = field(default=None, init=False)
    _native_started: bool = field(default=False, init=False)

    @property
    def admission(self) -> RuntimeAdmission:
        return self.frontend.admission

    def require_current(self, *, accepted: bool = False) -> None:
        self.frontend.require_current()
        journal = self.frontend.runtime.store.journal
        report = journal.read_launch(self.request, self.handoff_id)
        _token, stage, _data = self.frontend.runtime.store.recovery(self.request)
        if (
            report["handoff"]["admission"] != self.admission.document()
            or report["handoff"]["consumer"]["session_id"] != self.session_id
            or report["handoff"]["consumer"]["native_session_id"] != self.native_session_id
            or (report["result"] is not None) != accepted
            or stage not in ("prepared", "launching")
        ):
            raise RuntimeCustodyError("Original acquisition launch changed or already accepted")
        if self._session is not None:
            if (
                self.frontend.manager.get_session(self.session_id) is not self._session
                or self._session.target is not self._target
                or self._target.to_json() != self._target_json
                or self._session.status != SessionStatus.STARTING
                or self._session.acp_session_id != self.native_session_id
                or self._session._handoff_in_progress
                or self._session.client is not None and self._session.client is not self._client
            ):
                raise RuntimeCustodyError("Original acquisition session or target changed")
            require_local(self._target)

    def bind(self, manager: SessionManager, session: Session, target: SpawnTarget) -> None:
        self.require_current()
        require_local(target)
        if (
            manager is not self.frontend.manager or manager.get_session(self.session_id) is not session
            or session.session_id != self.session_id or session.target is not target
            or target.worktree_id != self.request.worktree_id or not target.explicit_cwd
            or session.acp_session_id != self.native_session_id or self._session is not None
            or target.native_resume_id not in (None, self.native_session_id)
        ):
            raise RuntimeCustodyError("Acquisition launch requires its original keyed local session")
        self._session, self._target, self._target_json = session, target, target.to_json()
        self.require_current()

    def require_no_host(self) -> None:
        index = self.frontend.manager._host_index
        if index is None:
            raise RuntimeCustodyError("Acquisition resume has no strict host inventory")
        inventory = index.locked_inventory()
        if not inventory.storage_complete or any(
            row.session_id == self.session_id for row in inventory.records
        ):
            raise RuntimeCustodyError("Acquisition resume cannot replace unknown or retained host custody")

    def begin_host(self) -> None:
        """Runs in the launch worker, immediately before its actual Popen."""
        self.require_current()
        if self._session is None:
            raise RuntimeCustodyError("Acquisition launch has no bound local session")
        if self.native_session_id is not None:
            self.require_no_host()
        journal = self.frontend.runtime.store.journal
        if not journal.begin_launch(
            self.request, self.frontend.installation, self.admission,
            self.handoff_id, now=time.time(),
        ):
            raise RuntimeCustodyError("Original acquisition launch remains held; do not respawn")
        self.require_current()

    def child_context(self, child_argv: list[str], cwd: str | None) -> str:
        from .acquisition_host_admission import context

        self.require_current()
        if self._target is None or cwd != self._target.cwd:
            raise RuntimeCustodyError("Acquisition child workspace changed")
        return context(self, child_argv, cwd)

    def attach_transport(self, client: AcpClient, host: Any) -> None:
        from .acp_client import AcpClient

        self.require_current()
        if (
            type(client) is not AcpClient or self._client is not None or host.boundary != "local"
            or host.proc is None or host.proc.pid != host.host_pid
            or host.proc.poll() is not None
            or client._connection is None or not client._host_mode or not client.is_running
            or client._host_child_pid != host.child_pid or client.acp_session_id is not None
        ):
            raise RuntimeCustodyError("Acquisition launch requires its actual local host transport")
        self._host_birth = process_start_time(host.host_pid)
        self._child_birth = process_start_time(host.child_pid)
        if not self._host_birth or not self._child_birth:
            raise RuntimeCustodyError("Acquisition host or child birth is unavailable")
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
            raise RuntimeCustodyError("Original acquisition native transport changed")

    def require_configuration(self, client: AcpClient) -> None:
        self.require_current(accepted=True)
        self._require_transport(client)
        report = self.frontend.runtime.store.journal.read_launch(self.request, self.handoff_id)
        if client.acp_session_id != report["result"]["native_session_id"]:
            raise RuntimeCustodyError("Original acquisition native configuration identity changed")

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
                raise RuntimeCustodyError("Original acquisition native effect changed or remains held")
            report = self.frontend.runtime.store.journal.read_launch(self.request, self.handoff_id)
            if report["started_at"] is None:
                raise RuntimeCustodyError("Acquisition host never reached final admission")
            self._native_started = True
            result = await method(**kwargs)
            native = self.native_session_id if mode == "load" else result.session_id
            self.frontend.runtime.store.journal.report_launch(
                self.request, self.handoff_id, native_session_id=native, now=time.time(),
            )
            return result

        return invoke


def session_association(manager: SessionManager, session: Session) -> str | None:
    """Read the original session association without enrolling or repairing it."""
    rows = manager.db.execute_read(
        "SELECT runtime_custody_json FROM sessions WHERE id=?", (session.session_id,),
    )
    return rows[0]["runtime_custody_json"] if rows else None


def require_unassociated_session(
    manager: SessionManager, session: Session, *, operation: str,
) -> None:
    if session_association(manager, session) is not None:
        raise RuntimeCustodyError(f"Admitted acquisition {operation} requires its actual original owner")


def require_resume_owner(
    manager: SessionManager, session: Session, owner: AcquisitionLaunchOwner | None,
    *, drain: bool, allow_recreate: bool,
) -> None:
    association = session_association(manager, session)
    if owner is None:
        if association is not None:
            raise RuntimeCustodyError("Admitted acquisition resume requires its actual original owner")
        return
    owner.require_current()
    require_local(session.target)
    if (
        manager is not owner.frontend.manager or session.session_id != owner.session_id
        or not owner.native_session_id or session.acp_session_id != owner.native_session_id
        or association != owner.admission.document() or session.client is not None
        or session._native_turn_transport is not None
        or session.status != SessionStatus.STOPPED or drain or allow_recreate
    ):
        raise RuntimeCustodyError("Acquisition resume requires a stopped admitted local owner without replay")
    owner.require_no_host()


def require_start_owner(
    manager: SessionManager, target: SpawnTarget, session_id: str | None,
    owner: AcquisitionLaunchOwner | None,
) -> None:
    if owner is None:
        return
    owner.require_current()
    require_local(target)
    if (
        manager is not owner.frontend.manager or owner.native_session_id is not None
        or owner.session_id != session_id or not target.explicit_cwd
    ):
        raise RuntimeCustodyError("Acquisition creation requires its keyed exact local owner")


def original_admission(
    runtime: AcquisitionRuntime, request: AcquisitionRequest, owner: AcquisitionFrontend | None,
) -> RuntimeAdmission | None:
    retained = runtime.store.journal.original_admission(request)
    if owner is None:
        if retained is not None:
            raise RuntimeCustodyError("Pending acquisition requires its actual original frontend")
        return None
    if type(owner) is not AcquisitionFrontend or owner.runtime is not runtime or request.mode != "acp":
        raise RuntimeCustodyError("Acquisition custody requires its explicit internal ACP frontend")
    owner.require_current()
    if retained is not None and retained != owner.admission:
        raise RuntimeCustodyError("Acquisition cannot replace its original frontend admission")
    return owner.admission
