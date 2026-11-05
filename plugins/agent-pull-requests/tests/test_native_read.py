"""Actual finite native process and existing storage/Gateway, synthetic evidence."""

import asyncio
import copy
import json
import sys
import threading
import time

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

from test_review_workspace import evidence as evidence
from test_review_workspace import section

from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.native_read import NativeReadHandler, PROFILE
from agent_pull_requests.owner_read import (
    OwnerReadBudgetExceeded, OwnerReadUnavailable, PRINCIPAL_SEMANTICS, REQUEST_SCHEMA,
)
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
from agent_pull_requests.workspace_operations import NativeWorkspaceClient, WorkspaceOperations

CONTRACT = {
    "schema": "agent-worktrees.review-evidence-capabilities", "version": 1,
    "providers": ["azure-devops"], "evidence_versions": [1],
    "capabilities": [
        "finite_acquisition", "renewable_observe_credentials",
        "principal_binding", "credential_budget",
    ],
    "effects": "none", "submission": False, "source_attachment": False,
}
SCRIPT = """
import argparse,json,pathlib,sys,time
path=pathlib.Path(sys.argv[1])
value=json.loads(path.read_text())
operation=sys.argv[2]
with path.with_suffix('.calls').open('a') as calls:
    calls.write(operation+'\\n')
if operation=='capabilities':
    print(json.dumps(value['contract']))
else:
    assert operation=='read'
    parser=argparse.ArgumentParser(allow_abbrev=False)
    for name in ('repo','number','endpoint','expected-principal','credential-command','budget'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args(sys.argv[3:])
    assert args.repo=='project/repository' and int(args.number)==42
    assert args.expected_principal==value['evidence']['scope']['principal_id'] or value.get('mismatch')
    assert json.loads(args.credential_command)==[sys.executable]
    assert float(args.budget)>0
    path.with_suffix('.entered').write_text('entered')
    time.sleep(value.get('delay',0))
    if value.get('oversize'):
        print('x'*(8*1024*1024+1))
    elif value.get('malformed'):
        print('{"duplicate":1,"duplicate":2}')
    else:
        print(json.dumps(value['evidence']))
    path.with_suffix('.settled').write_text('settled')
"""


@pytest.fixture
def native(tmp_path, evidence):
    evidence["acquisition"] = {
        "source": PROFILE, "effects": "none", "principal_semantics": PRINCIPAL_SEMANTICS,
        "principal_source": "provider_connection_data", "snapshot_atomic": False,
        "duration_ms": 1, "credential_identity_checks": 2, "omissions": [],
    }
    script = tmp_path / "exporter.py"
    script.write_text(SCRIPT)
    path = tmp_path / "evidence.json"
    value = {"contract": copy.deepcopy(CONTRACT), "evidence": evidence}
    path.write_text(json.dumps(value))
    scope = dict(evidence["scope"])
    scope.pop("number")
    reader = NativeReadHandler(
        command=(sys.executable, "-I", str(script), str(path)),
        credential_command=(sys.executable,), repo="project/repository", scope=scope,
        binding_ref="selected", max_work_seconds=10,
    )
    request = {
        "schema": REQUEST_SCHEMA, "version": 1, "binding_ref": "selected",
        "number": 42, "budget_seconds": 10, "expected_principal": scope["principal_id"],
    }
    return reader, request, path, value


def update(native, **changes):
    _, _, path, value = native
    value.update(changes)
    path.write_text(json.dumps(value))


def operations(native, tmp_path):
    reader, _, _, _ = native
    store = ReplayStore(
        tmp_path / "owner", "namespace",
        service_identity=ReplayServiceIdentity("fleet", "gateway", "slot"),
    )
    store.provision(conversations=True)
    cache = GenerationCache(store.root, store.namespace, max_bytes=4 * 1024 * 1024,
                            max_generations=20, service_identity=store.service_identity)
    cache.provision()
    return WorkspaceOperations(
        store, cache, scope=dict(reader.scope), read_owner=reader, binding_ref="selected",
        checkpoint=lambda: None, max_work_seconds=10, conversations=True,
    )


def test_native_process_keeps_distinct_profile_and_partial_evidence(native):
    reader, request, path, value = native
    value["evidence"]["sections"]["policies"] = section([], complete=False, errors=["unavailable"])
    update(native)
    observed = reader.read(request)
    assert observed["acquisition"]["source"] == PROFILE
    assert "owner" not in observed
    assert observed["acquisition"]["credential_identity_checks"] == 2
    assert not observed["generation"]["complete"]
    assert observed["generation"]["sections"]["policies"]["errors"] == ["unavailable"]
    assert path.with_suffix(".calls").read_text().splitlines() == ["capabilities", "read"]


