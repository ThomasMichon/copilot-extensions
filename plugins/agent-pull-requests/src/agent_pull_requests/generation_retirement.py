"""Scoped physical retirement through GenerationCache's existing file owner."""

from __future__ import annotations

import os

from .generation_cache import CacheFull, CacheInvalid, MAX_POINTER_BYTES, POINTER_SCHEMA, _encoded, _plain
from .review_workspace import MAX_GENERATION_BYTES, _digest, _scope
from .watch_storage import _atomic_write_json


def retire_scope(cache, scope: dict, progress: dict | None, *, capture, checkpoint) -> dict:
    """Caller holds replay custody and actual lifecycle/materialization exclusion."""
    scope = _scope(scope)
    with cache._transaction() as lock:
        def current():
            cache._check(lock)
            checkpoint()

        current()
        generations, pointers, total, _ = cache._inventory(include_staging=True)
        selected = []
        for path in generations:
            value = cache._read(path, MAX_GENERATION_BYTES)
            object_scope = _scope(value.get("scope"))
            cache._load_generation(path.stem, object_scope)
            if object_scope == scope:
                selected.append(path.stem)
        for path in pointers:
            value = cache._read(path, MAX_POINTER_BYTES)
            pointer_scope = _scope(value.get("scope"))
            if path.stem != _digest(pointer_scope):
                raise CacheInvalid("cache pointer filename does not match its scope")
            cache._current(pointer_scope)
        pointer = cache._current(scope)
        target = cache.targets / (_digest(scope) + ".json")
        tombstone = {
            "schema": POINTER_SCHEMA, "version": 1, "scope": scope, "generation_id": None,
        }
        if total - (target.stat().st_size if target.exists() else 0) + len(_encoded(tombstone)) > cache.max_bytes:
            raise CacheFull("terminal pointer does not fit configured cache capacity")
        if progress is None:
            # Capture only validated owned inventory, including pre-adoption objects.
            current()
            progress = capture(sorted(selected), pointer)
            current()
        ids = progress["generation_ids"]
        if any(value not in ids for value in selected) or pointer not in (None, progress["current_id"]):
            raise CacheInvalid("terminal cache inventory changed after capture")
        if pointer is not None and any(value not in selected for value in ids):
            raise CacheInvalid("terminal cache objects missing before pointer clearing")
        current()
        # A previous uncertain replacement is read above, then re-acknowledged
        # durably here. No object removal follows a failed acknowledgement.
        _atomic_write_json(target, tombstone)
        for identity in ids:
            current()
            path = cache.generations / (identity + ".json")
            if path.exists() or path.is_symlink():
                _plain(path)
                cache._load_generation(identity, scope)
                current()
                path.unlink()
        if os.name != "nt":
            directory = os.open(cache.generations, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        current()
        return progress
