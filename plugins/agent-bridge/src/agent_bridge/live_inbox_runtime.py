"""Compatible retained-inbox adapter over the existing queue and handoff ledger."""

from __future__ import annotations

from dataclasses import dataclass
from .db import Database
from .inbox_custody import InboxConsumer
from .loop_governance import LoopGovernance
from .runtime_custody import nonblank, positive
from . import compatible_inbox


@dataclass(frozen=True)
class InboxBinding:
    """Legacy v1 wire observations, not installation or lifecycle authority."""

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
    """Keep the v1 wire shape while treating provenance fields as observations."""

    def negotiate(
        self, db: Database, governance: LoopGovernance | None, consumer: InboxConsumer, *, now: float,
    ) -> InboxNegotiation:
        predecessor = compatible_inbox.negotiate(db, consumer, now=now)
        return InboxNegotiation(
            InboxBinding(compatible_inbox.BINDING, 1), predecessor,
        )

    def enroll(
        self, db: Database, governance: LoopGovernance | None, consumer: InboxConsumer,
        binding: InboxBinding, *, expected_incarnation: str | None, now: float,
    ) -> None:
        compatible_inbox.enroll(db, consumer, now=now)

    def poll(
        self, db: Database, governance: LoopGovernance | None, consumer: InboxConsumer,
        binding: InboxBinding, *, now: float, limit: int | None = None,
    ) -> list[dict[str, object]]:
        return compatible_inbox.poll(db, consumer, now=now, limit=limit)

    def begin(
        self, db: Database, governance: LoopGovernance | None, consumer: InboxConsumer,
        binding: InboxBinding, message_id: int, handoff_id: str, *, now: float,
    ) -> bool:
        return compatible_inbox.begin(db, consumer, message_id, handoff_id, now=now)
