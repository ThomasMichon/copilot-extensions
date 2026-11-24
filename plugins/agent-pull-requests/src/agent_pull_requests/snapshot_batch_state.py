"""Bounded query admission metadata in the existing replay-owner transaction."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from urllib.parse import urlsplit
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from .repository_contract import RepositoryIdentity

if TYPE_CHECKING:
    from .watch_replay_store import ReplayStore

PROTOCOL = "agent-worktrees.pr-snapshot-batch/v1"
VERSION_FIELD = "github_snapshot_batches_version"
STATE_FIELD = "github_snapshot_batches"
MAX_SCOPES = 64
Document = dict[str, Any]
_T = TypeVar("_T")


def scope(identity: RepositoryIdentity) -> str:
    """Secondary limits belong to the account/API, not the current PR or binding."""
    endpoint, account = identity.endpoint, identity.account
    if identity.provider == "github":
        address = urlsplit(endpoint)
        if address.hostname in ("github.com", "api.github.com") and (
            address.port in (None, 443) and address.path in ("", "/api/v3")
        ):
            endpoint = "https://api.github.com"
        account = account.casefold()
    return hashlib.sha256(json.dumps([
        identity.provider, endpoint, account,
    ]).encode()).hexdigest()


def _instant(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        0 <= value <= 253402300799 and math.isfinite(value)
    )


def validate(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"scopes"} or (
        not isinstance(value["scopes"], list) or len(value["scopes"]) > MAX_SCOPES
    ):
        raise ValueError("invalid bounded snapshot batch state")
    keys: set[str] = set()
    for row in value["scopes"]:
        if not isinstance(row, dict) or set(row) != {
            "key", "pending", "primary_until", "secondary_until", "observed_at",
        } or (
            not isinstance(row["key"], str) or re.fullmatch(r"[a-f0-9]{64}", row["key"]) is None
            or row["key"] in keys
            or row["pending"] is not None and (
                not isinstance(row["pending"], str)
                or re.fullmatch(r"[a-f0-9]{32}", row["pending"]) is None
            )
            or any(not _instant(row[name]) for name in (
                "primary_until", "secondary_until", "observed_at",
            ))
        ):
            raise ValueError("invalid snapshot batch admission")
        keys.add(row["key"])


def validate_extension(document: Document, *, selected: bool) -> set[str]:
    from .watch_replay_store import ReplayStoreInvalid
    present = VERSION_FIELD in document
    if present != selected or present and (
        type(document[VERSION_FIELD]) is not int or document[VERSION_FIELD] != 1
    ):
        raise ReplayStoreInvalid("snapshot batch reader unavailable for adopted owner")
    if not present:
        return set()
    validate(document.get(STATE_FIELD))
    return {VERSION_FIELD, STATE_FIELD}


def adopt(
    store: ReplayStore, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
    checkpoint: Callable[[], None] | None = None,
) -> Document:
    if not store.github_snapshot_batches or not callable(lifecycle_checkpoint):
        raise ValueError("selected snapshot batch owner and reader lifecycle required")
    store.check_current(expires_at, checkpoint)
    with store._lock() as lock:
        lifecycle_checkpoint()
        document = store._read_document()
        if VERSION_FIELD in document:
            store._validate_owner(document)
            return {"status": "already_adopted"}
        prior = copy.copy(store)
        prior.github_snapshot_batches = False
        prior._validate_owner(document)
        document.update({VERSION_FIELD: 1, STATE_FIELD: {"scopes": []}})
        store._validate_owner(document)
        lock.assert_owned()
        lifecycle_checkpoint()
        store.check_current(expires_at, checkpoint)
        from .watch_storage import _atomic_write_json
        _atomic_write_json(store.path, document)
        store.check_current(expires_at, checkpoint)
        return {"status": "adopted"}


class BatchAdmission:
    """An interrupted/undecodable query remains unresolved, never an expired lease."""

    def __init__(self, store: ReplayStore) -> None:
        if not store.github_snapshot_batches:
            raise ValueError("snapshot batch persistence is not selected")
        self.store = store

    def _transaction(
        self, key: str, operation: Callable[[Document], _T], *, expires_at: int,
        checkpoint: Callable[[], None] | None, persist: bool,
    ) -> _T:
        def selected(document: Document) -> _T:
            rows = document[STATE_FIELD]["scopes"]
            row = next((row for row in rows if row["key"] == key), None)
            if row is None:
                row = {
                    "key": key, "pending": None, "primary_until": 0,
                    "secondary_until": 0, "observed_at": 0,
                }
                if persist:
                    if len(rows) == MAX_SCOPES:
                        raise ValueError("snapshot batch admission capacity exhausted")
                    rows.append(row)
            return operation(row)
        return self.store._review_transaction(
            selected, expires_at=expires_at, checkpoint=checkpoint, persist=persist,
        )

    def allowed(
        self, key: str, *, query: bool, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> bool:
        def check(row: Document) -> bool:
            now = self.store.clock()
            return bool(
                row["pending"] is None and now >= row["observed_at"]
                and now >= row["secondary_until"]
                and (not query or now >= row["primary_until"])
            )
        return self._transaction(key, check, expires_at=expires_at, checkpoint=checkpoint, persist=False)

    def begin(
        self, key: str, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> str | None:
        def reserve(row: Document) -> str | None:
            now = self.store.clock()
            if row["pending"] is not None or now < max(
                row["observed_at"], row["primary_until"], row["secondary_until"],
            ):
                return None
            row["pending"] = uuid.uuid4().hex
            row["observed_at"] = now
            return str(row["pending"])
        return self._transaction(key, reserve, expires_at=expires_at, checkpoint=checkpoint, persist=True)

    def finish(
        self, key: str, operation: str, backoff: Document | None, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        if backoff is not None and (
            set(backoff) != {"resource", "not_before", "reason"}
            or backoff["resource"] not in ("graphql-primary", "shared-secondary")
            or not _instant(backoff["not_before"])
            or not isinstance(backoff["reason"], str) or not 1 <= len(backoff["reason"]) <= 128
        ):
            raise ValueError("invalid snapshot batch backoff settlement")
        def settle(row: Document) -> None:
            if row["pending"] != operation:
                raise ValueError("snapshot batch operation differs from retained admission")
            now = self.store.clock()
            if now < row["observed_at"]:
                raise ValueError("snapshot batch clock moved backwards")
            if backoff is not None:
                field = "primary_until" if backoff["resource"] == "graphql-primary" else "secondary_until"
                row[field] = max(row[field], backoff["not_before"])
            row.update(pending=None, observed_at=now)
        self._transaction(key, settle, expires_at=expires_at, checkpoint=checkpoint, persist=True)
