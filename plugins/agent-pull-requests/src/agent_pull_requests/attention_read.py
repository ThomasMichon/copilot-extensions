"""Optional retained attention reader under the existing complete-envelope owner."""

from __future__ import annotations

from collections.abc import Callable
from collections import defaultdict
from dataclasses import asdict, replace

from .attention import Facts, Handled, Interest, PRKey, rank, project
from .attention_state import _scoped
from .review_workspace import _digest
from .review_intent import identifier
from .watch_replay_store import ReplayStoreInvalid
from .workspace_conversation import conversation_key
from .workspace_operations import WorkspaceOperations

PROTOCOL = "agent-pull-requests.attention-read/v1"
HISTORICAL_PROTOCOL = "agent-pull-requests.attention-read/v2"
MAX_BATCH = 16
MAX_BATCH_BYTES = 24 * 1024


class AttentionUnavailable(RuntimeError):
    """Unsupported executing owners or changed page evidence, not empty success."""


def _feedback_index(document: dict) -> tuple[dict, dict, set[PRKey]]:
    captures = defaultdict(list)
    captured_operations = set()
    for value in document["feedback_captures"]["records"]:
        captured_operations.add(value["operation_id"])
        key = PRKey(**value["key"])
        for interest in value["interests"]:
            captures[(key, interest["consumer"], interest["role"])].append(value)
    operations = {value["intent"]["operation_id"]: value for value in document["reviews"]}
    from .terminal_recovery import binding
    operations.update(
        (value["operation_id"], binding(value))
        for value in document.get("terminal_recovery", {}).get("operations", [])
    )
    unbound = {
        conversation_key(value["intent"]["scope"], document["namespace"])
        for operation_id, value in operations.items()
        if operation_id not in captured_operations
        and any(action["state"] in ("unknown", "confirmed") for action in value["actions"])
    }
    return captures, operations, unbound


def _feedback_reason(
    row: dict, index: tuple[dict, dict, set[PRKey]], *, historical: bool = False,
) -> str | None:
    facts, interest, handled = row["facts"], row["interest"], row["handled"]
    retained, operations, unbound = index
    key = PRKey(**facts["key"])
    if key in unbound:
        return "feedback-interest-unbound"
    captures = {
        value["operation_id"]: value for value in retained.get(
            (key, interest["consumer"], interest["role"]), (),
        )
    }
    for capture in captures.values():
        operation = operations[capture["operation_id"]]
        actions = operation["actions"]
        if all(value["state"] in ("unattempted", "retired") for value in actions):
            continue
        if any(value["state"] != "confirmed" for value in actions):
            return "feedback-reconciliation-pending"
        if historical and capture["readback"] is None:
            return "feedback-operation-readback-unavailable"
        if capture["cursor"]["epoch"] != facts["cursor"]["epoch"]:
            continue
        if handled is None or handled["cursor"]["sequence"] < capture["cursor"]["sequence"]:
            return "feedback-tracking-unavailable"
        if historical:
            continue
        proof = row["provenance"]
        if (
            proof["operation_id"] != capture["operation_id"]
            or proof["generation_id"] != capture["generation_id"]
            or handled["candidate"] != capture["candidate"]
            or proof["receipt_refs"] != [
                {"action_id": value["id"], "attempt_id": value["attempt_id"]}
                for value in actions
            ]
        ):
            return "feedback-operation-readback-unavailable"
        if (
            facts["candidate"] == capture["candidate"]
            and handled["cursor"]["sequence"] < facts["cursor"]["sequence"]
        ):
            return "feedback-change-unclassified"
    if handled is not None:
        proof = row["provenance"]
        capture = captures.get(proof["operation_id"])
        operation = operations.get(proof["operation_id"])
        if (
            capture is None or operation is None
            or capture["cursor"]["epoch"] != facts["cursor"]["epoch"]
            or proof["generation_id"] != capture["generation_id"]
            or handled["candidate"] != capture["candidate"]
            or proof["receipt_refs"] != [
                {"action_id": value["id"], "attempt_id": value["attempt_id"]}
                for value in operation["actions"]
            ]
            or any(value["state"] != "confirmed" for value in operation["actions"])
            or historical and capture["readback"] is None
        ):
            return "feedback-provenance-unavailable"
        if historical and (
            facts["candidate"] == handled["candidate"]
            and handled["cursor"]["sequence"] < facts["cursor"]["sequence"]
        ):
            return "feedback-change-unclassified"
    return None


def validate_after(value: dict | None) -> None:
    if value is None:
        return
    if (
        not isinstance(value, dict) or set(value) != {"snapshot", "offset"}
        or type(value["offset"]) is not int or not 1 <= value["offset"] <= 4096
    ):
        raise ValueError("invalid attention page")
    identifier(value["snapshot"])


