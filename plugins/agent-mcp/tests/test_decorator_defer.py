from __future__ import annotations

import json

from agent_mcp.decorators.defer import DeferDecorator

from ._fake import FakeUpstream, call_req, list_req, make_ctx, names_in, run, tool

CATALOG = [
    tool("list_clients", "List network clients", {"type": "object"}),
    tool("get_client", "Get one client by id"),
    tool("reboot_device", "Reboot a device"),
    tool("search_logs", "Search system logs"),
]


def _defer(ctx=None, **opts):
    if ctx is None:
        ctx, _ = make_ctx()
    return DeferDecorator(opts, ctx)


async def test_lazy_mode_hides_catalog_behind_meta_tools():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_defer(), up, list_req())
    assert names_in(resp) == ["find_tool", "execute_tool", "load_tools"]


async def test_eager_mode_lists_catalog_plus_meta():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_defer(mode="eager"), up, list_req())
    names = names_in(resp)
    assert "list_clients" in names and "find_tool" in names
    assert "load_tools" not in names  # only in lazy


async def test_meta_only_with_expose():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_defer(mode="meta_only", expose=["search_*"]), up, list_req())
    assert names_in(resp) == ["search_logs", "find_tool", "execute_tool"]


async def test_find_tool_searches_catalog():
    up = FakeUpstream(list(CATALOG))
    dec = _defer()
    await run(dec, up, list_req())  # capture catalog
    resp = await run(dec, up, call_req("find_tool", {"query": "client"}))
    structured = json.loads(resp["result"]["content"][1]["text"])
    found = [t["name"] for t in structured["tools"]]
    assert found == ["list_clients", "get_client"]
    assert up.calls == []  # find does not hit upstream tools


async def test_find_tool_fetches_catalog_if_empty():
    up = FakeUpstream(list(CATALOG))
    dec = _defer()
    # No prior tools/list; find_tool should lazily fetch the catalog.
    resp = await run(dec, up, call_req("find_tool", {"query": "reboot"}))
    structured = json.loads(resp["result"]["content"][1]["text"])
    assert [t["name"] for t in structured["tools"]] == ["reboot_device"]
    assert up.list_requests == 1


async def test_find_tool_include_schemas():
    up = FakeUpstream(list(CATALOG))
    dec = _defer()
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req("find_tool",
                                       {"query": "list_clients", "include_schemas": True}))
    structured = json.loads(resp["result"]["content"][1]["text"])
    assert structured["tools"][0]["inputSchema"] == {"type": "object"}


async def test_execute_tool_invokes_real_tool():
    up = FakeUpstream(list(CATALOG),
                      handlers={"reboot_device": lambda a: {"content": [
                          {"type": "text", "text": f"rebooted {a['id']}"}], "isError": False}})
    dec = _defer()
    resp = await run(dec, up, call_req("execute_tool",
                                       {"tool": "reboot_device", "arguments": {"id": "ap1"}}))
    assert resp["result"]["content"][0]["text"] == "rebooted ap1"
    assert up.calls == [("reboot_device", {"id": "ap1"})]


async def test_execute_tool_requires_tool_name():
    up = FakeUpstream(list(CATALOG))
    resp = await run(_defer(), up, call_req("execute_tool", {}))
    assert "error" in resp


async def test_load_tools_emits_list_changed_and_exposes():
    up = FakeUpstream(list(CATALOG))
    ctx, emitted = make_ctx()
    dec = _defer(ctx)
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req("load_tools", {"tools": ["get_client"]}))
    assert resp["result"]["isError"] is False
    assert any(m.get("method") == "notifications/tools/list_changed" for m in emitted)
    # Subsequent tools/list now exposes the loaded tool.
    resp2 = await run(dec, up, list_req())
    assert "get_client" in names_in(resp2)


async def test_load_tools_reports_unknown():
    up = FakeUpstream(list(CATALOG))
    dec = _defer()
    await run(dec, up, list_req())
    resp = await run(dec, up, call_req("load_tools", {"tools": ["nope"]}))
    assert "unknown" in resp["result"]["content"][0]["text"]


async def test_paginated_catalog_fully_captured():
    # 25 tools served 10 per page; find_tool must see tools beyond page 1.
    big = [tool(f"tool_{i}", f"demo {i}") for i in range(25)]
    up = FakeUpstream(big, page_size=10)
    dec = _defer()
    await run(dec, up, list_req())  # pass-through returns only page 1
    resp = await run(dec, up, call_req("find_tool", {"query": "tool_23"}))
    structured = json.loads(resp["result"]["content"][1]["text"])
    assert [t["name"] for t in structured["tools"]] == ["tool_23"]
