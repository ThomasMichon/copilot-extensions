"""Original checkpoint, target binding and bounded retirement review controls."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from test_retained_live_cache import (
    adopt, cache_for, evidence as evidence, owner as owner, prepared, selected, targets,
)
from test_terminal_cache_retirement import retire as _retire, setup as _retirement_setup
from test_workspace_terminal_retention import (
    Workspace, environment as environment, integration as integration, workspace as workspace,
)

from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import CacheInvalid, GenerationCache
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_retention import replay_epoch
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ConsumerScope, ObservationTarget
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_operations import WorkspaceOperations

Document = dict[str, Any]
retirement_setup = cast(Callable[[Workspace], tuple[tuple[Any, ...], Any]], _retirement_setup)
retire = cast(Callable[..., Document], _retire)


@pytest.mark.parametrize("invalid", [
    "account", "repo", "scope", "account-ambiguity", "scope-ambiguity", "empty",
])
def test_empty_journal_still_requires_canonical_bijective_targets(
    owner: ReplayStore, evidence: Document, invalid: str,
) -> None:
    cache = cache_for(owner)
    bindings = targets(selected(evidence))
    if invalid == "account":
        bindings[0]["account"] = ""
    elif invalid == "repo":
        bindings[0]["repo"] = True
    elif invalid == "scope":
        bindings[0]["scope"]["endpoint"] += "/"
    elif invalid == "account-ambiguity":
        other = copy.deepcopy(bindings[0])
        other["scope"]["principal_id"] = "another-reader"
        bindings.append(other)
    elif invalid == "scope-ambiguity":
        bindings.append({**bindings[0], "account": "another-account"})
    else:
        bindings.clear()
    before = owner.path.read_bytes()
    with pytest.raises(ValueError):
        adopt(owner, cache, bindings)
    assert owner.path.read_bytes() == before


def test_original_cache_checkpoint_survives_the_post_scan_precommit_boundary(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, values = prepared(owner, evidence)
    before = owner.path.read_bytes()
    scanned, withdrawn = [False], [False]
    inspect = GenerationCache.inspect_retained
    validate = owner._validate_owner

    def inspected(
        candidate: GenerationCache, visit: Callable[[Document], None],
        visit_pointer: Callable[[Document], None], **options: Any,
    ) -> None:
        inspect(candidate, visit, visit_pointer, **options)
        scanned[0] = True

    def validated(document: Document) -> None:
        validate(document)
        if scanned[0]:
            withdrawn[0] = True

    def original_checkpoint() -> None:
        if withdrawn[0]:
            raise ValueError("original cache authority withdrawn after scan")

    cache.checkpoint = original_checkpoint
    monkeypatch.setattr(GenerationCache, "inspect_retained", inspected)
    monkeypatch.setattr(owner, "_validate_owner", validated)
    with pytest.raises(ValueError, match="authority withdrawn after scan"):
        adopt(owner, cache, targets(values[0]))
    assert scanned[0] and withdrawn[0]
    assert owner.path.read_bytes() == before


def test_metadata_only_refusal_precedes_generation_decoding(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, values = prepared(owner, evidence, with_generations=True)
    before = owner.path.read_bytes()
    read = GenerationCache._read

    def guarded(candidate: GenerationCache, path: Path, limit: int) -> Document:
        if path.parent == cache.generations:
            pytest.fail("metadata-only refusal decoded a legacy generation")
        result: Document = read(candidate, path, limit)
        return result

    monkeypatch.setattr(GenerationCache, "_read", guarded)
    with pytest.raises(CacheInvalid, match="association"):
        adopt(owner, cache, targets(values[0]))
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("remap", ["account", "repo"])
def test_retained_native_watch_cannot_escape_reopen_fencing_through_remapping(
    owner: ReplayStore, evidence: Document, remap: str,
) -> None:
    cache = cache_for(owner)
    original = selected(evidence)
    ConversationStore(owner).observe_workspace(original, expires_at=100)
    native_scope = ConsumerScope("original-waiter", "original-account", "example/project")
    owner.execute(native_scope, "register", {
        "request_id": "original-native-wait", "issued_at": 1,
        "number": original["scope"]["number"], "until": ("merged",), "deadline_at": 50,
    }, expires_at=100)
    bindings = targets(original)
    bindings[0][remap] = "another-account" if remap == "account" else "another/project"
    before = owner.path.read_bytes()
    try:
        adopt(owner, cache, bindings)
    except ValueError:
        assert owner.path.read_bytes() == before
        legacy = ReplayStore(
            owner.root, owner.namespace, clock=owner.clock, service_identity=owner.service_identity,
        )
        retained = legacy.observation_targets(expires_at=100)
        assert len(retained) == 1 and retained[0].account == native_scope.account
        return

    owner.terminal_native_watches = True
    target = owner.observation_targets(expires_at=100)[0]
    owner.observe(
        target, PRSnapshot(pr_state="open", merged=False, head_sha="a" * 40), expires_at=100,
    )
    delayed = owner.observation_targets(expires_at=100)[0]

    def workspace_target() -> ObservationTarget:
        target = ObservationTarget(
            bindings[0]["account"], bindings[0]["repo"], original["scope"]["number"], frozenset(),
        )
        return owner._review_transaction(
            lambda document: replace(target, retention_epoch=replay_epoch(document, target)),
            expires_at=100, persist=False,
        )

    closed = copy.deepcopy(evidence)
    closed["sections"]["pull_request"]["data"]["status"] = "completed"
    ConversationStore(owner).observe_workspace(
        selected(closed, 2), terminal_target=workspace_target(), expires_at=100,
    )
    ConversationStore(owner).observe_workspace(
        selected(evidence, 4), terminal_target=workspace_target(), expires_at=100,
    )
    outcome = owner.observe(
        delayed, PRSnapshot(pr_state="closed", merged=True, head_sha="a" * 40), expires_at=100,
    )
    pending = owner.execute(native_scope, "pending", {}, expires_at=100)
    assert outcome["status"] == "stale_observation"
    assert pending["events"] == []


@pytest.mark.parametrize("environment", [True], indirect=True)
def test_retirement_reuses_selection_while_replay_custody_is_held(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained, _ = retirement_setup(workspace)
    operations: WorkspaceOperations = retained[0]
    scope: Document = retained[2]["scope"]
    current: Document = operations.cache.lookup(scope)["generation"]
    unrelated = []
    for number in range(100, 108):
        value = copy.deepcopy(current)
        value["scope"]["number"] = number
        value["sections"]["pull_request"]["data"]["pullRequestId"] = number
        value["schema"] = "agent-pull-requests.review-evidence"
        generation: Document = build_generation({
            name: value[name] for name in ("schema", "version", "scope", "sections")
        })["generation"]
        operations.cache.publish(generation, expected_current=None)
        unrelated.append(generation)
    calls = [0]
    original = TerminalCacheRetirementStore.selected

    def measured(document: Document, selected_scope: Document, cursor: Cursor) -> tuple[Any, Any]:
        calls[0] += 1
        return original(document, selected_scope, cursor)

    monkeypatch.setattr(TerminalCacheRetirementStore, "selected", staticmethod(measured))
    assert retire(retained)["stage"] == "complete"
    assert calls[0] <= 4, "repeated whole-journal selection inside per-entry checkpoints"
    for generation in unrelated:
        assert operations.cache.lookup(generation["scope"])["generation"]["id"] == generation["id"]
