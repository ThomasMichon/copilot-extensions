"""Conversation operations over ReplayStore's existing complete-envelope custody."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from .attention import Cursor, PRKey
from .attention_acquisition import AttentionInputs, observe_attention, unavailable_attention
from .attention_state import AttentionState
from .conversation_journal import ConversationJournal, Observation, PROTOCOL
from .watch_replay_store import ReplayStore, ReplayStoreInvalid
from .watch_replay import ObservationTarget


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
            if document["version"] in (3, 4, 5, 6):
                if document["capacities"]["conversation_bytes"] != conversation_bytes:
                    raise ValueError("conversation capacity differs from adopted layout")
                return {"status": "already_adopted", "version": document["version"]}
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
        versions: tuple[int, ...] = (3, 4, 5, 6),
    ) -> dict:
        def execute(document: dict) -> dict:
            if versions == (3,) and "terminal_retention" in document:
                raise ReplayStoreInvalid("terminal retention serving contract unavailable")
            if document["version"] not in versions:
                if document["version"] in (4, 5, 6):
                    raise ReplayStoreInvalid("conversation serving contract unavailable for adopted owner layout")
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
            versions=(3,),
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
        def observe(document: dict) -> dict:
            if "terminal_retention" in document:
                raise ReplayStoreInvalid("terminal ingestion requires workspace evidence and delivery target")
            if document["version"] not in (3, 4, 5, 6):
                raise ReplayStoreInvalid("conversation journal requires explicit owner adoption")
            journal = ConversationJournal(document["conversations"])
            result = journal.observe(observation, expected=expected, reopen=reopen)
            document["conversations"] = journal.snapshot()
            return result

        return self.owner._review_transaction(observe, expires_at=expires_at, checkpoint=checkpoint)

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

    def observe_workspace(
        self,
        generation: dict,
        *,
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        attention: AttentionInputs | None = None,
        comment_mappings: bool = False,
        terminal_target: ObservationTarget | None = None,
    ) -> dict:
        """Project trusted acquisition under the same journal transaction, not HTTP ingest."""
        from .workspace_conversation import conversation_key, observation_interval, project_generation

        if type(comment_mappings) is not bool:
            raise ValueError("explicit comment mapping selection required")

        def observe(document: dict) -> dict:
            if document["version"] not in (3, 4, 5, 6):
                raise ReplayStoreInvalid("conversation journal requires explicit owner adoption")
            if attention is not None and document["version"] not in (4, 5, 6):
                raise ReplayStoreInvalid("attention requires explicit owner adoption")
            if comment_mappings and document.get("review_comment_mapping_version") != 1:
                raise ReplayStoreInvalid("comment mapping requires explicit storage adoption")
            journal = ConversationJournal(document["conversations"])
            key = conversation_key(generation["scope"], self.owner.namespace)
            record = journal._find(key)
            previous = Observation.restore(record["current"]) if record is not None else None
            value = project_generation(generation, self.owner.namespace, previous)
            attention_state = None
            interval = None
            if attention is not None:
                attention_state = AttentionState(document["attention"])
                interval = observation_interval(generation["sections"])
                if interval is None:
                    return {"status": "unavailable", "reason": "observation_interval_missing"}
                scoped_key = asdict(key)
                watermark = max(
                    (row["facts"]["observed_at"] for row in attention_state.records
                     if row["facts"]["key"] == scoped_key),
                    default=0,
                )
                watermark = max(watermark, previous.observed_to if previous is not None else 0)
                current = value.document() if value is not None else None
                if current is not None and value is not None and value.terminal:
                    current["sections"] = {}
                same = current is not None and record is not None and current == record["current"]
                if interval[0] < watermark and not (same and interval[1] >= watermark):
                    return {
                        "status": "stale_observation",
                        "cursor": {"epoch": record["epoch"], "sequence": record["sequence"]} if record else None,
                    }
            if value is None or previous is not None and previous.terminal and not value.complete:
                result = {
                    "status": "unavailable",
                    "reason": "candidate_state_or_interval_missing" if value is None
                    else "terminal_requires_complete_observation",
                }
                if attention is not None and attention_state is not None and interval is not None:
                    result["attention"] = unavailable_attention(
                        attention_state, key, generation, attention, interval[1],
                    )
                    document["attention"] = attention_state.snapshot()
                return result
            expected = Cursor(record["epoch"], record["sequence"]) if record else None
            result = journal.observe(
                value, expected=expected,
                reopen=bool(previous and previous.terminal and value.complete and value.state == "open"),
            )
            if attention is not None and result["status"] in ("observed", "unchanged"):
                state = AttentionState(document["attention"])
                result["attention"] = observe_attention(
                    state, value, Cursor(**result["cursor"]), generation, attention,
                )
                document["attention"] = state.snapshot()
            document["conversations"] = journal.snapshot()
            if "terminal_retention" in document and result["status"] in ("observed", "unchanged"):
                from .terminal_retention import observe_live, observe_terminal
                if value.terminal:
                    result["retirement"] = observe_terminal(
                        document, generation["scope"], journal._find(key), terminal_target,
                        clock=self.owner.clock,
                    )
                elif value.complete and value.state == "open":
                    observe_live(document, generation["scope"], journal._find(key), terminal_target)
            if comment_mappings and result["status"] in ("observed", "unchanged"):
                from .review_comment_mapping import observe
                observe(document, generation)
            return result

        return self.owner._review_transaction(
            observe, expires_at=expires_at, checkpoint=checkpoint, persist=True,
        )
