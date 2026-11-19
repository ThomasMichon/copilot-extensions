"""Explicit retained-volume adoption under the existing service writer lease."""

from __future__ import annotations

import os
import time
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent_pull_requests.generation_cache import CacheFull, CacheInvalid, GenerationCache, _plain
from single_instance_lease import SingleInstance

from . import terminal

if TYPE_CHECKING:
    from .config import HostConfig


def _validate_cache(cache: GenerationCache, checkpoint: Callable[[], None]) -> None:
    with cache._transaction():
        checkpoint()
        if cache.descriptor.stat().st_size > cache.max_bytes:
            raise CacheFull("retained cache exceeds its configured capacity")
        for directory in (cache.generations, cache.targets):
            checkpoint()
            with os.scandir(directory) as entries:
                if next(entries, None) is not None:
                    raise CacheInvalid("populated cache requires a recovery-compatible reader transition")
            checkpoint()


def adopt(config: HostConfig) -> None:
    """Bootstrap an eligible owner without excluding readers of retained work."""
    workspace = config.workspaces
    if workspace is None or not workspace.terminal_targets:
        raise ValueError("retained transition requires explicit terminal workspace selection")
    readers = {(principal, target): dict(reader.scope) for principal, target, reader in workspace.readers}
    scopes: dict[tuple[str, str], dict[str, Any]] = {}
    for binding in workspace.terminal_targets:
        key = binding.scope.account, binding.scope.repo
        scope = readers[(binding.principal, binding.target)]
        if scopes.setdefault(key, scope) != scope:
            raise ValueError("terminal watch binding is ambiguous across workspace scopes")
    store = config.store()
    if (
        not config.root.is_dir() or config.root.is_symlink()
        or not store.path.is_file() or store.path.is_symlink()
    ):
        raise ValueError("retained transition requires an existing owner volume")
    mapping = terminal.mapping_core() if workspace.terminal_mapping_reads else None
    lease = SingleInstance(config.root, service="agent-pull-requests-watch")
    if lease.lock_path.exists() or lease.lock_path.is_symlink():
        _plain(lease.lock_path)
    pid = os.getpid()
    deadline = time.monotonic() + config.operation_seconds
    expires_at = math.ceil(time.time() + config.operation_seconds)

    def lifecycle() -> None:
        if not lease.held or os.getpid() != pid or time.monotonic() >= deadline:
            raise ValueError("retained workspace lifecycle exclusion unavailable")

    def validate(document: dict[str, Any]) -> None:
        lifecycle()
        if document["version"] not in (3, 4, 5, 6):
            raise ValueError("retained transition requires an adopted conversation owner")
        if document["reviews"]:
            raise ValueError("active review operations require their original recovery reader")
        if document.get("terminal_recovery", {}).get("operations"):
            raise ValueError("compact recovery operations require their original recovery reader")
        if any(document.get("terminal_retention", {}).get(name) for name in (
            "records", "generations", "admissions",
        )):
            raise ValueError("populated terminal state requires its original lifecycle transition")
        if any(
            record["outcome"] == "active" and record["deadline_at"] is None
            for record in document["replay"]["records"]
        ):
            raise ValueError("terminal reader requires original finite delivery deadlines")
        if mapping is not None and (
            document.get("review_intent_version") != 2
            or document.get("review_comment_mapping_version") != 1
        ):
            raise ValueError("retained terminal mapping requires existing prepared comment mappings")

    with lease:
        lifecycle()
        native = config.root / "watch-subscriptions.json"
        if native.exists() or native.is_symlink():
            raise ValueError("native and replay state roots must be separate")
        store._review_transaction(
            validate, expires_at=expires_at, checkpoint=lifecycle, persist=False,
        )
        cache = workspace.cache(store)
        cache.checkpoint = lifecycle
        _validate_cache(cache, lifecycle)
        terminal.adopt(store, lifecycle, expires_at)
        if mapping is not None:
            mapping(store).adopt(
                lifecycle_checkpoint=lifecycle, expires_at=expires_at, checkpoint=lifecycle,
            )
        lifecycle()
