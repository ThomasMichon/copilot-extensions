"""Default outbox delivery addresses the captured hosted native conversation."""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge, remote_dispatch
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.events import EventBus
from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import (
    RunWaiterWakeOperation,
    Task,
    WakeOperation,
)
from agent_dispatch.queue_records import SpawnReservation, TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.wake import drain_wake_outbox
from tests._helpers import required
from tests._legacy_supervisor_history import before_retention_abandon
from tests.test_pool_placement_client import connected

OWNER = "host-a/worktree-a"
NATIVE = "native-a"
BRIDGE = "bridge-a"


class _HostedQueue(TaskQueue):
    client: DispatchClient


Hosted = tuple[_HostedQueue, str]


def _published_agent(queue: TaskQueue, workspace: Path) -> Task:
    task = queue.create("review", repo="queue-project", source="wake-test")
    assert isinstance(task, Task)
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="supervisor", driver="ordinary-bridge",
    )
    queue.record_spawn_allocation(reservation.key, "execution-project")
    info = {
        "session_id": BRIDGE, "ready": True, "status": "awaiting_first_prompt",
        "connection": {
            "native_session_id": NATIVE, "worktree_id": "worktree-a",
            "repo": "execution-project", "target_dir": str(workspace),
        },
    }
    record_created_agent(queue, reservation.key, task_generation=task.generation, agent_info=info)
    queue.record_spawn(
        reservation.key, session_handle=f"local-body:{BRIDGE}",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    return task


@pytest.fixture
def hosted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Hosted]:
    queue = _HostedQueue(tmp_path / "tasks.db")
    task = _published_agent(queue, tmp_path / "worktree-a")
    queue.claim_one(OWNER, task_id=task.id)
    queue.start(task.id, OWNER, owner_session_id=NATIVE)
    queue.suspend(task.id, OWNER, reason="external")
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["bridge-python", "-m", "agent_bridge"]
    )
    monkeypatch.setattr(
        bridge,
        "resume_steered_owner",
        lambda *_args, **_kwargs: pytest.fail(
            "hosted wake must not enter the represented CLI inbox"
        ),
    )
    # No coordinator lifespan: delivery tests drive their own outbox explicitly.
    api = TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False))
    try:
        with connected(api, []) as queue.client:
            yield queue, task.id
    finally:
        api.close()


