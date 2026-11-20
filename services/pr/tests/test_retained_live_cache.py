"""Positive retained-cache reader continuity without observations or cleanup."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from single_instance_lease import SingleInstance

from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import CacheFull, GenerationCache
from agent_pull_requests.native_read import NativeReadHandler
from agent_pull_requests.review_workspace import SECTIONS, _digest, build_generation
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_retention import TerminalRetentionStore
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_replay_store import ReplayStoreInvalid
from agent_pull_requests.watch_storage import _atomic_write_json
from agent_pull_requests.workspace_operations import CHUNK_BYTES, WorkspaceOperations
from agent_pull_requests_service import terminal_transition
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import HostConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
    from services.pr.tests.test_terminal_transition import cache_bytes, command
else:
    from test_host import Configuration, configuration as configuration, workspace_config
    from test_terminal_transition import cache_bytes, command


def generation(
    index: int, *, terminal: bool = False, complete: bool = True,
    inner_offset: float = 0, padding: int = 0,
) -> dict[str, Any]:
    evidence = json.loads((
        Path(__file__).resolve().parents[3]
        / "plugins/agent-pull-requests/docs/review-evidence.example.json"
    ).read_text())
    pull = evidence["sections"]["pull_request"]
    pull["data"].update(title=f"retained generation {index}", status="completed" if terminal else "active")
    if padding:
        pull["data"]["description"] = "x" * padding
    start = datetime.fromisoformat(pull["observed_from"].replace("Z", "+00:00"))
    for name in SECTIONS:
        section = evidence["sections"].setdefault(name, {**pull, "data": []})
        section.update(
            complete=True, errors=[], observed_from=(start + timedelta(seconds=index * 3)).isoformat(),
            observed_to=(start + timedelta(seconds=index * 3 + 1)).isoformat(),
        )
    if not complete:
        evidence["sections"]["pull_request"]["complete"] = False
    if inner_offset:
        evidence["sections"]["policies"].update(
            observed_from=(start + timedelta(seconds=index * 3 + inner_offset)).isoformat(),
            observed_to=(start + timedelta(seconds=index * 3 + 1 - inner_offset)).isoformat(),
        )
    return cast(dict[str, Any], build_generation(evidence)["generation"])


def populated(
    configuration: Configuration, *, padding: int = 0,
) -> tuple[Path, dict[str, Any], HostConfig, list[dict[str, Any]]]:
    path, value = workspace_config(configuration)
    values = [
        generation(0, inner_offset=0.1, padding=padding),
        generation(0, inner_offset=0.2, padding=padding),
    ]
    value["workspaces"]["bindings"][0]["scope"] = {
        name: selected for name, selected in values[0]["scope"].items() if name != "number"
    }
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    baseline.provision()
    assert baseline.workspaces is not None
    store = baseline.store()
    cache = baseline.workspaces.cache(store)
    previous = None
    for selected in values:
        assert cache.publish(selected, expected_current=previous)["status"] == "published"
        ConversationStore(store).observe_workspace(selected, expires_at=int(time.time()) + 60)
        previous = selected["id"]
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value, baseline, values


def reader(config: HostConfig) -> WorkspaceOperations:
    assert config.workspaces is not None
    return config.workspaces.operations(config.store(), lambda: None, 60)[("caller-a", "repository-a")]


def materialized(operations: WorkspaceOperations, selected: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    payload = {"number": selected["scope"]["number"], "generation_id": selected["id"]}
    expires_at = int(time.time()) + 60
    descriptor = operations.execute(
        "lookup", payload, expires_at=expires_at, checkpoint=lambda: None,
    )["result"]["descriptor"]
    chunks = []
    for offset in range(0, descriptor["bytes"], CHUNK_BYTES):
        result = operations.execute(
            "chunk", {**payload, "offset": offset}, expires_at=expires_at, checkpoint=lambda: None,
        )["result"]
        chunks.append(base64.b64decode(result["data"]))
    return descriptor, b"".join(chunks)


def test_actual_cli_preserves_current_noncurrent_and_original_journal_after_restart(
    configuration: Configuration,
) -> None:
    path, _, baseline, values = populated(configuration)
    original = [materialized(reader(baseline), selected) for selected in values]
    store = baseline.store()
    scope = baseline.bindings[0].scope
    identity = {"request_id": "original-populated", "issued_at": time.time()}
    receipt = store.execute(scope, "register", {
        **identity, "number": values[0]["scope"]["number"],
        "until": ("head_changed",), "deadline_at": time.time() + 60,
    }, expires_at=int(time.time()) + 60)
    target = ObservationTarget(
        scope.account, scope.repo, values[0]["scope"]["number"], frozenset({receipt["registration_id"]}),
    )
    store.observe(target, PRSnapshot(head_sha="a" * 40), expires_at=int(time.time()) + 60)
    store.observe(target, PRSnapshot(head_sha="b" * 40), expires_at=int(time.time()) + 60)
    receipt = store.execute(scope, "inspect", identity, expires_at=int(time.time()) + 60)
    pending = store.execute(scope, "pending", {}, expires_at=int(time.time()) + 60)
    assert len(pending["events"]) == 1
    before, objects = json.loads(store.path.read_bytes()), cache_bytes(baseline)
    result = command(path)
    assert result.returncode == 0, result.stderr
    process = subprocess.run(
        [sys.executable, "-I", "-c", """
