"""Compatibility refusals retain the original reader rather than only its bytes."""

from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from single_instance_lease import SingleInstance

from agent_pull_requests.attention import Cursor
from agent_pull_requests.attention_store import AttentionStore
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_workspace import SECTIONS, build_generation
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore, compact
from agent_pull_requests.terminal_retention import TerminalRetentionStore, bind_generation
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests_service import terminal_transition
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import HostConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_terminal_transition import cache_bytes, retained
else:
    from test_host import Configuration, configuration as configuration
    from test_terminal_transition import cache_bytes, retained


@pytest.mark.parametrize("direction", ["forward", "reverse"])
def test_ambiguous_watch_workspace_bindings_refuse(configuration: Configuration, direction: str) -> None:
    path, value, config = retained(configuration)
    value["workspaces"]["terminal_reads"] = False
    binding = {
        **value["bindings"][0], "principal": "caller-b", "target": "repository-b", "consumer": "dispatch-b",
    }
    if direction == "reverse":
        binding["account"] = "account-b"
        value["observations"].append({**value["observations"][0], "account": "account-b"})
    value["bindings"].append(binding)
    reader = copy.deepcopy(value["workspaces"]["bindings"][0])
    reader.update(principal="caller-b", target="repository-b")
    if direction == "forward":
        reader["scope"]["principal_id"] = "another-reviewer"
    value["workspaces"]["bindings"].append(reader)
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    assert baseline.workspaces is not None
    for operations in baseline.workspaces.operations(baseline.store(), lambda: None, 60).values():
        operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    before = config.store().path.read_bytes(), cache_bytes(config)
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="ambiguous"):
        terminal_transition.adopt(load_config(path))
    assert (config.store().path.read_bytes(), cache_bytes(config)) == before


def test_legacy_registration_without_deadline_keeps_original_inspection(configuration: Configuration) -> None:
    path, _, config = retained(configuration)
    store = ReplayStore(
        config.root, config.namespace, service_identity=config.store().service_identity,
    )
    scope = config.bindings[0].scope
    identity = {"request_id": "original-unbounded", "issued_at": time.time()}
    receipt = store.execute(scope, "register", {
        **identity, "number": 42, "until": ("closed",),
    }, expires_at=int(time.time()) + 60)
    assert receipt["deadline_at"] is None
    before = store.path.read_bytes()
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert store.path.read_bytes() == before
    assert store.execute(scope, "inspect", identity, expires_at=int(time.time()) + 60) == receipt


def test_one_second_budget_survives_wall_clock_boundary(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, config = retained(configuration)
    config = replace(config, operation_seconds=1)
    started = math.floor(time.time()) + 0.99
    original = HostConfig.store

    def store(selected: HostConfig) -> ReplayStore:
        owner = original(selected)
        owner.clock = lambda: started + 0.2
        return owner

    monkeypatch.setattr(HostConfig, "store", store)
    monkeypatch.setattr(terminal_transition, "time", SimpleNamespace(
        time=lambda: started, monotonic=lambda: 0.0,
    ))
    terminal_transition.adopt(config)


def complete_evidence() -> dict[str, Any]:
    evidence = json.loads((
        Path(__file__).resolve().parents[3]
        / "plugins/agent-pull-requests/docs/review-evidence.example.json"
    ).read_text())
    template = evidence["sections"]["pull_request"]
    for name in SECTIONS:
        evidence["sections"].setdefault(name, {**template, "data": []})
        evidence["sections"][name].update(complete=True, errors=[])
    return cast(dict[str, Any], evidence)


@pytest.mark.parametrize("with_operation", [False, True])
def test_retained_terminal_records_refuse_expansion_and_preserve_original_inspection(
    configuration: Configuration, with_operation: bool,
) -> None:
    _, _, config = retained(configuration)
    owner = ReplayStore(
        config.root, config.namespace, service_identity=config.store().service_identity,
        terminal_retention=True, terminal_recovery=True,
    )
    evidence = complete_evidence()
    live = build_generation(evidence)["generation"]
    target = ObservationTarget("account-a", "example/repository", live["scope"]["number"], frozenset())
    expires_at = int(time.time()) + 60
    with SingleInstance(config.root, service="agent-pull-requests-watch") as lease:
        def current() -> None:
            assert lease.held
        TerminalRetentionStore(owner).adopt(lifecycle_checkpoint=current, expires_at=expires_at)
        recovery = TerminalRecoveryStore(owner)
        recovery.adopt(lifecycle_checkpoint=current, expires_at=expires_at)
        observed = ConversationStore(owner).observe_workspace(
            live, expires_at=expires_at, terminal_target=target,
        )
        owner._review_transaction(
            lambda document: bind_generation(document, live, observed["cursor"]), expires_at=expires_at,
        )
        intent = {
            "schema": "agent-pull-requests.review-intent", "version": 1,
            "operation_id": "original-compact-unknown", "scope": live["scope"],
            "generation_id": live["id"], "source_id": "a" * 64,
            "revision": {name: live["revision"][name] for name in ("head_sha", "common_sha", "iteration")},
            "actions": [{"id": "verdict", "kind": "verdict", "verdict": "APPROVED"}],
        }
        if with_operation:
            owner.admit_review(intent, expires_at=expires_at)
            owner.begin_review_action(intent["operation_id"], intent["scope"], "verdict", expires_at=expires_at)
        evidence["sections"]["pull_request"]["data"]["status"] = "completed"
        latest = max(
            datetime.fromisoformat(section["observed_to"].replace("Z", "+00:00"))
            for section in evidence["sections"].values()
        )
        for section in evidence["sections"].values():
            section["observed_from"] = (latest + timedelta(seconds=1)).isoformat()
            section["observed_to"] = (latest + timedelta(seconds=2)).isoformat()
        terminal = ConversationStore(owner).observe_workspace(
            build_generation(evidence)["generation"], expires_at=expires_at, terminal_target=target,
        )
        assert terminal["status"] == "observed"
        cursor = Cursor(**terminal["cursor"])

        def retire(document: dict[str, Any]) -> None:
            # This fixture has no running host, readers or native sender to drain.
            TerminalRetentionStore.selected(document, intent["scope"], cursor)["drain"] = "drained"
            policies = {intent["operation_id"]: {
                "owner": "original-reader", "deadline_at": expires_at, "escalation_ref": "original-escalation",
            }} if with_operation else {}
            compact(document, intent["scope"], cursor, policies)
        owner._review_transaction(retire, expires_at=expires_at)
    original_record = (
        recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires_at) if with_operation
        else TerminalRetentionStore(owner).inspect(intent["scope"], cursor, expires_at=expires_at)
    )
    if with_operation:
        assert original_record["actions"][0]["state"] == "unknown"
    before = owner.path.read_bytes()
    assert json.loads(before)["reviews"] == []
    with pytest.raises(ValueError, match="compact recovery" if with_operation else "populated terminal state"):
        terminal_transition.adopt(config)
    assert owner.path.read_bytes() == before
    after = (
        recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires_at) if with_operation
        else TerminalRetentionStore(owner).inspect(intent["scope"], cursor, expires_at=expires_at)
    )
    assert after == original_record


