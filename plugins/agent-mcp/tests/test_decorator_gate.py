from __future__ import annotations

from agent_mcp.decorators.gate import (
    GateDecorator,
    _eval_predicate,
    _resolve_path,
)

from ._fake import FakeUpstream, call_req, make_ctx, run, tool

# A preflight lookup ("record") whose fields decide whether a gated tool is safe.
SAFE = {"tags": ["public", "internal"], "title": "[OK] widget",
        "isSensitive": False, "scope": {"zone": "green"}}
RESTRICTED = {"tags": ["secret"], "title": "[CRI] widget",
              "isSensitive": True, "scope": {"zone": "red"}}


def _gate(preflight_doc, **opts):
    """A single-tool gate over a fake upstream whose 'lookup' returns preflight_doc."""
    ctx, _ = make_ctx()
    options = {
        "match_tools": ["get_details", "get_discussion"],
        "preflight": {"tool": "lookup", "args_from": {"id": "$args.recordId"},
                      "cache": "per-key"},
        "allow_when": {"all": [
            {"path": "tags[*]", "in": ["public", "internal"]},
            {"path": "isSensitive", "equals": False},
        ]},
        **opts,
    }
    gate = GateDecorator(options, ctx)
    up = FakeUpstream(
        [tool("get_details"), tool("get_discussion"), tool("lookup"), tool("other")],
        handlers={"lookup": lambda args: {"structuredContent": preflight_doc}},
    )
    return gate, up


def _calls(up, name):
    return [c for c in up.calls if c[0] == name]


# ---------------------------------------------------------------------------
# path resolver
# ---------------------------------------------------------------------------

def test_resolve_path_key_and_nesting():
    assert _resolve_path({"a": {"b": 1}}, "a.b") == [1]
    assert _resolve_path({"a": {"b": 1}}, "a.missing") == []


def test_resolve_path_array_wildcard_and_index():
    doc = {"tags": ["x", "y", "z"], "items": [{"n": 1}, {"n": 2}]}
    assert _resolve_path(doc, "tags[*]") == ["x", "y", "z"]
    assert _resolve_path(doc, "items[*].n") == [1, 2]
    assert _resolve_path(doc, "tags[0]") == ["x"]
    assert _resolve_path(doc, "tags[-1]") == ["z"]


# ---------------------------------------------------------------------------
# predicate engine
# ---------------------------------------------------------------------------

def test_predicate_ops():
    doc = {"tags": ["public"], "title": "[OK] x", "isSensitive": False, "n": 3}
    assert _eval_predicate({"path": "tags[*]", "in": ["public", "internal"]}, doc)
    assert _eval_predicate({"path": "tags[*]", "not_in": ["secret"]}, doc)
    assert _eval_predicate({"path": "isSensitive", "equals": False}, doc)
    assert _eval_predicate({"path": "title", "matches": r"\[OK\]"}, doc)
    assert _eval_predicate({"path": "title", "not_matches": r"\[CRI\]"}, doc)
    assert _eval_predicate({"path": "tags", "contains": "public"}, doc)
    assert _eval_predicate({"path": "n", "exists": True}, doc)
    assert _eval_predicate({"path": "missing", "exists": False}, doc)


def test_predicate_not_in_vacuously_true_when_absent():
    # A path that resolves to nothing satisfies a negative op (nothing violates it).
    assert _eval_predicate({"path": "missing[*]", "not_in": ["secret"]}, {})


def test_predicate_combinators():
    doc = {"tags": ["public"], "isSensitive": False}
    assert _eval_predicate({"all": [
        {"any": [{"path": "tags[*]", "in": ["public"]},
                 {"path": "tags[*]", "in": ["internal"]}]},
        {"not": {"path": "isSensitive", "equals": True}},
    ]}, doc)
    assert not _eval_predicate({"any": []}, doc)  # any:[] is false
    assert _eval_predicate({"all": []}, doc)  # all:[] is vacuously true


