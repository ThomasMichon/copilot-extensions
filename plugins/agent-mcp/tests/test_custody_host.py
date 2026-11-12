"""Shared provider composition, not native physical-generation qualification."""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import Mapping
import contextvars
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_mcp import backend, custody_conduit, custody_host, custody_session, execution_context as ec, incoming, ipc, owner_entry, serve
from agent_mcp.custody_contract import CONTRACT, host_descriptor, supports
from agent_mcp.custody_host import CustodyHost
from agent_mcp.custody_wire import MAX_FRAME
from agent_mcp.request_context import capture_context
from agent_mcp.serve import Server
from .test_custody_session import Upstream, configuration, settled


async def send(writer, message):
    writer.write((json.dumps(message) + "\n").encode())
    await writer.drain()


async def receive(reader):
    return json.loads(await asyncio.wait_for(reader.readline(), 2))


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 2)


@asynccontextmanager
async def shared(
    tmp_path, monkeypatch, *, early=False, legacy_generations=(), legacy_custodian=False,
):
    root = tmp_path / "installation"
    root.mkdir()
    selector = root / "current-version"
    selector.write_text("outgoing", encoding="utf-8")
    handle = tmp_path / "backend" / "serve.sock"
    handle.parent.mkdir()
    host = CustodyHost(tmp_path / "keeper" / "custody.sock", handle, root, enable_lease=False)
    host._token = "private-custody-token"
    cfg = configuration()
    physical = contextvars.ContextVar("fixture_backend", default=None)
    state = SimpleNamespace(
        host=host, root=root, handle=handle, selector=selector, cfg=cfg,
        backends=[], upstreams=[], listeners=[], tasks=[], writers=[], pipelines=[],
        requests=[], refuse=False, refuse_prepare=False, context_modes=[],
    )
    monkeypatch.setattr(ipc, "_HAS_AF_UNIX", False)
    monkeypatch.setattr(incoming, "launch_generation", physical.get)
    if legacy_generations or legacy_custodian:
        describe = ec.descriptor
        fields = ec.fields_for_peer
        configure = ec.load_config

        def descriptor():
            result = describe()
            if physical.get() in legacy_generations or (
                physical.get() is None and legacy_custodian
            ):
                result.pop("trace_bookkeeping", None)
            return result

        def legacy_caller_fields(cfg, reply):
            if physical.get() is None and legacy_custodian:
                reply = {**reply, "execution_context": dict(reply["execution_context"])}
                reply["execution_context"].pop("trace_bookkeeping", None)
            return fields(cfg, reply)

        def configured(bridge, request, loader, *, repository_root):
            generation = physical.get()
            if generation is not None:
                mode = request.get("execution_context", {}).get("trace_bookkeeping", 0)
                state.context_modes.append((generation, mode))
                if generation in legacy_generations:
                    assert mode == 0, "legacy receivers must get legacy digests"
            return configure(bridge, request, loader, repository_root=repository_root)

        monkeypatch.setattr(ec, "descriptor", descriptor)
        monkeypatch.setattr(ec, "fields_for_peer", legacy_caller_fields)
        monkeypatch.setattr(ec, "load_config", configured)

    def load(bridge, *, repository_root):
        assert bridge == "fixture"
        assert str(repository_root) == capture_context(bridge)["root"]
        if state.refuse and physical.get() == "incoming":
            raise ec.SharingUnavailable("fixture preparation refusal")
        return cfg

    monkeypatch.setattr(custody_host, "load_config", load)
    monkeypatch.setattr(custody_conduit, "load_config", load)
    monkeypatch.setattr(backend, "load_config", load)
    original_prepare = backend.BackendSession._prepare

    async def prepare(instance, data):
        if state.refuse_prepare and physical.get() == "incoming":
            raise ec.SharingUnavailable("fixture checkpoint preparation refusal")
        result = await original_prepare(instance, data)
        state.pipelines.append((physical.get(), instance.session._pipeline))
        return result
    monkeypatch.setattr(backend.BackendSession, "_prepare", prepare)

    def transport(*_args):
        upstream = Upstream()
        if early:
            original = upstream.start

            async def start():
                await original()
                await upstream._emit_message({"jsonrpc": "2.0", "method": "notifications/ready"})
            upstream.start = start
        state.upstreams.append(upstream)
        return upstream
    monkeypatch.setattr(custody_session, "build_transport", transport)

    async def listen(server, generation):
        async def accept(reader, writer):
            state.tasks.append(asyncio.current_task())
            state.writers.append((server, writer))
            token = physical.set(generation)
            try:
                await server._handle(reader, writer)
            finally:
                physical.reset(token)
        listener = await asyncio.start_server(accept, "127.0.0.1", 0, limit=MAX_FRAME)
        server._server = listener
        state.listeners.append(listener)
        return listener

    async def publish(generation):
        server = Server(handle, enable_lease=False, version=generation)
        server._runtime_root = root
        server._token = f"private-{generation}-token"
        listener = await listen(server, generation)
        ipc._endpoint_path(handle).write_text(
            json.dumps({"port": listener.sockets[0].getsockname()[1], "token": server._token}),
            encoding="utf-8",
        )
        selector.write_text(generation, encoding="utf-8")
        state.backends.append(server)
        return server
    state.publish = publish
    await publish("outgoing")
    await listen(host, None)
    host.socket_path.parent.mkdir()
    ipc._endpoint_path(host.socket_path).write_text(
        json.dumps({"port": host._server.sockets[0].getsockname()[1], "token": host._token}),
        encoding="utf-8",
    )

    async def attach(change=None):
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", host._server.sockets[0].getsockname()[1], limit=MAX_FRAME,
        )
        await send(writer, {"op": "ping", "token": host._token})
        ping = await receive(reader)
        request = {
            "op": "attach", "token": host._token, "bridge": "fixture",
            "custody": CONTRACT, "installation_root": str(root),
            "configuration_context": capture_context("fixture"),
            **ec.fields_for_peer(cfg, ping),
        }
        if change:
            change(request)
        await send(writer, request)
        return reader, writer, await receive(reader), ping
    state.attach = attach
    try:
        yield state
    finally:
        for upstream in state.upstreams:
            if not upstream.closes:
                for mid in tuple(upstream.pending):
                    await upstream.complete(mid)
        for _server, writer in tuple(state.writers):
            await ipc.aclose_writer(writer)
        for listener in state.listeners:
            listener.close()
            await listener.wait_closed()
        await asyncio.wait_for(asyncio.gather(*state.tasks), 3)
        assert host.attached == 0 and not host.sessions
        assert all(server.attached == 0 for server in state.backends)
        assert all(upstream.closes == 1 for upstream in state.upstreams)


