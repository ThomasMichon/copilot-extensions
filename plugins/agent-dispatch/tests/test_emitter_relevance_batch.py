"""Batch discovery stays outside queue writes without weakening per-task custody."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.producers import emitter, emitter_relevance
from agent_dispatch.queue import Status
from tests.test_emitter_relevance import _reconcile, _retain, _runner, spec as spec
from tests.test_pool_catalog_transport import _headers
from tests.test_task_relevance_effective_scope import venue as venue
from tests.test_task_relevance_registration import q as q
from tests.test_task_relevance_transport import api as api, transport


def test_batch_discovers_twice_outside_queue_writer_and_admits_each_task(q, api, spec, monkeypatch):
    tasks = [_retain(q, spec) for _ in range(2)]
    discoveries = []

    def discover(*_args, **_kwargs):
        with q._connect() as conn:
            conn.execute("PRAGMA busy_timeout=1")
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        discoveries.append(True)
        return DeclarationRead((), True)

    monkeypatch.setattr("agent_dispatch.relevance_source.read_supervisor_declarations", discover)
    calls = []
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        result = _reconcile(client, spec)
    assert result["errors"] == [] and len(result["accepted"]) == 2
    assert len(discoveries) == 2
    assert len([path for method, path in calls if method == "POST"]) == 1
    assert all(q.get(task.id).status == Status.ABANDONED for task in tasks)


def test_partial_batch_keeps_unknown_committed_receipt_and_finishes_sibling(q, api, spec, monkeypatch):
    first, second = [_retain(q, spec) for _ in range(2)]
    original = q.reconcile_task_relevance

    def uncertain(task_id, **kwargs):
        result = original(task_id, **kwargs)
        if task_id == first.id:
            raise ValueError("callback failed after commit")
        return result

    monkeypatch.setattr(q, "reconcile_task_relevance", uncertain)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec)
        recovered = _reconcile(client, spec)
    assert result["pending"] == 1
    assert [row["task_id"] for row in result["accepted"]] == [second.id]
    assert recovered["pending"] == 0
    assert recovered["recovered"][0]["task_id"] == first.id
    assert all(q.get(task.id).status == Status.ABANDONED for task in (first, second))


def test_partial_batch_rechecks_direct_revision_between_task_transactions(q, api, spec, monkeypatch):
    first, second = [_retain(q, spec) for _ in range(2)]
    original = q.reconcile_task_relevance

    def move(task_id, **kwargs):
        result = original(task_id, **kwargs)
        if task_id == first.id:
            q.set_registration_status("source-a", "paused")
        return result

    monkeypatch.setattr(q, "reconcile_task_relevance", move)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec)
    assert result["pending"] == 0
    assert [row["task_id"] for row in result["accepted"]] == [first.id]
    assert q.get(second.id).status == Status.QUEUED


@pytest.mark.parametrize("failure", ["missing", "foreign", "duplicate"])
def test_invalid_batch_response_retains_all_original_requests(q, api, spec, failure):
    tasks = [_retain(q, spec) for _ in range(2)]
    delegate = transport(api, [])

    def send(request):
        response = delegate.handle_request(request)
        if request.url.path == "/task-relevance/reports":
            value = json.loads(response.content)
            items = value["result"]["items"]
            if failure == "missing":
                items.pop()
            elif failure == "foreign":
                items[0]["request_id"] = "foreign-request"
            else:
                items[1] = items[0]
            return httpx.Response(200, json=value)
        return response

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        result = _reconcile(client, spec)
        state = json.loads(emitter.receipts_path(spec["id"]).with_name("relevance.json").read_text())
        recovered = _reconcile(client, spec)
    assert result["pending"] == len(state["pending"]) == len(tasks)
    assert recovered["pending"] == 0 and len(recovered["recovered"]) == len(tasks)


def test_prior_receiver_without_batch_support_never_checks_or_reports(q, api, spec):
    _retain(q, spec)
    delegate = transport(api, [])

    def send(request):
        response = delegate.handle_request(request)
        if request.url.path == "/health":
            value = json.loads(response.content)
            value["task_relevance"].pop("bounded_batches")
            return httpx.Response(200, json=value)
        assert not request.url.path.startswith("/task-relevance/")
        return response

    def forbidden(*_args, **_kwargs):
        pytest.fail("source command ran without batch capability")

    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        result = _reconcile(client, spec, forbidden)
    assert result["errors"] and result["pending"] == 0


@pytest.mark.parametrize("credential", [None, "data"])
@pytest.mark.parametrize("path", ["/task-relevance/snapshots", "/task-relevance/reports"])
def test_batch_routes_require_control_authority(q, api, spec, path, credential):
    task = _retain(q, spec)
    response = api.request(
        "GET" if path.endswith("snapshots") else "POST", path,
        headers={} if credential is None else _headers(credential),
        params={"registration_id": "source-a", "task_ids": [task.id]},
        json={"reports": []} if path.endswith("reports") else None,
    )
    assert response.status_code in {401, 403}
    assert q.get(task.id).status == Status.QUEUED


def test_oversized_snapshot_batch_is_refused_before_discovery(q, api, spec, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized batch reached source discovery")

    monkeypatch.setattr("agent_dispatch.relevance_source.read_supervisor_declarations", forbidden)
    response = api.get(
        "/task-relevance/snapshots", headers=_headers(),
        params={"registration_id": "source-a", "task_ids": ["task"] * 101},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("failure", ["changed", "incomplete"])
def test_report_batch_reobserves_declarations_after_source_command(q, venue, spec, monkeypatch, failure):
    from tests.test_emitter_relevance import _runner
    from tests.test_task_relevance_effective_scope import declaration

    api, trusted, _ = venue
    rid = declaration(trusted, "review-source", "emitter", spec)
    tasks = [_retain(q, spec) for _ in range(2)]

    def move():
        if failure == "changed":
            declaration(trusted, "review-source", "emitter", {**spec, "command": ["new-source"]})
        else:
            monkeypatch.setattr(
                "agent_dispatch.relevance_source.read_supervisor_declarations",
                lambda *_args, **_kwargs: DeclarationRead((), False),
            )

    from agent_dispatch.producers.emitter_relevance import reconcile_receipts
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = reconcile_receipts(
            client, spec, registration_id=rid, runner=_runner(before_return=move),
        )
    assert result["pending"] == 0 and result["errors"]
    assert all(q.get(task.id).status == Status.QUEUED for task in tasks)


def test_oversized_report_batch_is_refused_before_discovery(q, api, spec, monkeypatch):
    from tests.test_task_relevance_transport import body

    task = _retain(q, spec)
    report = {"task_id": task.id, **body(q, task.id)}

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized batch reached source discovery")

    monkeypatch.setattr("agent_dispatch.relevance_source.read_supervisor_declarations", forbidden)
    response = api.post(
        "/task-relevance/reports", headers=_headers(), json={"reports": [report] * 101},
    )
    assert response.status_code == 422 and q.get(task.id).status == Status.QUEUED


def test_accepted_and_recovered_batches_checkpoint_sidecar_without_per_item_rewrites(q, api, spec, monkeypatch):
    spec["relevance"]["batch_size"] = 25
    q.register_registration("emitter", spec, reg_id="source-a", machine="host-a")
    tasks = [_retain(q, spec) for _ in range(25)]
    saved = []
    save = emitter_relevance._save

    def record(path, state):
        saved.append(len(state["pending"]))
        save(path, state)

    monkeypatch.setattr(emitter_relevance, "_save", record)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, []),
    ) as client:
        result = _reconcile(client, spec, _runner("needed"))
    assert len(result["accepted"]) == len(tasks)
    assert saved == [0, 25, 0]

    saved.clear()
    # A crash before the final accepted checkpoint leaves the original requests
    # recoverable from their server receipts, without another report.
    delegate = transport(api, [])

    def crash_before_ack_checkpoint(path, state):
        if not state["pending"] and saved:
            raise SystemExit("lost process after acceptance")
        record(path, state)

    monkeypatch.setattr(emitter_relevance, "_save", crash_before_ack_checkpoint)
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=delegate,
    ) as client:
        with pytest.raises(SystemExit):
            _reconcile(client, spec)
    monkeypatch.setattr(emitter_relevance, "_save", record)
    saved.clear()
    calls = []
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        recovered = _reconcile(client, spec)
    assert len(recovered["recovered"]) == len(tasks) and recovered["pending"] == 0
    assert saved == [0]
    assert not any(method == "POST" for method, _ in calls)
