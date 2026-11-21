"""Hot negative selectors do not grant eligibility or change existing custody."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import early_cli, ordinary_bridge
from agent_dispatch.action_controls import require_worker_admission
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.admission_holds import STATE_NAME, read_holds, selector, update_hold
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import WorkerAdmissionInhibited
from agent_dispatch.spawn_factories import make_headless_spawn, make_label_routed_spawn
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, record_native, task

api = controls.api
q = controls.q


def command(*args: str) -> None:
    assert early_cli.dispatch(["actions", *args]) == 0


def test_routed_agent_and_labels_rechecked_on_same_lane(
    q: TaskQueue, api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "policy"))
    review = task(q, labels=["review", "urgent"])
    regular = task(q, labels=["work"])
    attempts: list[str] = []

    def observe(_client: object, row: dict[str, str], _owner: str, **_kw: object) -> dict[str, bool]:
        attempts.append(row["id"])
        return {"reserved": False}

    monkeypatch.setattr(ordinary_bridge, "reserve_for_backend", observe)
    with controls.connected(api, []) as client:
        lane = Supervisor(client, supervisor_id="workers", repo="example", spawn_fn=make_label_routed_spawn(
            make_headless_spawn(agent="general"), overrides={"review": make_headless_spawn(agent="reviewer")},
        ))

        def poll(*expected: str) -> None:
            attempts.clear()
            assert lane.poll_once() == []
            assert sorted(attempts) == sorted(expected)

        command("hold", "--agent", "reviewer", "--reason", "maintenance")
        command("hold", "--label", "urgent", "--reason", "pause intake")
        command("hold", "--pool", "workers", "--reason", "drain")
        poll()
        command("resume", "--pool", "workers")
        poll(regular.id)
        command("resume", "--label", "urgent")
        poll(regular.id)
        command("resume", "--agent", "reviewer")
        poll(review.id, regular.id)
        command("hold", "--agent", "general", "--reason", "paused")
        poll(review.id)
        assert lane._spawn_attribute({"labels": ["review"], "agent": "spoof"}, "admission_agent", "") == "reviewer"


def test_queue_checks_selected_instance_without_adopting_or_releasing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "policy"))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    revision = publish(queue, pool(), pool("worker", "host-b", "linux"))
    a, b = task(queue, target_machine="host-a"), task(queue, target_machine="host-b")

    def place(task_id: str) -> bool:
        return queue.place_pool_task(task_id, reserved_by="allocator", catalog_revision=revision).reserved

    command("hold", "--instance", "worker", "host-a", "linux", "--reason", "drain a")
    with pytest.raises(WorkerAdmissionInhibited):
        place(a.id)  # No caller-supplied instance: the actual winner must still be checked.
    assert queue.latest_reservation(a.id) is None and queue.get(a.id) == a
    assert place(b.id)
    before = queue.latest_reservation(b.id)
    command("hold", "--kind", "finite", "--reason", "pause finite work")
    command("resume", "--instance", "worker", "host-a", "linux")
    with pytest.raises(WorkerAdmissionInhibited):
        place(a.id)
    assert not place(b.id) and queue.latest_reservation(b.id) == before
    command("inhibit", "worker-admission", "--reason", "global")
    command("resume", "--kind", "finite")
    with pytest.raises(WorkerAdmissionInhibited):
        place(a.id)
    command("clear", "worker-admission")
    assert place(a.id)


def test_malformed_policy_blocks_fresh_not_cleanup_or_exact_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    active, waiting = task(queue), task(queue)
    reservation, _ = queue.reserve_spawn(active.id)
    key = json.dumps(selector("label", ["x"]))
    data = "{" + key + ':{"reason":"stop"},' + key + ':{"reason":"duplicate"}}'
    path = tmp_path / STATE_NAME
    path.write_text(data, encoding="utf-8")
    assert early_cli.dispatch(["actions", "resume", "--label", "x"]) == 2
    assert path.read_text(encoding="utf-8") == data
    with pytest.raises(WorkerAdmissionInhibited):
        queue.reserve_spawn(waiting.id)
    assert queue.reserve_spawn(active.id) == (reservation, False)
    queue.fail_spawn(reservation.key, detail="existing cleanup")
    assert queue.latest_reservation(waiting.id) is None


def test_scoped_retry_and_fallback_keep_attempts_until_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    revision = publish(queue, pool())
    requested = task(queue, labels=["intake"])
    reservation = queue.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision).reservation
    record_native(queue, reservation)
    queue.record_pool_startup_outcome(reservation.key, task_id=requested.id, generation=1, startup_revision=1,
                                      native_id="native", outcome_kind="startup_failed", detail="original failed")
    handoff = task(queue, labels=["handoff", "intake"])
    with TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False,
                               verification_interval=0)) as api, controls.connected(api, []) as client:
        def retry() -> None:
            client.retry_pool_startup(requested.id, reservation.key, generation=1, startup_revision=1,
                                      native_id="native", worktree="worktree", machine="host-a", reserved_by="allocator")

        before = queue.pool_startup(requested.id), queue.get_reservation(reservation.key)
        command("hold", "--label", "intake", "--reason", "pause")
        with pytest.raises(DispatchError) as error:
            retry()
        assert error.value.status_code == 409
        assert (queue.pool_startup(requested.id), queue.get_reservation(reservation.key)) == before
        with pytest.raises(WorkerAdmissionInhibited):
            queue.claim_handoff_fallback(handoff.id)
        assert queue.get_handoff_fallback_attempt(handoff.id) is None
        command("resume", "--label", "intake")
        retry()
        assert queue.claim_handoff_fallback(handoff.id)
        command("hold", "--agent", "cli", "--reason", "no new launches")
        client.start_pool_task(requested.id, "host-a/worktree", owner_session_id="native", expected_generation=1)
        client.complete_pool_startup(requested.id, reservation.key, generation=1, startup_revision=2,
                                     native_id="native", worktree="worktree", machine="host-a", reserved_by="allocator",
                                     outcome_kind="startup_succeeded", detail="original caller completed")
        retained = queue.get_reservation(reservation.key)
        assert retained and retained.startup_complete


def test_concurrent_holds_and_cli_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    path = tmp_path / STATE_NAME
    keys = [selector("agent", [f"worker-{i}"]) for i in range(12)]
    with ThreadPoolExecutor(6) as writers:
        list(writers.map(lambda key: update_hold(path, key, "maintenance"), keys))
        list(writers.map(lambda key: update_hold(path, key, None), keys[::2]))
    command("holds", "--agent", "worker-1")
    assert json.loads(capsys.readouterr().out)["holds"] == [
        {"selector": ["agent", "worker-1"], "reason": "maintenance"},
    ]
    assert set(read_holds(path)) == set(keys[1::2])
    assert early_cli.dispatch(["actions", "hold", "--agent", "new", "--reason", "x" * 241]) == 2
    assert set(read_holds(path)) == set(keys[1::2])
