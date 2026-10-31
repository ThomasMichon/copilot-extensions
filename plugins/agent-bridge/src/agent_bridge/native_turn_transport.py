"""Explicit local ACP custody on the existing original-turn journal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import time
from typing import TYPE_CHECKING, Any
import uuid

from acp import text_block
from zdd import diagnostics

from .models import SessionStatus
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer, producer_id,
)
from .turn_custody import NativeTurnConsumer

if TYPE_CHECKING:
    from acp.client.connection import ClientSideConnection

    from .acp_client import AcpClient
    from .acquisition_host_transport import AcquisitionHostTransport
    from .db import Database
    from .session_manager import Session, SessionManager
    from .transport import SpawnTarget


def _local_target(target: SpawnTarget) -> bool:
    return target.type == "local" and not any((
        target.spawn_command, target.codespace, target.container, target.venue, target.host,
    ))


@dataclass(frozen=True)
class NativeTurnTransport:
    """Live owner references, never reconstructed from durable observations."""

    manager: SessionManager
    session: Session
    db: Database
    client: AcpClient
    process: asyncio.subprocess.Process
    connection: ClientSideConnection
    target: SpawnTarget
    cwd: str
    installation: RuntimeInstallation
    admission: RuntimeAdmission
    producer: RuntimeProducer
    consumer: NativeTurnConsumer

    @classmethod
    def capture(
        cls, manager: SessionManager, session: Session,
        installation: RuntimeInstallation, admission: RuntimeAdmission,
    ) -> NativeTurnTransport:
        from .acp_client import AcpClient

        if type(installation) is not RuntimeInstallation or type(admission) is not RuntimeAdmission:
            raise RuntimeCustodyError("Native custody requires explicit installation and frontend admission")
        manager.db._runtime_installation(installation)
        client = session.client
        if (
            type(client) is not AcpClient or not _local_target(session.target)
            or client._host_mode or client._remote_transport is not None
            or client._idle_transport is not None or not client.is_running
            or client._process is None or client._connection is None
            or not client.acp_session_id or client.acp_session_id != session.acp_session_id
            or client._native_turn_transport is not None
            or session.status != SessionStatus.IDLE
            or session._handoff_in_progress
            or client._prompt_lock.locked() or client._prompt_in_flight
            or client._idle_operations
            or session._prompt_task is not None and not session._prompt_task.done()
        ):
            raise RuntimeCustodyError("Native custody requires an idle local process-owned ACP transport")
        child_birth = diagnostics.process_start_time(client._process.pid)
        frontend_birth = diagnostics.process_start_time(os.getpid())
        if not child_birth or not frontend_birth:
            raise RuntimeCustodyError("Native custody process birth is unavailable")
        rows = manager.db.execute_read(
            "SELECT producer_json FROM bridge_runtime_producers WHERE id=?", (admission.producer,),
        )
        if len(rows) != 1:
            raise RuntimeCustodyError("Native custody requires its enrolled frontend")
        try:
            producer = RuntimeProducer(**json.loads(rows[0]["producer_json"]))
        except (TypeError, ValueError) as exc:
            raise RuntimeCustodyError("Invalid native frontend producer history") from exc
        if (
            producer.pid != os.getpid() or producer.process_start != frontend_birth
            or producer.document() != rows[0]["producer_json"]
            or producer_id(installation.document(), admission.epoch, producer) != admission.producer
        ):
            raise RuntimeCustodyError("Native custody frontend process changed")
        owner = cls(
            manager, session, manager.db, client, client._process, client._connection,
            session.target, session.target.cwd,
            installation, admission, producer,
            NativeTurnConsumer(
                session.session_id, client.acp_session_id, uuid.uuid4().hex,
                client._process.pid, child_birth,
            ),
        )
        owner.require_current(enrolled=False)
        return owner

    def require_current(self, *, enrolled: bool = True) -> None:
        session, client = self.session, self.client
        if (
            self.manager.db is not self.db
            or self.manager.get_session(session.session_id) is not session
            or session.client is not client
            or session.target is not self.target or session.target.cwd != self.cwd
            or session.acp_session_id != self.consumer.native_session_id
            or client.acp_session_id != self.consumer.native_session_id
            or client._process is not self.process or client._connection is not self.connection
            or self.process.pid != self.consumer.pid or not client.is_running
            or not _local_target(session.target) or client._host_mode
            or client._remote_transport is not None or client._idle_transport is not None
            or client._loading_session
            or session.status not in (SessionStatus.IDLE, SessionStatus.RUNNING)
            or diagnostics.process_start_time(self.process.pid) != self.consumer.process_start
            or os.getpid() != self.producer.pid
            or diagnostics.process_start_time(self.producer.pid) != self.producer.process_start
            or enrolled and (
                session._native_turn_transport is not self or client._native_turn_transport is not self
            )
        ):
            raise RuntimeCustodyError("Original native transport owner changed or is unavailable")
        identity = self.db._runtime_installation(self.installation)
        with self.db._runtime_transaction(write=False) as conn:
            self.db._runtime_admission(conn, identity, self.admission, cohort="Native transport")
            self.db._native_context(conn, self.consumer)
            row = conn.execute(
                "SELECT producer_json FROM bridge_runtime_producers WHERE id=?",
                (self.admission.producer,),
            ).fetchone()
            if row is None or row["producer_json"] != self.producer.document():
                raise RuntimeCustodyError("Original native frontend producer changed")

    def attempt(self, index: int, prompt: str) -> NativeTurnAttempt:
        self.require_current()
        return NativeTurnAttempt(self, index, prompt)


@dataclass(frozen=True)
class NativeTurnAttempt:
    owner: NativeTurnTransport | AcquisitionHostTransport
    index: int
    prompt: str

    async def send(self, client: AcpClient, prompt: str) -> Any:
        """Final local call, after the client's lock and task scheduling boundary."""
        owner = self.owner
        if client is not owner.client or prompt != self.prompt:
            raise RuntimeCustodyError("Original native turn input or client changed")
        owner.require_current()
        handoff = owner.db.prepare_native_turn_handoff(
            owner.installation, owner.admission, owner.consumer, self.index,
            expected_prompt=prompt,
        )
        handoff_id = handoff["id"]
        if not isinstance(handoff_id, str):
            raise RuntimeCustodyError("Original native turn handoff identity is unavailable")
        if not owner.db.begin_native_turn_handoff(
            owner.installation, owner.admission, owner.consumer, self.index, handoff_id,
            expected_prompt=prompt, now=time.time(),
        ):
            raise RuntimeCustodyError("Original native turn attempt remains held; do not resend")
        try:
            owner.require_current()
            result = await owner.connection.prompt(
                session_id=owner.consumer.native_session_id, prompt=[text_block(prompt)],
            )
        except BaseException:
            owner.db.record_native_turn_handoff(
                owner.consumer, self.index, handoff_id, outcome="unknown", now=time.time(),
            )
            raise
        owner.db.record_native_turn_handoff(
            owner.consumer, self.index, handoff_id, outcome="native_accepted", now=time.time(),
        )
        return result
