"""Shared draft mutations fence both the card/owner and the observed draft."""

from dataclasses import asdict
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def owner(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("A draftable question")
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker")
    task = queue.set_card(task.id, "host/worker", card={
        "title": "Question", "request_input": [{"name": "answer", "type": "text"}],
    })
    return queue, task


def _basis(task):
    return {
        "expected_revision": task.steering_revision,
        "expected_draft_revision": task.card_draft_revision,
    }


def _snapshot(queue, task_id):
    return (
        asdict(queue.get(task_id)), queue.events(task_id),
        queue.steer_log(task_id), [asdict(wake) for wake in queue.list_wakes(task_id)],
    )


def test_first_touch_save_and_clear_preserve_task_and_wake_state(owner):
    queue, task = owner
    assert task.card_draft_revision == "absent"
    saved = queue.save_card_draft(task.id, fields={"answer": "draft"}, **_basis(task))
    assert saved.card_draft["steering_revision"] == task.steering_revision
    assert saved.card_draft_revision != "absent"
    assert saved.steering_revision == task.steering_revision
    assert saved.status == task.status and saved.awaiting_steer == task.awaiting_steer
    cleared = queue.clear_card_draft(task.id, **_basis(saved))
    assert cleared.card_draft is None and cleared.card_draft_revision == "absent"
    assert cleared.steering_revision == task.steering_revision
    assert queue.steer_log(task.id) == queue.list_wakes(task.id) == []


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("change", ["card", "owner", "draft"])
def test_stale_card_owner_or_shared_draft_refuses_without_any_effect(owner, clear, change):
    queue, task = owner
    if change == "owner":
        task = queue.resume(task.id, task.owner)
    original = queue.save_card_draft(task.id, fields={"answer": "original"}, **_basis(task))
    if change == "draft":
        queue.save_card_draft(task.id, fields={"answer": "newer"}, **_basis(original))
    elif change == "card":
        queue.resume(task.id, task.owner)
        queue.set_card(task.id, task.owner, card={"title": "Replacement"})
    else:
        queue.bind_owner_session(task.id, task.owner, "replacement-session")
    before = _snapshot(queue, task.id)
    with pytest.raises(TaskError, match="changed"):
        if clear:
            queue.clear_card_draft(task.id, **_basis(original))
        else:
            queue.save_card_draft(task.id, fields={"answer": "stale"}, **_basis(original))
    assert _snapshot(queue, task.id) == before


def test_same_values_and_timestamp_still_advance_observed_draft_revision(owner):
    queue, task = owner
    first = queue.save_card_draft(task.id, fields={"answer": "same"}, now=1000, **_basis(task))
    second = queue.save_card_draft(task.id, fields={"answer": "same"}, now=1000, **_basis(first))
    assert first.card_draft_revision != second.card_draft_revision
    assert second.steering_revision == first.steering_revision
    with pytest.raises(TaskError, match="draft changed"):
        queue.clear_card_draft(task.id, **_basis(first))


@pytest.mark.parametrize("clear", [False, True])
def test_guarded_mutation_requires_both_nonempty_preconditions(owner, clear):
    queue, task = owner
    before = _snapshot(queue, task.id)
    for basis in (
        {"expected_revision": task.steering_revision},
        {"expected_draft_revision": "absent"},
        {"expected_revision": "", "expected_draft_revision": "absent"},
    ):
        with pytest.raises(TaskError, match="both expected"):
            if clear:
                queue.clear_card_draft(task.id, **basis)
            else:
                queue.save_card_draft(task.id, fields={}, **basis)
    assert _snapshot(queue, task.id) == before


@pytest.mark.parametrize("second_clear", [False, True])
def test_two_clients_cannot_overwrite_or_clear_each_others_draft(owner, second_clear):
    queue, task = owner
    saved = queue.save_card_draft(task.id, fields={"answer": "initial"}, **_basis(task))
    barrier = threading.Barrier(2)
    outcomes = []

    def mutate(clear):
        barrier.wait(timeout=3)
        try:
            outcomes.append(
                queue.clear_card_draft(task.id, **_basis(saved)) if clear else
                queue.save_card_draft(task.id, fields={"answer": "changed"}, **_basis(saved))
            )
        except TaskError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=mutate, args=(clear,)) for clear in (False, second_clear)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert len(outcomes) == 2 and sum(isinstance(item, TaskError) for item in outcomes) == 1
    assert queue.steer_log(task.id) == queue.list_wakes(task.id) == []


