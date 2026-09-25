"""Tests for ``agent-mcp diagnose`` -- the staged connectivity check.

Reuses the minimal stdio MCP child from ``test_client.py`` for the success
path, and a deliberately-bad ``server.command`` for the transport-connect
failure path (no live network needed for either).
"""

from __future__ import annotations

import sys

from agent_mcp.config import parse_config
from agent_mcp.diagnose import diagnose

from .test_client import MCP_CHILD

# A stdio MCP child that answers initialize fine but returns a genuine
# JSON-RPC error for tools/list -- the regression case for the catalog
# stage: `fetch_all_tools` treats any non-"result" response as "no more
# pages" and returns an empty list, which must not be reported as a
# healthy empty catalog.
MCP_CHILD_BROKEN_CATALOG = r"""
import sys, json
def handle(m):
    mid = m.get("id"); method = m.get("method")
    if method == "initialize":
        return {"jsonrpc":"2.0","id":mid,"result":{
            "protocolVersion":"2025-06-18",
            "serverInfo":{"name":"broken-catalog","version":"1"},"capabilities":{}}}
    if method == "tools/list":
        return {"jsonrpc":"2.0","id":mid,"error":{"code":-32603,"message":"catalog backend down"}}
    if mid is not None:
        return {"jsonrpc":"2.0","id":mid,"result":{}}
    return None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    r = handle(json.loads(line))
    if r is not None:
        sys.stdout.write(json.dumps(r)+"\n"); sys.stdout.flush()
"""


def _cfg(extra: dict | None = None):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    }
    if extra:
        data.update(extra)
    return parse_config(data)


def _write_cfg(tmp_path, data: dict):
    import json as _json

    p = tmp_path / "fixture.json"
    p.write_text(_json.dumps(data), encoding="utf-8")
    return p


async def test_diagnose_success_runs_every_stage(tmp_path):
    path = _write_cfg(tmp_path, {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    })
    lines: list[str] = []
    report = await diagnose(str(path), printer=lines.append)

    assert report.ok
    assert [s.name for s in report.stages] == [
        "config", "auth", "transport-connect", "handshake", "catalog",
    ]
    assert all(s.ok for s in report.stages)
    assert report.tool_count == 3
    # Progress is genuinely staged, not one final line.
    assert any("config" in line and "OK" in line for line in lines)
    assert any("handshake" in line and "OK" in line for line in lines)
    assert any("catalog" in line and "OK" in line for line in lines)


async def test_diagnose_reports_config_stage_failure(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    report = await diagnose(str(bad), printer=lambda _line: None)

    assert not report.ok
    assert [s.name for s in report.stages] == ["config"]
    assert report.failed_stage.name == "config"


async def test_diagnose_reports_transport_connect_failure(tmp_path):
    # A stdio bridge whose command does not exist fails to spawn -- the
    # OneShotSession's stage is "transport-connect" at that point.
    path = _write_cfg(tmp_path, {
        "server": {"type": "stdio", "command": ["definitely-not-a-real-binary-xyz"]},
        "auth": {"kind": "none"},
    })
    report = await diagnose(str(path), printer=lambda _line: None)

    assert not report.ok
    failed = report.failed_stage
    assert failed is not None
    assert failed.name == "transport-connect"


async def test_diagnose_no_tools_skips_catalog_stage(tmp_path):
    path = _write_cfg(tmp_path, {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    })
    report = await diagnose(str(path), list_tools=False, printer=lambda _line: None)

    assert report.ok
    assert [s.name for s in report.stages] == ["config", "auth", "transport-connect", "handshake"]
    assert report.tool_count is None


def test_diagnose_report_to_dict_is_json_safe(tmp_path):
    import json

    path = _write_cfg(tmp_path, {
        "server": {"type": "stdio", "command": ["definitely-not-a-real-binary-xyz"]},
        "auth": {"kind": "none"},
    })
    import asyncio

    report = asyncio.run(diagnose(str(path), printer=lambda _line: None))
    # Must not raise -- source_path (a Path) must already be stringified.
    json.dumps(report.to_dict())


async def test_diagnose_reports_catalog_stage_failure_not_empty_success(tmp_path):
    # Regression: a tools/list JSON-RPC error must surface as a FAILED
    # catalog stage, never as a misleading "catalog: OK -- 0 tool(s))".
    path = _write_cfg(tmp_path, {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD_BROKEN_CATALOG]},
        "auth": {"kind": "none"},
    })
    report = await diagnose(str(path), printer=lambda _line: None)

    assert not report.ok
    failed = report.failed_stage
    assert failed is not None
    assert failed.name == "catalog"
    assert "catalog backend down" in failed.detail
    assert report.tool_count is None
