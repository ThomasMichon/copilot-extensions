"""Task-only CLI calls exercise real coordinator authorization, not fake handlers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from agent_procutil import no_window_flags
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.task_scope import TASK_SCOPE_HEADER
from agent_dispatch.task_worker_cli import TaskWorker
from tests._helpers import TEST_REPO, RepoDefaultingQueue


@pytest.fixture
def worker(monkeypatch, tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    task = queue.create("restricted work", repo=TEST_REPO, payload_inline="own payload")
    app = create_app(queue, token="fleet-test", control_token="control-test", enable_mcp=False)
    scheme = "Bear" + "er "
    with TestClient(app) as api:
        claimed = api.post(
            "/claim", headers={"Authorization": scheme + "fleet-test"},
            json={"worker_id": "host/worker", "task_id": task.id, "repo": TEST_REPO},
        )
        assert claimed.status_code == 200, claimed.text
        started = api.post(
            f"/tasks/{task.id}/start",
            headers={"Authorization": scheme + "fleet-test"},
            json={"worker_id": "host/worker", "owner_session_id": "native-worker"},
        )
        assert started.status_code == 200, started.text
        grant = started.json()["task_token"]
        for name, value in {
            "AGENT_DISPATCH_TASK_TOKEN": grant,
            "AGENT_DISPATCH_TASK_ID": task.id,
            "AGENT_DISPATCH_TASK_URL": "http://coordinator.test",
            "AGENT_DISPATCH_TASK_WORKER_ID": "host/worker",
            "AGENT_DISPATCH_TOKEN": "ambient-fleet",
            "AGENT_DISPATCH_CONTROL_TOKEN": "ambient-control",
            "AGENT_DISPATCH_TOKEN_COMMAND": "must-not-run",
            "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND": "must-not-run",
            "AGENT_DISPATCH_SHARED_URL": "http://wrong.test",
            "AGENT_DISPATCH_FAILOVER_MACHINE": "wrong-peer",
        }.items():
            monkeypatch.setenv(name, value)

        def forbidden(*_args, **_kwargs):
            pytest.fail("task-only worker attempted fleet discovery, autostart or identity")

        for name in (
            "_ensure_local_coordinator", "_resolve_client_target", "_should_ssh_failover",
            "client_token", "client_control_token", "shared_control_token", "_identity",
            "_scope_repo", "_enrich",
        ):
            monkeypatch.setattr(cli, name, forbidden)
        requests = []

        def send(request):
            assert request.url.host == "coordinator.test"
            assert "authorization" not in request.headers
            assert request.headers[TASK_SCOPE_HEADER] == os.environ["AGENT_DISPATCH_TASK_TOKEN"]
            requests.append((request.method, request.url.path))
            response = api.request(
                request.method, request.url.path,
                content=request.content, headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, content=response.content)

        monkeypatch.setattr(
            TaskWorker, "client",
            lambda self: DispatchClient(
                self.url, task_token=self.token, transport=httpx.MockTransport(send),
            ),
        )
        yield task.id, queue, requests


def test_task_worker_show_has_no_extra_history_or_tracking_reads(worker, capsys):
    task_id, _queue, requests = worker
    assert cli.main(["show", task_id]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == task_id
    assert requests == [("GET", f"/tasks/{task_id}")]


def test_task_worker_payload_progress_heartbeat_and_complete(worker, capsys):
    task_id, queue, requests = worker
    assert cli.main(["payload", task_id, "--raw"]) == 0
    assert capsys.readouterr().out == "own payload\n"
    assert cli.main(["progress", task_id, "--summary", "worker progress"]) == 0
    assert cli.main(["heartbeat", task_id, "host/worker"]) == 0
    assert cli.main(["complete", task_id, "--result-json", '{"ok":true}']) == 0
    assert queue.get(task_id).result == {"ok": True}
    assert queue.get(task_id).status == "completed"
    assert [path.rsplit("/", 1)[-1] for _, path in requests] == [
        "payload", "progress", "heartbeat", "complete",
    ]
    assert cli.main(["show", task_id]) == 1
    assert "403" in capsys.readouterr().err


def test_task_worker_can_abandon_own_task_without_fleet_cleanup(worker):
    task_id, queue, requests = worker
    assert cli.main(["abandon", task_id, "--permit", "--reason", "cannot finish"]) == 0
    assert queue.get(task_id).status == "abandoned"
    assert requests == [("POST", f"/tasks/{task_id}/abandon")]


@pytest.mark.parametrize("argv", [
    ["list"], ["health"], ["print-endpoint"], ["serve"], ["mcp"], ["watch"],
    ["_ensure-coordinator"], ["focus"], ["federation", "status"],
    ["show", "another-task"], ["start", "TASK"],
    ["show", "TASK", "--history"],
    ["progress", "TASK", "wrong", "--summary", "bad"],
    ["complete", "TASK", "wrong"],
    ["abandon", "TASK"], ["abandon", "TASK", "--permit", "--duplicate-of", "other"],
    ["abandon", "TASK", "--permit", "--resolve"],
    ["abandon", "TASK", "--permit", "--override-live"],
    ["--url", "http://wrong.test", "show", "TASK"],
    ["--shared", "show", "TASK"], ["--token", "broad", "show", "TASK"],
    ["--control-token", "broad", "show", "TASK"],
])
def test_task_worker_rejects_before_side_effects(worker, argv, capsys):
    task_id, _queue, requests = worker
    argv = [task_id if value == "TASK" else value for value in argv]
    assert cli.main(argv) == 1
    assert "agent-dispatch:" in capsys.readouterr().err
    assert not requests


@pytest.mark.parametrize("name", [
    "AGENT_DISPATCH_TASK_TOKEN", "AGENT_DISPATCH_TASK_ID",
    "AGENT_DISPATCH_TASK_URL", "AGENT_DISPATCH_TASK_WORKER_ID",
])
@pytest.mark.parametrize("empty", [True, False])
def test_task_worker_incomplete_projection_never_falls_back(worker, monkeypatch, name, empty):
    if empty:
        monkeypatch.setenv(name, "")
    else:
        monkeypatch.delenv(name)
    task_id, _queue, requests = worker
    assert cli.main(["show", task_id]) == 1
    assert not requests


@pytest.mark.parametrize("url", [
    "ftp://wrong.test", "http://user:password@wrong.test", "http://wrong.test?token=x",
    "http://wrong.test#fragment", "http://wrong.test:bad", "http://wrong.test:0",
])
def test_task_worker_rejects_unpinned_endpoint_shapes(worker, monkeypatch, url):
    task_id, _queue, requests = worker
    monkeypatch.setenv("AGENT_DISPATCH_TASK_URL", url)
    assert cli.main(["show", task_id]) == 1
    assert not requests


def test_task_worker_explicit_projection(monkeypatch):
    for name in (
        "AGENT_DISPATCH_TASK_TOKEN", "AGENT_DISPATCH_TASK_ID",
        "AGENT_DISPATCH_TASK_URL", "AGENT_DISPATCH_TASK_WORKER_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    args = cli.build_parser().parse_args([
        "--task-token", "test-grant", "--task-scope", "task-1",
        "--task-url", "http://coordinator.test", "--task-worker", "worker",
        "show", "task-1",
    ])
    with cli._client(args) as client:
        assert client.base_url == "http://coordinator.test"
        assert client._http.headers[TASK_SCOPE_HEADER] == "test-grant"
        assert "authorization" not in client._http.headers
        assert client._control_headers() == {}
        assert not client._http.trust_env
        assert not client._http.follow_redirects


@pytest.mark.parametrize("extra", [
    {"token": "broad"}, {"control_token": "broad"}, {"tunnel": object()},
])
def test_task_worker_client_cannot_mix_credentials(extra):
    with pytest.raises(ValueError, match="exclusive"):
        DispatchClient("http://coordinator.test", task_token="grant", **extra)


@pytest.mark.parametrize("url", [
    "http://user:password@coordinator.test", "http://@coordinator.test",
    "https://user@coordinator.test", "http://coordinator.test?credential=x",
    "ftp://coordinator.test", "http://coordinator.test:bad",
])
def test_task_worker_python_client_rejects_endpoint_credentials(url):
    with pytest.raises(ValueError, match="pinned HTTP coordinator"):
        DispatchClient(url, task_token="grant")


def test_task_worker_invalid_result_file_is_local_failure(worker, tmp_path, capsys):
    task_id, queue, requests = worker
    assert cli.main([
        "complete", task_id, "--result-file", str(tmp_path / "missing.json"),
    ]) == 1
    stderr = capsys.readouterr().err
    assert "invalid result" in stderr
    assert "cannot reach coordinator" not in stderr
    assert not requests
    assert queue.get(task_id).status == "started"


def test_task_worker_transport_preserves_ordinary_bearer():
    scheme = "Bear" + "er "
    with DispatchClient("http://coordinator.test", token="fake-fleet") as client:
        assert client._http.headers["Authorization"] == scheme + "fake-fleet"
        assert client._http.trust_env


def test_task_worker_redirect_does_not_forward_grant():
    from agent_dispatch.client import DispatchError

    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://wrong.test/tasks/task-1"})

    with DispatchClient(
        "http://coordinator.test", task_token="grant",
        transport=httpx.MockTransport(redirect),
    ) as client:
        with pytest.raises(DispatchError, match="redirect refused"):
            client.get("task-1")
    assert len(requests) == 1
    assert requests[0].url.host == "coordinator.test"


def test_task_worker_bad_grant_fails_without_fleet_fallback(worker, monkeypatch, capsys):
    task_id, _queue, requests = worker
    monkeypatch.setenv("AGENT_DISPATCH_TASK_TOKEN", "invalid-grant")
    assert cli.main(["show", task_id]) == 1
    assert "401" in capsys.readouterr().err
    assert requests == [("GET", f"/tasks/{task_id}")]


def test_task_worker_projection_repr_never_contains_secret():
    worker = TaskWorker("secret-grant", "task-1", "http://coordinator.test", "worker")
    assert "secret-grant" not in repr(worker)


@pytest.mark.timeout(120)
def test_task_worker_cli_over_real_http_process(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    task = queue.create("real worker", repo=TEST_REPO, payload_inline="process payload")
    app = create_app(queue, token="fleet-test", control_token="control-test", enable_mcp=False)
    scheme = "Bear" + "er "
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive(), "HTTP coordinator exited before readiness"
                assert time.monotonic() < deadline, "HTTP coordinator readiness timed out"
                time.sleep(0.01)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with httpx.Client(base_url=url, trust_env=False, timeout=5) as supervisor:
                claimed = supervisor.post(
                    "/claim", headers={"Authorization": scheme + "fleet-test"},
                    json={"worker_id": "worker", "task_id": task.id, "repo": TEST_REPO},
                )
                assert claimed.status_code == 200, claimed.text
                started = supervisor.post(
                    f"/tasks/{task.id}/start",
                    headers={"Authorization": scheme + "fleet-test"},
                    json={"worker_id": "worker", "owner_session_id": "process-worker"},
                )
                assert started.status_code == 200, started.text
                grant = started.json()["task_token"]
            env = {
                **os.environ,
                "AGENT_DISPATCH_TASK_TOKEN": grant,
                "AGENT_DISPATCH_TASK_ID": task.id,
                "AGENT_DISPATCH_TASK_URL": url,
                "AGENT_DISPATCH_TASK_WORKER_ID": "worker",
                "AGENT_DISPATCH_TOKEN_COMMAND": "must-not-run",
                "AGENT_DISPATCH_CONTROL_TOKEN_COMMAND": "must-not-run",
                "AGENT_DISPATCH_URL": "http://127.0.0.1:1",
                "AGENT_DISPATCH_SHARED_URL": "http://127.0.0.1:1",
                "AGENT_DISPATCH_FAILOVER_MACHINE": "unreachable-peer",
            }
            for command, expected in [
                (["list"], 1),
                (["show", "another-task"], 1),
                (["show", task.id], 0),
                (["payload", task.id, "--raw"], 0),
                (["progress", task.id, "--summary", "process worker progress"], 0),
                (["heartbeat", task.id, "worker"], 0),
                (["complete", task.id, "--result-json", '{"process":true}'], 0),
                (["show", task.id], 1),
            ]:
                child = subprocess.run(  # noqa: S603 -- fixed candidate CLI and test-owned arguments
                    [sys.executable, "-m", "agent_dispatch", *command],
                    env=env, capture_output=True, text=True, timeout=20,
                    creationflags=no_window_flags(),
                )
                assert child.returncode == expected, (command, child.stdout, child.stderr)
                assert grant not in child.stdout + child.stderr
            assert queue.get(task.id).status == "completed"
            assert queue.get(task.id).result == {"process": True}
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "HTTP coordinator failed to shut down"
