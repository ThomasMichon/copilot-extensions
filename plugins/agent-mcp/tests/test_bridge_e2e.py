"""End-to-end: run the real ``agent-mcp bridge`` process over stdio.

Spawns the bridge as a subprocess wrapping a tiny stdio upstream MCP, feeds
JSON-RPC on stdin, and checks the decorator stack (defer) is applied on the way
out -- exercising the bridge loop + transport + pipeline together, which the
pure-pipeline unit tests do not.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

UPSTREAM = textwrap.dedent(
    """
    import sys, json
    TOOLS = [
        {"name": f"tool_{i}", "description": f"demo tool number {i}",
         "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}}}
        for i in range(50)
    ]
    def reply(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        m = json.loads(line); mid = m.get("id"); method = m.get("method")
        if method == "tools/list":
            reply({"jsonrpc":"2.0","id":mid,"result":{"tools":TOOLS}})
        elif method == "tools/call":
            name = m["params"]["name"]; args = m["params"].get("arguments") or {}
            reply({"jsonrpc":"2.0","id":mid,"result":{"content":[
                {"type":"text","text":json.dumps({"tool":name,"args":args})}],"isError":False}})
        elif mid is not None:
            reply({"jsonrpc":"2.0","id":mid,"result":{}})
    """
)


def _run_bridge(cfg_path, lines):
    proc = subprocess.run(
        [sys.executable, "-m", "agent_mcp", "--log-level", "error",
         "bridge", "--config", str(cfg_path)],
        input="\n".join(lines) + "\n",
        capture_output=True, text=True, timeout=60,
    )
    out = [json.loads(line_) for line_ in proc.stdout.splitlines() if line_.strip()]
    return out, proc


@pytest.fixture()
def bridge_config(tmp_path):
    upstream = tmp_path / "upstream.py"
    upstream.write_text(UPSTREAM, encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    cfg.write_text(json.dumps({
        "server": {"type": "stdio",
                   "command": [sys.executable, str(upstream)]},
        "decorators": [{"type": "defer", "mode": "lazy", "expose": ["tool_1"]}],
    }), encoding="utf-8")
    return cfg


def test_defer_collapses_catalog_end_to_end(bridge_config):
    out, proc = _run_bridge(bridge_config, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "find_tool", "arguments": {"query": "number 7"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "execute_tool",
                               "arguments": {"tool": "tool_7", "arguments": {"x": 9}}}}),
    ])
    by_id = {m.get("id"): m for m in out if "id" in m}

    # tools/list: 50 upstream tools collapse to the exposed one + 3 meta-tools.
    names = [t["name"] for t in by_id[1]["result"]["tools"]]
    assert names == ["tool_1", "find_tool", "execute_tool", "load_tools"], proc.stderr

    # find_tool locates tool_7 without hitting upstream.
    structured = json.loads(by_id[2]["result"]["content"][1]["text"])
    assert [t["name"] for t in structured["tools"]] == ["tool_7"]

    # execute_tool round-trips through to the real upstream tool.
    executed = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert executed == {"tool": "tool_7", "args": {"x": 9}}
