"""Standing-charter routes, client capability gating and CLI over the real queue."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch import remote_dispatch
from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement_client import connected

REPO = "owner/repo"
DATA = {"Authorization": "Bearer data"}
CONTROL = {"Authorization": "Bearer control"}
DECLARATION = {
    "namespace": "owner", "charter_key": "dispatcher", "lane_key": "primary",
    "effort_ref": "efforts/dispatcher", "materialization_ref": "roles/dispatcher",
    "source_revision": "source-1", "expected_materialization_revision": "effective-1",
}


def app(queue: TaskQueue, *, control_token: str | None = "control") -> TestClient:
    return TestClient(create_app(
        queue, token="data", control_token=control_token, enable_mcp=False,
        verification_interval=0,
    ))


def owned_queue(tmp_path) -> tuple[TaskQueue, GroundOwner]:
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.sqlite3", relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = queue
    return queue, owner


def workspace(tmp_path) -> dict[str, str]:
    return {
        "machine": "host", "project": "example", "worktree": "workspace",
        "path": str(tmp_path / "checkout"), "creation_nonce": "original",
    }


def flags(values: dict[str, str]) -> list[str]:
    return [item for key, value in values.items() for item in (f"--{key.replace('_', '-')}", value)]


def task_count(queue: TaskQueue) -> int:
    with queue._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def _source_request(client, operation, *, deadline):
    if operation == "inventory":
        return client.standing_source_repositories("owner", deadline=deadline)
    if operation == "observation":
        return client.standing_source_observation(REPO, "owner", deadline=deadline)
    return client.reconcile_standing_source_set(
        REPO, "owner", [], revision="revision", expected_observation=None, deadline=deadline,
    )


@pytest.mark.parametrize("operation", ["inventory", "observation", "reconcile"])
def test_source_request_deadlines_are_per_call_and_rechecked_after_health(monkeypatch, operation):
    from agent_dispatch import client_standing

    clock = [10.0]
    requests = []
    monkeypatch.setattr(client_standing.time, "monotonic", lambda: clock[0])

    def handler(request):
        requests.append(request)
        if request.url.path == "/health":
            clock[0] += 0.25
            return httpx.Response(200, json={"standing_charters": {
                "version": 1, "finite_child_link": 1, "source_set": 1, "attachment": False,
            }})
        return httpx.Response(200, json={"repositories": [], "observation": None})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        original_timeout = client._http.timeout
        _source_request(client, operation, deadline=12.0)
        assert requests[0].extensions["timeout"]["read"] == 2.0
        assert requests[1].extensions["timeout"]["read"] == 1.75
        assert client._http.timeout == original_timeout
        clock[0] = 11.9
        with pytest.raises(TimeoutError):
            _source_request(client, operation, deadline=12.0)
        assert len(requests) == 3 and requests[-1].url.path == "/health"
        with pytest.raises(TimeoutError):
            _source_request(client, operation, deadline=12.0)
        assert len(requests) == 3
        client.health()
        assert requests[-1].extensions["timeout"]["read"] == original_timeout.read


def test_client_flow_preserves_native_store_fences(tmp_path):
    queue, owner = owned_queue(tmp_path)
    requests: list[httpx.Request] = []
    with app(queue) as api, connected(api, requests) as client:
        assert client.health()["standing_charters"] == {
            "version": 1, "finite_child_link": 1, "source_set": 1, "attachment": True,
        }
        standing = client.reconcile_standing_charter("Dispatcher", DECLARATION, repo=REPO)
        again = client.reconcile_standing_charter("Dispatcher", DECLARATION, repo=REPO)
        assert again["id"] == standing["id"]
        assert again["standing_revision"] == standing["standing_revision"]

        reserved = client.reserve_standing_charter(
            standing["id"], workspace(tmp_path), expected_revision=standing["standing_revision"],
        )
        assert (reserved["status"], reserved["owner"]) == ("claimed", "host/workspace")
        assert reserved["standing_revision"] == standing["standing_revision"] + 1
        assert reserved["owner_session_id"] is None and len(owner.claims) == 1

        child = client.create("Child", repo=REPO, standing_parent_id=standing["id"])
        unlinked = client.create("Unlinked", repo=REPO)
        assert child["standing_parent_id"] == standing["id"]
        assert unlinked["standing_parent_id"] is None
        listed = client.list(repo=REPO, standing_parent_id=standing["id"])
        assert [task["id"] for task in listed] == [child["id"]]

        with pytest.raises(DispatchError) as stale:
            client.change_standing_lifecycle(
                standing["id"], "retire", expected_revision=standing["standing_revision"],
            )
        assert stale.value.status_code == 409
        with pytest.raises(DispatchError) as incarnation:
            client.accept_standing_charter(
                standing["id"], workspace(tmp_path),
                expected_revision=reserved["standing_revision"],
                expected_generation=reserved["generation"] + 1,
                owner_session_id="native-session", expected_head_revision=1,
            )
        assert incarnation.value.status_code == 409
        with pytest.raises(DispatchError) as missing:
            client.change_standing_lifecycle("missing", "retire", expected_revision=1)
        assert (missing.value.status_code, missing.value.detail) == (404, "no such task")
        assert queue.get(standing["id"]).standing_revision == reserved["standing_revision"]

        secondary = client.reconcile_standing_charter(
            "Secondary", {**DECLARATION, "lane_key": "secondary"}, repo=REPO,
        )
        current = secondary
        for action in ("retire", "settle-retirement", "reenroll"):
            changed = client.change_standing_lifecycle(
                secondary["id"], action, expected_revision=current["standing_revision"],
            )
            assert changed["standing_revision"] == current["standing_revision"] + 1
            current = changed
        assert current["standing_charter"]["declaration_generation"] == 2

    standing_posts = [request for request in requests if "standing" in request.url.path]
    assert standing_posts and all(
        request.method == "POST" and request.headers["authorization"] == "Bearer control"
        for request in standing_posts
    )


def test_standing_routes_require_configured_control_authority(tmp_path):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    body = {"title": "Dispatcher", "repo": REPO, "declaration": DECLARATION}
    with app(queue) as api:
        denied = api.post("/standing-charters", json=body, headers=DATA)
        retire = api.post("/tasks/any/standing/retire", json={"expected_revision": 1}, headers=DATA)
    assert denied.status_code == retire.status_code == 403
    assert denied.json()["detail"]["code"] == "standing_charter_control_forbidden"
    with app(queue, control_token=None) as api:
        unavailable = api.post("/standing-charters", json=body, headers=DATA)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "standing_charter_control_unavailable"
    assert task_count(queue) == 0


def test_source_set_client_flow_fences_stale_scans_and_retires_last_entry(tmp_path):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    requests: list[httpx.Request] = []
    entries = [{"title": "Dispatcher", "declaration": DECLARATION}]
    with app(queue) as api, connected(api, requests) as client:
        assert client.standing_source_repositories("owner") == []
        assert client.standing_source_observation(REPO, "owner") is None
        first = client.reconcile_standing_source_set(
            REPO, "owner", entries, revision="commit-1", expected_observation=None,
        )
        task_id, = first["enrolled"]
        assert client.standing_source_repositories("owner") == [REPO]
        assert client.standing_source_repositories("another") == []
        before = queue.get(task_id)
        assert client.standing_source_observation(REPO, "owner") == "commit-1"
        repeated = client.reconcile_standing_source_set(
            REPO, "owner", entries, revision="commit-1", expected_observation="commit-1",
        )
        assert repeated["unchanged"] == [task_id]
        with pytest.raises(DispatchError) as stale:
            client.reconcile_standing_source_set(
                REPO, "owner", [], revision="commit-2", expected_observation=None,
            )
        assert stale.value.status_code == 409
        assert queue.get(task_id) == before
        assert client.standing_source_observation(REPO, "owner") == "commit-1"
        removed = client.reconcile_standing_source_set(
            REPO, "owner", [], revision="commit-2", expected_observation="commit-1",
        )
        assert removed["retired"] == [{"id": task_id, "reason": "removed"}]
        assert client.standing_source_observation(REPO, "owner") == "commit-2"
        assert client.standing_source_repositories("owner") == [REPO]
    assert queue.get(task_id).standing_charter["lifecycle"] == "retiring"
    assert task_count(queue) == 1
    source_requests = [r for r in requests if r.url.path in {
        "/standing-charters/source-sets", "/standing-charters/source-repositories",
    }]
    assert source_requests and all(r.headers["authorization"] == CONTROL["Authorization"]
                                   for r in source_requests)


@pytest.mark.parametrize("control_token,status", [("control", 403), (None, 503)])
def test_source_set_routes_require_control_before_effects(tmp_path, control_token, status):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    body = {
        "repo": REPO, "namespace": "owner",
        "entries": [{"title": "Dispatcher", "declaration": DECLARATION}],
        "revision": "commit-1", "expected_observation": None,
    }
    with app(queue, control_token=control_token) as api:
        read = api.get("/standing-charters/source-sets",
                       params={"repo": REPO, "namespace": "owner"}, headers=DATA)
        inventory = api.get("/standing-charters/source-repositories",
                            params={"namespace": "owner"}, headers=DATA)
        write = api.post("/standing-charters/source-sets", json=body, headers=DATA)
        assert read.status_code == write.status_code == inventory.status_code == status
    assert task_count(queue) == 0
    assert queue.standing_source_observation(REPO, "owner") is None


def test_invalid_source_sets_do_not_enroll_or_advance_observation(tmp_path):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    entry = {"title": "Dispatcher", "declaration": DECLARATION}
    body = {
        "repo": REPO, "namespace": "owner", "entries": [entry],
        "revision": "commit-1", "expected_observation": None,
    }
    invalid = [
        ({key: value for key, value in body.items() if key != "expected_observation"}, 422),
        ({**body, "entries": [entry, {**entry, "enabled": 0}]}, 422),
        ({**body, "entries": [entry, entry]}, 409),
        ({**body, "namespace": "another"}, 409),
    ]
    with app(queue) as api:
        for request, status in invalid:
            response = api.post("/standing-charters/source-sets", json=request, headers=CONTROL)
            assert response.status_code == status, response.text
        assert api.get("/standing-charters/source-sets", params={
            "repo": REPO, "namespace": " owner",
        }, headers=CONTROL).status_code == 400
        assert api.get("/standing-charters/source-repositories", params={
            "namespace": " owner",
        }, headers=CONTROL).status_code == 400
    assert task_count(queue) == 0
    assert queue.standing_source_observation(REPO, "owner") is None


@pytest.mark.parametrize("source_set", [None, True, 0, 2, "1"])
def test_source_set_support_is_independent_of_baseline_standing_support(source_set):
    requests: list[httpx.Request] = []
    support = {"version": 1, "finite_child_link": 1, "attachment": False}
    if source_set is not None:
        support["source_set"] = source_set

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"standing_charters": support})
        return httpx.Response(200, json={"id": "baseline"})

    with DispatchClient(
        "http://old", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.standing_source_repositories("owner")
        with pytest.raises(DispatchUpgradeRequired):
            client.standing_source_observation(REPO, "owner")
        with pytest.raises(DispatchUpgradeRequired):
            client.reconcile_standing_source_set(
                REPO, "owner", [], revision="commit-1", expected_observation=None,
            )
        assert {request.url.path for request in requests} == {"/health"}
        assert client.reconcile_standing_charter("D", DECLARATION, repo=REPO)["id"] == "baseline"


@pytest.mark.parametrize("response", [{}, [], {"observation": ""}, {"observation": True}])
def test_client_rejects_malformed_source_observation(response):
    def send(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"standing_charters": {
                "version": 1, "finite_child_link": 1, "source_set": 1, "attachment": False,
            }})
        return httpx.Response(200, json=response)

    with DispatchClient(
        "http://old", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        with pytest.raises(DispatchError) as invalid:
            client.standing_source_observation(REPO, "owner")
        assert invalid.value.status_code == 502


@pytest.mark.parametrize("response", [
    {}, [], {"repositories": None}, {"repositories": REPO}, {"repositories": [""]},
    {"repositories": [True]}, {"repositories": [{}]}, {"repositories": [" owner/repo"]},
    {"repositories": [REPO, REPO]},
])
def test_client_rejects_malformed_source_inventory(response):
    def send(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"standing_charters": {
                "version": 1, "finite_child_link": 1, "source_set": 1, "attachment": False,
            }})
        return httpx.Response(200, json=response)

    with DispatchClient(
        "http://old", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        with pytest.raises(DispatchError) as invalid:
            client.standing_source_repositories("owner")
        assert invalid.value.status_code == 502


INVALID_REQUESTS = [
    ("/standing-charters", {"title": "T", "repo": REPO, "declaration": DECLARATION, "x": 1}, 422),
    ("/standing-charters", {"title": "T", "repo": REPO, "declaration": {
        key: value for key, value in DECLARATION.items() if key != "lane_key"}}, 422),
    ("/standing-charters", {
        "title": "T", "repo": REPO, "declaration": DECLARATION, "expected_revision": True}, 422),
    ("/standing-charters", {
        "title": "T", "repo": REPO, "declaration": DECLARATION, "expected_revision": 0}, 422),
    ("/standing-charters", {
        "title": "T", "repo": REPO, "declaration": {**DECLARATION, "namespace": " owner"}}, 400),
    ("/standing-charters", {
        "title": "T", "repo": REPO, "declaration": {**DECLARATION, "lane_key": "a\x00b"}}, 400),
    ("/tasks/any/standing/retire", {"expected_revision": "1"}, 422),
    ("/tasks/any/standing/reenroll", {"expected_revision": 1, "extra": True}, 422),
    ("/tasks/any/standing/reserve", {"expected_revision": 1, "workspace": {
        "machine": "a/b", "project": "p", "worktree": "w", "path": "x",
        "creation_nonce": "n"}}, 400),
    ("/tasks/any/standing/cancel", {"expected_revision": 1, "expected_generation": 0, "workspace": {
        "machine": "m", "project": "p", "worktree": "w", "path": "x",
        "creation_nonce": "n"}}, 422),
]


def test_invalid_standing_requests_have_no_effects(tmp_path):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    with app(queue) as api:
        statuses = [
            api.post(path, json=body, headers=CONTROL).status_code
            for path, body, _ in INVALID_REQUESTS
        ]
        assert statuses == [status for _, _, status in INVALID_REQUESTS]
        combined = api.get("/tasks", params={"standing_parent_id": "x", "q": "y"}, headers=DATA)
        assert combined.status_code == 400
    assert task_count(queue) == 0


@pytest.mark.parametrize("health", [
    {},
    {"standing_charters": []},
    {"standing_charters": {"version": 2, "finite_child_link": 1, "attachment": True}},
    {"standing_charters": {"version": True, "finite_child_link": 1, "attachment": True}},
    {"standing_charters": {"version": 1, "finite_child_link": 2, "attachment": True}},
    {"standing_charters": {"version": 1, "finite_child_link": True, "attachment": True}},
    {"standing_charters": {"version": 1, "finite_child_link": 1.0, "attachment": True}},
    {"standing_charters": {"version": 1, "finite_child_link": 1, "attachment": "yes"}},
])
def test_client_refuses_before_effects_without_declared_support(health):
    requests: list[httpx.Request] = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=health)
        return httpx.Response(200, json={"id": "created", "standing_parent_id": None})

    with DispatchClient(
        "http://old", token="data", control_token="control", transport=httpx.MockTransport(send),
    ) as client:
        for call in (
            lambda: client.reconcile_standing_charter("T", DECLARATION, repo=REPO),
            lambda: client.reserve_standing_charter("t", {}, expected_revision=1),
            lambda: client.change_standing_lifecycle("t", "retire", expected_revision=1),
            lambda: client.create("Child", repo=REPO, standing_parent_id="parent"),
            lambda: client.list(repo=REPO, standing_parent_id="parent"),
        ):
            with pytest.raises(DispatchUpgradeRequired):
                call()
        assert {request.url.path for request in requests} == {"/health"}
        with pytest.raises(ValueError, match="unsupported"):
            client.change_standing_lifecycle("t", "delete", expected_revision=1)
        client.create("Plain", repo=REPO, standing_parent_id=None)
    created = requests[-1]
    assert created.url.path == "/tasks"
    assert "standing_parent_id" not in json.loads(created.content)


@pytest.mark.parametrize("writer", ["absent", "finite-only"])
def test_client_refuses_attachment_without_relationship_owner(tmp_path, writer):
    if writer == "absent":
        queue = TaskQueue(tmp_path / "queue.sqlite3")
    else:
        queue, owner = owned_queue(tmp_path)
        owner.standing_supported = False
    requests: list[httpx.Request] = []
    with app(queue) as api, connected(api, requests) as client:
        assert client.health()["standing_charters"]["attachment"] is False
        standing = client.reconcile_standing_charter("Dispatcher", DECLARATION, repo=REPO)
        posts = len([request for request in requests if request.method == "POST"])
        for call in (
            lambda: client.reserve_standing_charter(
                standing["id"], workspace(tmp_path), expected_revision=standing["standing_revision"],
            ),
            lambda: client.accept_standing_charter(
                standing["id"], workspace(tmp_path), expected_revision=1, expected_generation=1,
                owner_session_id="native", expected_head_revision=1,
            ),
            lambda: client.cancel_standing_reservation(
                standing["id"], workspace(tmp_path), expected_revision=1, expected_generation=1,
            ),
            lambda: client.release_standing_attachment(
                standing["id"], workspace(tmp_path), expected_revision=1, expected_generation=1,
                owner_session_id="native", expected_conclusion_revision=1,
            ),
        ):
            with pytest.raises(DispatchError) as refused:
                call()
            assert refused.value.status_code == 409
            assert not isinstance(refused.value, DispatchUpgradeRequired)
        assert len([request for request in requests if request.method == "POST"]) == posts
    assert queue.get(standing["id"]).status == standing["status"]


def test_cli_names_exact_revisions_without_ensure_or_failover(tmp_path, monkeypatch, capsys):
    queue, owner = owned_queue(tmp_path)
    requests: list[httpx.Request] = []
    options: list[dict] = []
    with app(queue) as api:
        def client(_args, **kwargs):
            options.append(kwargs)
            return connected(api, requests)

        monkeypatch.setattr(cli, "_client", client)
        monkeypatch.setattr(cli, "_enrich", lambda result, **_: result)
        monkeypatch.setattr(cli, "_scope_repo", lambda args: REPO)

        def run(*argv: str):
            assert cli.main(list(argv)) == 0
            return json.loads(capsys.readouterr().out)

        standing = run("standing", "reconcile", "--title", "Dispatcher", *flags(DECLARATION))
        reserved = run(
            "standing", "reserve", standing["id"],
            "--expected-revision", str(standing["standing_revision"]), *flags(workspace(tmp_path)),
        )
        assert reserved["status"] == "claimed" and len(owner.claims) == 1
        child = run("create", "Child", "--standing-parent", standing["id"])
        assert child["standing_parent_id"] == standing["id"]
        assert [task["id"] for task in run("list", "--standing-parent", standing["id"])] == [
            child["id"],
        ]
        retiring = run(
            "standing", "retire", standing["id"],
            "--expected-revision", str(reserved["standing_revision"]),
        )
        assert retiring["standing_revision"] == reserved["standing_revision"] + 1
        cancelled = run(
            "standing", "cancel", standing["id"],
            "--expected-revision", str(retiring["standing_revision"]),
            "--expected-generation", str(reserved["generation"]), *flags(workspace(tmp_path)),
        )
        assert cancelled["status"] == "queued" and cancelled["owner"] is None
        assert cancelled["generation"] == reserved["generation"] + 1
        assert [claim["phase"] for claim in owner.claims.values()] == ["closed"]
        retired = run(
            "standing", "settle-retirement", standing["id"],
            "--expected-revision", str(cancelled["standing_revision"]),
        )
        assert retired["standing_charter"]["lifecycle"] == "retired"
        before = len(options)
        no_nonce = {k: v for k, v in workspace(tmp_path).items() if k != "creation_nonce"}
        for argv in (
            ["standing", "reenroll", standing["id"], "--expected-revision", "0"],
            ["standing", "reenroll", standing["id"], "--expected-rev", "1"],
            ["standing", "reserve", standing["id"], "--expected-revision", "1", *flags(no_nonce)],
            ["standing", "cancel", standing["id"], "--expected-revision", "1", *flags(workspace(tmp_path))],
        ):
            with pytest.raises(SystemExit) as exited:
                cli.main(argv)
            assert exited.value.code == 2
        assert len(options) == before
    assert [option for option in options if option] == [
        {"ensure": False, "allow_ssh_failover": False},
    ] * 5
    assert options.count({}) == 2
    assert queue.get(standing["id"]).standing_revision == retired["standing_revision"]


def test_finite_routes_never_conclude_or_claim_a_standing_task(tmp_path):
    queue = TaskQueue(tmp_path / "queue.sqlite3")
    with app(queue) as api:
        standing = api.post(
            "/standing-charters", json={"title": "D", "repo": REPO, "declaration": DECLARATION},
            headers=CONTROL,
        ).json()
        finite = api.post("/tasks", json={"title": "Finite", "repo": REPO}, headers=DATA).json()
        claimed = api.post("/claim", json={"worker_id": "old", "repo": REPO}, headers=DATA)
        assert claimed.json()["id"] == finite["id"]
        refused = [
            api.post("/claim", json={
                "worker_id": "old", "repo": REPO, "task_id": standing["id"]}, headers=DATA),
            api.post(f"/tasks/{standing['id']}/complete", json={"worker_id": "old"}, headers=DATA),
            api.post(f"/tasks/{standing['id']}/abandon", json={
                "worker_id": "old", "permitted": True, "reason": "legacy"}, headers=DATA),
        ]
        assert [response.status_code for response in refused] == [409, 409, 409]
        assert api.post("/claim", json={"worker_id": "old", "repo": REPO}, headers=DATA).json() is None
    unchanged = queue.get(standing["id"])
    assert (unchanged.status, unchanged.standing_revision) == (
        standing["status"], standing["standing_revision"],
    )


def test_remote_dispatch_forwards_standing_parent_filters():
    parser = cli.build_parser()
    create = parser.parse_args([
        "create", "Child", "--standing-parent", "parent", "--target-machine", "peer",
    ])
    argv = remote_dispatch.build_remote_create_argv(create, repo=REPO, has_payload=False)
    assert argv[argv.index("--standing-parent") + 1] == "parent"
    browse = parser.parse_args(["list", "--standing-parent", "parent"])
    argv = remote_dispatch.build_remote_browse_argv("list", browse, repo=REPO)
    assert argv[argv.index("--standing-parent") + 1] == "parent"
