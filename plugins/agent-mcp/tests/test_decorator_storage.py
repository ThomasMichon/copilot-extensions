from __future__ import annotations

import json
import sys

import pytest

from agent_mcp.decorators.storage import FileBackend, HttpBackend, StorageDecorator

from ._fake import FakeUpstream, call_req, list_req, make_ctx, names_in, run, tool


def _storage(tmp_path, **opts):
    ctx, _ = make_ctx()
    opts.setdefault("dir", str(tmp_path))
    return StorageDecorator(opts, ctx)


def _json_result(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj)}], "isError": False}


def test_file_backend_round_trip(tmp_path):
    backend = FileBackend(tmp_path)
    handle = backend.store("hello world")
    assert backend.owns(handle)
    assert backend.load(handle) == "hello world"


async def test_large_output_externalized(tmp_path):
    big = "x" * 5000
    up = FakeUpstream([tool("dump")],
                      handlers={"dump": lambda a: {"content": [
                          {"type": "text", "text": big}], "isError": False}})
    dec = _storage(tmp_path, threshold=1000, max_preview=10)
    resp = await run(dec, up, call_req("dump"))
    block = resp["result"]["content"][0]
    assert block["text"].startswith("x" * 10)
    handle = block["annotations"]["stream"]
    assert handle.startswith("mcpstream://")
    # The full value is retrievable from the backend.
    assert dec.backend.load(handle) == big


async def test_small_output_not_externalized(tmp_path):
    up = FakeUpstream([tool("ping")],
                      handlers={"ping": lambda a: {"content": [
                          {"type": "text", "text": "pong"}], "isError": False}})
    resp = await run(_storage(tmp_path, threshold=1000), up, call_req("ping"))
    assert resp["result"]["content"][0]["text"] == "pong"
    assert "annotations" not in resp["result"]["content"][0]


async def test_read_stream_tool_listed_and_reads(tmp_path):
    dec = _storage(tmp_path, threshold=1000)
    handle = dec.backend.store("the full payload")
    up = FakeUpstream([tool("a")])
    resp = await run(dec, up, list_req())
    assert "read_stream" in names_in(resp)
    out = await run(dec, up, call_req("read_stream", {"handle": handle}))
    assert out["result"]["content"][0]["text"] == "the full payload"


async def test_read_stream_slice(tmp_path):
    dec = _storage(tmp_path, threshold=1000)
    handle = dec.backend.store("0123456789")
    up = FakeUpstream([tool("a")])
    out = await run(dec, up, call_req("read_stream",
                                      {"handle": handle, "offset": 2, "length": 3}))
    assert out["result"]["content"][0]["text"] == "234"


async def test_input_rehydrated_from_stream_wrapper(tmp_path):
    dec = _storage(tmp_path, threshold=1000)
    handle = dec.backend.store(json.dumps({"k": "v"}))
    up = FakeUpstream([tool("consume")])
    await run(dec, up, call_req("consume", {"data": {"$stream": handle}}))
    assert up.calls == [("consume", {"data": {"k": "v"}})]


async def test_input_rehydrated_from_bare_handle(tmp_path):
    dec = _storage(tmp_path, threshold=1000)
    handle = dec.backend.store("raw text")
    up = FakeUpstream([tool("consume")])
    await run(dec, up, call_req("consume", {"path": handle}))
    assert up.calls == [("consume", {"path": "raw text"})]


async def test_read_stream_invalid_handle(tmp_path):
    up = FakeUpstream([tool("a")])
    resp = await run(_storage(tmp_path), up, call_req("read_stream", {"handle": "nope"}))
    assert "error" in resp


def test_file_backend_rejects_path_traversal(tmp_path):
    backend = FileBackend(tmp_path)
    for bad in ("mcpstream://..", "mcpstream://../secret", "mcpstream://sub/x",
                "mcpstream://C:Windows", "mcpstream://"):
        with pytest.raises((ValueError, FileNotFoundError)):
            backend.load(bad)


def test_http_backend_owns_exact_host_and_path():
    backend = HttpBackend("https://store.internal/streams")
    assert backend.owns("https://store.internal/streams/abc")
    assert backend.owns("https://store.internal/streams")
    # look-alike host must be rejected (no SSRF)
    assert not backend.owns("https://store.internal.evil.com/streams/abc")
    assert not backend.owns("http://store.internal/streams/abc")  # scheme differs
    assert not backend.owns("https://store.internal/other/abc")   # path differs


# -- fine-grained rules ----------------------------------------------------

