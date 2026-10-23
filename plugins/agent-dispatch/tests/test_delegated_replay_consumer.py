from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from agent_dispatch.delegated_replay_consumer import ReplayConsumer
from agent_dispatch.delegated_waiter_contract import validate_delegation
from agent_dispatch.queue import TaskError
from agent_dispatch.run_waiter_recovery import recover_run_waiters
from tests._delegation_helpers import event, identity, prepare, task
from tests._helpers import RepoDefaultingQueue as TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.delegated_replay")

BINDING = {"gateway": "https://gateway.example", "route_prefix": "/services/pr",
           "target": "sample", "repo": "example/project", "caller_scope": "principal:one"}


def intent(queue, suffix="one"):
    return {**identity(queue, suffix), "replay": {
        "binding": copy.deepcopy(BINDING), "request_id": "request:" + suffix,
        "issued_at": 100.0, "deadline_at": 400.0,
    }}


class Service:
    def __init__(self, queue):
        self.queue = queue
        self.records, self.events, self.calls = {}, [], []
        self.fault, self.on_register, self.on_ack = None, None, None

    def call(self, name, request=None):
        self.calls.append((name, request))

    def fail(self, name):
        if self.fault == name:
            self.fault = None
            raise ValueError("outcome unknown")

    async def capabilities(self):
        self.call("capabilities")
        self.fail("capabilities")

    async def register(self, request, *, number, until, deadline_at):
        self.call("register", request)
        self.fail("before-register")
        key = request[0]
        self.records.setdefault(key, SimpleNamespace(
            status="active", registration_id="registration:" + key,
            deadline_at=deadline_at, event_id=None,
        ))
        if self.on_register:
            self.on_register()
        self.fail("after-register")
        return copy.copy(self.records[key])

    async def inspect(self, request):
        self.call("inspect", request)
        self.fail("inspect")
        return copy.copy(self.records.get(request[0], SimpleNamespace(status="uncertain")))

    async def acknowledge(self, request, *, registration_id, event_id):
        self.call("acknowledge", request)
        waiter = self.queue.find_replay_waiter(BINDING, registration_id)
        assert waiter["event_id"] == event_id and waiter["state"] == "retired"
        self.fail("before-ack")
        record = self.records[request[0]]
        assert record.registration_id == registration_id
        record.status, record.event_id = "acknowledged", event_id
        self.events = [e for e in self.events if e.event_id != event_id]
        if self.on_ack:
            self.on_ack()
        self.fail("after-ack")
        return copy.copy(record)

    async def cancel(self, request, *, registration_id):
        self.call("cancel", request)
        record = self.records[request[0]]
        assert record.registration_id == registration_id
        record.status, record.event_id = "cancelled", None
        self.events = [e for e in self.events if e.registration_id != registration_id]
        self.fail("after-cancel")
        return copy.copy(record)

    async def pending(self, *, limit):
        self.call("pending")
        assert limit == 16
        return SimpleNamespace(events=tuple(self.events[:limit]), more=len(self.events) > limit)

    def fire(self, spec, **changes):
        registration = self.records[spec["replay"]["request_id"]].registration_id
        payload = event({**spec, "subscriber_id": registration}, registration=registration)
        payload.update(changes)
        fired = SimpleNamespace(
            registration_id=registration, event_id=payload["event_id"],
            repo=spec["repo"], number=spec["number"], payload=lambda: copy.deepcopy(payload),
        )
        self.events.append(fired)
        return fired


def consumer(queue, service, binding=None):
    return ReplayConsumer(
        queue, binding=binding or BINDING, client=service,
        request_factory=lambda request_id, issued_at: (request_id, issued_at),
    )


def cycle(owner):
    return asyncio.run(owner.cycle())


