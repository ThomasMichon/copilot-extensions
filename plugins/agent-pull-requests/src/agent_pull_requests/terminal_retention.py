"""Optional terminal intent in the existing complete replay-owner envelope."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING

from .attention import Cursor
from .conversation_journal import ConversationJournal, Observation
from .review_intent import encoded, identifier, text
from .review_workspace import _scope
from .watch_contract import PRSnapshot
from .watch_replay import ConsumerScope, ObservationTarget, WatchReplay
from .workspace_conversation import conversation_key

if TYPE_CHECKING:
    from .watch_replay_store import ReplayStore

PROTOCOL = "agent-pull-requests.terminal-retention/v1"
MAX_RECORDS = 1024


class TerminalAdmissionClosed(RuntimeError):
    """Content or execution admission is fenced; recovery custody is unchanged."""


def validate(document: dict, namespace: str) -> None:
    state = document["terminal_retention"]
    if (
        document["terminal_retention_version"] != 1
        or type(document["terminal_retention_version"]) is not int
        or not isinstance(state, dict)
        or set(state) != {"protocol", "records", "generations", "admissions"}
        or state["protocol"] != PROTOCOL
        or any(not isinstance(state[name], list) or len(state[name]) > MAX_RECORDS
               for name in ("records", "generations", "admissions"))
    ):
        raise ValueError("invalid terminal retention contract")
    identities = set()
    targets = {}
    journal = ConversationJournal(document["conversations"])
    admitted = set()
    for row in state["admissions"]:
        if not isinstance(row, dict) or set(row) != {"scope", "cursor", "target"}:
            raise ValueError("invalid authoritative admission")
        scope = _scope(row["scope"])
        key = conversation_key(scope, namespace)
        cursor = Cursor(**row["cursor"])
        current = journal._find(key)
        if (
            scope != row["scope"] or key in admitted or cursor.sequence < 1
            or current is None or current["epoch"] != cursor.epoch
            or current["sequence"] < cursor.sequence
        ):
            raise ValueError("authoritative admission differs from conversation epoch")
        admitted.add(key)
        target = row["target"]
        if not isinstance(target, dict) or set(target) != {"account", "repo", "number"}:
            raise ValueError("invalid authoritative watch binding")
        ConsumerScope("terminal-observer", target["account"], target["repo"])
        if type(target["number"]) is not int or target["number"] != scope["number"]:
            raise ValueError("authoritative watch target differs from selected PR")
        binding = encoded(target)
        if binding in targets and targets[binding] != scope:
            raise ValueError("terminal watch binding is ambiguous across full scopes")
        targets[binding] = scope
    for row in state["records"]:
        if not isinstance(row, dict) or set(row) != {
            "scope", "cursor", "evidence", "operations", "deliveries", "drain", "target",
        }:
            raise ValueError("invalid terminal retirement")
        scope = _scope(row["scope"])
        if scope != row["scope"]:
            raise ValueError("noncanonical terminal scope")
        cursor = Cursor(**row["cursor"])
        target = row["target"]
        if not isinstance(target, dict) or set(target) != {"account", "repo", "number"}:
            raise ValueError("invalid terminal watch binding")
        ConsumerScope("terminal-observer", target["account"], target["repo"])
        if type(target["number"]) is not int or target["number"] != scope["number"]:
            raise ValueError("terminal target differs from selected PR")
        binding = encoded(target)
        if binding in targets and targets[binding] != scope:
            raise ValueError("terminal watch binding is ambiguous across full scopes")
        targets[binding] = scope
        evidence = Observation.restore(row["evidence"])
        identity = (evidence.key, cursor.epoch)
        if (
            evidence.key != conversation_key(scope, namespace)
            or not evidence.terminal or evidence.sections
            or cursor.sequence < 1 or identity in identities
            or row["drain"] not in ("pending", "drained")
            or not isinstance(row["operations"], list)
            or len(row["operations"]) > MAX_RECORDS
            or len(set(row["operations"])) != len(row["operations"])
            or not isinstance(row["deliveries"], list)
            or len(row["deliveries"]) > MAX_RECORDS
        ):
            raise ValueError("invalid terminal evidence or progress")
        conversation = journal._find(evidence.key)
        if conversation is None or conversation["epoch"] < cursor.epoch or (
            conversation["epoch"] == cursor.epoch and (
                conversation["sequence"] < cursor.sequence
                or not Observation.restore(conversation["current"]).terminal
            )
        ):
            raise ValueError("terminal retirement differs from its conversation epoch")
        identities.add(identity)
        for operation_id in row["operations"]:
            text(operation_id, 128)
        registrations = set()
        for delivery in row["deliveries"]:
            if not isinstance(delivery, dict) or set(delivery) != {
                "scope", "request_id", "issued_at", "registration_id", "event_id",
                "deadline_at", "frozen_before_terminal",
            }:
                raise ValueError("invalid terminal delivery identity")
            from .watch_replay import _timestamp

            ConsumerScope(**delivery["scope"])
            WatchReplay._token(delivery["request_id"], delivery["issued_at"])
            text(delivery["registration_id"], 128)
            if delivery["event_id"] is not None:
                text(delivery["event_id"], 128)
            if delivery["deadline_at"] is not None:
                _timestamp(delivery["deadline_at"])
            if (
                type(delivery["frozen_before_terminal"]) is not bool
                or delivery["registration_id"] in registrations
            ):
                raise ValueError("invalid terminal delivery progress")
            registrations.add(delivery["registration_id"])
    generations = set()
    for row in state["generations"]:
        if not isinstance(row, dict) or set(row) != {"scope", "generation_id", "epoch"}:
            raise ValueError("invalid generation epoch binding")
        if _scope(row["scope"]) != row["scope"]:
            raise ValueError("noncanonical generation scope")
        identifier(row["generation_id"])
        Cursor(row["epoch"], 0)
        identity = (encoded(row["scope"]), row["generation_id"])
        if identity in generations:
            raise ValueError("duplicate generation epoch binding")
        generations.add(identity)
    if len(encoded(state)) > document["capacities"]["terminal_retention_bytes"]:
        raise ValueError("terminal retention exceeds its reserved capacity")


def require_live(
    document: dict, scope: dict, *, operation_id: str | None = None,
    generation_id: str | None = None, epoch: int | None = None,
) -> int | None:
    """Called under replay custody at each content/effect mutation boundary."""
    state = document.get("terminal_retention")
    if state is None:
        return None
    scope = _scope(scope)
    if operation_id is not None and any(
        row["operation_id"] == operation_id
        for row in document.get("terminal_recovery", {}).get("operations", [])
    ):
        raise TerminalAdmissionClosed("historical compact operation is recovery-only")
    admission = next((row for row in state["admissions"] if row["scope"] == scope), None)
    if admission is None or any(
        row["scope"] == scope and row["cursor"]["epoch"] == admission["cursor"]["epoch"]
        for row in state["records"]
    ):
        raise TerminalAdmissionClosed("live conversation epoch unavailable")
    current_epoch = admission["cursor"]["epoch"]
    if epoch is not None and epoch != current_epoch:
        raise TerminalAdmissionClosed("selected conversation epoch changed")
    if operation_id is not None and any(
        row["scope"] == scope and operation_id in row["operations"]
        for row in state["records"]
    ):
        raise TerminalAdmissionClosed("historical terminal operation is recovery-only")
    if generation_id is not None and not any(
        row == {"scope": scope, "generation_id": generation_id, "epoch": current_epoch}
        for row in state["generations"]
    ):
        raise TerminalAdmissionClosed("generation is not bound to the live epoch")
    return current_epoch


def bind_generation(document: dict, generation: dict, cursor: dict) -> None:
    state = document.get("terminal_retention")
    if state is None:
        return
    epoch = require_live(document, generation["scope"], epoch=cursor["epoch"])
    current = next(row for row in state["admissions"] if row["scope"] == generation["scope"])
    if current["cursor"] != cursor:
        raise TerminalAdmissionClosed("publication observation is no longer current")
    row = {"scope": copy.deepcopy(generation["scope"]), "generation_id": generation["id"], "epoch": epoch}
    previous = next(
        (value for value in state["generations"] if value["scope"] == row["scope"]
         and value["generation_id"] == row["generation_id"]), None,
    )
    if previous is not None and previous != row:
        raise TerminalAdmissionClosed("immutable generation belongs to a prior epoch")
    if previous is None:
        state["generations"].append(row)


def observe_terminal(
    document: dict, scope: dict, record: dict, target: ObservationTarget, *, clock: Callable[[], float],
) -> dict:
    """Stage notification and fence with the tombstone, never a second file commit."""
    if type(target) is not ObservationTarget or target.number != scope["number"]:
        raise ValueError("actual captured terminal observation target required")
    ConsumerScope("terminal-observer", target.account, target.repo)
    if type(target.registration_ids) is not frozenset or len(target.registration_ids) > MAX_RECORDS:
        raise ValueError("bounded captured terminal registrations required")
    for identity in target.registration_ids:
        text(identity, 128)
    state = document["terminal_retention"]
    previous = next(
        (row for row in state["records"] if row["scope"] == scope
         and row["cursor"]["epoch"] == record["epoch"]), None,
    )
    value = Observation.restore(record["current"])
    if not value.terminal:
        raise ValueError("complete terminal evidence required")
    binding = {"account": target.account, "repo": target.repo, "number": target.number}
    if previous is not None:
        if previous["target"] != binding:
            raise ValueError("terminal observation changed its original watch binding")
        known = {row["registration_id"] for row in previous["deliveries"]}
        if previous["evidence"] == record["current"] and target.registration_ids <= known:
            return copy.deepcopy(previous)
    before = document["replay"]
    if any(
        row["outcome"] == "active" and row["scope"]["account"] == target.account
        and row["scope"]["repo"] == target.repo and row["number"] == target.number
        and row["deadline_at"] is None for row in before["records"]
    ):
        raise TerminalAdmissionClosed("finite original delivery recovery policy required")
    core = WatchReplay(document["namespace"], state=before, commit=lambda _state: None, clock=clock)
    core.observe(
        target.account, target.repo, target.number,
        PRSnapshot(
            pr_state="closed", merged=value.state == "merged",
            head_sha=value.candidate.head, provider=value.key.provider,
        ),
        registration_ids=target.registration_ids,
    )
    after = core.snapshot()
    deliveries = []
    for registration in before["records"]:
        if (
            registration["outcome"] != "active"
            or registration["scope"]["account"] != target.account
            or registration["scope"]["repo"] != target.repo
            or registration["number"] != target.number
            or registration["registration_id"] not in target.registration_ids
        ):
            continue
        identity = registration["registration_id"]

        def pending(replay: dict) -> dict | None:
            return next(
                (sub["pending"] for group in replay["registries"]
                 for sub in group["subscriptions"] if sub["registration_id"] == identity), None,
            )

        event = pending(after)
        deliveries.append({
            **{name: copy.deepcopy(registration[name]) for name in (
                "scope", "request_id", "issued_at", "registration_id", "deadline_at",
            )},
            "event_id": event["event_id"] if event is not None else None,
            "frozen_before_terminal": pending(before) is not None,
        })
    if previous is not None:
        previous["evidence"] = copy.deepcopy(record["current"])
        for delivery in deliveries:
            retained = next(
                (row for row in previous["deliveries"]
                 if row["registration_id"] == delivery["registration_id"]), None,
            )
            if retained is None:
                previous["deliveries"].append(delivery)
            elif retained["event_id"] is None:
                retained["event_id"] = delivery["event_id"]
        document["replay"] = after
        return copy.deepcopy(previous)
    row = {
        "scope": copy.deepcopy(scope),
        "target": binding,
        "cursor": {"epoch": record["epoch"], "sequence": record["sequence"]},
        "evidence": copy.deepcopy(record["current"]),
        "operations": [
            operation["intent"]["operation_id"] for operation in document["reviews"]
            if operation["intent"]["scope"] == scope
        ],
        "deliveries": deliveries, "drain": "pending",
    }
    state["records"].append(row)
    document["replay"] = after
    return copy.deepcopy(row)


def observe_live(document: dict, scope: dict, record: dict, target: ObservationTarget) -> None:
    """Keep admission authority distinct from later partial journal evidence."""
    if type(target) is not ObservationTarget or target.number != scope["number"]:
        raise ValueError("actual captured live observation target required")
    state = document["terminal_retention"]
    value = Observation.restore(record["current"])
    if not value.complete or value.state != "open":
        return
    row = {
        "scope": copy.deepcopy(scope),
        "cursor": {"epoch": record["epoch"], "sequence": record["sequence"]},
        "target": {"account": target.account, "repo": target.repo, "number": target.number},
    }
    previous = next((item for item in state["admissions"] if item["scope"] == scope), None)
    if previous is not None:
        previous.update(row)
    else:
        state["admissions"].append(row)


def replay_epoch(document: dict, target: ObservationTarget) -> tuple[str, int] | None:
    state = document.get("terminal_retention")
    if state is None:
        return None
    binding = {"account": target.account, "repo": target.repo, "number": target.number}
    journal = ConversationJournal(document["conversations"])
    for row in state["admissions"] + state["records"]:
        if row["target"] != binding:
            continue
        current = journal._find(conversation_key(row["scope"], document["namespace"]))
        if current is not None:
            from .review_workspace import _digest
            return _digest(row["scope"]), current["epoch"]
    return None


def replay_observation(
    document: dict, target: ObservationTarget, snapshot: PRSnapshot,
) -> PRSnapshot | None:
    """Preserve the pre-IO epoch; never promote an old read into reopened content."""
    current_epoch = replay_epoch(document, target)
    if current_epoch is None:
        return snapshot
    from .review_workspace import _digest
    for row in document["terminal_retention"]["records"]:
        identity = (_digest(row["scope"]), row["cursor"]["epoch"])
        if identity == target.retention_epoch or identity == current_epoch:
            value = Observation.restore(row["evidence"])
            return PRSnapshot(
                pr_state="closed", merged=value.state == "merged",
                head_sha=value.candidate.head, provider=value.key.provider,
            )
    return snapshot if target.retention_epoch == current_epoch else None


class TerminalRetentionStore:
    """Internal embedding only; no serving declaration, effects or lifecycle authority."""

    def __init__(self, owner: ReplayStore) -> None:
        from .watch_replay_store import ReplayStore

        if not isinstance(owner, ReplayStore) or owner.terminal_retention is not True:
            raise ValueError("explicit terminal-capable replay reader required")
        self.owner = owner

    def adopt(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None, retention_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def current() -> None:
            if checkpoint is not None:
                checkpoint()
            lifecycle_checkpoint()

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document["version"] not in (3, 4, 5, 6):
                raise ValueError("terminal retention requires conversation adoption")
            if "terminal_retention" in document:
                if document["capacities"]["terminal_retention_bytes"] != retention_bytes:
                    raise ValueError("terminal capacity differs from adopted layout")
                return {"status": "already_adopted", "protocol": PROTOCOL}
            document["terminal_retention_version"] = 1
            document["terminal_retention"] = {
                "protocol": PROTOCOL, "records": [], "generations": [], "admissions": [],
            }
            document["capacities"]["terminal_retention_bytes"] = retention_bytes
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": "adopted", "protocol": PROTOCOL}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=current)

    @staticmethod
    def selected(document: dict, scope: dict, cursor: Cursor) -> dict:
        if type(cursor) is not Cursor:
            raise ValueError("exact terminal cursor required")
        state = document.get("terminal_retention")
        if state is None:
            raise ValueError("terminal retention requires explicit adoption")
        record = ConversationJournal(document["conversations"])._find(
            conversation_key(scope, document["namespace"]),
        )
        row = next(
            (row for row in state["records"] if row["scope"] == scope
             and row["cursor"] == asdict(cursor)), None,
        )
        if row is None or record is None or (
            record["epoch"] != cursor.epoch or not Observation.restore(record["current"]).terminal
        ):
            raise TerminalAdmissionClosed("terminal retirement no longer owns the selected epoch")
        return row

    def inspect(
        self, scope: dict, cursor: Cursor, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        scope = _scope(scope)
        return self.owner._review_transaction(
            lambda document: self.selected(document, scope, cursor),
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
