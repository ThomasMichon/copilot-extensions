"""Read-only, repo-qualified dedup identity lookup across queue/API/client/CLI."""

from __future__ import annotations

import json
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status, TaskQueue

REPO = "github.com/example/project"
OTHER_REPO = "github.com/example/other"
KEY = "review:literal_%'?#42"


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def api(queue):
    # No lifespan: only the in-process read routes, never coordinator workers.
    client = TestClient(create_app(queue))
    yield client
    client.close()


def test_exact_dedup_literal_key_repo_scope_and_no_roster_cap(queue, api, monkeypatch):
    wanted = queue.create("unrelated title", repo=REPO, dedup_key=KEY, now=1)
    queue.create("other lane", repo=OTHER_REPO, dedup_key=KEY, now=2)
    queue.create(KEY, repo=REPO, dedup_key="not-the-key", now=3)
    queue.create("similar", repo=REPO, dedup_key="review:literal_AX'?#42", now=4)
    assert queue.list(repo=REPO, limit=1)[0].id != wanted.id

    def no_roster(*_args, **_kwargs):
        raise AssertionError("identity lookup must not scan a capped roster")

    monkeypatch.setattr(queue, "list", no_roster)
    monkeypatch.setattr(queue, "sweep", no_roster)
    assert queue.lookup_dedup(KEY, repo="https://" + REPO + ".git") == wanted
    response = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY})
    assert response.status_code == 200
    assert response.json() == [api.get(f"/tasks/{wanted.id}").json()]
    assert api.get("/tasks", params={"repo": REPO, "dedup_key": "missing"}).json() == []
    other = api.get("/tasks", params={"repo": OTHER_REPO, "dedup_key": KEY}).json()
    assert len(other) == 1 and other[0]["repo"] == OTHER_REPO
    assert other[0]["id"] != wanted.id
    found = api.get("/tasks", params={"repo": REPO, "q": KEY}).json()
    assert [task["dedup_key"] for task in found] == ["not-the-key"]


@pytest.mark.parametrize(
    ("status", "require_verification", "occupies"),
    [
        (Status.PROPOSED, False, True),
        (Status.QUEUED, False, True),
        (Status.CLAIMED, False, True),
        (Status.STARTED, False, True),
        (Status.SUSPENDED, False, True),
        (Status.SUBMITTED, False, False),
        (Status.COMPLETED, False, False),
        (Status.ABANDONED, False, False),
        (Status.DEAD_LETTER, False, False),
        (Status.SUBMITTED, True, True),
        (Status.COMPLETED, True, False),
        (Status.ABANDONED, True, False),
        (Status.DEAD_LETTER, True, False),
    ],
)
def test_exact_dedup_release_semantics_match_create(
    queue, api, status, require_verification, occupies
):
    task = queue.create(
        "first generation",
        repo=REPO,
        dedup_key=KEY,
        require_verification=require_verification,
    )
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task.id))
    before = asdict(queue.get(task.id))
    result = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY})
    assert result.status_code == 200
    assert [item["id"] for item in result.json()] == ([task.id] if occupies else [])
    assert asdict(queue.get(task.id)) == before
    created = queue.create("next generation", repo=REPO, dedup_key=KEY)
    assert (created.id == task.id) is occupies
    assert queue.lookup_dedup(KEY, repo=REPO).id == created.id


def test_exact_dedup_held_task_detail_and_storage_are_unchanged(queue, api, monkeypatch):
    task = queue.create(
        "review",
        repo=REPO,
        dedup_key=KEY,
        evaluator_ref="review-lifecycle",
        labels=["external-review"],
        exclusive_key="review-subject",
        category="maintenance",
    )
    queue.reserve_spawn(task.id, reserved_by="review-supervisor")
    queue.claim_one("review-worker", repo=REPO, task_id=task.id)
    queue.set_hold(task.id, reason="waiting for operator", actor="operator")
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET owner_session_id = ?, payload_inline = ? WHERE id = ?",
            ("existing-session", "existing payload", task.id),
        )
        before = "\n".join(conn.iterdump())
    expected = api.get(f"/tasks/{task.id}").json()
    assert expected["spawn_reservation"]
    connect = queue._connect

    def read_only_connection():
        conn = connect()
        conn.execute("PRAGMA query_only = ON")
        return conn

    monkeypatch.setattr(queue, "_connect", read_only_connection)
    response = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY})
    assert response.status_code == 200
    assert response.json() == [expected]
    assert expected["hold_reason"] == "waiting for operator"
    assert expected["owner_session_id"] == "existing-session"
    with queue._connect() as conn:
        assert "\n".join(conn.iterdump()) == before


