"""Terminal fencing and drain of the existing workspace's admitted operations."""

from __future__ import annotations

import math
import copy
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from .attention import Cursor
from .conversation_journal import ConversationJournal
from .conversation_store import ConversationStore
from .review_workspace import _digest, _scope
from .terminal_retention import (
    TerminalAdmissionClosed, TerminalRetentionStore, bind_generation, require_live,
)
from .workspace_conversation import conversation_key
from .watch_replay import ObservationTarget

if TYPE_CHECKING:
    from .attention_acquisition import AttentionInputs
    from .generation_cache import GenerationCache
    from .workspace_operations import WorkspaceOperations

_T = TypeVar("_T")


class WorkspaceRetention:
    """Uses the embedding's single workspace lifecycle, never a process/lease owner.

    Lock order: short lifecycle condition, then replay transaction, then
    materialization lock, then cache transaction. Request accounting releases
    the condition before IO. Drain waits without any disk or cache lock.
    """

    def __init__(self, workspace: WorkspaceOperations) -> None:
        self.workspace = workspace
        self._condition = threading.Condition()
        self._active: dict[str, int] = {}

    @contextmanager
    def request(
        self, operation: str, payload: dict, *, expires_at: int, checkpoint: Callable[[], None],
    ) -> Iterator[int | None]:
        owner = self.workspace.store
        if not owner.terminal_retention or operation in (
            "capabilities", "events", "receipt", "retained-comment",
        ):
            yield None
            return
        scope = _scope({**self.workspace.scope, "number": payload.get("number")})
        identity = _digest(scope)
        with self._condition:
            self._active[identity] = self._active.get(identity, 0) + 1
        try:
            def admit(document: dict) -> int | None:
                if "terminal_retention" not in document:
                    return None
                if operation not in ("load", "sync"):
                    return require_live(document, scope)
                record = ConversationJournal(document["conversations"])._find(
                    conversation_key(scope, owner.namespace),
                )
                return record["epoch"] if record is not None else 0

            epoch = owner._review_transaction(
                admit, expires_at=expires_at, checkpoint=checkpoint, persist=False,
            )
            yield epoch
        finally:
            with self._condition:
                self._active[identity] -= 1
                if not self._active[identity]:
                    del self._active[identity]
                self._condition.notify_all()

    def guarded(
        self, scope: dict, operation: Callable[[], _T], *, expires_at: int,
        checkpoint: Callable[[], None], epoch: int | None, generation_id: str | None = None,
    ) -> _T:
        owner = self.workspace.store
        if not owner.terminal_retention:
            return operation()

        def guarded(document: dict) -> _T:
            require_live(document, scope, epoch=epoch, generation_id=generation_id)
            return operation()

        return owner._review_transaction(
            guarded, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def publish(
        self, generation: dict, cache: GenerationCache, *, expected_current: str | None, expires_at: int,
        checkpoint: Callable[[], None], attention: AttentionInputs | None,
        comment_mappings: bool, target: ObservationTarget,
    ) -> tuple[dict, dict]:
        """Terminal evidence is never published as another body-bearing generation."""
        workspace, owner = self.workspace, self.workspace.store
        if not workspace.conversations or workspace.retention_target is None:
            raise TerminalAdmissionClosed("terminal workspace embedding is not qualified")
        observation = ConversationStore(owner).observe_workspace(
            generation, expires_at=expires_at, checkpoint=checkpoint,
            attention=attention, comment_mappings=comment_mappings, terminal_target=target,
        )
        if "retirement" in observation:
            return {"status": "retired", "effects": "none"}, observation
        if observation["status"] not in ("observed", "unchanged"):
            return {"status": "not_published", "effects": "none"}, observation
        if not generation["complete"]:
            return {"status": "not_published", "effects": "none"}, observation

        def publish(document: dict, commit: Callable[[], None]) -> dict:
            previous = copy.deepcopy(document["terminal_retention"]["generations"])
            bind_generation(document, generation, observation["cursor"])
            owner._validate_owner(document)  # Reserve metadata before cache IO.
            if document["terminal_retention"]["generations"] != previous:
                commit()
            publication = cache.publish(generation, expected_current=expected_current)
            if publication["status"] not in ("published", "unconfirmed"):
                document["terminal_retention"]["generations"] = previous
            return publication

        publication = owner._review_transaction(
            publish, expires_at=expires_at, checkpoint=checkpoint, staged=True,
        )
        return publication, observation

    def materialize(
        self, cache: GenerationCache, scope: dict, generation_id: str | None, *, expires_at: int,
        checkpoint: Callable[[], None], epoch: int | None,
    ) -> tuple[dict, bytes]:
        owner = self.workspace.store
        if not owner.terminal_retention:
            return self.workspace._materialized(cache, scope, generation_id)

        def read(document: dict) -> tuple[dict, bytes]:
            require_live(document, scope, epoch=epoch, generation_id=generation_id)
            descriptor, data = self.workspace._materialized(cache, scope, generation_id)
            require_live(document, scope, epoch=epoch, generation_id=descriptor["id"])
            return descriptor, data

        return owner._review_transaction(
            read, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def drain(
        self, scope: dict, cursor: Cursor, *, expires_at: int,
        checkpoint: Callable[[], None], deadline: float, lifecycle_checkpoint: Callable[[], None],
    ) -> dict:
        """Internal owner call; joining this object does not qualify foreign readers."""
        scope = _scope(scope)
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError("finite existing lifecycle deadline required")
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual exclusive workspace lifecycle checkpoint required")
        lifecycle_checkpoint()
        owner = self.workspace.store
        selected = TerminalRetentionStore(owner)
        selected.inspect(scope, cursor, expires_at=expires_at, checkpoint=checkpoint)
        identity = _digest(scope)
        with self._condition:
            while self._active.get(identity, 0):
                owner.check_current(expires_at, checkpoint)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("terminal workspace drain incomplete")
                self._condition.wait(timeout=min(remaining, 0.1))
            owner.check_current(expires_at, checkpoint)
            if time.monotonic() >= deadline:
                raise TimeoutError("terminal workspace drain incomplete")

            def drained(document: dict) -> dict:
                lifecycle_checkpoint()
                row = selected.selected(document, scope, cursor)
                row["drain"] = "drained"
                return row

            return owner._review_transaction(
                drained, expires_at=expires_at, checkpoint=checkpoint,
            )

    def compact_recovery(
        self, scope: dict, cursor: Cursor, policies: dict, *, expires_at: int,
        checkpoint: Callable[[], None], deadline: float, lifecycle_checkpoint: Callable[[], None],
    ) -> dict:
        """Rejoin actual admitted work; a prior persisted drain is not exclusion."""
        from .terminal_recovery import TerminalRecoveryStore, compact

        scope, policies = _scope(scope), copy.deepcopy(policies)
        owner = self.workspace.store
        TerminalRecoveryStore(owner)

        def current() -> None:
            checkpoint()
            lifecycle_checkpoint()
            if time.monotonic() >= deadline:
                raise TimeoutError("terminal workspace drain incomplete")

        self.drain(
            scope, cursor, expires_at=expires_at, checkpoint=current,
            deadline=deadline, lifecycle_checkpoint=lifecycle_checkpoint,
        )
        with self._condition:
            if self._active.get(_digest(scope), 0):
                raise TerminalAdmissionClosed("terminal workspace drain must be rejoined")

            def retire(document: dict) -> dict:
                current()
                result = compact(document, scope, cursor, policies)
                current()
                return result

            return owner._review_transaction(retire, expires_at=expires_at, checkpoint=current)

    def retire_cache(
        self, scope: dict, cursor: Cursor, *, expires_at: int,
        checkpoint: Callable[[], None], deadline: float, lifecycle_checkpoint: Callable[[], None],
    ) -> dict:
        """Resume one terminal epoch; delivery and unknown effects remain independent."""
        from dataclasses import asdict
        from .generation_cache import GenerationCache
        from .terminal_cache_retirement import TerminalCacheRetirementStore

        scope = _scope(scope)
        workspace, owner = self.workspace, self.workspace.store
        selected = TerminalCacheRetirementStore(owner)

        def current() -> None:
            checkpoint()
            lifecycle_checkpoint()
            if time.monotonic() >= deadline:
                raise TimeoutError("terminal workspace drain incomplete")

        selected.inspect(scope, cursor, expires_at=expires_at, checkpoint=current)
        self.drain(
            scope, cursor, expires_at=expires_at, checkpoint=current,
            deadline=deadline, lifecycle_checkpoint=lifecycle_checkpoint,
        )
        with self._condition:
            if self._active.get(_digest(scope), 0):
                raise TerminalAdmissionClosed("terminal workspace drain must be rejoined")

            def retire(document: dict, commit: Callable[[], None]) -> dict:
                _, progress = selected.selected(document, scope, cursor)

                def fenced():
                    owner.check_current(expires_at, current)
                    selected.selected(document, scope, cursor)

                def capture(ids, pointer):
                    row = {
                        "scope": copy.deepcopy(scope), "cursor": asdict(cursor),
                        "generation_ids": ids, "current_id": pointer, "stage": "captured",
                    }
                    document["terminal_cache_retirement"]["records"].append(row)
                    commit()
                    return row

                cache = GenerationCache(
                    workspace.cache.owner_root, owner.namespace,
                    max_bytes=workspace.cache.max_bytes, max_generations=workspace.cache.max_generations,
                    service_identity=owner.service_identity, checkpoint=fenced,
                )
                with workspace._materialization_lock:
                    progress = cache._retire_scope(scope, progress, capture=capture, checkpoint=fenced)
                    progress["stage"] = "cache_retired"
                    commit()
                    fenced()
                    for key in list(workspace._materializations):
                        if key[0] == _digest(scope):
                            fenced()
                            del workspace._materializations[key]
                    ids = set(progress["generation_ids"]) | {
                        row["generation_id"] for row in document["terminal_retention"]["generations"]
                        if row["scope"] == scope and row["epoch"] <= cursor.epoch
                    }
                    for identity in ids:
                        fenced()
                        workspace._generation_positions.pop(identity, None)
                    progress["stage"] = "complete"
                    fenced()
                return {
                    "status": "complete", **copy.deepcopy(progress),
                    "delivery_settled": False, "recovery_settled": False,
                }

            return owner._review_transaction(
                retire, expires_at=expires_at, checkpoint=current, staged=True,
            )
