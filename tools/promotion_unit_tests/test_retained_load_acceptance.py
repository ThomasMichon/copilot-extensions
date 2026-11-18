"""Actor-free admission and acknowledgement-journal controls."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_hosted_load"


class Refused(RuntimeError):
    def __init__(self, _code: str, message: str) -> None:
        super().__init__(message)


@pytest.fixture
def original(monkeypatch: pytest.MonkeyPatch) -> Any:
    root = ROOT / "plugins/agent-bridge/src/agent_bridge"
    package = ModuleType(PACKAGE)
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, PACKAGE, package)
    contract = ModuleType(f"{PACKAGE}.acquisition_contract")
    setattr(contract, "AcquisitionRefused", Refused)
    monkeypatch.setitem(sys.modules, contract.__name__, contract)
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.retained_load_acceptance", root / "retained_load_acceptance.py",
    )
    assert spec and spec.loader
    source = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, source.__name__, source)
    spec.loader.exec_module(source)
    epoch = {"platform": "linux", "environment_id": "original-environment", "boot_id": "original-boot"}
    monkeypatch.setattr(source, "epoch", lambda: epoch.copy())
    physical = {
        41: {"pid": 41, "start_time": "100", "parent_pid": 1},
        42: {"pid": 42, "start_time": "101", "parent_pid": 41},
    }
    monkeypatch.setattr(source, "process", lambda pid: physical[pid].copy())
    events: list[tuple[str, dict[str, Any]]] = []
    writer = {"version": 1, "identity": {"runtime_generation": "executing"}, "creation_nonce": "original"}
    writers = ModuleType(f"{PACKAGE}.acquisition_worktree_writer")
    setattr(writers, "current", lambda: SimpleNamespace(
        execution_requirement=SimpleNamespace(model_dump=lambda: writer.copy()),
    ))
    monkeypatch.setitem(sys.modules, writers.__name__, writers)
    owner = SimpleNamespace(
        session_id="original-control", hosted_epoch=source.negotiate({"retained_hosted_activation": 1}),
        host_record=SimpleNamespace(
            session_id="original-control", host_pid=41, child_pid=42, boundary="local", endpoint={},
            nonce="private-host-nonce", protocol_version=1, port=1234,
            state_file="/example/original-host", created_at=1.0,
            extra={"host_process_start": "100", "child_process_start": "101"},
        ),
        host_process=SimpleNamespace(pid=41, poll=lambda: None),
        request=SimpleNamespace(
            operation_id="original-operation", native_session_id="original-native", head_revision=7,
            model_dump=lambda: {"operation_id": "original-operation"},
        ),
        socket=object(), streams=object(), accepted_load=None,
        require_current=Mock(),
        manager=SimpleNamespace(db=SimpleNamespace(flush=Mock())),
        session=SimpleNamespace(event_log=SimpleNamespace(
            append=lambda name, value: events.append((name, value)),
        )),
    )
    client = SimpleNamespace(session_host_client=owner.socket)
    owner.session.client = client
    return SimpleNamespace(
        source=source, owner=owner, client=client, physical=physical,
        events=events, epoch=epoch, writer=writer,
    )


def test_original_transport_ack_is_durable_without_disclosing_nonce(original: Any) -> None:
    b = original
    document = b.source.prepare(b.owner, b.client)
    b.source.record(b.owner, document)
    b.owner.manager.db.flush.assert_called_once()
    assert b.events == [(b.source.EVENT, document)]
    assert document["activation"]["session_id"] == b.owner.request.native_session_id
    assert document["activation"]["child"] == {"pid": 42, "start_time": "101"}
    assert document["writer"] == b.writer
    assert "nonce" not in document["host"] and len(document["host"]["nonce_digest"]) == 64
    with pytest.raises(Refused, match="prior evidence"):
        b.source.record(b.owner, document)
    assert len(b.events) == 1


@pytest.mark.parametrize("changed", ["birth", "parent", "epoch", "transport"])
def test_changed_load_association_has_no_journal_effect(original: Any, changed: str) -> None:
    b = original
    if changed == "birth":
        b.physical[42]["start_time"] = "102"
    elif changed == "parent":
        b.physical[42]["parent_pid"] = 99
    elif changed == "epoch":
        b.owner.hosted_epoch = {**b.epoch, "boot_id": "different"}
    else:
        b.client.session_host_client = object()
    with pytest.raises(Refused):
        b.source.prepare(b.owner, b.client)
    assert b.events == [] and b.owner.accepted_load is None


@pytest.mark.parametrize("support", [None, False, True, 0, "1", 2])
def test_executor_capability_cannot_be_guessed(original: Any, support: object) -> None:
    with pytest.raises(Refused, match="lacks retained hosted"):
        original.source.negotiate({"retained_hosted_activation": support})


def test_failed_commit_retains_unknown_acceptance(original: Any) -> None:
    b = original
    document = b.source.prepare(b.owner, b.client)
    b.owner.manager.db.flush.side_effect = OSError("commit failed")
    with pytest.raises(OSError, match="commit failed"):
        b.source.record(b.owner, document)
    assert b.owner.accepted_load is None


def test_registration_uses_only_original_acknowledged_activation(original: Any) -> None:
    b = original
    document = b.source.prepare(b.owner, b.client)
    b.source.record(b.owner, document)
    registered = b.source.registration(b.owner, b.client)
    assert registered == document["activation"]
    registered["child"]["pid"] = 99
    assert document["activation"]["child"]["pid"] == 42
    b.owner.require_current.assert_called_once()


@pytest.mark.parametrize("changed", ["missing", "transport", "nonce", "birth", "writer", "stop"])
def test_registration_retains_unknown_or_changed_custody(original: Any, changed: str) -> None:
    b = original
    b.source.record(b.owner, b.source.prepare(b.owner, b.client))
    if changed == "missing":
        b.owner.accepted_load = None
    elif changed == "transport":
        b.client.session_host_client = object()
    elif changed == "nonce":
        b.owner.host_record.nonce = "different"
    elif changed == "birth":
        b.physical[42]["start_time"] = "102"
    elif changed == "writer":
        b.writer["creation_nonce"] = "different"
    else:
        b.owner.require_current.side_effect = Refused("stopped", "stop hold")
    with pytest.raises(Refused):
        b.source.registration(b.owner, b.client)


@pytest.fixture
def postload(original: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    b = original
    b.writer["identity"]["installation_root"] = "/example/worktrees"

    class Requirement:
        @classmethod
        def model_validate(cls, value: Any) -> Any:
            return SimpleNamespace(
                identity=SimpleNamespace(**value["identity"]), creation_nonce=value["creation_nonce"],
            )

    setattr(sys.modules[f"{PACKAGE}.acquisition_contract"], "WorktreeWriterRequirement", Requirement)
    models = ModuleType(f"{PACKAGE}.models")
    setattr(models, "RetainedRecoveryRequest", SimpleNamespace)
    recovery = ModuleType(f"{PACKAGE}.retained_session_recovery")
    setattr(recovery, "_refuse", lambda message: Refused("held", message))
    settlement = ModuleType(f"{PACKAGE}.retained_recovery_settlement")
    setattr(settlement, "START_EVENT", "retained_recovery_started")
    for module in (models, recovery, settlement):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.retained_postload_evidence",
        ROOT / "plugins/agent-bridge/src/agent_bridge/retained_postload_evidence.py",
    )
    assert spec and spec.loader
    source = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, source.__name__, source)
    spec.loader.exec_module(source)
    host = b.owner.host_record
    host.state_file = str(tmp_path / "host-state.json")
    state = {
        "version": 2, **{key: getattr(host, key) for key in (
            "session_id", "port", "host_pid", "child_pid", "protocol_version", "nonce",
        )}, "cwd": "/example/workspace", "created_at": .5, "boot_id": "original-boot",
        "host_start_ticks": 100, "child_start_ticks": 101,
    }
    Path(host.state_file).write_text(json.dumps(state), encoding="utf-8")
    events = [
        ("retained_recovery_started", {"version": 1, "request": b.owner.request.model_dump()}),
        ("startup_progress", {"phase": "resume", "state": "started"}),
        ("startup_progress", {"phase": "session-host-start", "state": "started"}),
        ("startup_progress", {"phase": "session-host-start", "state": "finished"}),
        ("startup_progress", {"phase": "native-session-load", "state": "started"}),
        (b.source.EVENT, b.source.prepare(b.owner, b.client)),
        ("startup_progress", {"phase": "native-session-load", "state": "finished"}),
        ("startup_progress", {"phase": "resume", "state": "failed"}),
    ]
    b.history = [
        {"event_id": index, "event_type": name, "data": data, "timestamp": timestamp}
        for index, ((name, data), timestamp) in enumerate(zip(
            events, [.01, .02, .1, 2., 3., 4., 5., 6.], strict=True,
        ), 1)
    ]
    b.evidence, b.state = source, state
    b.requirement = Requirement.model_validate(b.writer)
    b.qualify = lambda: source.qualify(
        b.history, host, b.owner.request, b.requirement, "/example/workspace",
    )
    return b


@pytest.mark.parametrize("historical", [False, True])
def test_original_ack_survives_compatible_writer_without_health_inference(postload: Any, historical: bool) -> None:
    b = postload
    expected = b.source.prepare(b.owner, b.client)["activation"]
    if historical:
        b.history.pop(5)
        for index, item in enumerate(b.history, 1):
            item["event_id"] = index
    b.writer["identity"] = {**b.writer["identity"], "runtime_generation": "new-executor"}
    if not historical:
        assert b.history[5]["data"]["writer"]["identity"]["runtime_generation"] == "executing"
    assert b.qualify() == expected


@pytest.mark.parametrize("changed", [
    "gap", "marker", "missing-ack", "nonce", "birth", "epoch", "writer", "subsequent-prompt",
    "historical-state", "historical-time", "historical-cwd",
])
def test_missing_or_changed_original_load_keeps_evidence_unqualified(postload: Any, changed: str) -> None:
    b = postload
    if changed == "gap":
        b.history[2]["event_id"] = 99
    elif changed == "marker":
        b.history[0]["data"]["request"] = {"operation_id": "other"}
    elif changed == "missing-ack":
        b.history[5]["event_type"], b.history[5]["data"] = "connect_checkpoint", {}
        b.history[6]["data"]["state"] = "failed"
    elif changed == "nonce":
        b.owner.host_record.nonce = "other"
    elif changed == "birth":
        b.physical[42]["start_time"] = "other"
    elif changed == "epoch":
        b.epoch["boot_id"] = "other"
    elif changed == "writer":
        b.history[5]["data"]["writer"]["creation_nonce"] = "other"
    elif changed == "subsequent-prompt":
        b.history.append({"event_id": 9, "event_type": "user_message", "data": {}})
    else:
        b.history.pop(5)
        for index, item in enumerate(b.history, 1):
            item["event_id"] = index
        if changed == "historical-state":
            b.state["host_pid"] = 99
        elif changed == "historical-time":
            b.state["created_at"] = 2.
        else:
            b.state["cwd"] = "/other"
        Path(b.owner.host_record.state_file).write_text(json.dumps(b.state), encoding="utf-8")
    with pytest.raises(Refused):
        b.qualify()


@pytest.mark.parametrize("terminal", ["finished", "failed", "absent"])
def test_durable_ack_does_not_depend_on_later_progress(postload: Any, terminal: str) -> None:
    b = postload
    if terminal == "absent":
        b.history = b.history[:6]
    else:
        b.history[6]["data"]["state"] = terminal
    assert b.qualify()["session_id"] == b.owner.request.native_session_id


@pytest.mark.parametrize("historical", [False, True])
def test_original_configured_load_events_are_not_turn_activity(postload: Any, historical: bool) -> None:
    b = postload
    additions = [
        {"event_type": name, "data": {"model": "original-model"}}
        for name in ("usage_update", "model_applied", "preference_application", "preference_selected")
    ]
    b.history[6:6] = additions
    if historical:
        b.history.pop(5)
    for index, item in enumerate(b.history, 1):
        item["event_id"] = index
    assert b.qualify()["session_id"] == b.owner.request.native_session_id


@pytest.mark.parametrize("changed", ["early-ack", "double-load", "turn-usage", "legacy-failed", "legacy-crash"])
def test_ack_order_and_legacy_terminal_proof_remain_required(postload: Any, changed: str) -> None:
    b = postload
    if changed == "early-ack":
        b.history[4], b.history[5] = b.history[5], b.history[4]
    elif changed == "double-load":
        b.history[6]["data"]["state"] = "started"
    elif changed == "turn-usage":
        b.history.append({"event_type": "usage_update", "data": {"model": "original-model", "input_tokens": 1}})
    else:
        b.history.pop(5)
        if changed == "legacy-failed":
            b.history[5]["data"]["state"] = "failed"
        else:
            b.history = b.history[:5]
    for index, item in enumerate(b.history, 1):
        item["event_id"] = index
    with pytest.raises(Refused):
        b.qualify()
