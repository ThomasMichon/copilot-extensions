"""Metadata-only transition; legacy generations lack durable observation binding."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .conversation_journal import ConversationJournal, Observation
from .generation_cache import CacheInvalid, GenerationCache
from .terminal_retention import MAX_RECORDS
from .workspace_conversation import conversation_key

Document = dict[str, Any]


def contract() -> dict[str, str]:
    """Declare executing-core support separately from terminal read selection."""
    return {
        "protocol": "agent-pull-requests.retained-cache-metadata-adoption/v1",
        "source": "original_first_epoch", "effects": "no_observations_or_bindings",
    }


def validate(
    document: Document, cache: GenerationCache, targets: list[Document],
    checkpoint: Callable[[], None],
) -> None:
    """Run inside the existing replay adoption and exclusive service lifecycle."""
    checkpoint()
    if cache.max_generations > MAX_RECORDS:
        raise ValueError("configured cache growth exceeds terminal generation binding capacity")
    if (
        document["version"] != 3 or document["reviews"]
        or document.get("terminal_recovery", {}).get("operations")
        or document.get("terminal_cache_retirement", {}).get("records")
    ):
        raise ValueError("retained cache transition requires the original standalone recovery-free owner")
    journal = ConversationJournal(document["conversations"])
    for record in journal.records:
        checkpoint()
        observed = Observation.restore(record["current"])
        if (
            record["epoch"] != 1 or record["previous_terminal"] is not None
            or observed.state != "open" or not observed.complete
        ):
            raise ValueError("retained conversations require a complete nonterminal original first epoch")
        matches = [
            target for target in targets
            if conversation_key({**target["scope"], "number": observed.key.number}, document["namespace"])
            == observed.key
        ]
        if len(matches) != 1:
            raise ValueError("retained conversation is outside the selected workspace scope")

    def visit(generation: Document) -> None:
        checkpoint()
        raise CacheInvalid("retained generations require durable original observation association")

    def visit_pointer(pointer: Document) -> None:
        checkpoint()
        scope = pointer["scope"]
        if not any({**target["scope"], "number": scope["number"]} == scope for target in targets):
            raise ValueError("retained pointer is outside the selected workspace scope")

    cache.inspect_retained(visit, visit_pointer)
    checkpoint()
    state = document["terminal_retention"]
    if any(state[name] for name in ("records", "generations", "admissions")):
        raise ValueError("populated terminal state requires its original lifecycle transition")
    checkpoint()
