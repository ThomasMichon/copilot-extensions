"""Repository-qualified allocation survives acquisition, retry and restart."""

from dataclasses import asdict
import json
import subprocess
import argparse

import pytest

from agent_dispatch import embody, session_acquisition, spawn_factories
from agent_dispatch.queue import TaskQueue, TaskError
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.spawn_factories import SpawnPreparationRetained, SpawnPreparationPending
from agent_dispatch.workspace_binding import record_prepared
from tests.test_supervisor import QueueBackedClient

pytestmark = pytest.mark.contract("agent_dispatch.session_acquisition")


def test_owner_returned_project_is_kept(monkeypatch):
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(embody.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 0, json.dumps({
            "worktree": {"id": "workspace-a", "path": "workspace-path", "repo": "execution-repo"},
        }), ""))
    allocation = embody.create_worktree(
        project="execution-repo", task_id="task-a", reservation_key="reservation-a",
        attempt=1, driver="agent-dispatch", supervisor="supervisor-a",
    )
    assert allocation["project"] == "execution-repo"


@pytest.mark.parametrize("factory", [spawn_factories.make_embody_spawn])
def test_factory_acquires_owner_project_not_queue_lane(monkeypatch, factory):
    calls = []
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    def run(argv, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append(payload)
        return subprocess.CompletedProcess(argv, 1, json.dumps({"detail": {
            "code": "worktree_unknown", "effect": "not_started",
        }}), "")
    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    prepared = {"project": "execution-repo", "path": "workspace-path"}
    task = session_acquisition.spawn_snapshot(
        {"id": "task-a", "repo": "queue-repo", "generation": 1},
        {"key": "reservation-a", "conversation_retired": False},
        "workspace-a", prepared, "created", False,
    )
    factory()(task)
    assert calls[0]["project"] == "execution-repo"


def test_unkeyed_retry_preserves_project_path_and_session(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    first, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_allocation(first.key, "execution-repo")
    queue.record_spawn_worktree(
        first.key, "workspace-a", ownership="created", creating_host="host-a",
        driver="agent-dispatch", allocation_project="execution-repo", worktree_path="workspace-path",
    )
    queue.defer_spawn(first.key, detail="outcome unknown")
    restarted = TaskQueue(tmp_path / "queue.db")
    second, reserved = restarted.reserve_spawn(task.id)
    assert reserved
    assert (second.worktree, second.allocation_project, second.worktree_path) == (
        "workspace-a", "execution-repo", "workspace-path",
    )
    assert second.worktree_ownership == "reused"
    with pytest.raises(TaskError, match="identity"):
        restarted.record_spawn_worktree(
            second.key, "workspace-a", ownership="reused",
            allocation_project="queue-repo", worktree_path="workspace-path",
        )
    assert asdict(restarted.get_reservation(second.key))["allocation_project"] == "execution-repo"
    with pytest.raises(TaskError, match="identity"):
        restarted.record_spawn(second.key, worktree="workspace-other", session_handle="session-other")


@pytest.fixture
def owner_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr("agent_dispatch.bridge.registered_agent_project", lambda *_a, **_k: "execution-repo")
    monkeypatch.setattr(embody, "project_for_task", lambda _task: "execution-repo")
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "worktrees":
            assert argv[1:3] == ["--project", "execution-repo"]
            row = {"id": "workspace-a", "path": "workspace-path", "repo": "execution-repo"}
            data = {
                "worktree": row,
                "creation": {"version": 1, "status": "complete"},
            } if "create" in argv else {"worktrees": [row]}
        else:
            data = {"detail": {"code": "worktree_unknown", "effect": "not_started"}}
            payload = json.loads(kwargs["input"])
            assert payload["project"] == "execution-repo"
            assert payload["worktree_path"] == "workspace-path"
        return subprocess.CompletedProcess(argv, int(argv[0] == "bridge"), json.dumps(data), "")
    monkeypatch.setattr(embody.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("ambiguous", [False, True])
def test_supervised_retry_keeps_one_allocation_and_truthful_failure_budget(
    tmp_path, monkeypatch, owner_transport, ambiguous,
):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    client = QueueBackedClient(queue)
    spawn = spawn_factories.make_embody_spawn()
    supervisor = Supervisor(client, spawn_fn=spawn, repo=task.repo, machine="host-a")
    if ambiguous:
        original = embody.subprocess.run
        def pending(argv, **kwargs):
            if argv[0] == "bridge":
                return subprocess.CompletedProcess(argv, 1, '{"detail":{"code":"acquisition_pending"}}', "")
            return original(argv, **kwargs)
        monkeypatch.setattr(embody.subprocess, "run", pending)
    for attempt in range(3):
        reservation, _ = queue.reserve_spawn(task.id)
        prepared = supervisor._prepare_spawn_task(asdict(task), asdict(reservation))
        ok, outcome = spawn(prepared)
        assert not ok
        if ambiguous:
            assert outcome["deferred"]
            queue.defer_spawn(reservation.key, detail="pending")
        else:
            assert not outcome.get("deferred")
            assert outcome["preserve_workspace"]
            queue.fail_spawn(reservation.key, detail="refused")
        # Routing is mutable, allocation is not.
        spawn.allocation_project_for = lambda _task: "changed-routing"
    assert sum("create" in call for call in owner_transport) == 1
    counts = supervisor._failed_spawn_counts()
    assert counts.get(task.id, 0) == (0 if ambiguous else 3)
    assert supervisor._is_dead_lettered(asdict(task), counts) is (not ambiguous)


def test_one_shot_cli_uses_execution_project_and_reuses_retry(
    tmp_path, monkeypatch, owner_transport,
):
    from agent_dispatch import create_cli
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    client = QueueBackedClient(queue)
    monkeypatch.setattr(create_cli._core(), "_client", lambda _args: client)
    args = argparse.Namespace(
        spawn_backend="embody", spawn_agent="task-worker", run_async=True,
        verify_timeout=0, url=None, shared=False,
    )
    create_cli._spawn_worker_for(args, asdict(task))
    create_cli._spawn_worker_for(args, asdict(task))
    assert sum("create" in call for call in owner_transport) == 1
    assert queue.latest_reservation(task.id).allocation_project == "execution-repo"
    assert not any("conclude-disposable" in call for call in owner_transport)


@pytest.mark.parametrize("owner_project", [None, "queue-repo"])
def test_incompatible_owner_result_is_recorded_but_never_becomes_retry_authority(
    tmp_path, owner_project,
):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    reservation, _ = queue.reserve_spawn(task.id)
    prepared = {
        "worktree": "workspace-a", "path": "workspace-path", "ownership": "created",
        "project": owner_project, "requested_project": "execution-repo",
    }
    with pytest.raises(SpawnPreparationRetained):
        record_prepared(QueueBackedClient(queue), asdict(reservation), prepared, driver="agent-dispatch")
    recorded = queue.get_reservation(reservation.key)
    assert recorded.worktree == "workspace-a"
    assert recorded.allocation_project is None
    queue.defer_spawn(reservation.key, detail="identity requires repair")
    retry, _ = queue.reserve_spawn(task.id)
    with pytest.raises(SpawnPreparationRetained, match="no allocation provenance"):
        embody.prepare_reusable_worktree(
            asdict(task), asdict(retry), project="execution-repo",
            interface="acp", driver="agent-dispatch", supervisor="host-a",
        )


def test_old_coordinator_cannot_authorize_another_unrecordable_create(monkeypatch):
    monkeypatch.setattr(embody, "create_worktree", lambda **_kwargs: pytest.fail("must not create"))
    with pytest.raises(SpawnPreparationRetained, match="upgrade before allocation"):
        embody.prepare_reusable_worktree(
            {"id": "task-a", "repo": "queue-repo"}, {"key": "reservation-a", "attempt": 1},
            project="execution-repo", interface="acp", driver="agent-dispatch", supervisor="host-a",
        )

def test_pending_ground_creation_defers_supervisor_beyond_failure_budget(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "queue.db")
    clock = [1000.0]
    monkeypatch.setattr(queue, "_now", lambda now: clock[0] if now is None else now)
    task = queue.create(title="work", repo="queue-repo")
    client = QueueBackedClient(queue)
    requests = []
    spawned = []
    pending = [True]
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])

    def run(argv, **_kwargs):
        requests.append(argv)
        if pending[0]:
            return subprocess.CompletedProcess(argv, 3, json.dumps({
                "error": "original allocation still pending",
                "creation": {"version": 1, "status": "pending", "repeat_new": False},
            }), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "creation": {"version": 1, "status": "complete", "replayed": True},
            "worktree": {"id": "workspace-a", "path": "workspace-path", "repo": "execution-repo"},
        }), "")

    def spawn(snapshot):
        spawned.append(snapshot)
        return True, {"session": "cli-session", "worktree": snapshot["spawn_worktree"]}

    spawn.requires_reusable_worktree = True
    spawn.allocation_project = "execution-repo"
    spawn.allocation_interface = "cli"
    monkeypatch.setattr(embody.subprocess, "run", run)
    supervisor = Supervisor(client, spawn_fn=spawn, repo=task.repo, machine="host-a", max_attempts=3)
    for _ in range(6):
        assert supervisor.poll_once(now=clock[0]) == []
        assert supervisor._failed_spawn_counts().get(task.id, 0) == 0
        clock[0] += 120
    assert len(requests) == 6 and not spawned
    keys = {argv[argv.index("--creation-key") + 1] for argv in requests}
    assert len(keys) == 1
    assert not supervisor._is_dead_lettered(asdict(task), supervisor._failed_spawn_counts())
    pending[0] = False
    assert supervisor.poll_once(now=clock[0]) == [task.id]
    assert len(spawned) == 1
    assert spawned[0]["spawn_worktree"] == "workspace-a"
    assert requests[-1][requests[-1].index("--creation-key") + 1] in keys