def _capture(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, unknown_first: bool = False
) -> list[tuple[list[str], dict[str, object]]]:
    calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        code = 70 if unknown_first and len(calls) == 1 else returncode
        return subprocess.CompletedProcess(argv, code, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _expected(key: str) -> list[str]:
    return [
        "bridge-python", "-m", "agent_bridge", "--json", "send", BRIDGE,
        "--expected-native-session-id", NATIVE, "--idempotency-key", key,
        "--caller", f"agent-dispatch:{key}",
        "--prompt-file", "-", "--no-wait",
    ]


def test_native_wake_keeps_standard_send_key_and_literal_prompt(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, task_id = hosted
    calls = _capture(monkeypatch)
    reservation = required(queue.latest_reservation(task_id))
    assert (
        required(reservation.agent_info)["task_generation"]
        < required(queue.get(task_id)).generation
    )
    prompt = "Continue with the author's `choice` & (new head)."
    for _ in range(2):
        assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, prompt, "wake-1")
    assert [argv for argv, _ in calls] == [_expected("wake-1")] * 2
    assert all(kwargs["input"] == prompt for _, kwargs in calls)
    assert all("--queue" not in argv and "--force" not in argv for argv, _ in calls)


def test_native_wake_accepts_retained_resume_facts_and_cold_body(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, task_id = hosted
    original = required(queue.latest_reservation(task_id))
    info = {
        "fact_kind": "resumed", "session_id": BRIDGE, "acp_session_id": NATIVE,
        "durable_session_id": NATIVE, "worktree_id": "worktree-a",
        "project": "execution-project", "target_dir": original.worktree_path,
        "status": "idle", "read_only": False, "at_rest": True, "liveness": None,
    }
    monkeypatch.setattr(queue, "latest_reservation", lambda _id: replace(
        original, state="cold", agent_info=info,
    ))
    calls = _capture(monkeypatch)
    assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, None, "wake-1")
    assert calls[0][0] == _expected("wake-1")
    prompt = calls[0][1]["input"]
    assert isinstance(prompt, str) and f"agent-dispatch steer take {task_id} --all" in prompt


def test_stopped_ordinary_owner_keeps_its_claim_for_native_continuation(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id = hosted
    before = queue.resume(task_id, OWNER, reuse_session=True)
    reservation = required(queue.latest_reservation(task_id))
    counts = queue.reconcile_liveness(headless_local_verdict=lambda _session: "gone")
    current = required(queue.get(task_id))
    assert counts["requeued"] == 0
    assert counts["suspended"] == 1
    assert current.status == "suspended"
    assert (current.owner, current.owner_session_id, current.generation) == (
        OWNER, NATIVE, before.generation,
    )
    assert current.attempts == before.attempts
    assert required(queue.latest_reservation(task_id)) == reservation
    queue.resume(task_id, OWNER, wake_requested=True, wake_message="continue")
    [wake] = queue.list_wakes(task_id)
    calls = _capture(monkeypatch)
    assert deliver_owner_wake(
        queue, OWNER, NATIVE, task_id, wake.message, wake.id,
    )
    assert calls[0][0] == _expected(wake.id)


def test_native_wake_does_not_guess_local_machine_for_remote_owner(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, task_id = hosted
    calls = _capture(monkeypatch)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)
    with pytest.raises(RuntimeError, match="local machine identity"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


@pytest.mark.parametrize("body_kind", ["local", "fleet"])
@pytest.mark.parametrize("queued", [False, True])
def test_supervisor_does_not_release_stopped_ordinary_native_owner(
    hosted: Hosted, body_kind: str, queued: bool,
) -> None:

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    if queued:
        queue.yield_task(task_id, OWNER)
    before = required(queue.get(task_id))
    if body_kind == "fleet":
        with queue._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET session_handle=?, creating_host=? WHERE task_id=?",
                (f"fleet-body:host-a:{BRIDGE}", "host-a", task_id),
            )
    reservation = required(queue.latest_reservation(task_id))
    supervisor = Supervisor(
        queue.client, repo="queue-project",
        spawn_fn=lambda _task: pytest.fail("must retain the original native conversation"),
        local_body_verdict_fn=lambda _session: "gone",
        fleet_verdict_fn=lambda _host, _session: "gone",
        nudge=False,
    )

    assert supervisor.recover_gone() == 0
    current = required(queue.get(task_id))
    assert (current.owner, current.owner_session_id, current.generation) == (
        before.owner, before.owner_session_id, before.generation,
    )
    assert current.attempts == before.attempts
    assert current.status == before.status
    assert required(queue.latest_reservation(task_id)) == reservation


def test_stranded_cold_recovery_retains_original_ordinary_conversation(
    hosted: Hosted,
) -> None:

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    queue.record_cold(required(queue.latest_reservation(task_id)).key)
    before, reservation = required(queue.get(task_id)), required(queue.latest_reservation(task_id))
    supervisor = Supervisor(
        queue.client, repo="queue-project",
        spawn_fn=lambda _task: pytest.fail("must not replace a retained native conversation"),
        local_body_verdict_fn=lambda _session: "gone", nudge=False,
    )

    assert supervisor.recover_stranded_cold_reservations() == 0
    assert required(queue.get(task_id)) == before
    assert required(queue.latest_reservation(task_id)) == reservation


def test_gone_ordinary_native_with_pending_wake_stays_started_for_same_native_resume(
    hosted: Hosted,
) -> None:
    queue, task_id = hosted
    before = queue.resume(task_id, OWNER, wake_requested=True, wake_message="revise")
    [wake] = queue.list_wakes(task_id)
    assert before.status == "started" and before.wake_operation_id == wake.id

    counts = queue.reconcile_liveness(headless_local_verdict=lambda _session: "gone")

    assert counts["suspended"] == counts["requeued"] == 0
    current = required(queue.get(task_id))
    assert (current.status, current.owner_session_id, current.generation) == (
        "started", NATIVE, before.generation,
    )
    [still] = queue.list_wakes(task_id)
    assert still.id == wake.id and still.status == "pending"


def test_gone_verdict_observed_before_wake_delivery_does_not_suspend_resumed_native(
    hosted: Hosted,
) -> None:
    queue, task_id = hosted
    before = queue.resume(task_id, OWNER, wake_requested=True, wake_message="revise")

    def delivered_during_probe(_session: str) -> str:
        claimed = required(queue.claim_due_wake())
        assert claimed is not None and claimed.id == before.wake_operation_id
        queue.finish_wake(claimed.id, required(claimed.delivery_token), delivered=True)
        return "gone"

    counts = queue.reconcile_liveness(headless_local_verdict=delivered_during_probe)

    assert counts["gone"] == 1 and counts["suspended"] == counts["requeued"] == 0
    current = required(queue.get(task_id))
    assert (current.status, current.owner_session_id, current.generation) == (
        "started", NATIVE, before.generation,
    )
    [wake] = queue.list_wakes(task_id)
    assert wake.status == "delivered"


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_liveness_keeps_live_or_unknown_ordinary_owners(
    hosted: Hosted, verdict: str,
) -> None:
    queue, task_id = hosted
    before = queue.resume(task_id, OWNER, reuse_session=True)
    reservation = required(queue.latest_reservation(task_id))

    counts = queue.reconcile_liveness(headless_local_verdict=lambda _session: verdict)

    assert counts["suspended"] == counts["requeued"] == 0
    current = required(queue.get(task_id))
    assert (current.status, current.owner, current.owner_session_id, current.generation) == (
        before.status, OWNER, NATIVE, before.generation,
    )
    assert required(queue.latest_reservation(task_id)) == reservation


def test_liveness_does_not_erase_unfinished_ordinary_owner_binding(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _published_agent(queue, tmp_path / "worktree-a").id
    queue.claim_one(OWNER, task_id=task_id)
    with pytest.raises(TaskError, match="ordinary created-agent reservation"):
        queue.start(task_id, OWNER)
    before, reservation = required(queue.get(task_id)), required(queue.latest_reservation(task_id))

    counts = queue.reconcile_liveness(headless_local_verdict=lambda _session: "gone")

    assert counts["suspended"] == counts["requeued"] == 0
    current = required(queue.get(task_id))
    assert (current.status, current.owner, current.owner_session_id, current.generation) == (
        before.status, before.owner, before.owner_session_id, before.generation,
    )
    assert current.attempts == before.attempts
    assert required(queue.latest_reservation(task_id)) == reservation


@pytest.mark.parametrize("release", [False, True])
def test_liveness_preserves_claim_before_ordinary_spawn_publication(
    tmp_path: Path, release: bool
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", repo="queue-project", source="wake-test")
    assert isinstance(task, Task)
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="supervisor", driver="ordinary-bridge",
    )
    queue.record_spawn_allocation(reservation.key, "execution-project")
    record_created_agent(queue, reservation.key, task_generation=task.generation, agent_info={
        "session_id": BRIDGE, "ready": True, "status": "awaiting_first_prompt",
        "connection": {
            "native_session_id": NATIVE, "worktree_id": "worktree-a",
            "repo": "execution-project", "target_dir": str(tmp_path / "worktree-a"),
        },
    })
    queue.claim_one(OWNER, task_id=task.id)
    if release:
        queue.request_spawn_release(reservation.key, detail="release overlaps startup claim")
    before, retained = required(queue.get(task.id)), required(queue.latest_reservation(task.id))
    assert before.status == "claimed" and before.owner_session_id is None
    assert retained.state == ("releasing" if release else "reserving")
    assert queue.attachment_history(task.id) == []

    counts = queue.reconcile_liveness(resolver=lambda *_args: "gone")

    assert counts["gone"] == 1
    assert counts["suspended"] == counts["requeued"] == 0
    after = required(queue.get(task.id))
    assert (
        after.status,
        after.owner,
        after.owner_session_id,
        after.generation,
        after.attempts,
    ) == (
        before.status,
        before.owner,
        before.owner_session_id,
        before.generation,
        before.attempts,
    )
    assert required(queue.latest_reservation(task.id)) == retained


def test_original_native_can_normally_reclaim_a_retained_queued_task(
    hosted: Hosted,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    reservation = required(queue.latest_reservation(task_id))
    claimed = queue.claim_one(OWNER, task_id=task_id)
    assert isinstance(claimed, Task)

    started = queue.start(task_id, OWNER, owner_session_id=NATIVE)

    assert (started.owner, started.owner_session_id, started.generation) == (
        OWNER, NATIVE, claimed.generation,
    )
    assert required(queue.latest_reservation(task_id)) == reservation


@pytest.mark.parametrize(
    "change", ["native", "owner", "history", "history-native", "retired", "release"]
)
def test_retained_native_reclaim_does_not_bypass_identity_or_custody(
    hosted: Hosted,
    change: str,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    owner = "host-a/other-worktree" if change == "owner" else OWNER
    queue.claim_one(owner, task_id=task_id)
    with queue._connect() as conn:
        if change == "history":
            conn.execute("DELETE FROM task_attachments WHERE task_id=?", (task_id,))
        elif change == "history-native":
            conn.execute(
                "UPDATE task_attachments SET session_id='different-native' WHERE task_id=?",
                (task_id,),
            )
        elif change in {"retired", "release"}:
            sql = ("UPDATE spawn_reservations SET conversation_retired=1 WHERE task_id=?"
                   if change == "retired" else
                   "UPDATE spawn_reservations SET release_requested=1 WHERE task_id=?")
            conn.execute(sql, (task_id,))
    native = "different-native" if change == "native" else NATIVE
    with pytest.raises(TaskError, match="ordinary created-agent reservation"):
        queue.start(task_id, owner, owner_session_id=native)
    assert required(queue.get(task_id)).status == "claimed"


def _native_supervisor(queue: _HostedQueue, client: DispatchClient | None = None) -> Supervisor:
    return Supervisor(
        client if client is not None else queue.client, repo="queue-project", machine="host-a",
        spawn_fn=lambda _task: pytest.fail("must not replace the retained native conversation"),
        local_body_verdict_fn=lambda _session: "gone", nudge=False,
    )


def _assert_redrive_custody(before: SpawnReservation, after: SpawnReservation) -> None:
    assert after.startup_complete is True
    assert after.startup_revision == before.startup_revision + 1
    info = dict(required(after.agent_info))
    marker = info.pop("retained_redrive")
    assert info == before.agent_info
    assert marker["startup_revision"] == after.startup_revision
    assert replace(
        after, startup_revision=before.startup_revision,
        agent_info=before.agent_info, updated_at=before.updated_at,
    ) == before


@pytest.mark.parametrize("unresolved_wake", [False, True])
@pytest.mark.parametrize("body_state", ["spawned", "cold"])
@pytest.mark.parametrize("handle_kind", ["local", "fleet"])
def test_queued_ordinary_redrive_preserves_native_and_unresolved_wake(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, unresolved_wake: bool,
    body_state: str, handle_kind: str,
) -> None:
    queue, task_id = hosted
    queue.resume(
        task_id, OWNER, reuse_session=True, wake_requested=unresolved_wake,
        wake_message="original continuation",
    )
    if unresolved_wake:
        wake = required(queue.claim_due_wake())
        queue.finish_wake(
            wake.id, required(wake.delivery_token), delivered=False, max_attempts=1,
            error="acceptance unknown",
        )
    queue.yield_task(task_id, OWNER)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state=?,session_handle=? WHERE task_id=?",
            (
                body_state,
                f"local-body:{BRIDGE}"
                if handle_kind == "local"
                else f"fleet-body:host-a:{BRIDGE}",
                task_id,
            ),
        )
    before, reservation = required(queue.get(task_id)), required(queue.latest_reservation(task_id))

    calls = _capture(monkeypatch)
    supervisor = _native_supervisor(queue)

    held = unresolved_wake or body_state == "cold"
    assert supervisor.redrive_unclaimed_spawns() == (0 if held else 1)
    assert required(queue.get(task_id)) == before
    if held:
        assert required(queue.latest_reservation(task_id)) == reservation
    else:
        _assert_redrive_custody(reservation, required(queue.latest_reservation(task_id)))
    if held:
        assert calls == []
    if unresolved_wake:
        assert queue.list_wakes(task_id)[0].status == "failed"
    if not held:
        [call] = calls
        argv, kwargs = call
        assert argv[:3] == ["bridge-python", "-m", "agent_bridge"]
        assert argv[3:6] == ["--json", "send", BRIDGE]
        assert argv[argv.index("--expected-native-session-id") + 1] == NATIVE
        assert "--queue" not in argv and "--force" not in argv
        prompt = kwargs["input"]
        assert isinstance(prompt, str) and f"--owner-session-id {NATIVE}" in prompt
        assert f"claim --task {task_id}" in prompt


def test_queued_ordinary_redrive_unknown_retry_and_restart_keep_exact_key_and_prompt(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    calls = _capture(monkeypatch, unknown_first=True)
    supervisor = _native_supervisor(queue)
    assert supervisor.redrive_unclaimed_spawns() == 0
    pending = required(queue.latest_reservation(task_id))
    assert pending.startup_complete is False
    monkeypatch.setattr(
        "agent_dispatch.ordinary_native_redrive._prompt",
        lambda *_args: "A new supervisor must not regenerate an UNKNOWN invocation.",
    )
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["other-context", "-m", "agent_bridge"]
    )
    queue.claim_one(OWNER, task_id=task_id)
    queue.start(task_id, OWNER, owner_session_id=NATIVE)
    queue.yield_task(task_id, OWNER)
    assert supervisor.redrive_unclaimed_spawns() == 1
    assert supervisor.redrive_unclaimed_spawns() == 0
    assert _native_supervisor(queue).redrive_unclaimed_spawns() == 1
    assert len(calls) == 3
    first_argv, first_kwargs = calls[0]
    assert all(
        argv[3:] == first_argv[3:] and kw["input"] == first_kwargs["input"] for argv, kw in calls
    )
    assert calls[1][0][0] == "other-context"
    assert first_argv[first_argv.index("--caller") + 1] == (
        "agent-dispatch:" + required(queue.latest_reservation(task_id)).key + ":redrive"
    )
    assert first_argv[first_argv.index("--idempotency-key") + 1] == (
        required(queue.latest_reservation(task_id)).key + ":redrive"
    )


@pytest.mark.parametrize("provider", ["current", "legacy", "old", "future", "rollback"])
def test_queued_ordinary_redrive_negotiates_existing_reservation_read(
    hosted: Hosted,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import httpx
    from fastapi.testclient import TestClient

    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    reservation = required(queue.latest_reservation(task_id))
    calls = _capture(monkeypatch)
    with TestClient(create_app(queue, enable_mcp=False)) as api:
        def exchange(request: httpx.Request) -> httpx.Response:
            content = request.content
            if provider == "rollback" and request.url.path.endswith("/spawned"):
                body = json.loads(content)
                body.pop("ordinary_redrive", None)
                content = json.dumps(body).encode()
            result = api.request(request.method, str(request.url), content=content,
                                 headers=dict(request.headers))
            payload = result.json()
            if request.url.path == f"/spawn-reservations/{reservation.key}":
                assert payload["key"] == reservation.key
                assert payload["state"] == reservation.state
                assert payload["session_handle"] == reservation.session_handle
                assert payload["ordinary_redrive"]["version"] == 2
                if provider == "legacy":
                    payload.pop("ordinary_redrive")
                    payload.pop("ordinary_redrive_error")
                elif provider == "future":
                    payload["ordinary_redrive"]["version"] = 3
                elif provider == "old":
                    payload["ordinary_redrive"]["version"] = 1
            return httpx.Response(result.status_code, json=payload)

        with DispatchClient(
            "http://testserver", transport=httpx.MockTransport(exchange)
        ) as client:
            with caplog.at_level("INFO", logger="agent-dispatch.supervisor"):
                assert _native_supervisor(queue, client).redrive_unclaimed_spawns() == (
                    1 if provider == "current" else 0
                )
    assert len(calls) == (1 if provider == "current" else 0)
    if provider == "rollback":
        assert "pending startup publication not confirmed" in caplog.text
    elif provider != "current":
        assert "unsupported coordinator redrive facts" in caplog.text
    if provider == "current":
        _assert_redrive_custody(reservation, required(queue.latest_reservation(task_id)))
    elif provider != "rollback":
        assert required(queue.latest_reservation(task_id)) == reservation
    else:
        assert required(queue.latest_reservation(task_id)).startup_complete is False


@pytest.mark.parametrize("lost_completion", [False, True])
def test_queued_redrive_recovers_lost_startup_publication_response(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, lost_completion: bool
) -> None:
    import httpx
    from fastapi.testclient import TestClient

    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    original = required(queue.latest_reservation(task_id))
    calls = _capture(monkeypatch)
    lost = False
    with TestClient(create_app(queue, enable_mcp=False)) as api:
        def exchange(request: httpx.Request) -> httpx.Response:
            nonlocal lost
            result = api.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            if request.url.path.endswith("/spawned") and not lost:
                body = json.loads(request.content)
                if body["startup_complete"] is lost_completion:
                    assert result.status_code == 200
                    lost = True
                    return httpx.Response(503, json={"detail": "publication response lost"})
            return httpx.Response(result.status_code, json=result.json())

        with DispatchClient(
            "http://testserver", transport=httpx.MockTransport(exchange)
        ) as client:
            assert _native_supervisor(queue, client).redrive_unclaimed_spawns() == 0
            pending = required(queue.latest_reservation(task_id))
            assert pending.startup_revision == original.startup_revision + 1
            assert pending.startup_complete is lost_completion
            assert _native_supervisor(queue, client).redrive_unclaimed_spawns() == 1
    assert lost
    assert len(calls) == (2 if lost_completion else 1)
    if lost_completion:
        assert calls[0] == calls[1]
    after = required(queue.latest_reservation(task_id))
    assert after.startup_complete is True
    assert after.startup_revision == pending.startup_revision + int(lost_completion)
    assert required(after.agent_info)["retained_redrive"]["invocation"] == (
        required(pending.agent_info)["retained_redrive"]["invocation"]
    )


@pytest.mark.parametrize("returncode", [0, 70])
def test_queued_redrive_holds_startup_during_terminal_overlap(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, returncode: int,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    original = required(queue.latest_reservation(task_id))
    supervisor = _native_supervisor(queue)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        pending = required(queue.latest_reservation(task_id))
        assert pending.startup_complete is False
        assert pending.startup_revision == original.startup_revision + 1
        before_retention_abandon(queue, task_id, reason="terminal overlap control")
        current = required(queue.latest_reservation(task_id))
        assert supervisor._reservation_has_live_process(
            asdict(current), asdict(required(queue.get(task_id))),
        )
        with pytest.raises(TaskError, match="incomplete"):
            queue.fence_spawn_startup(current.key, startup_revision=current.startup_revision)
        queue.request_spawn_release(
            current.key, disposition="settled", detail="terminal overlap control"
        )
        return subprocess.CompletedProcess(argv, returncode, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert supervisor.redrive_unclaimed_spawns() == (1 if returncode == 0 else 0)
    after = required(queue.latest_reservation(task_id))
    assert after.startup_complete is (returncode == 0)
    assert after.release_requested is True
    assert after.state == "releasing"
    assert (after.key, after.worktree, after.session_handle) == (
        original.key, original.worktree, original.session_handle,
    )


@pytest.mark.parametrize("same_worktree", [False, True])
def test_queued_redrive_respects_released_sibling_exclusivity(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, same_worktree: bool,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    sibling = queue.create("other", repo="queue-project", exclusive_key="shared")
    assert isinstance(sibling, Task)
    other, _ = queue.reserve_spawn(sibling.id, reserved_by="other")
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state='cold',exclusive_released=1,worktree=?"
            " WHERE key=?",
            ("worktree-a" if same_worktree else "other-worktree", other.key),
        )
        conn.execute("UPDATE tasks SET exclusive_key='shared' WHERE id=?", (task_id,))
        conn.execute(
            "UPDATE spawn_reservations SET exclusive_key='shared' WHERE task_id=?", (task_id,)
        )
    calls = _capture(monkeypatch)
    assert _native_supervisor(queue).redrive_unclaimed_spawns() == (0 if same_worktree else 1)
    assert len(calls) == (0 if same_worktree else 1)


@pytest.mark.parametrize("change", ["generic", "revision", "prompt", "native"])
def test_queued_redrive_completion_requires_its_exact_operation(
    hosted: Hosted, change: str
) -> None:
    from agent_dispatch.ordinary_agent_storage import reservation_native_facts

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    original = required(queue.latest_reservation(task_id))
    facts = reservation_native_facts(queue, original.key)["ordinary_redrive"]
    kwargs = dict(
        session_handle=original.session_handle, worktree=original.worktree,
        ordinary_redrive=facts,
    )
    pending = queue.record_spawn(
        original.key, startup_complete=False, startup_revision=original.startup_revision, **kwargs,
    )
    bad = json.loads(json.dumps(kwargs))
    revision = pending.startup_revision
    if change == "generic":
        bad.pop("ordinary_redrive")
    elif change == "revision":
        revision -= 1
    else:
        field = "prompt" if change == "prompt" else "native_session_id"
        bad["ordinary_redrive"]["invocation"][field] = "changed"
    with pytest.raises(TaskError, match=r"startup|operation"):
        queue.record_spawn(
            original.key, startup_complete=True, startup_revision=revision, **bad,
        )
    assert required(queue.latest_reservation(task_id)) == pending
    completed = queue.record_spawn(
        original.key, startup_complete=True, startup_revision=pending.startup_revision, **kwargs,
    )
    _assert_redrive_custody(original, completed)


@pytest.mark.parametrize("change", ["generation", "release", "wake", "unrelated-startup"])
def test_queued_redrive_admission_rechecks_snapshot_under_startup_lock(
    hosted: Hosted, change: str
) -> None:
    from agent_dispatch.ordinary_agent_storage import reservation_native_facts

    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    original = required(queue.latest_reservation(task_id))
    facts = reservation_native_facts(queue, original.key)["ordinary_redrive"]
    if change == "release":
        queue.request_spawn_release(original.key)
    elif change == "unrelated-startup":
        queue.record_spawn(
            original.key, startup_complete=False, startup_revision=original.startup_revision,
        )
    elif change == "wake":
        queue.claim_one(OWNER, task_id=task_id)
        queue.start(task_id, OWNER, owner_session_id=NATIVE)
        queue.suspend(task_id, OWNER, reason="external")
        queue.resume(task_id, OWNER, reuse_session=True, wake_requested=True, wake_message="held")
        queue.yield_task(task_id, OWNER)
    else:
        queue.claim_one(OWNER, task_id=task_id)
        queue.yield_task(task_id, OWNER)
    before = required(queue.latest_reservation(task_id))
    with pytest.raises(TaskError, match="redrive"):
        queue.record_spawn(
            original.key,
            session_handle=original.session_handle,
            worktree=original.worktree,
            startup_complete=False,
            startup_revision=original.startup_revision,
            ordinary_redrive=facts,
        )
    assert required(queue.latest_reservation(task_id)) == before


@pytest.mark.parametrize("change", ["marker", "revision", "caller", "target"])
def test_queued_redrive_unknown_requires_its_frozen_pending_operation(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    calls = _capture(monkeypatch, returncode=70)
    assert _native_supervisor(queue).redrive_unclaimed_spawns() == 0
    pending = required(queue.latest_reservation(task_id))
    info = dict(required(pending.agent_info))
    if change == "marker":
        info.pop("retained_redrive")
    elif change == "revision":
        info["retained_redrive"]["startup_revision"] -= 1
    elif change == "caller":
        info["retained_redrive"]["invocation"]["caller"] = "another-caller"
    else:
        info["retained_redrive"]["invocation"]["native_session_id"] = "another-native"
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=? WHERE key=?",
            (json.dumps(info), pending.key),
        )
    assert _native_supervisor(queue).redrive_unclaimed_spawns() == 0
    assert len(calls) == 1
    assert required(queue.latest_reservation(task_id)).startup_complete is False


def test_completed_redrive_allows_later_normal_cold_resume(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    _capture(monkeypatch)
    supervisor = _native_supervisor(queue)
    assert supervisor.redrive_unclaimed_spawns() == 1
    completed = required(queue.latest_reservation(task_id))
    frozen = required(completed.agent_info)["retained_redrive"]
    queue.claim_one(OWNER, task_id=task_id)
    queue.start(task_id, OWNER, owner_session_id=NATIVE)
    queue.suspend(task_id, OWNER, reason="external")
    cooling = queue.claim_spawn_cooling(
        completed.key, startup_revision=completed.startup_revision,
        session_handle=required(completed.session_handle), state="spawned",
    )
    queue.record_cold(
        completed.key,
        startup_revision=cooling.startup_revision,
        cooling_token=cooling.cooling_token,
    )
    queue.submit_steer(task_id, fields={"continue": True}, sender="producer")
    resumed = []
    supervisor.local_body_target_dir_fn = lambda _sid: str(tmp_path)
    def resume(sid: str, _prompt: str) -> bool:
        resumed.append(sid)
        return True

    supervisor.local_resume_fn = resume
    assert supervisor.release_resumed_cold_tasks() == 1
    after = required(queue.latest_reservation(task_id))
    assert resumed == [BRIDGE]
    assert after.startup_complete is True
    assert after.startup_revision == completed.startup_revision + 1
    assert required(after.agent_info)["retained_redrive"] == frozen
    assert required(queue.get(task_id)).owner_session_id == NATIVE
    queue.take_steer(task_id, OWNER, all_pending=True)
    queue.suspend(task_id, OWNER, reason="external")
    assert queue.claim_spawn_cooling(
        after.key, startup_revision=after.startup_revision,
        session_handle=required(after.session_handle), state="spawned",
    ).cooling_token is not None


@pytest.mark.parametrize("change", [
    "native-history", "missing-history", "host", "handle", "retired", "release",
    "cooling", "hold", "qualified", "waiter-failed", "waiter-unknown",
])
def test_queued_ordinary_redrive_refuses_changed_custody(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    queue, task_id = hosted
    if change.startswith("waiter-"):
        waiter = queue.register_run_waiter(
            task_id, pid=101, host="host-a", start_token="token",
            resume_worktree=OWNER, command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task_id, generation=waiter["generation"], reason="external",
            message="original continuation", sender="recovery",
        )
    queue.resume(task_id, OWNER, reuse_session=True)
    queue.yield_task(task_id, OWNER)
    with queue._connect() as conn:
        if change == "native-history":
            conn.execute(
                "UPDATE task_attachments SET session_id='different-native' WHERE task_id=?",
                (task_id,),
            )
        elif change == "missing-history":
            conn.execute("DELETE FROM task_attachments WHERE task_id=?", (task_id,))
        elif change == "hold":
            conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task_id,))
        elif change == "qualified":
            conn.execute(
                "INSERT INTO execution_scope_bindings VALUES (?,?,?,?,?)",
                ("qualified", "queue-project", "wake-test", "baseline", 1.0),
            )
        elif change.startswith("waiter-"):
            conn.execute(
                "UPDATE run_waiter_wakes SET attempts=1,status=?,execution_send_state=?"
                " WHERE task_id=?",
                (
                    "failed" if change == "waiter-failed" else "superseded",
                    None if change == "waiter-failed" else "unknown",
                    task_id,
                ),
            )
        else:
            sql, value = {
                "host": (
                    "UPDATE spawn_reservations SET creating_host=? WHERE task_id=?",
                    "other-host",
                ),
                "handle": (
                    "UPDATE spawn_reservations SET session_handle=? WHERE task_id=?",
                    "local-body:other-bridge",
                ),
                "retired": (
                    "UPDATE spawn_reservations SET conversation_retired=? WHERE task_id=?",
                    1,
                ),
                "release": (
                    "UPDATE spawn_reservations SET release_requested=? WHERE task_id=?",
                    1,
                ),
                "cooling": (
                    "UPDATE spawn_reservations SET cooling_token=? WHERE task_id=?",
                    "unsettled-stop",
                ),
            }[change]
            conn.execute(sql, (value, task_id))
    before, reservation = required(queue.get(task_id)), required(queue.latest_reservation(task_id))
    calls = _capture(monkeypatch)
    assert _native_supervisor(queue).redrive_unclaimed_spawns() == 0
    assert calls == []
    assert required(queue.get(task_id)) == before
    assert required(queue.latest_reservation(task_id)) == reservation


def test_native_wake_fleet_uses_same_standard_command_over_ssh(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, task_id = hosted
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET session_handle=?, creating_host=? WHERE task_id=?",
            (f"fleet-body:host-a:{BRIDGE}", "host-a", task_id),
        )
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "coordinator")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    calls = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", run)
    assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    argv, kwargs = calls[0]
    assert argv[:6] == ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "host-a"]
    assert shlex.split(argv[6]) == ["agent-bridge", *_expected("wake-1")[3:]]
    assert kwargs["input"] == "continue"


class _ChangedFacts(TypedDict, total=False):
    session_handle: str | None
    driver: str | None
    conversation_retired: bool
    release_requested: bool
    conclusion_state: str
    cooling_token: str
    exclusive_key: str
    exclusive_released: bool
    state: str
    agent_info: None
    worktree: str
    allocation_project: str
    creating_host: str


@pytest.mark.parametrize("changes", [
    {"session_handle": "local-body:other"},
    {"session_handle": "local-body:"},
    {"session_handle": None},
    {"driver": None, "session_handle": None},
    {"session_handle": "fleet-body:other-host:bridge-a"},
    {"conversation_retired": True},
    {"release_requested": True},
    {"conclusion_state": "pending"},
    {"cooling_token": "unsettled-stop"},
    {"exclusive_key": "review-slot", "exclusive_released": True},
    {"state": "failed"},
    {"agent_info": None},
    {"worktree": "different"},
    {"allocation_project": "different"},
    {"creating_host": "different"},
])
def test_invalid_hosted_facts_never_fall_back_to_cli(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, changes: _ChangedFacts
) -> None:
    queue, task_id = hosted
    original = required(queue.latest_reservation(task_id))
    monkeypatch.setattr(queue, "latest_reservation", lambda _id: replace(original, **changes))
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError, match="hosted wake"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


@pytest.mark.parametrize("mutation", ["native", "worktree", "bridge", "malformed"])
def test_conflicting_agent_identity_is_refused(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    queue, task_id = hosted
    original = required(queue.latest_reservation(task_id))
    info = json.loads(json.dumps(required(original.agent_info)))
    if mutation == "native":
        info["connection"]["native_session_id"] = "other"
    elif mutation == "worktree":
        info["connection"]["worktree_id"] = "other"
    elif mutation == "bridge":
        info["session_id"] = "other"
    elif mutation == "malformed":
        info["connection"] = {}
    monkeypatch.setattr(
        queue, "latest_reservation", lambda _id: replace(original, agent_info=info)
    )
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


@pytest.mark.parametrize("returncode", [1, 2, 69, 70])
def test_native_refusal_or_unknown_never_uses_another_transport(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    queue, task_id = hosted
    calls = _capture(monkeypatch, returncode=returncode)
    assert not deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert [argv for argv, _ in calls] == [_expected("wake-1")]


@pytest.mark.parametrize("kind", ["owner", "waiter"])
@pytest.mark.parametrize("unknown_first", [False, True])
def test_actual_default_drainer_delivers_native_prompt_with_outbox_key(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch, kind: str, unknown_first: bool,
) -> None:
    queue, task_id = hosted
    list_wakes: Callable[[str], Sequence[WakeOperation | RunWaiterWakeOperation]]
    if kind == "owner":
        queue.resume(task_id, OWNER, wake_requested=True, wake_message="continue")
        list_wakes = queue.list_wakes
    else:
        waiter = queue.register_run_waiter(
            task_id, pid=101, host="host-a", start_token="token",
            resume_worktree=OWNER, command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task_id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
        list_wakes = queue.list_run_waiter_wakes
    [wake] = list_wakes(task_id)
    calls = _capture(monkeypatch, unknown_first=unknown_first)

    async def scenario() -> None:
        drain = (
            drain_wake_outbox(queue, EventBus(), interval=0.01, retry_base=0.01)
            if kind == "owner" else drain_run_waiter_wakes(queue, interval=0.01, retry_base=0.01)
        )
        loop = asyncio.create_task(drain)
        try:
            for _ in range(200):
                if list_wakes(task_id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("default wake did not deliver")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert [argv for argv, _ in calls] == [_expected(wake.id)] * (2 if unknown_first else 1)
    assert all(kwargs["input"] == "continue" for _, kwargs in calls)


@pytest.mark.parametrize("reserved", [False, True])
def test_explicit_cli_owner_retains_existing_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reserved: bool
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("external CLI owner", repo="project")
    assert isinstance(task, Task)
    if reserved:
        reservation, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="cli")
        queue.record_spawn(reservation.key, session_handle=NATIVE, worktree="worktree-a")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def resume(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(bridge, "resume_steered_owner", resume)
    assert deliver_owner_wake(queue, OWNER, NATIVE, task.id, "continue", "wake-1")
    assert calls == [((OWNER, task.id, "continue"), {
        "owner_session_id": NATIVE, "idempotency_key": "wake-1",
    })]


def test_retired_history_prevents_native_delivery(
    hosted: Hosted, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_dispatch import owner_wake_delivery

    queue, task_id = hosted
    monkeypatch.setattr(
        owner_wake_delivery, "native_bindings", lambda *_args: ([NATIVE], [NATIVE])
    )
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError, match="retired native"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []
