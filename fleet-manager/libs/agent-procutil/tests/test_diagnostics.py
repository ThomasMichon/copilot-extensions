"""Shared diagnostic selection is bounded and independent of plugin imports."""

import json

import pytest

from agent_procutil.diagnostics import (
    resolver_failure_detail,
    safe_diagnostic_text,
    structured_diagnostic_fields,
    structured_resolver_error,
)

pytestmark = pytest.mark.contract("agent_procutil.diagnostics")


@pytest.mark.parametrize("body,expected", [
    ({"error": "RPC outcome unknown: timed out", "code": "owner_rpc_unknown"},
     {"error": "RPC outcome unknown: timed out", "code": "owner_rpc_unknown"}),
    ({"reason": "creation_conflict", "message": "HTTP 409 conflict"},
     {"reason": "creation_conflict", "message": "HTTP 409 conflict"}),
    ({"error": {"message": "failure", "stage": "send", "code": "unknown_code"}},
     {"message": "failure", "stage": "send", "code": "unknown_code"}),
    ({"launch": {"env": {"error": "not-a-diagnostic"}}}, {}),
    ({"message": {"secret": "not-a-string"}, "env": {"OTHER": "not-a-diagnostic"}}, {}),
])
def test_select_only_known_diagnostic_fields(body, expected):
    assert structured_diagnostic_fields(json.dumps(body)) == expected


@pytest.mark.parametrize("text", [
    '{"launch":\n{"env":\n{"error":"opaque-value"}',
    '[\n{"error":"opaque-value"}',
    '{"error":"bounded"}' + "x" * 65536,
    '{"error":"not-independently-framed"} trailing data',
], ids=["malformed-object", "malformed-array", "oversized", "unframed-suffix"])
def test_untrusted_outer_frames_do_not_authorize_nested_diagnostics(text):
    assert structured_diagnostic_fields(text) == {}
    assert structured_resolver_error(text) is None


def test_existing_resolver_rendering_and_environment_redaction_are_preserved():
    body = '{"error":{"message":"failure","stage":"send","code":"unknown"}}'
    assert structured_resolver_error(body) == "failure (stage=send, code=unknown)"
    assert resolver_failure_detail(body, "warning") == "failure (stage=send, code=unknown); stderr: warning"
    text = safe_diagnostic_text(
        "visible context\nknown-value\nTO\x1b[31mKEN=credential-value",
        800, {"EXAMPLE_SECRET": "known-value"},
    )
    assert "credential-value" not in text and "known-value" not in text
    assert "visible context" in text and "\x1b" not in text
