"""Explicit original local acquisition owners, not automatic runtime enrollment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import TYPE_CHECKING
import uuid

from .acquisition_contract import AcquisitionRequest
from .frontend_process import frontend_producer, require_frontend_process
from .local_launch_owner import LocalLaunchOwner, require_local
from .models import SessionStatus
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
)

if TYPE_CHECKING:
    from .acquisition_runtime import AcquisitionRuntime
    from .db import Database
    from .session_manager import Session, SessionManager
    from .transport import SpawnTarget


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
class AcquisitionLaunchOwner(LocalLaunchOwner):
    frontend: AcquisitionFrontend
    request: AcquisitionRequest
    session_id: str
    native_session_id: str | None
    handoff_id: str

    @property
    def worktree_id(self) -> str:
        return self.request.worktree_id

    def read_launch(self) -> dict:
        return self.frontend.runtime.store.journal.read_launch(self.request, self.handoff_id)

    def require_custody(self, *, accepted: bool) -> None:
        report = self.read_launch()
        _token, stage, _data = self.frontend.runtime.store.recovery(self.request)
        if (
            report["handoff"]["admission"] != self.admission.document()
            or report["handoff"]["consumer"]["session_id"] != self.session_id
            or report["handoff"]["consumer"]["native_session_id"] != self.native_session_id
            or (report["result"] is not None) != accepted
            or stage not in ("prepared", "launching")
        ):
            raise RuntimeCustodyError("Original acquisition launch changed or already accepted")
    def begin_launch(self) -> bool:
        journal = self.frontend.runtime.store.journal
        return journal.begin_launch(
            self.request, self.frontend.installation, self.admission,
            self.handoff_id, now=time.time(),
        )

    def report_launch(self, native_session_id: str) -> None:
        self.frontend.runtime.store.journal.report_launch(
            self.request, self.handoff_id, native_session_id=native_session_id, now=time.time(),
        )

    def child_context(self, child_argv: list[str], cwd: str | None) -> str:
        from .acquisition_host_admission import context

        self.require_current()
        if self._target is None or cwd != self._target.cwd:
            raise RuntimeCustodyError("Acquisition child workspace changed")
        return context(self, child_argv, cwd)


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
    owner: LocalLaunchOwner | None,
) -> None:
    if owner is None:
        return
    from .creation_launch_owner import CreationLaunchOwner

    if type(owner) not in (AcquisitionLaunchOwner, CreationLaunchOwner):
        raise RuntimeCustodyError("Local creation requires its concrete original launch owner")
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
