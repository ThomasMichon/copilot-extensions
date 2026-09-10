"""Initial preparation and post-attach reconnect have distinct bounded lifetimes."""

import asyncio
from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import native_terminal as terminal
from agent_bridge.client import BridgeClientError
from agent_bridge.native_store import NativeError

pytestmark = pytest.mark.filterwarnings("error:coroutine .* was never awaited:RuntimeWarning")


@pytest.fixture
def rig(monkeypatch):
    source = SimpleNamespace(
        detached=asyncio.Event(), queue=asyncio.Queue(), connected=False,
        has_attached=False, last_disconnect=0.0,
    )
    state = SimpleNamespace(
        now=0.0, step=1.0, source=source, calls=[], connections=[],
        status=lambda: {"state": "starting"}, restored=False, on_sleep=lambda: None,
    )

    def status(execution_id, *, generation):
        state.calls.append((execution_id, generation))
        return {
            "executionId": execution_id, "generation": generation,
            **state.status(),
        }

    client = SimpleNamespace(native_status=status)

    def get_client(*, ensure):
        assert ensure is False  # observing/presenting never restarts the owner
        return client

    async def sleep(_seconds):
        state.now += state.step
        state.on_sleep()
        await asyncio.sleep(0)

    @contextmanager
    def raw():
        try:
            yield
        finally:
            state.restored = True

    async def connect(client, execution_id, generation, source, *, handshake_timeout):
        state.connections.append((execution_id, generation, handshake_timeout))
        source.has_attached = True
        return 7

    proxy = {name: getattr(asyncio, name) for name in (
        "to_thread", "create_task", "wait", "gather", "FIRST_COMPLETED",
    )}
    monkeypatch.setattr(terminal, "asyncio", SimpleNamespace(**proxy, sleep=sleep))
    monkeypatch.setattr(terminal, "time", SimpleNamespace(monotonic=lambda: state.now))
    monkeypatch.setattr(terminal, "Input", lambda: source)
    monkeypatch.setattr(terminal, "raw_terminal", raw)
    monkeypatch.setattr(terminal, "connection", connect)
    monkeypatch.setattr(cli, "_get_client", get_client)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_initial_preparation_can_exceed_120_seconds_without_replacement(rig, unavailable):
    rig.step = 61

    def status():
        if unavailable and rig.now < 180:
            raise BridgeClientError(503, "owner preparing")
        return {"state": "starting" if rig.now < 180 else "unrepresented"}

    rig.status = status
    assert await terminal.attach("execution", "generation") == 7
    assert rig.now == 183
    assert rig.connections == [("execution", "generation", 1617)]
    assert set(rig.calls) == {("execution", "generation")}
    assert rig.restored


@pytest.mark.asyncio
async def test_registration_terminal_is_available_before_session_is_represented(rig):
    receipt = {
        "state": "starting", "phase": "registration",
        "sessionId": None, "represented": False, "ready": False,
    }
    rig.status = lambda: receipt
    assert await terminal.attach("execution", "generation") == 7
    assert rig.connections == [("execution", "generation", 1800)]
    assert receipt == {
        "state": "starting", "phase": "registration",
        "sessionId": None, "represented": False, "ready": False,
    }
    assert rig.restored


@pytest.mark.asyncio
async def test_registration_still_requires_authenticated_host_handshake(rig, monkeypatch):
    rig.status = lambda: {"state": "starting", "phase": "registration"}
    rig.step = 600

    async def connect(*args, **kwargs):
        raise NativeError("not_ready", "native activation is not verified", 503)

    monkeypatch.setattr(terminal, "connection", connect)
    with pytest.raises(NativeError, match="preparation budget exhausted"):
        await terminal.attach("execution", "generation")
    assert not rig.source.has_attached and rig.restored


@pytest.mark.asyncio
async def test_initial_preparation_has_a_fixed_1800_second_window(rig):
    rig.step = 600
    with pytest.raises(NativeError, match="preparation budget exhausted") as exc:
        await terminal.attach("execution", "generation")
    assert exc.value.code == "preparation_timeout"
    assert rig.now == 1800
    assert rig.connections == []
    assert rig.restored


@pytest.mark.asyncio
async def test_initial_handshake_not_ready_uses_preparation_window(rig, monkeypatch):
    rig.step = 61
    rig.status = lambda: {"state": "unrepresented"}

    async def connect(client, execution_id, generation, source, *, handshake_timeout):
        if rig.now < 180:
            raise NativeError("not_ready", "native host preparing", 503)
        source.has_attached = True
        return 11

    monkeypatch.setattr(terminal, "connection", connect)
    assert await terminal.attach("execution", "generation") == 11
    assert rig.now == 183 and rig.restored
    assert set(rig.calls) == {("execution", "generation")}


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, 0, 23])
async def test_stopped_receipt_returns_owned_exit_code(rig, code):
    rig.status = lambda: {"state": "stopped", "exitCode": code}
    assert await terminal.attach("execution", "generation") == (code or 0)
    assert rig.now == 0 and rig.connections == [] and rig.restored


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [
    ({"state": "rejected"}, "venue_busy"),
    ({"state": "unexpected"}, "invalid_status"),
    ({"state": "ready", "generation": "different"}, "identity_mismatch"),
])
async def test_terminal_rejection_or_identity_failure_never_retries(rig, status, error):
    rig.status = lambda: status
    with pytest.raises(NativeError) as exc:
        await terminal.attach("execution", "generation")
    assert exc.value.code == error
    assert len(rig.calls) == 1 and not rig.connections and rig.restored


