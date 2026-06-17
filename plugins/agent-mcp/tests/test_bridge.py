from __future__ import annotations

from agent_mcp.bridge import apply_tool_filter
from agent_mcp.config import ToolFilter


def _tools_msg(*names):
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"tools": [{"name": n} for n in names]}}


def _names(msg):
    return [t["name"] for t in msg["result"]["tools"]]


def test_filter_inactive_passes_through():
    msg = _tools_msg("a", "b")
    out = apply_tool_filter(msg, ToolFilter())
    assert _names(out) == ["a", "b"]


def test_allow_glob():
    out = apply_tool_filter(_tools_msg("repo_get", "wit_x", "other"),
                            ToolFilter(allow=["repo_*", "wit_*"]))
    assert _names(out) == ["repo_get", "wit_x"]


def test_deny_glob():
    out = apply_tool_filter(_tools_msg("repo_get", "danger_drop"),
                            ToolFilter(deny=["danger_*"]))
    assert _names(out) == ["repo_get"]


def test_non_tools_list_untouched():
    msg = {"jsonrpc": "2.0", "id": 2, "result": {"serverInfo": {"name": "X"}}}
    out = apply_tool_filter(msg, ToolFilter(allow=["repo_*"]))
    assert out["result"]["serverInfo"]["name"] == "X"
