"""Original acquisition's local Host transport on the existing turn protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from zdd.diagnostics import process_start_time

from .models import SessionStatus
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation
from .turn_custody import NativeTurnConsumer

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .acquisition_contract import AcquisitionRequest
    from .acquisition_launch_owner import AcquisitionFrontend, AcquisitionLaunchOwner
    from .db import Database
    from .native_turn_transport import NativeTurnAttempt
    from .session_manager import Session
    from .transport import SpawnTarget


@dataclass(frozen=True)
class AcquisitionHostTransport:
    frontend: AcquisitionFrontend
    session: Session
    client: AcpClient
    connection: Any
    host: Any
    process: Any
    host_pid: int
    host_birth: str
    consumer: NativeTurnConsumer
    target: SpawnTarget
    target_json: str
    socket: Any
    request: AcquisitionRequest
    handoff_id: str

    @property
    def db(self) -> Database:
        return self.frontend.db

    @property
    def installation(self) -> RuntimeInstallation:
        return self.frontend.installation

    @property
    def admission(self) -> RuntimeAdmission:
        return self.frontend.admission

    @classmethod
    def capture(cls, launch: AcquisitionLaunchOwner, session: Session) -> AcquisitionHostTransport:
        from .acp_client import AcpClient

        launch.frontend.require_current()
        report = launch.frontend.runtime.store.journal.read_launch(
            launch.request, launch.handoff_id,
        )
        if (
            launch._session is not session or not launch._native_started
            or type(session.client) is not AcpClient or launch._client is not session.client
            or launch._host is None or launch._host_birth is None or launch._child_birth is None
            or report["result"] is None or report["result"]["native_session_id"] != session.acp_session_id
            or session._native_turn_transport is not None or session.client._native_turn_transport is not None
        ):
            raise RuntimeCustodyError("Host turn custody requires the actual accepted acquisition owner")
        owner = cls(
            launch.frontend, session, session.client, launch._connection, launch._host,
            launch._host_process, launch._host_pid,
            launch._host_birth,
            NativeTurnConsumer(
                session.session_id, session.acp_session_id,
                report["handoff"]["consumer"]["consumer_incarnation"],
                launch._host.child_pid, launch._child_birth,
            ),
            session.target, session.target.to_json(), session.client.session_host_client,
            launch.request, launch.handoff_id,
        )
        owner.require_current(enrolled=False)
        session._native_turn_transport = owner
        session.client._native_turn_transport = owner
        return owner

    def require_current(self, *, enrolled: bool = True) -> None:
        self.frontend.require_current()
        session, client = self.session, self.client
        if (
            self.frontend.manager.get_session(self.consumer.session_id) is not session
            or session.client is not client or session.target is not self.target
            or session.target.to_json() != self.target_json
            or session.acp_session_id != self.consumer.native_session_id
            or client.acp_session_id != self.consumer.native_session_id
            or client._connection is not self.connection or client.session_host_client is not self.socket
            or not client._host_mode or not client.is_running
            or client._host_child_pid != self.consumer.pid
            or client._remote_transport is not None or client._idle_transport is not None
            or client._loading_session or session._handoff_in_progress
            or session.status not in (SessionStatus.IDLE, SessionStatus.RUNNING)
            or self.host.proc is not self.process or self.host.host_pid != self.host_pid
            or self.host.child_pid != self.consumer.pid or self.process.poll() is not None
            or process_start_time(self.host_pid) != self.host_birth
            or process_start_time(self.consumer.pid) != self.consumer.process_start
            or enrolled and (
                session._native_turn_transport is not self or client._native_turn_transport is not self
            )
        ):
            raise RuntimeCustodyError("Original acquisition Host transport changed or is unavailable")
        with self.db._runtime_transaction(write=False) as conn:
            self.db._native_context(conn, self.consumer)
        report = self.frontend.runtime.store.journal.read_launch(self.request, self.handoff_id)
        if (
            report["handoff"]["admission"] != self.admission.document()
            or report["result"] is None
            or report["result"]["native_session_id"] != self.consumer.native_session_id
        ):
            raise RuntimeCustodyError("Original acquisition Host acceptance changed")

    def attempt(self, index: int, prompt: str) -> NativeTurnAttempt:
        from .native_turn_transport import NativeTurnAttempt

        self.require_current()
        return NativeTurnAttempt(self, index, prompt)