def test_legacy_draft_stays_readable_without_inventing_card_provenance(owner):
    queue, task = owner
    legacy = {"fields": {"answer": "legacy"}, "ts": 1000}
    with queue._connect() as connection:
        connection.execute("UPDATE tasks SET card_draft = ? WHERE id = ?", (json.dumps(legacy), task.id))
    observed = queue.get(task.id)
    assert observed.card_draft == legacy and observed.card_draft_revision != "absent"
    saved = queue.save_card_draft(task.id, fields={"answer": "bound"}, **_basis(observed))
    assert saved.card_draft["steering_revision"] == observed.steering_revision
    unguarded = queue.save_card_draft(task.id, fields={"answer": "legacy caller"})
    assert "steering_revision" not in unguarded.card_draft
    assert queue.clear_card_draft(task.id).card_draft is None


def _forward(api, request):
    response = api.request(request.method, request.url.path, content=request.content,
                           headers={"Content-Type": "application/json"})
    return httpx.Response(response.status_code, json=response.json(), request=request)


def test_queue_scope_is_stable_read_only_and_preserved_on_task_views_and_board(owner, tmp_path):
    from agent_dispatch.board_cli import _build
    from agent_dispatch.coordinator_tasks import _draft_scope
    queue, task = owner
    before = _snapshot(queue, task.id)
    scope = _draft_scope(queue)
    assert _draft_scope(TaskQueue(queue.db_path)) == scope
    assert _draft_scope(TaskQueue(tmp_path / "different.db"))["queue"] != scope["queue"]
    with TestClient(create_app(queue)) as api:
        direct = api.get(f"/tasks/{task.id}").json()
        rows = api.get("/tasks").json()
        assert direct["draft_scope"] == rows[0]["draft_scope"] == scope
        assert _build(rows, machine=scope["machine"], recent_mins=60)[0]["draft_scope"] == scope
    assert _snapshot(queue, task.id) == before


@pytest.mark.parametrize("clear", [False, True])
def test_wrong_queue_refuses_even_with_matching_task_and_card_revisions(owner, clear):
    queue, task = owner
    before = _snapshot(queue, task.id)
    operation = "scoped-clear" if clear else "scoped"
    with TestClient(create_app(queue)) as api:
        response = api.post(f"/tasks/{task.id}/card-draft/{operation}", json={
            **_basis(task), "expected_queue_id": "another-queue", "fields": {"answer": "wrong target"},
        })
        assert response.status_code == 409 and "another queue" in response.json()["detail"]
        missing = api.post(f"/tasks/{task.id}/card-draft/{operation}", json=_basis(task))
        assert missing.status_code == 422
    assert _snapshot(queue, task.id) == before


def test_scoped_sdk_saves_and_clears_existing_cas_without_any_steer_or_wake(owner):
    queue, task = owner
    with TestClient(create_app(queue)) as api:
        scope = api.get(f"/tasks/{task.id}").json()["draft_scope"]
        with DispatchClient("http://owner", transport=httpx.MockTransport(
            lambda request: _forward(api, request),
        )) as client:
            saved = client.save_card_draft(
                task.id, fields={"answer": "bound"}, expected_queue_id=scope["queue"], **_basis(task),
            )
            assert saved["draft_scope"] == scope
            cleared = client.clear_card_draft(
                task.id, expected_queue_id=scope["queue"],
                expected_revision=saved["steering_revision"],
                expected_draft_revision=saved["card_draft_revision"],
            )
            assert cleared["draft_scope"] == scope and cleared["card_draft"] is None
    assert queue.get(task.id).status == task.status
    assert queue.steer_log(task.id) == queue.list_wakes(task.id) == []


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("scenario", ["old", "downgrade", "wrong-ack"])
def test_scoped_sdk_never_downgrades_or_replays(owner, clear, scenario):
    queue, task = owner
    posts = []

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "card_draft_cas": 1, **({"card_draft_scope": 1} if scenario != "old" else {}),
            })
        posts.append(request.url.path)
        if scenario == "downgrade":
            return httpx.Response(404, json={"detail": "Not Found"})
        saved = queue.save_card_draft(task.id, fields={"answer": "bound"}, **_basis(task))
        return httpx.Response(200, json={
            **asdict(saved), "steering_revision": saved.steering_revision,
            "card_draft_revision": saved.card_draft_revision,
            "draft_scope": {"queue": "different"},
        })

    error = DispatchError if scenario == "wrong-ack" else DispatchUpgradeRequired
    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error):
            if clear:
                client.clear_card_draft(task.id, expected_queue_id="queue-one", **_basis(task))
            else:
                client.save_card_draft(
                    task.id, fields={"answer": "bound"}, expected_queue_id="queue-one", **_basis(task),
                )
    assert len(posts) == (0 if scenario == "old" else 1)
    if posts:
        assert posts[0].endswith("/scoped-clear" if clear else "/scoped")