def setup(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = intent(queue)
    prepare(queue, task_id, spec)
    service = Service(queue)
    return queue, task_id, spec, service, consumer(queue, service)


@pytest.mark.parametrize("fault", ["before-register", "after-register"])
def test_original_request_survives_registration_disconnect_and_restart(tmp_path, fault):
    queue, task_id, spec, service, owner = setup(tmp_path)
    service.fault = fault
    assert cycle(owner)["blocked"]
    assert [c[0] for c in service.calls] == ["capabilities", "register"]
    restored = TaskQueue(tmp_path / "tasks.db")
    assert restored.list_pending_run_waiters()[0]["replay_attempted"]
    owner = consumer(restored, service)
    assert not cycle(owner)["blocked"]
    requests = [r for name, r in service.calls if name == "register"]
    assert requests == [("request:one", 100.0)] * 2
    assert len(service.records) == 1
    assert restored.get_active_run_waiter(task_id)["delegation"]["replay"] == spec["replay"]
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("fault", [None, "before-ack", "after-ack", "cancelled-after-ack"])
def test_effect_commits_before_ack_and_restarts_without_duplicate_wake(tmp_path, fault):
    queue, task_id, spec, service, owner = setup(tmp_path)
    cycle(owner)
    service.fire(spec, snapshot={"native": "retained upstream"})
    if fault == "cancelled-after-ack":
        def cancel():
            raise asyncio.CancelledError
        service.on_ack = cancel
        with pytest.raises(asyncio.CancelledError):
            cycle(owner)
        service.on_ack = None
    else:
        service.fault = fault
        assert cycle(owner)["blocked"] == (fault is not None)
    assert len(queue.list_run_waiter_wakes(task_id)) == 1
    restored = TaskQueue(tmp_path / "tasks.db")
    owner = consumer(restored, service)
    assert not cycle(owner)["blocked"]
    assert not restored.list_replay_waiters(BINDING)
    assert len(restored.list_run_waiter_wakes(task_id)) == 1
    assert len(service.records) == 1


def test_supersession_during_remote_commit_cancels_only_original(tmp_path):
    queue, task_id, spec, service, owner = setup(tmp_path)
    service.on_register = lambda: prepare(queue, task_id, identity(queue, "next"))
    cycle(owner)
    service.on_register = None
    assert queue.list_pending_run_waiters()[0]["generation"] == 2
    cycle(owner)
    assert [c[0] for c in service.calls].count("cancel") == 1
    assert len(service.records) == 1
    assert service.records["request:one"].status == "cancelled"
    assert not queue.list_replay_waiters(BINDING)
    assert queue.list_pending_run_waiters()[0]["generation"] == 2


def test_cancelled_before_emission_never_registers(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    queue.cancel_preparing_run_waiter(task_id, generation=1, reason="cancelled")
    assert cycle(owner)["settled"] == 1
    assert service.records == {}
    assert [c[0] for c in service.calls] == ["capabilities", "pending"]


def test_cancel_commit_lost_receipt_is_reconciled_after_restart(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    cycle(owner)
    queue.supersede_run_waiter_with_wake(
        task_id, reason="cancelled", message="recheck", sender="test",
    )
    assert cycle(owner)["checked"] == 0
    service.fault = "after-cancel"
    assert cycle(owner)["blocked"]
    assert not cycle(consumer(TaskQueue(tmp_path / "tasks.db"), service))["blocked"]
    assert not queue.list_replay_waiters(BINDING)
    assert len(queue.list_run_waiter_wakes(task_id)) == 1


@pytest.mark.parametrize("armed", [False, True])
def test_legacy_recovery_never_probes_or_expires_service_wait(tmp_path, armed):
    queue, task_id, spec, service, owner = setup(tmp_path)
    if armed:
        cycle(owner)
    def forbidden(*args):
        pytest.fail("service replay is not local callback/PID recovery")
    queue._now = lambda _: 10_000.0
    counts = recover_run_waiters(
        queue, current_machine="host", process_exists=forbidden,
        start_token_for_pid=forbidden, delegated_owner_probe=forbidden,
    )
    assert counts == {"checked": 0, "live": 0, "unknown": 0, "recovered": 0}
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("field,value", [
    ("gateway", "https://another.example"), ("target", "another"),
    ("route_prefix", "/services/another"), ("caller_scope", "principal:two"),
])
def test_configuration_change_cannot_retarget_old_intent(tmp_path, field, value):
    queue, _, _, service, _ = setup(tmp_path)
    owner = consumer(queue, service, {**BINDING, field: value})
    cycle(owner)
    assert service.records == {}
    assert len(queue.list_replay_waiters(BINDING)) == 1


@pytest.mark.parametrize("change", [
    {"subscriber_id": "other"}, {"number": 8}, {"repo": "other/project"},
    {"transitions": ["checks_changed"]}, {"merged": "yes"},
])
def test_mismatching_event_never_wakes_or_acknowledges(tmp_path, change):
    queue, task_id, spec, service, owner = setup(tmp_path)
    cycle(owner)
    service.fire(spec, **change)
    assert cycle(owner)["blocked"]
    assert not queue.list_run_waiter_wakes(task_id)
    assert "acknowledge" not in [c[0] for c in service.calls]


def test_receipt_without_local_effect_cannot_fabricate_completion(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    cycle(owner)
    service.records["request:one"].status = "acknowledged"
    service.records["request:one"].event_id = "foreign"
    assert cycle(owner)["checked"] == 0
    assert cycle(owner)["blocked"]
    assert not queue.list_run_waiter_wakes(task_id)
    assert queue.list_replay_waiters(BINDING)


def test_bounded_scan_rotates_past_unknown_originals(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    for i in range(35):
        prepare(queue, task(queue), intent(queue, str(i)))
    service = Service(queue)
    owner = consumer(queue, service)
    assert cycle(owner)["checked"] == 32
    assert cycle(owner)["checked"] == 3
    assert cycle(owner)["checked"] == 0
    assert cycle(owner)["checked"] == 32
    assert [c[0] for c in service.calls].count("pending") == 4
    assert len(service.records) == 35


def test_original_request_cannot_be_reused_by_another_task(tmp_path):
    queue, _, spec, _, _ = setup(tmp_path)
    another = task(queue)
    with pytest.raises(TaskError, match="already bound"):
        prepare(queue, another, {**spec, "preparation_id": "different"})
    assert len(queue.list_pending_run_waiters()) == 1


def test_attempt_is_durable_before_network_and_settlement_is_atomic(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    def cancel():
        waiter = queue.list_pending_run_waiters()[0]
        assert waiter["replay_attempted"]
        queue.cancel_preparing_run_waiter(task_id, generation=1, reason="cancelled")
        assert not queue.replay_waiter_progress(waiter, settle_unattempted=True)["replay_settled"]
    service.on_register = cancel
    cycle(owner)
    assert service.records["request:one"].status == "cancelled"
    assert not queue.list_replay_waiters(BINDING)


@pytest.mark.parametrize("change", [
    {"issued_at": True}, {"deadline_at": float("inf")}, {"deadline_at": 401.0},
    {"extra": "unknown"}, {"request_id": "invalid/id"},
])
def test_invalid_remote_intent_is_rejected(tmp_path, change):
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = intent(queue)
    spec["replay"].update(change)
    with pytest.raises(ValueError):
        validate_delegation(spec)


def test_service_summary_uses_original_deadline(tmp_path):
    from agent_dispatch.queue_run_waiter_records import run_waiter_summary
    queue, task_id, _, service, owner = setup(tmp_path)
    cycle(owner)
    summary = run_waiter_summary(queue.get_active_run_waiter(task_id))
    assert summary["observation_deadline"] == 400.0
    assert summary["delivery"] == "service-replay"
    with pytest.raises(TaskError):
        queue.replay_waiter_progress(queue.get_active_run_waiter(task_id), settled=True)


def test_preparation_negotiates_before_suspending_task(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    service = Service(queue)
    owner = consumer(queue, service)
    service.fault = "capabilities"
    options = dict(delegation=intent(queue), worker_id="worker", resume_worktree="host/wt",
                   host="host", reason="waiting")
    with pytest.raises(ValueError):
        asyncio.run(owner.prepare(task_id, **options))
    assert queue.get(task_id).status == "started"
    assert not queue.list_pending_run_waiters()
    asyncio.run(owner.prepare(task_id, **options))
    assert queue.get(task_id).status == "suspended"
    assert len(queue.list_pending_run_waiters()) == 1
    assert service.records == {}


def test_failed_first_row_does_not_skip_rest_of_page_forever(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    for i in range(35):
        prepare(queue, task(queue), intent(queue, str(i)))
    service = Service(queue)
    owner = consumer(queue, service)
    for _ in range(35):
        service.fault = "before-register"
        assert cycle(owner)["checked"] == 1
    requests = [r[0] for name, r in service.calls if name == "register"]
    assert requests == ["request:" + str(i) for i in range(35)]
    assert len(service.calls) == 70
    assert service.records == {}


@pytest.mark.parametrize("status", ["uncertain", "conflict", "unavailable"])
def test_ambiguous_or_refused_receipt_keeps_original_without_later_emission(tmp_path, status):
    queue, task_id, _, service, owner = setup(tmp_path)
    async def register(*args, **kwargs):
        return SimpleNamespace(status=status)
    service.register = register
    assert cycle(owner)["blocked"]
    assert queue.list_pending_run_waiters()[0]["replay_attempted"]
    assert [c[0] for c in service.calls] == ["capabilities"]
    assert not queue.list_run_waiter_wakes(task_id)


def test_completed_event_cannot_wake_replacement_generation(tmp_path):
    queue, task_id, spec, service, owner = setup(tmp_path)
    cycle(owner)
    service.fire(spec)
    def replace():
        prepare(queue, task_id, identity(queue, "replacement"))
    service.on_ack = replace
    service.fault = "after-ack"
    assert cycle(owner)["blocked"]
    service.on_ack = None
    assert len(queue.list_run_waiter_wakes(task_id)) == 1
    assert not cycle(consumer(TaskQueue(tmp_path / "tasks.db"), service))["blocked"]
    assert not queue.list_replay_waiters(BINDING)
    assert queue.list_pending_run_waiters()[0]["generation"] == 2
    assert len(queue.list_run_waiter_wakes(task_id)) == 1


def test_stale_pending_event_cancels_without_completing_new_generation(tmp_path):
    queue, task_id, spec, service, owner = setup(tmp_path)
    cycle(owner)
    fired = service.fire(spec)
    prepare(queue, task_id, identity(queue, "replacement"))
    assert asyncio.run(owner._complete(fired))
    assert service.records["request:one"].status == "cancelled"
    assert queue.list_pending_run_waiters()[0]["generation"] == 2
    assert "acknowledge" not in [c[0] for c in service.calls]
    # Preparing the replacement journals custody release, never a completion wake.
    assert all(wake.sender != "agent-dispatch-hibernate" for wake in queue.list_run_waiter_wakes(task_id))


def test_binding_mutation_and_changed_caller_are_not_new_admission(tmp_path):
    queue, _, _, service, owner = setup(tmp_path)
    owner.binding["gateway"] = "https://another.example"
    assert owner.binding == BINDING
    owner._binding_current = lambda: False
    assert cycle(owner)["blocked"]
    assert service.calls == []


def test_duplicate_registration_is_refused_without_cross_task_effect(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    cycle(owner)
    another = task(queue)
    second = intent(queue, "second")
    prepare(queue, another, second)
    service.records["request:second"] = copy.copy(service.records["request:one"])
    assert cycle(owner)["blocked"]
    assert not queue.list_run_waiter_wakes(task_id)
    assert not queue.list_run_waiter_wakes(another)
    assert queue.list_pending_run_waiters()[0]["task_id"] == another


def test_oversized_pending_batch_is_refused(tmp_path):
    queue, _, _, service, owner = setup(tmp_path)
    async def pending(**kwargs):
        return SimpleNamespace(events=(object(),) * 17)
    service.pending = pending
    assert cycle(owner)["blocked"]
    assert queue.list_active_run_waiters()


def test_preparation_detaches_identity_before_awaiting_capabilities(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = intent(queue)
    service = Service(queue)
    async def capabilities():
        spec["replay"]["binding"]["gateway"] = "https://another.example"
    service.capabilities = capabilities
    owner = consumer(queue, service)
    asyncio.run(owner.prepare(
        task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
        host="host", reason="waiting",
    ))
    assert queue.list_pending_run_waiters()[0]["delegation"]["replay"]["binding"] == BINDING


def test_local_recovery_query_excludes_remote_projection_with_an_index(tmp_path):
    queue, task_id, _, service, owner = setup(tmp_path)
    assert queue.list_pending_run_waiters(local_only=True) == []
    cycle(owner)
    assert queue.list_active_run_waiters(local_only=True) == []
    assert queue.list_active_run_waiters()[0]["task_id"] == task_id
    with queue._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM run_waiters WHERE state = 'active' "
            "AND replay_scope IS NULL ORDER BY created_at ASC, id ASC",
        ).fetchall()
    details = " ".join(row["detail"] for row in plan)
    assert "idx_run_waiters_replay_local" in details
    assert "TEMP B-TREE" not in details


def test_worst_case_cycle_has_exact_82_call_bound(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    targets = []
    for i in range(48):
        task_id, spec = task(queue), intent(queue, str(i))
        prepare(queue, task_id, spec)
        targets.append((task_id, spec))
    service = Service(queue)
    owner = consumer(queue, service)
    cycle(owner)
    cycle(owner)
    for task_id, _ in targets[:32]:
        queue.supersede_run_waiter_with_wake(
            task_id, reason="cancelled", message="recheck", sender="test",
        )
    for _, spec in targets[32:]:
        service.fire(spec, event_id="event:" + spec["preparation_id"])
    service.calls.clear()
    result = cycle(consumer(queue, service))
    assert result == {"checked": 32, "events": 16, "settled": 48, "blocked": False}
    assert len(service.calls) == 82


def test_unresolved_final_row_yields_a_separately_admitted_pending_turn(tmp_path):
    queue, task_id, spec, service, owner = setup(tmp_path)
    final_task = task(queue)
    final_spec = intent(queue, "final")
    prepare(queue, final_task, final_spec)
    cycle(owner)
    service.fire(spec)
    queue.supersede_run_waiter_with_wake(
        final_task, reason="cancelled", message="recheck", sender="test",
    )
    service.records["request:final"].status = "uncertain"
    owner = consumer(queue, service)
    assert cycle(owner)["blocked"]
    assert not queue.list_run_waiter_wakes(task_id)
    result = cycle(owner)
    assert result == {"checked": 0, "events": 1, "settled": 1, "blocked": False}
    assert len(queue.list_run_waiter_wakes(task_id)) == 1
    assert queue.list_replay_waiters(BINDING)[0]["task_id"] == final_task
    assert len(service.records) == 2


@pytest.mark.parametrize("operation", ["acknowledge", "cancel"])
def test_unresolved_terminal_settlement_stops_before_later_registration(tmp_path, operation):
    queue, task_id, spec, service, owner = setup(tmp_path)
    cycle(owner)
    if operation == "acknowledge":
        service.fire(spec)
        service.fault = "before-ack"
        assert cycle(owner)["blocked"]
    else:
        queue.supersede_run_waiter_with_wake(
            task_id, reason="cancelled", message="recheck", sender="test",
        )
    prepare(queue, task(queue), intent(queue, "later"))
    async def unresolved(*args, **kwargs):
        return SimpleNamespace(status="uncertain")
    setattr(service, operation, unresolved)
    result = cycle(consumer(queue, service))
    assert result["checked"] == 1 and result["blocked"]
    assert "request:later" not in service.records
    assert queue.list_replay_waiters(BINDING)[0]["task_id"] == task_id


def test_duplicate_replay_transitions_refused_before_suspension(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = intent(queue)
    spec["until"] = ["merged", "merged"]
    service = Service(queue)
    with pytest.raises(ValueError, match="duplicate replay transitions"):
        asyncio.run(consumer(queue, service).prepare(
            task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
            host="host", reason="waiting",
        ))
    assert queue.get(task_id).status == "started"
    assert not queue.list_pending_run_waiters()
    assert service.calls == []
    local = identity(queue)
    local["until"] = ["merged", "merged"]
    assert validate_delegation(local) == local
