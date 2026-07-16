"""Unit tests for the CLI->MCP sidecar model (:mod:`agent_mcp.cli_tools`)."""

from __future__ import annotations

import pytest
import yaml

from agent_mcp.cli_tools import (
    CliToolError,
    build_argv,
    load_cli_tools,
    parse_sidecar,
    tool_in_scope,
)
from agent_mcp.config import ConfigError, parse_config


def _sidecar(**over) -> str:
    mcp = {
        "name": "vei_search",
        "description": "Semantic search via VEI.",
        "scope": "shared",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        "invoke": {
            "command": "vei-search",
            "args": ["{query}", {"flag": "--limit", "value": "{limit}", "when": "limit"}],
        },
    }
    mcp.update(over)
    return "---\n" + yaml.safe_dump({"mcp": mcp}) + "---\n# doc body\n"


def test_parse_sidecar_ok():
    tool = parse_sidecar(_sidecar())
    assert tool.name == "vei_search"
    assert tool.command == "vei-search"
    assert tool.scope == "shared"
    assert tool.mcp_dict()["inputSchema"]["required"] == ["query"]


def test_parse_sidecar_requires_mcp_block():
    with pytest.raises(CliToolError, match="no 'mcp:'"):
        parse_sidecar("# just a doc, no frontmatter\n")


def test_parse_sidecar_requires_name_and_command():
    with pytest.raises(CliToolError, match="mcp.name"):
        parse_sidecar(_sidecar(name=""))
    with pytest.raises(CliToolError, match="invoke"):
        parse_sidecar(_sidecar(invoke={"args": []}))


def test_build_argv_positional_and_optional_flag():
    tool = parse_sidecar(_sidecar())
    assert build_argv(tool, {"query": "bears", "limit": 5}) == \
        ["vei-search", "bears", "--limit", "5"]
    # ``limit`` absent -> the whole optional entry is skipped via ``when``.
    assert build_argv(tool, {"query": "bears"}) == ["vei-search", "bears"]


def test_build_argv_missing_required_positional_errors():
    tool = parse_sidecar(_sidecar())
    with pytest.raises(CliToolError, match="missing value for"):
        build_argv(tool, {"limit": 5})


def test_build_argv_boolean_presence_and_repeat():
    tool = parse_sidecar(_sidecar(invoke={
        "command": "demo",
        "args": [
            {"flag": "--verbose", "when": "verbose"},
            {"flag": "--tag", "repeat": "tags"},
        ],
    }))
    assert build_argv(tool, {"verbose": True, "tags": ["a", "b"]}) == \
        ["demo", "--verbose", "--tag", "a", "--tag", "b"]
    assert build_argv(tool, {}) == ["demo"]


def test_build_argv_value_is_single_token_no_shell_split():
    """A metacharacter-laden value stays one argv token (no shell = no injection)."""
    tool = parse_sidecar(_sidecar())
    argv = build_argv(tool, {"query": "a; rm -rf / && echo $HOME"})
    assert argv == ["vei-search", "a; rm -rf / && echo $HOME"]


def test_scope_gating():
    shared = parse_sidecar(_sidecar(scope="shared"))
    scoped = parse_sidecar(_sidecar(scope="lambda-core"))
    untagged = parse_sidecar(_sidecar(scope=None))
    # No configured scopes -> everything allowed.
    assert tool_in_scope(scoped, [])
    # Configured host scopes gate by tag; untagged always allowed.
    assert tool_in_scope(shared, ["shared", "lambda-core"])
    assert tool_in_scope(scoped, ["shared", "lambda-core"])
    assert not tool_in_scope(scoped, ["shared"])
    assert tool_in_scope(untagged, ["shared"])


def test_load_cli_tools_dedups_and_resolves_relative(tmp_path):
    (tmp_path / "a.md").write_text(_sidecar(name="tool_a"), encoding="utf-8")
    (tmp_path / "b.md").write_text(_sidecar(name="tool_b"), encoding="utf-8")
    tools = load_cli_tools(["a.md", "b.md"], base_dir=tmp_path)
    assert [t.name for t in tools] == ["tool_a", "tool_b"]

    (tmp_path / "c.md").write_text(_sidecar(name="tool_a"), encoding="utf-8")
    with pytest.raises(CliToolError, match="duplicate tool name"):
        load_cli_tools(["a.md", "c.md"], base_dir=tmp_path)


def test_load_cli_tools_missing_file(tmp_path):
    with pytest.raises(CliToolError, match="sidecar not found"):
        load_cli_tools(["nope.md"], base_dir=tmp_path)


def test_config_cli_type_requires_tools_from():
    with pytest.raises(ConfigError, match="tools_from is required"):
        parse_config({"server": {"type": "cli"}}, name="vei")


def test_config_cli_type_parses_tools_from_and_scopes():
    cfg = parse_config({
        "server": {"type": "cli", "tools_from": ["tools/vei-search.md"],
                   "scopes": ["shared", "lambda-core"]},
        "tools": {"allow": ["vei_*"]},
    }, name="vei")
    assert cfg.server.type == "cli"
    assert cfg.server.tools_from == ["tools/vei-search.md"]
    assert cfg.server.scopes == ["shared", "lambda-core"]
    assert cfg.server.launch_desc == "cli:1 sidecar(s)"