@pytest.mark.parametrize("change", ["prior", "missing", "version", "effects"])
def test_unsupported_executing_contract_never_issues_read(native, change):
    reader, request, path, value = native
    if change == "prior":
        value["contract"]["capabilities"].remove("principal_binding")
    elif change == "missing":
        value["contract"] = {}
    elif change == "version":
        value["contract"]["version"] = True
    else:
        value["contract"]["submission"] = True
    update(native)
    with pytest.raises(OwnerReadUnavailable, match="unsupported"):
        reader.read(request)
    assert path.with_suffix(".calls").read_text().splitlines() == ["capabilities"]


@pytest.mark.parametrize("field", ["endpoint", "project_id", "repository_id", "principal_id", "number"])
def test_native_scope_mismatch_cannot_publish(native, field):
    reader, request, _, value = native
    value["evidence"]["scope"][field] = 43 if field == "number" else "other"
    update(native, mismatch=True)
    with pytest.raises(OwnerReadUnavailable, match="mismatched"):
        reader.read(request)


@pytest.mark.parametrize("change", ["oversize", "malformed"])
def test_native_stream_and_duplicate_json_limits(native, change):
    reader, request, _, _ = native
    update(native, **{change: True})
    with pytest.raises(OwnerReadUnavailable):
        reader.read(request)


def test_request_cannot_override_trusted_owner_and_withdrawal_stops_next_stage(native):
    reader, request, path, _ = native
    with pytest.raises(ValueError):
        reader.read(dict(request, command=["untrusted"]))
    checks = []

    def check():
        checks.append(True)
        if path.with_suffix(".calls").exists():
            raise PermissionError("withdrawn")

    with pytest.raises(PermissionError):
        reader.read(request, request_checkpoint=check)
    assert path.with_suffix(".calls").read_text().splitlines() == ["capabilities"]


def test_native_timeout_never_publishes_and_actual_process_is_settled(native):
    reader, request, path, _ = native
    update(native, delay=2)
    with pytest.raises((OwnerReadUnavailable, OwnerReadBudgetExceeded)):
        reader.read(dict(request, budget_seconds=.3))
    # The canonical owned primitive's return is the cleanup boundary, not the
    # fixture's normal-return marker or an early future cancellation.
    assert not path.with_suffix(".settled").exists()


def test_unconfirmed_cleanup_keeps_exact_request_until_retry_settles(native, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_pull_requests import native_read
    reader, request, _, _ = native
    entered, release = threading.Event(), threading.Event()
    settled = []

    def cleanup(deadline):
        entered.set()
        if not release.wait(.01):
            raise TimeoutError
        settled.append(True)

    error = OwnedCleanupError(None, cleanup_retry=cleanup)

    def launch(*args, **kwargs):
        raise error

    monkeypatch.setattr(native_read, "run_caller_owned", launch)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reader.read, request)
        try:
            assert entered.wait(2)
            assert not future.done()
        finally:
            release.set()
        with pytest.raises(OwnerReadUnavailable, match="cleanup"):
            future.result(timeout=3)
    assert settled == [True]


@pytest.mark.parametrize("change", ["dialogue", "target"])
def test_native_load_sync_journal_cache_conflict_and_restart(native, tmp_path, change):
    workspace = operations(native, tmp_path)
    client = NativeWorkspaceClient(
        workspace, checkpoint=lambda: None, expires_at=lambda: int(time.time()) + 60,
    )
    first = client.exchange("load", {
        "number": 42, "budget_seconds": 10, "expected_current": None,
    })["result"]
    generation_id = first["descriptor"]["id"]
    assert client.exchange("capabilities", {})["read_acquisition"] == PROFILE
    assert "submit" not in client.exchange("capabilities", {})["operations"]
    assert client.materialize(42, generation_id)["id"] == generation_id
    _, _, path, value = native
    if change == "dialogue":
        value["evidence"]["sections"]["discussions"]["data"][0]["comments"][0]["content"] = "Changed"
    else:
        value["evidence"]["sections"]["pull_request"]["data"]["lastMergeTargetCommit"]["commitId"] = "f" * 40
    for item in value["evidence"]["sections"].values():
        item["observed_from"], item["observed_to"] = "2026-10-30T00:00:02Z", "2026-10-30T00:00:03Z"
    update(native)
    conflict = client.exchange("load", {
        "number": 42, "budget_seconds": 10, "expected_current": None,
    })["result"]
    assert conflict["conversation"] == {
        "status": "not_observed", "reason": "cache_publication_not_confirmed",
    }
    synced = client.exchange("sync", {
        "number": 42, "budget_seconds": 10, "previous_id": generation_id,
        "expected_current": generation_id,
    })["result"]
    assert ("discussions" if change == "dialogue" else "pull_request") in synced["comparison"]["changed_sections"]
    assert synced["conversation"]["cursor"]["sequence"] == 2
    # A new owner instance reads the same immutable generations and journal.
    restarted = WorkspaceOperations(
        ReplayStore(
            workspace.store.root, "namespace", service_identity=workspace.store.service_identity,
        ), workspace.cache,
        scope=dict(workspace.read_owner.scope), read_owner=workspace.read_owner,
        binding_ref="selected", checkpoint=lambda: None, max_work_seconds=10, conversations=True,
    )
    result = restarted.execute(
        "events", {"number": 42, "after": None, "limit": 16},
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )["result"]
    assert result["high_water"]["sequence"] == 2
    assert "Changed" not in json.dumps(result)
    assert path.with_suffix(".calls").read_text().splitlines() == [
        "capabilities", "read", "read", "read",
    ]


