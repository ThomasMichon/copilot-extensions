"""Body-free original-operation recovery under the existing replay-file owner."""

from __future__ import annotations

import copy
import math
from dataclasses import asdict
from collections.abc import Callable

from . import review_intent
from .attention import Cursor
from .review_workspace import _digest, _scope
from .terminal_retention import TerminalRetentionStore
from . import review_comment_mapping

PROTOCOL = "agent-pull-requests.terminal-recovery/v1"
MAX_RECORDS = review_intent.MAX_OPERATIONS


def policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"owner", "deadline_at", "escalation_ref"}:
        raise ValueError("original bounded recovery owner/deadline/escalation required")
    for name in ("owner", "escalation_ref"):
        review_intent.text(value[name])
    if type(value["deadline_at"]) not in (int, float) or not math.isfinite(value["deadline_at"]):
        raise ValueError("finite original recovery deadline required")
    return copy.deepcopy(value)


def binding(record: dict) -> dict:
    """Only for original receipt/capture validation, never executable intent."""
    return {
        "intent": {
            name: copy.deepcopy(record[name]) for name in
            ("operation_id", "scope", "generation_id", "source_id", "revision", "actions")
        },
        "intent_id": record["intent_id"],
        "actions": record["actions"],
        **({"selection": record["selection"]} if record["selection"] is not None else {}),
    }


def _reservation(record: dict, receipt_bytes: int) -> int:
    baseline = copy.deepcopy(record)
    baseline["reserved_bytes"] = 0
    for action in baseline["actions"]:
        action["receipt"] = None
        if action["state"] == "unknown":
            action["state"] = "confirmed"
        if "comment_observation" in action:
            action["comment_observation"] = None
    slots = sum("comment_observation" in action for action in record["actions"])
    return (
        len(review_intent.encoded(baseline)) + len(record["actions"]) * (receipt_bytes + 512)
        + slots * review_comment_mapping.MAX_SLOT_BYTES + 1024
    )


def validate(document: dict) -> int:
    """Strict optional reader extension; reserve recovery growth with active reviews."""
    state = document["terminal_recovery"]
    if (
        type(document["terminal_recovery_version"]) is not int
        or document["terminal_recovery_version"] != 1
        or not isinstance(state, dict) or set(state) != {"protocol", "operations"}
        or state["protocol"] != PROTOCOL or not isinstance(state["operations"], list)
        or len(state["operations"]) > MAX_RECORDS
    ):
        raise ValueError("invalid terminal recovery contract")
    identities = {operation["intent"]["operation_id"] for operation in document["reviews"]}
    writes = {
        action["preparation"]["identity"]["write_id"]
        for operation in document["reviews"] for action in operation["intent"]["actions"]
        if "preparation" in action
    }
    for record in state["operations"]:
        if not isinstance(record, dict) or set(record) != {
            "protocol", "operation_id", "scope", "intent_id", "generation_id", "source_id",
            "revision", "created_at", "selection", "terminal_cursor", "recovery",
            "actions", "reserved_bytes",
        } or record["protocol"] != PROTOCOL:
            raise ValueError("invalid compact review operation")
        identity = review_intent.text(record["operation_id"], 128)
        if identity in identities or _scope(record["scope"]) != record["scope"]:
            raise ValueError("duplicate or noncanonical compact review operation")
        identities.add(identity)
        for name in ("intent_id", "generation_id", "source_id"):
            review_intent.identifier(record[name])
        review_intent.revision(record["revision"])
        policy(record["recovery"])
        if type(record["created_at"]) not in (int, float) or not math.isfinite(record["created_at"]):
            raise ValueError("invalid compact creation time")
        cursor = Cursor(**record["terminal_cursor"])
        if cursor.sequence < 1 or not any(
            row["scope"] == record["scope"] and row["cursor"] == asdict(cursor)
            and identity in row["operations"] and row["drain"] == "drained"
            for row in document["terminal_retention"]["records"]
        ):
            raise ValueError("compact operation lost its original terminal custody")
        selection = record["selection"]
        if selection is not None:
            if not isinstance(selection, dict) or set(selection) != {"input_id", "stage"}:
                raise ValueError("invalid compact selection identity")
            review_intent.identifier(selection["input_id"])
            if selection["stage"] not in ("prepared", "admitted"):
                raise ValueError("invalid compact selection stage")
        actions = record["actions"]
        if not isinstance(actions, list) or not 1 <= len(actions) <= review_intent.MAX_ACTIONS:
            raise ValueError("invalid compact action count")
        seen = set()
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("invalid compact action")
            fields = {
                "comment": {"body_id"}, "reply": {"body_id", "thread_id"},
                "edit_owned_comment": {"body_id", "thread_id", "comment_id", "expected_content_id"},
                "verdict": {"verdict"},
            }
            kind = action.get("kind")
            if not isinstance(kind, str) or kind not in fields:
                raise ValueError("invalid compact action kind")
            expected = {"id", "kind", "state", "attempt_id", "receipt"} | fields[kind]
            if "prepared" in action and kind != "verdict":
                expected.add("prepared")
                prepared = action["prepared"]
                if not isinstance(prepared, dict) or set(prepared) != {"comment_id", "write_id", "body_id"}:
                    raise ValueError("invalid compact prepared identity")
                from .review_preparation import _guid
                _guid(prepared["comment_id"])
                write = _guid(prepared["write_id"])
                review_intent.identifier(prepared["body_id"])
                if write == prepared["comment_id"] or write in writes:
                    raise ValueError("duplicate compact prepared write")
                writes.add(write)
            if "comment_observation" in action:
                if (
                    document.get("terminal_comment_mapping_version") != 1
                    or "prepared" not in action
                ):
                    raise ValueError("compact mapping requires explicit reader adoption and preparation")
                expected.add("comment_observation")
                review_comment_mapping.validate_slot(
                    action["comment_observation"], action_state=action["state"],
                )
            if set(action) != expected:
                raise ValueError("compact action contains unsupported data")
            action_id = review_intent.text(action["id"], 128)
            if action_id in seen:
                raise ValueError("duplicate compact action")
            seen.add(action_id)
            for name in ("body_id", "expected_content_id"):
                if name in action:
                    review_intent.identifier(action[name])
            for name in ("thread_id", "comment_id"):
                if name in action and (type(action[name]) is not int or action[name] <= 0):
                    raise ValueError("invalid compact native identity")
            if kind == "verdict" and action["verdict"] not in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                raise ValueError("invalid compact verdict")
            if action["state"] == "retired":
                if action["attempt_id"] is not None or action["receipt"] is not None:
                    raise ValueError("unattempted terminal action acquired an effect")
            elif action["state"] in ("unknown", "confirmed"):
                review_intent.text(action["attempt_id"], 128)
                if selection is not None and selection["stage"] == "prepared":
                    raise ValueError("pre-approval terminal selection acquired an attempt")
                if action["receipt"] is not None:
                    review_intent.receipt(
                        action["receipt"], operation=binding(record), action=action,
                        limit=document["capacities"]["receipt_bytes"],
                    )
                if action["state"] == "confirmed" and (
                    action["receipt"] is None
                    or not review_intent.confirms(action["receipt"], action, record["scope"])
                ):
                    raise ValueError("compact confirmation lost its original receipt")
            else:
                raise ValueError("invalid compact action outcome")
        reserved = record["reserved_bytes"]
        if (
            type(reserved) is not int or reserved < _reservation(record, document["capacities"]["receipt_bytes"])
            or len(review_intent.encoded(record)) > reserved
        ):
            raise ValueError("compact recovery lost its bounded receipt reservation")
    return sum(record["reserved_bytes"] for record in state["operations"])


