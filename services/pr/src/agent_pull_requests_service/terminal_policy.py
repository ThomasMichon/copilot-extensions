"""Admission and public projection for the finite standalone terminal profile."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_operations import WorkspaceOperations

TRANSITIONS = ("merged", "closed", "head_changed")


def registration(payload: dict[str, Any]) -> None:
    if payload.get("deadline_at") is None:
        raise ValueError("terminal watches require an original finite deadline")
    until = payload.get("until")
    if not isinstance(until, (list, tuple)) or any(
        name not in TRANSITIONS for name in until
    ):
        raise ValueError("terminal watches require supported terminal predicates")


class TerminalReplayStore(ReplayStore):
    def execute(
        self, scope: ConsumerScope, operation: str, payload: dict[str, Any], *,
        expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if operation == "register":
            registration(payload)
        return super().execute(
            scope, operation, payload, expires_at=expires_at, checkpoint=checkpoint,
        )


class TerminalWorkspaceOperations(WorkspaceOperations):
    def execute(
        self, operation: str, payload: dict[str, Any], *, expires_at: int,
        checkpoint: Callable[[], None],
        selection_validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = super().execute(
            operation, payload, expires_at=expires_at, checkpoint=checkpoint,
            selection_validator=selection_validator,
        )
        if operation == "capabilities":
            value["watch_registration_policy"] = {
                "protocol": "agent-pull-requests.terminal-watch-admission/v1",
                "deadline": "finite_original", "until": list(TRANSITIONS),
            }
        elif operation in ("load", "sync"):
            observation = value["result"].get("conversation", {})
            if "retirement" in observation:
                retained = observation["retirement"]
                observation["retirement"] = {
                    "cursor": retained["cursor"], "drain": retained["drain"], "effects": "none",
                }
        return value