async def test_shared_host_moves_settled_session_without_upstream_reinitialize(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, ack, ping = await fixture.attach()
        assert ack["attached"] is True
        assert "fresh_admission" not in ping and "custody_backend" not in ping
        assert fixture.host._runtime_root is None
        await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        assert (await receive(reader))["id"] == 0
        await send(writer, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert (await receive(reader))["result"]["tools"][0]["name"] == "test_echo"
        entry = next(iter(fixture.host.sessions))
        await settled(entry.owner)
        old = entry.owner.state.current
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await asyncio.wait_for(entry.migration, 2)
        assert entry.owner.state.current is not old and entry.connector.generation == "incoming"
        assert fixture.pipelines[0][0] == "outgoing" and fixture.pipelines[1][0] == "incoming"
        assert fixture.pipelines[0][1] is not fixture.pipelines[1][1]
        await send(writer, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert (await receive(reader))["id"] == 2
        upstream = fixture.upstreams[0]
        assert upstream.starts == 1 and upstream.closes == 0
        assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


@pytest.mark.parametrize("capable", [False, True])
async def test_required_trace_mode_uses_actual_backend_before_preparation(tmp_path, monkeypatch, capable):
    async with shared(tmp_path, monkeypatch) as fixture:
        if not capable:
            monkeypatch.setattr(backend, "backend_descriptor", lambda: CONTRACT)
        reader, writer, ack, ping = await fixture.attach(
            lambda request: request.update(checkpoint_trace_bookkeeping=1),
        )
        try:
            assert ping["custody_host"] == host_descriptor()
            if capable:
                assert ack["attached"] is True
                assert ack["checkpoint_trace_bookkeeping"] == 1
                assert ack["custody"] == host_descriptor()
                owner = next(iter(fixture.host.sessions)).owner
                assert owner.binding.trace_bookkeeping == 1
                await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
                assert (await receive(reader))["id"] == 0
                assert len(fixture.upstreams) == 1 and fixture.upstreams[0].starts == 1
            else:
                assert ack["ok"] is False
                assert ack["shared_execution"] is False
                assert "required checkpoint identity mode" in ack["error"]
                assert not fixture.pipelines and not fixture.upstreams
        finally:
            await ipc.aclose_writer(writer)


@pytest.mark.parametrize("capable", [False, True])
async def test_optional_receiver_probes_actual_keeper_before_any_application_start(
    tmp_path, monkeypatch, capable,
):
    async with shared(tmp_path, monkeypatch) as fixture:
        from agent_mcp import config, sockio
        monkeypatch.setattr(owner_entry.sys, "platform", "linux")
        monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
        monkeypatch.setenv("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", "new-launch")
        for key in ("AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(config, "load_config", lambda *_args, **_kwargs: fixture.cfg)
        monkeypatch.setattr(incoming, "require_selected", lambda: (
            "outgoing", fixture.root / "versions" / "outgoing", fixture.root,
        ))
        monkeypatch.setattr(sockio, "default_home_dir", lambda: tmp_path)
        # The backend remains capable; only the separately running keeper varies.
        if not capable:
            monkeypatch.setattr(custody_host, "host_descriptor", lambda: CONTRACT)
        request = {
            "receiver": {
                "python": str(Path(sys.executable).absolute()),
                "prefix": str(Path(sys.prefix).resolve()),
                "module": str(Path(owner_entry.__file__).resolve()), "cwd": os.getcwd(),
            },
            "selection": {
                "generation": "outgoing", "root": str(fixture.root.resolve()),
                "prefix": str(Path(sys.prefix).resolve()), "python": str(Path(sys.executable).resolve()),
            },
            "arguments": ["forward", "fixture", "--socket", str(fixture.handle)],
            "pid": 42, "birth": "fixture",
        }
        monkeypatch.setattr(
            "agent_mcp.custody_launch.probe_conduit",
            lambda _handle, root, **kwargs: custody_conduit.probe(
                fixture.host.socket_path, root, **kwargs,
            ),
        )
        if capable:
            args = await asyncio.to_thread(owner_entry._qualify, request)
            assert args.checkpoint_trace_bookkeeping == 1
        else:
            with pytest.raises(ec.SharingUnavailable, match="cannot enforce"):
                await asyncio.to_thread(owner_entry._qualify, request)
        assert not fixture.host.sessions and fixture.host.attached == 0
        assert not fixture.pipelines and not fixture.upstreams


@pytest.mark.parametrize("mode", [True, -1, 2, "1", None])
async def test_invalid_required_mode_refuses_before_configuration(tmp_path, monkeypatch, mode):
    async with shared(tmp_path, monkeypatch) as fixture:
        def unexpected(_request):
            pytest.fail("invalid checkpoint mode reached configuration loading")
        monkeypatch.setattr(fixture.host, "_configuration", unexpected)
        _, writer, ack, _ = await fixture.attach(
            lambda request: request.update(checkpoint_trace_bookkeeping=mode),
        )
        try:
            assert ack["ok"] is False and ack["shared_execution"] is False
            assert "identity mode is invalid" in ack["error"]
            assert not fixture.pipelines and not fixture.upstreams
        finally:
            await ipc.aclose_writer(writer)


async def test_legacy_attach_keeps_baseline_with_legacy_backend(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        monkeypatch.setattr(backend, "backend_descriptor", lambda: CONTRACT)
        reader, writer, ack, _ = await fixture.attach()
        try:
            assert ack == {"ok": True, "attached": True, "custody": CONTRACT}
            assert next(iter(fixture.host.sessions)).owner.binding.trace_bookkeeping == 0
            await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
            assert (await receive(reader))["id"] == 0
        finally:
            await ipc.aclose_writer(writer)


@pytest.mark.parametrize("referenced", [False, True], ids=["bookkeeping", "explicit-config-use"])
async def test_two_launch_trace_timestamps_against_one_shared_host(tmp_path, monkeypatch, referenced):
    initial = {
        **os.environ, "COPILOT_EXTENSIONS_BOOT_TRACE": "1",
        "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN": "agent-mcp",
        "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS": "1000",
    }
    process_environment = contextvars.ContextVar("fixture_process_environment", default=initial)

    class Environment(Mapping):
        def __getitem__(self, key):
            return process_environment.get()[key]

        def __iter__(self):
            return iter(process_environment.get())

        def __len__(self):
            return len(process_environment.get())

    monkeypatch.setattr(ec, "os", SimpleNamespace(
        name=os.name, environ=Environment(), getcwd=os.getcwd,
    ))
    async with shared(tmp_path, monkeypatch) as fixture:
        if referenced:
            fixture.cfg.server.env["EXPLICIT_TRACE"] = "${COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS}"
        configure = fixture.host._configuration

        def configured_in_host(request):
            # Listener tasks retain the first process environment; only the caller advances.
            assert process_environment.get()["COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS"] == "1000"
            return configure(request)
        monkeypatch.setattr(fixture.host, "_configuration", configured_in_host)
        writers = []
        try:
            first, writer1, ack1, _ = await fixture.attach()
            writers.append(writer1)
            assert ack1["attached"] is True
            await send(writer1, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
            assert (await receive(first))["id"] == 0
            first_entry = next(iter(fixture.host.sessions))
            await settled(first_entry.owner)
            process_environment.set({
                **initial, "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS": "2000",
            })
            assert ec.inherited_environment(fixture.cfg)[
                "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS"
            ] == "2000"
            second, writer2, ack2, _ = await fixture.attach()
            writers.append(writer2)
            if referenced:
                assert ack2["ok"] is False and ack2["shared_execution"] is False
                assert len(fixture.upstreams) == 1
            else:
                assert ack2["attached"] is True
                await send(writer2, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                assert (await receive(second))["id"] == 1
                assert len(fixture.host.sessions) == 2 and len(fixture.upstreams) == 2
                await settled(next(
                    entry for entry in fixture.host.sessions if entry is not first_entry
                ).owner)
        finally:
            for writer in writers:
                await ipc.aclose_writer(writer)


async def test_shared_host_adopts_same_slot_backend_replacement(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        entry = next(iter(fixture.host.sessions))
        old = entry.owner.state.current
        old_server = fixture.backends[0]
        await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        assert (await receive(reader))["id"] == 0
        await settled(entry.owner)
        replacement = await fixture.publish("outgoing")
        fixture.host.inspect_sessions()
        await until(lambda: entry.migration is not None)
        await asyncio.wait_for(entry.migration, 2)
        await until(lambda: old_server.attached == 0)
        assert entry.owner.state.current is not old
        assert replacement.attached == 1 and entry.generation == "outgoing"
        adopted = entry.migration
        fixture.host.inspect_sessions()
        assert entry.migration is adopted
        await send(writer, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert (await receive(reader))["id"] == 1
        upstream = fixture.upstreams[0]
        assert upstream.starts == 1
        assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


@pytest.mark.parametrize("legacy_custodian", [False, True], ids=["new-keeper", "legacy-keeper"])
async def test_trace_context_update_and_rollback_preserve_the_same_session(
    tmp_path, monkeypatch, legacy_custodian,
):
    monkeypatch.setenv("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", "retained-launch")
    async with shared(
        tmp_path, monkeypatch, legacy_generations={"outgoing"},
        legacy_custodian=legacy_custodian,
    ) as fixture:
        reader, writer, ack, _ = await fixture.attach()
        assert ack["attached"] is True
        entry = next(iter(fixture.host.sessions))
        await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        assert (await receive(reader))["id"] == 0
        await settled(entry.owner)
        for generation in ("incoming", "outgoing"):
            await fixture.publish(generation)
            fixture.host.inspect_sessions()
            await asyncio.wait_for(entry.migration, 2)
            assert entry.connector.generation == generation
            assert not entry.owner.state.uncertain
            await send(writer, {"jsonrpc": "2.0", "id": generation, "method": "tools/list"})
            assert (await receive(reader))["id"] == generation
            await settled(entry.owner)
        modes = {
            generation: {mode for selected, mode in fixture.context_modes if selected == generation}
            for generation in ("outgoing", "incoming")
        }
        assert modes == {"outgoing": {0}, "incoming": {0 if legacy_custodian else 1}}
        upstream = fixture.upstreams[0]
        assert len(fixture.upstreams) == 1 and upstream.starts == 1 and upstream.closes == 0
        assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


async def test_one_host_migrates_other_client_while_first_mutation_is_pending(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        first, writer1, _, _ = await fixture.attach()
        second, writer2, _, _ = await fixture.attach()
        entries = list(fixture.host.sessions)
        blocked = next(entry for entry in entries if entry.owner.transport is fixture.upstreams[0])
        free = next(entry for entry in entries if entry is not blocked)
        await send(writer1, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "test_echo", "arguments": {}},
        })
        await until(lambda: fixture.upstreams[0].pending)
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await until(lambda: blocked.owner.state.fenced)
        await asyncio.wait_for(free.migration, 2)
        assert not blocked.migration.done() and fixture.host.attached == 2
        await send(writer2, {"jsonrpc": "2.0", "id": 20, "method": "tools/list"})
        assert (await receive(second))["id"] == 20
        await fixture.upstreams[0]._emit_message(
            {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"},
        )
        assert (await receive(first))["id"] == "server-1"
        await send(writer1, {"jsonrpc": "2.0", "id": "server-1", "result": {}})
        await until(lambda: any(
            item.get("id") == "server-1" and "result" in item
            for item in fixture.upstreams[0].messages
        ))
        writer1.write_eof()
        await until(lambda: blocked.closing)
        assert not blocked.migration.done()
        await fixture.upstreams[0].complete(10)
        assert (await receive(first))["id"] == 10
        assert await asyncio.wait_for(first.read(), 2) == b""
        assert blocked.connector.generation == "incoming"
        assert fixture.upstreams[0].starts == 1
        assert fixture.upstreams[1].closes == 0
        writer2.write_eof()
        assert await asyncio.wait_for(second.read(), 2) == b""
        await ipc.aclose_writer(writer1)
        await ipc.aclose_writer(writer2)


@pytest.mark.parametrize("request_timing", ["before-eof", "after-eof"])
async def test_eof_with_unanswerable_server_request_aborts_only_its_owned_session(
    tmp_path, monkeypatch, request_timing,
):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        other, other_writer, _, _ = await fixture.attach()
        upstream = fixture.upstreams[0]
        entry = next(item for item in fixture.host.sessions if item.owner.transport is upstream)
        await send(writer, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "test_echo", "arguments": {}},
        })
        await until(lambda: upstream.pending)
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await until(lambda: entry.owner.state.fenced)
        request = {"jsonrpc": "2.0", "id": "unanswered", "method": "sampling/createMessage"}
        if request_timing == "before-eof":
            await upstream._emit_message(request)
            assert (await receive(reader))["id"] == "unanswered"
        await send(writer, {"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        await asyncio.sleep(0)
        writer.write_eof()
        if request_timing == "after-eof":
            await until(lambda: entry.owner._input_ended)
            with pytest.raises(ConnectionError, match="input is closed"):
                await upstream._emit_message(request)
        await until(lambda: entry not in fixture.host.sessions)
        assert entry.owner.state.uncertain and entry.migration.done()
        assert upstream.starts == upstream.ends == upstream.closes == 1
        assert list(upstream.pending) == [10]
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await send(other_writer, {"jsonrpc": "2.0", "id": 20, "method": "tools/list"})
        assert (await receive(other))["id"] == 20
        other_writer.write_eof()
        assert await asyncio.wait_for(other.read(), 2) == b""
        await ipc.aclose_writer(writer)
        await ipc.aclose_writer(other_writer)


async def test_eof_preserves_queued_server_reply_and_fenced_application(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        upstream = fixture.upstreams[0]
        entry = next(iter(fixture.host.sessions))
        await send(writer, {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "test_echo", "arguments": {}},
        })
        await until(lambda: upstream.pending)
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await until(lambda: entry.owner.state.fenced)
        await upstream._emit_message(
            {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"},
        )
        assert (await receive(reader))["id"] == "server-1"
        original = entry.owner.submit
        release = asyncio.Event()

        async def submit(message, *, wait):
            if "method" not in message:
                await release.wait()
            return await original(message, wait=wait)

        monkeypatch.setattr(entry.owner, "submit", submit)
        try:
            await send(writer, {"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
            await send(writer, {"jsonrpc": "2.0", "id": "server-1", "result": {}})
            writer.write_eof()
            await until(lambda: entry.owner._input_ended)
            assert not entry.owner.state.uncertain and not entry.migration.done()
            release.set()
            await until(lambda: not entry.owner.state.server_requests)
            await upstream.complete(10)
            assert (await receive(reader))["id"] == 10
            assert (await receive(reader))["id"] == 11
            assert await asyncio.wait_for(reader.read(), 2) == b""
            assert not entry.owner.state.uncertain
            assert entry.connector.generation == "incoming"
            assert sum(item.get("id") == "server-1" and "result" in item
                       for item in upstream.messages) == 1
            await ipc.aclose_writer(writer)
        finally:
            release.set()


@pytest.mark.parametrize("after_handshake", [False, True])
async def test_failed_new_backend_preserves_old_epoch_and_backs_off(tmp_path, monkeypatch, after_handshake):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        entry = next(iter(fixture.host.sessions))
        old = entry.owner.state.current
        fixture.refuse = not after_handshake
        fixture.refuse_prepare = after_handshake
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await asyncio.wait_for(entry.migration, 2)
        failed = entry.migration
        assert entry.owner.state.current is old and not entry.owner.state.fenced
        assert entry.generation == "outgoing"
        fixture.host.inspect_sessions()
        assert entry.migration is failed
        await send(writer, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert (await receive(reader))["id"] == 1
        fixture.refuse = False
        fixture.refuse_prepare = False
        entry.retry_at = 0
        fixture.host.inspect_sessions()
        await asyncio.wait_for(entry.migration, 2)
        assert entry.connector.generation == "incoming"
        assert entry.generation == "incoming"
        assert fixture.upstreams[0].starts == 1
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


@pytest.mark.parametrize("case", ["legacy", "foreign", "context", "auth"])
async def test_host_refuses_before_upstream_or_backend_preparation(tmp_path, monkeypatch, case):
    async with shared(tmp_path, monkeypatch) as fixture:
        def change(request):
            if case == "legacy":
                request.pop("custody")
            elif case == "foreign":
                request["installation_root"] = str(tmp_path)
            elif case == "context":
                request["execution_context"]["digest"] = "0" * 64
            else:
                request["token"] = "wrong-owner"
        reader, writer, ack, _ = await fixture.attach(change)
        assert ack["ok"] is False and not fixture.upstreams and not fixture.pipelines
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


async def test_early_upstream_notification_follows_attach_ack(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch, early=True) as fixture:
        reader, writer, ack, _ = await fixture.attach()
        assert ack["attached"] is True
        assert (await receive(reader))["method"] == "notifications/ready"
        await ipc.aclose_writer(writer)


@pytest.mark.parametrize("required_mode", [0, 1])
async def test_sync_conduit_keeps_early_notification_and_live_stream_across_handover(
    tmp_path, monkeypatch, required_mode,
):
    async with shared(tmp_path, monkeypatch, early=True) as fixture:
        connection = await asyncio.to_thread(
            custody_conduit.attach, fixture.host.socket_path, "fixture", fixture.root,
            required_trace_bookkeeping=required_mode,
        )
        assert connection.gettimeout() is None
        reader, writer = await asyncio.open_connection(sock=connection, limit=MAX_FRAME)
        assert (await receive(reader))["method"] == "notifications/ready"
        await send(writer, {"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        assert (await receive(reader))["id"] == 0
        entry = next(iter(fixture.host.sessions))
        await settled(entry.owner)
        await fixture.publish("incoming")
        fixture.host.inspect_sessions()
        await asyncio.wait_for(entry.migration, 2)
        await send(writer, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert (await receive(reader))["id"] == 1
        assert fixture.upstreams[0].starts == 1 and fixture.upstreams[0].closes == 0
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        await ipc.aclose_writer(writer)


async def test_bad_mode_ack_after_actual_upstream_start_never_permits_fallback(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        original_send = fixture.host._send

        async def send_without_mode(writer, reply):
            if reply.get("attached") is True:
                reply = {key: value for key, value in reply.items() if key != "checkpoint_trace_bookkeeping"}
            await original_send(writer, reply)
        monkeypatch.setattr(fixture.host, "_send", send_without_mode)
        with pytest.raises(ConnectionError) as failure:
            await asyncio.to_thread(
                custody_conduit.attach, fixture.host.socket_path, "fixture", fixture.root,
                required_trace_bookkeeping=1,
            )
        assert not isinstance(failure.value, ec.SharingUnavailable)
        assert len(fixture.upstreams) == 1 and fixture.upstreams[0].starts == 1
        await until(lambda: fixture.upstreams[0].closes == 1 and not fixture.host.sessions)


async def test_backend_link_loss_is_terminal_not_a_new_upstream(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        entry = next(iter(fixture.host.sessions))
        backend_writer = next(
            connection for server, connection in fixture.writers if server is fixture.backends[0]
        )
        await ipc.aclose_writer(backend_writer)
        await until(lambda: entry.owner.state.uncertain)
        fixture.host.inspect_sessions()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert fixture.upstreams[0].starts == 1
        assert entry.migration is None
        await ipc.aclose_writer(writer)


async def test_failed_eof_quiescence_still_tears_down_exact_retained_transport(tmp_path, monkeypatch):
    async with shared(tmp_path, monkeypatch) as fixture:
        reader, writer, _, _ = await fixture.attach()
        entry = next(iter(fixture.host.sessions))
        peer = entry.owner._links[entry.owner.state.current].peer
        original = peer.request

        async def fail_quiesce(operation, **data):
            if operation == "quiesce":
                raise ConnectionError("fixture lost quiescence receipt")
            return await original(operation, **data)
        monkeypatch.setattr(peer, "request", fail_quiesce)
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert entry.owner.state.uncertain
        assert fixture.upstreams[0].ends == fixture.upstreams[0].closes == 1
        assert fixture.host.attached == 0
        await ipc.aclose_writer(writer)


@pytest.mark.parametrize("case", ["keeper", "wrong-physical", "unselected", "draining"])
async def test_backend_capability_requires_actual_current_provider(tmp_path, monkeypatch, case):
    async with shared(tmp_path, monkeypatch) as fixture:
        server = fixture.backends[0]
        monkeypatch.setattr(incoming, "launch_generation", lambda: "outgoing")
        assert supports(backend.capability(server).get("custody_backend"))
        if case == "keeper":
            server._runtime_root = None
        elif case == "wrong-physical":
            monkeypatch.setattr(incoming, "launch_generation", lambda: "wrong")
        elif case == "unselected":
            fixture.selector.write_text("incoming", encoding="utf-8")
        else:
            server._draining = True
        assert backend.capability(server) == {}


async def test_custody_cannot_publish_in_backend_namespace(tmp_path):
    with pytest.raises(ValueError, match="separate publication"):
        CustodyHost(tmp_path / "one.sock", tmp_path / "two.sock", tmp_path)


async def test_custody_cannot_alias_backend_publication_namespace(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    alias = tmp_path / "alias"
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return backend if path == alias else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="separate publication"):
        CustodyHost(alias / "serve.sock", backend / "serve.sock", tmp_path)


async def test_keeper_listener_lifecycle_never_publishes_central_backend_route(tmp_path, monkeypatch):
    monkeypatch.setattr(serve, "_HAS_AF_UNIX", False)
    monkeypatch.setattr(ipc, "_HAS_AF_UNIX", False)
    backend_handle = tmp_path / "backend" / "serve.sock"
    backend_handle.parent.mkdir()
    sentinel = ipc._endpoint_path(backend_handle)
    sentinel.write_text("unchanged-backend-publication", encoding="utf-8")
    host = CustodyHost(
        tmp_path / "keeper" / "custody.sock", backend_handle, tmp_path,
        enable_lease=False, idle_timeout=0,
    )
    run = asyncio.create_task(host.serve_forever())
    try:
        await until(lambda: host._server is not None and host._server.is_serving())
        reader, writer, token = await ipc._connect(host.socket_path)
        try:
            await send(writer, {"op": "ping", "token": token})
            reply = await receive(reader)
            assert reply["ready"] is True and reply["custody_host"] == host_descriptor()
            assert host._control_server is None and not (host.socket_path.parent / "active.json").exists()
            assert sentinel.read_text(encoding="utf-8") == "unchanged-backend-publication"
        finally:
            await ipc.aclose_writer(writer)
    finally:
        host.stop()
        await asyncio.wait_for(run, 2)