async def test_output_field_externalized_with_summary(tmp_path):
    items = [{"id": i, "name": f"n{i}"} for i in range(10)]
    up = FakeUpstream(
        [tool("get_list_items")],
        handlers={"get_list_items": lambda a: _json_result({"items": items, "total": 10})})
    dec = _storage(tmp_path, rules=[
        {"tool": "get_list_items",
         "outputs": [{"path": "items", "summary": {"head": 3}}]}])
    resp = await run(dec, up, call_req("get_list_items"))
    doc = json.loads(resp["result"]["content"][0]["text"])
    ref = doc["items"]
    assert ref["$stream"].startswith("mcpstream://")
    assert ref["summary"]["count"] == 10
    assert ref["summary"]["schema"]["type"] == "array"
    assert ref["summary"]["schema"]["items"]["type"] == "object"
    assert ref["summary"]["head"] == items[:3]
    assert doc["total"] == 10  # sibling field untouched
    # The full array is recoverable from the stream.
    assert json.loads(dec.backend.load(ref["$stream"])) == items


async def test_output_field_externalized_in_structured_content(tmp_path):
    items = [{"a": 1}, {"a": 2}]

    def handler(_args):
        r = _json_result({"ok": True})
        r["structuredContent"] = {"items": items}
        return r

    up = FakeUpstream([tool("g")], handlers={"g": handler})
    dec = _storage(tmp_path, rules=[{"tool": "g", "outputs": [{"path": "items"}]}])
    resp = await run(dec, up, call_req("g"))
    ref = resp["result"]["structuredContent"]["items"]
    assert ref["$stream"].startswith("mcpstream://")
    assert ref["summary"]["count"] == 2


async def test_nested_output_path(tmp_path):
    up = FakeUpstream(
        [tool("q")],
        handlers={"q": lambda a: _json_result({"data": {"rows": [1, 2, 3]}})})
    dec = _storage(tmp_path, rules=[{"tool": "q", "outputs": [{"path": "data.rows"}]}])
    resp = await run(dec, up, call_req("q"))
    doc = json.loads(resp["result"]["content"][0]["text"])
    assert "$stream" in doc["data"]["rows"]


async def test_externalized_output_pipes_back_as_input(tmp_path):
    dec = _storage(tmp_path)
    handle = dec.backend.store(json.dumps([1, 2, 3]))
    up = FakeUpstream([tool("consume")])
    # An output ref {"$stream": ..., "summary": ...} is accepted as an input.
    await run(dec, up, call_req("consume",
                                {"data": {"$stream": handle, "summary": {"count": 3}}}))
    assert up.calls == [("consume", {"data": [1, 2, 3]})]


async def test_input_param_schema_streamified(tmp_path):
    up = FakeUpstream([tool("create", "Create an item", {
        "type": "object",
        "properties": {"payload": {"type": "object", "description": "the body"}},
        "required": ["payload"]})])
    dec = _storage(tmp_path, rules=[
        {"tool": "create", "inputs": [{"path": "payload", "note": "large body"}]}])
    resp = await run(dec, up, list_req())
    sch = resp["result"]["tools"][0]["inputSchema"]["properties"]["payload"]
    assert sch["type"] == "string"
    assert sch["format"] == "uri"
    assert "JSON-serialized object" in sch["description"]
    assert "large body" in sch["description"]
    assert "the body" in sch["description"]  # original description preserved


async def test_command_summarizer(tmp_path):
    items = [1, 2, 3, 4]
    up = FakeUpstream([tool("g")],
                      handlers={"g": lambda a: _json_result({"items": items})})
    cmd = [sys.executable, "-c",
           "import sys, json; d = json.load(sys.stdin); print(json.dumps({'n': len(d)}))"]
    dec = _storage(tmp_path, rules=[
        {"tool": "g", "outputs": [{"path": "items", "summary": {"command": cmd}}]}])
    resp = await run(dec, up, call_req("g"))
    doc = json.loads(resp["result"]["content"][0]["text"])
    assert doc["items"]["summary"] == {"n": 4}


async def test_rule_tool_glob_no_match_falls_back_to_blanket(tmp_path):
    big = "y" * 5000
    up = FakeUpstream([tool("other")],
                      handlers={"other": lambda a: {"content": [
                          {"type": "text", "text": big}], "isError": False}})
    # Rule targets a different tool; this call gets blanket externalization.
    dec = _storage(tmp_path, threshold=1000, rules=[
        {"tool": "get_*", "outputs": [{"path": "items"}]}])
    resp = await run(dec, up, call_req("other"))
    assert resp["result"]["content"][0]["annotations"]["stream"].startswith("mcpstream://")
