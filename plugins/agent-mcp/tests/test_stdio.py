from __future__ import annotations

import sys

from agent_mcp.auth.base import NoneInjector
from agent_mcp.config import parse_config
from agent_mcp.transports.stdio import StdioTransport

# A minimal stdio MCP child: echo back an initialize result, surfacing the
# injected API_KEY so we can assert env injection end-to-end.
_CHILD = (
    "import sys,json,os\n"
    "for line in sys.stdin:\n"
    "    line=line.strip()\n"
    "    if not line: continue\n"
    "    m=json.loads(line)\n"
    "    out={'jsonrpc':'2.0','id':m.get('id'),"
    "'result':{'name':'echo','key':os.environ.get('API_KEY','')}}\n"
    "    sys.stdout.write(json.dumps(out)+'\\n'); sys.stdout.flush()\n"
)


class _EnvInjector(NoneInjector):
    async def child_env(self):
        return {"API_KEY": "injected"}


async def test_stdio_roundtrip_and_env_injection():
    cfg = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", _CHILD]},
        "auth": {"kind": "none"},
    })
    transport = StdioTransport(cfg, _EnvInjector())
    received: list[dict] = []
    transport.on_message(lambda m: received.append(m))

    await transport.start()
    await transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    await transport.end_input()  # drains buffered child output before close
    await transport.aclose()

    assert received == [{"jsonrpc": "2.0", "id": 1,
                         "result": {"name": "echo", "key": "injected"}}]


async def test_stdio_npm_resolves_runner_at_spawn(monkeypatch):
    # An npm-mode config has no server.command; the transport must resolve it to
    # a runner argv at spawn via resolve_npm_command. Patch the resolver to point
    # at our python child so we can assert the resolved argv actually spawns.
    import agent_mcp.transports.stdio as stdio_mod

    seen: dict = {}

    def _fake_resolve(package, args=()):
        seen["package"] = package
        seen["args"] = list(args)
        return [sys.executable, "-c", _CHILD]

    monkeypatch.setattr(stdio_mod, "resolve_npm_command", _fake_resolve)

    cfg = parse_config({
        "server": {"type": "stdio", "npm": "echo-mcp", "args": ["--x"]},
        "auth": {"kind": "none"},
    })
    transport = StdioTransport(cfg, _EnvInjector())
    received: list[dict] = []
    transport.on_message(lambda m: received.append(m))

    await transport.start()
    await transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    await transport.end_input()
    await transport.aclose()

    assert seen == {"package": "echo-mcp", "args": ["--x"]}
    assert received == [{"jsonrpc": "2.0", "id": 1,
                         "result": {"name": "echo", "key": "injected"}}]

# dropped-`tools/list` bug where big upstream frames were silently lost.
_BIG_CHILD = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    line=line.strip()\n"
    "    if not line: continue\n"
    "    m=json.loads(line)\n"
    "    payload='x'*(200*1024)\n"  # ~200 KiB, well over the 64 KiB default
    "    out={'jsonrpc':'2.0','id':m.get('id'),'result':{'blob':payload}}\n"
    "    sys.stdout.write(json.dumps(out)+'\\n'); sys.stdout.flush()\n"
)


async def test_stdio_forwards_line_over_default_64k_limit():
    cfg = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", _BIG_CHILD]},
        "auth": {"kind": "none"},
    })
    transport = StdioTransport(cfg, NoneInjector())
    received: list[dict] = []
    transport.on_message(lambda m: received.append(m))

    await transport.start()
    await transport.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    await transport.end_input()
    await transport.aclose()

    assert len(received) == 1
    assert received[0]["id"] == 1
    assert len(received[0]["result"]["blob"]) == 200 * 1024
