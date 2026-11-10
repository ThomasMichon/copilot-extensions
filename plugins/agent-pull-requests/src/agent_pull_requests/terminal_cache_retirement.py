"""Explicit terminal cache progress in the existing replay-owner envelope."""

from __future__ import annotations

import copy
from dataclasses import asdict

from .attention import Cursor
from .review_intent import encoded, identifier
from .review_workspace import _scope
from .terminal_retention import MAX_RECORDS, TerminalRetentionStore

PROTOCOL = "agent-pull-requests.terminal-cache-retirement/v1"


def validate(document: dict) -> None:
    state = document["terminal_cache_retirement"]
    if (
        type(document["terminal_cache_retirement_version"]) is not int
        or document["terminal_cache_retirement_version"] != 1
        or not isinstance(state, dict) or set(state) != {"protocol", "records"}
        or state["protocol"] != PROTOCOL or not isinstance(state["records"], list)
        or len(state["records"]) > MAX_RECORDS
    ):
        raise ValueError("invalid terminal cache retirement contract")
    seen = set()
    for row in state["records"]:
        if not isinstance(row, dict) or set(row) != {
            "scope", "cursor", "generation_ids", "current_id", "stage",
        }:
            raise ValueError("invalid terminal cache progress")
        scope, cursor = _scope(row["scope"]), Cursor(**row["cursor"])
        key = (encoded(scope), cursor.epoch)
        if scope != row["scope"] or key in seen or row["stage"] not in ("captured", "cache_retired", "complete"):
            raise ValueError("invalid terminal cache identity or stage")
        seen.add(key)
        terminal = next(
            (item for item in document["terminal_retention"]["records"]
             if item["scope"] == scope and item["cursor"] == asdict(cursor)), None,
        )
        if terminal is None or terminal["drain"] != "drained":
            raise ValueError("terminal cache retirement lost its original custody")
        require_compact(document, terminal)
        ids = row["generation_ids"]
        if not isinstance(ids, list) or len(ids) > MAX_RECORDS or len(set(ids)) != len(ids):
            raise ValueError("invalid captured terminal generations")
        for value in ids:
            identifier(value)
        if row["current_id"] is not None and row["current_id"] not in ids:
            raise ValueError("current generation outside captured terminal inventory")
    # Reserve the longest completion stage before the first physical mutation.
    reservation = copy.deepcopy(state)
    for row in reservation["records"]:
        row["stage"] = "cache_retired"
    if len(encoded(reservation)) + len(encoded(document["terminal_retention"])) > (
        document["capacities"]["terminal_retention_bytes"]
    ):
        raise ValueError("terminal cache progress exceeds reserved capacity")


def require_compact(document: dict, terminal: dict) -> None:
    recovered = document["terminal_recovery"]["operations"]
    if any(not any(
        row["operation_id"] == identity and row["scope"] == terminal["scope"]
        and row["terminal_cursor"] == terminal["cursor"]
        for row in recovered
    ) for identity in terminal["operations"]):
        raise ValueError("all captured operations require compact recovery before cache retirement")


class TerminalCacheRetirementStore:
    """Source embedding only; package selection never adopts or retires state."""

    def __init__(self, owner) -> None:
        from .watch_replay_store import ReplayStore
        if not isinstance(owner, ReplayStore) or not owner.terminal_cache_retirement:
            raise ValueError("explicit terminal cache retirement reader required")
        self.owner = owner

    def adopt(self, *, lifecycle_checkpoint, expires_at: int, checkpoint=None) -> dict:
        if not callable(lifecycle_checkpoint) or checkpoint is not None and not callable(checkpoint):
            raise ValueError("actual exclusive reader lifecycle checkpoint required")

        def current():
            if checkpoint is not None:
                checkpoint()
            lifecycle_checkpoint()

        def adopt(document):
            if "terminal_recovery" not in document:
                raise ValueError("terminal recovery adoption required before cache retirement")
            status = "already_adopted" if "terminal_cache_retirement" in document else "adopted"
            if status == "adopted":
                document["terminal_cache_retirement_version"] = 1
                document["terminal_cache_retirement"] = {"protocol": PROTOCOL, "records": []}
            return {"status": status, "protocol": PROTOCOL}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=current)

    @staticmethod
    def selected(document: dict, scope: dict, cursor: Cursor) -> tuple[dict, dict | None]:
        terminal = TerminalRetentionStore.selected(document, scope, cursor)
        if "terminal_cache_retirement" not in document:
            raise ValueError("terminal cache retirement requires explicit adoption")
        require_compact(document, terminal)
        if any(row["intent"]["scope"] == scope for row in document["reviews"]):
            raise ValueError("body-bearing review still owns the terminal scope")
        return terminal, next(
            (row for row in document["terminal_cache_retirement"]["records"]
             if row["scope"] == scope and row["cursor"] == asdict(cursor)), None,
        )

    def inspect(self, scope: dict, cursor: Cursor, *, expires_at: int, checkpoint=None) -> dict | None:
        return self.owner._review_transaction(
            lambda document: self.selected(document, _scope(scope), cursor)[1],
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
