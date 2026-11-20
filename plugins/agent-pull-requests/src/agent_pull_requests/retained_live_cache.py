"""Metadata-only transition; legacy generations lack durable observation binding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .attention import PRKey
from .conversation_journal import ConversationJournal, Observation
from .generation_cache import CacheInvalid, GenerationCache
from .terminal_retention import MAX_RECORDS
from .review_workspace import _scope
from .watch_replay import ConsumerScope, WatchReplay
from .workspace_conversation import conversation_key

Document = dict[str, Any]


def contract() -> dict[str, str]:
    """Declare executing-core support separately from terminal read selection."""
    return {
        "protocol": "agent-pull-requests.retained-cache-metadata-adoption/v1",
        "source": "original_first_epoch", "effects": "no_observations_or_bindings",
    }


def _targets(targets: list[Document]) -> list[Document]:
    if not isinstance(targets, list) or not 1 <= len(targets) <= 128:
        raise ValueError("retained cache transition requires bounded original target bindings")
    accounts: dict[tuple[str, str], Document] = {}
    scopes: dict[tuple[tuple[str, str], ...], tuple[str, str]] = {}
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"scope", "account", "repo"}:
            raise ValueError("invalid retained target binding")
        selected = ConsumerScope("retained-transition", target["account"], target["repo"])
        scope = target["scope"]
        if not isinstance(scope, dict):
            raise ValueError("invalid retained workspace scope")
        canonical = _scope({**scope, "number": 1})
        canonical.pop("number")
        if canonical != scope:
            raise ValueError("retained workspace scope must be canonical")
        account = selected.account, selected.repo
        key = tuple(sorted(canonical.items()))
        if accounts.setdefault(account, canonical) != canonical or scopes.setdefault(key, account) != account:
            raise ValueError("retained targets must map accounts and workspace scopes unambiguously")
    return targets


def validate(
    document: Document, cache: GenerationCache, targets: list[Document],
    checkpoint: Callable[[], None],
    *, recorded_generations: bool = False, recorded_native_targets: bool = False,
) -> None:
    """Run inside the existing replay adoption and exclusive service lifecycle."""
    checkpoint()
    if type(recorded_generations) is not bool:
        raise ValueError("explicit retained generation adoption selection required")
    if type(recorded_native_targets) is not bool or recorded_native_targets and not recorded_generations:
        raise ValueError("native adoption requires the recorded generation reader")
    targets = _targets(targets)
    keys = {
        conversation_key({**target["scope"], "number": 1}, document["namespace"])
        for target in targets
    }

    def visit_pointer(pointer: Document) -> None:
        checkpoint()
        scope = {**pointer["scope"], "number": 1}
        if conversation_key(scope, document["namespace"]) not in keys:
            raise ValueError("retained pointer is outside the selected workspace scope")

    if cache.max_generations > MAX_RECORDS:
        raise ValueError("configured cache growth exceeds terminal generation binding capacity")
    from .recorded_terminal_cache import complete, stage
    if recorded_generations and complete(document):
        from .generation_associations import selected
        from .recorded_terminal_cache import readable_bindings
        from .review_intent import encoded
        selected(document, cache)
        if recorded_native_targets:
            from .recorded_native_cache import original_targets
            original_targets(document, targets, checkpoint)
        readable = readable_bindings(document, checkpoint)

        def existing(value: Document) -> None:
            visit_pointer(value)
            if (encoded(value["scope"]), value["id"]) not in readable:
                raise CacheInvalid("retained generation is not bound to a live workspace epoch")

        cache.inspect_retained(existing, visit_pointer)
        checkpoint()
        return
    if (
        document["version"] != 3 or document["reviews"]
        or document.get("terminal_recovery", {}).get("operations")
        or document.get("terminal_cache_retirement", {}).get("records")
    ):
        raise ValueError("retained cache transition requires the original standalone recovery-free owner")
    journal = ConversationJournal(document["conversations"])
    records: dict[PRKey, Document] = {}
    for record in journal.records:
        checkpoint()
        observed = Observation.restore(record["current"])
        if (
            record["epoch"] != 1 or record["previous_terminal"] is not None
            or observed.state != "open" or not observed.complete
        ):
            raise ValueError("retained conversations require a complete nonterminal original first epoch")
        if replace(observed.key, number=1) not in keys:
            raise ValueError("retained conversation is outside the selected workspace scope")
        records[observed.key] = record
    if not recorded_native_targets and journal.records and WatchReplay(
        document["namespace"], state=document["replay"], commit=lambda state: None,
    ).observation_targets():
        raise ValueError("retained active native watches require their original workspace association")

    def visit(generation: Document) -> None:
        checkpoint()
        raise CacheInvalid("retained generations require durable original observation association")

    if recorded_generations:
        stage(
            document, cache, records, visit_pointer, checkpoint,
            native_targets=targets if recorded_native_targets else None,
        )
        return
    cache.inspect_retained(visit, visit_pointer, allow_generations=False)
    checkpoint()
    state = document["terminal_retention"]
    if any(state[name] for name in ("records", "generations", "admissions")):
        raise ValueError("populated terminal state requires its original lifecycle transition")
    checkpoint()
