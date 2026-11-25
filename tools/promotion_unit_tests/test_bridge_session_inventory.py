"""Actor-free inventory decisions; native identity and owner processes are boundaries."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = "plugins/agent-bridge/src/agent_bridge/"


def load(monkeypatch: pytest.MonkeyPatch, name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def package(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []
    monkeypatch.setitem(sys.modules, name, module)
    return module


def remote_observer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package(monkeypatch, "_remote_bridge")
    load(monkeypatch, "_remote_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    monkeypatch.setitem(sys.modules, "machine_transport", SimpleNamespace(AmbiguousMachineError=ValueError))
    monkeypatch.setitem(sys.modules, "ssh_manager", SimpleNamespace(CarrierRemoteError=RuntimeError))
    load(monkeypatch, "_remote_bridge.remote_errors", BRIDGE + "remote_errors.py")
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(
        BaseModel=type("BaseModel", (), {}),
        ConfigDict=dict, Field=lambda **_kwargs: None, ValidationError=ValueError,
    ))
    monkeypatch.setitem(sys.modules, "_remote_bridge.models", SimpleNamespace(
        DelegatedResultSnapshot=None,
    ))
    monkeypatch.setitem(sys.modules, "_remote_bridge.result_snapshot", SimpleNamespace(
        DEFAULT_MAX_ITEMS=20, DEFAULT_MAX_TEXT_CHARS=6000, MAX_MAX_ITEMS=100, MAX_MAX_TEXT_CHARS=20000,
    ))
    return load(monkeypatch, "_remote_bridge.remote_observation", BRIDGE + "remote_observation.py")


@pytest.mark.parametrize("scope", ["deadline", "socket", "legacy"])
def test_early_raw_timeout_keeps_the_selected_observation_budget(
    monkeypatch: pytest.MonkeyPatch, scope: str,
) -> None:
    helper = load(monkeypatch, "_early_budget", BRIDGE + "client_read_budget.py")
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def read(_buffer: Any) -> int:
        clock[0] = 0.14
        raise TimeoutError("fixture early socket timeout")

    raw = SimpleNamespace(readinto=read, close=lambda: None)
    sock = SimpleNamespace(
        gettimeout=lambda: 20.0 if scope == "deadline" else 0.05,
        settimeout=lambda _value: None, makefile=lambda *_args, **_kwargs: raw,
    )
    reader = helper._ResponseReader(sock, "rb")
    try:
        expected = helper.ReadDeadlineExpired if scope == "deadline" else TimeoutError
        with pytest.raises(expected), helper.command_deadline(None if scope == "legacy" else 0.15):
            reader.readinto(bytearray(1))
    finally:
        reader.close()
    assert clock[0] < 0.15


@pytest.mark.parametrize("content", ["ascii", "unicode", "escaping", "boundary"])
def test_canonical_request_preflight_counts_complete_encoded_frame(
    monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    package(monkeypatch, "_frame_owner")
    protocol = load(
        monkeypatch, "_frame_owner.carrier_protocol",
        "libs/ssh-manager/src/ssh_manager/carrier_protocol.py",
    )
    monkeypatch.setitem(sys.modules, "_frame_owner.carrier_subscriptions", SimpleNamespace(
        CarrierSubscription=object, _BoundedFrameQueue=object,
    ))
    helper = load(
        monkeypatch, "_frame_owner.carrier_requests",
        "libs/ssh-manager/src/ssh_manager/carrier_requests.py",
    )
    state = SimpleNamespace(_outbound_max_frame_size=protocol.DEFAULT_MAX_FRAME_SIZE)
    encode = helper._CarrierRequestMixin.encode_request
    correlation = "original-correlation"
    if content == "boundary":
        payload = {"prompt": "bounded"}
        frame = encode(state, payload, request_id=correlation)
        assert protocol.decode_envelope(frame[4:]).request_id == correlation
        state._outbound_max_frame_size = len(frame) - 4
        assert encode(state, payload, request_id=correlation) == frame
        state._outbound_max_frame_size -= 1
        with pytest.raises(protocol.CarrierProtocolError):
            encode(state, payload, request_id=correlation)
        return
    value = {
        "ascii": "x" * protocol.DEFAULT_MAX_FRAME_SIZE,
        "unicode": "\U0001f600" * 100_000,
        "escaping": '"' * 600_000,
    }[content]
    with pytest.raises(protocol.CarrierProtocolError, match="exceeds limit"):
        encode(state, {"prompt": value}, request_id=correlation)


@pytest.mark.parametrize("fault", ["none", "owner", "native", "key", "queued", "turn"])
def test_remote_native_receipt_preserves_binding_or_reports_unknown(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    remote_observer(monkeypatch)
    monkeypatch.setitem(sys.modules, "ssh_manager", SimpleNamespace(
        CarrierLease=object, CarrierProtocolError=RuntimeError,
        CarrierRemoteError=RuntimeError, CarrierUnavailable=ConnectionError,
        EnvelopeType=SimpleNamespace(RESPONSE="response"),
    ))
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(
        BaseModel=type("BaseModel", (), {}), ConfigDict=dict,
        Field=lambda **_kwargs: None, ValidationError=ValueError,
        field_validator=lambda *_a, **_kw: lambda function: function,
    ))
    helper = load(monkeypatch, "_remote_bridge.remote_native_prompt", BRIDGE + "remote_native_prompt.py")
    selected = SimpleNamespace(
        session_id="managed-a", native_session_id="native-a", invocation_key="original-key",
    )
    value = {
        "version": 1, "session_id": "managed-a", "native_session_id": "native-a",
        "invocation_key": "original-key", "queued": False, "admission": "accepted",
        "turn_index": 0,
    }
    if fault == "none":
        assert helper.validate_receipt(value, selected) == value
        return
    field, replacement = {
        "owner": ("session_id", "other-owner"), "native": ("native_session_id", "other-native"),
        "key": ("invocation_key", "fresh-key"), "queued": ("queued", True),
        "turn": ("turn_index", True),
    }[fault]
    with pytest.raises(RuntimeError) as error:
        helper.validate_receipt({**value, field: replacement}, selected)
    assert getattr(error.value, "code") == "native_prompt_admission_unknown"
    assert getattr(error.value, "details")["invocation_key"] == "original-key"


def test_native_exact_owner_mode_requires_key_before_client_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package(monkeypatch, "_native_owner")
    monkeypatch.setitem(sys.modules, "_native_owner.client", SimpleNamespace(
        BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
    ))
    monkeypatch.setitem(sys.modules, "_native_owner.protocol", SimpleNamespace(
        NATIVE_BOUND_PROMPT_PROTOCOL_VERSION=51,
    ))
    outcome = load(monkeypatch, "_native_owner.send_outcome", BRIDGE + "send_outcome.py")
    helper = load(monkeypatch, "_native_owner.native_bound_prompt", BRIDGE + "native_bound_prompt.py")
    with pytest.raises(outcome.SendRefused) as error:
        helper.submit_native_bound_prompt(
            object(), "managed-a", "Continue", expected_native_session_id="native-a",
            caller_id=None, require_exact_owner=True,
        )
    assert error.value.reason == "exact_owner_requires_key"


def test_native_prompt_wire_uses_mutation_version_without_upgrading_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package(monkeypatch, "_prompt_version")
    monkeypatch.setitem(sys.modules, "_prompt_version.live_peer_address", SimpleNamespace(
        peer_reply_requested=lambda _payload: False,
    ))
    monkeypatch.setitem(sys.modules, "_prompt_version.remote_errors", SimpleNamespace(
        RemoteBridgeError=RuntimeError,
    ))
    helper = load(
        monkeypatch, "_prompt_version.remote_operation_versions", BRIDGE + "remote_operation_versions.py",
    )
    assert [
        helper.request_operation_version({"operation": operation})
        for operation in ("session.native_prompt", "live_session.exact_prompt", "session.stop", "session.status")
    ] == [2, 2, 2, 1]


@pytest.mark.parametrize("fault", ["none", "session", "key", "sender", "message", "admission", "version"])
def test_remote_exact_inbox_receipt_preserves_binding_or_reports_unknown(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    remote_observer(monkeypatch)
    monkeypatch.setitem(sys.modules, "ssh_manager", SimpleNamespace(
        CarrierLease=object, CarrierProtocolError=RuntimeError,
        CarrierRemoteError=RuntimeError, CarrierUnavailable=ConnectionError,
        EnvelopeType=SimpleNamespace(RESPONSE="response"),
    ))
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(
        BaseModel=type("BaseModel", (), {}), ConfigDict=dict,
        Field=lambda **_kwargs: None, ValidationError=ValueError,
        field_validator=lambda *_a, **_kw: lambda function: function,
    ))
    helper = load(monkeypatch, "_remote_bridge.remote_live_prompt", BRIDGE + "remote_live_prompt.py")
    selected = SimpleNamespace(session_id="native-a", invocation_key="original-key", sender="controller")
    value = {
        "version": 1, "session_id": "native-a", "invocation_key": "original-key",
        "sender": "controller", "admission": "accepted", "message_id": 1,
    }
    if fault == "none":
        assert helper.validate_receipt(value, selected) == value
        return
    field, replacement = {
        "session": ("session_id", "successor"), "key": ("invocation_key", "fresh-key"),
        "sender": ("sender", "other"), "message": ("message_id", True),
        "admission": ("admission", "consumed"), "version": ("version", True),
    }[fault]
    with pytest.raises(RuntimeError) as error:
        helper.validate_receipt({**value, field: replacement}, selected)
    assert getattr(error.value, "code") == "exact_live_prompt_admission_unknown"
    assert getattr(error.value, "details")["invocation_key"] == "original-key"


@pytest.mark.parametrize("represented", [False, True])
def test_remote_cli_observation_pins_host_owner_and_rechecks_registration(
    monkeypatch: pytest.MonkeyPatch, represented: bool,
) -> None:
    root = package(monkeypatch, "_observe_cli")
    load(monkeypatch, "_observe_cli.client_read_budget", BRIDGE + "client_read_budget.py")
    monkeypatch.setitem(sys.modules, "_observe_cli.client", SimpleNamespace(
        BridgeClientError=ValueError, BridgeConnectionError=ConnectionError,
    ))
    monkeypatch.setitem(sys.modules, "_observe_cli.bridge_listing_cli", SimpleNamespace(
        _scope=lambda _args: {"project": "demo"},
    ))
    helper = load(monkeypatch, "_observe_cli.bridge_observation_cli", BRIDGE + "bridge_observation_cli.py")
    calls: list[tuple[str, str, Any]] = []
    session_id = "native-a" if represented else "managed-a"

    def resolve(handle: str, **scope: Any) -> dict[str, Any]:
        calls.append(("resolve", handle, scope))
        return {"bridge": {
            "session_id": "native-a", "route": {"host": "host-b"},
            "managed_session_id": None if represented else session_id,
            "registered_session_id": session_id if represented else None,
        }}

    def snapshot(host: str, **options: Any) -> dict[str, Any]:
        calls.append(("snapshot", host, options))
        return {
            "identity": {
                "snapshot_session_id": session_id, "native_session_id": "native-a",
                "current_session_id": session_id,
            },
            "state": {"session_status": "idle", "at_rest": True},
        }

    def registration(host: str, handle: str) -> dict[str, str]:
        calls.append(("registration", host, handle))
        return {"session_id": handle, "status": "expired"}

    client = SimpleNamespace(
        resolve_bridge=resolve, get_remote_result_snapshot=snapshot,
        resolve_remote_live_session=registration,
    )
    results: list[dict[str, Any]] = []

    def get_client(**kwargs: Any) -> Any:
        assert kwargs == {"ensure": False}
        return client

    core = SimpleNamespace(
        _get_client=get_client, _phased_timeouts=lambda: SimpleNamespace(command=1),
        _json_out=results.append,
    )
    monkeypatch.setitem(sys.modules, "_observe_cli.__main__", core)
    monkeypatch.setattr(root, "__main__", core, raising=False)
    helper.wait(SimpleNamespace(session_id="workspace-a", host="host-b", json=True))
    assert calls == [
        ("resolve", "workspace-a", {"hosts": ["host-b"], "project": "demo"}),
        ("snapshot", "host-b", {
            "session_id": session_id, "native_session_id": "native-a",
            "represented": represented, "max_items": 1, "max_text_chars": 256,
        }),
        *([("registration", "host-b", session_id)] if represented else []),
    ]
    assert results[0]["reason"] == ("expired" if represented else "idle")
    assert results[0]["settled"] is True


@pytest.mark.parametrize("capable", [False, True])
def test_remote_http_adapter_preserves_native_binding_and_capability_refusal(
    monkeypatch: pytest.MonkeyPatch, capable: bool,
) -> None:
    observer = remote_observer(monkeypatch)
    load(monkeypatch, "_remote_bridge.client_protocol_floor", BRIDGE + "client_protocol_floor.py")
    monkeypatch.setitem(sys.modules, "_remote_bridge.protocol", SimpleNamespace(
        REMOTE_OPERATIONS_PROTOCOL_VERSION=11,
        HTTP_PROTOCOL_MIN_SUPPORTED=1, HTTP_PROTOCOL_VERSION=52,
    ))

    class ClientError(Exception):
        def __init__(self, status: int, detail: Any) -> None:
            self.status = status
            super().__init__(detail)

    monkeypatch.setitem(sys.modules, "_remote_bridge.client", SimpleNamespace(
        BridgeClientError=ClientError, BridgeConnectionError=ConnectionError,
    ))
    selected: dict[str, Any] = {}

    def parse(values: dict[str, Any]) -> Any:
        selected.update(values)
        return SimpleNamespace(model_dump=lambda **_kwargs: {
            key: value for key, value in values.items()
            if value is not None and key != "session_id"
        })

    monkeypatch.setattr(observer, "parse_request", parse)
    monkeypatch.setattr(observer, "validate_result", lambda value, _selected: value)
    helper = load(
        monkeypatch, "_remote_bridge.client_remote_observation",
        BRIDGE + "client_remote_observation.py",
    )
    calls = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/health":
            return {
                "protocol_version": 52,
                "capabilities": {"remote_native_result": 1} if capable else {},
            }
        return {"native": "native-a"}

    client = SimpleNamespace(
        _request=request, _base="fixture-endpoint",
        _remote_path=lambda host, suffix: f"/api/v1/remote/{host}{suffix}",
    )
    options = {
        "session_id": "managed-a", "native_session_id": "native-a",
        "represented": True, "position": None, "max_items": 2, "max_text_chars": 256,
    }
    if capable:
        assert helper.remote_snapshot(client, "host-b", **options) == {"native": "native-a"}
        assert calls[-1][:2] == ("GET", "/api/v1/remote/host-b/sessions/managed-a/result")
        assert calls[-1][2]["params"] == {
            "native_session_id": "native-a", "represented": "true",
            "max_items": 2, "max_text_chars": 256,
        }
        assert calls[-1][2]["request_timeout"] == 15
    else:
        with pytest.raises(ClientError) as error:
            helper.remote_snapshot(client, "host-b", **options)
        assert error.value.status == 426
    assert selected == options
    assert len(calls) == (2 if capable else 1)
    assert calls[0][:2] == ("GET", "/health")


@pytest.mark.parametrize("outcome", ["prior", "accepted", "unknown", "interrupted"])
def test_remote_http_prompt_keeps_one_attempt_and_original_binding(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    remote_observer(monkeypatch)
    monkeypatch.setitem(sys.modules, "ssh_manager", SimpleNamespace(
        CarrierLease=object, CarrierProtocolError=RuntimeError,
        CarrierRemoteError=RuntimeError, CarrierUnavailable=ConnectionError,
        EnvelopeType=SimpleNamespace(RESPONSE="response"),
    ))
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(
        BaseModel=type("BaseModel", (), {}), ConfigDict=dict,
        Field=lambda **_kwargs: None, ValidationError=ValueError,
        field_validator=lambda *_a, **_kw: lambda function: function,
    ))
    native = load(monkeypatch, "_remote_bridge.remote_native_prompt", BRIDGE + "remote_native_prompt.py")
    load(monkeypatch, "_remote_bridge.client_protocol_floor", BRIDGE + "client_protocol_floor.py")
    monkeypatch.setitem(sys.modules, "_remote_bridge.protocol", SimpleNamespace(
        REMOTE_OPERATIONS_PROTOCOL_VERSION=11,
        HTTP_PROTOCOL_MIN_SUPPORTED=1, HTTP_PROTOCOL_VERSION=52,
    ))
    monkeypatch.setitem(sys.modules, "_remote_bridge.remote_operations", SimpleNamespace(
        validate_host=lambda host: host,
    ))

    class ClientError(Exception):
        def __init__(self, status: int, detail: Any) -> None:
            self.status, self.detail = status, detail
            super().__init__(detail)

    monkeypatch.setitem(sys.modules, "_remote_bridge.client", SimpleNamespace(
        BridgeClientError=ClientError, BridgeConnectionError=ConnectionError,
    ))
    selected = SimpleNamespace(
        session_id="managed-a", native_session_id="native-a", invocation_key="original-key",
        model_dump=lambda **_kw: {
            "native_session_id": "native-a", "invocation_key": "original-key", "prompt": "Continue",
        },
    )
    monkeypatch.setattr(native, "parse_request", lambda _values: selected)
    helper = load(
        monkeypatch, "_remote_bridge.client_remote_native_prompt",
        BRIDGE + "client_remote_native_prompt.py",
    )
    calls = []

    def request(method: str, path: str, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False
        assert kwargs["_endpoint"] == "original-endpoint"
        if method == "GET":
            client._base = "replacement-endpoint"
            return {
                "protocol_version": 52,
                "capabilities": {} if outcome == "prior" else {"remote_native_prompt": 1},
            }
        if outcome == "unknown":
            raise ClientError(503, "draining")
        if outcome == "interrupted":
            raise KeyboardInterrupt
        return {
            "version": 1, "host": "host-b", "session_id": "managed-a",
            "native_session_id": "native-a", "invocation_key": "original-key",
            "turn_index": 0, "queued": False, "admission": "accepted",
        }

    client = SimpleNamespace(
        _request=request, _base="original-endpoint",
        _remote_path=lambda host, suffix: f"/api/v1/remote/{host}{suffix}",
    )
    options = {
        "session_id": "managed-a", "native_session_id": "native-a",
        "invocation_key": "original-key", "prompt": "Continue", "caller_id": None,
    }
    if outcome == "accepted":
        assert helper.submit_remote(client, "host-b", **options)["invocation_key"] == "original-key"
    else:
        with pytest.raises(ClientError) as error:
            helper.submit_remote(client, "host-b", **options)
        if outcome == "prior":
            assert error.value.status == 426
        else:
            assert error.value.detail["code"] == "native_prompt_admission_unknown"
            assert error.value.detail["invocation_key"] == "original-key"
            assert error.value.detail["host"] == "host-b"
    assert [method for method, _path in calls] == (["GET"] if outcome == "prior" else ["GET", "POST"])


@pytest.mark.parametrize("outcome", [
    "prior", "accepted", "unknown", "interrupted", "session", "sender", "key", "host", "message",
    "bound-refusal", "unbound-refusal", "health-deadline", "post-deadline",
    "health-error", "health-shape",
])
def test_remote_http_inbox_prompt_keeps_one_attempt_and_original_binding(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    remote_observer(monkeypatch)
    monkeypatch.setitem(sys.modules, "ssh_manager", SimpleNamespace(
        CarrierLease=object, CarrierProtocolError=RuntimeError,
        CarrierRemoteError=RuntimeError, CarrierUnavailable=ConnectionError,
        EnvelopeType=SimpleNamespace(RESPONSE="response"),
    ))
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(
        BaseModel=type("BaseModel", (), {}), ConfigDict=dict,
        Field=lambda **_kwargs: None, ValidationError=ValueError,
        field_validator=lambda *_a, **_kw: lambda function: function,
    ))
    live = load(monkeypatch, "_remote_bridge.remote_live_prompt", BRIDGE + "remote_live_prompt.py")
    load(monkeypatch, "_remote_bridge.client_protocol_floor", BRIDGE + "client_protocol_floor.py")
    monkeypatch.setitem(sys.modules, "_remote_bridge.protocol", SimpleNamespace(
        REMOTE_OPERATIONS_PROTOCOL_VERSION=11,
        HTTP_PROTOCOL_MIN_SUPPORTED=1, HTTP_PROTOCOL_VERSION=52,
    ))
    monkeypatch.setitem(sys.modules, "_remote_bridge.remote_operations", SimpleNamespace(
        validate_host=lambda host: host,
    ))

    class ClientError(Exception):
        def __init__(self, status: int, detail: Any) -> None:
            self.status, self.detail = status, detail
            super().__init__(detail)

    monkeypatch.setitem(sys.modules, "_remote_bridge.client", SimpleNamespace(
        BridgeClientError=ClientError, BridgeConnectionError=ConnectionError,
    ))
    options = {
        "session_id": "live-a", "invocation_key": "original-key",
        "sender": "fixture-controller", "prompt": "Continue",
    }
    selected = SimpleNamespace(
        **options, model_dump=lambda **_kw: {
            key: value for key, value in options.items() if key != "session_id"
        },
    )
    monkeypatch.setattr(live, "parse_request", lambda _values: selected)
    helper = load(
        monkeypatch, "_remote_bridge.client_remote_live_prompt",
        BRIDGE + "client_remote_live_prompt.py",
    )
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls = []

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False and kwargs["_endpoint"] == "original-endpoint"
        if method == "GET":
            if outcome == "health-error":
                raise ConnectionError("fixture failed health")
            if outcome == "health-shape":
                return {"protocol_version": "invalid"}
            client._base = "replacement-endpoint"
            if outcome == "health-deadline":
                clock[0] = 21
            return {
                "protocol_version": 52,
                "capabilities": {} if outcome == "prior" else {"remote_exact_live_prompt": 1},
            }
        assert args == ({key: value for key, value in options.items() if key != "session_id"},)
        assert path == "/api/v1/remote/host-b/live-sessions/live-a/exact-prompt"
        if outcome == "post-deadline":
            clock[0] = 21
        if outcome == "unknown":
            raise ClientError(503, "draining")
        if outcome == "interrupted":
            raise KeyboardInterrupt
        value = {
            "version": 1, "host": "host-b", **options,
            "message_id": 1, "admission": "accepted",
        }
        if outcome in {"bound-refusal", "unbound-refusal"}:
            detail = {**value, "code": "unsupported_operation", "reconnectable": False}
            if outcome == "unbound-refusal":
                detail["sender"] = "replacement"
            raise ClientError(501, detail)
        field = {
            "session": "session_id", "sender": "sender", "key": "invocation_key",
            "host": "host", "message": "message_id",
        }.get(outcome)
        if field:
            value[field] = "replacement"
        return value

    client = SimpleNamespace(
        _request=request, _base="original-endpoint",
        _remote_path=lambda host, suffix: f"/api/v1/remote/{host}{suffix}",
        _connect_grace=30.0,
        _reresolve=lambda: pytest.fail("failed health must not select a replacement endpoint"),
    )
    if outcome == "accepted":
        assert helper.submit_remote(client, "host-b", **options)["invocation_key"] == "original-key"
    elif outcome in {"health-error", "health-shape"}:
        with pytest.raises(ConnectionError):
            helper.submit_remote(client, "host-b", **options)
    elif outcome == "health-deadline":
        with pytest.raises(sys.modules["_remote_bridge.client_read_budget"].ReadDeadlineExpired):
            helper.submit_remote(client, "host-b", **options)
    else:
        with pytest.raises(ClientError) as error:
            helper.submit_remote(client, "host-b", **options)
        if outcome == "prior":
            assert error.value.status == 426
        else:
            assert error.value.detail["code"] == (
                "unsupported_operation" if outcome == "bound-refusal" else "exact_live_prompt_admission_unknown"
            )
            assert error.value.detail["invocation_key"] == "original-key"
            assert error.value.detail["sender"] == "fixture-controller"
            if outcome == "post-deadline":
                assert error.value.status == 504
    assert [method for method, _path in calls] == (
        ["GET"] if outcome in {"prior", "health-deadline", "health-error", "health-shape"}
        else ["GET", "POST"]
    )


def test_remote_observation_counts_native_content_not_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = remote_observer(monkeypatch)
    result = SimpleNamespace(
        latest_result=SimpleNamespace(value={"text": "latest", "stop_reason": "done", "completed_at": "ignored"}),
        state=SimpleNamespace(
            active_work=SimpleNamespace(value={
                "tool": {"title": "tool", "command": "command", "kind": "read", "tool_call_id": "id"},
                "milestones": {"phase": "ready"},
            }),
            pending_input=SimpleNamespace(value=[{
                "message": "question", "tool_call_id": "ask", "fields": ["answer"],
                "kind": "input", "request_id": "request", "request_family": "user_input",
            }]),
            pending_messages=SimpleNamespace(value=[{"sender": "sender", "kind": "peer", "delivery": "ignored"}]),
        ),
        incremental=SimpleNamespace(items=[SimpleNamespace(summary="summary", status="ok")]),
    )
    budgeted = (
        "latest", "done", "command", "read", "id", "phase", "ready", "question", "ask",
        "answer", "request", "user_input", "sender", "peer", "summary", "ok",
    )
    assert observer._content_text_chars(result) == sum(map(len, budgeted))


@pytest.mark.parametrize("represented", [False, True])
def test_remote_observation_calls_only_the_selected_native_owner(
    monkeypatch: pytest.MonkeyPatch, represented: bool,
) -> None:
    observer = remote_observer(monkeypatch)
    calls = []
    value = {"native": "native-a"}
    request = SimpleNamespace(
        session_id="managed-a", native_session_id="native-a", represented=represented,
        position="original-position", max_items=2, max_text_chars=256,
    )

    def read(session_id: str, **bounds: Any) -> Any:
        calls.append((session_id, bounds))
        return value

    client = SimpleNamespace(**{
        "get_live_result_snapshot" if represented else "get_result_snapshot": read,
    })
    validated: list[tuple[Any, Any]] = []

    def validate(actual: dict[str, Any], selected: Any) -> dict[str, Any]:
        validated.append((actual, selected))
        return actual

    monkeypatch.setattr(observer, "validate_result", validate)
    assert observer.read_native_result(lambda: client, request) is value
    assert calls == [("managed-a", {
        "require_native_identity": True, "position": "original-position",
        "max_items": 2, "max_text_chars": 256,
    })]
    assert validated == [(value, request)]


@pytest.mark.parametrize("phase", ["factory", "snapshot"])
def test_remote_observation_rejects_late_evidence_with_one_shared_deadline(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    observer = remote_observer(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls = []

    def read(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("snapshot")
        clock[0] = 15.1
        return {}

    def factory() -> Any:
        calls.append("factory")
        if phase == "factory":
            clock[0] = 15.1
        return SimpleNamespace(get_result_snapshot=read)

    monkeypatch.setattr(observer, "validate_result", lambda *_args: pytest.fail("late result"))
    request = SimpleNamespace(
        session_id="managed-a", represented=False, position=None, max_items=2, max_text_chars=256,
    )
    with pytest.raises(observer.RemoteBridgeError) as error:
        observer.read_native_result(factory, request)
    assert (error.value.status, error.value.code) == (504, "remote_operation_timeout")
    assert calls == (["factory"] if phase == "factory" else ["factory", "snapshot"])


@pytest.mark.parametrize("represented", [False, True])
def test_bridge_send_adapter_pins_native_owner_and_never_uses_legacy_selection(
    monkeypatch: pytest.MonkeyPatch, represented: bool,
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    adapter = load(monkeypatch, "_send_bridge.bridge_send_cli", BRIDGE + "bridge_send_cli.py")
    load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")
    calls: list[tuple[Any, ...]] = []
    row = {
        "status": "running", "session_id": "native",
        "managed_session_id": None if represented else "managed",
        "registered_session_id": "native" if represented else None,
    }

    def resolve(handle: str, **scope: Any) -> dict[str, Any]:
        calls.append(("resolve", handle, scope))
        return {"bridge": row}

    client = SimpleNamespace(resolve_bridge=resolve)

    def get_client(**options: Any) -> Any:
        assert options == {"ensure": False, "propagate_errors": True}
        return client

    core = SimpleNamespace(
        _get_client=get_client, _caller_id_for=lambda _args: "caller",
        _submit_and_stream=lambda *args, **kwargs: calls.append(("managed", args, kwargs)),
        _deliver_to_live_session=lambda *args: calls.append(("registered", args)),
    )
    for name, value in {
        "__main__": core,
        "client": SimpleNamespace(BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError),
        "bridge_listing_cli": SimpleNamespace(_scope=lambda _args: {"project": "demo"}),
        "client_protocol_floor": SimpleNamespace(hold_protocol_floor=lambda *_args: None),
        "hosting_admission_cli": SimpleNamespace(configure_client=lambda *_args: None),
        "session_targeting_cli": SimpleNamespace(
            _resolve_prompt=lambda *_args, **_kw: "work",
            _submit_and_stream=core._submit_and_stream,
            _deliver_to_live_session=core._deliver_to_live_session,
        ),
    }.items():
        monkeypatch.setitem(sys.modules, "_send_bridge." + name, value)
    args = argparse.Namespace(target="workspace", bridge=True, idempotency_key="original")
    adapter.send(args)
    assert calls[0] == ("resolve", "workspace", {"local_only": True, "project": "demo"})
    assert len(calls) == 2
    actual = calls[1][1]
    bound = actual[1]
    assert actual[0] is client and actual[2:] == (
        "native" if represented else "managed", "work",
    )
    assert bound is not args and bound.idempotency_key == "original"
    if represented:
        assert bound.expected_session_id == "native" and bound.exact_session
    else:
        assert bound.expected_native_session_id == "native" and bound.no_resume
        assert calls[1][2] == {"caller_id": "caller"}
    assert not hasattr(args, "expected_session_id")
    assert not hasattr(args, "expected_native_session_id")


@pytest.mark.parametrize("observation", ["selected", "later", "timeout", "identity", "failure", "origin"])
def test_remote_send_wait_never_reports_another_turn_or_replays_admission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], observation: str,
) -> None:
    package(monkeypatch, "_send_bridge")
    budget = load(monkeypatch, "_send_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    helper = load(monkeypatch, "_send_bridge.bridge_remote_send_cli", BRIDGE + "bridge_remote_send_cli.py")
    monkeypatch.setitem(sys.modules, "_send_bridge.__main__", SimpleNamespace(_RECONNECT_BACKOFF=0))
    monkeypatch.setitem(sys.modules, "_send_bridge.client", SimpleNamespace(
        BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
    ))
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls = []

    def snapshot(client: Any, target: Any, **bounds: Any) -> dict[str, Any]:
        calls.append((client, target, bounds))
        if observation == "failure":
            raise ConnectionError("fixture read unavailable")
        if observation == "timeout":
            clock[0] = 2.1
        return {
            "identity": {
                "current_session_id": "successor" if observation == "identity" else "managed",
            },
            "latest_result": {
                "availability": "unsupported_for_target" if observation == "origin" else "available",
                "value": {
                    "turn_index": 3 if observation == "selected" else 4,
                    "text": "selected result" if observation == "selected" else "foreign result",
                },
            },
            "state": {
                "session_status": "idle" if observation == "origin" else "running", "at_rest": True,
            },
        }

    monkeypatch.setitem(sys.modules, "_send_bridge.bridge_observation_cli", SimpleNamespace(
        _snapshot=snapshot,
    ))
    client, target = object(), SimpleNamespace(session_id="managed")
    helper._wait_for_result(client, target, 3, 2)
    output = capsys.readouterr()
    assert calls == [(client, target, {"max_items": 1, "max_text_chars": 6000})]
    assert "foreign result" not in output.out
    if observation == "selected":
        assert output.out.strip() == "selected result" and not output.err
    else:
        assert not output.out
        assert "remains accepted" in output.err and "do not resend" in output.err
    assert budget.remaining() is None


@pytest.mark.parametrize("outcome", ["accepted", "busy", "unknown"])
def test_remote_send_adoption_retains_original_key_and_bound_error_meaning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str,
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    load(monkeypatch, "_send_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    errors = load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")
    helper = load(monkeypatch, "_send_bridge.bridge_remote_send_cli", BRIDGE + "bridge_remote_send_cli.py")

    class ClientError(Exception):
        def __init__(self, detail: dict[str, Any]) -> None:
            self.detail = detail
            self.status = 409

    class Unknown(Exception):
        pass

    def unknown(_error: Exception) -> Any:
        raise Unknown

    monkeypatch.setitem(sys.modules, "_send_bridge.client", SimpleNamespace(
        BridgeClientError=ClientError, BridgeConnectionError=ConnectionError,
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.__main__", SimpleNamespace(
        _caller_id_for=lambda _args: "caller",
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.bridge_send_cli", SimpleNamespace(
        _refuse=lambda *_args: pytest.fail("unexpected preflight refusal"), _unknown=unknown,
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.bridge_observation_cli", SimpleNamespace(
        _Target=lambda *args: SimpleNamespace(native_session_id=args[2]),
        _snapshot=lambda *_args, **_kw: pytest.fail("JSON admission must not read results"),
    ))
    calls = []

    def submit(host: str, **fields: Any) -> dict[str, Any]:
        calls.append((host, fields))
        if outcome != "accepted":
            raise ClientError({
                "code": "native_prompt_refused" if outcome == "busy" else "native_prompt_admission_unknown",
                "refusal": {
                    "outcome": "refused_busy", "reason": "busy", "error": "target busy", "retryable": True,
                },
            })
        return {"queued": False, "invocation_key": fields["invocation_key"]}

    args = argparse.Namespace(target="workspace", host="host-b", json=True, no_wait=False)
    row = {"managed_session_id": "managed", "session_id": "native"}
    if outcome == "accepted":
        helper.send(SimpleNamespace(submit_remote_native_prompt=submit), args, row, "work")
        assert json.loads(capsys.readouterr().out)["outcome"] == "delivered"
    else:
        with pytest.raises(errors.SendRefused if outcome == "busy" else Unknown) as error:
            helper.send(SimpleNamespace(submit_remote_native_prompt=submit), args, row, "work")
        if outcome == "busy":
            assert error.value.exit_code == 75
    assert calls == [("host-b", {
        "session_id": "managed", "native_session_id": "native",
        "invocation_key": args.idempotency_key, "prompt": "work", "caller_id": "caller",
    })]
    assert len(args.idempotency_key) == 32
    assert args._bridge_send_identity["host"] == "host-b"


@pytest.mark.parametrize("outcome", ["accepted", "unknown", "prior", "invalid"])
@pytest.mark.parametrize("original_key", [None, "original-key"])
def test_remote_registered_send_retains_binding_without_result_reads_or_replay(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    outcome: str, original_key: str | None,
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    load(monkeypatch, "_send_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    errors = load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")
    helper = load(monkeypatch, "_send_bridge.bridge_remote_send_cli", BRIDGE + "bridge_remote_send_cli.py")

    class ClientError(Exception):
        def __init__(self, status: int, code: str) -> None:
            self.status, self.detail = status, {"code": code}

    class Unknown(Exception):
        pass

    def unknown(_error: Exception) -> Any:
        raise Unknown

    def refuse(_args: Any, reason: str, message: str) -> Any:
        raise errors.SendRefused(
            "refused_invalid" if reason == "bridge_send_options" else "refused_unavailable",
            reason=reason, error=message, retryable=False,
        )

    monkeypatch.setitem(sys.modules, "_send_bridge.client", SimpleNamespace(
        BridgeClientError=ClientError, BridgeConnectionError=ConnectionError,
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.__main__", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "_send_bridge.session_targeting_cli", SimpleNamespace(
        _live_sender_label=lambda _args: "controller",
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.bridge_send_cli", SimpleNamespace(
        _refuse=refuse, _unknown=unknown,
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.bridge_observation_cli", SimpleNamespace(
        _Target=lambda *_args: pytest.fail("inbox admission must not construct a managed target"),
        _snapshot=lambda *_args, **_kw: pytest.fail("inbox admission must not read results"),
    ))
    calls = []

    def submit(host: str, **fields: Any) -> dict[str, Any]:
        calls.append((host, fields))
        if outcome != "accepted":
            status, code = {
                "unknown": (502, "exact_live_prompt_admission_unknown"),
                "prior": (426, "unsupported_protocol"),
                "invalid": (400, "invalid_request"),
            }[outcome]
            raise ClientError(status, code)
        return {
            "version": 1, "host": host, **fields, "admission": "accepted", "message_id": 41,
        }

    args = argparse.Namespace(
        target="workspace", host="host-b", json=True, no_wait=False, idempotency_key=original_key,
    )
    row = {"registered_session_id": "native", "session_id": "native"}
    client = SimpleNamespace(submit_remote_exact_live_prompt=submit)
    if outcome == "accepted":
        helper.send(client, args, row, "work")
        value = json.loads(capsys.readouterr().out)
        assert value["outcome"] == "accepted" and value["delivered"] is None
        assert value["message_id"] == 41 and value["duplicate"] is None
    else:
        with pytest.raises(Unknown if outcome == "unknown" else errors.SendRefused) as error:
            helper.send(client, args, row, "work")
        if outcome != "unknown":
            assert error.value.exit_code == (2 if outcome == "invalid" else 69)
    assert calls == [("host-b", {
        "session_id": "native", "sender": "controller", "invocation_key": args.idempotency_key,
        "prompt": "work",
    })]
    assert args.idempotency_key == original_key if original_key else len(args.idempotency_key) == 32
    assert args._bridge_send_identity == {
        "host": "host-b", "session_id": "native", "managed_session_id": None,
        "registered_session_id": "native", "sender": "controller",
    }


@pytest.mark.parametrize("module", ["bridge_send_cli", "bridge_observation_cli"])
@pytest.mark.parametrize("options,expected", [
    ([], True), (["--bridge"], True), (["--legacy-selector"], False),
])
def test_ordinary_parser_defaults_to_exact_selection_with_explicit_legacy_escape(
    monkeypatch: pytest.MonkeyPatch, module: str, options: list[str], expected: bool,
) -> None:
    import argparse

    package(monkeypatch, "_default_bridge")
    adapter = load(monkeypatch, "_default_bridge." + module, BRIDGE + module + ".py")
    parser = argparse.ArgumentParser()
    adapter.register(parser)
    args = parser.parse_args(options)
    assert adapter.selected(args) is expected
    with pytest.raises(SystemExit) as conflict:
        parser.parse_args(["--bridge", "--legacy-selector"])
    assert conflict.value.code == 2


@pytest.mark.parametrize("module,field,value", [
    ("bridge_send_cli", "force", True),
    ("bridge_send_cli", "expected_native_session_id", ""),
    ("bridge_send_cli", "delivery", "queue"),
    ("bridge_send_cli", "receipt_only", True),
    ("bridge_observation_cli", "event", 0),
    ("bridge_observation_cli", "tail", 0),
    ("bridge_observation_cli", "attention", ["input_required"]),
    ("bridge_observation_cli", "no_follow", True),
])
def test_specialized_parser_mode_keeps_its_owner_without_scope_fallback(
    monkeypatch: pytest.MonkeyPatch, module: str, field: str, value: Any,
) -> None:
    import argparse

    package(monkeypatch, "_default_bridge")
    adapter = load(monkeypatch, "_default_bridge." + module, BRIDGE + module + ".py")
    args = argparse.Namespace(bridge=None, **{field: value})
    assert adapter.selected(args) is False
    args.host = "host-b"
    with pytest.raises(SystemExit, match="exact bridge selection"):
        adapter.selected(args)


@pytest.mark.parametrize("option", [
    "force", "queue", "new", "wait_reply", "wait_delivery", "full_history", "live_exact",
    "native_invocation_file", "settle_native_send", "expected_session_id",
    "expected_native_session_id", "plugin_context_stdin", "startup_process_timeout",
    "require_agent_hosting",
])
def test_bridge_send_rejects_mixed_modes_before_client_or_prompt(
    monkeypatch: pytest.MonkeyPatch, option: str,
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    adapter = load(monkeypatch, "_send_bridge.bridge_send_cli", BRIDGE + "bridge_send_cli.py")
    outcome = load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")
    for name in (
        "__main__", "bridge_listing_cli", "client_protocol_floor", "client",
        "hosting_admission_cli", "session_targeting_cli",
    ):
        monkeypatch.setitem(sys.modules, "_send_bridge." + name, SimpleNamespace(
            _scope=None, hold_protocol_floor=None, configure_client=None, _resolve_prompt=None,
            _submit_and_stream=None, _deliver_to_live_session=None,
            BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
        ))
    with pytest.raises(outcome.SendRefused) as exc:
        adapter.send(argparse.Namespace(
            target="workspace", **{option: True},
            **({"host": "host-b"} if option == "require_agent_hosting" else {}),
        ))
    assert exc.value.reason == "bridge_send_options" and exc.value.exit_code == 2


def test_text_unknown_admission_preserves_original_remote_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    outcome = load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")

    class Unknown(Exception):
        clock, phase, reason, elapsed = None, "bridge-prompt-admission", "admission_unknown", 0.0

    monkeypatch.setitem(sys.modules, "_send_bridge.client", SimpleNamespace(BridgeClientError=RuntimeError))
    monkeypatch.setitem(sys.modules, "_send_bridge.startup_observation", SimpleNamespace(
        StartupObservationError=Unknown,
    ))

    def unconfirmed(_args: Any) -> None:
        raise Unknown

    args = argparse.Namespace(
        json=False, target="workspace", idempotency_key="generated-key",
        _bridge_send_identity={
            "host": "host-b", "managed_session_id": "managed", "session_id": "native",
        },
    )
    with pytest.raises(SystemExit) as error:
        outcome.run_send(args, unconfirmed)
    assert error.value.code == 70
    output = capsys.readouterr()
    assert not output.out
    assert all(value in output.err for value in ("host-b", "managed", "native", "generated-key"))

@pytest.mark.parametrize("option", [
    "native_invocation_file", "expected_native_session_id", "expected_session_id", "idempotency_key",
])
def test_bridge_send_never_ignores_empty_special_option(
    monkeypatch: pytest.MonkeyPatch, option: str,
) -> None:
    import argparse

    package(monkeypatch, "_send_bridge")
    adapter = load(monkeypatch, "_send_bridge.bridge_send_cli", BRIDGE + "bridge_send_cli.py")
    outcome = load(monkeypatch, "_send_bridge.send_outcome", BRIDGE + "send_outcome.py")
    for name in (
        "__main__", "bridge_listing_cli", "client_protocol_floor", "client",
        "hosting_admission_cli", "session_targeting_cli",
    ):
        monkeypatch.setitem(sys.modules, "_send_bridge." + name, SimpleNamespace(
            _scope=None, hold_protocol_floor=None, configure_client=None, _resolve_prompt=None,
            _submit_and_stream=None, _deliver_to_live_session=None,
            BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
        ))
    with pytest.raises(outcome.SendRefused) as exc:
        adapter.send(argparse.Namespace(target="workspace", **{option: ""}))
    assert exc.value.reason == "bridge_send_options" and exc.value.exit_code == 2


@pytest.mark.parametrize("exact", [False, True])
def test_live_send_helper_keeps_legacy_payload_and_selects_exact_request_without_retry(
    monkeypatch: pytest.MonkeyPatch, exact: bool,
) -> None:
    package(monkeypatch, "_send_bridge")
    load(monkeypatch, "_send_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    load(monkeypatch, "_send_bridge.client_protocol_floor", BRIDGE + "client_protocol_floor.py")
    monkeypatch.setitem(sys.modules, "_send_bridge.protocol", SimpleNamespace(
        HTTP_PROTOCOL_MIN_SUPPORTED=1, HTTP_PROTOCOL_VERSION=52,
        LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION=30, PLUGIN_READINESS_PROTOCOL_VERSION=41,
    ))
    monkeypatch.setitem(sys.modules, "_send_bridge.client", SimpleNamespace(
        BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
    ))
    helper = load(monkeypatch, "_send_bridge.client_live_send", BRIDGE + "client_live_send.py")
    calls = []

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, args, kwargs))
        if path == "/health":
            return {"protocol_version": 52, "capabilities": {"live_exact_send": 1}}
        return {"session_id": "native", "message_id": 7}

    client = SimpleNamespace(_request=request, _base="fixture-endpoint")
    assert helper.send_live_message(
        client, "native", sender="peer", body="work", reply_to="caller",
        expected_session_id="native", idempotency_key="original", exact_session=exact,
    ) == {"session_id": "native", "message_id": 7}
    payload: dict[str, Any] = {
        "sender": "peer", "body": "work", "reply_to": "caller",
        "expected_session_id": "native", "idempotency_key": "original",
    }
    if exact:
        payload["exact_session"] = True
        assert calls[0][1] == "/health"
        assert calls[-1][3]["allow_retry"] is False
    else:
        assert calls[-1][3] == {"request_timeout": None}
    assert calls[-1][:3] == ("POST", "/api/v1/live-sessions/native/messages", (payload,))
    assert len(calls) == (2 if exact else 1)


@pytest.mark.parametrize("status,code", [
    (409, "retained_inbox_refused"), (409, "fixture_other"), (500, "retained_inbox_refused"),
])
def test_live_refusal_mapper_only_classifies_known_retained_pre_admission_refusal(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str,
) -> None:
    outcome = load(monkeypatch, "_retained_send_outcome", BRIDGE + "send_outcome.py")
    refused = outcome.refusal_from_live_error(
        SimpleNamespace(status=status, detail={"code": code, "message": "fixture"}), "native",
    )
    if status == 409 and code == "retained_inbox_refused":
        assert refused is not None
        assert refused.payload()["reason"] == code
        assert refused.exit_code == 69 and refused.retryable is False
    else:
        assert refused is None


@pytest.mark.parametrize("native", [False, True])
def test_owned_snapshot_request_preserves_legacy_and_qualifies_native_attempt(
    monkeypatch: pytest.MonkeyPatch, native: bool,
) -> None:
    package(monkeypatch, "_read_bridge")
    load(monkeypatch, "_read_bridge.client_read_budget", BRIDGE + "client_read_budget.py")
    load(monkeypatch, "_read_bridge.client_protocol_floor", BRIDGE + "client_protocol_floor.py")
    monkeypatch.setitem(sys.modules, "_read_bridge.protocol", SimpleNamespace(
        RESULT_SNAPSHOT_PROTOCOL_VERSION=5,
        HTTP_PROTOCOL_MIN_SUPPORTED=1, HTTP_PROTOCOL_VERSION=10,
    ))
    monkeypatch.setitem(sys.modules, "_read_bridge.client", SimpleNamespace(
        BridgeClientError=RuntimeError, BridgeConnectionError=ConnectionError,
    ))
    helper = load(
        monkeypatch, "_read_bridge.client_result_snapshots", BRIDGE + "client_result_snapshots.py",
    )
    calls = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/health":
            return {"protocol_version": 10, "capabilities": {"result_native_identity": 1}}
        return {"identity": {"native_session_id": "original-native"}}

    client = SimpleNamespace(
        _require_result_snapshots=lambda: None, _request=request, _base="fixture-endpoint",
    )
    assert helper.owned_snapshot(
        client, "managed", position="cursor", max_items=2, max_text_chars=256,
        require_native_identity=native,
    ) == {"identity": {"native_session_id": "original-native"}}
    params = {"position": "cursor", "max_items": 2, "max_text_chars": 256}
    if native:
        params["native_only"] = "true"
        assert calls[0][1] == "/health"
    assert calls[-1][:2] == ("GET", "/api/v1/sessions/managed/result")
    assert calls[-1][2]["params"] == params
    assert len(calls) == (2 if native else 1)


def test_observation_deadline_caps_sleep_and_restores_outer_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = load(monkeypatch, "_read_budget", BRIDGE + "client_read_budget.py")
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    sleeps = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(time, "sleep", sleep)
    with budget.command_deadline(2):
        with pytest.raises(budget.ReadDeadlineExpired), budget.command_deadline(.25):
            budget.pause(5)
        assert budget.remaining() == 1.75
    assert budget.remaining() is None
    assert sleeps == [.25]


def test_observation_deadline_rechecks_each_raw_response_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    budget = load(monkeypatch, "_read_budget", BRIDGE + "client_read_budget.py")
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    timeouts: list[float] = []

    class Trickle(io.BytesIO):
        def readinto(self, buffer: Any) -> int:
            clock[0] += .1
            return super().readinto(memoryview(buffer)[:1])

    stream = Trickle(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    sock = SimpleNamespace(
        gettimeout=lambda: 120, settimeout=timeouts.append,
        makefile=lambda _mode, **_kwargs: stream,
    )
    with budget.command_deadline(.25):
        response = budget._Response(sock, method="GET")
        try:
            with pytest.raises(budget.ReadDeadlineExpired):
                response.begin()
        finally:
            response.close()
    assert timeouts == pytest.approx([.25, .15, .05])
    assert stream.closed


def test_source_window_and_degraded_render_do_not_renew_or_invent_native_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package(monkeypatch, "_inventory_bridge")
    package(monkeypatch, "_inventory_bridge.routes")
    refresh = load(
        monkeypatch, "_inventory_bridge.routes.worktree_refresh", BRIDGE + "routes/worktree_refresh.py",
    )
    catalog = load(monkeypatch, "_inventory_bridge.local_session_catalog", BRIDGE + "local_session_catalog.py")
    value: dict[str, Any] = {
        "version": 1, "contract_version": 1,
        "projection": {
            "quality": "current", "observed_at": time.time() - 40,
            "freshness": {"bound_s": 90, "refreshed_age_s": 40},
        },
        "worktrees": [{"id": "workspace", "path": "workspace-path"}],
    }
    observed, expiry = refresh.inventory_window(json.dumps(value))
    assert 39 <= time.monotonic() - observed <= 42
    assert 48 <= expiry - time.monotonic() <= 51
    evidence, retained_expiry = catalog._inventory_observation(json.dumps(value))
    assert evidence["observation_valid"] is True
    assert retained_expiry is not None and abs(retained_expiry - expiry) < 1
    value["availability"] = {"status": "stale", "reason": "cleanup pending"}
    evidence, retained_expiry = catalog._inventory_observation(json.dumps(value))
    assert evidence == {
        "availability": "unavailable", "reason": "source-degraded", "observation_valid": False,
    }
    assert retained_expiry is None
    assert "session_id" not in evidence
    value["worktrees"] = [{"path": "missing-id"}]
    with pytest.raises(ValueError):
        catalog._inventory_observation(json.dumps(value))


def test_host_observation_filters_service_and_exact_birth_without_controller_adoption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    package(monkeypatch, "_inventory_procutil")
    records = [
        {"service": "other", "pid": "999", "birth": "other"},
        {
            "service": "agent-bridge-session-host", "pid": "123", "birth": "native-birth",
            "state": "running",
            "purpose": "production", "runtime_root": str(tmp_path), "instance_id": "original",
        },
    ]
    monkeypatch.setitem(sys.modules, "_inventory_procutil._daemon_instance_store", SimpleNamespace(
        inspect=lambda root: records,
    ))
    probes: list[tuple[int, str]] = []

    def identity(pid: int, birth: str) -> str:
        probes.append((pid, birth))
        return "matching"

    monkeypatch.setitem(sys.modules, "_inventory_procutil._daemon_instance_identity", SimpleNamespace(
        identity_state=identity,
    ))
    procutil = load(
        monkeypatch, "_inventory_procutil.daemon_instances",
        "libs/agent-procutil/src/agent_procutil/daemon_instances.py",
    )
    assert procutil.inspect_instances(tmp_path) is records
    assert probes == []
    observation = load(
        monkeypatch, "_inventory_procutil.daemon_observation",
        "libs/agent-procutil/src/agent_procutil/daemon_observation.py",
    )
    filtered = observation.observe_instances(tmp_path, service="agent-bridge-session-host")
    assert [record["pid"] for record in filtered] == ["123"]
    assert probes == [(123, "native-birth")]
    probes.clear()
    package(monkeypatch, "agent_procutil")
    monkeypatch.setitem(sys.modules, "agent_procutil.daemon_instances", procutil)
    monkeypatch.setitem(sys.modules, "agent_procutil.daemon_observation", observation)
    package(monkeypatch, "_inventory_host")
    package(monkeypatch, "agent_remote_driver")
    package(monkeypatch, "agent_remote_driver.session_host")
    monkeypatch.setitem(sys.modules, "agent_remote_driver.session_host.osutil", SimpleNamespace(
        process_start_time=lambda pid: "native-birth",
    ))
    load(
        monkeypatch, "agent_remote_driver.session_host.registered_inventory",
        "libs/copilot-driver-core/src/agent_remote_driver/session_host/registered_inventory.py",
    )
    hosts = load(
        monkeypatch, "_inventory_host.registered_inventory",
        BRIDGE + "session_host/registered_inventory.py",
    )
    state = {
        "state": "running", "host_pid": 123, "host_instance_id": "original",
        "host_process_start_time": "native-birth", "cwd": "workspace",
        "session_id": None, "control_nonce": "private-control",
    }
    (tmp_path / "host-original.json").write_text(json.dumps(state), encoding="utf-8")
    rows, evidence = hosts.registered_hosts()
    assert probes == [(999, "other"), (123, "native-birth")]
    assert evidence["availability"] == "available"
    assert rows[0]["handle"] == "host-instance:original"
    assert rows[0]["registration"]["session_id"] is None
    assert rows[0]["registration"]["controller"] == "original_owner"
    assert "private-control" not in json.dumps(rows)


def test_managed_listing_reader_preserves_arrays_and_isolates_qualified_catalog_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = load(
        monkeypatch, "_inventory_dispatch_listing",
        "plugins/agent-dispatch/src/agent_dispatch/bridge_listing.py",
    )
    managed = {"session_id": "owned-control", "status": "running"}
    assert reader.managed_rows([managed]) == [managed]
    catalog: dict[str, Any] = {
        "version": 1,
        "sources": {
            "managed": {"availability": "available"},
            "worktrees": {"availability": "unavailable"},
        },
        "targets": [
            {"kind": "managed", "session": managed},
            {"kind": "worktree", "handle": "cold-workspace"},
            {"kind": "session_host", "handle": "unbound-host"},
            {"kind": "live", "handle": "interactive-native"},
        ],
    }
    assert reader.managed_rows(catalog) == [managed]
    for changed in (
        {**catalog, "version": True}, {**catalog, "version": 2},
        {**catalog, "sources": {"managed": {"availability": "unavailable"}}},
        {**catalog, "targets": [{"kind": "managed", "session": None}]},
    ):
        with pytest.raises(ValueError):
            reader.managed_rows(changed)


@pytest.mark.parametrize("owns_record", [False, True])
def test_file_observation_publication_is_owner_bound_and_never_grants_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owns_record: bool,
) -> None:
    package(monkeypatch, "_inventory_file_publisher")
    api = load(
        monkeypatch, "_inventory_file_publisher.daemon_instances",
        "libs/agent-procutil/src/agent_procutil/daemon_instances.py",
    )
    record = {
        "state": "running", "pid": "123", "birth": "native-birth",
        "runtime_root": "unchanged-source-root", "artifact_count": "0",
    }
    writes = []

    @contextmanager
    def locked(receipt: Any, **kwargs: Any) -> Iterator[Path]:
        yield tmp_path / "fixture.instance"

    class Identity:
        @classmethod
        def current(cls) -> Identity:
            return cls()

        def matches(self, value: dict[str, str]) -> bool:
            assert value == record
            return owns_record

    monkeypatch.setitem(sys.modules, "_inventory_file_publisher._daemon_instance_store", SimpleNamespace(
        _locked=locked, _read=lambda path: dict(record),
        _write=lambda path, value: writes.append(value),
    ))
    monkeypatch.setitem(sys.modules, "_inventory_file_publisher._daemon_instance_records", SimpleNamespace(
        ProcessIdentity=Identity,
    ))
    observation = load(
        monkeypatch, "_inventory_file_publisher.daemon_observation",
        "libs/agent-procutil/src/agent_procutil/daemon_observation.py",
    )
    state = tmp_path / "caller-selected.data"
    state.write_text("{}", encoding="utf-8")
    receipt = api.DaemonInstanceReceipt("a" * 32, tmp_path / "registry")
    if not owns_record:
        with pytest.raises(ValueError):
            observation.publish_file_observation(receipt, "session_host_state", state)
        assert writes == []
    else:
        observation.publish_file_observation(receipt, "session_host_state", state)
        assert len(writes) == 1
        assert writes[0]["observation_session_host_state_path"] == str(state.resolve())
        assert writes[0]["runtime_root"] == record["runtime_root"]
        assert writes[0]["artifact_count"] == "0"
