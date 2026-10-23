"""Original wire context survives process restart without another task authority."""

import copy
import json
import subprocess
import sys

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.steering import SteeringError
from agent_dispatch.steering_requests import validate_prepared_request
from tests import test_steering_acceptance_http as owner_fixtures

api = owner_fixtures.api
native_url = owner_fixtures.native_url
owner = owner_fixtures.owner


def _client(api):
    return DispatchClient("http://owner", transport=httpx.MockTransport(
        lambda request: owner_fixtures._forward(api, request),
    ))


def _prepare(client, task_id, *, wake=True):
    task = client.get(task_id)
    return client.prepare_steer(
        task_id, "original/request", fields={"answer": "first\nsecond"}, sender="original/operator",
        wake=wake, expected_scope=task["draft_scope"], expected_status=task["status"],
        expected_revision=task["steering_revision"],
    )


@pytest.mark.parametrize("wake", [False, True])
def test_prepare_is_read_only_and_detaches_all_request_values(api, owner, wake):
    queue, task_id = owner
    with _client(api) as client:
        before = queue.get(task_id), queue.events(task_id)
        request = _prepare(client, task_id, wake=wake)
        assert request["wake"] is wake and request["message"]
        assert request["sender"] == "original/operator" and request["request_id"] == "original/request"
        assert (queue.get(task_id), queue.events(task_id)) == before
        assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []
        detached = validate_prepared_request(request)
        request["fields"]["answer"] = "edited afterward"
        assert detached["fields"] == {"answer": "first\nsecond"}


@pytest.mark.parametrize("wake", [False, True])
def test_restart_replays_original_request_after_new_card_and_reads_original_consumption(api, owner, wake):
    queue, task_id = owner
    with _client(api) as client:
        request = json.loads(json.dumps(_prepare(client, task_id, wake=wake)))
        first = client.steer_prepared(request)
    assert queue.take_steer(task_id, "host/worker")["fields"] == request["fields"]
    queue.set_card(task_id, "host/worker", card={
        "title": "Replacement", "request_input": [{"name": "answer", "type": "textarea"}],
    })
    with _client(api) as restarted:
        replay = restarted.steer_prepared(request)
        receipt = restarted.prepared_steer_receipt(request)
    assert first["steer_receipt"]["acceptance"] == replay["steer_receipt"]["acceptance"] == receipt["acceptance"]
    assert receipt["consumed_at"] is not None
    assert receipt["steering_scope"] == request["scope"]
    assert len(queue.steer_log(task_id)) == 1 and len(queue.list_wakes(task_id)) == int(wake)
    assert queue.steer_log(task_id)[0]["sender"] == "original/operator"
    if wake:
        assert queue.list_wakes(task_id)[0].message == request["message"]


@pytest.mark.parametrize("part", ["queue", "installation", "machine"])
def test_changed_scope_cannot_submit_or_read_another_owners_receipt(api, owner, part):
    queue, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
        client.steer_prepared(request)
        before = queue.get(task_id), queue.steer_log(task_id), queue.list_wakes(task_id)
        request["scope"][part] = "another-owner"
        for operation in (client.steer_prepared, client.prepared_steer_receipt):
            with pytest.raises(DispatchError, match="another scope") as error:
                operation(request)
            assert error.value.status_code == 409
        assert (queue.get(task_id), queue.steer_log(task_id), queue.list_wakes(task_id)) == before


def test_replacement_before_first_submit_is_not_retargeted(api, owner):
    queue, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
        queue.resume(task_id, "host/worker")
        queue.set_card(task_id, "host/worker", card={
            "title": "Replacement", "request_input": [{"name": "answer", "type": "textarea"}],
        })
        with pytest.raises(DispatchError, match="card or owner changed"):
            client.steer_prepared(request)
        with pytest.raises(DispatchError) as missing:
            client.prepared_steer_receipt(request)
        assert missing.value.status_code == 404
    assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []
    assert request["fields"] == {"answer": "first\nsecond"}