def test_pending_ground_creation_defers_one_shot_without_spawning(tmp_path, monkeypatch):
    from agent_dispatch import create_cli
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    client = QueueBackedClient(queue)
    monkeypatch.setattr(create_cli._core(), "_client", lambda _args: client)
    monkeypatch.setattr(create_cli._core(), "_do_spawn", lambda *_a, **_kw: pytest.fail("pending must not spawn"))
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    requests = []
    monkeypatch.setattr(embody.subprocess, "run", lambda argv, **_kwargs:
        requests.append(argv) or subprocess.CompletedProcess(argv, 3, json.dumps({
            "error": "original allocation still pending",
            "creation": {"version": 1, "status": "pending", "repeat_new": False},
        }), ""))
    args = argparse.Namespace(spawn_backend="embody", url=None, shared=False)
    for _ in range(6):
        create_cli._spawn_worker_for(args, asdict(task))
    assert len(requests) == 6
    assert len({argv[argv.index("--creation-key") + 1] for argv in requests}) == 1
    supervisor = Supervisor(client, spawn_fn=lambda _task: pytest.fail("must not spawn"), repo=task.repo, machine="host-a")
    assert supervisor._failed_spawn_counts().get(task.id, 0) == 0


@pytest.mark.parametrize("status", ["pending", "conflict", "terminal"])
def test_only_typed_pending_creation_is_deferred(monkeypatch, status):
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(embody.subprocess, "run", lambda argv, **_kwargs:
        subprocess.CompletedProcess(argv, 3, json.dumps({
            "creation": {"version": 1, "status": status},
        }), ""))
    failure = SpawnPreparationPending if status == "pending" else embody.EmbodyUnavailable
    with pytest.raises(failure):
        embody.create_worktree(
            project="execution-repo", task_id="task-a", reservation_key="reservation-a",
            attempt=1, driver="agent-dispatch", supervisor="host-a", creation_key="logical-work",
        )


def test_creation_key_survives_unrecorded_response_and_restart(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    first, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_allocation(first.key, "execution-repo")
    calls = []
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "worktree": {"id": "workspace-a", "path": "workspace-path", "repo": "execution-repo"},
            "creation": {"version": 1, "status": "complete", "replayed": True},
        }), "")
    monkeypatch.setattr(embody.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        embody.prepare_reusable_worktree(
            asdict(task), asdict(first), project="execution-repo",
            interface="acp", driver="agent-dispatch", supervisor="spawner-a",
        )
    queue.fail_spawn(first.key, detail="transport interrupted")
    restarted = TaskQueue(tmp_path / "queue.db")
    retry, reserved = restarted.reserve_spawn(task.id)
    assert reserved and retry.attempt == 2
    assert retry.creation_key == first.creation_key
    assert retry.allocation_reservation_key == first.key
    assert retry.allocation_project == "execution-repo"
    result = embody.prepare_reusable_worktree(
        asdict(task), asdict(retry), project="changed-routing",
        interface="acp", driver="agent-dispatch", supervisor="spawner-b",
    )
    assert result["worktree"] == "workspace-a"
    for argv in calls:
        assert argv[argv.index("--creation-key") + 1] == first.creation_key
        assert argv[argv.index("--dispatch-reservation-key") + 1] == first.key
        assert argv[argv.index("--dispatch-attempt") + 1] == "1"

@pytest.mark.parametrize("recorded", [False, True])
def test_dedup_reemission_preserves_ground_allocation_task_provenance(tmp_path, monkeypatch, recorded):
    queue = TaskQueue(tmp_path / "queue.db")
    first_task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    first, _ = queue.reserve_spawn(first_task.id)
    client = QueueBackedClient(queue)
    calls = []
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])

    def run(argv, **_kwargs):
        calls.append(argv)
        row = {
            "id": "workspace-a", "path": "workspace-path", "repo": "execution-repo",
            "dispatch_attempt": {"task_id": first_task.id, "reservation_key": first.key, "attempt": 1},
        }
        if "create" in argv:
            assert argv[argv.index("--dispatch-task-id") + 1] == first_task.id
            if len(calls) == 1 and not recorded:
                raise subprocess.TimeoutExpired(argv, 1)
            payload = {"creation": {"version": 1, "status": "complete"}, "worktree": row}
        else:
            payload = {"worktrees": [row]}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(embody.subprocess, "run", run)
    if recorded:
        prepared = embody.prepare_reusable_worktree(
            asdict(first_task), asdict(first), project="execution-repo",
            interface="cli", driver="agent-dispatch", supervisor="first", allocation_client=client,
        )
        record_prepared(client, asdict(first), prepared, driver="agent-dispatch", creating_host="host-a")
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            embody.prepare_reusable_worktree(
                asdict(first_task), asdict(first), project="execution-repo",
                interface="cli", driver="agent-dispatch", supervisor="first", allocation_client=client,
            )
    queue.fail_spawn(first.key, detail="prior allocation retained")
    queue.claim_one("first-worker", task_id=first_task.id)
    queue.start(first_task.id, "first-worker")
    queue.complete(first_task.id, "first-worker")
    new_task = queue.create(title="next generation", repo="queue-repo", dedup_key="logical-work")
    assert new_task.id != first_task.id
    retry, _ = queue.reserve_spawn(new_task.id)
    assert retry.creation_key == first.creation_key and retry.allocation_reservation_key == first.key
    prepared = embody.prepare_reusable_worktree(
        asdict(new_task), asdict(retry), project="changed-routing",
        interface="cli", driver="agent-dispatch", supervisor="next", allocation_client=client,
    )
    assert prepared["worktree"] == "workspace-a" and prepared["project"] == "execution-repo"
    snapshot = session_acquisition.spawn_snapshot(
        asdict(new_task), asdict(retry), prepared["worktree"], prepared, "reused", False,
    )
    assert snapshot["id"] == new_task.id and snapshot["spawn_reservation_key"] == retry.key
    assert snapshot["spawn_allocation_reservation_key"] == first.key


