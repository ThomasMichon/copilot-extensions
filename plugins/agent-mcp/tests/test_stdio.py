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
