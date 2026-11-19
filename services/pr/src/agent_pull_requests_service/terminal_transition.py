"""Explicit retained-volume adoption under the existing service writer lease."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent_pull_requests.generation_cache import (
    MAX_POINTER_BYTES, CacheFull, CacheInvalid, GenerationCache, _identifier, _plain,
)
from agent_pull_requests.review_workspace import MAX_GENERATION_BYTES, _digest, _scope
from single_instance_lease import SingleInstance

from . import terminal

if TYPE_CHECKING:
    from .config import HostConfig


def _validate_cache(cache: GenerationCache, checkpoint: Callable[[], None]) -> None:
    with cache._transaction():
        total, count = cache.descriptor.stat().st_size, 0
        for directory in (cache.generations, cache.targets):
            for path in directory.iterdir():
                checkpoint()
                _plain(path)
                _identifier(path.stem)
                if path.suffix != ".json":
                    raise CacheInvalid("unexpected retained cache entry")
                total += path.stat().st_size
                if directory == cache.generations:
                    count += 1
                if total > cache.max_bytes or count > cache.max_generations:
                    raise CacheFull("retained cache exceeds its configured capacity")
                checkpoint()
                value = cache._read(
                    path, MAX_GENERATION_BYTES if directory == cache.generations else MAX_POINTER_BYTES,
                )
                scope = _scope(value.get("scope"))
                checkpoint()
                if directory == cache.generations:
                    cache._load_generation(path.stem, scope)
                else:
                    if path.stem != _digest(scope):
                        raise CacheInvalid("cache pointer filename differs from its scope")
                    cache._current(scope)
                checkpoint()


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
    if lease.lock_path.exists() or lease.lock_path.is_symlink():
        _plain(lease.lock_path)
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
        if document["reviews"]:
            raise ValueError("active review operations require their original recovery reader")
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
