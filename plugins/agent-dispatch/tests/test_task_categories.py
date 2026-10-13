"""Category preservation and real coordinator/client query contracts."""

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import board_cli, remote_dispatch
from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Task, TaskQueue, TaskError, ProducerFenceError
from agent_dispatch.producers.schedule import _create_kwargs

REPO = "github.com/example/harness"
PROVENANCE = dict(repo=REPO, source="schedule", origin_ref="schedule/health", labels=["health"])


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_legacy_database_default_is_additive_and_preserves_rows(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, repo TEXT, "
            "created_at REAL, updated_at REAL, status TEXT)"
        )
        conn.execute("INSERT INTO tasks VALUES ('old','keep',?,10,20,'completed')", (REPO,))
    queue = TaskQueue(path)
    before = asdict(queue.get("old"))
    assert before["category"] == "work"
    assert before["created_at"] == 10 and before["updated_at"] == 20
    assert asdict(TaskQueue(path).get("old")) == before


def test_existing_positional_task_fields_are_unchanged():
    task = Task("id", "title", "prompt", "queued", REPO, ["capability"])
    assert task.category == "work" and task.requires == ["capability"]


def test_before_limit_list_find_sweep_and_unchanged_dedup(queue):
    work = queue.create("needle work", repo=REPO, now=1, dedup_key="subject")
    for n in range(205):
        queue.create(f"needle upkeep {n}", repo=REPO, category="maintenance", now=n + 2)
    for tasks in (
        queue.list(category="work", limit=1),
        queue.find("needle", category="work", limit=1),
        queue.sweep(category="work", limit=1),
    ):
        assert [task.id for task in tasks] == [work.id]
    assert len(queue.list(category="all", limit=300)) == 206
    assert (
        queue.create(
            "different category", repo=REPO, category="maintenance", dedup_key="subject"
        ).id
        == work.id
    )
    with pytest.raises(TaskError):
        queue.create("bad", repo=REPO, category="script")


def test_classification_preview_fencing_audit_and_full_preservation(queue):
    task = queue.create("upkeep", **PROVENANCE, now=1)
    reservation, _created = queue.reserve_spawn(task.id, reserved_by="supervisor")
    ambiguous = queue.create(
        "upkeep", repo=REPO, source="schedule", origin_ref="schedule/other", labels=["health"]
    )
    owner = "machine/worktree"
    queue.claim_one(owner, task_id=task.id)
    queue.start(task.id, owner)
    queue.bind_owner_session(task.id, owner, "session")
    queue.record_progress(task.id, owner, phase="running", summary="keep")
    queue.complete(task.id, owner, result={"statistics": {"count": 2}, "logs": ["log-ref"]})
    before = asdict(queue.get(task.id))
    events = queue.events(task.id)
    attachments = queue.attachment_history(task.id)
    progress = queue.progress_log(task.id)
    retained_reservation = queue.get_reservation(reservation.key)
    preview = queue.classify_maintenance(**PROVENANCE)
    assert preview["changed"] == 0 and preview["provenance"]["ids"] == [task.id]
    assert asdict(queue.get(task.id)) == before
    with pytest.raises(TaskError):
        queue.classify_maintenance(**PROVENANCE, apply=True, preview_token="stale")
    applied = queue.classify_maintenance(
        **PROVENANCE, apply=True, preview_token=preview["preview_token"]
    )
    assert applied["changed"] == 1
    after = asdict(queue.get(task.id))
    assert after == {**before, "category": "maintenance"}
    assert queue.get(ambiguous.id).category == "work"
    assert queue.attachment_history(task.id) == attachments
    assert queue.progress_log(task.id) == progress
    assert queue.get_reservation(reservation.key) == retained_reservation
    audited = queue.events(task.id)
    assert audited[:-1] == events
    assert json.loads(audited[-1]["note"])["operation"] == "category-classification"
    assert (
        queue.classify_maintenance(
            **PROVENANCE, apply=True, preview_token=preview["preview_token"]
        )["changed"]
        == 0
    )
    assert queue.events(task.id) == audited