def test_allocation_revision_is_explicit_and_disposal_qualified(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    first, _ = queue.reserve_spawn(task.id)
    with pytest.raises(TaskError, match="disposal"):
        queue.reserve_spawn(task.id, allocation_revision=2)
    queue.record_spawn_worktree(first.key, "workspace-a", ownership="created")
    queue.fail_spawn(first.key, detail="refused", conclusion_state="complete",
        conclusion_detail=json.dumps({"action": "primed"}))
    with pytest.raises(TaskError, match="disposal"):
        queue.reserve_spawn(task.id, allocation_revision=2)
    queue.record_spawn_conclusion(first.key, conclusion_state="complete",
        conclusion_detail=json.dumps({"action": "removed"}))
    retry, _ = queue.reserve_spawn(task.id, allocation_revision=2)
    assert retry.allocation_revision == 2 and retry.creation_key != first.creation_key
    assert retry.worktree is None and retry.allocation_reservation_key == retry.key
    with pytest.raises(TaskError, match="revision"):
        queue.reserve_spawn(task.id, allocation_revision=1)

def test_allocation_revision_does_not_regress_after_wall_clock_rollback(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    first, _ = queue.reserve_spawn(task.id, now=1000)
    queue.record_spawn_worktree(first.key, "workspace-a", ownership="created")
    queue.fail_spawn(first.key, now=1001, conclusion_state="complete",
                     conclusion_detail=json.dumps({"action": "removed"}))
    replacement, _ = queue.reserve_spawn(task.id, allocation_revision=2, now=100)
    queue.record_spawn_allocation(replacement.key, "execution-repo")
    queue.fail_spawn(replacement.key, now=101, detail="replacement response lost")
    retry, admitted = TaskQueue(tmp_path / "queue.db").reserve_spawn(task.id, now=102)
    assert admitted and retry.allocation_revision == 2
    assert retry.creation_key == replacement.creation_key != first.creation_key
    assert retry.allocation_reservation_key == replacement.key
    assert retry.allocation_project == "execution-repo" and retry.worktree is None


@pytest.mark.parametrize("exclusive_key", [None, "exclusive-work"])
def test_replacement_revision_lost_response_never_inherits_the_disposed_revision(
    tmp_path, monkeypatch, exclusive_key,
):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work", exclusive_key=exclusive_key)
    first, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(
        first.key, "workspace-a", ownership="created",
        allocation_project="execution-repo", worktree_path="path-a",
    )
    queue.fail_spawn(first.key, conclusion_state="complete",
        conclusion_detail=json.dumps({"action": "removed"}))
    replacement, _ = queue.reserve_spawn(task.id, allocation_revision=2)
    queue.record_spawn_allocation(replacement.key, "execution-repo")
    queue.fail_spawn(replacement.key, detail="replacement owner response lost")
    retry, _ = TaskQueue(tmp_path / "queue.db").reserve_spawn(task.id)
    assert retry.worktree is None
    assert retry.allocation_revision == 2
    assert retry.creation_key == replacement.creation_key != first.creation_key
    assert retry.allocation_reservation_key == replacement.key
    calls = []
    monkeypatch.setattr(embody, "resolve_worktree", lambda *_a, **_kw: pytest.fail("must not resolve revision 1"))
    monkeypatch.setattr(embody, "create_worktree", lambda **kw:
        calls.append(kw) or {"worktree": "workspace-b", "path": "path-b", "project": "execution-repo"})
    prepared = embody.prepare_reusable_worktree(
        asdict(task), asdict(retry), project="changed-routing",
        interface="acp", driver="agent-dispatch", supervisor="retry",
    )
    assert prepared["worktree"] == "workspace-b"
    assert calls[0]["creation_key"] == replacement.creation_key
    assert calls[0]["reservation_key"] == replacement.key
    assert calls[0]["attempt"] == replacement.attempt


def test_replacement_revision_accepts_actual_supervisor_disposal_envelope(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    first, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(first.key, "workspace-a", ownership="created")
    supervisor = Supervisor(QueueBackedClient(queue), spawn_fn=lambda _task: (False, {}), machine="host-a")
    state, envelope = supervisor._retired_cleanup_plan(
        asdict(queue.get_reservation(first.key)), target_missing=True,
    )
    assert state == "complete"
    assert envelope["worktree_cleanup"]["outcome"]["action"] == "already-removed"
    queue.fail_spawn(first.key, conclusion_state=state, conclusion_detail=json.dumps(envelope))
    replacement, reserved = queue.reserve_spawn(task.id, allocation_revision=2)
    assert reserved and replacement.worktree is None and replacement.allocation_revision == 2


def test_creation_identity_is_queue_lane_and_typed_logical_identity_scoped(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    one = queue.create(title="one", repo="queue-a", dedup_key="same")
    two = queue.create(title="two", repo="queue-b", dedup_key="same")
    first, _ = queue.reserve_spawn(one.id)
    second, _ = queue.reserve_spawn(two.id)
    assert first.creation_key != second.creation_key
    other_queue = TaskQueue(tmp_path / "other.db")
    other = other_queue.create(title="one", repo="queue-a", dedup_key="same")
    third, _ = other_queue.reserve_spawn(other.id)
    assert third.creation_key != first.creation_key
    with pytest.raises(TaskError, match="positive integer"):
        queue.reserve_spawn(one.id, allocation_revision=True)


def test_claim_generation_does_not_advance_allocation_revision(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    first, _ = queue.reserve_spawn(task.id)
    queue.defer_spawn(first.key, detail="pending")
    claimed = queue.claim_one("host/worktree", repo=task.repo, task_id=task.id)
    assert claimed is not None and claimed.generation > task.generation
    queue.yield_task(task.id, "host/worktree", release_spawn=False)
    retry, _ = queue.reserve_spawn(task.id)
    assert retry.creation_key == first.creation_key
    assert retry.allocation_revision == first.allocation_revision == 1


def test_legacy_unrecorded_allocation_cannot_mint_a_new_intent(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="queue-repo")
    first, _ = queue.reserve_spawn(task.id)
    queue.fail_spawn(first.key, detail="old response was lost")
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET creation_key=NULL WHERE key=?", (first.key,))
    with pytest.raises(TaskError, match="legacy allocation intent is unknown"):
        queue.reserve_spawn(task.id)
    assert len(queue.list_reservations(task_id=task.id)) == 1


@pytest.mark.parametrize("recorded", [None, "execution-repo"])
def test_absence_probe_uses_only_persisted_project(monkeypatch, recorded):
    calls = []
    supervisor = Supervisor(object(), spawn_fn=lambda _task: (False, {}), machine="host-a",
        worktree_directory_present_fn=lambda worktree, project: calls.append((worktree, project)) or False)
    res = {
        "worktree": "workspace-a", "worktree_ownership": "created", "creating_host": "host-a",
        "allocation_project": recorded,
    }
    result = supervisor._reserving_worktree_confirmed_absent(res, {"repo": "changed-routing"}, "workspace-a")
    assert result is bool(recorded)
    assert calls == ([("workspace-a", "execution-repo")] if recorded else [])
