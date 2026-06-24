from __future__ import annotations

from agent_mcp.decorators.rename import RenameDecorator

from ._fake import FakeUpstream, call_req, list_req, make_ctx, names_in, run, tool


def _rename(**opts):
    ctx, _ = make_ctx()
    return RenameDecorator(opts, ctx)


async def test_namespace_prefixes_names():
    up = FakeUpstream([tool("get"), tool("list")])
    resp = await run(_rename(namespace="ado"), up, list_req())
    assert names_in(resp) == ["ado__get", "ado__list"]


async def test_namespaced_call_routes_back_to_original():
    up = FakeUpstream([tool("get")])
    dec = _rename(namespace="ado")
    await run(dec, up, list_req())  # learn the mapping
    await run(dec, up, call_req("ado__get"))
    assert up.calls == [("get", {})]


async def test_structural_reverse_without_prior_list():
    # namespace/prefix are reversible even before a tools/list.
    up = FakeUpstream([tool("get")])
    await run(_rename(namespace="ado", prefix="x_"), up, call_req("ado__x_get"))
    assert up.calls == [("get", {})]


async def test_regex_pattern_rename():
    up = FakeUpstream([tool("wit_query"), tool("repo_get")])
    resp = await run(_rename(patterns=[{"match": "^wit_", "replace": "workitem_"}]),
                     up, list_req())
    assert names_in(resp) == ["workitem_query", "repo_get"]


async def test_regex_call_routes_back_via_learned_map():
    up = FakeUpstream([tool("wit_query")])
    dec = _rename(patterns=[{"match": "^wit_", "replace": "workitem_"}])
    await run(dec, up, list_req())
    await run(dec, up, call_req("workitem_query"))
    assert up.calls == [("wit_query", {})]


async def test_description_rewrite():
    up = FakeUpstream([tool("get", "Fetch a repo")])
    resp = await run(_rename(description={"prefix": "[ADO] "}), up, list_req())
    assert resp["result"]["tools"][0]["description"] == "[ADO] Fetch a repo"
