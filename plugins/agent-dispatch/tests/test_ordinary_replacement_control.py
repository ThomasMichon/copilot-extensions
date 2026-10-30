"""Only control authority may grant replacement after receiving-side checks."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli, ordinary_replacement_control as control
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue_records import TaskError
from tests.test_ordinary_replacement import recovery as recovery


@pytest.fixture
def control_case(recovery, monkeypatch):
    queue, task, previous, args = recovery
    args = dict(args)
    record = args.pop("worktree_record")
    calls = []

    def read(reservation, task_id, **kwargs):
        calls.append("record")
        assert reservation["key"] == previous.key and task_id == task.id
        return dict(record)

    def verify(target, project):
        calls.append("preflight")
        assert target["creation_nonce"] == record["creation_nonce"]
        assert target["expected_head"] == record["last_session_id"]
        assert project == record["repo"]

    monkeypatch.setattr(control, "read_owner", read)
    monkeypatch.setattr(control, "verify_receivers", verify)
    app = TestClient(create_app(queue, token="worker-test", control_token="control-test"))
    return queue, task, previous, args, record, calls, app


def client_for(app, token):
    def send(request):
        response = app.request(
            request.method, request.url.path, content=request.content, headers=dict(request.headers),
        )
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    return DispatchClient(
        "http://test", token="worker-test", control_token=token,
        transport=httpx.MockTransport(send),
    )


@pytest.mark.parametrize("token,code", [("worker-test", 403), ("control-test", 200)])
def test_control_client_enforces_separate_authority(control_case, token, code):
    queue, task, previous, args, _, calls, app = control_case
    with client_for(app, token) as client:
        if code != 200:
            with pytest.raises(DispatchError) as failure:
                client.authorize_native_replacement(task.id, **args)
            assert failure.value.status_code == code
            assert not calls
        else:
            result = client.authorize_native_replacement(task.id, **args)
            assert result["launched"] is False
            assert result["authorization"]["task_generation"] == task.generation + 1
            assert calls == ["record", "preflight"]
    assert queue.get_reservation(previous.key).conversation_retired is (code == 200)


@pytest.mark.parametrize("change", [
    {"expected_generation": True}, {"expected_head_revision": "3"},
    {"expected_lifecycle_revision": 2**63}, {"worktree_record": {}},
    {"permitted": 1},
])
def test_malformed_request_never_reaches_owner(control_case, change):
    queue, task, previous, args, _, calls, app = control_case
    with client_for(app, "control-test") as client:
        response = app.post(
            f"/spawn-reservations/tasks/{task.id}/authorize-replacement",
            json={**args, **change}, headers=client._control_headers(),
        )
    assert response.status_code == 422
    assert not calls
    assert queue.get(task.id).generation == task.generation
    assert not queue.get_reservation(previous.key).conversation_retired


def test_permission_required_before_readiness(control_case):
    queue, task, _, args, _, calls, app = control_case
    with client_for(app, "control-test") as client:
        with pytest.raises(DispatchError):
            client.authorize_native_replacement(task.id, **{**args, "permitted": False})
    assert not calls
    assert queue.get(task.id).generation == task.generation


@pytest.mark.parametrize("failure", ["old-runtime", "changed-generation", "changed-head", "timeout"])
def test_readiness_and_queue_races_do_not_grant(control_case, monkeypatch, failure):
    queue, task, previous, args, record, _, app = control_case

    def verify(*_args):
        if failure == "old-runtime":
            raise TaskError("protocol unavailable")
        if failure == "timeout":
            raise control.subprocess.TimeoutExpired("owner", 30)
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task.id,))

    monkeypatch.setattr(control, "verify_receivers", verify)
    if failure == "changed-head":
        record["last_session_id"] = "different-head"
    with client_for(app, "control-test") as client:
        with pytest.raises(DispatchError) as error:
            client.authorize_native_replacement(task.id, **args)
    assert error.value.status_code == (503 if failure == "timeout" else 409)
    assert not queue.get_reservation(previous.key).conversation_retired
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM ordinary_native_replacements").fetchone()[0] == 0


@pytest.mark.parametrize("failure", [None, "bridge", "preflight-support", "candidate-support", "eligible", "target"])
def test_receiver_commands_are_readonly_and_checked(monkeypatch, failure):
    target = {
        "worktree_id": "worktree-a", "worktree_path": "/worktree-a", "creation_nonce": "allocation",
        "expected_head": "11111111-1111-4111-8111-111111111111",
        "expected_head_revision": 3, "expected_lifecycle_revision": 5,
    }
    monkeypatch.setattr(control, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(control, "agent_worktrees_launch_prefix", lambda: ["owner"])
    monkeypatch.setattr(control, "agent_worktrees_environment", lambda: {"OWNER": "yes"})
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        if argv[0] == "bridge":
            data = {
                "contract": control.replacement.CONTRACT, "version": 1,
                "supported": True, "required_protocol": 51 if failure == "bridge" else 52,
            }
        elif argv[-1] == "capabilities":
            data = {
                "contract": control.OWNER_CONTRACT, "version": 1, "versions": [1],
                "preflight": 0 if failure == "preflight-support" else 1,
                "candidate_observation": 0 if failure == "candidate-support" else 1,
            }
        else:
            assert argv == ["owner", "--project", "example", "replace-dead-head", "check", "--stdin"]
            assert json.loads(kwargs["input"]) == {
                "contract": control.OWNER_CONTRACT, "version": 1, **target,
            }
            assert kwargs["env"] == {"OWNER": "yes"}
            data = {
                "contract": control.OWNER_CONTRACT, "version": 1,
                "eligible": failure != "eligible", "target": {} if failure == "target" else target,
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))

    monkeypatch.setattr(control.subprocess, "run", run)
    if failure:
        with pytest.raises(TaskError):
            control.verify_receivers(target, "example")
    else:
        control.verify_receivers(target, "example")
        assert len(seen) == 3
    assert seen[0][0] == ["bridge", "native-replacement-readiness"]
    assert all("apply" not in argv and "create" not in argv for argv, _ in seen)


def test_cli_carries_exact_control_inputs():
    args = cli.build_parser().parse_args([
        "reservations", "authorize-replacement", "task-a",
        "--reservation-key", "reservation-a", "--worktree", "worktree-a",
        "--expected-generation", "0", "--expected-head", "native-a",
        "--expected-head-revision", "3", "--expected-lifecycle-revision", "5",
        "--permit", "--confirm-no-live-holder", "--reason", "checked",
    ])
    assert args.expected_head_revision == 3 and args.expected_lifecycle_revision == 5
    assert args.permit and args.confirm_no_live_holder
