from __future__ import annotations

from agent_mcp.decorators.filter import FilterDecorator, visible

from ._fake import FakeUpstream, call_req, list_req, make_ctx, names_in, run, tool


def _filter(**opts):
    ctx, _ = make_ctx()
    return FilterDecorator(opts, ctx)


def test_visible_allow_deny():
    assert visible("repo_get", ["repo_*"], [])
    assert not visible("other", ["repo_*"], [])
    assert not visible("danger_drop", [], ["danger_*"])
    assert not visible("repo_x", ["repo_*"], ["repo_x"])  # deny wins


async def test_filter_prunes_tools_list():
    up = FakeUpstream([tool("repo_get"), tool("wit_x"), tool("other")])
    resp = await run(_filter(allow=["repo_*", "wit_*"]), up, list_req())
    assert names_in(resp) == ["repo_get", "wit_x"]


async def test_filter_rejects_hidden_tool_call():
    up = FakeUpstream([tool("repo_get"), tool("danger_drop")])
    resp = await run(_filter(deny=["danger_*"]), up, call_req("danger_drop"))
    assert "error" in resp
    assert up.calls == []  # never forwarded upstream


async def test_filter_allows_visible_tool_call():
    up = FakeUpstream([tool("repo_get")])
    resp = await run(_filter(allow=["repo_*"]), up, call_req("repo_get"))
    assert "result" in resp
    assert up.calls == [("repo_get", {})]


async def test_inactive_filter_passes_through():
    up = FakeUpstream([tool("a"), tool("b")])
    resp = await run(_filter(), up, list_req())
    assert names_in(resp) == ["a", "b"]
