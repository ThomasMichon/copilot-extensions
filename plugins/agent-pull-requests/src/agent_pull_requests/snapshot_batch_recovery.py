"""Explicit owner transition for an unresolved observation, never its settlement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from .snapshot_batch_state import SCOPE_FIELDS, STATE_FIELD, VERSION_FIELD, Document, _identity, _instant

if TYPE_CHECKING:
    from .watch_replay_store import ReplayStore

RECOVERY_PROTOCOL = "agent-pull-requests.snapshot-query-recovery/v1"
MAX_RECOVERIES = 64
_POLICY: Final = "retain-unknown"
_DISPOSITION: Final = "outcome-unknown"


@dataclass(frozen=True)
class RecoverySelection:
    """Deliberate selection of an exact account/API fence and stable recovery ID."""

    key: str
    pending: str
    recovery_id: str
    policy: Literal["retain-unknown"]

    def __post_init__(self) -> None:
        _check_selection(self)


class RecoveryRecord(TypedDict):
    recovery_id: str
    key: str
    pending: str
    observed_at: float
    primary_until: float
    secondary_until: float
    recorded_at: float
    disposition: Literal["outcome-unknown"]


def _check_selection(selection: RecoverySelection) -> None:
    if (
        not isinstance(selection, RecoverySelection)
        or selection.policy != _POLICY
        or not _identity(selection.key, 64)
        or not _identity(selection.pending, 32)
        or not _identity(selection.recovery_id, 32)
    ):
        raise ValueError("explicit exact-fence snapshot query recovery selection required")


def validate_records(value: object, scopes: Mapping[str, Document]) -> None:
    """Validate bounded recovery facts against their continuing admission scopes."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RECOVERIES:
        raise ValueError("invalid bounded snapshot query recovery history")
    identities: set[str] = set()
    fences: set[tuple[str, str]] = set()
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != SCOPE_FIELDS | {"recovery_id", "recorded_at", "disposition"}
            or not _identity(row["key"], 64)
            or not _identity(row["pending"], 32)
            or not _identity(row["recovery_id"], 32)
            or row["disposition"] != _DISPOSITION
            or not all(_instant(row[field]) for field in (
                "observed_at", "recorded_at", "primary_until", "secondary_until",
            ))
        ):
            raise ValueError("invalid snapshot query recovery record")
        current = scopes.get(row["key"])
        fence = row["key"], row["pending"]
        if (
            current is None or row["recovery_id"] in identities or fence in fences
            or current["pending"] == row["pending"]
            or not row["observed_at"] <= row["recorded_at"] <= current["observed_at"]
            or any(current[field] < row[field] for field in ("primary_until", "secondary_until"))
        ):
            raise ValueError("snapshot query recovery history conflicts with admission")
        identities.add(row["recovery_id"])
        fences.add(fence)


def _check_store(store: ReplayStore, selection: RecoverySelection) -> None:
    _check_selection(selection)
    if not store.github_snapshot_batches:
        raise ValueError("selected snapshot batch owner and reader required")


def recover(
    store: ReplayStore, selection: RecoverySelection, *,
    lifecycle_checkpoint: Callable[[], None], expires_at: int,
    checkpoint: Callable[[], None] | None = None,
) -> RecoveryRecord:
    """Release only the selected fence under the embedding owner's joined lifecycle."""
    _check_store(store, selection)
    if not callable(lifecycle_checkpoint) or checkpoint is not None and not callable(checkpoint):
        raise ValueError("snapshot query recovery requires current lifecycle authority")
    recorded_at: float | None = None

    def current() -> None:
        lifecycle_checkpoint()
        if checkpoint is not None:
            checkpoint()
        if recorded_at is not None and not recorded_at <= store.clock():
            raise ValueError("snapshot batch clock moved backwards")

    def transition(document: Document) -> RecoveryRecord:
        nonlocal recorded_at
        state = document[STATE_FIELD]
        history = state.get("recoveries", [])
        if any(row["recovery_id"] == selection.recovery_id for row in history):
            raise ValueError("recovery identity already recorded; use original readback")
        row = next((row for row in state["scopes"] if row["key"] == selection.key), None)
        if row is None or row["pending"] != selection.pending:
            raise ValueError("snapshot query fence differs from selected admission")
        now = store.clock()
        if not _instant(now) or now < row["observed_at"]:
            raise ValueError("invalid or backwards snapshot batch clock")
        if len(history) >= MAX_RECOVERIES:
            raise ValueError("snapshot query recovery capacity exhausted")
        recorded_at = now
        result: RecoveryRecord = {
            "key": selection.key,
            "pending": selection.pending,
            "recovery_id": selection.recovery_id,
            "observed_at": row["observed_at"],
            "primary_until": row["primary_until"],
            "secondary_until": row["secondary_until"],
            "recorded_at": now,
            "disposition": _DISPOSITION,
        }
        history.append(result)
        state["recoveries"] = history
        document[VERSION_FIELD] = 2
        row.update(pending=None, observed_at=now)
        return result

    return store._review_transaction(transition, expires_at=expires_at, checkpoint=current)


def read_recovery(
    store: ReplayStore, selection: RecoverySelection, *, expires_at: int,
    checkpoint: Callable[[], None] | None = None,
) -> RecoveryRecord | None:
    """Read the exact original selection without touching current query admission."""
    _check_store(store, selection)

    def read(document: Document) -> RecoveryRecord | None:
        rows: list[RecoveryRecord] = document[STATE_FIELD].get("recoveries", [])
        for row in rows:
            if row["recovery_id"] == selection.recovery_id:
                if row["key"] != selection.key or row["pending"] != selection.pending:
                    raise ValueError("snapshot query recovery readback selection differs")
                return row
        return None

    return store._review_transaction(
        read, expires_at=expires_at, checkpoint=checkpoint, persist=False,
    )