def test_classification_rejects_incomplete_or_changed_provenance(queue):
    with pytest.raises(TaskError):
        queue.classify_maintenance(**{**PROVENANCE, "labels": []})
    queue.create("first", **PROVENANCE)
    preview = queue.classify_maintenance(**PROVENANCE)
    queue.create("second", **PROVENANCE)
    with pytest.raises(TaskError):
        queue.classify_maintenance(
            **PROVENANCE, apply=True, preview_token=preview["preview_token"]
        )
    assert all(task.category == "work" for task in queue.list())


def test_classification_filters_corroboration_before_bound_and_leaves_ambiguous_rows(queue):
    selected = queue.create("selected", **PROVENANCE)
    malformed = queue.create("ambiguous", **PROVENANCE)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET labels = ? WHERE id = ?", ('["health",2]', malformed.id))
    preview = queue.classify_maintenance(**PROVENANCE, limit=1)
    assert preview["provenance"]["ids"] == [selected.id]
    queue.classify_maintenance(
        **PROVENANCE, limit=1, apply=True, preview_token=preview["preview_token"]
    )
    with queue._connect() as conn:
        assert (
            conn.execute("SELECT category FROM tasks WHERE id = ?", (malformed.id,)).fetchone()[0]
            == "work"
        )
    queue.create("another", **PROVENANCE)
    with pytest.raises(TaskError, match="exceeds limit"):
        queue.classify_maintenance(**PROVENANCE, limit=1)


def test_managed_request_default_hash_and_classification_replay(queue):
    fence = queue.handoff_producer_scope(
        REPO, "schedule", producer_id="producer", expected_generation=0, required_label="health"
    )
    fields = dict(
        PROVENANCE,
        producer_scope={"repo": REPO, "source": "schedule"},
        producer_id="producer",
        producer_generation=1,
        producer_capability=fence.producer_capability,
        producer_request_id="request",
    )
    task = queue.create("health", **fields)
    assert queue.create("health", **fields, category="work").id == task.id
    with pytest.raises(ProducerFenceError):
        queue.create("health", **fields, category="maintenance")
    preview = queue.classify_maintenance(**PROVENANCE)
    queue.classify_maintenance(**PROVENANCE, apply=True, preview_token=preview["preview_token"])
    assert queue.create("health", **fields).category == "maintenance"
    maintenance = queue.create(
        "health", **{**fields, "producer_request_id": "maintenance"}, category="maintenance"
    )
    assert (
        queue.create(
            "health", **{**fields, "producer_request_id": "maintenance"}, category="maintenance"
        ).id
        == maintenance.id
    )
    for candidate in (task, maintenance):
        claimed = queue.claim_one("worker/worktree", repo=REPO, task_id=candidate.id)
        assert claimed.id == candidate.id and claimed.category == "maintenance"


def test_http_query_parity_and_invalid_values(queue):
    work = queue.create("needle work", repo=REPO, now=1)
    queue.create("needle maintenance", repo=REPO, category="maintenance", now=2)
    with TestClient(create_app(queue)) as http:
        assert http.get("/health").json()["task_categories"] == 1
        for params in ({}, {"q": "needle"}, {"sweep": True}):
            response = http.get("/tasks", params={**params, "category": "work", "limit": 1})
            assert response.status_code == 200
            assert response.json()[0]["id"] == work.id
        assert len(http.get("/tasks", params={"category": "all"}).json()) == 2
        assert http.get("/tasks", params={"category": "script"}).status_code == 422
        assert (
            http.post(
                "/tasks", json={"title": "bad", "repo": REPO, "category": "script"}
            ).status_code
            == 422
        )


