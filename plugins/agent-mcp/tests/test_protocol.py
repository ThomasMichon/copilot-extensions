"""Unit tests for :mod:`agent_mcp.protocol` -- the dual-era version model."""

from __future__ import annotations

import base64

from agent_mcp import protocol as proto


def test_version_ordering_and_is_modern():
    assert proto.MODERN == "2026-07-28"
    assert proto.LEGACY == "2025-06-18"
    assert proto.SUPPORTED_VERSIONS[0] == proto.MODERN  # modern preferred first
    assert proto.is_modern(proto.MODERN)
    assert proto.is_modern("2027-01-01")  # a later revision is still modern
    assert not proto.is_modern(proto.LEGACY)
    assert not proto.is_modern(None)


def test_client_meta_has_the_three_fields():
    meta = proto.client_meta("2026-07-28", {"name": "x", "version": "1"})
    assert meta[proto.META_PROTOCOL_VERSION] == "2026-07-28"
    assert meta[proto.META_CLIENT_INFO] == {"name": "x", "version": "1"}
    assert meta[proto.META_CLIENT_CAPABILITIES] == {}


def test_inject_client_meta_merges_and_reads_back():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "s", "arguments": {}, "_meta": {"keep": True}}}
    proto.inject_client_meta(msg, "2026-07-28", {"name": "amcp", "version": "9"})
    meta = msg["params"]["_meta"]
    assert meta["keep"] is True  # pre-existing _meta preserved
    assert meta[proto.META_PROTOCOL_VERSION] == "2026-07-28"
    assert proto.request_protocol_version(msg) == "2026-07-28"


def test_inject_client_meta_creates_params_when_missing():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    proto.inject_client_meta(msg, proto.MODERN, {"name": "a", "version": "1"})
    assert proto.request_protocol_version(msg) == proto.MODERN


def test_inject_client_meta_ignores_notifications():
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert proto.inject_client_meta(note, proto.MODERN, {"name": "a"}) is note
    assert "params" not in note


def test_request_protocol_version_absent_is_none():
    assert proto.request_protocol_version(
        {"method": "tools/list", "params": {}}) is None


def test_http_metadata_headers_tools_call():
    msg = {"method": "tools/call", "params": {"name": "get_weather"}}
    h = proto.http_metadata_headers(msg, "2026-07-28")
    assert h["MCP-Protocol-Version"] == "2026-07-28"
    assert h["Mcp-Method"] == "tools/call"
    assert h["Mcp-Name"] == "get_weather"


def test_http_metadata_headers_resources_read_uses_uri():
    msg = {"method": "resources/read", "params": {"uri": "file:///c/config.json"}}
    h = proto.http_metadata_headers(msg, "2026-07-28")
    assert h["Mcp-Name"] == "file:///c/config.json"


def test_http_metadata_headers_no_name_for_plain_method():
    h = proto.http_metadata_headers({"method": "tools/list", "params": {}}, "2026-07-28")
    assert "Mcp-Name" not in h
    assert h["Mcp-Method"] == "tools/list"


def test_encode_header_value_ascii_passthrough():
    assert proto.encode_header_value("us-west1") == "us-west1"


def test_encode_header_value_base64_sentinel_for_non_ascii():
    enc = proto.encode_header_value("Hello, 世界")
    assert enc.startswith("=?base64?") and enc.endswith("?=")
    inner = enc[len("=?base64?"):-len("?=")]
    assert base64.b64decode(inner).decode("utf-8") == "Hello, 世界"


def test_encode_header_value_escapes_literal_sentinel():
    # A plain value that looks like the sentinel must itself be encoded.
    enc = proto.encode_header_value("=?base64?literal?=")
    assert enc != "=?base64?literal?="
    inner = enc[len("=?base64?"):-len("?=")]
    assert base64.b64decode(inner).decode("utf-8") == "=?base64?literal?="


def test_negotiate():
    assert proto.negotiate(proto.MODERN) == proto.MODERN
    assert proto.negotiate(proto.LEGACY) == proto.LEGACY
    assert proto.negotiate("1999-01-01") is None
    # A version-less legacy client is answered with our newest supported version.
    assert proto.negotiate(None) == proto.MODERN


def test_discover_result_shape():
    res = proto.discover_result({"name": "srv", "version": "1"}, {"tools": {}})
    assert res["resultType"] == "complete"
    assert res["supportedVersions"] == list(proto.SUPPORTED_VERSIONS)
    assert res["_meta"][proto.META_SERVER_INFO] == {"name": "srv", "version": "1"}
    assert res["ttlMs"] > 0
    assert res["cacheScope"] == "public"


def test_unsupported_version_error_roundtrip():
    err = proto.unsupported_version_error({"id": 7}, "1999-01-01")
    assert err["id"] == 7
    assert err["error"]["code"] == proto.UNSUPPORTED_PROTOCOL_VERSION
    assert err["error"]["data"]["requested"] == "1999-01-01"
    assert proto.MODERN in err["error"]["data"]["supported"]
    assert proto.is_unsupported_version_error(err)
    assert not proto.is_unsupported_version_error(
        {"error": {"code": -32601, "message": "x"}})