import base64, json, sys, time
from agent_pull_requests_service.config import load_config
from agent_pull_requests.workspace_operations import CHUNK_BYTES
config = load_config(sys.argv[1])
reader = config.workspaces.operations(config.store(), lambda: None, 60)[("caller-a", "repository-a")]
reader.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
output = []
for number, identity in json.loads(sys.argv[2]):
    payload = {"number": number, "generation_id": identity}
    def execute(operation, selected):
        return reader.execute(operation, selected, expires_at=int(time.time()) + 60, checkpoint=lambda: None)["result"]
    descriptor = execute("lookup", payload)["descriptor"]
    data = b"".join(base64.b64decode(execute("chunk", {**payload, "offset": offset})["data"])
                    for offset in range(0, descriptor["bytes"], CHUNK_BYTES))
    output.append([descriptor, base64.b64encode(data).decode("ascii")])
print(json.dumps(output))
""", str(path), json.dumps([(value["scope"]["number"], value["id"]) for value in values])],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == [
        [descriptor, base64.b64encode(data).decode("ascii")] for descriptor, data in original
    ]
    restarted = load_config(path)
    assert [materialized(reader(restarted), selected) for selected in values] == original
    assert reader(restarted).execute(
        "lookup", {"number": values[0]["scope"]["number"]},
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )["result"]["descriptor"] == original[1][0]
    after = json.loads(store.path.read_bytes())
    for field in ("replay", "reviews", "conversations"):
        assert after[field] == before[field]
    assert cache_bytes(restarted) == objects
    assert restarted.store().execute(scope, "inspect", identity, expires_at=int(time.time()) + 60) == receipt
    assert restarted.store().execute(scope, "pending", {}, expires_at=int(time.time()) + 60) == pending
    assert command(path).returncode == 0
    assert json.loads(store.path.read_bytes()) == after
    with pytest.raises(ReplayStoreInvalid):
        reader(baseline).validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)


def test_retained_generation_is_usable_for_selected_sync_comparison(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, _, values = populated(configuration)
    assert main(["adopt-terminal", "--config", str(path)]) == 0
    fresh = generation(2)
    reads = []

    def read(self: NativeReadHandler, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        reads.append(request)
        return {"generation": fresh, "acquisition": {"source": "synthetic-owner"}}

    monkeypatch.setattr(NativeReadHandler, "read", read)
    result = reader(load_config(path)).execute("sync", {
        "number": fresh["scope"]["number"], "previous_id": values[0]["id"],
        "expected_current": values[1]["id"], "budget_seconds": 30,
    }, expires_at=int(time.time()) + 60, checkpoint=lambda: None)["result"]
    assert len(reads) == 1
    assert result["comparison"]["generation_id"] == fresh["id"]
    assert result["publication"]["status"] == "published"
    assert materialized(reader(load_config(path)), values[0])[0]["id"] == values[0]["id"]


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("boundary", ["retention", "recovery", "cache"])
def test_populated_partial_adoption_and_lost_replace_retry_keep_original_bindings(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, uncertain: bool, boundary: str,
) -> None:
    from agent_pull_requests import watch_replay_store

    path, _, baseline, values = populated(configuration)
    before, objects = json.loads(baseline.store().path.read_bytes()), cache_bytes(baseline)
    with monkeypatch.context() as patch:
        if uncertain:
            original = watch_replay_store._atomic_write_json
            calls = [0]

            def lost(*args: Any, **kwargs: Any) -> None:
                original(*args, **kwargs)
                calls[0] += 1
                if calls[0] == {"retention": 1, "recovery": 2, "cache": 3}[boundary]:
                    raise OSError("synthetic lost adoption result")

            patch.setattr(watch_replay_store, "_atomic_write_json", lost)
        else:
            original_adopt = TerminalCacheRetirementStore.adopt

            def interrupted(*args: Any, **kwargs: Any) -> None:
                if boundary == "cache":
                    original_adopt(*args, **kwargs)
                raise OSError("synthetic next adoption interruption")

            selected = TerminalRecoveryStore if boundary == "retention" else TerminalCacheRetirementStore
            patch.setattr(selected, "adopt", interrupted)
        assert main(["adopt-terminal", "--config", str(path)]) == 1
    partial = json.loads(baseline.store().path.read_bytes())
    assert len(partial["terminal_retention"]["generations"]) == 2
    assert partial["conversations"] == before["conversations"]
    assert command(path).returncode == 0
    assert json.loads(baseline.store().path.read_bytes())["terminal_retention"] == partial["terminal_retention"]
    assert materialized(reader(load_config(path)), values[0])[0]["id"] == values[0]["id"]
    assert cache_bytes(baseline) == objects


def test_optional_mapping_adoption_preserves_live_cache_through_final_boundary(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests.review_comment_mapping import CommentMappingStore
    from agent_pull_requests.terminal_comment_mapping import TerminalCommentMappingStore

    path, value, baseline, values = populated(configuration)
    owner = baseline.store()
    expires_at = int(time.time()) + 60
    owner.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires_at)
    CommentMappingStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires_at)
    value["workspaces"]["terminal_mapping_reads"] = True
    path.write_text(json.dumps(value))
    before, objects = json.loads(owner.path.read_bytes()), cache_bytes(baseline)
    original = TerminalCommentMappingStore.adopt

    def lost(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise OSError("synthetic lost final mapping adoption result")

    with monkeypatch.context() as patch:
        patch.setattr(TerminalCommentMappingStore, "adopt", lost)
        assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert command(path).returncode == 0
    reopened = load_config(path)
    assert materialized(reader(reopened), values[0])[0]["id"] == values[0]["id"]
    after = json.loads(owner.path.read_bytes())
    assert after["terminal_comment_mapping_version"] == 1
    for field in ("replay", "reviews", "conversations", "review_intent_version", "review_comment_mapping_version"):
        assert after[field] == before[field]
    assert cache_bytes(reopened) == objects


def test_core_adoption_refuses_another_cache_owner_even_with_identical_generations(
    configuration: Configuration,
) -> None:
    path, _, baseline, values = populated(configuration)
    owner = load_config(path).store()
    foreign = GenerationCache(
        baseline.root.with_name("another-owner"), baseline.namespace,
        max_bytes=4 * 1024 * 1024, max_generations=20, service_identity=owner.service_identity,
    )
    foreign.provision()
    for selected in values:
        foreign.publish(selected, expected_current=None if selected is values[0] else values[0]["id"])
    before, objects = owner.path.read_bytes(), cache_bytes(baseline)
    assert baseline.workspaces is not None
    with SingleInstance(owner.root, service="agent-pull-requests-watch") as lease:
        def current() -> None:
            assert lease.held
        with pytest.raises(ValueError, match="original replay owner"):
            TerminalRetentionStore(owner).adopt(
                retained_cache=foreign, retained_targets=[{
                    "scope": dict(baseline.workspaces.readers[0][2].scope),
                    "account": baseline.bindings[0].scope.account, "repo": baseline.bindings[0].scope.repo,
                }], lifecycle_checkpoint=current, checkpoint=current, expires_at=int(time.time()) + 60,
            )
    assert owner.path.read_bytes() == before and cache_bytes(baseline) == objects


@pytest.mark.parametrize("failure", [
    "missing-conversation", "closed", "reopened", "dangling", "staged", "scope",
    "incomplete", "terminal", "corrupt",
])
def test_populated_incompatible_inputs_refuse_before_adoption(
    configuration: Configuration, failure: str,
) -> None:
    path, value, baseline, values = populated(configuration)
    store = baseline.store()
    assert baseline.workspaces is not None
    cache = baseline.workspaces.cache(store)
    expires_at = int(time.time()) + 60
    if failure == "missing-conversation":
        store._review_transaction(lambda doc: doc["conversations"]["records"].clear(), expires_at=expires_at)
    elif failure in ("closed", "reopened"):
        ConversationStore(store).observe_workspace(generation(2, terminal=True), expires_at=expires_at)
        if failure == "reopened":
            ConversationStore(store).observe_workspace(generation(3), expires_at=expires_at)
    elif failure == "dangling":
        (cache.generations / (values[1]["id"] + ".json")).unlink()
    elif failure == "staged":
        (cache.generations / ".unfinished.tmp").write_text("uncommitted")
    elif failure in ("incomplete", "terminal"):
        selected = generation(2, terminal=failure == "terminal", complete=failure != "incomplete")
        assert cache.publish(selected, expected_current=values[1]["id"])["status"] == "published"
    elif failure == "corrupt":
        (cache.generations / (values[0]["id"] + ".json")).write_text("{}")
    else:
        value["workspaces"]["bindings"][0]["scope"]["principal_id"] = "another-reader"
        path.write_text(json.dumps(value))
    before, objects = store.path.read_bytes(), cache_bytes(baseline)
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert store.path.read_bytes() == before and cache_bytes(baseline) == objects


def test_generation_scan_obeys_original_budget_before_visiting_later_objects(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, baseline, _ = populated(configuration)
    before, objects = baseline.store().path.read_bytes(), cache_bytes(baseline)
    clock = [0.0]
    reads: list[Path] = []
    original = GenerationCache._read

    def read(self: GenerationCache, selected: Path, limit: int) -> dict[str, Any]:
        result: dict[str, Any] = original(self, selected, limit)
        if selected.parent == self.generations:
            reads.append(selected)
            clock[0] = 1000.0
        return result

    monkeypatch.setattr(GenerationCache, "_read", read)
    monkeypatch.setattr(terminal_transition, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time,
    ))
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert len(reads) == 1
    assert baseline.store().path.read_bytes() == before and cache_bytes(baseline) == objects


@pytest.mark.parametrize("support", ["absent", "incompatible"])
def test_unknown_selected_core_keeps_populated_refusal_and_original_reads(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    from agent_pull_requests import retained_live_cache

    path, _, baseline, values = populated(configuration)
    original = materialized(reader(baseline), values[0])
    if support == "absent":
        monkeypatch.delattr(retained_live_cache, "contract")
    else:
        monkeypatch.setattr(retained_live_cache, "contract", lambda: {"protocol": "future-contract"})
    before, objects = baseline.store().path.read_bytes(), cache_bytes(baseline)
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert baseline.store().path.read_bytes() == before and cache_bytes(baseline) == objects
    assert materialized(reader(baseline), values[0]) == original


def test_null_current_pointer_preserves_cache_miss_and_explicit_retained_read(
    configuration: Configuration,
) -> None:
    path, _, baseline, values = populated(configuration)
    assert baseline.workspaces is not None
    cache = baseline.workspaces.cache(baseline.store())
    pointer = cache.targets / (_digest(values[0]["scope"]) + ".json")
    document = json.loads(pointer.read_bytes())
    document["generation_id"] = None
    _atomic_write_json(pointer, document)
    original = cache.lookup(values[0]["scope"])
    assert original["found"] is False
    objects = cache_bytes(baseline)
    assert command(path).returncode == 0
    assert cache.lookup(values[0]["scope"]) == original
    assert materialized(reader(load_config(path)), values[0])[0]["id"] == values[0]["id"]
    assert cache_bytes(baseline) == objects


@pytest.mark.parametrize("capacity", ["bytes", "generations"])
def test_populated_capacity_refuses_without_adoption_or_eviction(
    configuration: Configuration, capacity: str,
) -> None:
    path, value, baseline, _ = populated(configuration, padding=70000 if capacity == "bytes" else 0)
    assert baseline.workspaces is not None
    cache = baseline.workspaces.cache(baseline.store())
    descriptor = json.loads(cache.descriptor.read_bytes())
    name = "max_bytes" if capacity == "bytes" else "max_generations"
    limit = 65536 if capacity == "bytes" else 1
    descriptor["limits"][name] = limit
    value["workspaces"]["cache_bytes" if capacity == "bytes" else "cache_generations"] = limit
    _atomic_write_json(cache.descriptor, descriptor)
    path.write_text(json.dumps(value))
    before, objects = baseline.store().path.read_bytes(), cache_bytes(baseline)
    with pytest.raises(CacheFull):
        terminal_transition.adopt(load_config(path))
    assert baseline.store().path.read_bytes() == before and cache_bytes(baseline) == objects