def test_actual_cli_and_board_contract_with_first_touch_coordinator(queue, monkeypatch, capsys):
    from agent_dispatch import __main__ as cli

    queue.create("ordinary", repo=REPO, now=1)
    queue.create("upkeep", repo=REPO, category="maintenance", now=2)
    with TestClient(create_app(queue)) as http:

        def exchange(request):
            response = http.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        def client(_):
            return DispatchClient("http://testserver", transport=httpx.MockTransport(exchange))

        monkeypatch.setattr(cli, "_client", client)
        monkeypatch.setattr(cli, "_enrich", lambda tasks: tasks)
        monkeypatch.setattr(cli, "_scope_repo", lambda args: REPO)
        assert cli.main(["list", "--repo", REPO, "--limit", "1"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["title"] == "ordinary"
        assert cli.main(["list", "--repo", REPO, "--category", "all"]) == 0
        assert len(json.loads(capsys.readouterr().out)) == 2
        with client(None) as c:
            tasks = c.list(category="maintenance", limit=1)
        rows = board_cli._build(tasks, machine="worker", recent_mins=120)
        assert rows[0]["title"] == "upkeep" and rows[0]["category"] == "maintenance"


def test_native_cli_rpc_first_touch_and_both_pivots(queue):
    import os
    import subprocess
    import sys
    from agent_procutil import no_window_kwargs
    from tests.test_mcp_http import _boot

    url, stop = _boot(create_app(queue))
    source = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": source, "AGENT_DISPATCH_SUPERVISE_MACHINE": "worker"}

    def run(module, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", module, *arguments],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            **no_window_kwargs(),
        )
        return json.loads(result.stdout)

    try:
        with DispatchClient(url) as producer:
            ordinary = producer.create("ordinary", repo=REPO)
            upkeep = producer.create("upkeep", repo=REPO, category="maintenance")
        assert ordinary["category"] == "work" and upkeep["category"] == "maintenance"
        assert (
            run("agent_dispatch", "--url", url, "list", "--repo", REPO, "--limit", "1")[0]["id"]
            == ordinary["id"]
        )
        assert (
            len(run("agent_dispatch", "--url", url, "list", "--repo", REPO, "--category", "all"))
            == 2
        )
        env["AGENT_DISPATCH_URL"] = url
        env["AGENT_DISPATCH_ENDPOINT"] = url
        rows = run(
            "agent_dispatch.board_cli",
            "--machine",
            "worker",
            "--category",
            "maintenance",
            "--limit",
            "1",
        )
        assert rows[0]["id"] == upkeep["id"]
        owner = "worker/worktree"
        queue.claim_one(owner, task_id=upkeep["id"])
        queue.start(upkeep["id"], owner)
        queue.bind_owner_session(upkeep["id"], owner, "session")
        queue.record_progress(upkeep["id"], owner, phase="checked", summary="healthy")
        queue.complete(upkeep["id"], owner, result={"logs": ["log-ref"], "count": 2})
        evidence = run("agent_dispatch", "--url", url, "inspect-task", upkeep["id"])
        assert evidence["task"]["category"] == "maintenance"
        assert evidence["progress"][0]["summary"] == "healthy"
        assert evidence["attachments"][0]["session_id"] == "session"
        assert evidence["statistics"]["attempts"] == 1
        assert evidence["result"]["result"] == {"logs": ["log-ref"], "count": 2}
    finally:
        stop()


def test_http_classification_control_auth_and_preview(queue):
    queue.create("health", **PROVENANCE)
    with TestClient(create_app(queue, control_token="control")) as http:
        assert http.post("/task-categories/classify", json=PROVENANCE).status_code == 403
        scheme = "Bear" + "er "
        headers = {"Authorization": scheme + "control"}
        preview = http.post("/task-categories/classify", json=PROVENANCE, headers=headers)
        assert preview.status_code == 200
        applied = http.post(
            "/task-categories/classify",
            json={
                **PROVENANCE,
                "apply": True,
                "preview_token": preview.json()["preview_token"],
            },
            headers=headers,
        )
        assert applied.status_code == 200 and applied.json()["changed"] == 1


@pytest.mark.parametrize("operation", ["list", "find", "sweep", "create"])
def test_client_refuses_silently_ignoring_old_peer(operation):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"} if request.url.path == "/health" else [])

    with DispatchClient("http://coordinator", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            if operation == "create":
                client.create("upkeep", repo=REPO, category="maintenance")
            elif operation == "find":
                client.find("upkeep", category="maintenance")
            elif operation == "sweep":
                client.sweep(category="maintenance")
            else:
                client.list(category="work")
    assert seen == ["/health"]


def test_old_peer_omitted_work_wire_shape():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "old"})

    with DispatchClient("http://coordinator", transport=httpx.MockTransport(handler)) as client:
        client.create("work", repo=REPO)
        client.create("work", repo=REPO, category="work")
    assert bodies == [{"title": "work", "repo": REPO}] * 2


def test_authenticated_category_client_constructs_real_headers():
    scheme = "Bear" + "er "
    seen = []

    def exchange(request):
        seen.append(request.url.path)
        expected = "client-fixture" if request.url.path == "/health" else "control-fixture"
        assert request.headers["Authorization"] == scheme + expected
        return httpx.Response(200, json={"task_categories": 1, "changed": 0})

    with DispatchClient(
        "http://coordinator",
        token="client-fixture",
        control_token="control-fixture",
        transport=httpx.MockTransport(exchange),
    ) as client:
        client.classify_maintenance(**PROVENANCE)
    assert seen == ["/health", "/task-categories/classify"]


def test_existing_completion_readback_contract_is_preserved():
    result = {"statistics": [1, 2], "logs": ["log-ref"]}
    with DispatchClient(
        "http://coordinator",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"category": "maintenance", "result": result}
            ),
        ),
    ) as client:
        assert client.complete("task", "worker", result=result)["result"] == result
    with DispatchClient(
        "http://coordinator",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"category": "maintenance"}),
        ),
    ) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.complete("task", "worker", result=result)


