"""Disk-backed replay transactions, separate from the native callback owner."""

from __future__ import annotations

import copy
import json
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
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
        terminal_retention: bool = False, terminal_recovery: bool = False,
        terminal_cache_retirement: bool = False,
        terminal_comment_mapping: bool = False,
        repository_cache: bool = False,
    ) -> None:
        if type(repository_cache) is not bool:
            raise ValueError("explicit repository cache selection required")
        self.repository_cache = repository_cache
        if type(terminal_retention) is not bool:
            raise ValueError("explicit terminal reader selection required")
        self.terminal_retention = terminal_retention
        if type(terminal_recovery) is not bool or terminal_recovery and not terminal_retention:
            raise ValueError("recovery reader requires explicit terminal reader selection")
        self.terminal_recovery = terminal_recovery
        if type(terminal_cache_retirement) is not bool or terminal_cache_retirement and not terminal_recovery:
            raise ValueError("cache retirement reader requires explicit recovery reader selection")
        self.terminal_cache_retirement = terminal_cache_retirement
        if type(terminal_comment_mapping) is not bool or terminal_comment_mapping and not terminal_recovery:
            raise ValueError("terminal mapping reader requires explicit recovery reader selection")
        self.terminal_comment_mapping = terminal_comment_mapping
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
        version = document.get("version")
        names = {"schema", "version", "namespace", "service", "replay", "reviews", "capacities"}
        cache = "repository_cache_version" in document
        if cache:
            from .repository_state import validate as validate_cache
            if not self.repository_cache or type(document["repository_cache_version"]) is not int or (
                document["repository_cache_version"] != 1
            ):
                raise ReplayStoreInvalid("repository cache reader unavailable for adopted owner")
            names.update(("repository_cache_version", "repository_cache"))
            validate_cache(document.get("repository_cache"), self.namespace)
        elif self.repository_cache:
            raise ReplayStoreInvalid("repository cache requires explicit fresh provisioning")
        prepared = "review_intent_version" in document
        selections = "review_selection_version" in document
        mappings = "review_comment_mapping_version" in document
        terminal = "terminal_retention_version" in document
        recovery = "terminal_recovery_version" in document
        retirement = "terminal_cache_retirement_version" in document
        if "terminal_comment_mapping_version" in document:
            if (
                not self.terminal_comment_mapping or not recovery or not mappings
                or type(document["terminal_comment_mapping_version"]) is not int
                or document["terminal_comment_mapping_version"] != 1
            ):
                raise ReplayStoreInvalid("terminal comment mapping reader unavailable for adopted owner")
            names.add("terminal_comment_mapping_version")
        if retirement:
            if not self.terminal_cache_retirement or not recovery:
                raise ReplayStoreInvalid("terminal cache retirement reader unavailable for adopted owner")
            names.update(("terminal_cache_retirement_version", "terminal_cache_retirement"))
        if recovery:
            if not self.terminal_recovery or not terminal:
                raise ReplayStoreInvalid("terminal recovery reader unavailable for adopted owner")
            names.update(("terminal_recovery_version", "terminal_recovery"))
        if terminal:
            if not self.terminal_retention or version not in (3, 4, 5, 6):
                raise ReplayStoreInvalid("terminal retention reader unavailable for adopted owner")
            names.update(("terminal_retention_version", "terminal_retention"))
        if mappings:
            names.add("review_comment_mapping_version")
            if (
                type(document["review_comment_mapping_version"]) is not int
                or document["review_comment_mapping_version"] != 1
                or not prepared or version not in (3, 4, 5, 6)
            ):
                raise ReplayStoreInvalid("unsupported acquired comment mapping storage contract")
        if selections:
            names.add("review_selection_version")
            if (
                type(document["review_selection_version"]) is not int
                or document["review_selection_version"] != 1 or not prepared
            ):
                raise ReplayStoreInvalid("unsupported pre-approval selection storage contract")
        if prepared:
            names.add("review_intent_version")
            if type(document["review_intent_version"]) is not int or document["review_intent_version"] != 2:
                raise ReplayStoreInvalid("unsupported prepared review storage contract")
        if version in (3, 4, 5, 6):
            names.add("conversations")
        if version in (4, 5, 6):
            names.add("attention")
        if version in (5, 6):
            names.add("feedback_captures")
        if (
            set(document) != names
            or document["schema"] != OWNER_SCHEMA or type(document["version"]) is not int
            or version not in (2, 3, 4, 5, 6) or document["namespace"] != self.namespace
            or document["service"] != (self.service_identity.document() if self.service_identity else None)
        ):
            raise ReplayStoreInvalid("review owner storage binding mismatch")
        capacities = document["capacities"]
        capacity_names = {"replay_bytes", "review_bytes", "receipt_bytes"}
        if terminal:
            capacity_names.add("terminal_retention_bytes")
        if version in (3, 4, 5, 6):
            capacity_names.add("conversation_bytes")
        if version in (4, 5, 6):
            capacity_names.add("attention_bytes")
        if version in (5, 6):
            capacity_names.add("feedback_capture_bytes")
        if version == 6:
            capacity_names.add("feedback_readback_bytes")
        if not isinstance(capacities, dict) or set(capacities) != capacity_names:
            raise ReplayStoreInvalid("invalid owner capacity declaration")
        if any(type(value) is not int or value <= 0 for value in capacities.values()) or (
            capacities["replay_bytes"] + capacities["review_bytes"]
            + capacities.get("conversation_bytes", 0)
            + capacities.get("attention_bytes", 0)
            + capacities.get("feedback_capture_bytes", 0)
            + capacities.get("feedback_readback_bytes", 0)
            + (16 * 1024 * 1024 if cache else 0)
            + capacities.get("terminal_retention_bytes", 0) > MAX_OWNER_BYTES - 4096
            or capacities["receipt_bytes"] < 4096
        ):
            raise ReplayStoreInvalid("invalid owner capacity limits")
        if terminal:
            from .terminal_retention import validate
            try:
                validate(document, self.namespace)
            except (KeyError, TypeError, ValueError):
                raise ReplayStoreInvalid("invalid persisted terminal retention") from None
        if retirement:
            from .terminal_cache_retirement import validate as validate_retirement
            try:
                validate_retirement(document)
            except (KeyError, TypeError, ValueError):
                raise ReplayStoreInvalid("invalid persisted terminal cache retirement") from None
        WatchReplay(self.namespace, state=document["replay"], commit=lambda _state: None, clock=self.clock)
        if self._replay_reservation(document["replay"]) > capacities["replay_bytes"]:
            raise ReplayStoreInvalid("replay state exceeds its reserved capacity")
        from .terminal_recovery import binding, validate as validate_recovery
        recovery_bytes = validate_recovery(document) if recovery else 0
        review_intent.validate_operations(
            document["reviews"], receipt_bytes=capacities["receipt_bytes"],
            review_bytes=capacities["review_bytes"] - recovery_bytes,
            versions=(1, 2) if prepared else (1,),
            selections=selections,
            mappings=mappings,
        )
        if version in (3, 4, 5, 6):
            from .conversation_journal import ConversationJournal
            if not isinstance(document["conversations"], dict):
                raise ReplayStoreInvalid("persisted conversation journal must be an object")
            try:
                journal = ConversationJournal(document["conversations"])
            except ValueError:
                raise ReplayStoreInvalid("invalid persisted conversation journal") from None
            if (
                any(row["current"]["key"]["namespace"] != self.namespace for row in journal.records)
                or len(review_intent.encoded(document["conversations"])) > capacities["conversation_bytes"]
            ):
                raise ReplayStoreInvalid("conversation journal exceeds its namespace or capacity")
        if version in (4, 5, 6):
            from .attention_state import AttentionState
            if not isinstance(document["attention"], dict):
                raise ReplayStoreInvalid("persisted attention state must be an object")
            try:
                attention = AttentionState(document["attention"])
            except ValueError:
                raise ReplayStoreInvalid("invalid persisted attention state") from None
            if (
                any(row["facts"]["key"]["namespace"] != self.namespace for row in attention.records)
                or len(review_intent.encoded(document["attention"])) > capacities["attention_bytes"]
            ):
                raise ReplayStoreInvalid("attention state exceeds its namespace or capacity")
        if version in (5, 6):
            from .feedback_capture import FeedbackCaptures, PROTOCOL, READBACK_PROTOCOL
            if not isinstance(document["feedback_captures"], dict):
                raise ReplayStoreInvalid("persisted feedback captures must be an object")
            try:
                protocol = READBACK_PROTOCOL if version == 6 else PROTOCOL
                captures = FeedbackCaptures(document["feedback_captures"], protocol=protocol)
                recovered = [binding(row) for row in document["terminal_recovery"]["operations"]] if recovery else []
                captures.validate_bindings(document["reviews"] + recovered, self.namespace)
            except ValueError:
                raise ReplayStoreInvalid("invalid persisted feedback captures") from None
            if captures.capture_bytes() > capacities["feedback_capture_bytes"]:
                raise ReplayStoreInvalid("feedback captures exceed their reserved capacity")
            if version == 6 and captures.readback_reservation() > capacities["feedback_readback_bytes"]:
                raise ReplayStoreInvalid("feedback readback exceeds its reserved capacity")

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
                return {"status": "already_adopted", "version": document["version"]}
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

    def _review_transaction(self, operation, *, expires_at, checkpoint=None, persist=True, staged=False):
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid request checkpoint")
        self.check_current(expires_at, checkpoint)
        with self._lock() as lock:
            self.check_current(expires_at, checkpoint)
            document = self._read_document()
            if document.get("schema") != OWNER_SCHEMA:
                raise ReplayStoreInvalid("review operations require explicit owner adoption")
            self._validate_owner(document)

            def commit():
                self._validate_owner(document)
                lock.assert_owned()
                self.check_current(expires_at, checkpoint)
                _atomic_write_json(self.path, document)
                self.check_current(expires_at, checkpoint)

            # A bounded cache mutation may require a durable owner reservation
            # before its separate pointer write, without releasing replay custody.
            result = operation(document, commit) if staged else operation(document)
            self._validate_owner(document)
            self.check_current(expires_at, checkpoint)
            if persist:
                commit()
            self.check_current(expires_at, checkpoint)
            return copy.deepcopy(result)

    def adopt_prepared_reviews(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Explicit orthogonal extension; the existing owner excludes old strict readers."""
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            status = "already_adopted" if "review_intent_version" in document else "adopted"
            document["review_intent_version"] = 2
            self._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": status, "review_intent_versions": [1, 2]}

        return self._review_transaction(adopt, expires_at=expires_at, checkpoint=checkpoint)

    @staticmethod
    def _review_operation(document, operation_id, scope):
        review_intent.text(operation_id, 128)
        scope = _scope(scope)
        operation = next((row for row in document["reviews"] if row["intent"]["operation_id"] == operation_id), None)
        if operation is None or operation["intent"]["scope"] != scope:
            raise ValueError("review operation missing or outside selected scope")
        if operation.get("selection", {}).get("stage") == "prepared":
            raise ValueError("pre-approval selection is not execution-admitted")
        return operation

    def admit_review(self, selected: dict, *, expires_at: int, checkpoint=None) -> dict:
        """Owner-only data admission; never authorizes or invokes backend effects."""
        selected = review_intent.intent(selected, versions=(1, 2))
        digest = _digest(selected)

        def admit(document):
            from .terminal_retention import require_live
            require_live(
                document, selected["scope"], operation_id=selected["operation_id"],
                generation_id=selected["generation_id"],
            )
            if selected["version"] == 2 and document.get("review_intent_version") != 2:
                raise ReplayStoreInvalid("prepared review requires explicit storage adoption")
            previous = next((row for row in document["reviews"]
                             if row["intent"]["operation_id"] == selected["operation_id"]), None)
            if previous is not None:
                if previous["intent_id"] != digest or previous["intent"] != selected:
                    raise ValueError("review operation ID already has a different immutable intent")
                if previous.get("selection", {}).get("stage") == "prepared":
                    previous["selection"]["stage"] = "admitted"
                    return {"status": "admitted", "operation": previous}
                return {"status": "existing", "operation": previous}
            operation = review_intent.new_operation(
                selected, created_at=self.clock(), receipt_bytes=document["capacities"]["receipt_bytes"],
                mappings=document.get("review_comment_mapping_version") == 1,
            )
            document["reviews"].append(operation)
            return {"status": "admitted", "operation": operation}

        return self._review_transaction(admit, expires_at=expires_at, checkpoint=checkpoint)

    def inspect_review(self, operation_id, scope, *, expires_at: int, checkpoint=None) -> dict:
        def inspect(document):
            from .terminal_retention import require_live
            require_live(document, scope, operation_id=operation_id)
            return self._review_operation(document, operation_id, scope)

        return self._review_transaction(
            inspect,
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def review_intent_versions(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> tuple[int, ...]:
        """Read the actual adopted contract without provisioning or granting write authority."""
        return self._review_transaction(
            lambda document: (1, 2) if document.get("review_intent_version") == 2 else (1,),
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def prepared_review_receipt(
        self, operation_id: str, scope: dict, action_id: str, *,
        expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Read original intent/receipt correlation, never tag discovery or fresh native proof."""
        review_intent.text(action_id, 128)

        def project(document: dict) -> dict:
            if document.get("review_intent_version") != 2:
                raise ReplayStoreInvalid("prepared receipt requires explicit storage adoption")
            operation = self._review_operation(document, operation_id, scope)
            selected = next(
                (action for action in operation["intent"]["actions"] if action["id"] == action_id), None,
            )
            if selected is None:
                raise ValueError("selected review action missing")
            result = {
                "protocol": "agent-pull-requests.prepared-comment-receipt/v1",
                "operation_id": operation_id, "action_id": action_id,
                "scope": copy.deepcopy(operation["intent"]["scope"]),
                "intent_id": operation["intent_id"], "effects": "none",
            }
            if operation["intent"]["version"] != 2 or "preparation" not in selected:
                return {**result, "status": "unavailable", "reason": "original_preparation_missing"}
            state = next(action for action in operation["actions"] if action["id"] == action_id)
            return {
                **result, "status": "retained",
                "identity": copy.deepcopy(selected["preparation"]["identity"]),
                "body_id": selected["preparation"]["body_id"],
                "action_state": state["state"], "receipt": copy.deepcopy(state["receipt"]),
                "basis": "original_intent_and_retained_receipt", "fresh_native_read": False,
            }

        return self._review_transaction(
            project, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def begin_review_action(self, operation_id, scope, action_id, *, expires_at: int, checkpoint=None) -> dict:
        """Durably fence one admitted action before an existing protected sender may run."""
        def begin(document):
            operation = self._review_operation(document, operation_id, scope)
            from .terminal_retention import require_live
            require_live(
                document, scope, operation_id=operation_id,
                generation_id=operation["intent"]["generation_id"],
            )
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
            from .review_comment_mapping import invalidate
            invalidate(operation, action_id)
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
            from .review_comment_mapping import invalidate
            invalidate(operation, action_id)
            return action
        return self._review_transaction(record, expires_at=expires_at, checkpoint=checkpoint)

    def provision(self, *, conversations: bool = False) -> None:
        """Explicit first creation; never replace existing or corrupt state."""
        if type(conversations) is not bool:
            raise ValueError("explicit fresh conversation layout selection required")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                raise FileExistsError("replay store already exists")
            core = WatchReplay(
                self.namespace, state=None, commit=lambda _state: None, clock=self.clock,
            )
            document = self._encode(core.snapshot())
            if conversations or self.repository_cache:
                from .conversation_journal import ConversationJournal
                document = {
                    "schema": OWNER_SCHEMA, "version": 3, "namespace": self.namespace,
                    "service": self.service_identity.document() if self.service_identity else None,
                    "replay": core.snapshot(), "reviews": [],
                    "conversations": ConversationJournal().snapshot(),
                    "capacities": {
                        "replay_bytes": 64 * 1024 * 1024,
                        "review_bytes": 64 * 1024 * 1024, "receipt_bytes": 4096,
                        "conversation_bytes": 8 * 1024 * 1024,
                    },
                }
                if self.repository_cache:
                    from .repository_state import empty
                    document.update(repository_cache_version=1, repository_cache=empty())
                self._validate_owner(document)
            _atomic_write_json(self.path, document)

    def check_current(self, expires_at: int, checkpoint: Callable[[], None] | None) -> None:
        """Check the embedding owner's current authority, including between reads."""
        if type(expires_at) is not int or expires_at <= self.clock():
            raise ReplayCallerExpired("caller context expired")
        if checkpoint is not None:
            checkpoint()

    def _transaction(
        self, operation: Callable[[WatchReplay], _T], *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        prepare: Callable[[dict], None] | None = None,
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
            if prepare is not None:
                # This callback only selects data from the same complete owner
                # envelope; core commits still use that envelope and custody.
                self._decode(document)
                prepare(document)
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
        document = None

        def prepare(current: dict) -> None:
            nonlocal document
            document = current

        def targets(core: WatchReplay) -> tuple[ObservationTarget, ...]:
            from .terminal_retention import replay_epoch
            return tuple(
                replace(target, retention_epoch=replay_epoch(document, target))
                for target in core.observation_targets()
            )

        return self._transaction(
            targets if self.terminal_retention else lambda core: core.observation_targets(),
            expires_at=expires_at, checkpoint=checkpoint,
            prepare=prepare if self.terminal_retention else None,
        )

    def observe(
        self, target: ObservationTarget, snapshot: PRSnapshot, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Trusted owner-only input, never a serving operation."""
        def prepare(document: dict) -> None:
            nonlocal snapshot
            from .terminal_retention import replay_observation
            snapshot = replay_observation(document, target, snapshot)

        return self._transaction(
            lambda core: core.observe(
                target.account, target.repo, target.number, snapshot,
                registration_ids=target.registration_ids,
            ) if snapshot is not None else {"status": "stale_observation"},
            expires_at=expires_at, checkpoint=checkpoint,
            prepare=prepare if self.terminal_retention else None,
        )

    def waiting_target(
        self, target: ObservationTarget, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> ObservationTarget | None:
        return self._transaction(
            lambda core: (
                replace(current, retention_epoch=target.retention_epoch)
                if (current := core.waiting_target(target)) is not None else None
            ),
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
