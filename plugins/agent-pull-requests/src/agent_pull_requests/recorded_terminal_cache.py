"""Recorded workspace epochs never manufacture native observation targets."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .attention import PRKey
from .conversation_journal import ConversationJournal, Observation
from .generation_associations import selected
from .generation_cache import CacheInvalid, GenerationCache
from .review_intent import encoded
from .workspace_conversation import conversation_key

Document = dict[str, Any]
MARKER = "recorded_terminal_cache_version"


def contract() -> Document:
    return {
        "protocol": "agent-pull-requests.recorded-terminal-cache-adoption/v1",
        "storage_extension": MARKER + ":1",
        "bindings": "recorded_workspace_epochs_only", "native_targets": False,
    }


def complete(document: Document) -> bool:
    return document.get(MARKER) == 1 and all(
        document.get(name + "_version") == 1 for name in (
            "terminal_retention", "terminal_recovery", "terminal_cache_retirement",
        )
    )


def workspace_epoch(document: Document, scope: Document) -> int | None:
    """Body reads only; replay_epoch deliberately ignores these cache bindings."""
    if document.get(MARKER) != 1:
        return None
    record = ConversationJournal(document["conversations"])._find(
        conversation_key(scope, document["namespace"]),
    )
    if record is None or record["epoch"] != 1 or record["previous_terminal"] is not None:
        return None
    observed = Observation.restore(record["current"])
    if not observed.complete or observed.state != "open" or not any(
        row["scope"] == scope and row["epoch"] == record["epoch"]
        for row in document["terminal_retention"]["generations"]
    ):
        return None
    return 1


def stage(
    document: Document, cache: GenerationCache, records: dict[PRKey, Document],
    visit_pointer: Callable[[Document], None], checkpoint: Callable[[], None],
) -> None:
    """Called under replay custody; inspect each immutable cache object once."""
    associations = selected(document, cache)["records"]
    by_id: dict[str, Document] = {}
    for row in associations:
        checkpoint()
        if row["publication"] != "confirmed":
            raise CacheInvalid("pending original generation publication requires its original reader")
        by_id[row["generation_id"]] = row
    bindings: list[Document] = []

    def visit(generation: Document) -> None:
        checkpoint()
        scope = generation["scope"]
        row = by_id.get(generation["id"])
        record = records.get(conversation_key(scope, document["namespace"]))
        if (
            row is None or row["scope"] != scope or row["cursor"]["epoch"] != 1
            or row["complete"] is not True or row["state"] != "open"
            or record is None or row["cursor"]["sequence"] > record["sequence"]
            or generation["complete"] is not True
            or generation["sections"]["pull_request"]["data"].get("status") != "active"
        ):
            raise CacheInvalid("retained generation lacks a complete original open association")
        bindings.append({
            "scope": copy.deepcopy(scope), "generation_id": generation["id"],
            "epoch": row["cursor"]["epoch"],
        })

    cache.inspect_retained(visit, visit_pointer)
    checkpoint()
    bindings.sort(key=encoded)
    state = document["terminal_retention"]
    if state["records"] or state["admissions"] or (
        document.get(MARKER) == 1 and state["generations"] != bindings
    ) or document.get(MARKER) is None and state["generations"]:
        raise CacheInvalid("populated terminal state differs from the original recorded transition")
    state["generations"] = bindings
    document[MARKER] = 1
    checkpoint()
