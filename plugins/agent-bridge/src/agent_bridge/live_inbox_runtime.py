"""Local retained-inbox adapter over the installation and original DB owners."""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import uuid

from zdd.diagnostics import process_start_time

from .db import Database
from .inbox_custody import InboxConsumer
from .loop_governance import LoopGovernance
from .runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
    nonblank, positive,
)


@dataclass(frozen=True)
class InboxBinding:
    """An echoed wrong-target/replay fence, never a client-selected owner."""

    installation: str
    epoch: int

    def __post_init__(self) -> None:
        nonblank(self.installation)
        positive(self.epoch)


@dataclass(frozen=True)
class InboxNegotiation:
    binding: InboxBinding
    expected_incarnation: str | None


class LiveInboxRuntime:
    """One serving frontend, initialized only by explicit consumer negotiation.

    Construction and ordinary registry heartbeats do not initialize an epoch.
    This enrolls only this frontend, not all background/native producers.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._database: Database | None = None
        self._producer: RuntimeProducer | None = None
        self._admission: RuntimeAdmission | None = None

    def _context(
        self, db: Database, governance: LoopGovernance, *,
        initialize: bool = False, binding: InboxBinding | None = None,
    ) -> tuple[RuntimeInstallation, RuntimeAdmission]:
        with self._lock:
            installation = governance.runtime_installation(db)
            if self._database is not None and self._database is not db:
                raise RuntimeCustodyError("Inbox frontend database owner changed")
            pid = os.getpid()
            birth = process_start_time(pid)
            if not birth:
                raise RuntimeCustodyError("Inbox frontend process birth is unavailable")
            if self._producer is not None and (
                self._producer.pid != pid or self._producer.process_start != birth
            ):
                raise RuntimeCustodyError("Inbox frontend process incarnation changed")
            if initialize:
                epoch = db.initialize_runtime_epoch(installation)
                producer = self._producer or RuntimeProducer(uuid.uuid4().hex, pid, birth)
                admission = db.enroll_runtime_producer(installation, epoch.epoch, producer)
                self._database, self._producer, self._admission = db, producer, admission
            else:
                admission = self._admission
                if admission is None:
                    raise RuntimeCustodyError("Inbox frontend requires explicit negotiation")
                epoch = db.runtime_epoch(installation)
                if epoch.state != "open" or epoch.epoch != admission.epoch:
                    raise RuntimeCustodyError("Inbox frontend admission is no longer current")
            if admission.installation != installation.document():
                raise RuntimeCustodyError("Inbox frontend installation changed")
            if binding is not None and binding != InboxBinding(
                installation.document(), admission.epoch,
            ):
                raise RuntimeCustodyError("Inbox consumer installation or epoch changed")
            return installation, admission

    def negotiate(
        self, db: Database, governance: LoopGovernance, consumer: InboxConsumer, *, now: float,
    ) -> InboxNegotiation:
        installation, admission = self._context(db, governance, initialize=True)
        predecessor = db.observe_inbox_consumer(installation, admission, consumer, now=now)
        return InboxNegotiation(
            InboxBinding(installation.document(), admission.epoch), predecessor,
        )

    def enroll(
        self, db: Database, governance: LoopGovernance, consumer: InboxConsumer,
        binding: InboxBinding, *, expected_incarnation: str | None, now: float,
    ) -> None:
        installation, admission = self._context(db, governance, binding=binding)
        db.enroll_inbox_consumer(
            installation, admission, consumer, expected_incarnation=expected_incarnation, now=now,
        )

    def poll(
        self, db: Database, governance: LoopGovernance, consumer: InboxConsumer,
        binding: InboxBinding, *, now: float,
    ) -> list[dict[str, object]]:
        installation, admission = self._context(db, governance, binding=binding)
        return db.poll_live_message_handoffs(installation, admission, consumer, now=now)

    def begin(
        self, db: Database, governance: LoopGovernance, consumer: InboxConsumer,
        binding: InboxBinding, message_id: int, handoff_id: str, *, now: float,
    ) -> bool:
        installation, admission = self._context(db, governance, binding=binding)
        return db.begin_live_message_handoff(
            installation, admission, consumer, message_id, handoff_id, now=now,
        )