def test_schedule_explicit_category_and_omitted_compatibility():
    template = {"id": "health"}
    assert "category" not in _create_kwargs(template, REPO, 10)
    assert (
        _create_kwargs({**template, "category": "maintenance"}, REPO, 10)["category"]
        == "maintenance"
    )


def test_remote_browse_and_create_forward_category():
    args = SimpleNamespace(title="health", category="maintenance", machine="peer", board=True)
    for argv in (
        remote_dispatch.build_remote_create_argv(args, repo=REPO, has_payload=False),
        remote_dispatch.build_remote_browse_argv("inbox", args),
    ):
        assert argv[argv.index("--category") + 1] == "maintenance"


@pytest.mark.guard
def test_independent_pivot_manifests_and_full_inspection_access():
    directory = Path(__file__).resolve().parents[1] / "pivots"
    for filename, category in (
        ("agent-dispatch.json", "work"),
        ("agent-dispatch-maintenance.json", "maintenance"),
    ):
        manifest = json.loads((directory / filename).read_text())
        assert manifest["label"] not in {"Worktrees", "Maintenance", "Profiles"}
        assert manifest["list"][-2:] == ["--category", category]
        inspect_action = next(a for a in manifest["actions"] if a["key"] == "inspect-evidence")
        assert inspect_action["run"] == [
            "agent-dispatch",
            "inspect-task",
            "{task_id}",
            "--machine",
            "{machine}",
        ]
        assert manifest["stream"] and manifest["subscribe"]


