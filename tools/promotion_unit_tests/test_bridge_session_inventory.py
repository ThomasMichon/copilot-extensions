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
        assert options == {"ensure": False}
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


@pytest.mark.parametrize("option", [
    "force", "queue", "new", "wait_reply", "wait_delivery", "full_history", "live_exact",
    "native_invocation_file", "settle_native_send", "expected_session_id",
    "expected_native_session_id", "plugin_context_stdin", "startup_process_timeout",
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
        adapter.send(argparse.Namespace(target="workspace", **{option: True}))
    assert exc.value.reason == "bridge_send_options" and exc.value.exit_code == 2


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
    probes = []

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
    assert probes == [(123, "native-birth")]
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
