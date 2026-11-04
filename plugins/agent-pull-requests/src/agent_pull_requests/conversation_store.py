"""Conversation operations over ReplayStore's existing complete-envelope custody."""

from __future__ import annotations

from collections.abc import Callable

from .attention import Cursor, PRKey
from .conversation_journal import ConversationJournal, Observation, PROTOCOL
from .watch_replay_store import ReplayStore, ReplayStoreInvalid


class ConversationStore:
    """No separate file, lock, provisioner, service, provider or admission authority."""

    def __init__(self, owner: ReplayStore) -> None:
        if not isinstance(owner, ReplayStore):
            raise ValueError("existing replay owner required")
        self.owner = owner

    def adopt(
        self,
        *,
        lifecycle_checkpoint: Callable[[], None],
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        conversation_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        """Explicit v2-to-v3 layout adoption; lifecycle custody excludes old file readers."""
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document["version"] == 3:
                if document["capacities"]["conversation_bytes"] != conversation_bytes:
                    raise ValueError("conversation capacity differs from adopted layout")
                return {"status": "already_adopted", "version": 3}
            document["version"] = 3
            document["conversations"] = ConversationJournal().snapshot()
            document["capacities"]["conversation_bytes"] = conversation_bytes
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": "adopted", "version": 3}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=checkpoint)

    def _transaction(
        self,
        operation: Callable[[ConversationJournal], dict],
        *,
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        persist: bool,
    ) -> dict:
        def execute(document: dict) -> dict:
            if document["version"] != 3:
                raise ReplayStoreInvalid("conversation journal requires explicit owner adoption")
            journal = ConversationJournal(document["conversations"])
            result = operation(journal)
            document["conversations"] = journal.snapshot()
            return result

        return self.owner._review_transaction(
            execute,
            expires_at=expires_at,
            checkpoint=checkpoint,
            persist=persist,
        )

    def capabilities(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None
    ) -> dict:
        """Read support from the executing owner and adopted layout, not package metadata."""
        return self._transaction(
            lambda _journal: {
                "protocol": PROTOCOL,
                "owner_store_versions": [3],
                "ingest": "trusted_owner_only",
                "bodies": False,
                "acknowledges": False,
            },
            expires_at=expires_at,
            checkpoint=checkpoint,
            persist=False,
        )

    def observe(
        self,
        observation: Observation,
        *,
        expected: Cursor | None,
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        reopen: bool = False,
    ) -> dict:
        """Trusted owner-only evidence; never exposed as a caller-supplied HTTP ingest."""
        return self._transaction(
            lambda journal: journal.observe(observation, expected=expected, reopen=reopen),
            expires_at=expires_at,
            checkpoint=checkpoint,
            persist=True,
        )

    def events(
        self,
        key: PRKey,
        *,
        after: Cursor | None,
        limit: int,
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        if key.namespace != self.owner.namespace:
            raise ValueError("conversation namespace differs from owner")
        return self._transaction(
            lambda journal: journal.events(
                key, after=after, limit=limit, now=int(self.owner.clock())
            ),
            expires_at=expires_at,
            checkpoint=checkpoint,
            persist=False,
        )
