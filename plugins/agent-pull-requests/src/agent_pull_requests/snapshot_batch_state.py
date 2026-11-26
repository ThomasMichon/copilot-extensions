"""Bounded query admission metadata in the existing replay-owner transaction."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from urllib.parse import urlsplit
from time import monotonic
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from .repository_contract import RepositoryIdentity
from .native_command import NativeCommandNotStarted

if TYPE_CHECKING:
    from .watch_replay_store import ReplayStore

PROTOCOL = "agent-worktrees.pr-snapshot-batch/v1"
VERSION_FIELD = "github_snapshot_batches_version"
STATE_FIELD = "github_snapshot_batches"
MAX_SCOPES = 64
SCOPE_FIELDS = {"key", "pending", "primary_until", "secondary_until", "observed_at"}
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


def _identity(value: object, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and re.fullmatch(r"[a-f0-9]+", value) is not None


def validate(value: object, *, version: int = 1) -> None:
    """Validate the selected historical or recovery-aware batch layout."""
    if version not in (1, 2):
        raise ValueError("unsupported snapshot batch state revision")
    fields = {"scopes"} if version == 1 else {"scopes", "recoveries"}
    if not isinstance(value, dict) or set(value) != fields or (
        not isinstance(value["scopes"], list) or len(value["scopes"]) > MAX_SCOPES
    ):
        raise ValueError("invalid bounded snapshot batch state")
    scopes: dict[str, Document] = {}
    for row in value["scopes"]:
        if not isinstance(row, dict) or set(row) != SCOPE_FIELDS or (
            not _identity(row["key"], 64) or row["key"] in scopes
            or row["pending"] is not None and not _identity(row["pending"], 32)
            or any(not _instant(row[name]) for name in (
                "primary_until", "secondary_until", "observed_at",
            ))
        ):
            raise ValueError("invalid snapshot batch admission")
        scopes[row["key"]] = row
    if version == 2:
        from .snapshot_batch_recovery import validate_records
        validate_records(value["recoveries"], scopes)


def validate_extension(document: Document, *, selected: bool) -> set[str]:
    from .watch_replay_store import ReplayStoreInvalid
    present = VERSION_FIELD in document
    version = document.get(VERSION_FIELD)
    if present != selected:
        raise ReplayStoreInvalid("snapshot batch reader unavailable for adopted owner")
    if not present:
        return set()
    if type(version) is not int or version not in (1, 2):
        raise ReplayStoreInvalid("snapshot batch reader unavailable for adopted owner")
    validate(document.get(STATE_FIELD), version=version)
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
        native_deadline: float | None = None,
    ) -> str | None:
        reserved: str | None = None
        def admitted() -> None:
            if checkpoint is not None:
                checkpoint()
            if native_deadline is not None and monotonic() >= native_deadline:
                raise NativeCommandNotStarted("native cutoff reached before query admission")
        def reserve(row: Document) -> str | None:
            nonlocal reserved
            admitted()
            now = self.store.clock()
            if row["pending"] is not None or now < max(
                row["observed_at"], row["primary_until"], row["secondary_until"],
            ):
                return None
            reserved = uuid.uuid4().hex
            row["pending"] = reserved
            row["observed_at"] = now
            return reserved
        try:
            return self._transaction(key, reserve, expires_at=expires_at, checkpoint=admitted, persist=True)
        except NativeCommandNotStarted:
            if reserved is not None:
                self.cancel_prelaunch(key, reserved, expires_at=expires_at, checkpoint=checkpoint)
            raise

    def cancel_prelaunch(
        self, key: str, operation: str, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        """Only the still-prelaunch caller may clear its own proven unsent reservation."""
        def cancel(document: Document) -> None:
            for row in document[STATE_FIELD]["scopes"]:
                if row["key"] == key and row["pending"] == operation:
                    row["pending"] = None
        self.store._review_transaction(cancel, expires_at=expires_at, checkpoint=checkpoint)

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