def test_real_http_and_sdk_share_revisions_and_refuse_stale_clear(owner):
    queue, task = owner
    with TestClient(create_app(queue)) as api:
        client = DispatchClient("http://owner")
        client._http.close()
        client._http = httpx.Client(base_url="http://owner", transport=httpx.MockTransport(
            lambda request: _forward(api, request),
        ))
        try:
            assert api.get("/health").json()["card_draft_cas"] == 1
            observed = api.get(f"/tasks/{task.id}").json()
            assert observed["card_draft_revision"] == "absent"
            saved = client.save_card_draft(task.id, fields={"answer": "first"}, **_basis(task))
            newer = client.save_card_draft(
                task.id, fields={"answer": "second"},
                expected_revision=saved["steering_revision"],
                expected_draft_revision=saved["card_draft_revision"],
            )
            with pytest.raises(DispatchError, match="draft changed"):
                client.clear_card_draft(
                    task.id, expected_revision=saved["steering_revision"],
                    expected_draft_revision=saved["card_draft_revision"],
                )
            result = client.clear_card_draft(
                task.id, expected_revision=newer["steering_revision"],
                expected_draft_revision=newer["card_draft_revision"],
            )
            assert result["card_draft_revision"] == "absent"
        finally:
            client.close()


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("downgrade", [False, True])
def test_guarded_sdk_never_falls_back_to_an_unguarded_old_owner(owner, clear, downgrade):
    _, task = owner
    mutations = []

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"card_draft_cas": 1} if downgrade else {})
        mutations.append(request.url.path)
        return httpx.Response(404, json={"detail": "Not Found"})

    client = DispatchClient("http://old")
    client._http.close()
    client._http = httpx.Client(base_url="http://old", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DispatchUpgradeRequired):
            if clear:
                client.clear_card_draft(task.id, **_basis(task))
            else:
                client.save_card_draft(task.id, fields={}, **_basis(task))
    finally:
        client.close()
    assert mutations == ([f"/tasks/{task.id}/card-draft/guarded-clear" if clear else
                          f"/tasks/{task.id}/card-draft/guarded"] if downgrade else [])


@pytest.mark.parametrize("operation", ["guarded", "guarded-clear"])
def test_http_guarded_routes_refuse_missing_or_invalid_preconditions(owner, operation):
    queue, task = owner
    before = _snapshot(queue, task.id)
    with TestClient(create_app(queue)) as api:
        for body in ({}, {"expected_revision": task.steering_revision},
                     {"expected_revision": "", "expected_draft_revision": "absent"}):
            assert api.post(f"/tasks/{task.id}/card-draft/{operation}", json=body).status_code == 422
    assert _snapshot(queue, task.id) == before


@pytest.mark.parametrize("clear", [False, True])
def test_sdk_rejects_unverifiable_success_without_retrying(owner, clear):
    _, task = owner
    posts = []

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"card_draft_cas": 1})
        posts.append(request.url.path)
        return httpx.Response(200, json={"id": task.id, "card_draft": None})

    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError, match="no verifiable guarded draft"):
            if clear:
                client.clear_card_draft(task.id, **_basis(task))
            else:
                client.save_card_draft(task.id, fields={}, **_basis(task))
    assert len(posts) == 1


@pytest.mark.parametrize("scenario", ["missing-clear", "old-clear", "unchanged-save", "absent-save"])
def test_sdk_does_not_acknowledge_an_unproven_draft_mutation(owner, scenario):
    queue, task = owner
    task = queue.save_card_draft(task.id, fields={"answer": "same"}, **_basis(task))
    snapshot = {
        **asdict(task), "steering_revision": task.steering_revision,
        "card_draft_revision": task.card_draft_revision,
    }
    if scenario == "missing-clear":
        snapshot.pop("card_draft")
        snapshot["card_draft_revision"] = "absent"
    elif scenario == "old-clear":
        snapshot["card_draft"] = None
    elif scenario == "absent-save":
        snapshot["card_draft_revision"] = "absent"
    posts = []

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"card_draft_cas": 1})
        posts.append(request.url.path)
        return httpx.Response(200, json=snapshot)

    before = _snapshot(queue, task.id)
    with DispatchClient("http://owner", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError, match="refresh before retrying"):
            if scenario.endswith("clear"):
                client.clear_card_draft(task.id, **_basis(task))
            else:
                client.save_card_draft(task.id, fields={"answer": "same"}, **_basis(task))
    assert len(posts) == 1 and _snapshot(queue, task.id) == before