@pytest.mark.parametrize("part", ["status", "revision", "scope"])
def test_prepare_refuses_stale_observation_without_any_write(api, owner, part):
    queue, task_id = owner
    with _client(api) as client:
        task = client.get(task_id)
        arguments = {
            "expected_status": task["status"], "expected_revision": task["steering_revision"],
            "expected_scope": task["draft_scope"],
        }
        if part == "scope":
            arguments["expected_scope"] = {**task["draft_scope"], "queue": "changed"}
        else:
            arguments["expected_" + part] = "changed"
        with pytest.raises(DispatchError, match="changed"):
            client.prepare_steer(task_id, "new", fields={}, **arguments)
    assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []


@pytest.mark.parametrize("version", [None, False, True, 0, -1, "1"])
@pytest.mark.parametrize("operation", ["prepare", "submit", "receipt"])
def test_old_owner_never_receives_a_scoped_write_or_unscoped_receipt(api, owner, version, operation):
    _, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
    seen = []

    def old(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"steering_acceptance": 1, "steering_request_context": version})

    with DispatchClient("http://old", transport=httpx.MockTransport(old)) as client:
        with pytest.raises(DispatchUpgradeRequired, match="scope-bound"):
            if operation == "prepare":
                client.prepare_steer(
                    task_id, "new", fields={}, expected_scope=request["scope"],
                    expected_status=request["expected_status"], expected_revision=request["expected_revision"],
                )
            elif operation == "submit":
                client.steer_prepared(request)
            else:
                client.prepared_steer_receipt(request)
    assert seen and all(item == ("GET", "/health") for item in seen)


@pytest.mark.parametrize("operation", ["submit", "receipt"])
def test_scope_acknowledgement_must_match_even_if_acceptance_looks_valid(api, owner, operation):
    _, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
        if operation == "receipt":
            client.steer_prepared(request)

    def corrupt(message):
        response = owner_fixtures._forward(api, message)
        if "/scoped" in message.url.path:
            body = response.json()
            body["steering_scope"]["queue"] = "wrong"
            return httpx.Response(response.status_code, json=body)
        return response

    with DispatchClient("http://owner", transport=httpx.MockTransport(corrupt)) as client:
        with pytest.raises(DispatchError, match="scope"):
            (client.steer_prepared if operation == "submit" else client.prepared_steer_receipt)(request)


def test_receipt_is_checked_against_complete_original_request(api, owner):
    _, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
        client.steer_prepared(request)
        other = copy.deepcopy(request)
        other["fingerprint"] = "changed"
        with pytest.raises(SteeringError, match="changed"):
            client.prepared_steer_receipt(other)
        from agent_dispatch.steering_requests import request_fingerprint
        other["sender"] = "another sender"
        other["fingerprint"] = request_fingerprint(other)
        with pytest.raises(DispatchError, match="original request"):
            client.prepared_steer_receipt(other)


def test_cli_prepared_replay_never_resolves_identity_even_for_original_null_sender(api, owner, monkeypatch):
    import argparse
    from agent_dispatch import steering_cli
    from agent_dispatch.steering_requests import request_fingerprint

    _, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id, wake=False)
    request["sender"] = None
    request["fingerprint"] = request_fingerprint(request)
    core = steering_cli._core()
    monkeypatch.setattr(core, "_client", lambda _args: _client(api))
    monkeypatch.setattr(core, "_owner_from_identity", lambda _args: pytest.fail("identity re-resolved"))
    emitted = []
    monkeypatch.setattr(core, "_emit", lambda result: emitted.append(result) or 0)
    assert steering_cli._cmd_steer(argparse.Namespace(
        task_id=task_id, prepared_json=json.dumps(request), field=None, sender=None,
        message=None, wake=True, expected_status=None, expected_revision=None, idempotency_key=None,
    )) == 0
    assert emitted[0]["receipt"]["acceptance"]["request_fingerprint"] == request["fingerprint"]
    assert owner[0].steer_log(task_id)[0]["sender"] is None


@pytest.mark.parametrize("key,value", [
    ("version", True), ("version", 2), ("task_id", ""), ("request_id", ""),
    ("expected_revision", ""), ("fields", []), ("wake", 1), ("sender", 1),
    ("scope", {"queue": "only-partial"}), ("message", "changed"),
])
def test_malformed_or_changed_recovery_data_never_reaches_a_transport(api, owner, key, value):
    _, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
    request[key] = value
    with DispatchClient("http://owner", transport=httpx.MockTransport(
        lambda _: pytest.fail("malformed request reached owner"),
    )) as client:
        with pytest.raises(SteeringError):
            client.steer_prepared(request)
        with pytest.raises(SteeringError):
            client.prepared_steer_receipt(request)


