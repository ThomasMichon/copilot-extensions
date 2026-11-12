"""Authenticated connector composition; not native installed-generation proof."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_mcp import backend, execution_context as ec, incoming, ipc
from agent_mcp.custody_connector import BackendConnector
from agent_mcp.custody_contract import CONTRACT, backend_descriptor
from agent_mcp.custody_session import CustodySession
from agent_mcp.custody_wire import MAX_FRAME
from agent_mcp.request_context import CONTEXT_VERSION
from .test_custody_session import Upstream, configuration, settled


@asynccontextmanager
async def endpoint(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    root.mkdir()
    selector = root / "current-version"
    selector.write_text("incoming", encoding="utf-8")
    handle = tmp_path / "serve.sock"
    cfg = configuration()
    fixture = SimpleNamespace(
        _token="fixture-private-token", _version="incoming", _runtime_root=root,
        _draining=False, _closing=False, _attached=0, _touch=lambda: None,
        _send=backend_send, requests=[], writers=[], tasks=[], roots=[],
        change=None, ack=None, hold=False, observed=asyncio.Event(),
        checkpoint_contract=backend_descriptor(),
    )
    monkeypatch.setattr(ipc, "_HAS_AF_UNIX", False)
    monkeypatch.setattr(incoming, "launch_generation", lambda: fixture._version)

    def load(bridge, *, repository_root):
        assert bridge == "fixture"
        fixture.roots.append(repository_root)
        return cfg

    monkeypatch.setattr(backend, "load_config", load)

    async def accept(reader, writer):
        fixture.writers.append(writer)
        fixture.tasks.append(asyncio.current_task())
        try:
            while line := await reader.readline():
                request = json.loads(line)
                fixture.requests.append(request)
                if request.get("token") != fixture._token:
                    await backend_send(writer, {"ok": False, "error": "unauthorized"})
                    continue
                if request["op"] == "ping":
                    reply = {
                        "ok": True, "pong": True, "ready": True, "draining": False,
                        "version": fixture._version, "runtime_root": str(root),
                        "fresh_admission": incoming.CONTRACT,
                        "custody_backend": fixture.checkpoint_contract,
                        "configuration_context": CONTEXT_VERSION,
                        "execution_context": ec.descriptor(),
                    }
                    if fixture.change:
                        fixture.change(reply)
                    await backend_send(writer, reply)
                    fixture.observed.set()
                    if fixture.hold:
                        await reader.read()
                        return
                elif fixture.ack is not None:
                    await backend_send(writer, fixture.ack)
                else:
                    await backend.run_backend(fixture, request, reader, writer)
                    return
        finally:
            await ipc.aclose_writer(writer)

    listener = await asyncio.start_server(accept, "127.0.0.1", 0, limit=MAX_FRAME)
    ipc._endpoint_path(handle).write_text(
        json.dumps({"port": listener.sockets[0].getsockname()[1], "token": fixture._token}),
        encoding="utf-8",
    )
    fixture.connector = BackendConnector(handle, "fixture", root, cfg, timeout=1)
    fixture.selector, fixture.handle, fixture.cfg = selector, handle, cfg
    try:
        yield fixture
    finally:
        listener.close()
        await listener.wait_closed()
        for writer in fixture.writers:
            await ipc.aclose_writer(writer)
        await asyncio.gather(*fixture.tasks)
        assert fixture._attached == 0


async def backend_send(writer, message):
    writer.write((json.dumps(message) + "\n").encode())
    await writer.drain()


async def test_connector_composes_with_real_backend_and_preserves_caller_root(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        upstream, output = Upstream(), []
        owner = CustodySession(fixture.cfg, output.append, transport=upstream)
        try:
            await owner.start(fixture.connector)
            assert owner.binding.trace_bookkeeping == 1
            await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            await settled(owner)
            await owner.handover(fixture.connector)
            await owner.submit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            await settled(owner)
            attachments = [item for item in fixture.requests if item["op"] == "backend"]
            assert [item["epoch"] for item in attachments] == [1, 2]
            assert all(item["generation"] == "incoming" for item in attachments)
            assert all(item["runtime_root"] == str(fixture._runtime_root) for item in attachments)
            assert fixture.roots and all(root != fixture._runtime_root for root in fixture.roots)
            assert all(str(root) == fixture.connector.context["root"] for root in fixture.roots)
            assert upstream.starts == 1 and upstream.closes == 0
            assert [item["id"] for item in output] == [1, 2]
            assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        finally:
            await owner.close()


async def test_legacy_binding_is_not_upgraded_when_a_capable_backend_arrives(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        fixture.checkpoint_contract = CONTRACT
        upstream = Upstream()
        owner = CustodySession(fixture.cfg, lambda _message: None, transport=upstream)
        try:
            await owner.start(fixture.connector)
            binding = owner.binding
            assert binding.trace_bookkeeping == 0
            fixture.checkpoint_contract = backend_descriptor()
            await owner.handover(fixture.connector)
            assert fixture.connector.checkpoint_trace_bookkeeping == 1
            assert owner.binding is binding and upstream.starts == 1
        finally:
            await owner.close()


async def test_unsupported_rollback_retains_the_original_epoch_and_binding(tmp_path, monkeypatch):
    from agent_mcp.checkpoint import CheckpointRefused

    async with endpoint(tmp_path, monkeypatch) as fixture:
        upstream, output = Upstream(), []
        owner = CustodySession(fixture.cfg, output.append, transport=upstream)
        try:
            await owner.start(fixture.connector)
            binding, original = owner.binding, owner.state.current
            await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            await settled(owner)
            fixture.checkpoint_contract = CONTRACT
            with pytest.raises(CheckpointRefused, match="cannot import"):
                await owner.handover(fixture.connector)
            assert owner.binding is binding and owner.state.current is original
            assert not owner.state.fenced and not owner.state.uncertain
            assert owner.state.candidate is None and len(owner._links) == 1
            await owner.submit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            await settled(owner)
            assert [item["id"] for item in output] == [1, 2]
            assert upstream.starts == 1 and upstream.closes == 0
            assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        finally:
            await owner.close()


@pytest.mark.parametrize("declaration", ["ping", "ack", "both"])
async def test_binding_support_requires_both_authenticated_backend_declarations(
    tmp_path, monkeypatch, declaration,
):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        if declaration == "ack":
            fixture.checkpoint_contract = CONTRACT
        fixture.ack = {
            "ok": True, "generation": "incoming",
            "runtime_root": str(fixture._runtime_root), "epoch": 1,
            "contract": CONTRACT if declaration == "ping" else backend_descriptor(),
        }
        _, writer = await fixture.connector(1)
        await ipc.aclose_writer(writer)
        assert fixture.connector.checkpoint_trace_bookkeeping == int(declaration == "both")


async def test_connector_does_not_consult_stale_keepers_physical_generation(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        # A legacy keeper has no local launch metadata; only the backend owns it.
        monkeypatch.setattr(incoming, "launch_generation", lambda: None)
        fixture.ack = {
            "ok": True, "contract": CONTRACT, "generation": "incoming",
            "runtime_root": str(fixture._runtime_root), "epoch": 7,
        }
        reader, writer = await fixture.connector(7)
        await ipc.aclose_writer(writer)
        assert not reader.exception()
        assert [item["op"] for item in fixture.requests] == ["ping", "backend"]


@pytest.mark.parametrize("case", ["legacy", "foreign", "stale", "key", "draining"])
async def test_unqualified_peer_never_receives_backend_binding(tmp_path, monkeypatch, case):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        def change(reply):
            if case == "legacy":
                reply.pop("custody_backend")
            elif case == "foreign":
                reply["runtime_root"] = str(tmp_path)
            elif case == "stale":
                fixture.selector.write_text("replacement", encoding="utf-8")
            elif case == "key":
                reply["execution_context"] = {**ec.descriptor(), "key": "z" * 64}
            else:
                reply["draining"] = True
        fixture.change = change
        with pytest.raises(OSError):
            await fixture.connector(1)
        assert [item["op"] for item in fixture.requests] == ["ping"]


async def test_missing_endpoint_token_is_refused_before_any_request(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        sidecar = ipc._endpoint_path(fixture.handle)
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        record.pop("token")
        sidecar.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(incoming.AdmissionRefused, match="authentication"):
            await fixture.connector(1)
        assert not fixture.requests


async def test_authentication_refusal_is_not_backend_admission(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        fixture._token = "different-owner"
        with pytest.raises(incoming.AdmissionRefused, match="qualify"):
            await fixture.connector(1)
        assert [item["op"] for item in fixture.requests] == ["ping"]


async def test_context_drift_refuses_before_connecting(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        monkeypatch.setenv("CUSTODY_CONNECTOR_FIXTURE", "changed")
        with pytest.raises(ec.SharingUnavailable, match="context changed"):
            await fixture.connector(1)
        assert not fixture.requests


async def test_backend_checks_equivalence_before_preparing_upstream(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        def different_key(reply):
            reply["execution_context"] = {**ec.descriptor(), "key": "a" * 64}
        fixture.change = different_key
        with pytest.raises(incoming.AdmissionRefused, match="not confirmed"):
            await fixture.connector(1)
        assert fixture.roots and fixture._attached == 0


@pytest.mark.parametrize("case", ["malformed", "epoch", "selection", "endpoint"])
async def test_uncertain_or_stale_ack_never_returns_streams(tmp_path, monkeypatch, case):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        ack = {
            "ok": True, "contract": deepcopy(CONTRACT), "generation": "incoming",
            "runtime_root": str(fixture._runtime_root), "epoch": 1,
        }
        if case == "epoch":
            ack["epoch"] = True
        elif case == "malformed":
            ack = {}
        else:
            original = fixture._send

            async def changed(writer, message):
                await original(writer, message)
                if case == "selection":
                    fixture.selector.write_text("replacement", encoding="utf-8")
                else:
                    sidecar = ipc._endpoint_path(fixture.handle)
                    sidecar.write_text(sidecar.read_text(encoding="utf-8") + " ", encoding="utf-8")
            fixture._send = changed
        if case not in {"selection", "endpoint"}:
            fixture.ack = ack
        with pytest.raises(OSError):
            await fixture.connector(1)
        assert [item["op"] for item in fixture.requests] == ["ping", "backend"]


async def test_cancelled_handshake_discards_only_uncommitted_epoch(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        fixture.hold = True
        owner = CustodySession(fixture.cfg, lambda _message: None, transport=Upstream())
        start = asyncio.create_task(owner.start(fixture.connector))
        await fixture.observed.wait()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        assert owner.state.candidate is None and owner.state.current is None
        assert not owner.state.uncertain and owner.transport.starts == 0
        await owner.close()


async def test_handshake_timeout_never_prepares_or_starts_retained_transport(tmp_path, monkeypatch):
    async with endpoint(tmp_path, monkeypatch) as fixture:
        fixture.hold = True
        owner = CustodySession(fixture.cfg, lambda _message: None, transport=Upstream())
        with pytest.raises(asyncio.TimeoutError):
            await owner.start(fixture.connector)
        assert owner.state.candidate is None and owner.state.current is None
        assert owner.transport.starts == 0
        await owner.close()
