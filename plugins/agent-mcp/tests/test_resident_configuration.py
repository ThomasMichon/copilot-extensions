from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from agent_mcp import config, configuration, ipc, serve, sockio
from agent_mcp.request_context import capture_context
from agent_mcp.serve import Server


@pytest.fixture
def owners(tmp_path, monkeypatch):
    """Keep the native owner reader/composer; substitute only its peer transport."""
    cell = tmp_path / "marketplaces" / "selected"
    own = cell / "plugins" / "agent-mcp"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    roots = [tmp_path / "first", tmp_path / "second"]
    reports = {}
    for index, root in enumerate(roots):
        root.mkdir()
        preference = root / ".copilot-extensions" / "agent-mcp" / "bridges" / "shared.yaml"
        preference.parent.mkdir(parents=True)
        preference.write_text(f"timeout: {21 + index}\n", encoding="utf-8")
        reports[str(root)] = {
            "schema": "agent-worktrees.configuration", "version": 1,
            "context": {
                "repository_id": f"repository-{index}",
                "execution_machine": "test-machine", "installation_cell": str(cell),
                "binding": "unbound", "base_root": str(root),
            },
        }
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(configuration._peer_launch, "validate_owner", lambda *args: {
        "cellRoot": str(cell), "pluginRoot": str(own),
    })
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["owner"])
    reads = []

    def run(argv, **kwargs):
        assert argv == ["owner", "state-root", "--configuration", "--json"]
        root = str(kwargs["cwd"])
        reads.append(root)
        if root not in reports:
            return subprocess.CompletedProcess(argv, 1, b"", b"unqualified repository")
        return subprocess.CompletedProcess(argv, 0, json.dumps(reports[root]).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(config, "OVERRIDES_DIR", tmp_path / "no-overrides")
    bridge = tmp_path / "shared.mcp.yaml"
    bridge.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")
    neutral = tmp_path / "daemon-data"
    neutral.mkdir()
    return roots, reports, bridge, neutral, reads


class ConsumerPool:
    size = 0

    def __init__(self):
        self.started = 0
        self.both_started = asyncio.Event()

    async def call(self, key, cfg, tool, arguments):
        self.started += 1
        if self.started == 2:
            self.both_started.set()
        await asyncio.wait_for(self.both_started.wait(), timeout=1)
        return {"content": [{"type": "text", "text": json.dumps({
            "key": key, "timeout": cfg.timeout,
            "provenance": cfg.configuration_provenance["/timeout"],
            "source": str(cfg.source_path),
        })}]}


def request(bridge: Path, root: Path) -> dict:
    return {
        "op": "call", "bridge": str(bridge), "tool": "inspect", "arguments": {},
        "configuration_context": {"version": 1, "root": str(root), "bridge": str(bridge)},
    }


async def test_real_consumer_parity_and_concurrent_repository_isolation(owners, monkeypatch):
    roots, reports, bridge, neutral, reads = owners
    direct = []
    requests = []
    for root in roots:
        monkeypatch.chdir(root)
        cfg = config.load_config(str(bridge))
        direct.append((cfg.timeout, cfg.configuration_provenance["/timeout"]))
        req = request(bridge, root)
        assert capture_context(str(bridge)) == req["configuration_context"]
        requests.append(req)
    monkeypatch.chdir(neutral)
    environment = dict(configuration.os.environ)
    server = Server(neutral / "serve.sock", pool=ConsumerPool(), enable_lease=False)
    replies = await asyncio.gather(*(server._dispatch(req) for req in requests))
    assert Path.cwd() == neutral
    assert dict(configuration.os.environ) == environment
    values = [json.loads(reply["content"]) for reply in replies]
    assert [(value["timeout"], value["provenance"]) for value in values] == direct
    assert values[0]["key"] != values[1]["key"]
    assert all(value["source"] == str(bridge) for value in values)
    assert reads == [str(root) for root in roots] * 2
    assert all(reply["ok"] for reply in replies)


@pytest.mark.parametrize("bad", ["missing", "bridge", "relative", "unknown-version", "unknown-field"])
async def test_missing_or_mismatched_selection_refuses_before_consumer(owners, monkeypatch, bad):
    roots, reports, bridge, neutral, reads = owners
    monkeypatch.chdir(neutral)
    req = request(bridge, roots[0])
    if bad == "missing":
        req.pop("configuration_context")
    elif bad == "bridge":
        req["configuration_context"]["bridge"] = "another.yaml"
    elif bad == "relative":
        req["configuration_context"]["root"] = "."
    elif bad == "unknown-version":
        req["configuration_context"]["version"] = True
    else:
        req["configuration_context"]["owner"] = reports[str(roots[0])]
    server = Server(neutral / "serve.sock", pool=ConsumerPool(), enable_lease=False)
    result = await server._dispatch(req)
    assert not result["ok"] and result["error"].startswith("config:")
    assert server.pool.started == 0
    assert reads == []


@pytest.mark.parametrize("bad_owner", ["foreign-cell", "unqualified-root"])
async def test_real_owner_refusal_is_not_suppressed(owners, monkeypatch, bad_owner):
    roots, reports, bridge, neutral, reads = owners
    root = roots[0]
    if bad_owner == "foreign-cell":
        reports[str(root)]["context"]["installation_cell"] = str(neutral / "foreign-cell")
    else:
        root = neutral
    monkeypatch.chdir(neutral)
    server = Server(neutral / "serve.sock", pool=ConsumerPool(), enable_lease=False)
    result = await server._dispatch(request(bridge, root))
    assert not result["ok"]
    assert "another installation cell" in result["error"] or "owner refused" in result["error"]
    assert server.pool.started == 0
    assert reads == [str(root)]


async def test_relative_config_uses_caller_root_not_daemon_root(owners, monkeypatch):
    roots, reports, bridge, neutral, reads = owners
    selected = roots[0] / bridge.name
    selected.write_bytes(bridge.read_bytes())
    req = request(Path(bridge.name), roots[0])
    monkeypatch.chdir(neutral)
    server = Server(neutral / "serve.sock", pool=ConsumerPool(), enable_lease=False)
    server.pool.both_started.set()
    result = await server._dispatch(req)
    assert result["ok"]
    value = json.loads(result["content"])
    assert (value["timeout"], value["source"]) == (21, str(selected))


async def test_attach_uses_same_qualified_native_consumer(owners, monkeypatch):
    roots, reports, bridge, neutral, reads = owners
    monkeypatch.chdir(neutral)
    loaded = []

    class Session:
        decorator_count = 0

        def __init__(self, cfg, sink):
            loaded.append(cfg)

        async def start(self):
            pass

        async def aclose(self):
            pass

    monkeypatch.setattr(serve, "BridgeSession", Session)
    server = Server(neutral / "serve.sock", enable_lease=False)
    req = request(bridge, roots[0])
    req["op"] = "attach"
    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = Writer()
    await server._run_session(req, reader, writer)
    assert writer.messages == [{"ok": True, "attached": True}]
    assert loaded[0].timeout == 21
    assert loaded[0].configuration_provenance["/timeout"]["repository_id"] == "repository-0"
    assert reads == [str(roots[0])]
    assert server.attached == 0


async def test_named_directory_marketplace_uses_request_workspace(owners, monkeypatch):
    roots, reports, bridge, neutral, reads = owners
    root = roots[0]
    declaration = root / ".github" / "copilot" / "settings.json"
    declaration.parent.mkdir(parents=True)
    declaration.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "local": {"source": {"source": "directory", "path": "./payloads"}},
        },
    }), encoding="utf-8")
    selected = root / "payloads" / "sample" / "mcp" / "shared.yaml"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(bridge.read_bytes())
    monkeypatch.setattr(config, "BRIDGES_DIR", neutral / "no-bridges")
    monkeypatch.chdir(neutral)
    server = Server(neutral / "serve.sock", pool=ConsumerPool(), enable_lease=False)
    server.pool.both_started.set()
    result = await server._dispatch(request(Path("shared"), root))
    assert result["ok"]
    value = json.loads(result["content"])
    assert (value["timeout"], value["source"]) == (21, str(selected))


