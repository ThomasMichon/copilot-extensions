"""Actor-free codec controls; authenticated/native qualification is separate."""

from __future__ import annotations

import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package = ModuleType("promotion_workspace_bulk")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, package.__name__, package)

    def stub(name: str, **fields: Any) -> ModuleType:
        module = ModuleType(name)
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    fleet = stub("fleet_manager")
    fleet.__path__ = []
    stub("fleet_manager.gateway_service_channel", BODY_BYTES=32768, RESPONSE_BYTES=8 * 1024 * 1024)
    class Response:
        def __init__(self, status: int, headers: tuple[tuple[str, str], ...], body: bytes) -> None:
            self.status, self.headers, self.body = status, headers, body
    stub("fleet_manager.gateway_service_handler",
         ServiceResponse=Response, BoundedServiceResponse=Response)
    stub(package.__name__ + ".workspace_operations", CHUNK_BYTES=8192)
    stub(package.__name__ + ".owner_read", OwnerReadResponseTooLarge=ValueError)
    def serve(workspace: Any, operation: str, payload: dict[str, Any], **options: Any) -> dict[str, Any]:
        options["checkpoint"]()
        value: dict[str, Any] = workspace.read(operation, payload)
        return value
    stub(package.__name__ + ".workspace_terminal_read", serve=serve)
    path = (
        Path(__file__).resolve().parents[2] / "plugins" / "agent-pull-requests"
        / "src" / "agent_pull_requests" / "workspace_bulk.py"
    )
    spec = importlib.util.spec_from_file_location(package.__name__ + ".workspace_bulk", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def fixture_frames() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    descriptor = {
        "id": "a" * 64, "sha256": "b" * 64, "bytes": 8192 + 5,
        "scope": {"principal_id": "selected-reviewer"},
    }
    return descriptor, [
        {"descriptor": copy.deepcopy(descriptor), "offset": offset,
         "data": base64.b64encode(block).decode("ascii")}
        for offset, block in ((0, b"a" * 8192), (8192, b"final"))
    ]


@pytest.mark.parametrize("change", ["offset", "scope", "encoding", "short", "empty", "gap", "count", "protocol"])
def test_group_rejects_corruption_without_retaining_partial_evidence(api: ModuleType, change: str) -> None:
    descriptor, frames = fixture_frames()
    result = {api.FIELD: api.PROTOCOL, "chunks": frames}
    if change == "offset":
        frames[1]["offset"] += 1
    elif change == "scope":
        frames[1]["descriptor"]["scope"]["principal_id"] = "foreign-reviewer"
    elif change == "encoding":
        frames[1]["data"] = "!"
    elif change == "short":
        frames[1]["data"] = ""
    elif change == "empty":
        frames.clear()
    elif change == "gap":
        frames.pop(0)
    elif change == "count":
        frames *= 65
    else:
        result[api.FIELD] = "unknown"
    with pytest.raises(ValueError):
        api.checked_frames(result, descriptor, 0)


def test_short_final_frame_and_existing_single_frame_reply_share_validation(api: ModuleType) -> None:
    descriptor, frames = fixture_frames()
    assert api.checked_frames({api.FIELD: api.PROTOCOL, "chunks": frames}, descriptor, 0) == frames
    assert api.checked_frames(frames[1], descriptor, 8192) == frames[1:]
    with pytest.raises(ValueError):
        api.checked_frames(frames[1], descriptor, 8197)


def test_actual_escaped_envelope_bound_splits_only_complete_frames(api: ModuleType) -> None:
    scope = {"principal_id": "\u2603" * 4096}
    descriptor = {"scope": scope, "bytes": 3 * 8192}
    calls: list[int] = []
    def read(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert operation == "chunk"
        calls.append(payload["offset"])
        return {
            "protocol": "workspace/v1", "scope": scope,
            "result": {"descriptor": descriptor, "offset": payload["offset"],
                       "data": base64.b64encode(b"a" * 8192).decode("ascii")},
        }
    raw: bytes = api.grouped_response(
        SimpleNamespace(read=read), {"offset": 0}, binding={"principal": "caller"},
        selection=None, expires_at=1, checkpoint=lambda: None, response_limit=65536,
    )
    value = json.loads(raw)
    assert len(raw) <= 65536 and len(value["result"]["chunks"]) == 1
    assert calls == [0, 8192] and value["scope"] == scope
    assert base64.b64decode(value["result"]["chunks"][0]["data"]) == b"a" * 8192
    with pytest.raises(ValueError, match="exceeds response limit"):
        api.grouped_response(
            SimpleNamespace(read=read), {"offset": 0}, binding={"principal": "caller"},
            selection=None, expires_at=1, checkpoint=lambda: None, response_limit=32768,
        )


def test_member_scope_drift_within_one_group_cannot_produce_a_response(api: ModuleType) -> None:
    def read(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        scope = {"principal_id": "first" if payload["offset"] == 0 else "foreign"}
        return {
            "protocol": "workspace/v1", "scope": scope,
            "result": {"descriptor": {"scope": scope, "bytes": 16384},
                       "offset": payload["offset"], "data": "unchanged"},
        }
    with pytest.raises(ValueError, match="scope changed"):
        api.grouped_response(
            SimpleNamespace(read=read), {"offset": 0}, binding={"principal": "caller"},
            selection=None, expires_at=1, checkpoint=lambda: None, response_limit=65536,
        )


def test_optional_discovery_requires_a_bounded_receiver_and_typed_contract(
    api: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = api.contract(1024 * 1024)
    assert api.available(selected["response_bytes"]) and api.supports(selected)
    selected["max_chunks"] = 64.0
    assert not api.supports(selected)
    assert not api.available(32768)
    receiver = sys.modules["fleet_manager.gateway_service_handler"]
    monkeypatch.delattr(receiver, "BoundedServiceResponse")
    assert not api.available(1024 * 1024)
