"""Payload-local invocation and explicit management-boundary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_bridge.session_host import launcher

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_bridge_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 1,
        "command": "agent-bridge",
        "module": "agent_bridge",
        "runtimeRoot": ".agent-bridge",
        "noSelfProvisionEnv": "AGENT_BRIDGE_NO_SELFPROVISION",
        "purpose": "Communicate with persistent agent sessions",
        "installer": "install",
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
    }

    posix = (PLUGIN / "bin" / "agent-bridge").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-bridge.ps1").read_text(encoding="utf-8")
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
    assert "else printf '{}'" in session_hooks[0]["bash"]
    assert "else { [Console]::Out.Write('{}') }" in session_hooks[0]["powershell"]

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
    assert r"bin\agent-bridge.cmd" in powershell_catalog
    assert "shell = 'cmd'" in powershell_catalog


def test_out_of_session_boundaries_remain_explicit() -> None:
    pivot = json.loads(
        (PLUGIN / "pivots" / "agent-bridge.json").read_text(encoding="utf-8")
    )
    assert pivot["list"][0] == "agent-bridge"

    provider = (
        PLUGIN / "src" / "agent_bridge" / "session_host" / "spawner.py"
    ).read_text(encoding="utf-8")
    assert 'shutil.which("agent-codespaces")' in provider
    assert "marketplace-isolation: allow provider-management" in provider

    cli_reference = (
        PLUGIN
        / "skills"
        / "agent-bridge"
        / "references"
        / "cli-commands.md"
    ).read_text(encoding="utf-8")
    assert "agent-bridge service start" in cli_reference
    assert "marketplace-isolation: allow service-management" in cli_reference

    extension = (
        PLUGIN / "extensions" / "agent-bridge" / "extension.mjs"
    ).read_text(encoding="utf-8")
    assert "agent-bridge session command catalog" in extension
    assert "`agent-bridge send <reply-to>" not in extension


@pytest.mark.asyncio
async def test_session_host_strips_parent_payload_context(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_spawn(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return object()

    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/parent/payload")
    monkeypatch.setattr(launcher.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(launcher, "child_preexec", lambda: None)

    await launcher._spawn_child(
        ["copilot"],
        None,
        {"COPILOT_PLUGIN_ROOT": "/explicit/payload", "KEEP": "yes"},
    )

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "COPILOT_PLUGIN_ROOT" not in child_env
    assert child_env["KEEP"] == "yes"


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-bridge"
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
    shadow = shadow_bin / "agent-bridge"
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
        [str(PLUGIN / "bin" / "agent-bridge"), "create", "example"],
        input="prompt",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "-m agent_bridge create example|prompt"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_preserves_native_stdin(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin"
    bin_dir = plugin / "bin"
    bin_dir.mkdir(parents=True)
    cmd = bin_dir / "agent-bridge.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.cmd", cmd)
    shutil.copy2(PLUGIN / "bin" / "agent-bridge.ps1", bin_dir)
    shutil.copy2(PLUGIN / "plugin.json", plugin)

    scripts = plugin / "scripts"
    scripts.mkdir()
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = Join-Path (Split-Path -Parent $PSScriptRoot) "
        "'fake-python.cmd'\n",
        encoding="utf-8",
    )
    (plugin / "fake-python.cmd").write_text(
        '@echo off\r\n<nul set /p "=%*::"\r\nmore\r\nexit /b 0\r\n',
        encoding="utf-8",
    )

    comspec = os.environ.get("COMSPEC", "cmd.exe")
    env = {
        **os.environ,
        "COPILOT_PLUGIN_ROOT": str(plugin),
    }
    result = subprocess.run(
        [
            comspec, "/d", "/s", "/c", str(cmd),
            "create", "target", "--prompt-file", "-",
        ],
        input="task body",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.replace("\r\n", "\n") == (
        "-m agent_bridge create target --prompt-file -::task body\n"
    )
