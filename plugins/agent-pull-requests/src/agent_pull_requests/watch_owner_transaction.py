"""Owner transactions with optional authority checks at atomic replacement."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from .watch_replay_store import Checkpoint, Commit, Document, ReplayStore

_T = TypeVar("_T")


def review_transaction(
    store: ReplayStore,
    operation: Callable[[Document], _T] | Callable[[Document, Commit], _T], *,
    expires_at: int, checkpoint: Checkpoint | None, persist: bool, staged: bool,
    before_replace: Checkpoint | None,
) -> _T:
    from . import watch_replay_store as storage

    if checkpoint is not None and not callable(checkpoint):
        raise ValueError("invalid request checkpoint")
    if before_replace is not None and not callable(before_replace):
        raise ValueError("invalid replacement checkpoint")
    store.check_current(expires_at, checkpoint)
    with store._review_lock(expires_at, checkpoint) as lock:
        store.check_current(expires_at, checkpoint)
        document = store._read_document()
        if document.get("schema") != storage.OWNER_SCHEMA:
            raise storage.ReplayStoreInvalid("review operations require explicit owner adoption")
        store._validate_owner(document)

        def replacement_checkpoint() -> None:
            lock.assert_owned()
            store.check_current(expires_at, checkpoint)
            if before_replace is not None:
                before_replace()

        def commit() -> None:
            store._validate_owner(document)
            lock.assert_owned()
            store.check_current(expires_at, checkpoint)
            if before_replace is None:
                storage._atomic_write_json(store.path, document)
            else:
                storage._atomic_write_json(
                    store.path, document, before_replace=replacement_checkpoint,
                )
            store.check_current(expires_at, checkpoint)

        # Staged operations reserve durably before a separate pointer write,
        # without releasing replay custody.
        result = (
            cast("Callable[[Document, Commit], _T]", operation)(document, commit) if staged
            else cast("Callable[[Document], _T]", operation)(document)
        )
        store._validate_owner(document)
        store.check_current(expires_at, checkpoint)
        if persist:
            commit()
        store.check_current(expires_at, checkpoint)
        return copy.deepcopy(result)
