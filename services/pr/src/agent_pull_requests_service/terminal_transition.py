"""Explicit retained-volume adoption under the existing service writer lease."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from single_instance_lease import SingleInstance

from . import terminal

if TYPE_CHECKING:
    from .config import HostConfig


def adopt(config: HostConfig) -> None:
    workspace = config.workspaces
    if workspace is None or not workspace.terminal_targets:
        raise ValueError("retained transition requires explicit terminal workspace selection")
    store = config.store()
    if (
        not config.root.is_dir() or config.root.is_symlink()
        or not store.path.is_file() or store.path.is_symlink()
    ):
        raise ValueError("retained transition requires an existing owner volume")
    mapping = terminal.mapping_core() if workspace.terminal_mapping_reads else None
    lease = SingleInstance(config.root, service="agent-pull-requests-watch")
    pid = os.getpid()
    deadline = time.monotonic() + config.operation_seconds
    expires_at = int(time.time()) + config.operation_seconds

    def lifecycle() -> None:
        if not lease.held or os.getpid() != pid or time.monotonic() >= deadline:
            raise ValueError("retained workspace lifecycle exclusion unavailable")

    def validate(document: dict[str, Any]) -> None:
        lifecycle()
        if document["version"] not in (3, 4, 5, 6):
            raise ValueError("retained transition requires an adopted conversation owner")
        if mapping is not None and (
            document.get("review_intent_version") != 2
            or document.get("review_comment_mapping_version") != 1
        ):
            raise ValueError("retained terminal mapping requires existing prepared comment mappings")

    with lease:
        lifecycle()
        store._review_transaction(
            validate, expires_at=expires_at, checkpoint=lifecycle, persist=False,
        )
        cache = workspace.cache(store)
        cache.checkpoint = lifecycle
        with cache._transaction():
            cache._inventory()
        terminal.adopt(store, lifecycle, expires_at)
        if mapping is not None:
            mapping(store).adopt(
                lifecycle_checkpoint=lifecycle, expires_at=expires_at, checkpoint=lifecycle,
            )
        lifecycle()
