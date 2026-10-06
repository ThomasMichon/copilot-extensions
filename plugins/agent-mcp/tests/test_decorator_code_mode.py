from __future__ import annotations

import shutil

import pytest

from agent_mcp.decorators._catalog import render_tools_interface
from agent_mcp.decorators.code_mode import CodeModeDecorator

from ._fake import FakeUpstream, call_req, list_req, make_ctx, names_in, run, tool

CATALOG = [
    tool("add", "Add two numbers",
         {"type": "object",
          "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
          "required": ["a", "b"]}),
    tool("greet", "Greet someone",
         {"type": "object", "properties": {"name": {"type": "string"}}}),
]

HAS_NODE = shutil.which("node") is not None


def _code(ctx=None, **opts):
    if ctx is None:
        ctx, _ = make_ctx()
    return CodeModeDecorator(opts, ctx)


def test_render_tools_interface():
    ts = render_tools_interface(CATALOG)
    assert "interface Tools {" in ts
    assert "add(args: { a: number; b: number }): Promise<any>;" in ts
    assert "/** Add two numbers */" in ts


def test_render_handles_invalid_identifier():
    ts = render_tools_interface([tool("weird-name", "x", {"type": "object"})])
    assert '["weird-name"](args: Record<string, any>): Promise<any>;' in ts


async def test_tools_list_collapses_to_run_code():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_code(), up, list_req())
    assert names_in(resp) == ["run_code", "find_tool", "code_apis"]


async def test_expose_keeps_named_tools():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_code(expose=["greet"]), up, list_req())
    assert names_in(resp) == ["greet", "run_code", "find_tool", "code_apis"]


async def test_code_apis_returns_interface():
    up = FakeUpstream(list(CATALOG))
    dec = _code()
    resp = await run(dec, up, call_req("code_apis"))
    assert "interface Tools {" in resp["result"]["content"][0]["text"]


async def test_run_code_missing_code_errors():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_code(), up, call_req("run_code", {}))
    assert "error" in resp


@pytest.mark.skipif(not HAS_NODE, reason="node runtime not available")
async def test_run_code_executes_and_chains_tools():
    up = FakeUpstream(
        list(CATALOG),
        handlers={
            "add": lambda a: {"content": [
                {"type": "text", "text": str(a["a"] + a["b"])}], "isError": False},
        },
    )
    dec = _code()
    await run(dec, up, list_req())  # capture catalog
    code = "const x = await tools.add({a: 2, b: 3}); return {sum: x};"
    resp = await run(dec, up, call_req("run_code", {"code": code}))
    assert resp["result"]["isError"] is False
    assert '"sum": 5' in resp["result"]["content"][0]["text"]
    assert up.calls == [("add", {"a": 2, "b": 3})]


@pytest.mark.skipif(not HAS_NODE, reason="node runtime not available")
async def test_run_code_captures_console_log():
    up = FakeUpstream(list(CATALOG))
    dec = _code()
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req(
        "run_code", {"code": "console.log('hello from node'); return 1;"}))
    assert "hello from node" in resp["result"]["content"][0]["text"]


@pytest.mark.skipif(not HAS_NODE, reason="node runtime not available")
async def test_run_code_reports_errors():
    up = FakeUpstream(list(CATALOG))
    dec = _code()
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req("run_code", {"code": "throw new Error('boom');"}))
    assert resp["result"]["isError"] is True
    assert "boom" in resp["result"]["content"][0]["text"]


@pytest.mark.skipif(not HAS_NODE, reason="node runtime not available")
async def test_run_code_times_out_and_reaps_child():
    up = FakeUpstream(list(CATALOG))
    dec = _code(timeout=0.5)
    await run(dec, up, list_req())
    # A busy-loop snippet never returns; the timeout path must kill the child.
    resp = await run(dec, up, call_req("run_code", {"code": "while (true) {}"}))
    assert resp["result"]["isError"] is True
    assert "timed out" in resp["result"]["content"][0]["text"]


async def test_paginated_catalog_in_interface():
    big = [tool(f"tool_{i}", f"demo {i}", {"type": "object"}) for i in range(25)]
    up = FakeUpstream(big, page_size=10)
    dec = _code()
    await run(dec, up, list_req())  # pass-through is only page 1
    resp = await run(dec, up, call_req("code_apis"))
    text = resp["result"]["content"][0]["text"]
    assert "tool_23(args" in text  # beyond page 1


async def test_find_tool_returns_typed_signatures():
    up = FakeUpstream(list(CATALOG))
    dec = _code()
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req("find_tool", {"query": "add"}))
    text = resp["result"]["content"][0]["text"]
    assert "add(args: { a: number; b: number }): Promise<any>;" in text
    assert "greet(" not in text  # only matches


async def test_large_catalog_run_code_points_to_find():
    big = [tool(f"tool_{i}", f"demo {i}", {"type": "object"}) for i in range(60)]
    up = FakeUpstream(big)
    dec = _code(interface_limit=40)
    resp = await run(dec, up, list_req())
    run_tool = next(t for t in resp["result"]["tools"] if t["name"] == "run_code")
    assert "find_tool" in run_tool["description"]
    assert "interface Tools {" not in run_tool["description"]  # not embedded


async def test_small_catalog_run_code_embeds_interface():
    up = FakeUpstream(list(CATALOG))
    dec = _code(interface_limit=40)
    resp = await run(dec, up, list_req())
    run_tool = next(t for t in resp["result"]["tools"] if t["name"] == "run_code")
    assert "interface Tools {" in run_tool["description"]


async def test_run_node_passes_no_window_creationflags(monkeypatch):
    # The Node-harness spawn must carry the standard Windows console-
    # suppression flag (copilot-extensions#5425), regardless of whether a
    # real `node` runtime is available on this runner -- stub `shutil.which`
    # so `_handle_run`'s own runtime-presence check doesn't short-circuit
    # before `_run_node` ever spawns anything.
    from agent_mcp._exec import no_window_creationflags

    monkeypatch.setattr(
        "agent_mcp.decorators.code_mode.shutil.which",
        lambda name: "/fake/node",
    )

    captured: dict = {}

    class _FakeStdin:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class _FakeProc:
        def __init__(self):
            self.stdin = _FakeStdin()
            self.stdout = None  # short-circuits _run_node's read loop
            self.returncode = 0

        async def wait(self):
            return 0

    async def fake_exec(*argv, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(
        "agent_mcp.decorators.code_mode.asyncio.create_subprocess_exec", fake_exec,
    )

    up = FakeUpstream(list(CATALOG))
    dec = _code()
    await run(dec, up, list_req())  # capture catalog
    resp = await run(dec, up, call_req("run_code", {"code": "1"}))
    assert resp["result"]["isError"] is True  # no "done" message -> error outcome
    assert captured["kwargs"]["creationflags"] == no_window_creationflags()
