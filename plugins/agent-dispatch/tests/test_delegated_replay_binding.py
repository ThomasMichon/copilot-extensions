"""Scoped durable intent stays distinct from legacy rows and changed selections."""

from __future__ import annotations

import asyncio
import copy
import sys
from types import SimpleNamespace

import pytest

from agent_dispatch.delegated_replay_consumer import create_replay_consumer
from agent_dispatch.delegated_replay_contract import SCOPED_PROTOCOL, validate_replay
from tests._delegation_helpers import prepare, task
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_delegated_replay_consumer import BINDING, Service, consumer, cycle, intent

SERVICE = {
    "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
    "namespace": "installation-a", "principal": "caller-a", "target": BINDING["target"],
    "consumer": "coordinator-a", "account": "account-a", "repo": BINDING["repo"],
}
SCOPED = {**BINDING, "protocol": SCOPED_PROTOCOL, "service_binding": SERVICE}

pytestmark = pytest.mark.contract("agent_dispatch.delegated_replay.binding")


def scoped_intent(queue, suffix="one"):
    spec = intent(queue, suffix)
    spec["replay"]["binding"] = copy.deepcopy(SCOPED)
    return spec


def test_legacy_and_scoped_rows_survive_restart_without_conversion_or_cross_adoption(tmp_path):
    database = tmp_path / "tasks.db"
    queue = TaskQueue(database)
    legacy_task, scoped_task = task(queue), task(queue)
    legacy, selected = intent(queue, "legacy"), scoped_intent(queue, "scoped")
    prepare(queue, legacy_task, legacy)
    prepare(queue, scoped_task, selected)
    queue = TaskQueue(database)
    service = Service(queue)
    owner = consumer(queue, service, SCOPED)
    assert not cycle(owner)["blocked"]
    assert set(service.records) == {"request:scoped"}
    assert queue.get_active_run_waiter(scoped_task)["delegation"]["replay"] == selected["replay"]
    old, = queue.list_replay_waiters(BINDING)
    assert old["delegation"]["replay"] == legacy["replay"] and not old["replay_attempted"]
    assert old["state"] == "preparing"
    assert not queue.list_run_waiter_wakes(legacy_task)
    # The explicitly selected legacy embedding still sees only its own row.
    assert not cycle(consumer(queue, service))["blocked"]
    assert set(service.records) == {"request:legacy", "request:scoped"}
    assert queue.get_active_run_waiter(legacy_task)["delegation"]["replay"] == legacy["replay"]


@pytest.mark.parametrize("field", list(SERVICE))
def test_each_changed_service_dimension_cannot_consume_original_rows(tmp_path, field):
    queue = TaskQueue(tmp_path / "tasks.db")
    original = scoped_intent(queue)
    task_id = task(queue)
    prepare(queue, task_id, original)
    changed = copy.deepcopy(SCOPED)
    changed["service_binding"][field] = "another"
    if field in ("target", "repo"):
        changed[field] = "another" if field == "target" else "another/project"
        changed["service_binding"][field] = changed[field]
    service = Service(queue)
    assert cycle(consumer(queue, service, changed))["checked"] == 0
    assert service.records == {} and len(queue.list_replay_waiters(SCOPED)) == 1
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("owner_binding,scoped", [(BINDING, True), (SCOPED, False)])
def test_prepare_cannot_downgrade_or_upgrade_an_existing_intent(tmp_path, owner_binding, scoped):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    service = Service(queue)
    spec = scoped_intent(queue) if scoped else intent(queue)
    with pytest.raises(ValueError, match="binding changed"):
        asyncio.run(consumer(queue, service, owner_binding).prepare(
            task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
            host="host", reason="waiting",
        ))
    assert queue.get(task_id).status == "started"
    assert not queue.list_pending_run_waiters() and not service.calls


@pytest.mark.parametrize("change", [
    {"protocol": "agent-pull-requests.watch/v1"},
    {"protocol": "unknown"},
    {"service_binding": None},
    {"service_binding": {}},
    {"service_binding": dict(SERVICE, extra=True)},
    {"service_binding": dict(SERVICE, account=1)},
    {"service_binding": dict(SERVICE, namespace="")},
    {"service_binding": dict(SERVICE, principal="caller\n")},
    {"service_binding": dict(SERVICE, namespace="x" * 257)},
    {"service_binding": dict(SERVICE, target="different")},
    {"service_binding": dict(SERVICE, repo="different/project")},
    {"extra": "unsupported"},
])
def test_scoped_variant_is_strict_without_importing_optional_packages(tmp_path, change):
    queue = TaskQueue(tmp_path / "tasks.db")
    replay = scoped_intent(queue)["replay"]
    replay["binding"].update(change)
    with pytest.raises(ValueError):
        validate_replay(replay, repo=BINDING["repo"], timeout=300)


@pytest.mark.parametrize("missing", ["protocol", "service_binding"])
def test_partial_scoped_variants_do_not_become_legacy(tmp_path, missing):
    queue = TaskQueue(tmp_path / "tasks.db")
    replay = scoped_intent(queue)["replay"]
    del replay["binding"][missing]
    with pytest.raises(ValueError, match="binding"):
        validate_replay(replay, repo=BINDING["repo"], timeout=300)


def test_selected_scope_and_returned_binding_are_deeply_detached(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    binding = copy.deepcopy(SCOPED)
    owner = consumer(queue, Service(queue), binding)
    binding["service_binding"]["namespace"] = "replaced-input"
    returned = owner.binding
    returned["service_binding"]["namespace"] = "replaced-result"
    assert owner.binding == SCOPED


def test_default_factory_keeps_older_pr_client_signature_and_no_scoped_import(tmp_path, monkeypatch):
    class LegacyClient:
        def __init__(self, caller, *, route_prefix, target, repo):
            assert (route_prefix, target, repo) == (
                BINDING["route_prefix"], BINDING["target"], BINDING["repo"],
            )

    monkeypatch.setitem(sys.modules, "agent_pull_requests.watch_replay_client", SimpleNamespace(
        ReplayClient=LegacyClient, ReplayRequest=lambda request_id, issued_at: (request_id, issued_at),
    ))
    monkeypatch.setitem(sys.modules, "agent_pull_requests.watch_replay_binding", None)
    queue = TaskQueue(tmp_path / "tasks.db")
    owner = create_replay_consumer(
        queue, SimpleNamespace(gateway=BINDING["gateway"]),
        **{k: v for k, v in BINDING.items() if k != "gateway"},
    )
    assert owner.binding == BINDING and isinstance(owner.client, LegacyClient)
    assert not queue.list_pending_run_waiters()
    with pytest.raises(ImportError):
        create_replay_consumer(
            queue, SimpleNamespace(gateway=BINDING["gateway"]), service_binding=SERVICE,
            **{k: v for k, v in BINDING.items() if k != "gateway"},
        )
    assert not queue.list_pending_run_waiters()
