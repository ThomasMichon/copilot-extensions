"""Retained recovery qualifies the real Host helper, not a replacement connector."""

from types import SimpleNamespace
import asyncio
import contextlib
import os
from unittest.mock import AsyncMock

import pytest

from agent_bridge import retained_session_recovery as recovery
from agent_bridge.connect import ConnectError
from agent_bridge.runtime_custody import RuntimeCustodyError
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from test_creation_worktree_writer import recovery_fixture, writer_creation  # noqa: F401
from test_workspace_creation import creation_rig, rig as _rig  # noqa: F401

rig = _rig

pytestmark = pytest.mark.contract("agent_bridge.retained_host_recovery")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "resolve-stop", "guidance-stop", "initialize-stop", "initialize-timeout", "load-timeout",
    "host-stop", "child-stop", "host-timeout", "load-stop", "load-retry", "permission-timeout",
])
async def test_real_host_boundary_retains_stop_and_uncertain_custody(
    writer_creation, monkeypatch, fault,  # noqa: F811
):
    fixture, rig = writer_creation, writer_creation.rig
    session, request, _command = await recovery_fixture(fixture, monkeypatch)
    host_records = []
    rig.manager._host_index.register = host_records.append
    monkeypatch.setattr(
        rig.manager, "_connect_via_session_host",
        _SessionHostConnectionMixin._connect_via_session_host.__get__(rig.manager),
    )

    def stop():
        rig.db.execute_write(
            "UPDATE sessions SET cold_recovery_stop_token='late-stop' WHERE id=?",
            (session.session_id,),
        )

    async def resolve(target, **_kwargs):
        if fault == "resolve-stop":
            stop()
        return ["copilot", "--acp", "--stdio"], target.cwd, target.env

    async def guidance(_cwd):
        if fault == "guidance-stop":
            stop()

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", resolve)
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", guidance)
    spawned = SimpleNamespace(
        port=1234, host_pid=99999998, child_pid=99999999, protocol_version=1,
        state_file="fixture-state", nonce="fixture-nonce", boundary="local", endpoint={},
        proc=SimpleNamespace(pid=99999998, poll=lambda: None),
    )
    launches = []

    def launch(args, *, acquisition_launch, **kwargs):
        owner = acquisition_launch
        assert type(owner) is recovery.RetainedRecovery
        context = owner.child_context(args, kwargs["cwd"])
        if fault == "host-stop":
            stop()
        owner.begin_host()
        launches.append(owner)
        owner.retain_process(spawned.proc, spawned.state_file, spawned.nonce)
        if fault == "child-stop":
            stop()
            owner.admit_host_child(context, {
                "argv": args, "cwd": kwargs["cwd"], "session_id": session.session_id,
                "parent_pid": os.getpid(), "host_pid": spawned.host_pid,
                "environment": {"AGENT_BRIDGE_SESSION_ID": session.session_id},
            })
        if fault == "host-timeout":
            raise TimeoutError("original Host readiness unknown")
        return spawned

    monkeypatch.setattr("agent_bridge.session_host.launcher._core.launch_session_host", launch)
    sock = SimpleNamespace(
        attach=AsyncMock(return_value=SimpleNamespace(preference_receipt=None)),
        terminate=AsyncMock(), close=AsyncMock(),
    )
    streams = SimpleNamespace(
        reader=object(), writer=object(), child_exit_code=None, aclose=AsyncMock(),
    )
    monkeypatch.setattr("agent_bridge.session_host.client.SessionHostClient.connect",
                        AsyncMock(return_value=sock))
    monkeypatch.setattr("agent_bridge.session_host.acp_adapter.open_acp_streams",
                        AsyncMock(return_value=streams))

    async def initialize(*_args, **_kwargs):
        if fault == "initialize-stop":
            stop()
        if fault == "initialize-timeout":
            raise TimeoutError("original handshake outcome unknown")
        if fault == "permission-timeout":
            from acp.schema import PermissionOption, ToolCallUpdate

            assert rig.client.auto_approve is False
            native = request.native_session_id
            rig.client._acp_session_id = native
            rig.client.current_requests.session_changed(native)
            pending = asyncio.create_task(rig.client._handle_permission_request(
                [PermissionOption(option_id="allow", name="Allow", kind="allow_once")],
                ToolCallUpdate(tool_call_id="fixture-tool", title="Fixture tool"),
                session_id=native,
            ))
            try:
                await asyncio.sleep(0)
                assert not pending.done()
                assert rig.client._pending_permission_future is not None
            finally:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
            raise TimeoutError("fixture permission held until explicit owner resolution")

    if fault in {"load-stop", "load-retry", "permission-timeout"}:
        from agent_bridge import acp_client

        rig.client = acp_client.AcpClient()
        connection = SimpleNamespace(load_session=AsyncMock(return_value=SimpleNamespace(config_options=None)))
        rig.client._connection = connection
        load_session = rig.client.load_session
        rig.client.load_session = AsyncMock(wraps=load_session)
        monkeypatch.setattr("agent_bridge.session_manager.AcpClient", lambda **_kw: rig.client)

        async def late_rpc(client, method, **kwargs):
            if fault == "load-stop":
                stop()
            else:
                await method(**kwargs)
            return await method(**kwargs)

        monkeypatch.setattr(acp_client, "lifecycle_rpc", late_rpc)
    else:
        rig.client.load_session = AsyncMock(side_effect=(
            TimeoutError("original load outcome unknown") if fault == "load-timeout" else None
        ))
    rig.client.start_streams = AsyncMock(side_effect=initialize)
    with pytest.raises(ConnectError):
        await recovery.resume(
            rig.manager, session.session_id, request, fixture.requirement, lambda: True,
        )
    before_host = fault in {"resolve-stop", "guidance-stop", "host-stop"}
    before_connection = before_host or fault in {"host-timeout", "child-stop"}
    outcome = recovery.inspect(rig.manager, session.session_id, request.operation_id)
    assert outcome["state"] == ("not-started" if before_host else "unknown")
    assert len(launches) == (0 if before_host else 1)
    assert len(host_records) == (0 if before_connection else 1)
    assert bool(rig.db.execute_read("SELECT * FROM acquisition_launch_gates")) is not before_host
    if not before_connection:
        assert session.client is rig.client
        assert session._retained_recovery_owner.socket is sock
        assert session._retained_recovery_owner.streams is streams
    if not before_host:
        with pytest.raises(RuntimeCustodyError, match="reconciliation before teardown"):
            await rig.manager.end_session(session.session_id, force=True)
        assert rig.manager.get_session(session.session_id) is session
    if fault not in {"load-timeout", "load-stop", "load-retry"}:
        rig.client.load_session.assert_not_awaited()
    if fault == "load-stop":
        connection.load_session.assert_not_awaited()
    if fault == "load-retry":
        connection.load_session.assert_awaited_once()
    sock.terminate.assert_not_awaited()
    sock.close.assert_not_awaited()
    streams.aclose.assert_not_awaited()
    if fault.endswith("stop"):
        assert rig.db.get_session(session.session_id)["cold_recovery_stop_token"] == "late-stop"
    assert (await recovery.resume(
        rig.manager, session.session_id, request, fixture.requirement, lambda: True,
    ))[1] == outcome
    assert len(launches) == (0 if before_host else 1)


@pytest.mark.asyncio
async def test_retained_recovery_cannot_enter_while_exact_session_end_is_active(writer_creation, monkeypatch):  # noqa: F811
    from agent_bridge.acquisition_contract import AcquisitionRefused

    fixture, rig = writer_creation, writer_creation.rig
    session, request, _command = await recovery_fixture(fixture, monkeypatch)
    connect = AsyncMock(side_effect=AssertionError("teardown owns native admission"))
    monkeypatch.setattr(rig.manager, "_connect_via_session_host", connect)
    ended = []

    async def ending(session_id, *, force):
        assert session_id == session.session_id and force is True
        with pytest.raises(AcquisitionRefused) as refused:
            await recovery.resume(
                rig.manager, session_id, request, fixture.requirement, lambda: True,
            )
        assert refused.value.code == "executor_live"
        ended.append(session_id)

    monkeypatch.setattr(rig.manager, "_end_session_owned", ending)
    await rig.manager.end_session(session.session_id, force=True)
    assert ended == [session.session_id]
    connect.assert_not_awaited()
    row = rig.db.execute_read("SELECT creation_recoveries_json FROM workspace_embodiments")[0]
    assert row["creation_recoveries_json"] is None
