"""Actor-free qualification of the exact stop owner's identity predicate."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-bridge/src/agent_bridge/managed_exact_stop.py"


@pytest.fixture(scope="session")
def require_holder() -> Any:
    tree = ast.parse(SOURCE.read_bytes(), str(SOURCE))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "require_holder")
    selected = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    ast.fix_missing_locations(selected)
    namespace: dict[str, Any] = {"SessionStatus": SimpleNamespace(ENDED="ended")}
    exec(compile(selected, str(SOURCE), "exec"), namespace)
    return namespace["require_holder"]


@pytest.mark.parametrize("change", [
    "none", "empty-native", "native", "persisted-native", "missing-row",
    "object", "ended", "persisted-ended", "remote", "client-native", "client-unknown", "cold",
])
def test_exact_stop_requires_current_native_in_both_owners(require_holder: Any, change: str) -> None:
    session = SimpleNamespace(
        acp_session_id="native-original", status="idle", target=SimpleNamespace(type="local"),
        client=SimpleNamespace(acp_session_id="native-original"),
    )
    row: dict[str, Any] | None = {"acp_session_id": "native-original", "status": "idle"}
    sessions = {"managed-original": session}
    expected = "native-original"
    if change == "empty-native":
        expected = ""
    elif change == "native":
        session.acp_session_id = "native-newer"
    elif change == "persisted-native":
        assert row is not None
        row["acp_session_id"] = "native-newer"
    elif change == "missing-row":
        row = None
    elif change == "object":
        sessions["managed-original"] = SimpleNamespace()
    elif change == "ended":
        session.status = "ended"
    elif change == "persisted-ended":
        assert row is not None
        row["status"] = "ended"
    elif change == "remote":
        session.target.type = "remote-acp"
    elif change == "client-native":
        session.client.acp_session_id = "native-newer"
    elif change == "client-unknown":
        session.client.acp_session_id = None
    elif change == "cold":
        session.client = None
    manager = SimpleNamespace(_sessions=sessions, db=SimpleNamespace(get_session=lambda _: row))
    if change in {"none", "cold"}:
        require_holder(manager, "managed-original", session, expected)
    else:
        with pytest.raises(ValueError, match="native_stop_owner_unsupported|expected_native_mismatch"):
            require_holder(manager, "managed-original", session, expected)


@pytest.mark.parametrize("case", [
    "success", "connection", "host", "process", "background",
])
def test_exact_shutdown_retains_handles_before_legacy_cleanup(case: str) -> None:
    source = ROOT / "plugins/agent-bridge/src/agent_bridge/acp_shutdown.py"
    tree = ast.parse(source.read_bytes(), str(source))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "shutdown_exact")
    selected = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    ast.fix_missing_locations(selected)
    namespace: dict[str, Any] = {}
    exec(compile(selected, str(source), "exec"), namespace)
    failure = case

    async def close() -> None:
        if failure == "connection":
            raise RuntimeError("closure unknown")
        if failure == "background":
            client.has_active_background_tasks = True

    async def close_host() -> None:
        if failure == "host":
            raise RuntimeError("Host closure unknown")

    async def terminate(proc: Any) -> None:
        assert failure != "background"
        if failure != "process":
            proc.returncode = 0

    async def cleanup() -> None:
        assert client._connection is None and client._host_closer is None
        client._background_tasks.clear()

    connection, proc = SimpleNamespace(close=close), SimpleNamespace(returncode=None)
    client = SimpleNamespace(
        _remote_transport=None, _pending_permission_future=None,
        _cancel_out_of_turn=lambda: None, _pending_elicitations={}, _pending_ask_user_meta={},
        _connection=connection, _host_mode=failure not in {"process", "background"}, _host_closer=close_host,
        _process=proc, _background_tasks={"retained"}, shutdown=cleanup,
        has_active_background_tasks=False,
    )
    if failure in {"process", "background"}:
        client._host_closer = None
    operation = namespace["shutdown_exact"](client, terminate)
    try:
        with pytest.raises(RuntimeError if failure != "success" else StopIteration):
            operation.send(None)
    finally:
        operation.close()
    if failure != "success":
        assert client._background_tasks == {"retained"}
        if failure == "connection":
            assert client._connection is connection
        elif failure == "host":
            assert client._host_closer is close_host
        else:
            assert client._process is proc
    else:
        assert client._connection is None and not client._background_tasks