def compact(document: dict, scope: dict, cursor: Cursor, policies: dict) -> dict:
    """Called only while the workspace's actual drain/lifecycle exclusion is held."""
    row = TerminalRetentionStore.selected(document, scope, cursor)
    if row["drain"] != "drained" or "terminal_recovery" not in document:
        raise ValueError("adopted recovery reader and actual completed drain required")
    if not isinstance(policies, dict) or set(policies) != set(row["operations"]):
        raise ValueError("original recovery policy required for every captured operation")
    retained = document["terminal_recovery"]["operations"]
    originals = {operation["intent"]["operation_id"]: operation for operation in document["reviews"]}
    for identity in row["operations"]:
        recovery = policy(policies[identity])
        previous = next((record for record in retained if record["operation_id"] == identity), None)
        if previous is not None:
            if previous["scope"] != scope or previous["terminal_cursor"] != asdict(cursor) or previous["recovery"] != recovery:
                raise ValueError("compact retry changed original recovery custody")
            continue
        operation = originals.get(identity)
        if operation is None or operation["intent"]["scope"] != scope:
            raise ValueError("original terminal operation unavailable")
        selected = operation["intent"]
        if review_comment_mapping.FIELD in operation and document.get("terminal_comment_mapping_version") != 1:
            raise ValueError("lossless terminal mapping adoption required before compaction")
        record = {
            "protocol": PROTOCOL,
            **{name: copy.deepcopy(selected[name]) for name in (
                "operation_id", "scope", "generation_id", "source_id", "revision",
            )},
            "intent_id": operation["intent_id"], "created_at": operation["created_at"],
            "selection": copy.deepcopy(operation.get("selection")),
            "terminal_cursor": asdict(cursor), "recovery": recovery, "actions": [], "reserved_bytes": 0,
        }
        for chosen, state in zip(selected["actions"], operation["actions"]):
            action = {
                **copy.deepcopy(state), "kind": chosen["kind"],
                **{name: chosen[name] for name in ("thread_id", "comment_id", "expected_content_id", "verdict")
                   if name in chosen},
            }
            if action["state"] == "unattempted":
                action["state"] = "retired"
            if "body" in chosen:
                action["body_id"] = _digest(chosen["body"])
            if "preparation" in chosen:
                prepared = chosen["preparation"]
                action["prepared"] = {
                    name: prepared["identity"][name] for name in ("comment_id", "write_id")
                }
                action["prepared"]["body_id"] = prepared["body_id"]
                if chosen["id"] in operation.get(review_comment_mapping.FIELD, {}):
                    action["comment_observation"] = copy.deepcopy(
                        operation[review_comment_mapping.FIELD][chosen["id"]],
                    )
            record["actions"].append(action)
        record["reserved_bytes"] = _reservation(record, document["capacities"]["receipt_bytes"])
        retained.append(record)
    document["reviews"] = [
        operation for operation in document["reviews"] if operation["intent"]["operation_id"] not in policies
    ]
    return {"status": "compacted", "scope": scope, "cursor": asdict(cursor), "operations": list(row["operations"])}