@pytest.mark.parametrize(
    "selector",
    [
        {"q": ""},
        {"sweep": True},
        {"status": "queued"},
        {"category": "all"},
        {"limit": 1},
        {"target_machine": "host-a"},
        {"target_repo": REPO},
        {"label": "review"},
        {"evaluator_ref": "review"},
        {"source": "recipe"},
        {"origin_ref": "reviewer"},
        {"exclusive_key": "review"},
    ],
)
def test_exact_dedup_http_rejects_conflicting_selectors(api, selector):
    response = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY, **selector})
    assert response.status_code == 400
    assert "cannot combine selectors" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {"dedup_key": KEY},
        {"repo": "", "dedup_key": KEY},
        {"repo": REPO, "dedup_key": ""},
        {"repo": REPO, "dedup_key": " \t"},
    ],
)
def test_exact_dedup_http_rejects_missing_identity(api, params):
    assert api.get("/tasks", params=params).status_code == 400


def test_exact_dedup_client_and_cli_preserve_authoritative_details(
    queue, api, monkeypatch, capsys
):
    from agent_dispatch import __main__ as cli

    task = queue.create("review", repo=REPO, dedup_key=KEY)
    expected = [api.get(f"/tasks/{task.id}").json()]
    requests = []

    def exchange(request):
        requests.append((request.method, request.url.path, dict(request.url.params)))
        response = api.request(
            request.method,
            str(request.url),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(response.status_code, json=response.json())

    def client(_args, *, ensure):
        assert ensure is False
        return DispatchClient("http://testserver", transport=httpx.MockTransport(exchange))

    def no_enrichment(_tasks):
        raise AssertionError("exact lookup must not enrich or probe task sessions")

    monkeypatch.setattr(cli, "_client", client)
    monkeypatch.setattr(cli, "_scope_repo", lambda _args: REPO)
    monkeypatch.setattr(cli, "_enrich", no_enrichment)
    assert cli.main(["lookup", "--repo", REPO, "--dedup-key", KEY]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    with client(None, ensure=False) as c:
        assert c.lookup_dedup(KEY, repo=REPO) == expected
        assert c.list(repo=REPO, dedup_key=KEY) == expected
        assert c.lookup_dedup("missing", repo=REPO) == []
    assert all(method == "GET" for method, _path, _params in requests)
    assert requests[1] == ("GET", "/tasks", {"repo": REPO, "dedup_key": KEY})


@pytest.mark.parametrize("params", [{"q": KEY}, {"limit": 1}, {"category": "all"}])
def test_exact_dedup_client_rejects_conflicts_before_request(params):
    def unexpected(_request):
        raise AssertionError("invalid lookup must not reach the coordinator")

    with DispatchClient("http://testserver", transport=httpx.MockTransport(unexpected)) as c:
        with pytest.raises(DispatchError, match="cannot combine selectors"):
            c.list(repo=REPO, dedup_key=KEY, **params)


def test_exact_dedup_client_refuses_old_coordinator_instead_of_listing():
    paths = []

    def legacy(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    with DispatchClient("http://legacy", transport=httpx.MockTransport(legacy)) as c:
        with pytest.raises(DispatchUpgradeRequired, match="exact repo-scoped dedup"):
            c.lookup_dedup(KEY, repo=REPO)
    assert paths == ["/health"]


def test_exact_dedup_cli_rejects_conflicting_or_unscoped_lookup(monkeypatch, capsys):
    from agent_dispatch import __main__ as cli

    with pytest.raises(SystemExit):
        cli.main(["lookup", "--dedup-key", KEY, "--limit", "1"])
    capsys.readouterr()
    monkeypatch.setattr(cli, "_scope_repo", lambda _args: None)
    assert cli.main(["lookup", "--dedup-key", KEY]) == 2
    assert "repo" in capsys.readouterr().err.lower()