class AttentionReader:
    """Explicit optional embedding; no adoption, provider IO, ACK or handled writes."""

    def __init__(
        self, workspace: WorkspaceOperations, *, consumer: str,
        max_age_seconds: int, aging_seconds: int, historical: bool = False,
    ) -> None:
        Interest("maintainer", None, consumer)
        if (
            not isinstance(workspace, WorkspaceOperations)
            or not callable(workspace.attention_owner)
            or not callable(workspace.feedback_owner)
            or not workspace.write_available
            or not workspace.conversations
            or type(historical) is not bool
            or any(type(value) is not int or not 1 <= value <= 86400
                   for value in (max_age_seconds, aging_seconds))
        ):
            raise ValueError("actual acquisition and protected feedback owners required")
        self.workspace, self.store = workspace, workspace.store
        self.owner_version = 6 if historical else 5
        self.protocol = HISTORICAL_PROTOCOL if historical else PROTOCOL
        self.consumer = consumer
        self.max_age_seconds, self.aging_seconds = max_age_seconds, aging_seconds
        self._scope = conversation_key({**workspace.scope, "number": 1}, self.store.namespace)

    def _transaction(
        self, operation: Callable[[dict], dict], *, expires_at: int,
        checkpoint: Callable[[], None],
    ) -> dict:
        def check() -> None:
            checkpoint()
            self.workspace.checkpoint()
            if (
                self.store is not self.workspace.store
                or conversation_key({**self.workspace.scope, "number": 1}, self.store.namespace) != self._scope
                or not all(callable(value) for value in (
                self.workspace.attention_owner, self.workspace.feedback_owner,
                )) or not self.workspace.write_available or not self.workspace.conversations
            ):
                raise AttentionUnavailable("attention owners unavailable")

        def read(document: dict) -> dict:
            if document["version"] != self.owner_version:
                raise ReplayStoreInvalid(f"attention reader requires adopted owner v{self.owner_version}")
            check()
            result = operation(document)
            check()
            return result

        return self.store._review_transaction(
            read, expires_at=expires_at, checkpoint=check, persist=False,
        )

    def execute(
        self, operation: str, payload: dict, *, expires_at: int,
        checkpoint: Callable[[], None],
    ) -> dict:
        if operation == "capabilities" and payload == {}:
            return self._transaction(lambda _document: {
                "protocol": self.protocol, "operations": ["capabilities", "read"],
                "owner_store_versions": [self.owner_version], "max_batch": MAX_BATCH,
                "max_batch_bytes": MAX_BATCH_BYTES,
                "max_age_seconds": self.max_age_seconds, "aging_seconds": self.aging_seconds,
                "effects": "none", "adopts": False, "acknowledges": False,
                "feedback": (
                    "retained_original_readback" if self.owner_version == 6 else "retained_original_capture"
                ),
            }, expires_at=expires_at, checkpoint=checkpoint)
        if (
            operation != "read" or not isinstance(payload, dict)
            or set(payload) != {"limit", "after"}
            or type(payload["limit"]) is not int or not 1 <= payload["limit"] <= MAX_BATCH
        ):
            raise ValueError("invalid attention read fields")
        after = payload["after"]
        validate_after(after)

        def read(document: dict) -> dict:
            now = int(self.store.clock())
            feedback = _feedback_index(document)
            scope = asdict(self._scope)
            items = []
            for row in document["attention"]["records"]:
                key = row["facts"]["key"]
                if (
                    row["interest"]["consumer"] != self.consumer
                    or any(key[name] != value for name, value in scope.items()
                           if name != "number")
                ):
                    continue
                facts = Facts(**_scoped(row["facts"]))
                interest = Interest(**row["interest"])
                handled = Handled(**_scoped(row["handled"])) if row["handled"] else None
                item = project(facts, interest, handled, now=now, max_age_seconds=self.max_age_seconds)
                reason = _feedback_reason(row, feedback, historical=self.owner_version == 6)
                if reason is not None and item.status not in ("excluded", "terminal"):
                    item = replace(item, status="unavailable", reasons=(reason,), priority=0)
                items.append(item)
            ordered = list(rank(items, now=now, aging_seconds=self.aging_seconds))
            ordered += sorted(
                (item for item in items if item.status != "actionable"),
                key=lambda item: (item.key.number, item.role),
            )
            projection = [asdict(item) for item in ordered]
            snapshot = _digest(
                {"protocol": self.protocol, "items": projection} if self.owner_version == 6 else projection
            )
            if after is not None and after["snapshot"] != snapshot:
                raise AttentionUnavailable("attention page changed; restart the read")
            offset = after["offset"] if after is not None else 0
            if offset > len(ordered):
                raise ValueError("attention page beyond snapshot")
            end = min(len(ordered), offset + payload["limit"])
            return {
                "protocol": self.protocol, "items": projection[offset:end],
                "snapshot": snapshot,
                "next": {"snapshot": snapshot, "offset": end} if end < len(ordered) else None,
                "effects": "none",
            }

        return self._transaction(read, expires_at=expires_at, checkpoint=checkpoint)
