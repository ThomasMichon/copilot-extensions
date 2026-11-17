"""Inert reference planning; no service, task, wake, ACK or publication effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_reviewer import PROTOCOL


@dataclass(frozen=True)
class Scope:
    identity: RepositoryIdentity
    consumer: str
    reviewer: str
    task_scope: tuple[str, str, str]


@dataclass(frozen=True)
class Task:
    """Projection supplied by the existing exact task-lookup owner."""

    scope: tuple[str, str, str]
    outcome: Literal["absent", "present", "unknown"]
    task_id: str = ""
    revision: str = ""
    status: str = ""
    held: bool = False


@dataclass(frozen=True)
class Notification:
    """Previously retained request evidence, not proof of a handled PR action."""

    action_key: str
    request_id: str
    task_id: str
    outcome: Literal["accepted", "unknown"]


@dataclass(frozen=True)
class Proposal:
    kind: Literal["create", "steer", "noop", "reconcile", "read-page"]
    reason: str
    pr: int | None = None
    action_key: str = ""
    task_id: str = ""
    revision: str = ""
    notification_key: str = ""
    unavailable: int = 0


def _key(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def propose(
    page: dict[str, Any], scope: Scope, *,
    lookup: Callable[[dict[str, Any]], Task],
    notifications: Mapping[int, Notification], now: int,
    last_served_pr: int | None = None, after: int = 0,
) -> Proposal:
    """Select at most one proposal; the embedding retains all real admission."""
    if (
        page.get("protocol") != PROTOCOL
        or page.get("identity") != scope.identity.document()
        or page.get("consumer") != scope.consumer
        or page.get("reviewer") != scope.reviewer
        or len(scope.task_scope) != 3 or not all(scope.task_scope)
    ):
        raise ValueError("consumer scope differs")
    unavailable = len(page["unavailable"])
    if type(after) is not int or after != 0:
        return Proposal("reconcile", "a complete first-page selection is required",
                        unavailable=unavailable)
    if page["inventory"]["outcome"] != "complete":
        return Proposal("reconcile", "inventory unavailable", unavailable=unavailable)
    if page["more"]:
        return Proposal("read-page", "retain the same projection cursor", unavailable=unavailable)
    actions = page["next_actions"]
    if not isinstance(actions, list) or len(actions) > 16:
        raise ValueError("bounded action page required")
    numbers = [action["pr"] for action in actions]
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate PR action")
    if last_served_pr in numbers:
        offset = numbers.index(last_served_pr) + 1
        actions = actions[offset:] + actions[:offset]
    for action in actions:
        number = action["pr"]
        if action["actor_role"] != "reviewer":
            continue
        if action["actor"] != scope.reviewer or action["action"] not in {
            "initial_verdict", "new_verdict",
        }:
            return Proposal("reconcile", "unsupported reviewer action", number, unavailable=unavailable)
        if not action["observed_at"] <= now < action["expires_at"]:
            return Proposal("reconcile", "action evidence expired", number, unavailable=unavailable)
        action_key = _key([
            scope.identity.document(), scope.consumer, scope.reviewer, number,
            action["action"], action["reason"], action["candidate"], action["cause"],
        ])
        prior = notifications.get(number)
        if prior is not None and prior.outcome == "unknown":
            return Proposal("reconcile", "original notification unsettled", number,
                            action_key, prior.task_id, unavailable=unavailable)
        task = lookup(action)
        if task.scope != scope.task_scope or task.outcome == "unknown":
            return Proposal("reconcile", "task association unavailable", number,
                            action_key, unavailable=unavailable)
        if task.outcome == "absent":
            if prior is not None:
                return Proposal("reconcile", "prior task receipt still retained", number,
                                action_key, prior.task_id, unavailable=unavailable)
            return Proposal("create", "conclusively absent task", number, action_key,
                            notification_key=_key([action_key, "create"]), unavailable=unavailable)
        if not task.task_id or not task.revision:
            return Proposal("reconcile", "task identity unavailable", number,
                            action_key, unavailable=unavailable)
        if prior is not None and prior.task_id != task.task_id:
            return Proposal("reconcile", "task owner changed", number,
                            action_key, task.task_id, unavailable=unavailable)
        if task.held or task.status not in {"started", "suspended"}:
            continue
        if prior is not None and prior.action_key == action_key:
            continue
        return Proposal(
            "steer", "materially new action for retained task", number, action_key,
            task.task_id, task.revision, _key([action_key, task.task_id, task.revision]),
            unavailable,
        )
    return Proposal("noop", "no eligible unnotified action in this view", unavailable=unavailable)