# ---------------------------------------------------------------------------
# gate behavior
# ---------------------------------------------------------------------------

async def test_gate_allows_safe_record():
    gate, up = _gate(SAFE)
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert resp["result"]["content"][0]["text"] == "ran get_details"
    assert _calls(up, "lookup") == [("lookup", {"id": "r1"})]   # preflight ran
    assert _calls(up, "get_details") == [("get_details", {"recordId": "r1"})]


async def test_gate_stubs_restricted_record():
    gate, up = _gate(RESTRICTED, stub={"blocked": True, "reason": "nope"})
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert resp["result"]["structuredContent"] == {"blocked": True, "reason": "nope"}
    assert resp["result"]["isError"] is False
    assert _calls(up, "get_details") == []   # real call never forwarded


async def test_gate_on_deny_drop():
    gate, up = _gate(RESTRICTED, on_deny="drop")
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert resp["result"] == {"content": [], "isError": False}
    assert _calls(up, "get_details") == []


async def test_gate_on_deny_error():
    gate, up = _gate(RESTRICTED, on_deny="error")
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert "error" in resp
    assert _calls(up, "get_details") == []


async def test_gate_ignores_unmatched_tool():
    gate, up = _gate(RESTRICTED)
    resp = await run(gate, up, call_req("other", {"x": 1}))
    assert resp["result"]["content"][0]["text"] == "ran other"
    assert _calls(up, "lookup") == []   # no preflight for an un-gated tool


async def test_gate_caches_preflight_per_key():
    gate, up = _gate(SAFE)
    await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    await run(gate, up, call_req("get_discussion", {"recordId": "r1"}))
    assert len(_calls(up, "lookup")) == 1   # one preflight shared across both gated calls


async def test_gate_preflight_not_cached_for_distinct_keys():
    gate, up = _gate(SAFE)
    await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    await run(gate, up, call_req("get_details", {"recordId": "r2"}))
    assert len(_calls(up, "lookup")) == 2


async def test_gate_fail_closed_on_preflight_error():
    ctx, _ = make_ctx()
    gate = GateDecorator({
        "match_tools": ["get_details"],
        "preflight": {"tool": "lookup", "args_from": {"id": "$args.recordId"}},
        "allow_when": {"path": "isSensitive", "equals": False},
    }, ctx)
    up = FakeUpstream(
        [tool("get_details"), tool("lookup")],
        handlers={"lookup": lambda args: {"content": [], "isError": True}},
    )
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert resp["result"]["structuredContent"]["blocked"] is True   # default stub
    assert _calls(up, "get_details") == []


async def test_gate_on_error_allow():
    ctx, _ = make_ctx()
    gate = GateDecorator({
        "match_tools": ["get_details"],
        "preflight": {"tool": "lookup", "args_from": {"id": "$args.recordId"}},
        "allow_when": {"path": "isSensitive", "equals": False},
        "on_error": "allow",
    }, ctx)
    up = FakeUpstream(
        [tool("get_details"), tool("lookup")],
        handlers={"lookup": lambda args: {"content": [], "isError": True}},
    )
    resp = await run(gate, up, call_req("get_details", {"recordId": "r1"}))
    assert resp["result"]["content"][0]["text"] == "ran get_details"


async def test_gate_args_from_literal_and_whole_args():
    ctx, _ = make_ctx()
    gate = GateDecorator({
        "match_tools": ["get_details"],
        "preflight": {"tool": "lookup",
                      "args_from": {"whole": "$args", "kind": "record"}},
        "allow_when": {"path": "isSensitive", "equals": False},
    }, ctx)
    up = FakeUpstream(
        [tool("get_details"), tool("lookup")],
        handlers={"lookup": lambda args: {"structuredContent": SAFE}},
    )
    await run(gate, up, call_req("get_details", {"recordId": "r1", "q": 2}))
    assert _calls(up, "lookup") == [
        ("lookup", {"whole": {"recordId": "r1", "q": 2}, "kind": "record"})]
