"""Delegating shims retain path context without retaining the payload CWD."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "caller_cwd_generator", ROOT / "libs" / "payload-invocation" / "generate.py",
)
assert spec and spec.loader
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def _manifest(tmp_path: Path, caller: object = "EXAMPLE_CALLER_CWD") -> Path:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}', encoding="utf-8")
    (scripts / "dispatch.sh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$EXAMPLE_CALLER_CWD" "$PWD" "$@"\nexit 23\n',
        encoding="utf-8",
    )
    (scripts / "dispatch.ps1").write_text(
        "@($env:EXAMPLE_CALLER_CWD, (Get-Location).ProviderPath, $args) "
        "| ForEach-Object { $_ }\nexit 23\n", encoding="utf-8",
    )
    (scripts / "dispatch.sh").chmod(0o755)
    for suffix in ("sh", "ps1"):
        (scripts / f"install.{suffix}").write_text("", encoding="utf-8")
        (scripts / f"resolve-runtime.{suffix}").write_text("", encoding="utf-8")
    manifest = payload / "payload-invocation.json"
    manifest.write_text(json.dumps({
        "schema": generator.SCHEMA, "version": 1,
        "command": "agent-example", "module": "agent_example",
        "purpose": "Example command", "runtimeRoot": ".agent-example",
        "noSelfProvisionEnv": "EXAMPLE_NO_PROVISION",
        "callerCwdEnv": caller,
        "payloadDispatcher": {"posix": "scripts/dispatch.sh", "windows": "scripts/dispatch.ps1"},
    }), encoding="utf-8")
    return manifest


@pytest.mark.parametrize("value", [True, "invalid-name", "INVALID;ENV"])
def test_rejects_invalid_caller_environment(tmp_path: Path, value: object) -> None:
    with pytest.raises(ValueError, match="callerCwdEnv"):
        generator.load_manifest(_manifest(tmp_path, value))


@pytest.mark.parametrize("host", ["sh", "pwsh", "powershell.exe"])
def test_advertised_dispatcher_retains_original_cwd(tmp_path: Path, host: str) -> None:
    if host == "sh" and os.name == "nt":
        executable = Path(r"C:\Program Files\Git\bin\bash.exe")
        command = str(executable) if executable.is_file() else None
    else:
        command = shutil.which(host)
    if not command:
        pytest.skip(f"{host} unavailable")
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    caller = manifest.parent / "caller"
    caller.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    launcher = manifest.parent / "bin" / ("agent-example" + ("" if host == "sh" else ".ps1"))
    argv = [command, str(launcher)] if host == "sh" else [command, "-NoProfile", "-File", str(launcher)]
    result = subprocess.run(
        [*argv, "--config-file", "relative config.yaml"], cwd=caller,
        env={**os.environ, "COPILOT_PROJECT_DIR": str(outside), "EXAMPLE_CALLER_CWD": "stale"},
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 23, result.stderr
    original, actual, *arguments = result.stdout.splitlines()
    if host == "sh" and os.name == "nt":
        assert original.endswith("/payload/caller")
        assert actual.endswith("/outside")
    else:
        assert Path(original) == caller
        assert Path(actual) == outside
    assert arguments == ["--config-file", "relative config.yaml"]
