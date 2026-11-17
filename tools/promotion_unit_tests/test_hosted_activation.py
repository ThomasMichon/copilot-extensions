"""Hosted source semantics with substituted canonical and physical owners."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def hosted_source(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    package = "promotion_hosted_source"
    root = ROOT / "plugins/agent-worktrees/src/agent_worktrees"
    epoch = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}
    processes = {
        101: {"pid": 101, "start_time": "100", "parent_pid": 1},
        102: {"pid": 102, "start_time": "200", "parent_pid": 101},
    }

    def process(pid: int, *, require_live: bool = False) -> dict[str, Any]:
        assert require_live
        return processes[pid].copy()

    for name, attributes in (
        (package, {"__path__": [str(root)]}),
        (package + ".native_source_context", {"epoch": lambda: epoch.copy(), "process": process}),
        (package + ".retained_record_writer", {"active": lambda: True}),
    ):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    name = package + ".retained_hosted_activation"
    spec = importlib.util.spec_from_file_location(name, root / "retained_hosted_activation.py")
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, owner)
    spec.loader.exec_module(owner)
    interval = SimpleNamespace(
        ordinal=1, source_identity="native:previous", ended_at="ended", source_ambiguous=False,
    )
    entry = SimpleNamespace(
        state="active", activations=[interval], pruned_sources=[], source_history_saturated=False,
    )
    record = SimpleNamespace(
        session_entry=lambda sid: entry if sid == SID else None,
        status="active", resolved_head_session=SID, head_revision=7,
        pending_handoffs=[], creation_nonce="original-incarnation",
    )
    value = {
        "version": 1, "operation_id": "original-operation", "session_id": SID,
        "head_revision": 7, "epoch": epoch.copy(),
        "host": {"pid": 101, "start_time": "100"}, "child": {"pid": 102, "start_time": "200"},
    }
    return SimpleNamespace(
        owner=owner, record=record, value=value, entry=entry, interval=interval,
        processes=processes, epoch=epoch, package=package,
    )


def test_hosted_source_is_bound_to_physical_activation_not_operation_id(hosted_source: Any) -> None:
    b = hosted_source
    token = b.owner.source_identity(b.record, SID, 102, b.value)
    b.value["operation_id"] = "another-operation"
    assert b.owner.source_identity(b.record, SID, 102, b.value) == token
    b.interval.source_identity = token
    b.interval.ended_at = None
    assert b.owner.source_identity(b.record, SID, 102, b.value) == token
    b.interval.ended_at = "ended"
    with pytest.raises(ValueError, match="already closed"):
        b.owner.source_identity(b.record, SID, 102, b.value)


@pytest.mark.parametrize("change", ["head", "nonce-source", "epoch", "birth", "parent", "pruned", "open"])
def test_hosted_source_fences_record_and_physical_boundaries(hosted_source: Any, change: str) -> None:
    b = hosted_source
    if change == "head":
        b.record.head_revision += 1
    elif change == "nonce-source":
        token = b.owner.source_identity(b.record, SID, 102, b.value)
        b.record.creation_nonce = "new-incarnation"
        assert b.owner.source_identity(b.record, SID, 102, b.value) != token
        return
    elif change == "epoch":
        b.epoch["boot_id"] = "changed"
    elif change == "birth":
        b.processes[102]["start_time"] = "201"
    elif change == "parent":
        b.processes[102]["parent_pid"] = 999
    elif change == "pruned":
        b.entry.pruned_sources = ["native:pruned"]
    elif change == "open":
        b.interval.ended_at = None
    with pytest.raises(ValueError):
        b.owner.source_identity(b.record, SID, 102, b.value)


@pytest.mark.parametrize("change", ["version", "pid", "head", "unknown", "session", "budget"])
def test_hosted_source_rejects_malformed_evidence(hosted_source: Any, change: str) -> None:
    b = hosted_source
    if change == "version":
        b.value["version"] = True
    elif change == "pid":
        b.value["host"]["pid"] = True
    elif change == "head":
        b.value["head_revision"] = True
    elif change == "unknown":
        b.value["accepted"] = True
    elif change == "session":
        b.value["session_id"] = "not-a-native-uuid"
    elif change == "budget":
        b.value["operation_id"] = "x" * (b.owner.MAX_INPUT_BYTES + 1)
    with pytest.raises(ValueError):
        b.owner.parse(json.dumps(b.value))


@pytest.mark.parametrize("boundary", ["process", "epoch"])
def test_hosted_source_rechecks_processes_and_epoch(
    hosted_source: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    b = hosted_source
    context = sys.modules[b.package + ".native_source_context"]
    reads = 0

    def changed_epoch() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        return {**b.epoch, **({"boot_id": "changed"} if boundary == "epoch" and reads > 1 else {})}

    monkeypatch.setattr(context, "epoch", changed_epoch)
    if boundary == "process":
        original_process = context.process
        process_reads = 0

        def changed_process(pid: int, **kwargs: Any) -> Any:
            nonlocal process_reads
            process_reads += 1
            observed = original_process(pid, **kwargs)
            return {**observed, **({"start_time": "changed"} if process_reads > 2 else {})}

        monkeypatch.setattr(context, "process", changed_process)
    with pytest.raises(ValueError, match="epoch changed"):
        b.owner.source_identity(b.record, SID, 102, b.value)
