"""Actor-free ordinary submission retry and failure ownership controls."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_submission_consumer"


@pytest.fixture
def boundary(monkeypatch):
    root = ROOT / "plugins/agent-dispatch/src/agent_dispatch"

    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    def load(name):
        qualified = f"{PACKAGE}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, root / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        return module

    class Refused(RuntimeError):
        pass

    stub(PACKAGE, __path__=[str(root)])
    stub(f"{PACKAGE}.queue_records", TaskError=Refused, Status=SimpleNamespace(
        SUBMITTED="submitted", COMPLETED="completed",
    ))
    stub(f"{PACKAGE}.queue_common", CompletionOutcome=object, StructuredResult=object, Task=object)
    stub(
        f"{PACKAGE}.queue_submission_preparation",
        _encoded=lambda value: json.dumps(value, sort_keys=True),
        _latest=lambda conn, _: conn.row,
        submission_required_condition=lambda _: "required",
    )
    stub(f"{PACKAGE}.queue_task_relationships", latest_relationship=lambda *_: None)
    stub(f"{PACKAGE}.queue_submission_advancement", _replay=lambda *_: "original receipt")
    workspace_module = load("assignment_workspace")
    consumer = load("queue_submission_consumer")
    workspace = workspace_module.AssignmentWorkspace(
        "host", "example", "workspace", "/example/workspace", "original-nonce",
    )
    arguments = {
        "request_id": "original", "expected_generation": 7,
        "expected_owner_session_id": "native", "expected_status": None,
        "workspace": workspace,
    }
    return consumer, Refused, workspace, arguments


def test_submission_keeps_exact_owner_order_and_retry_request(boundary, monkeypatch):
    consumer, _, workspace, arguments = boundary
    calls = []
    monkeypatch.setattr(consumer, "_exact_replay", lambda *_: None)
    queue = SimpleNamespace(
        prepare_submission=lambda *args, **kwargs: calls.append(("prepare", args, kwargs)),
        advance_prepared_submission=lambda *args, **kwargs: calls.append(("advance", args, kwargs)),
    )
    consumer.complete(
        queue, "task", workspace.worker_id, arguments,
        result={"answer": True}, result_ref="original-ref", now=10.0,
    )
    assert calls == [
        ("prepare", ("task", workspace.worker_id), {
            **arguments, "result": {"answer": True}, "result_ref": "original-ref", "now": 10.0,
        }),
        ("advance", ("task",), {"request_id": "original"}),
    ]


@pytest.mark.parametrize("prepared", [False, True])
def test_only_valid_preparation_can_receive_a_readiness_blocker(boundary, monkeypatch, prepared):
    consumer, Refused, workspace, arguments = boundary
    calls = []
    monkeypatch.setattr(consumer, "_exact_replay", lambda *_: None)

    def fail(*args, **kwargs):
        raise Refused("original input refused")

    queue = SimpleNamespace(
        prepare_submission=(lambda *a, **k: None) if prepared else fail,
        advance_prepared_submission=fail,
        record_submission_blocker=lambda *a, **k: calls.append((a, k)),
    )
    with pytest.raises(Refused, match="authoritative readiness"):
        consumer.complete(
            queue, "task", workspace.worker_id, arguments,
            result={"answer": True}, result_ref=None, now=10.0,
        )
    assert calls == ([
        (("task",), {
            "request_id": "original", "code": "submission-readiness",
            "message": "original input refused",
        }),
    ] if prepared else [])


@pytest.mark.parametrize("change", [
    None, "worker", "generation", "native", "nonce", "result", "reference",
], ids=["exact", "worker", "generation", "native", "nonce", "result", "reference"])
def test_committed_replay_requires_original_payload_and_binding(boundary, change):
    consumer, _, workspace, arguments = boundary
    arguments = {**arguments, "result": {"answer": True}, "result_ref": "original-ref"}
    request = {
        "request_id": "original", "worker_id": workspace.worker_id,
        "generation": 7, "owner_session_id": "native", "workspace": asdict(workspace),
        "result": {"answer": True}, "result_ref": "original-ref",
    }
    worker = workspace.worker_id
    if change == "worker":
        worker = "other/workspace"
    elif change == "generation":
        arguments["expected_generation"] = 8
    elif change == "native":
        arguments["expected_owner_session_id"] = "replacement"
    elif change == "nonce":
        arguments["workspace"] = replace(workspace, creation_nonce="replacement")
    elif change == "result":
        arguments["result"] = {"answer": 1}
    elif change == "reference":
        arguments["result_ref"] = None
    conn = SimpleNamespace(
        execute=lambda *_: None, row={"submission_preparation": json.dumps({"request": request})},
    )
    queue = SimpleNamespace(
        _connect=lambda: nullcontext(conn),
        _fetch=lambda *_: SimpleNamespace(status="submitted"),
    )
    assert consumer._exact_replay(queue, "task", worker, arguments) == (
        "original receipt" if change is None else None
    )