def test_terminal_conversation_after_cache_rebuild_keeps_original_watch_poll(
    configuration: Configuration,
) -> None:
    _, _, config = retained(configuration)
    assert config.workspaces is not None
    owner = ReplayStore(config.root, config.namespace, service_identity=config.store().service_identity)
    evidence = complete_evidence()
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    closed = build_generation(evidence)["generation"]
    cache = config.workspaces.cache(owner)
    cache.publish(closed, expected_current=None)
    ConversationStore(owner).observe_workspace(closed, expires_at=int(time.time()) + 60)
    cache.rebuild()
    assert not list(cache.generations.iterdir()) and not list(cache.targets.iterdir())
    scope = config.bindings[0].scope
    receipt = owner.execute(scope, "register", {
        "request_id": "original-after-rebuild", "issued_at": time.time(),
        "number": closed["scope"]["number"], "until": ("closed",), "deadline_at": time.time() + 60,
    }, expires_at=int(time.time()) + 60)
    before = owner.path.read_bytes()
    with pytest.raises(ValueError, match="retained conversations"):
        terminal_transition.adopt(config)
    assert owner.path.read_bytes() == before
    target = ObservationTarget(
        scope.account, scope.repo, closed["scope"]["number"], frozenset({receipt["registration_id"]}),
    )
    native = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "repo": scope.repo, "pr": closed["scope"]["number"], "provider": "azure-devops",
        "pr_state": "closed", "merged": False, "head_sha": closed["revision"]["head_sha"],
        "base_ref": "master", "mergeable": None, "checks_state": "success",
        "title": "Synthetic terminal PR", "draft": False,
        "reviews": [{"body": "synthetic native details"}],
    }
    result = owner.observe(
        target, decode_snapshot(native, scope.repo, target.number), expires_at=int(time.time()) + 60,
    )
    assert result["status"] == "observed"
    after = json.loads(owner.path.read_bytes())
    assert after["conversations"] == json.loads(before)["conversations"]
    assert "terminal_retention_version" not in after
    pending = owner.execute(scope, "pending", {}, expires_at=int(time.time()) + 60)
    assert pending["events"][0]["snapshot"]["reviews"] == native["reviews"]


def test_specialized_attention_owner_keeps_its_original_reader(configuration: Configuration) -> None:
    _, _, config = retained(configuration)
    owner = ReplayStore(config.root, config.namespace, service_identity=config.store().service_identity)
    attention = AttentionStore(owner)
    with SingleInstance(config.root, service="agent-pull-requests-watch") as lease:
        def current() -> None:
            assert lease.held
        attention.adopt(lifecycle_checkpoint=current, expires_at=int(time.time()) + 60)
    original = attention.snapshot(expires_at=int(time.time()) + 60)
    before = owner.path.read_bytes()
    with pytest.raises(ValueError, match="version-three"):
        terminal_transition.adopt(config)
    assert owner.path.read_bytes() == before
    assert attention.snapshot(expires_at=int(time.time()) + 60) == original
