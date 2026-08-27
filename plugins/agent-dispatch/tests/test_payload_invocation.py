"""Payload-local invocation and explicit management-boundary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_dispatch_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 1,
        "command": "agent-dispatch",
        "module": "agent_dispatch",
        "runtimeRoot": ".agent-dispatch",
        "noSelfProvisionEnv": "AGENT_DISPATCH_NO_SELFPROVISION",
        "purpose": "Coordinate queued agent work and task lifecycles",
        "installer": "install",
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
    }

    posix = (PLUGIN / "bin" / "agent-dispatch").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-dispatch.ps1").read_text(
        encoding="utf-8"
    )
    assert 'bash "$_installer" provision' in posix
    assert "payload-dir" not in posix
    assert "$_installer provision" in powershell
    assert "payload-dir" not in powershell


def test_session_catalog_hook_is_payload_root_aware_and_fail_open() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    assert all(
        "COPILOT_PLUGIN_ROOT" in hook["bash"]
        and "COPILOT_PLUGIN_ROOT" in hook["powershell"]
        for hook in session_hooks
    )
    catalog_hooks = [
        hook
        for hook in session_hooks
        if "emit-command-catalog" in hook["bash"]
        and "emit-command-catalog" in hook["powershell"]
    ]
    assert len(catalog_hooks) == 1
    assert "else printf '{}'" in catalog_hooks[0]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in catalog_hooks[0]["powershell"]

    powershell_catalog = (
        PLUGIN / "scripts" / "emit-command-catalog.ps1"
    ).read_text(encoding="utf-8")
    assert r"bin\agent-dispatch.cmd" in powershell_catalog
    assert "shell = 'cmd'" in powershell_catalog


def test_out_of_session_boundaries_remain_explicit() -> None:
    pivot = json.loads(
        (PLUGIN / "pivots" / "agent-dispatch.json").read_text(encoding="utf-8")
    )
    assert pivot["list"][0] == "agent-dispatch-board"
    actions = {action["key"]: action for action in pivot["actions"]}
    assert actions["steer"]["run"][0] == "agent-dispatch"
    assert actions["abandon"]["run"][0] == "agent-dispatch"

    skill = (PLUGIN / "skills" / "agent-dispatch" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert (
        '{"command": "agent-dispatch", "args": ["mcp"]}'
        in skill
    )
    assert "marketplace-isolation: allow mcp-server-startup" in skill
    assert "ssh Y agent-dispatch show <id>" in skill
    assert "marketplace-isolation: allow remote-management" in skill


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-dispatch"
    python = runtime / "versions" / "test" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (runtime / "current-version").write_text("test\n", encoding="utf-8")

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-dispatch"
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
        [str(PLUGIN / "bin" / "agent-dispatch"), "create", "example"],
        input="payload",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "-m agent_dispatch create example|payload"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_preserves_native_stdin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmd = bin_dir / "agent-dispatch.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-dispatch.cmd", cmd)
    (bin_dir / "agent-dispatch.ps1").write_text(
        "$body = [Console]::In.ReadToEnd()\n"
        "[Console]::Out.Write(($args -join '|') + '::' + $body)\n",
        encoding="utf-8",
    )
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    result = subprocess.run(
        [comspec, "/d", "/s", "/c", f"{cmd} payload --file -"],
        input="task body",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "payload|--file|-::task body"
