"""Core-owned retained binding, refusal, retry and bounded scan controls."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from single_instance_lease import SingleInstance

from test_review_workspace import evidence as evidence

from agent_pull_requests import generation_cache, review_workspace, watch_replay_store, workspace_conversation
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_workspace import _digest, build_generation
from agent_pull_requests.terminal_retention import TerminalRetentionStore
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore

Document = dict[str, Any]


@pytest.fixture
def owner(tmp_path: Path) -> ReplayStore:
    store = ReplayStore(
        tmp_path, "original-owner", clock=lambda: 1,
        service_identity=ReplayServiceIdentity("fleet", "gateway", "slot"),
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True,
    )
    store.provision(conversations=True)
    return store


def cache_for(owner: ReplayStore) -> GenerationCache:
    cache = GenerationCache(
        owner.root, owner.namespace, max_bytes=1024 * 1024, max_generations=10,
        service_identity=owner.service_identity,
    )
    cache.provision()
    return cache


def selected(evidence: Document, second: int = 0, *, offset: str = "100000") -> Document:
    value = copy.deepcopy(evidence)
    for section in value["sections"].values():
        section.update(
            observed_from=f"2026-10-30T00:00:{second:02d}.000000Z",
            observed_to=f"2026-10-30T00:00:{second + 1:02d}.000000Z",
        )
    value["sections"]["policies"].update(
        observed_from=f"2026-10-30T00:00:{second:02d}.{offset}Z",
        observed_to=f"2026-10-30T00:00:{second:02d}.900000Z",
    )
    result: Document = build_generation(value)["generation"]
    return result


def targets(generation: Document) -> list[Document]:
    return [{
        "scope": {name: value for name, value in generation["scope"].items() if name != "number"},
        "account": "original-account", "repo": "example/project",
    }]


def adopt(owner: ReplayStore, cache: GenerationCache, bindings: list[Document]) -> Document:
    with SingleInstance(owner.root, service="agent-pull-requests-watch") as lease:
        def current() -> None:
            assert lease.held
        return TerminalRetentionStore(owner).adopt(
            retained_cache=cache, retained_targets=bindings,
            lifecycle_checkpoint=current, checkpoint=current, expires_at=100,
        )


def populated(owner: ReplayStore, evidence: Document) -> tuple[GenerationCache, list[Document]]:
    cache = cache_for(owner)
    values = [selected(evidence), selected(evidence, offset="200000")]
    previous = None
    for value in values:
        assert cache.publish(value, expected_current=previous)["status"] == "published"
        ConversationStore(owner).observe_workspace(value, expires_at=100)
        previous = value["id"]
    return cache, values


def test_scan_work_counts_and_original_committed_projection_bindings(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, values = populated(owner, evidence)
    before = json.loads(owner.path.read_bytes())
    validated: list[str] = []
    reads: list[str] = []
    validate, read = review_workspace._generation, cache._read

    def validate_once(value: Document, scope: Document) -> Document:
        validated.append(value["id"])
        result: Document = validate(value, scope)
        return result

    def read_once(path: Path, limit: int) -> Document:
        reads.append(path.parent.name if path != cache.descriptor else "descriptor")
        return read(path, limit)

    def redundant_validation(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("projection repeated full generation validation")

    monkeypatch.setattr(generation_cache, "_generation", validate_once)
    monkeypatch.setattr(cache, "_read", read_once)
    monkeypatch.setattr(workspace_conversation, "_generation", redundant_validation)
    adopt(owner, cache, targets(values[0]))
    after = json.loads(owner.path.read_bytes())
    assert sorted(validated) == sorted(value["id"] for value in values)
    assert reads.count("descriptor") == 1 and reads.count("generations") == 2 and reads.count("targets") == 1
    assert after["conversations"] == before["conversations"]
    assert {row["generation_id"] for row in after["terminal_retention"]["generations"]} == set(validated)


@pytest.mark.parametrize("mismatch", ["root", "namespace", "service", "scope"])
def test_core_owner_and_scope_refusal_preserve_original_state(
    owner: ReplayStore, evidence: Document, mismatch: str,
) -> None:
    cache, values = populated(owner, evidence)
    bindings = targets(values[0])
    selected_cache = cache
    if mismatch == "scope":
        bindings[0]["scope"]["principal_id"] = "another-principal"
    else:
        selected_cache = GenerationCache(
            owner.root.with_name("foreign-owner") if mismatch == "root" else owner.root,
            "another-namespace" if mismatch == "namespace" else owner.namespace,
            max_bytes=cache.max_bytes, max_generations=cache.max_generations,
            service_identity=ReplayServiceIdentity("other", "gateway", "slot")
            if mismatch == "service" else owner.service_identity,
        )
    before = owner.path.read_bytes()
    with pytest.raises(ValueError):
        adopt(owner, selected_cache, bindings)
    assert owner.path.read_bytes() == before
    assert cache.lookup(values[0]["scope"], generation_id=values[0]["id"])["found"]


@pytest.mark.parametrize("uncertain", [False, True])
def test_core_explicit_retry_preserves_original_journal_and_bindings(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    cache, values = populated(owner, evidence)
    before = json.loads(owner.path.read_bytes())
    original = watch_replay_store._atomic_write_json

    def interrupted(*args: Any, **kwargs: Any) -> None:
        if uncertain:
            original(*args, **kwargs)
        raise OSError("synthetic original adoption interruption")

    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", interrupted)
        with pytest.raises(OSError):
            adopt(owner, cache, targets(values[0]))
    partial = json.loads(owner.path.read_bytes())
    assert ("terminal_retention" in partial) is uncertain
    adopt(owner, cache, targets(values[0]))
    after = json.loads(owner.path.read_bytes())
    assert after["conversations"] == before["conversations"]
    if uncertain:
        assert after["terminal_retention"] == partial["terminal_retention"]


@pytest.mark.parametrize("failure", ["partial-head", "uncommitted-before-reopen"])
def test_no_live_admission_from_partial_or_unassociated_journal_evidence(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    cache = cache_for(owner)
    old = selected(evidence)
    cache.publish(old, expected_current=None)
    if failure == "partial-head":
        partial = copy.deepcopy(evidence)
        partial["sections"]["pull_request"]["complete"] = False
        ConversationStore(owner).observe_workspace(selected(partial), expires_at=100)
    else:
        def failed_write(*args: Any, **kwargs: Any) -> None:
            raise OSError("synthetic uncommitted journal observation")
        with monkeypatch.context() as patch:
            patch.setattr(watch_replay_store, "_atomic_write_json", failed_write)
            for value in (evidence, copy.deepcopy(evidence)):
                if value is not evidence:
                    value["sections"]["pull_request"]["data"]["status"] = "abandoned"
                with pytest.raises(OSError):
                    ConversationStore(owner).observe_workspace(selected(value), expires_at=100)
        reopened = selected(evidence, 4)
        assert reopened["revision"] == old["revision"]
        ConversationStore(owner).observe_workspace(reopened, expires_at=100)
    before = owner.path.read_bytes()
    with pytest.raises(ValueError, match="complete"):
        adopt(owner, cache, targets(old))
    assert owner.path.read_bytes() == before
    assert cache.lookup(old["scope"], generation_id=old["id"])["generation"] == old


def test_null_rebuild_pointer_with_failed_journal_needs_no_admission(
    owner: ReplayStore, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = cache_for(owner)
    value = selected(evidence)
    cache.publish(value, expected_current=None)
    unlink = Path.unlink
    pointer = cache.targets / (_digest(value["scope"]) + ".json")

    def interrupted(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == pointer:
            raise OSError("synthetic interruption after generation retirement")
        unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError):
            cache.rebuild()
    assert not list(cache.generations.iterdir())
    original = cache.lookup(value["scope"])
    assert original["found"] is False
    before = json.loads(owner.path.read_bytes())
    adopt(owner, cache, targets(value))
    after = json.loads(owner.path.read_bytes())
    assert after["conversations"] == before["conversations"]
    assert after["terminal_retention"]["admissions"] == []
    assert after["terminal_retention"]["generations"] == []
    assert cache.lookup(value["scope"]) == original


def test_untrusted_projection_still_validates_generation_identity(evidence: Document) -> None:
    value = selected(evidence)
    value["sections"]["pull_request"]["data"]["title"] = "changed without original generation identity"
    with pytest.raises(ValueError, match="corrupt"):
        workspace_conversation.project_generation(value, "original-owner", None)
