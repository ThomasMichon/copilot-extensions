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
    checkpoint()
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
    """Adopt eligible retained work without losing its original usable reader."""
    workspace = config.workspaces
    if workspace is None or not workspace.terminal_targets:
        raise ValueError("retained transition requires explicit terminal workspace selection")
    readers = {(principal, target): dict(reader.scope) for principal, target, reader in workspace.readers}
    scopes: dict[tuple[str, str], dict[str, Any]] = {}
    targets: dict[tuple[tuple[str, Any], ...], tuple[str, str]] = {}
    for binding in workspace.terminal_targets:
        key = binding.scope.account, binding.scope.repo
        scope = readers[(binding.principal, binding.target)]
        if scopes.setdefault(key, scope) != scope:
            raise ValueError("terminal watch binding is ambiguous across workspace scopes")
        if targets.setdefault(tuple(sorted(scope.items())), key) != key:
            raise ValueError("workspace scope is ambiguous across terminal watch targets")
    store = config.store()
    from .terminal_policy import registration
    if (
        not config.root.is_dir() or config.root.is_symlink()
        or not store.path.is_file() or store.path.is_symlink()
    ):
        raise ValueError("retained transition requires an existing owner volume")
    mapping = terminal.mapping_core() if workspace.terminal_mapping_reads else None
    retained = terminal.retained_cache_supported()
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
        if document["version"] != 3:
            raise ValueError("bootstrap requires the standalone version-three conversation owner")
        if document["reviews"] and not workspace.retained_active_reviews:
            raise ValueError("active review operations require their original recovery reader")
        if workspace.retained_active_reviews:
            terminal.active_review_core()
            if not store.retained_active_reviews or any(name in document for name in (
                "review_intent_version", "review_selection_version", "review_comment_mapping_version",
            )):
                raise ValueError("original unprepared active review reader required")
            for operation in document["reviews"]:
                lifecycle()
                selected = operation["intent"]
                if selected["version"] != 1 or {
                    name: value for name, value in selected["scope"].items() if name != "number"
                } not in scopes.values():
                    raise ValueError("retained review operation is outside the selected original scope")
        if document.get("terminal_recovery", {}).get("operations"):
            raise ValueError("compact recovery operations require their original recovery reader")
        if any(document.get("terminal_retention", {}).get(name) for name in (
            ("records",) if retained else ("records", "generations", "admissions")
        )):
            raise ValueError("populated terminal state requires its original lifecycle transition")
        if not retained and document["conversations"]["records"]:
            raise ValueError("retained conversations require their original lifecycle transition")
        admitted = [vars(binding.scope) for binding in config.bindings]
        for record in document["replay"]["records"]:
            if record["scope"] not in admitted:
                raise ValueError("retained replay scope is absent from the selected bindings")
            registration(record)
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

        def cache_current() -> None:
            lifecycle()
            if cache.checkpoint is not None:
                cache.checkpoint()

        if retained:
            terminal.adopt(
                store, cache_current, expires_at, retained_cache=cache,
                retained_targets=[
                    {"scope": scope, "account": account, "repo": repo}
                    for (account, repo), scope in scopes.items()
                ],
            )
        else:
            _validate_cache(cache, cache_current)
            terminal.adopt(store, cache_current, expires_at)
        if mapping is not None:
            mapping(store).adopt(
                lifecycle_checkpoint=cache_current, expires_at=expires_at, checkpoint=cache_current,
            )
        cache_current()