def test_deadline_covers_cache_commit_without_assuming_rollback(native, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_pull_requests import workspace_operations
    workspace = operations(native, tmp_path)
    clock = [0.0]
    monkeypatch.setattr(workspace_operations, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = GenerationCache.publish

    def publish(cache, generation, **kwargs):
        result = original(cache, generation, **kwargs)
        clock[0] = 11
        return result

    monkeypatch.setattr(GenerationCache, "publish", publish)
    with pytest.raises(OwnerReadBudgetExceeded):
        workspace.execute(
            "load", {"number": 42, "budget_seconds": 10, "expected_current": None},
            expires_at=int(time.time()) + 60, checkpoint=lambda: None,
        )
    scope = {**workspace.scope, "number": 42}
    assert workspace.cache.lookup(scope)["generation"] is not None
    result = workspace.execute(
        "events", {"number": 42, "after": None, "limit": 16},
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )["result"]
    assert result == {"status": "unavailable", "reason": "conversation_unobserved"}


def test_lost_journal_commit_response_reconciles_original_generation_and_cursor(
    native, tmp_path, monkeypatch,
):
    from agent_pull_requests import watch_replay_store
    workspace = operations(native, tmp_path)
    original = watch_replay_store._atomic_write_json

    def committed(path, value):
        original(path, value)
        raise OSError("synthetic response loss after durable write")

    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", committed)
    with pytest.raises(OSError, match="response loss"):
        workspace.execute(
            "load", {"number": 42, "budget_seconds": 10, "expected_current": None},
            expires_at=int(time.time()) + 60, checkpoint=lambda: None,
        )
    generation = workspace.cache.lookup({**workspace.scope, "number": 42})["generation"]
    assert generation is not None
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", original)
    result = workspace.execute(
        "events", {"number": 42, "after": None, "limit": 16},
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )["result"]
    assert result["high_water"] == {"epoch": 1, "sequence": 1}
    assert native[2].with_suffix(".calls").read_text().splitlines() == ["capabilities", "read"]


def test_request_withdrawal_retains_gateway_drain_until_native_settlement(native, tmp_path):
    workspace = operations(native, tmp_path)
    update(native, delay=.5)
    binding = ReplayBinding("fleet", "gateway", "slot", "namespace", "caller", "target",
                            "consumer", "account", "project/repository")
    gateway = WatchGateway(
        workspace.store, fleet="fleet", gateway="gateway", slot="slot",
        bindings=(WatchScopeBinding("caller", "target", ConsumerScope(
            "consumer", "account", "project/repository",
        )),), workspaces={("caller", "target"): workspace},
    )
    _, _, path, _ = native

    async def scenario():
        request = ServiceRequest(
            CallerContext("fleet", "gateway", "slot", "caller", int(time.time()) + 60),
            "POST", "/v2/review/target/load", (("content-type", "application/json"),),
            json.dumps({"binding": binding.document(), "request": {
                "number": 42, "budget_seconds": 10, "expected_current": None,
            }}).encode(),
        )
        task = asyncio.create_task(gateway.handle(request))
        deadline = time.monotonic() + 5
        while not path.with_suffix(".entered").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        request.lifetime.close()
        with pytest.raises(TimeoutError):
            await gateway.drain(timeout=.05)
        assert not task.done()
        assert (await task).status == 503
        await gateway.drain(timeout=2)
        assert path.with_suffix(".settled").exists()
        assert workspace.cache.lookup({**workspace.scope, "number": 42})["generation"] is None

    asyncio.run(scenario())