def test_board_filters_before_raw_fetch_bound_and_refuses_old_peer(monkeypatch):
    from agent_dispatch import client_categories

    calls = []
    monkeypatch.setattr(
        client_categories, "require_endpoint_categories", lambda endpoint: calls.append(endpoint)
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return b"[]"

    def fetch(request, timeout):
        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr(board_cli.urllib.request, "urlopen", fetch)
    board_cli._fetch_raw_tasks_direct(
        SimpleNamespace(limit=1, label=None, category="maintenance"), endpoint="http://coordinator"
    )
    assert calls[0] == "http://coordinator"
    assert "category=maintenance" in calls[1] and "limit=1" in calls[1]


def test_subscription_downgrade_is_visible_not_an_empty_or_stale_success(monkeypatch):
    import io
    from agent_dispatch import board_relay

    def unsupported(*args, **kwargs):
        raise DispatchUpgradeRequired("unsupported category contract")

    monkeypatch.setattr(board_cli, "_fetch_rows", unsupported)
    out = io.StringIO()
    assert board_cli.poll_loop(SimpleNamespace(), out, [], 0, sleep=lambda _: None) == 1
    assert json.loads(out.getvalue())["type"] == "error"
    monkeypatch.setattr(board_cli, "_fetch_raw_tasks_direct", unsupported)
    with pytest.raises(DispatchUpgradeRequired):
        board_relay._Snapshot(SimpleNamespace(), "http://coordinator").full_refetch()


@pytest.mark.parametrize("category", ["script", None, "all", "", 1, ["maintenance"]])
@pytest.mark.guard
def test_schedule_registration_rejects_bad_category_without_persistence(queue, category):
    entry = {
        "id": "health",
        "title": "Health",
        "repo": REPO,
        "interval_seconds": 60,
        "category": category,
    }
    with TestClient(create_app(queue)) as http:
        assert http.post("/schedules", json=entry).status_code == 400
    assert queue.list_schedules() == []


@pytest.mark.parametrize("category", ["work", "maintenance"])
@pytest.mark.guard
def test_schedule_category_validation_preserves_valid_and_omitted_specs(queue, category):
    entry = {"id": "health", "title": "Health", "repo": REPO, "interval_seconds": 60}
    with TestClient(create_app(queue)) as http:
        assert http.post("/schedules", json=entry).status_code == 200
        previous = queue.get_schedule("health")
        assert http.post("/schedules", json={**entry, "category": None}).status_code == 400
        assert queue.get_schedule("health") == previous
        assert http.post("/schedules", json={**entry, "category": category}).status_code == 200
        assert queue.get_schedule("health").entry["category"] == category


@pytest.mark.parametrize("control,status", [(None, 503), ("configured", 403)])
@pytest.mark.guard
def test_category_control_rejection_uses_its_own_context_and_event(
    queue, monkeypatch, control, status
):
    app = create_app(queue, control_token=control)
    events = []
    monkeypatch.setattr(app.state.bus, "publish", events.append)
    with TestClient(app) as http:
        response = http.post("/task-categories/classify", json=PROVENANCE)
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["operation"] == "category_classify"
    assert detail["code"].startswith("category_control_")
    assert events[-1]["type"] == "task.categories_classify_rejected"
    assert all(event["type"] != "producer_scope.transition_rejected" for event in events)


@pytest.mark.parametrize("category", ["maintenance", "work", None, "script"])
@pytest.mark.guard
def test_schedule_client_blocks_explicit_category_before_old_peer_mutation(queue, category):
    from fastapi import FastAPI

    legacy = FastAPI()
    seen = []

    @legacy.get("/health")
    def health():
        return {"status": "ok"}

    @legacy.post("/schedules")
    def register(entry: dict):
        accepted = {key: value for key, value in entry.items() if key != "category"}
        queue.register_schedule(accepted)
        queue.create(accepted["title"], repo=REPO)
        return {"entry": accepted}

    with TestClient(legacy) as http:

        def exchange(request):
            seen.append((request.method, request.url.path))
            response = http.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient("http://legacy", transport=httpx.MockTransport(exchange)) as client:
            with pytest.raises(DispatchUpgradeRequired):
                client.register_schedule(
                    {
                        "id": "health",
                        "title": "Health",
                        "repo": REPO,
                        "interval_seconds": 60,
                        "category": category,
                    }
                )
    assert seen == [("GET", "/health")]
    assert queue.list_schedules() == [] and queue.list() == []


@pytest.mark.guard
def test_schedule_client_ordinary_legacy_registration_keeps_wire_shape():
    entry = {"id": "health", "title": "Health", "repo": REPO, "interval_seconds": 60}
    seen = []

    def exchange(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"entry": entry})

    with DispatchClient("http://legacy", transport=httpx.MockTransport(exchange)) as client:
        assert client.register_schedule(entry)["entry"] == entry
    assert seen == [("POST", "/schedules", entry)]


