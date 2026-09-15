from __future__ import annotations

from agent_mcp.decorators.input_gate import InputGateDecorator

from ._fake import FakeUpstream, call_req, make_ctx, run, tool


def _gate(**opts):
    ctx, _ = make_ctx()
    options = {
        "match_tools": ["update_incident"],
        "deny_when": {"any": [
            {"path": "tags[*]", "matches": "(?i)^ai-safe$"},
            {"path": "title",
             "matches": r"(?i)(^|[^A-Za-z0-9_-])ai-safe([^A-Za-z0-9_-]|$)"},
        ]},
        **opts,
    }
    gate = InputGateDecorator(options, ctx)
    up = FakeUpstream([tool("update_incident"), tool("other")])
    return gate, up


def _calls(up, name):
    return [c for c in up.calls if c[0] == name]


async def test_allows_a_call_without_the_denied_marker():
    gate, up = _gate()
    resp = await run(gate, up, call_req("update_incident",
                                         {"tags": ["Sev2"], "title": "[MSRC] foo"}))
    assert resp["result"]["content"][0]["text"] == "ran update_incident"
    assert _calls(up, "update_incident") == [
        ("update_incident", {"tags": ["Sev2"], "title": "[MSRC] foo"})]


async def test_denies_a_call_introducing_the_tag_case_insensitively():
    gate, up = _gate()
    resp = await run(gate, up, call_req("update_incident",
                                         {"tags": ["AI-SAFE"], "title": "[MSRC] foo"}))
    assert "error" in resp
    assert _calls(up, "update_incident") == []   # never forwarded


async def test_denies_a_call_introducing_the_title_keyword():
    gate, up = _gate()
    resp = await run(gate, up, call_req(
        "update_incident", {"tags": [], "title": "[MSRC] ai-safe reviewed"}))
    assert "error" in resp
    assert _calls(up, "update_incident") == []


async def test_does_not_deny_a_substring_that_is_not_a_discrete_keyword():
    gate, up = _gate()
    resp = await run(gate, up, call_req(
        "update_incident", {"tags": ["notai-safeish"], "title": "[MSRC] foo"}))
    assert resp["result"]["content"][0]["text"] == "ran update_incident"


async def test_ignores_unmatched_tool():
    gate, up = _gate()
    resp = await run(gate, up, call_req("other", {"tags": ["ai-safe"]}))
    assert resp["result"]["content"][0]["text"] == "ran other"
    assert _calls(up, "other") == [("other", {"tags": ["ai-safe"]})]


async def test_on_deny_stub():
    gate, up = _gate(on_deny="stub", stub={"blocked": True, "reason": "nope"})
    resp = await run(gate, up, call_req("update_incident", {"tags": ["ai-safe"]}))
    assert resp["result"]["structuredContent"] == {"blocked": True, "reason": "nope"}
    assert resp["result"]["isError"] is False
    assert _calls(up, "update_incident") == []


async def test_on_deny_drop():
    gate, up = _gate(on_deny="drop")
    resp = await run(gate, up, call_req("update_incident", {"tags": ["ai-safe"]}))
    assert resp["result"] == {"content": [], "isError": False}
    assert _calls(up, "update_incident") == []


async def test_on_deny_default_is_error():
    gate, up = _gate()
    resp = await run(gate, up, call_req("update_incident", {"tags": ["ai-safe"]}))
    assert "error" in resp
    assert resp["error"]["message"] == "denied by input policy"


async def test_custom_reason_used_in_error():
    gate, up = _gate(reason="ai-safe is human-only; agent must not self-grant it")
    resp = await run(gate, up, call_req("update_incident", {"tags": ["ai-safe"]}))
    assert resp["error"]["message"] == "ai-safe is human-only; agent must not self-grant it"


async def test_no_upstream_call_at_all_before_denial():
    # Unlike `gate`, there is no preflight round-trip -- the call itself is
    # evaluated locally and never reaches the upstream when denied.
    gate, up = _gate()
    await run(gate, up, call_req("update_incident", {"tags": ["ai-safe"]}))
    assert up.calls == []


async def test_denied_notification_produces_no_response_and_no_forward():
    # A tools/call with no 'id' is a JSON-RPC notification -- per the pipeline
    # contract, it never gets a response (pipeline.py's Next return type is
    # `dict | None`, None for notifications). A denied notification must
    # still produce None, not an id:null error/stub response.
    gate, up = _gate()
    notification = {"jsonrpc": "2.0", "method": "tools/call",
                     "params": {"name": "update_incident", "arguments": {"tags": ["ai-safe"]}}}
    resp = await run(gate, up, notification)
    assert resp is None
    assert up.calls == []


async def test_denies_non_object_arguments_outright():
    # `tool_call_args` coerces any non-dict `arguments` to `{}`, which would
    # otherwise let a malformed payload (a list/string/number instead of an
    # object) sail through deny_when evaluation as "no fields to check" --
    # this is a fail-open bypass for an authorization boundary. A non-object
    # `arguments` on a gated tool must be denied outright, never coerced away.
    gate, up = _gate()
    for bad_args in ([1, 2, 3], "not-an-object", 42, True):
        req = call_req("update_incident", None)
        req["params"]["arguments"] = bad_args
        resp = await run(gate, up, req)
        assert "error" in resp, f"expected denial for arguments={bad_args!r}"
    assert up.calls == []


async def test_allows_missing_or_null_arguments_as_empty():
    # Absent/null 'arguments' legitimately means "no fields" and is NOT the
    # same failure mode as a wrong-TYPE 'arguments' -- it should evaluate
    # deny_when against an empty object (matching pre-existing behavior),
    # not be denied outright.
    gate, up = _gate()
    req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
           "params": {"name": "update_incident"}}
    resp = await run(gate, up, req)
    assert resp["result"]["content"][0]["text"] == "ran update_incident"

