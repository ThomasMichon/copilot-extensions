"""Compatibility bindings from retained evidence, never a new observation."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .conversation_journal import ConversationJournal, Observation
from .generation_cache import GenerationCache
from .review_workspace import _digest, _scope
from .terminal_retention import MAX_RECORDS
from .workspace_conversation import conversation_key, project_generation

Document = dict[str, Any]


def contract() -> dict[str, str]:
    """Declare executing-core support separately from terminal read selection."""
    return {
        "protocol": "agent-pull-requests.retained-live-cache-adoption/v1",
        "source": "original_first_epoch", "effects": "compatibility_metadata_only",
    }


def bind(
    document: Document, cache: GenerationCache, targets: list[Document],
    checkpoint: Callable[[], None],
) -> None:
    """Run inside the existing replay adoption and exclusive service lifecycle."""
    from .terminal_retention import PROTOCOL

    checkpoint()
    if (
        document["version"] != 3 or document["reviews"]
        or document.get("terminal_recovery", {}).get("operations")
        or document.get("terminal_cache_retirement", {}).get("records")
    ):
        raise ValueError("retained cache transition requires the original standalone recovery-free owner")
    journal = ConversationJournal(document["conversations"])
    admissions: list[Document] = []
    by_scope: dict[str, Document] = {}
    for record in journal.records:
        checkpoint()
        observed = Observation.restore(record["current"])
        if record["epoch"] != 1 or record["previous_terminal"] is not None or observed.state != "open":
            raise ValueError("retained conversations require a nonterminal original first epoch")
        matches = [
            target for target in targets
            if conversation_key({**target["scope"], "number": observed.key.number}, document["namespace"])
            == observed.key
        ]
        if len(matches) != 1:
            raise ValueError("retained conversation is outside the selected workspace scope")
        target = matches[0]
        scope = _scope({**target["scope"], "number": observed.key.number})
        row = {
            "scope": scope, "cursor": {"epoch": record["epoch"], "sequence": record["sequence"]},
            "target": {"account": target["account"], "repo": target["repo"], "number": scope["number"]},
        }
        admissions.append(row)
        by_scope[_digest(scope)] = record

    generations: list[Document] = []

    def visit(generation: Document) -> None:
        checkpoint()
        scope = generation["scope"]
        record = by_scope.get(_digest(scope))
        observed = project_generation(generation, document["namespace"], None)
        if (
            record is None or not generation["complete"] or observed is None
            or observed.state != "open" or not observed.complete
            or observed.observed_to > record["current"]["observed_to"]
        ):
            raise ValueError("populated cache requires original complete live conversation evidence")
        if len(generations) >= MAX_RECORDS:
            raise ValueError("retained generation bindings exceed terminal capacity")
        generations.append({"scope": scope, "generation_id": generation["id"], "epoch": record["epoch"]})

    def visit_pointer(pointer: Document) -> None:
        checkpoint()
        if _digest(pointer["scope"]) not in by_scope:
            raise ValueError("retained pointer requires original selected conversation evidence")

    cache.inspect_retained(visit, visit_pointer)
    checkpoint()
    admissions.sort(key=lambda row: _digest(row["scope"]))
    generations.sort(key=lambda row: row["generation_id"])
    expected = {"protocol": PROTOCOL, "records": [], "generations": generations, "admissions": admissions}
    state = document["terminal_retention"]
    if state["records"]:
        raise ValueError("populated terminal state differs from original retained evidence")
    for name in ("generations", "admissions"):
        expected_rows = {_digest(row) for row in expected[name]}
        for row in state[name]:
            checkpoint()
            if _digest(row) not in expected_rows:
                raise ValueError("populated terminal state differs from original retained evidence")
    document["terminal_retention"] = copy.deepcopy(expected)
    checkpoint()