class TerminalRecoveryStore:
    """Explicit internal reader; compact records cannot allocate or send an action."""

    def __init__(self, owner) -> None:
        from .watch_replay_store import ReplayStore
        if not isinstance(owner, ReplayStore) or not owner.terminal_recovery:
            raise ValueError("explicit terminal recovery reader required")
        self.owner = owner

    def adopt(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        if not callable(lifecycle_checkpoint) or checkpoint is not None and not callable(checkpoint):
            raise ValueError("actual exclusive reader lifecycle checkpoint required")

        def current() -> None:
            if checkpoint is not None:
                checkpoint()
            lifecycle_checkpoint()

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if "terminal_retention" not in document:
                raise ValueError("terminal intent adoption required before recovery")
            status = "already_adopted" if "terminal_recovery" in document else "adopted"
            if status == "adopted":
                document["terminal_recovery_version"] = 1
                document["terminal_recovery"] = {"protocol": PROTOCOL, "operations": []}
            lifecycle_checkpoint()
            return {"status": status, "protocol": PROTOCOL}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=current)

    @staticmethod
    def selected(document: dict, scope: dict, operation_id: str) -> dict:
        review_intent.text(operation_id, 128)
        state = document.get("terminal_recovery")
        record = next(
            (row for row in state["operations"] if row["operation_id"] == operation_id), None,
        ) if state is not None else None
        if record is None or record["scope"] != _scope(scope):
            raise ValueError("compact recovery missing or outside selected scope")
        return record

    def inspect(
        self, scope: dict, operation_id: str, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        return self.owner._review_transaction(
            lambda document: self.selected(document, scope, operation_id),
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def record(
        self, original: dict, action_id: str, receipt: dict, *, expires_at: int,
        checkpoint: Callable[[], None],
    ) -> dict:
        """Only attributable native readback may resolve an original unknown attempt."""
        original, receipt = copy.deepcopy(original), copy.deepcopy(receipt)

        def current() -> None:
            checkpoint()
            if self.owner.clock() >= original["recovery"]["deadline_at"]:
                raise TimeoutError("original recovery deadline requires escalation")

        def record(document: dict) -> dict:
            current = self.selected(document, original["scope"], original["operation_id"])
            if any(current[name] != original[name] for name in current if name != "actions"):
                raise ValueError("recovery readback changed original custody")
            if self.owner.clock() >= current["recovery"]["deadline_at"]:
                raise TimeoutError("original recovery deadline requires escalation")
            state = next((action for action in current["actions"] if action["id"] == action_id), None)
            before = next((action for action in original["actions"] if action["id"] == action_id), None)
            if state is None or before is None or state != before or state["state"] != "unknown":
                raise ValueError("recovery readback no longer owns the original unknown attempt")
            if receipt.get("origin") != "attributable_readback":
                raise ValueError("terminal recovery requires attributable native readback")
            validated = review_intent.receipt(
                receipt, operation=binding(current), action=state, limit=document["capacities"]["receipt_bytes"],
            )
            known = (state["receipt"] or {}).get("platform_ids", {})
            if any(name in validated["platform_ids"] and validated["platform_ids"][name] != value
                   for name, value in known.items()):
                raise ValueError("recovery readback conflicts with original native identity")
            validated["platform_ids"] = {**known, **validated["platform_ids"]}
            validated = review_intent.receipt(
                validated, operation=binding(current), action=state, limit=document["capacities"]["receipt_bytes"],
            )
            if not review_intent.confirms(validated, state, current["scope"]):
                raise ValueError("recovery readback does not confirm the selected native target")
            state.update(state="confirmed", receipt=validated)
            if "comment_observation" in state:
                state["comment_observation"] = None
            return state

        return self.owner._review_transaction(record, expires_at=expires_at, checkpoint=current)
