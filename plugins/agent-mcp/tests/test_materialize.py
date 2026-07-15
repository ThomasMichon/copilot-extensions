"""Tests for MCP->CLI materialization: rendering + the on-disk stub farm."""

from __future__ import annotations

import json
import os
import sys

import pytest

from agent_mcp.__main__ import main
from agent_mcp.config import parse_config
from agent_mcp.materialize import (
    DISPATCHER_NAME,
    MaterializedTool,
    build_manifest,
    plan_tools,
    render_index,
    render_sidecar,
    sanitize_stub,
    server_name_for,
    write_farm,
)

from .test_client import MCP_CHILD

TOOLS = [
    {"name": "create_issue", "description": "Open an issue.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}},
                     "required": ["title"]}},
    {"name": "list_issues", "description": "List issues.\nWith detail.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def test_sanitize_stub():
    assert sanitize_stub("create_issue") == "create_issue"
    assert sanitize_stub("weird name!!") == "weird-name"
    assert sanitize_stub("--dash--") == "dash"


def test_plan_tools_collision_suffix():
    tools = [{"name": "a b"}, {"name": "a/b"}]  # both sanitize to "a-b"
    plan = plan_tools(tools)
    stubs = [mt.stub for mt in plan]
    assert stubs == ["a-b", "a-b-2"]


def test_plan_tools_skips_nameless():
    plan = plan_tools([{"description": "no name"}, {"name": "ok"}])
    assert [mt.stub for mt in plan] == ["ok"]


def test_render_sidecar_plates_schema():
    mt = MaterializedTool("create_issue", "create_issue", TOOLS[0])
    doc = render_sidecar(mt, server="gitea", bridge_ref="/x/gitea.yaml")
    assert "# create_issue" in doc
    assert "Open an issue." in doc
    # Raw inputSchema is plated verbatim.
    assert '"title"' in doc
    assert '"required"' in doc
    # No flag synthesis -- documents the raw arguments form.
    assert "--request-file" in doc
    assert "raw MCP `arguments`" in doc.lower() or "raw mcp `arguments`" in doc.lower()
    # TS signature rendered.
    assert "interface Tool" in doc


def test_render_sidecar_structured_output_note():
    tool = {"name": "s", "description": "d",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}
    doc = render_sidecar(MaterializedTool("s", "s", tool), server="x", bridge_ref="b")
    assert "structured output schema" in doc
    assert '"ok"' in doc


def test_render_index_table():
    plan = plan_tools(TOOLS)
    idx = render_index("gitea", plan, bridge_ref="/x/gitea.yaml")
    assert "| `create_issue` | `create_issue` |" in idx
    # Newline in a description is collapsed to the first line.
    assert "With detail." not in idx
    assert "List issues." in idx


def test_build_manifest():
    plan = plan_tools(TOOLS)
    m = build_manifest("gitea", plan, bridge_ref="/x/gitea.yaml", version="9.9")
    assert m["server"] == "gitea"
    assert m["bridge"] == "/x/gitea.yaml"
    assert m["tools"]["create_issue"] == {"tool": "create_issue"}


def test_server_name_for():
    cfg = parse_config({"server": {"type": "http", "url": "https://api.example.com/mcp"}})
    assert server_name_for(cfg) == "api.example.com"
    assert server_name_for(cfg, "custom") == "custom"


def test_server_name_for_npm_uses_package():
    # In npm mode the namespace is the package (e.g. "gitea-mcp"), not the runner.
    cfg = parse_config({"server": {"type": "stdio", "npm": "gitea-mcp"}})
    assert server_name_for(cfg) == "gitea-mcp"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_write_farm_posix(tmp_path):
    plan = plan_tools(TOOLS)
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan, server="gitea", bridge_ref="/x/g.yaml", version="1.0")

    dispatcher = server_dir / "bin" / DISPATCHER_NAME
    assert dispatcher.is_file()
    assert os.access(dispatcher, os.X_OK)

    link = server_dir / "bin" / "create_issue"
    assert link.is_symlink()
    assert os.readlink(link) == DISPATCHER_NAME

    assert (server_dir / "doc" / "create_issue.md").is_file()
    manifest = json.loads((server_dir / "manifest.json").read_text())
    assert manifest["tools"]["list_issues"] == {"tool": "list_issues"}
    assert (server_dir / "index.md").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_write_farm_atomic_rebuild(tmp_path):
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan_tools(TOOLS), server="gitea",
               bridge_ref="b", version="1.0")
    # Re-materialize with a smaller catalog: stale stubs must be gone.
    write_farm(server_dir, plan_tools([TOOLS[0]]), server="gitea",
               bridge_ref="b", version="1.0")
    assert (server_dir / "bin" / "create_issue").exists()
    assert not (server_dir / "bin" / "list_issues").exists()
    assert not (server_dir / "doc" / "list_issues.md").exists()


def test_write_farm_windows_shims(tmp_path):
    plan = plan_tools(TOOLS)
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan, server="gitea", bridge_ref="b", version="1.0",
               windows=True)
    bin_dir = server_dir / "bin"
    assert (bin_dir / "create_issue.ps1").is_file()
    assert (bin_dir / "create_issue.cmd").is_file()
    # No POSIX dispatcher/symlinks in the Windows farm.
    assert not (bin_dir / DISPATCHER_NAME).exists()
    assert not (bin_dir / "create_issue").is_symlink()
    ps1 = (bin_dir / "create_issue.ps1").read_text()
    assert "#Requires -Version 7.0" in ps1
    assert "agent-mcp call" in ps1


def _write_cfg(tmp_path):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    }
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_materialize_verb_then_stub_call(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    dest = tmp_path / "materialized"
    rc = main(["materialize", str(cfg), "--server-name", "fix", "--dest", str(dest)])
    assert rc == 0
    server_dir = dest / "fix"
    assert (server_dir / "bin" / "greet").is_symlink()

    manifest = server_dir / "manifest.json"
    capsys.readouterr()  # drain
    rc = main(["call", "--manifest", str(manifest), "--stub", "greet",
               '{"name": "materialized"}'])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "hello materialized"