@pytest.mark.guard
@pytest.mark.parametrize("operation", ["read", "schedule"])
def test_category_capability_rechecked_after_same_url_rolls_back(queue, operation):
    from fastapi import FastAPI

    peer = FastAPI()
    capable = True
    seen = []

    @peer.get("/health")
    def health():
        return {"status": "ok", **({"task_categories": 1} if capable else {})}

    @peer.get("/tasks")
    def tasks():
        return (
            [{"id": "work", "category": "work"}]
            if capable
            else [
                {"id": "wrong-view", "category": "maintenance"},
            ]
        )

    @peer.post("/schedules")
    def register(entry: dict):
        accepted = {key: value for key, value in entry.items() if key != "category"}
        queue.register_schedule(accepted)
        queue.create(accepted["title"], repo=REPO)
        return {"entry": accepted}

    with TestClient(peer) as http:

        def exchange(request):
            seen.append((request.method, request.url.path))
            response = http.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://same-endpoint", transport=httpx.MockTransport(exchange)
        ) as client:
            assert client.list(category="work")[0]["id"] == "work"
            capable = False
            with pytest.raises(DispatchUpgradeRequired):
                if operation == "read":
                    client.list(category="work")
                else:
                    client.register_schedule(
                        {
                            "id": "health",
                            "title": "Health",
                            "repo": REPO,
                            "interval_seconds": 60,
                            "category": "maintenance",
                        }
                    )
    assert seen == [("GET", "/health"), ("GET", "/tasks"), ("GET", "/health")]
    assert queue.list_schedules() == [] and queue.list() == []


@pytest.mark.guard
def test_remote_inspection_unavailable_is_concise_nonzero_not_local_all_clear(monkeypatch, capsys):
    from agent_dispatch import category_cli

    monkeypatch.setattr(remote_dispatch, "is_peer_machine", lambda machine: True)

    def unavailable(*args, **kwargs):
        raise remote_dispatch.RemoteDispatchUnavailable("SSH is unavailable")

    def local(_args):
        raise AssertionError("must not read a local task as a remote fallback")

    monkeypatch.setattr(remote_dispatch, "browse_remote", unavailable)
    code = category_cli.inspect_task(
        SimpleNamespace(machine="peer", task_id="task"),
        client_factory=local,
    )
    captured = capsys.readouterr()
    assert code != 0 and captured.out == ""
    assert "unavailable" in captured.err and "peer" in captured.err
    assert "Traceback" not in captured.err


def test_reattach_real_coordinator_retains_category_and_attachment_history(queue):
    from agent_dispatch import reattach

    old = queue.create("upkeep", repo=REPO, category="maintenance", dedup_key="health")
    owner = "worker/old-worktree"
    queue.claim_one(owner, task_id=old.id, repo=REPO)
    queue.start(old.id, owner)
    queue.bind_owner_session(old.id, owner, "old-session")
    queue.complete(old.id, owner, result={"logs": ["old-log"]})
    original = asdict(queue.get(old.id))
    attachments = queue.attachment_history(old.id)
    with TestClient(create_app(queue)) as http:

        def exchange(request):
            response = http.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://coordinator", transport=httpx.MockTransport(exchange)
        ) as client:
            result = reattach.reattach(
                client,
                old.id,
                "new-session",
                local_session_verdict=lambda sid: "live",
                resume=False,
            )
    new = queue.get(result.task_id)
    assert new.category == "maintenance"
    assert new.owner_session_id == "new-session"
    assert queue.attachment_history(new.id)[0].session_id == "new-session"
    assert asdict(queue.get(old.id)) == original
    assert queue.attachment_history(old.id) == attachments


