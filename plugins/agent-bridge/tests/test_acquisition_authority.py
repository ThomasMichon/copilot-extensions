"""Tracking-only acquisition reads retain independent native-head authority."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from agent_bridge import acquisition_authority as authority
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest

pytestmark = pytest.mark.contract("agent_bridge.acquisition.worktree_owner")

_VECTORS = json.loads((
    Path(__file__).resolve().parents[1]
    / "contract/fixtures/http/current/workspace-exact-owner-generation-41.json"
).read_text(encoding="utf-8"))


def _owner_result(row):
    if isinstance(row, dict):
        row = {
            "repo": "project-a", "creation_nonce": "original-allocation",
            "session_count": 0, "last_session_id": None, "execution_leg": None,
            "execution_leg_opaque": False, "session_backend": None,
            "session_backend_opaque": False, "sessions_indexed": True,
            **row,
        }
    return {"version": 1, "ok": True, "worktree": row}


@pytest.fixture
def owner_process(monkeypatch):
    from types import SimpleNamespace

    def configure(stdout=b"", stderr=b"", returncode=7, environment=None):
        process = SimpleNamespace(
            returncode=returncode,
            communicate=AsyncMock(return_value=(stdout, stderr)),
        )
        factory = AsyncMock(return_value=process)
        monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda: ["synthetic-owner"])
        monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: environment or {})
        monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", factory)
        return process, factory

    return configure


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    ["replace-dead-head", "check", "--stdin"],
    ["replace-dead-head", "apply", "--permit", "--stdin"],
])
async def test_replacement_owner_input_uses_stdin_not_argv(owner_process, operation):
    process, factory = owner_process(b'{"version":1}', returncode=0)
    payload = {"contract": "agent-worktrees/conditional-head-replacement", "version": 1,
               "worktree_id": "target", "replacement_activation": "synthetic-activation"}
    assert await authority.worktree_command(operation, "project-a", input_json=payload) == {"version": 1}
    process.communicate.assert_awaited_once_with(json.dumps(payload).encode("utf-8"))
    assert factory.call_args.args == ("synthetic-owner", "--project", "project-a", *operation)
    assert factory.call_args.kwargs["stdin"] == authority.asyncio.subprocess.PIPE


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,payload", [
    (["create"], {"unexpected": "input"}),
    (["replace-dead-head", "observe"], {"unexpected": "input"}),
    (["replace-dead-head", "apply", "--permit", "--stdin"], {"value": "x" * 65536}),
])
async def test_invalid_owner_input_refuses_before_subprocess(owner_process, operation, payload):
    _, factory = owner_process()
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(operation, "project-a", input_json=payload)
    assert refused.value.code == "owner_input_invalid"
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_json_failure_survives_warning_stderr(owner_process):
    process, factory = owner_process(
        json.dumps({
            "version": 1, "error": "registration acknowledgement is unknown",
            "stage": "session_register", "code": "outcome_unknown",
        }).encode(),
        b"optional native source warning\n",
    )
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["bind-session"], "project-a")
    assert refused.value.code == "owner_refused"
    assert refused.value.effect is None
    assert "registration acknowledgement is unknown" in str(refused.value)
    assert "stage=session_register" in str(refused.value)
    assert "code=outcome_unknown" in str(refused.value)
    assert "stderr: optional native source warning" in str(refused.value)
    assert "exit_code=7" in str(refused.value)
    process.communicate.assert_awaited_once()
    factory.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout,stderr,expected,forbidden", [
    ({"error": {"message": "nested owner failure", "stage": "bind", "code": "denied",
                "effect": "not_started",
                "environment": {"SECRET": "never-echo-environment"}}},
     b"warning", "nested owner failure (stage=bind, code=denied)", "never-echo-environment"),
    (b"opaque output never-echo-opaque", b"safe warning", "safe warning", "never-echo-opaque"),
    (b'{"error": "never-echo-malformed"', b"safe warning", "safe warning", "never-echo-malformed"),
    (["never-echo-array"], b"", "no structured error or stderr diagnostics", "never-echo-array"),
    ({"plan": {"environment": {"SECRET": "never-echo-plan"}}}, b"",
     "no structured error or stderr diagnostics", "never-echo-plan"),
    (b"\xff\xfeopaque", b"", "no structured error or stderr diagnostics", "opaque"),
    ({"error": "never-echo-oversized" + "x" * 65536}, b"safe warning",
     "safe warning", "never-echo-oversized"),
])
async def test_owner_failure_selects_only_bounded_error_fields(
    owner_process, stdout, stderr, expected, forbidden,
):
    raw = stdout if isinstance(stdout, bytes) else json.dumps(stdout).encode()
    owner_process(raw, stderr)
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["record"], "project-a")
    message = str(refused.value)
    assert refused.value.code == "owner_refused" and refused.value.effect is None
    assert expected in message and forbidden not in message
    assert "exit_code=7" in message and len(message) <= 1500


@pytest.mark.asyncio
async def test_owner_failure_redacts_before_output_truncation(owner_process):
    secret = "synthetic-sensitive-" + "s" * 900
    owner_process(
        json.dumps({
            "error": "owner refused " + secret + " password=never-echo-password",
            "stage": "b" * 200, "code": "c" * 200,
            "environment": {"TOKEN": "never-echo-environment"},
        }).encode(),
        ("\x1b[31mwarning " + secret + " cookie=never-echo-cookie\x1b[0m").encode(),
        environment={"DIAGNOSTIC_TEST_TOKEN": secret},
    )
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["record"], "project-a")
    message = str(refused.value)
    assert "owner refused" in message and "[REDACTED]" in message
    assert not any(value in message for value in (
        secret, "s" * 32, "never-echo-password", "never-echo-cookie",
        "never-echo-environment", "\x1b",
    ))
    assert len(message) <= 1500


@pytest.mark.asyncio
async def test_owner_timeout_preserves_unknown_outcome_without_retry(owner_process):
    process, factory = owner_process(returncode=None)
    process.communicate.side_effect = TimeoutError("synthetic acknowledgement timeout")
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["bind-session"], "project-a")
    assert refused.value.code == "owner_operation_pending"
    assert refused.value.effect is None
    assert "exit_code" not in str(refused.value)
    process.communicate.assert_awaited_once()
    factory.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_preparation_refusal_remains_typed(owner_process):
    owner_process(json.dumps({
        "plugin_preparation_version": 1, "native_launch_started": False,
        "worktree_id": "worktree-a", "error": "synthetic preparation refused",
    }).encode(), b"optional warning", returncode=1)
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command([
            "create", "--bridge-plugin-request", "request.json", "--worktree-id", "worktree-a",
        ], "project-a")
    assert refused.value.code == "plugin_preparation_failed"
    assert str(refused.value) == "synthetic preparation refused"
    assert refused.value.effect is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {}, {"bound": False}, {"is_head": False}, {"worktree_id": "other"},
    {"session": "other"}, {"head_session": "other"},
])
async def test_owner_success_and_head_validation_are_unchanged(owner_process, change):
    payload = {
        "version": 1, "bound": True, "is_head": True, "worktree_id": "worktree-a",
        "session": "native-a", "head_session": "native-a", **change,
    }
    owner_process(json.dumps(payload).encode(), b"harmless warning", returncode=0)
    args = ["bind-session", "--session-id", "native-a", "--worktree-id", "worktree-a"]
    if change:
        with pytest.raises(AcquisitionRefused) as refused:
            await authority.worktree_command(args, "project-a")
        assert refused.value.code == "native_binding_unproven"
    else:
        assert await authority.worktree_command(args, "project-a") == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["json-error", "opaque-error", "success"])
async def test_real_synthetic_owner_child_preserves_streams_and_exit(
    monkeypatch, tmp_path, mode,
):
    script = tmp_path / "synthetic_owner.py"
    payload = {
        "version": 1, "bound": True, "is_head": True, "worktree_id": "worktree-a",
        "session": "native-a", "head_session": "native-a",
    }
    script.write_text(
        "import json, sys\n"
        "mode = sys.argv[1]\n"
        "sys.stderr.write('synthetic owner warning\\n')\n"
        f"success = {payload!r}\n"
        "print(json.dumps(success if mode == 'success' else "
        "{'version': 1, 'error': 'synthetic owner failure'}) "
        "if mode != 'opaque-error' else 'never-echo-opaque')\n"
        "raise SystemExit(0 if mode == 'success' else 7)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix",
                        lambda: [sys.executable, "-I", str(script), mode])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: os.environ.copy())
    args = ["bind-session", "--session-id", "native-a", "--worktree-id", "worktree-a"]
    if mode == "success":
        assert await authority.worktree_command(args, "project-a") == payload
    else:
        with pytest.raises(AcquisitionRefused) as refused:
            await authority.worktree_command(args, "project-a")
        message = str(refused.value)
        assert refused.value.code == "owner_refused" and refused.value.effect is None
        assert "exit_code=7" in message and "synthetic owner warning" in message
        assert "never-echo-opaque" not in message
        if mode == "json-error":
            assert "synthetic owner failure" in message


def test_owner_diagnostic_survives_existing_http_refusal_envelope(owner_process, acquisition_request):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge.routes import acquisitions

    owner_process(json.dumps({"error": "owner acknowledgement outcome unknown"}).encode(),
                  b"optional warning")

    async def acquire(_request):
        return await authority.worktree_command(["bind-session"], "project-a")

    app = FastAPI()
    app.state.session_manager = SimpleNamespace(is_draining=False)
    app.state.acquisition_runtime = SimpleNamespace(acquire=acquire)
    app.include_router(acquisitions.router)
    with TestClient(app) as api:
        response = api.post("/api/v1/acquisitions", json={
            **acquisition_request.model_dump(),
            "protocol_version": acquisitions.ACQUISITION_PROTOCOL_VERSION,
        })
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "owner_refused"
    assert detail["delivery_id"] == acquisition_request.delivery_id
    assert "owner acknowledgement outcome unknown" in detail["message"]
    assert "optional warning" in detail["message"] and "exit_code=7" in detail["message"]
    assert "effect" not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "conflict", "terminal"])
async def test_ground_keyed_creation_preserves_only_typed_pending(monkeypatch, status):
    import json
    from types import SimpleNamespace

    process = SimpleNamespace(
        returncode=3, communicate=AsyncMock(return_value=(
            json.dumps({"creation": {"version": 1, "status": status}}).encode(), b"optional warning",
        )),
    )
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["create", "--creation-key", "logical-work", "--json"], "project-a")
    assert refused.value.code == ("owner_operation_pending" if status == "pending" else "owner_refused")


@pytest.fixture
def acquisition_request():
    return AcquisitionRequest.model_validate({
        "target": "task-worker", "mode": "cli", "task_id": "task-a",
        "task_generation": 0, "worktree_id": "worktree-a", "allocation": "created",
        "conversation_retired": False, "reservation_key": "reservation-a",
        "delivery_id": "delivery-a", "prompt": "Perform the assigned task",
        "project": "project-a",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("rows,code", [
    ([], "owner_response_invalid"),
    ([{"id": "other", "path": "/fixture", "status": "active"}], "owner_response_invalid"),
    ([{"id": "worktree-a", "status": "active"}], "owner_response_invalid"),
    ([{"id": "worktree-a", "path": "/fixture"}] * 2, "owner_response_invalid"),
    *[([{"id": "worktree-a", "path": "/fixture", "status": status}], "worktree_terminal")
      for status in ("finalized", "complete", "completed", "archived")],
])
async def test_invalid_tracking_record_refuses_before_head(
    acquisition_request, monkeypatch, rows, code,
):
    command = AsyncMock(return_value=_owner_result(rows[0] if len(rows) == 1 else rows))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == code
    command.assert_awaited_once_with(
        ["record", "--worktree-id", "worktree-a", "--json"], "project-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("head,code", [
    ({"tracked": False}, "head_unknown"),
    ({"tracked": True, "head_session": "native-a", "active": True, "state": "pending"},
     "transition_pending"),
])
async def test_cached_display_never_overrides_native_head(
    acquisition_request, monkeypatch, head, code,
):
    command = AsyncMock(side_effect=[
        _owner_result({
            "id": "worktree-a", "path": "/fixture", "status": "active",
            "state": "resolved", "session_count": 0,
        }),
        head,
    ])
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == code
    assert command.await_args_list[1].args == (
        ["head-session", "--worktree-id", "worktree-a", "--json"], "project-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("project,path,code", [
    ("queue-repo", "/fixture", "workspace_identity_conflict"),
    (None, "/fixture", "owner_response_invalid"),
    ("execution-repo", "/other-path", "workspace_identity_conflict"),
    ("execution-repo", None, "owner_response_invalid"),
])
async def test_workspace_owner_identity_must_match_request(
    acquisition_request, monkeypatch, project, path, code,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "project": "execution-repo", "worktree_path": "/fixture",
    })
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "repo": project, "path": path, "status": "active",
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code == code
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repo,nonce", _VECTORS["legacy_metadata_cases"])
async def test_version_one_accepts_owner_supported_legacy_metadata(
    acquisition_request, monkeypatch, repo, nonce,
):
    assert acquisition_request.version == 1
    command = AsyncMock(side_effect=[
        _owner_result({
            "id": "worktree-a", "repo": repo, "path": "/fixture", "status": "active",
            "creation_nonce": nonce, "session_count": 1, "last_session_id": "native-a",
        }),
        {"tracked": True, "head_session": "native-a", "active": True, "state": "active"},
    ])
    monkeypatch.setattr(authority, "worktree_command", command)
    row, head = await authority.verify_worktree(acquisition_request)
    assert (row["repo"], row["creation_nonce"], head) == (repo, nonce, "native-a")
    assert command.await_count == 2
    assert all(call.args[1] == "project-a" for call in command.await_args_list)


@pytest.mark.asyncio
async def test_version_two_companion_repository_still_refuses(
    acquisition_request, monkeypatch,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "worktree_path": "/fixture",
    })
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "repo": "companion-repo", "path": "/fixture", "status": "active",
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code == "workspace_identity_conflict"
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("last_session", ["", " ", 0, False, {}])
async def test_legacy_last_session_identity_remains_strict(
    acquisition_request, monkeypatch, last_session,
):
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "path": "/fixture", "status": "active",
        "creation_nonce": None, "last_session_id": last_session,
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_response_invalid"
    assert command.await_count == 1
