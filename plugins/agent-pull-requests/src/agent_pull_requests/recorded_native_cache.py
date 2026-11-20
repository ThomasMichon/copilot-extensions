"""Import original native admissions, never reconstruct them from current config."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .generation_cache import CacheInvalid
from .review_intent import encoded
from .watch_replay import WatchReplay

Document = dict[str, Any]


def contract() -> Document:
    return {
        "protocol": "agent-pull-requests.recorded-native-cache-adoption/v1",
        "storage_extension": "recorded_terminal_cache_version:2",
        "bindings": "original_confirmed_native_targets", "legacy_backfill": False,
    }


def original_targets(
    document: Document, targets: list[Document], checkpoint: Callable[[], None],
) -> dict[bytes, Document]:
    """Check all history, including associations without a current cache pointer."""
    if document.get("generation_associations_version") != 2:
        raise CacheInvalid("native adoption requires original native generation associations")
    configured = {encoded(row["scope"]): (row["account"], row["repo"]) for row in targets}
    scopes: dict[bytes, Document] = {}
    native: dict[bytes, bytes] = {}
    for row in document["generation_associations"]["records"]:
        checkpoint()
        scope, target = row["scope"], row["native_target"]
        full = {key: value for key, value in scope.items() if key != "number"}
        key, binding = encoded(scope), encoded(target)
        if (
            row["publication"] != "confirmed"
            or configured.get(encoded(full)) != (target["account"], target["repo"])
            or scopes.setdefault(key, target) != target
            or native.setdefault(binding, key) != key
        ):
            raise CacheInvalid("original native target conflicts with retained history or selection")
    return scopes


def admissions(
    document: Document, bindings: list[Document], targets: list[Document],
    checkpoint: Callable[[], None],
) -> list[Document]:
    """Only an actual validated retained object can supply an original admission."""
    scopes = original_targets(document, targets, checkpoint)
    retained = {(encoded(row["scope"]), row["generation_id"]) for row in bindings}
    by_scope: dict[bytes, Document] = {}
    by_target: set[bytes] = set()
    for row in document["generation_associations"]["records"]:
        checkpoint()
        key = encoded(row["scope"])
        if (key, row["generation_id"]) not in retained:
            continue
        if (
            row["cursor"]["epoch"] != 1 or row["complete"] is not True
            or row["state"] != "open" or row["acquisition_epoch"] not in (None, 1)
            or row["acquisition_state"] not in (None, "open")
        ):
            raise CacheInvalid("native admission lacks its original complete open first epoch")
        previous = by_scope.get(key)
        if previous is None or row["cursor"]["sequence"] < previous["cursor"]["sequence"]:
            by_scope[key] = {
                "scope": copy.deepcopy(row["scope"]), "cursor": copy.deepcopy(row["cursor"]),
                "target": copy.deepcopy(scopes[key]),
            }
        by_target.add(encoded(scopes[key]))
    replay = WatchReplay(document["namespace"], state=document["replay"], commit=lambda _state: None)
    for target in replay.observation_targets():
        checkpoint()
        if encoded({
            "account": target.account, "repo": target.repo, "number": target.number,
        }) not in by_target:
            raise CacheInvalid("waiting native target lacks an original retained workspace admission")
    return sorted(by_scope.values(), key=encoded)
