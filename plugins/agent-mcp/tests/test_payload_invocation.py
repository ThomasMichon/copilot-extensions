"""Payload-local invocation and explicit compatibility-boundary tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_mcp_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 1,
        "command": "agent-mcp",
        "module": "agent_mcp",
        "runtimeRoot": ".agent-mcp",
        "noSelfProvisionEnv": "AGENT_MCP_NO_SELFPROVISION",
        "purpose": "Wrap authenticate and materialize MCP servers",
        "installer": "init",
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
    }
    posix = (PLUGIN / "bin" / "agent-mcp").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-mcp.ps1").read_text(encoding="utf-8")
    assert 'bash "$_installer" provision' in posix
    assert "payload-dir" not in posix
    assert "$_installer provision" in powershell
    assert "payload-dir" not in powershell


def test_session_catalog_hook_is_payload_root_aware() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    catalog_hooks = [
        hook
        for hook in session_hooks
        if "emit-command-catalog" in hook["bash"]
        and "emit-command-catalog" in hook["powershell"]
    ]
    assert len(catalog_hooks) == 1
    assert "COPILOT_PLUGIN_ROOT" in catalog_hooks[0]["bash"]
    assert "COPILOT_PLUGIN_ROOT" in catalog_hooks[0]["powershell"]
    assert "else printf '{}'" in catalog_hooks[0]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in catalog_hooks[0]["powershell"]

    powershell_catalog = (
        PLUGIN / "scripts" / "emit-command-catalog.ps1"
    ).read_text(encoding="utf-8")
    assert r"bin\agent-mcp.cmd" in powershell_catalog
    assert "shell = 'cmd'" in powershell_catalog


def test_static_mcp_server_commands_remain_explicit_startup_boundaries() -> None:
    command_lines = [
        (path, line)
        for path in PLUGIN.rglob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*command:\s*agent-mcp(?:\s|$)", line)
    ]
    assert command_lines
    assert all(
        "marketplace-isolation: allow mcp-server-startup" in line
        for _path, line in command_lines
    )


def test_materialized_stubs_remain_explicit_management_boundaries() -> None:
    source = (
        PLUGIN / "src" / "agent_mcp" / "materialize.py"
    ).read_text(encoding="utf-8")
    launch_lines = [
        line
        for line in source.splitlines()
        if "marketplace-isolation: allow materialized-stub-management" in line
    ]
    assert len(launch_lines) == 3
    assert all(
        re.search(r"""["'](?:exec |& )?agent-mcp (?:call )?""", line)
        for line in launch_lines
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer test")
def test_posix_bootstrap_stamps_the_compatibility_wrapper(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    command = ["bash", str(PLUGIN / "scripts" / "bootstrap-check.sh")]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    runtime = home / ".agent-mcp"
    payload_dir = Path(
        (runtime / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert payload_dir == PLUGIN
    assert (home / ".local" / "bin" / "agent-mcp").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-mcp"
    python = runtime / "versions" / "test" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (python.parents[1] / ".install-complete.json").write_text(
        (
            '{"version": "test", '
            '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
        ),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("test\n", encoding="utf-8")

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-mcp"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(PLUGIN / "bin" / "agent-mcp"), "call", "bridge", "tool"],
        input='{"value":1}',
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == '-m agent_mcp call bridge tool|{"value":1}'
    assert not shadow_marker.exists()
