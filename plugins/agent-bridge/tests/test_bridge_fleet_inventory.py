"""Fleet observations preserve host routes, source failures and passive reads."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ssh_manager import Envelope, EnvelopeType
from test_worktree_metadata import metadata_boundary as metadata_boundary
from test_worktree_native_resume import boundary as boundary

from agent_bridge.bridge_fleet_inventory import fleet_inventory
from agent_bridge.remote_errors import RemoteBridgeError
from agent_bridge.remote_operations import CarrierRequestRouter


def report(host: str, kind: str = "live") -> dict[str, Any]:
    return {
        "version": 1,
        "identity_version": 2,
        "host": host,
        "complete": True,
        "sources": {kind: {"availability": "available"}},
        "bridges": [
            {
                "handle": "same-worktree",
                "worktree_id": "same-worktree",
                "session_id": f"native-{host}",
                "machine": host,
                "project": "demo",
                "status": "running",
                "live": True,
                "control": "registered",
                "sources": [kind],
                "route": {"handle": f"native-{host}", "kind": kind},
            }
        ],
    }


@pytest.mark.asyncio
async def test_five_hosts_include_local_managed_remote_registered_and_standalone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import bridge_fleet_inventory as fleet

    calls = []

    async def local(_request: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"local_only": True}
        calls.append("host-a")
        return report("host-a", "managed")

    async def remote(host: str) -> dict[str, Any]:
        calls.append(host)
        return report(host, "session_host" if host == "host-e" else "live")

    monkeypatch.setattr(fleet, "inventory", local)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=SimpleNamespace(key="host-a")),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    result = await fleet_inventory(request, ["host-a", "host-b", "host-c", "host-d", "host-e"])
    assert set(calls) == {f"host-{letter}" for letter in "abcde"}
    assert result["complete"] is True
    assert len(result["bridges"]) == 5
    assert {row["route"]["host"] for row in result["bridges"]} == set(calls)
    assert {source for row in result["bridges"] for source in row["sources"]} == {
        "managed",
        "live",
        "session_host",
    }


@pytest.mark.asyncio
async def test_failed_or_old_host_keeps_positives_and_explicit_incomplete_evidence() -> None:
    async def remote(host: str) -> dict[str, Any]:
        if host == "old-host":
            raise RemoteBridgeError(501, "unsupported_operation", "older carrier")
        return report(host)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    result = await fleet_inventory(request, ["host-b", "old-host"])
    assert result["complete"] is False
    (row,) = result["bridges"]
    assert row["route"]["host"] == "host-b"
    assert result["sources"]["hosts"]["old-host"]["reason"] == "unsupported_operation"


@pytest.mark.asyncio
async def test_timeout_keeps_other_hosts_without_exceeding_the_read_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import bridge_fleet_inventory as fleet

    monkeypatch.setattr(fleet, "HOST_TIMEOUT", 0.01)

    async def remote(host: str) -> dict[str, Any]:
        if host == "slow-host":
            await asyncio.Event().wait()
        return report(host)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    result = await fleet_inventory(request, ["host-b", "slow-host"])
    (row,) = result["bridges"]
    assert row["route"]["host"] == "host-b"
    assert result["complete"] is False
    assert result["sources"]["hosts"]["slow-host"]["availability"] == "unavailable"


@pytest.mark.asyncio
async def test_equal_handles_in_different_paths_do_not_merge_across_daemons() -> None:
    async def remote(host: str) -> dict[str, Any]:
        value = report("execution-host")
        value["bridges"][0]["directory"] = f"/work/{host}"
        return value

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    result = await fleet_inventory(request, ["controller-a", "controller-b"])
    assert len(result["bridges"]) == 2
    assert result["complete"] is True


@pytest.mark.asyncio
async def test_live_duplicate_reconciliation_is_independent_of_host_order() -> None:
    async def remote(host: str) -> dict[str, Any]:
        value = report("execution-host", "managed" if host == "old-controller" else "live")
        row = value["bridges"][0]
        if host == "old-controller":
            row.update(
                status="dormant", live=False, control="unavailable", control_reason="not_running"
            )
        return value

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    for hosts in (
        ["old-controller", "current-controller"],
        ["current-controller", "old-controller"],
    ):
        result = await fleet_inventory(request, hosts, live=True)
        (row,) = result["bridges"]
        assert row["live"] is True
        assert row["status"] == "running"
        assert row["control"] == "registered"
        assert row["route"]["host"] == "current-controller"
        assert set(row["sources"]) == {"managed", "live"}
        assert result["complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_live", [False, True])
async def test_selector_preserves_only_aliases_of_the_reconciled_native_head(
    managed_live: bool,
) -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    async def remote(host: str) -> dict[str, Any]:
        value = report("execution-host", "live" if host == "registration" else "managed")
        row = value["bridges"][0]
        if host == "registration":
            row["registered_session_id"] = row["session_id"]
        else:
            row.update(
                live=managed_live,
                status="running" if managed_live else "dormant",
                control="unavailable",
                route={"handle": f"managed-{host}", "kind": "managed"},
                identity_aliases=[f"secondary-{host}"],
            )
            row["managed_session_id" if managed_live else "retained_managed_session_id"] = (
                f"managed-{host}"
            )
            if host == "old-controller":
                row.update(session_id="previous-native", live=False, status="dormant")
        return value

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    hosts = ["registration", "controller-a", "controller-b", "old-controller"]
    for selected_hosts in (hosts, list(reversed(hosts))):
        result = await fleet_inventory(request, selected_hosts)
        (row,) = result["bridges"]
        assert row["route"]["host"] == "registration"
        for alias in (
            "same-worktree",
            "native-execution-host",
            "managed-controller-a",
            "managed-controller-b",
            "secondary-controller-a",
            "secondary-controller-b",
        ):
            assert select_bridge(result, alias)["bridge"] is row
        for alias in ("previous-native", "managed-old-controller", "secondary-old-controller"):
            with pytest.raises(BridgeSelectionError) as refused:
                select_bridge(result, alias)
            assert refused.value.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_selector_rejects_conflicting_heads_from_different_hosts(live: bool) -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    async def remote(host: str) -> dict[str, Any]:
        value = report("execution-host", "managed")
        value["bridges"][0].update(
            session_id=f"native-{host}",
            live=live,
            status="running" if live else "dormant",
            control="unavailable",
        )
        value["bridges"][0]["managed_session_id" if live else "retained_managed_session_id"] = (
            f"managed-{host}"
        )
        return value

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    for hosts in (["controller-a", "controller-b"], ["controller-b", "controller-a"]):
        result = await fleet_inventory(request, hosts)
        for alias in (
            "same-worktree",
            "native-controller-a",
            "native-controller-b",
            "managed-controller-a",
            "managed-controller-b",
        ):
            with pytest.raises(BridgeSelectionError) as refused:
                select_bridge(result, alias)
            assert (refused.value.status, refused.value.reason) == (
                409,
                "ambiguous_bridge_selector",
            )


@pytest.mark.asyncio
async def test_previous_v1_host_cannot_certify_a_missing_managed_alias() -> None:
    from agent_bridge.bridge_inventory import BridgeSelectionError, select_bridge

    async def remote(_host: str) -> dict[str, Any]:
        value = report("execution-host")
        value.pop("identity_version")
        return value

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=SimpleNamespace(_local_machine=None),
                remote_operations=SimpleNamespace(bridge_inventory=remote),
            )
        )
    )
    result = await fleet_inventory(request, ["previous-controller"])
    assert result["complete"] is False
    assert result["sources"]["hosts"]["previous-controller"]["identity_complete"] is False
    assert select_bridge(result, "native-execution-host")["complete"] is False
    with pytest.raises(BridgeSelectionError) as refused:
        select_bridge(result, "managed-alias")
    assert (refused.value.status, refused.value.reason) == (503, "bridge_inventory_incomplete")


@pytest.mark.asyncio
async def test_carrier_inventory_reads_only_local_sources_without_recursive_fanout() -> None:
    calls = []

    def listing(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return report("host-b", "managed")

    client = cast(Any, SimpleNamespace(list_bridges=listing))
    router = CarrierRequestRouter(lambda: client)
    response = await router(
        Envelope(
            EnvelopeType.REQUEST,
            payload={"version": 1, "operation": "bridge.inventory"},
        )
    )
    assert calls == [{"all_projects": True, "local_only": True}]
    assert isinstance(response, Envelope)
    assert response.payload["result"]["bridges"][0]["session_id"] == "native-host-b"


def test_http_inventory_uses_the_same_serialized_local_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import bridge_inventory
    from agent_bridge.worktree_native_resume import router

    calls = []

    async def local(_request: Any, **scope: Any) -> dict[str, Any]:
        calls.append(scope)
        return report("host-a")

    monkeypatch.setattr(bridge_inventory, "inventory", local)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/v1/bridges?project=demo&live=true")
    assert response.status_code == 200
    assert calls == [
        {
            "local_only": False,
            "project": "demo",
            "machine": None,
            "namespace": None,
            "status": None,
            "live": True,
        }
    ]
    assert response.json()["bridges"][0]["session_id"] == "native-host-a"


@pytest.mark.parametrize("capability", [0, 1, 2])
@pytest.mark.parametrize(("operation", "represented"), [
    ("list", True), ("show", True), ("read", True), ("wait", True),
    ("read", False), ("wait", False),
])
def test_source_cli_negotiates_and_reads_real_inventory_without_native_execution(
    metadata_boundary: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capability: int,
    operation: str,
    represented: bool,
) -> None:
    import json

    from agent_bridge import __main__ as cli
    from agent_bridge.client import BridgeClient
    from agent_bridge.events import EventLog
    from agent_bridge.live_representation import LiveEventStore
    from agent_bridge.protocol import HTTP_PROTOCOL_MIN_SUPPORTED, HTTP_PROTOCOL_VERSION
    from agent_bridge.routes.live_sessions import router as live_router
    from agent_bridge.routes.sessions import router as owned_router
    from agent_bridge.session_manager import Session
    from agent_bridge.session_status import SessionStatus
    from agent_bridge.transport import SpawnTarget
    from agent_bridge.worktree_native_resume import router

    bound = metadata_boundary
    if represented:
        bound.db.register_live_session(
            "native-real",
            machine=bound.entry.machine,
            cwd=bound.entry.path,
            worktree_id=bound.entry.id,
            repo="example-project",
            branch=None,
            pid=None,
            role=None,
            now=time.time(),
        )
        bound.db.update_live_turn_state(
            "native-real", turn_state="idle", last_activity_at=time.time(),
        )
    else:
        session = Session(
            "managed-real", "fixture", SpawnTarget(
                type="local", cwd=bound.entry.path,
                worktree_id=bound.entry.id, project="example-project",
            ), "example",
        )
        session.acp_session_id = "native-real"
        session.status = SessionStatus.IDLE
        bound.db.create_session(
            session.session_id, session.name, session.agent_name, session.target.cwd,
            "local", "idle", time.time(), acp_session_id=session.acp_session_id,
        )
        session.event_log = EventLog(db=bound.db, session_id=session.session_id)
        bound.manager._sessions[session.session_id] = session
    app = FastAPI()
    app.state = bound.request.app.state
    app.state.live_event_store = LiveEventStore()
    app.include_router(router)
    app.include_router(live_router)
    app.include_router(owned_router)
    calls = []

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION,
            "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
            "capabilities": {"bridge_inventory": capability} if capability else {},
        }

    bridge = BridgeClient("http://testserver", "fixture-token")
    with TestClient(app) as http:

        def request(method: str, path: str, **kwargs: Any) -> Any:
            calls.append(path)
            response = http.request(method, path, params=kwargs.get("params"))
            response.raise_for_status()
            return response.json()

        monkeypatch.setattr(bridge, "_request", request)
        monkeypatch.setattr(cli, "_sender_repo", lambda: "example-project")
        monkeypatch.setattr(
            cli,
            "_get_client",
            lambda **kwargs: (
                bridge
                if kwargs == {"ensure": False}
                else pytest.fail("listing ensured the service")
            ),
        )
        argv = ["--json", operation, *(["native-real"] if operation != "list" else [])]
        if operation in {"read", "wait"}:
            argv.append("--bridge")
        if capability >= (1 if operation == "list" else 2):
            cli.main(argv)
            result = json.loads(capsys.readouterr().out)
            if operation in {"read", "wait"}:
                payload = result["snapshot"] if operation == "wait" else result
                expected_id = "native-real" if represented else "managed-real"
                assert payload["identity"]["snapshot_session_id"] == expected_id
                assert payload["fidelity"]["level"] == ("reduced" if represented else "full")
                if operation == "wait":
                    assert result["reason"] == "idle"
                    assert result["settled"] is True
                result_path = (
                    "/api/v1/live-sessions/native-real/result"
                    if represented else "/api/v1/sessions/managed-real/result"
                )
                assert result_path in calls
                assert all(
                    path in {
                        "/health", "/api/v1/bridges",
                        "/api/v1/live-sessions/native-real",
                        result_path,
                    }
                    for path in calls
                )
            else:
                row = result["bridge"] if operation == "show" else result["bridges"][0]
                assert row["session_id"] == "native-real"
                assert row["worktree_id"] == bound.entry.id
                assert row["control"] == "registered"
                assert calls == ["/health", "/api/v1/bridges"]
        else:
            with pytest.raises(SystemExit) as refused:
                cli.main(argv)
            assert refused.value.code == 1
            assert calls == ["/health"]
            assert "bridge_inventory" in capsys.readouterr().err
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()
    assert not bound.calls
