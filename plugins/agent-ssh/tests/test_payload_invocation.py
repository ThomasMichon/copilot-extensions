"""Payload-local invocation tests for agent-ssh compatibility wrappers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_compatibility_wrappers_do_not_reference_global_binstub() -> None:
    for name in ("emit-profile.sh", "verify.sh", "emit-profile.ps1", "verify.ps1"):
        text = (PLUGIN / "scripts" / name).read_text(encoding="utf-8")
        assert ".local/bin" not in text
        assert r".local\bin" not in text
        assert "bin/agent-ssh" in text or r"bin\agent-ssh.ps1" in text


def test_payload_manifest_describes_installation_context_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-ssh",
        "module": "agent_ssh",
        "legacyRuntimeRoot": ".agent-ssh",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_SSH_NO_SELFPROVISION",
        "purpose": "Emit inspect and verify SSH fabric profiles",
        "payloadRootEnv": "AGENT_SSH_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }
    posix = (PLUGIN / "bin" / "agent-ssh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-ssh.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in posix
    assert "payload-dir" not in powershell


def test_bootstrap_stands_down_for_explicit_installation_context() -> None:
    sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(encoding="utf-8")

    assert 'if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then' in sh
    assert "if ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }" in ps1
    assert sh.index('\nif [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then') < sh.index(
        '\nInstallDir="$HOME/.agent-ssh"'
    )
    assert ps1.index("\nif ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }") < ps1.index(
        "\n$InstallDir = Join-Path $env:USERPROFILE '.agent-ssh'"
    )


def test_runtime_gates_keep_first_use_provisioning_lock() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(encoding="utf-8")
    assert ".provision.lock" in posix
    assert ".provision.lock" in powershell


def _fake_runtime_gate_payload(root: Path, *, windows: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"name": "agent-ssh", "version": "test"}),
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
        (scripts / "install.ps1").write_text(
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
        (scripts / "install.sh").write_text(
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
            scripts / "install.sh",
            subdir / "installation-context.sh",
        ):
            path.chmod(0o755)
    return root


def _malformed_config_dir(root: Path) -> Path:
    config_d = root / "config.d"
    config_d.mkdir(parents=True, exist_ok=True)
    (config_d / "50-agent-ssh-broken.conf").write_text(
        "Host missing-managed-header\n",
        encoding="utf-8",
    )
    return config_d


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper test")
@pytest.mark.parametrize(
    ("script_name", "subcommand"),
    (("emit-profile.sh", "emit-profile"), ("verify.sh", "verify")),
)
def test_posix_compatibility_wrapper_uses_own_payload_command(
    tmp_path: Path,
    script_name: str,
    subcommand: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    bin_dir = payload / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(PLUGIN / "scripts" / script_name, scripts / script_name)

    marker = tmp_path / "payload-called"
    payload_command = bin_dir / "agent-ssh"
    payload_command.write_text(
        f'#!/bin/sh\nprintf "%s" "$*" > "{marker}"\n',
        encoding="utf-8",
    )
    payload_command.chmod(0o755)

    home = tmp_path / "home"
    global_bin = home / ".local" / "bin"
    global_bin.mkdir(parents=True)
    shadow_marker = tmp_path / "global-called"
    shadow = global_bin / "agent-ssh"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    result = subprocess.run(
        ["bash", str(scripts / script_name), "two words"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == f"{subcommand} two words"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows wrapper test")
@pytest.mark.parametrize(
    ("script_name", "subcommand"),
    (("emit-profile.ps1", "emit-profile"), ("verify.ps1", "verify")),
)
def test_windows_compatibility_wrapper_uses_own_payload_command(
    tmp_path: Path,
    script_name: str,
    subcommand: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    bin_dir = payload / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(PLUGIN / "scripts" / script_name, scripts / script_name)

    marker = tmp_path / "payload-called"
    (bin_dir / "agent-ssh.ps1").write_text(
        f"[IO.File]::WriteAllText('{marker}', ($args -join ' '))\n",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    global_bin = home / ".local" / "bin"
    global_bin.mkdir(parents=True)
    shadow_marker = tmp_path / "global-called"
    (global_bin / "agent-ssh.ps1").write_text(
        f"[IO.File]::WriteAllText('{shadow_marker}', 'called')\nexit 99\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(scripts / script_name),
            "two words",
        ],
        env={**os.environ, "USERPROFILE": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == f"{subcommand} two words"
    assert not shadow_marker.exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX runtime-gate test",
)
def test_posix_runtime_gate_selects_cell_local_home(tmp_path: Path) -> None:
    runtime_root = tmp_path / "cell-a" / "plugins" / "agent-ssh"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-ssh",
        windows=False,
    )
    other_runtime = tmp_path / "cell-b" / "plugins" / "agent-ssh"
    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "AGENT_SSH_PAYLOAD_ROOT": str(payload),
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
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "bash",
            str(payload / "scripts" / "runtime-gate.sh"),
            "verify",
            "--config-d",
            str(_malformed_config_dir(tmp_path)),
            "host-a",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert (runtime_root / "fragment-warning-state.json").is_file()
    assert not (other_runtime / "fragment-warning-state.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows runtime-gate test")
def test_windows_runtime_gate_selects_cell_local_home(tmp_path: Path) -> None:
    runtime_root = tmp_path / "cell-a" / "plugins" / "agent-ssh"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-ssh",
        windows=True,
    )
    other_runtime = tmp_path / "cell-b" / "plugins" / "agent-ssh"
    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "AGENT_SSH_PAYLOAD_ROOT": str(payload),
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

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "runtime-gate.ps1"),
            "verify",
            "--config-d",
            str(_malformed_config_dir(tmp_path)),
            "host-a",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert (runtime_root / "fragment-warning-state.json").is_file()
    assert not (other_runtime / "fragment-warning-state.json").exists()


@pytest.mark.parametrize(
    ("command", "status_payload", "expected"),
    [
        (
            "bash",
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
            "requested installation context is not active",
        ),
        (
            "pwsh",
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
            "active installation context is incomplete",
        ),
    ],
)
def test_runtime_gate_rejects_invalid_or_spoofed_context(
    tmp_path: Path,
    command: str,
    status_payload: dict[str, object],
    expected: str,
) -> None:
    if command == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is unavailable")
    if command == "bash" and os.name == "nt":
        pytest.skip("POSIX runtime-gate test")
    if command == "pwsh" and os.name != "nt":
        pytest.skip("native Windows runtime-gate test")

    runtime_root = tmp_path / "cell" / "plugins" / "agent-ssh"
    windows = command == "pwsh"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-ssh",
        windows=windows,
    )
    context = runtime_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "AGENT_SSH_PAYLOAD_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(status_payload),
            "TEST_VALIDATE_JSON": json.dumps({"generation": 3}),
        }
    )
    if not windows:
        env["HOME"] = str(tmp_path / "home")
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    invocation = (
        [command, str(payload / "scripts" / "runtime-gate.sh"), "doctor", "--json"]
        if not windows
        else [
            command,
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "runtime-gate.ps1"),
            "doctor",
            "--json",
        ]
    )
    result = subprocess.run(
        invocation,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert expected in result.stderr