@pytest.mark.parametrize("missing", [
    "fields", "sender", "wake", "message", "expected_scope", "expected_status",
    "expected_revision", "idempotency_key",
])
def test_scoped_route_requires_explicit_complete_wire_context(api, owner, missing):
    queue, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
    body = {
        "fields": request["fields"], "sender": request["sender"], "wake": request["wake"],
        "message": request["message"], "expected_scope": request["scope"],
        "expected_status": request["expected_status"], "expected_revision": request["expected_revision"],
        "idempotency_key": request["request_id"],
    }
    body.pop(missing)
    assert api.post(f"/tasks/{task_id}/steer/scoped", json=body).status_code == 422
    assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []


def test_lost_response_survives_client_restart_without_another_answer(api, owner, monkeypatch):
    queue, task_id = owner
    with _client(api) as client:
        request = _prepare(client, task_id)
    monkeypatch.setenv("AGENT_DISPATCH_HTTP_IDEMPOTENT_RETRIES", "0")
    posts = []

    def lose_response(message):
        response = owner_fixtures._forward(api, message)
        if message.method == "POST":
            posts.append(json.loads(message.content))
            assert response.status_code == 200
            raise httpx.ReadTimeout("accepted response lost", request=message)
        return response

    with DispatchClient("http://owner", transport=httpx.MockTransport(lose_response)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.steer_prepared(request)
    queue.take_steer(task_id, "host/worker")
    queue.set_card(task_id, "host/worker", card={"title": "Next card"})
    with _client(api) as restarted:
        receipt = restarted.prepared_steer_receipt(request)
        replay = restarted.steer_prepared(request)
    assert receipt["consumed_at"] is not None
    assert replay["steer_receipt"]["acceptance"] == receipt["acceptance"]
    assert posts[0]["idempotency_key"] == request["request_id"]
    assert posts[0]["message"] == request["message"] and posts[0]["sender"] == request["sender"]
    assert len(queue.steer_log(task_id)) == len(queue.list_wakes(task_id)) == 1


@pytest.mark.timeout(120)
def test_native_prepare_and_fresh_process_retry_keep_original_wire_context(native_url, owner, tmp_path):
    queue, task_id = owner

    def cli(*arguments):
        return subprocess.run(
            [sys.executable, "-m", "agent_dispatch", "--url", native_url, *arguments],
            cwd=tmp_path, capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    with DispatchClient(native_url) as client:
        task = client.get(task_id)
    prepared = cli(
        "steer", "prepare", task_id, "--scope-json", json.dumps(task["draft_scope"]),
        "--expected-status", task["status"], "--expected-revision", task["steering_revision"],
        "--field", "answer=first\nsecond", "--sender", "original/operator",
        "--message", "original wake text", "--request-id", "native/original",
    )
    assert prepared.returncode == 0, prepared.stderr
    request = json.loads(prepared.stdout)
    assert queue.steer_log(task_id) == queue.list_wakes(task_id) == []
    encoded = json.dumps(request)
    arguments = ("steer", "submit", task_id, "--prepared-json", encoded)
    first = cli(*arguments)
    assert first.returncode == 0, first.stderr
    queue.take_steer(task_id, "host/worker")
    queue.set_card(task_id, "host/worker", card={"title": "Next question"})
    second = cli(*arguments)
    assert second.returncode == 0, second.stderr
    inspected = cli("steer", "receipt", task_id, "--prepared-json", encoded)
    assert inspected.returncode == 0, inspected.stderr
    receipt = json.loads(inspected.stdout)
    assert receipt["acceptance"] == json.loads(first.stdout)["receipt"]["acceptance"]
    assert receipt["consumed_at"] is not None and len(queue.steer_log(task_id)) == 1
    assert queue.list_wakes(task_id)[0].message == "original wake text"
    invalid = cli(*arguments, "--sender", "new sender")
    assert invalid.returncode == 2 and "overrides" in invalid.stderr
    assert len(queue.steer_log(task_id)) == len(queue.list_wakes(task_id)) == 1