@pytest.mark.asyncio
async def test_authoritative_client_rejection_is_terminal(rig):
    def rejected():
        raise BridgeClientError(409, "generation mismatch")

    rig.status = rejected
    with pytest.raises(BridgeClientError):
        await terminal.attach("execution", "generation")
    assert len(rig.calls) == 1 and rig.restored


@pytest.mark.asyncio
async def test_unexpected_local_failure_is_not_retried(rig):
    def broken():
        raise RuntimeError("invalid local configuration")

    rig.status = broken
    with pytest.raises(RuntimeError, match="invalid local configuration"):
        await terminal.attach("execution", "generation")
    assert len(rig.calls) == 1 and rig.restored


@pytest.mark.asyncio
async def test_preparation_detach_does_not_stop_or_replace_execution(rig):
    rig.on_sleep = rig.source.detached.set
    assert await terminal.attach("execution", "generation") == 0
    assert len(rig.calls) == 1 and not rig.connections and rig.restored


@pytest.mark.asyncio
async def test_stopping_waits_for_verified_stopped_receipt(rig):
    rig.status = lambda: {"state": "stopping" if rig.now < 2 else "stopped", "exitCode": 17}
    assert await terminal.attach("execution", "generation") == 17
    assert not rig.connections and rig.restored


@pytest.mark.asyncio
async def test_true_reconnect_exhausts_120_seconds_after_first_attachment(rig, monkeypatch):
    rig.status = lambda: {"state": "ready"}
    rig.step = 30
    attempted = []

    async def connect(client, execution_id, generation, source, *, handshake_timeout):
        attempted.append((execution_id, generation, handshake_timeout))
        if len(attempted) == 1:
            source.has_attached = True
            rig.now = 3600  # an attached terminal is not limited by preparation
            source.last_disconnect = rig.now
            return None
        raise OSError("transport unavailable")

    monkeypatch.setattr(terminal, "connection", connect)
    with pytest.raises(NativeError, match="reconnect budget exhausted") as exc:
        await terminal.attach("execution", "generation")
    assert exc.value.code == "unreachable"
    assert rig.now == 3720
    assert set((a, b) for a, b, _ in attempted) == {("execution", "generation")}
    assert all(budget <= 120 for _, _, budget in attempted[1:])
    assert rig.restored


@pytest.mark.asyncio
async def test_recovery_status_cannot_reset_an_existing_reconnect_budget(rig, monkeypatch):
    rig.status = lambda: {"state": "starting" if rig.source.has_attached else "ready"}
    rig.step = 40

    async def connect(client, execution_id, generation, source, *, handshake_timeout):
        source.has_attached = True
        source.last_disconnect = rig.now
        return None

    monkeypatch.setattr(terminal, "connection", connect)
    with pytest.raises(NativeError, match="reconnect budget exhausted"):
        await terminal.attach("execution", "generation")
    assert rig.now == 120


@pytest.mark.asyncio
async def test_task_cancellation_restores_terminal_and_preserves_identity(rig, monkeypatch):
    entered = asyncio.Event()
    rig.status = lambda: {"state": "unrepresented"}

    async def connect(*a, **k):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(terminal, "connection", connect)
    task = asyncio.create_task(terminal.attach("execution", "generation"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.restored
    assert set(rig.calls) == {("execution", "generation")}


@pytest.mark.asyncio
async def test_detach_interrupts_pending_observation_without_waiting_for_deadline():
    source = SimpleNamespace(detached=asyncio.Event())
    blocked = asyncio.Event()
    task = asyncio.create_task(terminal._until_detach(blocked.wait(), source, 1800))
    await asyncio.sleep(0)
    source.detached.set()
    assert await asyncio.wait_for(task, 1) is terminal._DETACHED


@pytest.mark.asyncio
async def test_detach_restores_terminal_during_blocked_owner_observation(rig, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_client(*, ensure):
        assert ensure is False
        entered.set()
        release.wait(5)
        return SimpleNamespace()

    monkeypatch.setattr(cli, "_get_client", blocked_client)
    task = asyncio.create_task(terminal.attach("execution", "generation"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        rig.source.detached.set()
        assert await asyncio.wait_for(task, 1) == 0
        assert rig.restored and not rig.calls and not rig.connections
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
@pytest.mark.asyncio
async def test_invalid_budgets_are_rejected(value):
    with pytest.raises(ValueError, match="positive and finite"):
        await terminal.attach("execution", "generation", preparation_timeout=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("accept", [False, True])
async def test_real_connection_bounds_handshake_but_preserves_accepted_terminal(accept):
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, Request, TextMessage

    finished = asyncio.Event()

    async def server(reader, writer):
        try:
            wire = WSConnection(ConnectionType.SERVER)
            while data := await reader.read(65536):
                wire.receive_data(data)
                for event in wire.events():
                    if isinstance(event, Request) and accept:
                        writer.write(wire.send(AcceptConnection(subprotocol="native.v1")))
                        await writer.drain()
                        await asyncio.sleep(1.1)  # longer than the handshake budget
                        writer.write(wire.send(TextMessage(data=json.dumps({"type": "exit", "exitCode": 19}))))
                        await writer.drain()
                        return
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    listener = await asyncio.start_server(server, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    source = SimpleNamespace(
        detached=asyncio.Event(), queue=asyncio.Queue(), connected=False,
        has_attached=False, last_disconnect=0.0,
    )
    client = SimpleNamespace(_base=f"http://127.0.0.1:{port}", _token="synthetic")
    try:
        if accept:
            assert await terminal.connection(client, "execution", "generation", source, handshake_timeout=1) == 19
            assert source.has_attached and source.last_disconnect > 0
        else:
            with pytest.raises(TimeoutError):
                await terminal.connection(client, "execution", "generation", source, handshake_timeout=1)
            assert not source.has_attached
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        listener.close()
        await listener.wait_closed()
