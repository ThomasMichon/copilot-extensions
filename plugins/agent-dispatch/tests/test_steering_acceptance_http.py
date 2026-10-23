"""Guarded owner acceptance through real routes, SDK retries and native CLI."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def owner(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("A structured answer")
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="session-one")
    queue.set_card(
        task.id, "host/worker",
        card={"title": "Question", "request_input": [{"name": "answer", "type": "textarea"}]},
    )
    return queue, task.id


@pytest.fixture
def api(owner):
    queue, _ = owner
    client = TestClient(create_app(queue))
    try:
        yield client
    finally:
        client.close()


def _forward(api, request):
    response = api.request(
        request.method, request.url.path, params=request.url.params,
        content=request.content, headers={"Content-Type": "application/json"},
    )
    return httpx.Response(response.status_code, json=response.json(), request=request)


def test_http_reads_project_the_same_owner_revision_and_guarded_route_refuses_missing_basis(api, owner):
    queue, task_id = owner
    revision = queue.get(task_id).steering_revision
    assert api.get(f"/tasks/{task_id}").json()["steering_revision"] == revision
    listing = api.get("/tasks").json()
    assert next(row for row in listing if row["id"] == task_id)["steering_revision"] == revision
    response = api.post(f"/tasks/{task_id}/steer/guarded", json={"fields": {"answer": "unsafe"}})
    assert response.status_code == 422
    assert queue.steer_log(task_id) == [] and queue.list_wakes(task_id) == []


def test_card_replaced_between_client_read_and_post_is_rejected_by_the_owner(api, owner):
    queue, task_id = owner
    revision = api.get(f"/tasks/{task_id}").json()["steering_revision"]
    queue.resume(task_id, "host/worker")
    queue.set_card(task_id, "host/worker", card={"title": "Different question"})
    queue.save_card_draft(task_id, fields={"answer": "replacement draft"})
    before = queue.get(task_id)
    response = api.post(
        f"/tasks/{task_id}/steer/guarded",
        json={"fields": {"answer": "stale"}, "expected_revision": revision},
    )
    assert response.status_code == 409 and "card or owner changed" in response.json()["detail"]
    assert queue.get(task_id) == before
    assert queue.steer_log(task_id) == [] and queue.list_wakes(task_id) == []


def test_worker_delivery_before_receipt_read_does_not_rewrite_acceptance(api, owner, monkeypatch):
    queue, task_id = owner
    original = queue.steer_receipt

    def delivered_before_read(task, request):
        wake = queue.claim_due_wake()
        assert wake is not None
        queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
        return original(task, request)

    monkeypatch.setattr(queue, "steer_receipt", delivered_before_read)
    response = api.post(
        f"/tasks/{task_id}/steer/guarded",
        json={
            "fields": {"answer": "accepted"},
            "expected_revision": queue.get(task_id).steering_revision,
            "idempotency_key": "delivery-interleaving",
        },
    )
    assert response.status_code == 200
    receipt = response.json()["steer_receipt"]
    assert receipt["acceptance"]["wake_status"] == "pending"
    assert response.json()["steer_wake_status"] == receipt["wake_status"] == "delivered"
    assert receipt["wake"]["id"] == receipt["acceptance"]["wake_operation_id"]
    assert receipt["consumed_at"] is None and queue.steer_log(task_id)[0]["taken"] is False


def test_sdk_lost_response_retries_exact_request_after_consumption_and_card_change(api, owner, monkeypatch):
    queue, task_id = owner
    revision = queue.get(task_id).steering_revision
    monkeypatch.setenv("AGENT_DISPATCH_HTTP_IDEMPOTENT_RETRIES", "1")
    posts = []

    def handler(request):
        response = _forward(api, request)
        if request.method == "POST":
            assert request.url.path.endswith("/steer/guarded")
            posts.append(json.loads(request.content))
            if len(posts) == 1:
                assert response.status_code == 200
                queue.take_steer(task_id, "host/worker")
                queue.set_card(task_id, "host/worker", card={"title": "Next question"})
                raise httpx.ReadTimeout("accepted response was lost", request=request)
        return response

    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        result = client.steer(
            task_id, fields={"answer": "first\nsecond"}, sender="operator",
            expected_revision=revision,
        )
        receipt = client.steer_receipt(task_id, posts[0]["idempotency_key"])
        replay = client.steer(
            task_id, fields={"answer": "first\nsecond"}, sender="operator",
            expected_revision=revision,
        )
    assert len(posts) == 3 and posts[0] == posts[1] == posts[2]
    assert result["steer_receipt"]["acceptance"] == receipt["acceptance"] == replay["steer_receipt"]["acceptance"]
    assert receipt["consumed_at"] is not None and receipt["wake_status"] == "pending"
    assert result["card"]["title"] == "Next question"
    assert len(queue.steer_log(task_id)) == len(queue.list_wakes(task_id)) == 1


@pytest.mark.parametrize("version", [None, False, True, 0, -1, "1"])
def test_old_owner_cannot_receive_a_guarded_write(version):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"steering_acceptance": version})

    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchUpgradeRequired, match="guarded steering"):
            client.steer("task", fields={"answer": "no write"}, expected_revision="snapshot")
    assert seen == [("GET", "/health")]


def test_owner_downgrade_after_health_never_falls_back_to_the_legacy_write():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"steering_acceptance": 1})
        return httpx.Response(404, json={"detail": "Not Found"})

    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.steer("task", fields={"answer": "no write"}, expected_revision="snapshot")
    assert seen == [("GET", "/health"), ("POST", "/tasks/task/steer/guarded")]


@pytest.mark.parametrize("field", ["request_id", "task_id", "steering_revision", "request_fingerprint"])
def test_sdk_never_reports_success_for_an_unbound_acceptance_receipt(api, owner, field):
    queue, task_id = owner
    revision = queue.get(task_id).steering_revision

    def handler(request):
        response = _forward(api, request)
        if request.method == "POST":
            body = response.json()
            body["steer_receipt"]["acceptance"][field] = "wrong"
            return httpx.Response(200, json=body, request=request)
        return response

    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError, match="no verifiable steering acceptance"):
            client.steer(
                task_id, fields={"answer": "accepted but response is corrupt"},
                expected_revision=revision, idempotency_key="known-request",
            )
        receipt = client.steer_receipt(task_id, "known-request")
    assert receipt["acceptance"]["request_id"] == "known-request"
    assert len(queue.steer_log(task_id)) == 1


def test_missing_task_and_missing_receipt_are_not_reported_as_unsupported_protocol(api):
    with DispatchClient("http://owner", transport=httpx.MockTransport(lambda request: _forward(api, request))) as client:
        with pytest.raises(DispatchError) as missing:
            client.steer("absent", fields={}, expected_revision="snapshot")
        assert missing.value.status_code == 404 and not isinstance(missing.value, DispatchUpgradeRequired)
        with pytest.raises(DispatchError) as missing_receipt:
            client.steer_receipt("absent", "unknown")
        assert missing_receipt.value.status_code == 404


@pytest.fixture
def native_url(owner):
    queue, _ = owner
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(queue), log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "isolated native owner did not become ready"
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "isolated native owner did not stop"


@pytest.mark.timeout(90)
def test_native_cli_card_submit_exact_retry_and_request_receipt(native_url, owner):
    queue, task_id = owner

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "--url", native_url, *args],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    card = cli("card", "show", task_id)
    assert card.returncode == 0, card.stderr
    revision = json.loads(card.stdout)["steering_revision"]
    arguments = (
        "steer", "submit", task_id, "--field", "answer=first\nsecond", "--sender", "operator",
        "--expected-revision", revision, "--idempotency-key", "request/with?reserved# chars",
    )
    submitted = cli(*arguments)
    assert submitted.returncode == 0, submitted.stderr
    accepted = json.loads(submitted.stdout)["receipt"]["acceptance"]
    assert accepted["request_id"] == "request/with?reserved# chars"
    queue.take_steer(task_id, "host/worker")
    queue.set_card(task_id, "host/worker", card={"title": "New question"})
    replay = cli(*arguments)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["receipt"]["acceptance"] == accepted
    observed = cli("steer", "receipt", task_id, accepted["request_id"])
    assert observed.returncode == 0, observed.stderr
    receipt = json.loads(observed.stdout)
    assert receipt["acceptance"] == accepted and receipt["consumed_at"] is not None
    assert receipt["wake"]["id"] == accepted["wake_operation_id"]
    assert receipt["wake_status"] == "pending"
    assert len(queue.steer_log(task_id)) == len(queue.list_wakes(task_id)) == 1


@pytest.mark.parametrize("name", ["agent-dispatch.json", "agent-dispatch-maintenance.json"])
def test_both_native_contributions_forward_the_owner_revision(name):
    root = Path(__file__).resolve().parents[1]
    document = json.loads((root / "pivots" / name).read_text("utf-8"))
    action = next(action for action in document["actions"] if action["key"] == "steer")
    prepare = action["request"]["prepare"]
    index = prepare.index("--expected-revision")
    assert prepare[index + 1] == "{steering_revision}"
    assert action["run"][-2:] == ["--prepared-json", "{json.prepared_request}"]
    assert action["request"]["receipt"][-2:] == ["--prepared-json", "{json.prepared_request}"]


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.timeout(120)
def test_native_cli_guarded_draft_first_touch_stale_reset_and_clear(native_url, owner, scoped):
    queue, task_id = owner

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "--url", native_url, *args],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    shown = cli("card", "show", task_id)
    assert shown.returncode == 0, shown.stderr
    original = json.loads(shown.stdout)
    scope_args = ("--expected-queue-id", original["draft_scope"]["queue"]) if scoped else ()
    assert original["card_draft_revision"] == "absent"
    basis = ("--expected-revision", original["steering_revision"],
             "--expected-draft-revision", original["card_draft_revision"])
    saved = cli("card", "draft", "save", task_id, "--field", "answer=first\nsecond, exact", *basis, *scope_args)
    assert saved.returncode == 0, saved.stderr
    draft = json.loads(saved.stdout)
    assert draft["card_draft"]["fields"] == {"answer": "first\nsecond, exact"}
    assert draft["card_draft"]["steering_revision"] == original["steering_revision"]
    newer = queue.save_card_draft(
        task_id, fields={"answer": "other client"},
        expected_revision=draft["steering_revision"],
        expected_draft_revision=draft["card_draft_revision"],
    )
    before = queue.get(task_id)
    stale = cli(
        "card", "draft", "clear", task_id, "--expected-revision", draft["steering_revision"],
        "--expected-draft-revision", draft["card_draft_revision"],
        *scope_args,
    )
    assert stale.returncode != 0 and "draft changed" in stale.stderr
    assert queue.get(task_id) == before
    cleared = cli(
        "card", "draft", "clear", task_id, "--expected-revision", newer.steering_revision,
        "--expected-draft-revision", newer.card_draft_revision,
        *scope_args,
    )
    assert cleared.returncode == 0, cleared.stderr
    assert json.loads(cleared.stdout)["card_draft_revision"] == "absent"
    if scoped:
        wrong_queue = cli(
            "card", "draft", "save", task_id, "--field", "answer=wrong target",
            "--expected-revision", newer.steering_revision,
            "--expected-draft-revision", "absent", "--expected-queue-id", "another-queue",
        )
        assert wrong_queue.returncode != 0 and "another queue" in wrong_queue.stderr
        assert queue.get(task_id).card_draft is None
    partial = cli("card", "draft", "clear", task_id, "--expected-revision", newer.steering_revision)
    assert partial.returncode == 2 and "both --expected" in partial.stderr
    assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []
