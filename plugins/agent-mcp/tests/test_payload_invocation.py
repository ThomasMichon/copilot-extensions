"""Payload-local invocation and explicit compatibility-boundary tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_mcp_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-mcp",
        "module": "agent_mcp",
        "legacyRuntimeRoot": ".agent-mcp",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_MCP_NO_SELFPROVISION",
        "purpose": "Wrap authenticate and materialize MCP servers",
        "installer": "init",
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
        "payloadRootEnv": "AGENT_MCP_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }
    posix = (PLUGIN / "bin" / "agent-mcp").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-mcp.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in posix
    assert "payload-dir" not in powershell


def test_session_catalog_producer_is_not_registered_as_a_hook() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    assert not any("emit-command-catalog" in str(hook) for hook in session_hooks)

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


def test_runtime_gates_keep_first_use_provisioning_lock() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(encoding="utf-8")
    assert ".provision.lock" in posix
    assert ".provision.lock" in powershell


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
    payload = _fake_runtime_gate_payload(
        tmp_path / "payload",
        windows=False,
        runtime_root=runtime,
    )
    (payload / "bin").mkdir(parents=True, exist_ok=True)
    (payload / "bin" / "agent-mcp").write_text(
        (PLUGIN / "bin" / "agent-mcp").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (payload / "bin" / "agent-mcp").chmod(0o755)

    python = tmp_path / "runtime-python"
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)

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
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
            "TEST_AGENT_RT_PY": str(python),
            "TEST_STATUS_JSON": json.dumps(
                {
                    "status": "ready",
                    "reason": "policy-default-false",
                    "actualMode": "legacy",
                    "desiredMode": "legacy",
                    "runtimeRoot": str(runtime),
                    "context": None,
                    "installGeneration": None,
                    "policy": {"enabled": False},
                    "legacy": {"tombstone": None, "disposition": "active"},
                }
            ),
            "TEST_VALIDATE_JSON": json.dumps({"generation": 0}),
        }
    )
    result = subprocess.run(
        [str(payload / "bin" / "agent-mcp"), "call", "bridge", "tool"],
        input='{"value":1}',
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == '-m agent_mcp call bridge tool|{"value":1}'
    assert not shadow_marker.exists()


def _fake_runtime_gate_payload(
    root: Path,
    *,
    windows: bool,
    runtime_root: Path,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"name": "agent-mcp", "version": "test"}),
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    subdir = scripts / "installation-context"
    subdir.mkdir()
    suffix = "ps1" if windows else "sh"
    (scripts / f"runtime-gate.{suffix}").write_text(
        (PLUGIN / "scripts" / f"runtime-gate.{suffix}").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if windows:
        (scripts / "resolve-runtime.ps1").write_text(
            "$AgentRtPy = $env:TEST_AGENT_RT_PY\n",
            encoding="utf-8",
        )
        (scripts / "init.ps1").write_text(
            "throw 'installer should not run in this test'\n",
            encoding="utf-8",
        )
        (subdir / "installation-context.ps1").write_text(
            "$action = $args[0]\n"
            "switch ($action) {\n"
            "  'status' { [Console]::Out.Write($env:TEST_STATUS_JSON); exit 0 }\n"
            "  'validate' {\n"
            "    if ($env:TEST_VALIDATE_EXIT) { exit [int]$env:TEST_VALIDATE_EXIT }\n"
            "    [Console]::Out.Write($env:TEST_VALIDATE_JSON)\n"
            "    exit 0\n"
            "  }\n"
            "}\n"
            "exit 1\n",
            encoding="utf-8",
        )
    else:
        (scripts / "resolve-runtime.sh").write_text(
            '#!/usr/bin/env bash\nAGENT_RT_PY="$TEST_AGENT_RT_PY"\n',
            encoding="utf-8",
        )
        (scripts / "init.sh").write_text(
            "#!/usr/bin/env bash\nprintf 'installer should not run in this test\\n' >&2\nexit 1\n",
            encoding="utf-8",
        )
        (subdir / "json-query.awk").write_text(
            (PLUGIN / "scripts" / "installation-context" / "json-query.awk").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        (subdir / "installation-context.sh").write_text(
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            "  status) printf '%s' \"$TEST_STATUS_JSON\" ;;\n"
            "  validate)\n"
            "    if [ -n \"${TEST_VALIDATE_EXIT:-}\" ]; then exit \"$TEST_VALIDATE_EXIT\"; fi\n"
            "    printf '%s' \"$TEST_VALIDATE_JSON\"\n"
            "    ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        for path in (
            scripts / "runtime-gate.sh",
            scripts / "resolve-runtime.sh",
            scripts / "init.sh",
            subdir / "installation-context.sh",
        ):
            path.chmod(0o755)
    runtime_root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.skipif(os.name == "nt", reason="POSIX runtime-gate test")
def test_posix_runtime_gate_selects_cell_local_home_and_marketplace(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "cell-a" / "plugins" / "agent-mcp"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-mcp",
        windows=False,
        runtime_root=runtime_root,
    )
    other_payload = _fake_runtime_gate_payload(
        tmp_path / "market-b" / "agent-mcp",
        windows=False,
        runtime_root=tmp_path / "cell-b" / "plugins" / "agent-mcp",
    )
    for market, marker in ((payload.parent, "a"), (other_payload.parent, "b")):
        agents = market / "ado-data" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "demo.mcp.yaml").write_text(
            json.dumps({"server": {"type": "http", "url": f"https://{marker}.example"}}),
            encoding="utf-8",
        )

    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "AGENT_MCP_PAYLOAD_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "runtimeRoot": str(runtime_root),
                    "context": str(context),
                    "installGeneration": 7,
                    "policy": {"enabled": True},
                    "legacy": {"tombstone": None, "disposition": "inactive"},
                }
            ),
            "TEST_VALIDATE_JSON": json.dumps({"generation": 7}),
        }
    )

    status = subprocess.run(
        ["bash", str(payload / "scripts" / "runtime-gate.sh"), "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert f"bridges dir: {runtime_root / 'bridges'}" in status.stdout
    assert str(other_payload.parent) not in status.stdout

    validate = subprocess.run(
        ["bash", str(payload / "scripts" / "runtime-gate.sh"), "validate", "demo"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr
    assert str(payload.parent / "ado-data" / "agents" / "demo.mcp.yaml") in validate.stdout
    assert str(other_payload.parent / "ado-data" / "agents" / "demo.mcp.yaml") not in validate.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX runtime-gate test")
@pytest.mark.parametrize(
    ("status_payload", "validate_payload", "validate_exit", "expected"),
    [
        (
            {
                "status": "ready",
                "reason": "policy-default-false",
                "actualMode": "legacy",
                "desiredMode": "legacy",
                "runtimeRoot": None,
                "context": None,
                "installGeneration": None,
                "policy": {"enabled": False},
                "legacy": {"tombstone": None, "disposition": "active"},
            },
            None,
            None,
            "requested installation context is not active",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": None,
                "context": None,
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            None,
            None,
            "active installation context is incomplete",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": "RUNTIME",
                "context": "CONTEXT",
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            {"generation": 2},
            None,
            "installation context generation does not match governance",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": "RUNTIME",
                "context": "CONTEXT",
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            {"generation": 3},
            1,
            "installation context validation failed",
        ),
    ],
)
def test_posix_runtime_gate_rejects_invalid_or_spoofed_context(
    tmp_path: Path,
    status_payload: dict[str, object],
    validate_payload: dict[str, object] | None,
    validate_exit: int | None,
    expected: str,
) -> None:
    runtime_root = tmp_path / "cell" / "plugins" / "agent-mcp"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-mcp",
        windows=False,
        runtime_root=runtime_root,
    )
    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")

    status_data = dict(status_payload)
    if status_data.get("runtimeRoot") == "RUNTIME":
        status_data["runtimeRoot"] = str(runtime_root)
    if status_data.get("context") == "CONTEXT":
        status_data["context"] = str(context)

    env = os.environ.copy()
    env.update(
        {
            "AGENT_MCP_PAYLOAD_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(status_data),
            "TEST_VALIDATE_JSON": json.dumps(validate_payload or {"generation": 3}),
        }
    )
    if validate_exit is not None:
        env["TEST_VALIDATE_EXIT"] = str(validate_exit)

    result = subprocess.run(
        ["bash", str(payload / "scripts" / "runtime-gate.sh"), "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 126
    assert expected in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows runtime-gate test")
def test_windows_runtime_gate_selects_cell_local_home_and_marketplace(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "cell-a" / "plugins" / "agent-mcp"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-mcp",
        windows=True,
        runtime_root=runtime_root,
    )
    other_payload = _fake_runtime_gate_payload(
        tmp_path / "market-b" / "agent-mcp",
        windows=True,
        runtime_root=tmp_path / "cell-b" / "plugins" / "agent-mcp",
    )
    for market, marker in ((payload.parent, "a"), (other_payload.parent, "b")):
        agents = market / "ado-data" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "demo.mcp.yaml").write_text(
            json.dumps({"server": {"type": "http", "url": f"https://{marker}.example"}}),
            encoding="utf-8",
        )

    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "AGENT_MCP_PAYLOAD_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "runtimeRoot": str(runtime_root),
                    "context": str(context),
                    "installGeneration": 7,
                    "policy": {"enabled": True},
                    "legacy": {"tombstone": None, "disposition": "inactive"},
                }
            ),
            "TEST_VALIDATE_JSON": json.dumps({"generation": 7}),
        }
    )

    status = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(payload / "scripts" / "runtime-gate.ps1"), "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert f"bridges dir: {runtime_root / 'bridges'}" in status.stdout
    assert str(other_payload.parent) not in status.stdout

    validate = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(payload / "scripts" / "runtime-gate.ps1"), "validate", "demo"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr
    assert str(payload.parent / "ado-data" / "agents" / "demo.mcp.yaml") in validate.stdout
    assert str(other_payload.parent / "ado-data" / "agents" / "demo.mcp.yaml") not in validate.stdout


@pytest.mark.skipif(os.name != "nt", reason="native Windows runtime-gate test")
@pytest.mark.parametrize(
    ("status_payload", "validate_payload", "validate_exit", "expected"),
    [
        (
            {
                "status": "ready",
                "reason": "policy-default-false",
                "actualMode": "legacy",
                "desiredMode": "legacy",
                "runtimeRoot": None,
                "context": None,
                "installGeneration": None,
                "policy": {"enabled": False},
                "legacy": {"tombstone": None, "disposition": "active"},
            },
            None,
            None,
            "requested installation context is not active",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": None,
                "context": None,
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            None,
            None,
            "active installation context is incomplete",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": "RUNTIME",
                "context": "CONTEXT",
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            {"generation": 2},
            None,
            "installation context generation does not match governance",
        ),
        (
            {
                "status": "ready",
                "reason": "namespaced-active",
                "actualMode": "namespaced",
                "desiredMode": "namespaced",
                "runtimeRoot": "RUNTIME",
                "context": "CONTEXT",
                "installGeneration": 3,
                "policy": {"enabled": True},
                "legacy": {"tombstone": None, "disposition": "inactive"},
            },
            {"generation": 3},
            1,
            "installation context validation failed",
        ),
    ],
)
def test_windows_runtime_gate_rejects_invalid_or_spoofed_context(
    tmp_path: Path,
    status_payload: dict[str, object],
    validate_payload: dict[str, object] | None,
    validate_exit: int | None,
    expected: str,
) -> None:
    runtime_root = tmp_path / "cell" / "plugins" / "agent-mcp"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-mcp",
        windows=True,
        runtime_root=runtime_root,
    )
    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")

    status_data = dict(status_payload)
    if status_data.get("runtimeRoot") == "RUNTIME":
        status_data["runtimeRoot"] = str(runtime_root)
    if status_data.get("context") == "CONTEXT":
        status_data["context"] = str(context)

    env = os.environ.copy()
    env.update(
        {
            "AGENT_MCP_PAYLOAD_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(status_data),
            "TEST_VALIDATE_JSON": json.dumps(validate_payload or {"generation": 3}),
        }
    )
    if validate_exit is not None:
        env["TEST_VALIDATE_EXIT"] = str(validate_exit)

    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(payload / "scripts" / "runtime-gate.ps1"), "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 126
    assert expected in result.stderr