def _block_category_scan(queue, monkeypatch, name):
    import threading

    entered = threading.Event()
    release = threading.Event()
    original = queue._connect

    def connect():
        conn = original()
        if threading.current_thread().name == name:
            blocked = False

            def json_valid(raw):
                nonlocal blocked
                if not blocked:
                    blocked = True
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("category scan was not released")
                try:
                    json.loads(raw)
                except (ValueError, TypeError):
                    return 0
                return 1

            conn.create_function("json_valid", 1, json_valid)
        return conn

    monkeypatch.setattr(queue, "_connect", connect)
    return entered, release


@pytest.mark.parametrize("operation", ["create", "claim", "heartbeat"])
@pytest.mark.guard
def test_category_preview_blocked_scan_does_not_block_other_sqlite_writer(
    queue, monkeypatch, operation
):
    import threading

    task = queue.create("health", **PROVENANCE)
    if operation == "heartbeat":
        queue.claim_one("worker/worktree", task_id=task.id, repo=REPO)
    entered, release = _block_category_scan(queue, monkeypatch, "category-preview")
    results, errors = [], []

    def preview():
        try:
            results.append(queue.classify_maintenance(**PROVENANCE))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=preview, name="category-preview")
    thread.start()
    committed = False
    try:
        assert entered.wait(3), "preview must be inside the actual SQLite scan"
        with sqlite3.connect(queue.db_path, timeout=0.2, isolation_level=None) as writer:
            try:
                writer.execute("BEGIN IMMEDIATE")
                if operation == "create":
                    writer.execute(
                        "INSERT INTO tasks(id,title,prompt,status,repo) VALUES (?,?,?,?,?)",
                        ("independent", "work", "", "queued", REPO),
                    )
                elif operation == "claim":
                    writer.execute(
                        "UPDATE tasks SET status='claimed',owner='worker/worktree' WHERE id=?",
                        (task.id,),
                    )
                else:
                    writer.execute(
                        "UPDATE tasks SET last_seen_at=123,lease_expires_at=456 WHERE id=?",
                        (task.id,),
                    )
                writer.execute("COMMIT")
                committed = True
            except sqlite3.OperationalError:
                writer.execute("ROLLBACK") if writer.in_transaction else None
        assert thread.is_alive(), "writer must commit before the blocked preview is released"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert committed, "read-only preview reserved the SQLite writer"
    assert results[0]["applied"] is False and results[0]["changed"] == 0
    if operation == "create":
        assert queue.get("independent") is not None
    elif operation == "claim":
        assert queue.get(task.id).owner == "worker/worktree"
    else:
        assert queue.get(task.id).last_seen_at == 123
        assert queue.get(task.id).lease_expires_at == 456


@pytest.mark.guard
def test_category_apply_keeps_writer_serialization_and_rechecks_current_token(queue, monkeypatch):
    import threading

    task = queue.create("health", **PROVENANCE)
    preview = queue.classify_maintenance(**PROVENANCE)
    entered, release = _block_category_scan(queue, monkeypatch, "category-apply")
    results, errors = [], []

    def apply():
        try:
            results.append(
                queue.classify_maintenance(
                    **PROVENANCE,
                    apply=True,
                    preview_token=preview["preview_token"],
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=apply, name="category-apply")
    thread.start()
    try:
        assert entered.wait(3)
        with sqlite3.connect(queue.db_path, timeout=0.2, isolation_level=None) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("BEGIN IMMEDIATE")
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert results[0]["changed"] == 1 and queue.get(task.id).category == "maintenance"
    new = queue.create("new eligible row", **PROVENANCE)
    with pytest.raises(TaskError, match="current preview_token"):
        queue.classify_maintenance(
            **PROVENANCE, apply=True, preview_token=preview["preview_token"]
        )
    assert queue.get(new.id).category == "work"