class Writer:
    def __init__(self):
        self.messages = []
        self.closed = False

    def write(self, data):
        self.messages.append(json.loads(data))

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


@pytest.mark.parametrize("hello", [
    b'{"ok":true,"pong":true}\n', b'{"ok":true,"pong":true,"configuration_context":true}\n',
    b'{"ok":true,"pong":true,"configuration_context":2}\n', b'not-json\n', b"",
])
async def test_new_async_client_refuses_legacy_or_invalid_peer_before_call(monkeypatch, hello):
    reader = asyncio.StreamReader()
    reader.feed_data(hello)
    reader.feed_eof()
    writer = Writer()

    async def connect(path):
        return reader, writer, "private-fixture-token"

    monkeypatch.setattr(ipc, "_connect", connect)
    with pytest.raises(OSError, match="configuration context"):
        await ipc.call_via_socket("unused", "bridge", "mutate", {})
    assert [message["op"] for message in writer.messages] == ["ping"]
    assert writer.closed


async def test_async_client_negotiates_and_binds_actual_request(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"ok":true,"pong":true,"configuration_context":1}\n{"ok":true}\n')
    writer = Writer()

    async def connect(path):
        return reader, writer, "private-fixture-token"

    monkeypatch.setattr(ipc, "_connect", connect)
    assert (await ipc.list_tools_via_socket("unused", "bridge"))["ok"]
    req = writer.messages[1]
    assert req["configuration_context"] == capture_context("bridge")
    assert req["token"] == "private-fixture-token"


def test_sync_forwarder_refuses_old_peer_before_attach(monkeypatch):
    class Socket:
        def __init__(self):
            self.sent = []
            self.closed = False

        def sendall(self, data):
            self.sent.append(json.loads(data))

        def close(self):
            self.closed = True

    sock = Socket()
    monkeypatch.setattr(sockio, "_connect", lambda path: (sock, None))
    monkeypatch.setattr(sockio, "_read_ack_line", lambda conn: b'{"ok":true,"pong":true}\n')
    with pytest.raises(OSError, match="configuration context"):
        sockio.attach("unused", "bridge")
    assert [message["op"] for message in sock.sent] == ["ping"]
    assert sock.closed


def test_real_cli_parser_falls_back_before_tool_submission_to_old_provider(owners, monkeypatch):
    from agent_mcp import __main__ as cli

    roots, reports, bridge, neutral, reads = owners
    monkeypatch.chdir(roots[0])
    writer = Writer()

    async def connect(path):
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"ok":true,"pong":true}\n')
        return reader, writer, None

    monkeypatch.setattr(ipc, "_connect", connect)
    monkeypatch.setattr(ipc, "serve_socket_if_available", lambda: neutral / "old.sock")
    consumed = []

    async def run_call(cfg, tool, arguments):
        consumed.append((cfg.timeout, tool, arguments))
        return 0

    monkeypatch.setattr(cli, "_run_call", run_call)
    args = cli.build_parser().parse_args(["call", str(bridge), "inspect", "{}"])
    assert args.func(args) == 0
    assert consumed == [(21, "inspect", {})]
    assert [message["op"] for message in writer.messages] == ["ping"]
    assert reads == [str(roots[0])]
