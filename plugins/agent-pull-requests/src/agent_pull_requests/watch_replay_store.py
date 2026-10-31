"""Disk-backed replay transactions, separate from the native callback owner."""

from __future__ import annotations

import copy
import json
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from single_instance_lease import OperationLock

from .watch_contract import PRSnapshot
from .watch_replay import ConsumerScope, ObservationTarget, WatchReplay
from .watch_storage import _atomic_write_json
from . import review_intent
from .review_workspace import _digest, _scope

_T = TypeVar("_T")
SERVICE_SCHEMA = "agent-pull-requests.watch-service-store/v1"
OWNER_SCHEMA = "agent-pull-requests.owner-store"
MAX_OWNER_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class ReplayServiceIdentity:
    """Trusted storage binding, not authentication or placement authority."""

    fleet: str
    gateway: str
    slot: str

    def __post_init__(self) -> None:
        for value in (self.fleet, self.gateway, self.slot):
            if (
                not isinstance(value, str) or not value or len(value) > 256
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("invalid replay service identity")

    def document(self) -> dict[str, str]:
        return {"fleet": self.fleet, "gateway": self.gateway, "slot": self.slot}


class ReplayStoreInvalid(ValueError):
    """Previously provisioned replay state cannot be restored."""


class ReplayCallerExpired(PermissionError):
    """Caller authority expired before a transaction could commit."""


def json_object(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


class ReplayStore:
    """Hold existing operation custody across each complete load/use/commit.

    No cached core outlives its transaction. A cancelled network request may
    still commit in its worker thread; the kernel lock stays held until that
    transaction finishes. Reconcile its original identity, never assume absence.
    This is not a daemon lease or placement/update authority.
    """

    def __init__(
        self, root: Path, namespace: str, *, clock: Callable[[], float] = time.time,
        service_identity: ReplayServiceIdentity | None = None,
    ) -> None:
        if service_identity is not None and not isinstance(service_identity, ReplayServiceIdentity):
            raise ValueError("typed replay service identity required")
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError("replay store requires an absolute canonical root")
        self.namespace = namespace
        self.clock = clock
        self.service_identity = service_identity
        self.path = self.root / "watch-replay.json"
        # Validate the namespace without reading or provisioning any state.
        WatchReplay(namespace, state=None, commit=lambda _state: None, clock=clock)

    def _lock(self) -> OperationLock:
        return OperationLock(self.root / "watch-replay.lock")

    def _encode(self, state: dict, *, owner_state: dict | None = None) -> dict:
        if owner_state is not None and owner_state.get("schema") == OWNER_SCHEMA:
            document = copy.deepcopy(owner_state)
            document["replay"] = state
            self._validate_owner(document)
            return document
        if self.service_identity is None:
            return state
        return {
            "schema": SERVICE_SCHEMA, "service": self.service_identity.document(),
            "replay": state,
        }

    def _decode(self, state: dict) -> dict:
        if state.get("schema") == OWNER_SCHEMA:
            self._validate_owner(state)
            return copy.deepcopy(state["replay"])
        return self._decode_legacy(state)

    def _decode_legacy(self, state: dict) -> dict:
        if self.service_identity is None:
            return state
        if (
            set(state) != {"schema", "service", "replay"}
            or state["schema"] != SERVICE_SCHEMA
            or state["service"] != self.service_identity.document()
            or not isinstance(state["replay"], dict)
        ):
            raise ValueError("replay service storage binding mismatch")
        return state["replay"]

    def _read_document(self) -> dict:
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ReplayStoreInvalid("linked or unsafe replay state")
        with self.path.open("rb") as stream:
            raw = stream.read(MAX_OWNER_BYTES + 1)
        if len(raw) > MAX_OWNER_BYTES:
            raise ReplayStoreInvalid("owner state exceeds its byte bound")
        try:
            return json_object(raw)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ReplayStoreInvalid("invalid persisted owner state") from None

    @staticmethod
    def _replay_reservation(state: dict) -> int:
        from .watch_replay import MAX_BATCH_BYTES
        growth = 0
        for group in state["registries"]:
            for subscription in group["subscriptions"]:
                # A waiting observation may fill unknown baseline fields and
                # create one bounded pending event. Once pending is frozen,
                # noncontinuous replay cannot grow another event/baseline.
                growth += 2048 if subscription.get("pending") is not None else 2 * MAX_BATCH_BYTES + 2048
        return len(review_intent.encoded(state)) + growth + 1024

    def _validate_owner(self, document: dict) -> None:
        if (
            set(document) != {"schema", "version", "namespace", "service", "replay", "reviews", "capacities"}
            or document["schema"] != OWNER_SCHEMA or type(document["version"]) is not int
            or document["version"] != 2 or document["namespace"] != self.namespace
            or document["service"] != (self.service_identity.document() if self.service_identity else None)
        ):
            raise ReplayStoreInvalid("review owner storage binding mismatch")
        capacities = document["capacities"]
        if not isinstance(capacities, dict) or set(capacities) != {"replay_bytes", "review_bytes", "receipt_bytes"}:
            raise ReplayStoreInvalid("invalid owner capacity declaration")
        if any(type(value) is not int or value <= 0 for value in capacities.values()) or (
            capacities["replay_bytes"] + capacities["review_bytes"] > MAX_OWNER_BYTES - 4096
            or capacities["receipt_bytes"] < 4096
        ):
            raise ReplayStoreInvalid("invalid owner capacity limits")
        WatchReplay(self.namespace, state=document["replay"], commit=lambda _state: None, clock=self.clock)
        if self._replay_reservation(document["replay"]) > capacities["replay_bytes"]:
            raise ReplayStoreInvalid("replay state exceeds its reserved capacity")
        review_intent.validate_operations(
            document["reviews"], receipt_bytes=capacities["receipt_bytes"], review_bytes=capacities["review_bytes"],
        )

    def adopt_review_operations(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None, replay_bytes: int = 64 * 1024 * 1024,
        review_bytes: int = 64 * 1024 * 1024, receipt_bytes: int = 4096,
    ) -> dict:
        """Explicit layout adoption; existing lifecycle owner excludes incompatible readers."""
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")
        self.check_current(expires_at, checkpoint)
        with self._lock() as lock:
            self.check_current(expires_at, checkpoint)
            lifecycle_checkpoint()
            document = self._read_document()
            if document.get("schema") == OWNER_SCHEMA:
                self._validate_owner(document)
                self.check_current(expires_at, checkpoint)
                return {"status": "already_adopted", "version": 2}
            replay = self._decode_legacy(document)
            WatchReplay(self.namespace, state=replay, commit=lambda _state: None, clock=self.clock)
            adopted = {
                "schema": OWNER_SCHEMA, "version": 2, "namespace": self.namespace,
                "service": self.service_identity.document() if self.service_identity else None,
                "replay": copy.deepcopy(replay), "reviews": [],
                "capacities": {"replay_bytes": replay_bytes, "review_bytes": review_bytes, "receipt_bytes": receipt_bytes},
            }
            self._validate_owner(adopted)
            lock.assert_owned()
            lifecycle_checkpoint()
            self.check_current(expires_at, checkpoint)
            _atomic_write_json(self.path, adopted)
            self.check_current(expires_at, checkpoint)
            return {"status": "adopted", "version": 2}

    def _review_transaction(self, operation, *, expires_at, checkpoint=None, persist=True):
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid request checkpoint")
        self.check_current(expires_at, checkpoint)
        with self._lock() as lock:
            self.check_current(expires_at, checkpoint)
            document = self._read_document()
            if document.get("schema") != OWNER_SCHEMA:
                raise ReplayStoreInvalid("review operations require explicit owner adoption")
            self._validate_owner(document)
            result = operation(document)
            self._validate_owner(document)
            self.check_current(expires_at, checkpoint)
            if persist:
                lock.assert_owned()
                self.check_current(expires_at, checkpoint)
                _atomic_write_json(self.path, document)
            self.check_current(expires_at, checkpoint)
            return copy.deepcopy(result)

    @staticmethod
    def _review_operation(document, operation_id, scope):
        review_intent.text(operation_id, 128)
        scope = _scope(scope)
        operation = next((row for row in document["reviews"] if row["intent"]["operation_id"] == operation_id), None)
        if operation is None or operation["intent"]["scope"] != scope:
            raise ValueError("review operation missing or outside selected scope")
        return operation

    def admit_review(self, selected: dict, *, expires_at: int, checkpoint=None) -> dict:
        """Owner-only data admission; never authorizes or invokes backend effects."""
        selected = review_intent.intent(selected)
        digest = _digest(selected)

        def admit(document):
            previous = next((row for row in document["reviews"]
                             if row["intent"]["operation_id"] == selected["operation_id"]), None)
            if previous is not None:
                if previous["intent_id"] != digest or previous["intent"] != selected:
                    raise ValueError("review operation ID already has a different immutable intent")
                return {"status": "existing", "operation": previous}
            operation = {
                "intent": copy.deepcopy(selected), "intent_id": digest, "created_at": self.clock(),
                "actions": [{"id": action["id"], "state": "unattempted", "attempt_id": None, "receipt": None}
                            for action in selected["actions"]],
            }
            operation["reserved_bytes"] = (
                len(review_intent.encoded(operation))
                + len(selected["actions"]) * (document["capacities"]["receipt_bytes"] + 512) + 1024
            )
            document["reviews"].append(operation)
            return {"status": "admitted", "operation": operation}

        return self._review_transaction(admit, expires_at=expires_at, checkpoint=checkpoint)

    def inspect_review(self, operation_id, scope, *, expires_at: int, checkpoint=None) -> dict:
        return self._review_transaction(
            lambda document: self._review_operation(document, operation_id, scope),
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def begin_review_action(self, operation_id, scope, action_id, *, expires_at: int, checkpoint=None) -> dict:
        """Durably fence one admitted action before an existing protected sender may run."""
        def begin(document):
            operation = self._review_operation(document, operation_id, scope)
            state = next((row for row in operation["actions"] if row["id"] == action_id), None)
            if state is None:
                raise ValueError("selected review action missing")
            if state["state"] != "unattempted":
                return {"status": state["state"], "new_attempt": False, "action": state}
            chosen = next(row for row in operation["intent"]["actions"] if row["id"] == action_id)
            if chosen.get("verdict") == "COMMENTED":
                return {"status": "unsupported_legacy_action", "new_attempt": False, "action": state}
            if any(row["state"] != "confirmed" for row in operation["actions"][:operation["actions"].index(state)]):
                return {"status": "blocked_by_previous", "new_attempt": False, "action": state}
            state.update(state="unknown", attempt_id=str(uuid.uuid4()))
            return {"status": "in_flight", "new_attempt": True, "action": state}

        return self._review_transaction(begin, expires_at=expires_at, checkpoint=checkpoint)

    def record_review_receipt(
        self, operation_id, scope, action_id, attempt_id, value, *, expires_at: int, checkpoint=None,
        confirm: bool = True,
    ) -> dict:
        """Existing sender-only receipt transition; no serving authorization or backend call."""
        value = copy.deepcopy(value)
        if type(confirm) is not bool:
            raise ValueError("receipt confirmation disposition must be boolean")
        def record(document):
            operation = self._review_operation(document, operation_id, scope)
            action = next((row for row in operation["actions"] if row["id"] == action_id), None)
            if (
                action is None or action["state"] == "unattempted"
                or not isinstance(attempt_id, str) or action["attempt_id"] != attempt_id
            ):
                raise ValueError("review receipt does not own the admitted attempt")
            validated = review_intent.receipt(
                value, operation=operation, action=action, limit=document["capacities"]["receipt_bytes"],
            )
            if action["receipt"] is not None:
                known = action["receipt"]["platform_ids"]
                if any(name in validated["platform_ids"] and validated["platform_ids"][name] != identity
                       for name, identity in known.items()):
                    raise ValueError("review receipt conflicts with a retained native identity")
                validated["platform_ids"] = {**known, **validated["platform_ids"]}
                validated = review_intent.receipt(
                    validated, operation=operation, action=action, limit=document["capacities"]["receipt_bytes"],
                )
            if action["state"] == "confirmed":
                if action["receipt"] != validated:
                    raise ValueError("confirmed review receipt is immutable")
                return action
            action["receipt"] = validated
            # No-send evidence is retained conservatively; this data primitive
            # cannot establish retirement of a still-admitted effect owner.
            chosen = next(row for row in operation["intent"]["actions"] if row["id"] == action_id)
            action["state"] = "confirmed" if confirm and review_intent.confirms(
                validated, chosen, operation["intent"]["scope"],
            ) else "unknown"
            return action
        return self._review_transaction(record, expires_at=expires_at, checkpoint=checkpoint)

    def provision(self) -> None:
        """Explicit first creation; never replace existing or corrupt state."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                raise FileExistsError("replay store already exists")
            core = WatchReplay(
                self.namespace, state=None, commit=lambda _state: None, clock=self.clock,
            )
            _atomic_write_json(self.path, self._encode(core.snapshot()))

    def check_current(self, expires_at: int, checkpoint: Callable[[], None] | None) -> None:
        """Check the embedding owner's current authority, including between reads."""
        if type(expires_at) is not int or expires_at <= self.clock():
            raise ReplayCallerExpired("caller context expired")
        if checkpoint is not None:
            checkpoint()

    def _transaction(
        self, operation: Callable[[WatchReplay], _T], *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> _T:
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid request checkpoint")
        self.check_current(expires_at, checkpoint)
        with self._lock() as lock:
            self.check_current(expires_at, checkpoint)

            def commit(state: dict) -> None:
                encoded_state = self._encode(state, owner_state=document)
                lock.assert_owned()
                self.check_current(expires_at, checkpoint)
                _atomic_write_json(self.path, encoded_state)

            if self.path.is_symlink():
                raise ReplayStoreInvalid("linked replay state")
            document = self._read_document()  # Missing state is not first provisioning.
            try:
                core = WatchReplay(
                    self.namespace, state=self._decode(document),
                    commit=commit, clock=self.clock,
                )
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ReplayStoreInvalid("invalid persisted replay state") from None
            self.check_current(expires_at, checkpoint)
            result = operation(core)
            self.check_current(expires_at, checkpoint)
            return result

    def observation_targets(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> tuple[ObservationTarget, ...]:
        return self._transaction(
            lambda core: core.observation_targets(), expires_at=expires_at, checkpoint=checkpoint,
        )

    def observe(
        self, target: ObservationTarget, snapshot: PRSnapshot, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Trusted owner-only input, never a serving operation."""
        return self._transaction(
            lambda core: core.observe(
                target.account, target.repo, target.number, snapshot,
                registration_ids=target.registration_ids,
            ), expires_at=expires_at, checkpoint=checkpoint,
        )

    def waiting_target(
        self, target: ObservationTarget, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> ObservationTarget | None:
        return self._transaction(
            lambda core: core.waiting_target(target),
            expires_at=expires_at, checkpoint=checkpoint,
        )

    def sweep_timeouts(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        return self._transaction(
            lambda core: core.sweep_timeouts(), expires_at=expires_at, checkpoint=checkpoint,
        )

    def execute(
        self, scope: ConsumerScope, operation: str, payload: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        def dispatch(core: WatchReplay) -> dict:
            if operation == "pending":
                swept = core.sweep_timeouts()
                if swept["status"] == "uncertain":
                    result = swept
                else:
                    result = core.pending(scope, **payload)
            elif operation == "register":
                result = core.register(scope, **payload)
            elif operation == "inspect":
                result = core.inspect(scope, **payload)
            elif operation == "cancel":
                result = core.cancel(scope, **payload)
            elif operation == "acknowledge":
                result = core.acknowledge(scope, **payload)
            elif operation == "capabilities":
                from .watch_replay import MAX_BATCH, MAX_BATCH_BYTES, RETENTION_SECONDS

                result = {
                    "protocol": "agent-pull-requests.watch/v1",
                    "operations": [
                        "capabilities", "register", "inspect", "pending", "acknowledge", "cancel",
                    ],
                    "max_batch": MAX_BATCH, "max_batch_bytes": MAX_BATCH_BYTES,
                    "receipt_retention_seconds": RETENTION_SECONDS,
                }
            else:
                raise ValueError("unsupported replay operation")
            return result

        return self._transaction(dispatch, expires_at=expires_at, checkpoint=checkpoint)
