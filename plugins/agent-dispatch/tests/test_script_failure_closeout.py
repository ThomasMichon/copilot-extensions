"""Use the real queue/HTTP transition and a disposable script PID, never a service."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import companion, procutil
from agent_dispatch import script_failure_closeout as closeout
from agent_dispatch import spawn_script_body as scripts
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.managed_retention import process_identity_domain
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError
from agent_dispatch.script_worker import ScriptTaskRuntime
from agent_dispatch.supervisor import Supervisor


def test_owned_process_exit_not_failure_delivery_closes_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("script", repo="example")
    assert isinstance(task, Task)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="lane")
    worker = "script-fixture"
    with subprocess.Popen(  # noqa: S603 -- exact disposable interpreter
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, **procutil._process_tree_kwargs(),
    ) as child, TestClient(create_app(queue, token="data", control_token="control",
                                      enable_mcp=False, verification_interval=0)) as api:
        assert child.stdin
        # Ensure the fixture never waits for EOF if an assertion fails before normal exit.
        try:
            handle = scripts._encode_script_body_handle(
                worker, child.pid, companion.process_start_token(child.pid),
                process_domain=process_identity_domain(), task_id=task.id, generation=task.generation,
            )
            queue.record_spawn(reservation.key, session_handle=handle, startup_complete=True, startup_revision=1)

            def send(req: httpx.Request) -> httpx.Response:
                res = api.request(req.method, req.url.path, headers=dict(req.headers), content=req.content)
                return httpx.Response(res.status_code, content=res.content)

            with DispatchClient("http://fixture", token="data", control_token="control",
                                transport=httpx.MockTransport(send)) as client:
                runtime = ScriptTaskRuntime(client, task.id, worker, repo="example", heartbeat_seconds=0)
                live = runtime.announce_start()
                reported = runtime.report_failure("fixture reason")
                assert reported["status"] == "started" and reported["owner"] == worker
                assert runtime.report_failure("fixture reason") == reported
                with pytest.raises(TaskError, match="retains assignment"):
                    queue.abandon(task.id, worker_id=worker, permitted=True)
                with pytest.raises(TaskError):
                    queue.settle_spawn(reservation.key, release_requested=True)
                with pytest.raises(TaskError):
                    queue.record_spawn(reservation.key, session_handle="local-body:other")
                expected: dict[str, Any] = dict(
                    reservation_key=reservation.key, session_handle=handle, worker_id=worker,
                    generation=live["generation"], status="started", owner_session_id=live["owner_session_id"],
                    startup_revision=1,
                )
                url = f"/tasks/{task.id}/script-exit"
                assert api.post(url, json=expected, headers={"Authorization": "Bearer data"}).status_code == 403
                assert api.post(url, json=expected, headers={"Authorization": "Bearer control"}).status_code == 409
                child.stdin.close()
                assert child.wait(timeout=10) == 0
                for bad in ({"worker_id": "other"}, {"generation": 99}, {"session_handle": handle+" "}):
                    response = api.post(url, json={**expected, **bad}, headers={"Authorization": "Bearer control"})
                    assert response.status_code == 409
                queue.set_hold(task.id, reason="preserve", actor="operator")
                assert api.post(url, json=expected, headers={"Authorization": "Bearer control"}).status_code == 409
                queue.clear_hold(task.id)
                supervisor = Supervisor(client, repo="example", supervisor_id="lane",
                                        spawn_fn=lambda _: pytest.fail("no script launches"))
                assert supervisor.recover_gone() == 1
                assert supervisor.recover_gone() == 0
                assert client.close_failed_script(task.id, **expected)["status"] == "abandoned"
                ended = queue.get(task.id)
                retired = queue.get_reservation(reservation.key)
                assert ended and ended.status == "abandoned" and ended.owner is None
                assert retired and retired.state == "settled" and retired.detail == "fixture reason"
        finally:
            child.stdin.close()
            child.wait(timeout=10)


@pytest.mark.parametrize("race", ["concurrent", "hold", "foreign-domain", "unknown", "legacy"])
def test_recheck_or_exact_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("script", repo="example")
    assert isinstance(task, Task)
    res, _ = queue.reserve_spawn(task.id)
    worker = "script-one"
    handle = (scripts._encode_script_body_handle(worker, 123, "fixture") if race == "legacy"
              else scripts._encode_script_body_handle(worker, 123, "fixture", process_domain="original",
                                                       task_id=task.id, generation=task.generation))
    queue.record_spawn(res.key, session_handle=handle, startup_complete=True, startup_revision=1)
    queue.claim_one(worker, task_id=task.id, repo="example")
    claimed = queue.start(task.id, worker)
    expected = closeout.ScriptExit(
        reservation_key=res.key, session_handle=handle, generation=claimed.generation,
        status=claimed.status, owner_session_id=claimed.owner_session_id, startup_revision=1, worker_id=worker,
    )
    barrier = Barrier(2)
    monkeypatch.setattr(closeout, "process_identity_domain", lambda: "other" if race=="foreign-domain" else "original")

    def observe(pid: int, token: str | None) -> bool:
        assert race not in ("foreign-domain", "legacy")
        if race == "hold":
            queue.set_hold(task.id, reason="new", actor="operator")
        if race == "concurrent":
            barrier.wait(timeout=5)
        return race != "unknown"

    monkeypatch.setattr(scripts, "exact_script_exit", observe)
    if race == "concurrent":
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: closeout.finish(queue, task.id, expected), range(2)))
        assert {r.event_type for r in results} == {None, "task.abandoned"}
        assert not closeout.finish(TaskQueue(queue.db_path), task.id, expected).event_type
    else:
        with pytest.raises(TaskError):
            closeout.finish(queue, task.id, expected)
        current, retained = queue.get(task.id), queue.get_reservation(res.key)
        assert current and current.owner == worker and retained and retained.state == "spawned"
