"""Compose the ordinary CLI resume owner, reservation recorder and worker start."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as core, native_keyed_admission, workspace_creation
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.creation_writer_inspection import inspect_writer
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes.sessions import _session_info
from agent_bridge import worktree_native_resume
from test_retained_metadata_recovery import ordinary_resume_fixture
from test_creation_worktree_writer import writer_creation as writer_creation
from test_workspace_creation import creation_rig as creation_rig, rig as rig

pytestmark = pytest.mark.contract("agent_bridge.retained_metadata_recovery")


@pytest.mark.asyncio
async def test_real_resume_frontdoor_records_dispatch_control_before_worker_acceptance(
    writer_creation: Any, monkeypatch: Any, tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"
    monkeypatch.syspath_prepend(str(source))
    dispatch = importlib.import_module("agent_dispatch.ordinary_bridge")
    facts = importlib.import_module("agent_dispatch.ordinary_agent_storage")
    invocation = importlib.import_module("agent_dispatch.ordinary_invocation")
    assignment = importlib.import_module("agent_dispatch.ordinary_assignment")
    support = importlib.import_module("agent_dispatch.queue_pool_placement")
    queue = importlib.import_module("agent_dispatch.queue").TaskQueue(tmp_path / "dispatch.db")
    task = queue.create("review", repo="source", exclusive_key="source-review")
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="ordinary-bridge")
    fixture = writer_creation
    row = fixture.rig.row
    row.update(dispatch_attempt={"task_id": task.id}, dispatch_attempt_opaque=False)
    queue.record_spawn_worktree(
        reservation.key, row["id"], ownership="targeted", driver="ordinary-bridge",
        allocation_project=row["repo"], worktree_path=row["path"],
    )
    queue.record_spawn_allocation(
        reservation.key, row["repo"], worktree_writer_requirement=fixture.requirement.model_dump(),
    )
    snapshot = {**asdict(task), "spawn_reservation_key": reservation.key}
    original_start = workspace_creation.start

    async def create(*args: Any, **kwargs: Any) -> Any:
        kwargs["key"] = reservation.creation_key
        session, receipt = await original_start(*args, **kwargs)
        actual_record = {**row, "last_session_id": session.acp_session_id}
        created = {
            "session_id": session.session_id, "ready": True, "status": "awaiting_first_prompt",
            "worktree_writer_requirement": fixture.requirement.model_dump(),
            "connection": {
                "native_session_id": session.acp_session_id, "worktree_id": row["id"],
                "repo": row["repo"], "target_dir": row["path"],
            },
        }
        birth = invocation.prepare(
            snapshot, asdict(queue.get_reservation(reservation.key)), created,
            "Review the assigned source", worktree_record=actual_record, agent="local",
        )
        dispatch.record_created_agent(
            queue, reservation.key, task_generation=task.generation, agent_info=created,
            startup_invocation=birth, worktree_record=actual_record,
        )
        return session, receipt

    monkeypatch.setattr(workspace_creation, "start", create)
    bound = await ordinary_resume_fixture(fixture, monkeypatch)
    row["last_session_id"] = bound.old.acp_session_id
    before = asdict(queue.get_reservation(reservation.key))
    loop = asyncio.get_running_loop()
    bridge_client = BridgeClient("http://fixture.invalid", "synthetic")
    sends: list[str] = []
    resumes: list[str] = []

    def wait(coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=15)

    def request(method: str, path: str, **kwargs: Any) -> Any:
        if path == "/health":
            return {
                "service": "agent-bridge", "status": "ok", "ready": True, "draining": False,
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "slot": {"role": "active", "pid": 42, "active": {"pid": 42, "generation": 1}},
                "capabilities": {
                    "repository_agent_hosting": 1, "worktree_record_delegation": 1,
                    "retained_workspace_creation_writer": 1,
                    "retained_workspace_creation_writer_inspection": 1,
                    "retained_worktree_resume": 1, "retained_native_metadata_adoption": 1,
                    "retained_native_session_recovery": 1,
                    "retained_keyed_native_admission": 1,
                },
            }
        if method == "POST":
            assert path == "/api/v1/worktrees/worktree-a/resume/acp"
            assert kwargs["allow_retry"] is False
            resumes.append(path)
            response = wait(worktree_native_resume.resume_native_worktree(
                row["id"], bound.request, "acp", strict=True,
            ))
            assert queue.get(task.id).status == "queued" and not sends
            row.update(session_live=True, native_liveness="active")
            return json.loads(bytes(response.body))
        session_id = path.split("/")[4].partition("?")[0]
        session = bound.manager.get_session(session_id)
        if session is None:
            raise BridgeClientError(404, "missing")
        value = _session_info(session, bound.manager).model_dump(mode="json")
        if "worktree_writer=true" in path:
            value["worktree_writer_observation"] = wait(inspect_writer(bound.manager, session, lambda: True))
        return value

    monkeypatch.setattr(bridge_client, "_request", request)
    monkeypatch.setattr(core, "_get_client", lambda **kwargs: bridge_client)

    class Client:
        def pool_placement_contract(self) -> Any:
            return support.pool_placement_contract()

        def get(self, task_id: str) -> Any:
            return asdict(queue.get(task_id))

        def get_reservation(self, key: str) -> Any:
            return {**asdict(queue.get_reservation(key)), **facts.reservation_native_facts(queue, key)}

        def record_spawn_allocation(self, key: str, project: str, **kwargs: Any) -> Any:
            return asdict(queue.record_spawn_allocation(key, project, **kwargs))

        def record_resumed_agent(self, key: str, **kwargs: Any) -> Any:
            return asdict(dispatch.record_resumed_agent(queue, key, **kwargs))

    def run(argv: list[str], **kwargs: Any) -> Any:
        if argv[2] != "send":
            output = io.StringIO()
            args = core.build_parser().parse_args(argv[1:])
            code = 0
            with contextlib.redirect_stdout(output):
                try:
                    args.func(args)
                except SystemExit as exc:
                    assert isinstance(exc.code, int)
                    code = exc.code
            return subprocess.CompletedProcess(argv, code, output.getvalue(), "")
        payload = native_keyed_admission.KeyedNativePrompt.model_validate(json.loads(kwargs["input"]))
        session_id = argv[3]
        if session_id == bound.old.session_id:
            assert "--receipt-only" in argv
            return subprocess.CompletedProcess(argv, 69, json.dumps({
                "outcome": "refused_unavailable", "reason": "native_session_unavailable",
            }), "")
        saved = queue.get_reservation(reservation.key).agent_info
        assert saved["current_control_invocation"]["invocation"]["invocation_key"] == payload.invocation_key
        assert saved["retained_control_history"]["predecessor"] == before["agent_info"]
        response = wait(native_keyed_admission.submit(
            bound.manager, session_id, payload, read_only="--receipt-only" in argv,
        ))
        if response is None:
            return subprocess.CompletedProcess(argv, 69, json.dumps({
                "outcome": "refused_unavailable", "reason": "native_receipt_missing",
            }), "")
        if "--receipt-only" not in argv:
            sends.append(payload.invocation_key)
        return subprocess.CompletedProcess(argv, 0, json.dumps({**response, "outcome": "delivered"}), "")

    monkeypatch.setattr(dispatch, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(dispatch, "subprocess", SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError,
    ))
    monkeypatch.setattr(assignment, "read_owner", lambda *args, **kwargs: dict(row))
    bridge = importlib.import_module("agent_dispatch.bridge")
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *args, **kwargs: row["repo"])
    result = await asyncio.to_thread(
        dispatch.start, snapshot, client=Client(), agent="local", project=row["repo"],
        prompt="Review the assigned source",
    )
    assert len(resumes) == len(sends) == 1
    assert queue.get(task.id).status == "queued"
    stored = queue.get_reservation(reservation.key)
    assert stored.agent_info["startup_invocation"] == before["agent_info"]["startup_invocation"]
    assert stored.agent_info["durable_session_id"] == bound.old.acp_session_id
    dispatch.record_session_info(queue, reservation.key, result["session_info"])
    assert queue.claim_one("worker", task_id=task.id) is not None
    assert queue.start(task.id, "worker", owner_session_id=bound.old.acp_session_id).status == "started"
