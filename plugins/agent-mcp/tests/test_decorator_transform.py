from __future__ import annotations

import json
import sys

from agent_mcp.decorators.transform import TransformDecorator

from ._fake import FakeUpstream, call_req, make_ctx, run, tool


def _transform(tmp_unused=None, **opts):
    ctx, _ = make_ctx()
    return TransformDecorator(opts, ctx)


def _json_result(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj)}], "isError": False}


async def test_extract_unwraps_envelope():
    up = FakeUpstream(
        [tool("list_prs")],
        handlers={"list_prs": lambda a: _json_result({"count": 2, "value": [1, 2]})})
    dec = _transform(rules=[{"tool": "list_prs", "extract": "value"}])
    resp = await run(dec, up, call_req("list_prs"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [1, 2]


async def test_pick_keeps_nested_fields():
    item = {"id": 7, "fields": {"System.Title": "Bug", "System.State": "Active",
                                "Extra": "noise"}, "url": "http://x"}
    up = FakeUpstream([tool("get_wi")], handlers={"get_wi": lambda a: _json_result(item)})
    dec = _transform(rules=[{"tool": "get_wi",
                             "pick": ["id", "fields.System.Title", "fields.System.State"]}])
    resp = await run(dec, up, call_req("get_wi"))
    doc = json.loads(resp["result"]["content"][0]["text"])
    assert doc == {"id": 7, "fields": {"System.Title": "Bug", "System.State": "Active"}}


async def test_drop_removes_fields():
    up = FakeUpstream(
        [tool("g")],
        handlers={"g": lambda a: _json_result({"keep": 1, "secret": 2, "big": [0] * 9})})
    dec = _transform(rules=[{"tool": "g", "drop": ["secret", "big"]}])
    resp = await run(dec, up, call_req("g"))
    assert json.loads(resp["result"]["content"][0]["text"]) == {"keep": 1}


async def test_extract_then_pick_chain():
    payload = {"value": [{"id": 1, "x": 9}], "count": 1}
    up = FakeUpstream([tool("g")], handlers={"g": lambda a: _json_result(payload)})
    # First rule unwraps; second rule (applies to the unwrapped list? no) -- chain on same tool
    dec = _transform(rules=[
        {"tool": "g", "extract": "value"}])
    resp = await run(dec, up, call_req("g"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [{"id": 1, "x": 9}]


async def test_inline_rule_form():
    up = FakeUpstream([tool("g")],
                      handlers={"g": lambda a: _json_result({"value": [1], "n": 1})})
    dec = _transform(tool="g", extract="value")
    resp = await run(dec, up, call_req("g"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [1]


async def test_structured_content_transformed():
    def handler(_a):
        r = _json_result({"ok": True})
        r["structuredContent"] = {"value": [1, 2, 3], "count": 3}
        return r

    up = FakeUpstream([tool("g")], handlers={"g": handler})
    dec = _transform(rules=[{"tool": "g", "extract": "value"}])
    resp = await run(dec, up, call_req("g"))
    assert resp["result"]["structuredContent"] == [1, 2, 3]


async def test_command_filter():
    up = FakeUpstream([tool("g")],
                      handlers={"g": lambda a: _json_result({"value": [1, 2, 3, 4]})})
    cmd = [sys.executable, "-c",
           "import sys, json; d = json.load(sys.stdin); print(json.dumps(len(d['value'])))"]
    dec = _transform(rules=[{"tool": "g", "command": cmd}])
    resp = await run(dec, up, call_req("g"))
    assert json.loads(resp["result"]["content"][0]["text"]) == 4


async def test_no_rule_match_passes_through():
    up = FakeUpstream([tool("other")],
                      handlers={"other": lambda a: _json_result({"value": [1]})})
    dec = _transform(rules=[{"tool": "list_*", "extract": "value"}])
    resp = await run(dec, up, call_req("other"))
    assert json.loads(resp["result"]["content"][0]["text"]) == {"value": [1]}


async def test_pick_maps_over_array_elements():
    prs = [{"id": 1, "title": "A", "noise": "x"}, {"id": 2, "title": "B", "noise": "y"}]
    up = FakeUpstream([tool("list_prs")], handlers={"list_prs": lambda a: _json_result(prs)})
    dec = _transform(rules=[{"tool": "list_prs", "pick": ["id", "title"]}])
    resp = await run(dec, up, call_req("list_prs"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [
        {"id": 1, "title": "A"}, {"id": 2, "title": "B"}]


async def test_extract_then_pick_over_array():
    payload = {"count": 2, "results": [
        {"fields": {"system.title": "A", "system.state": "New", "extra": 1}},
        {"fields": {"system.title": "B", "system.state": "Done", "extra": 2}}]}
    up = FakeUpstream([tool("sw")], handlers={"sw": lambda a: _json_result(payload)})
    dec = _transform(rules=[{"tool": "sw", "extract": "results",
                             "pick": ["fields.system.title", "fields.system.state"]}])
    resp = await run(dec, up, call_req("sw"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [
        {"fields": {"system.title": "A", "system.state": "New"}},
        {"fields": {"system.title": "B", "system.state": "Done"}}]


async def test_drop_maps_over_array_elements():
    items = [{"keep": 1, "drop_me": 9}, {"keep": 2, "drop_me": 8}]
    up = FakeUpstream([tool("g")], handlers={"g": lambda a: _json_result(items)})
    dec = _transform(rules=[{"tool": "g", "drop": ["drop_me"]}])
    resp = await run(dec, up, call_req("g"))
    assert json.loads(resp["result"]["content"][0]["text"]) == [{"keep": 1}, {"keep": 2}]
