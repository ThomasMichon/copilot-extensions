"""Cold helper execution must not change the admitted installer payload."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_install_engine_adoption import (
    PLUGIN, construction_bundle, environment, function,
)


def _files(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def _run(arguments, *, cwd, env, input=None):
    return subprocess.run(
        arguments, cwd=cwd, env=env, input=input, capture_output=True, text=True, timeout=25,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )


def _configuration_bundle(payload):
    scripts = payload / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("resolve-activation-role.py", "resolve_effective_config.py"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(PLUGIN / "scripts" / "configuration-layers", scripts / "configuration-layers", ignore=ignore)
    shutil.copytree(PLUGIN / "src", payload / "src", ignore=ignore)


def _repository(tmp_path, env):
    repository = tmp_path / "repository"
    repository.mkdir()
    result = _run(["git", "init", "-q", str(repository)], cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    return repository


def test_cold_runtime_helper_cli_does_not_write_into_its_payload(tmp_path):
    scripts = tmp_path / "payload" / "scripts"
    construction_bundle(scripts)
    before = _files(scripts)
    for _ in range(2):
        result = _run(
            [sys.executable, "-I", str(scripts / "versioned_runtime.py"),
             "--root", str(tmp_path / "runtime"), "construction-shell", "--shell", "ps1"],
            cwd=tmp_path, env=environment(tmp_path),
        )
        assert result.returncode == 0, result.stderr
        assert "Start-RuntimeConstruction" in result.stdout
        assert _files(scripts) == before


@pytest.mark.parametrize("entrypoint", ("session-writer", "pwsh", "powershell.exe", "bash"))
def test_cold_scope_binding_preserves_the_complete_payload(tmp_path, monkeypatch, entrypoint):
    from test_write_session_guidance import writer

    if entrypoint == "bash" and (os.name == "nt" or not shutil.which("bash")):
        pytest.skip("Native POSIX scope-binding entry required")
    shell = shutil.which(entrypoint) if entrypoint != "session-writer" else None
    if entrypoint != "session-writer" and not shell:
        pytest.skip("Native scope-binding shell unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "emit_scope_binding.py", "emit-scope-binding.ps1", "emit-scope-binding.sh",
    ):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    _configuration_bundle(payload)
    env = environment(tmp_path)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "owned-cache")
    repository = _repository(tmp_path, env)
    config = repository / ".agent-index" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        "indexers:\n  - machine: fixture\ncorpus:\n  sources:\n"
        "    - name: git:fixture\n      repo: fixture/repository\n",
        encoding="utf-8",
    )
    before = _files(payload)
    for _ in range(2):
        if entrypoint == "session-writer":
            monkeypatch.setattr(writer, "os", SimpleNamespace(environ=env, path=os.path))
            context = writer._scope_binding_context(payload, {"cwd": str(repository)})
        else:
            argv = [shell, str(scripts / "emit-scope-binding.sh")] if entrypoint == "bash" else [
                shell, "-NoProfile", "-File", str(scripts / "emit-scope-binding.ps1"),
            ]
            result = _run(argv, cwd=repository, env=env)
            assert result.returncode == 0, result.stdout + result.stderr
            context = json.loads(result.stdout)["additionalContext"]
        assert "agent-index:" in context
        assert _files(payload) == before


def test_cold_bash_role_probe_preserves_payload_for_real_completion(tmp_path):
    if os.name == "nt":
        git = shutil.which("git")
        shell = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        if shell is None or not shell.is_file():
            pytest.skip("Git Bash is unavailable; WSL is not a native Windows test lane")
    else:
        shell = shutil.which("bash")
        if not shell:
            pytest.skip("Bash is unavailable")
    scripts = tmp_path / "payload" / "scripts"
    construction_bundle(scripts)
    _configuration_bundle(scripts.parent)
    env = environment(tmp_path)
    repository = _repository(tmp_path, env)
    binary = tmp_path / "bin"
    binary.mkdir()
    wrapper = binary / "python3"
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(Path(sys._base_executable).as_posix())} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    env["PATH"] = str(binary) + os.pathsep + env["PATH"]
    env["AGENT_INDEX_REPO"] = repository.as_posix()
    env["AGENT_INDEX_MACHINE"] = "fixture-machine"
    probe = (
        "set -euo pipefail\n"
        f"SCRIPT_DIR={shlex.quote(scripts.as_posix())}\n"
        "_warn() { printf '%s\\n' \"$*\" >&2; }\n"
        + function("_activation_role", "sh").split("\n", 1)[1]
        + "\n_activation_role\n"
    )
    before = _files(scripts)
    result = _run([str(shell), "-c", probe], cwd=tmp_path, env=env)
    assert result.returncode == 0 and result.stdout == "unconfigured", result.stdout + result.stderr
    assert result.stderr == ""
    assert _files(scripts) == before
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    complete = _run(
        [sys.executable, "-I", str(scripts / "versioned_runtime.py"),
         "--root", str(tmp_path / "runtime"), "construction-session", "1.0.0",
         "--payload-hash", digest],
        cwd=tmp_path, env=env,
        input=json.dumps({"command": "complete", "payload_hash": digest}) + "\n"
        + json.dumps({"command": "settled"}) + "\n",
    )
    assert complete.returncode == 0, complete.stdout + complete.stderr
    assert [json.loads(line) for line in complete.stdout.splitlines()] == [
        {"admission": "construct"}, {"complete": True}, {"settled": True},
    ]
    assert _files(scripts) == before
    assert (tmp_path / "runtime" / "versions" / "1.0.0" / ".install-complete.json").is_file()


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer construction")
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_cold_installer_hash_survives_real_construction_and_role_resolution(tmp_path, host):
    shell = shutil.which(host)
    if not shell:
        pytest.skip("PowerShell is unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    construction_bundle(scripts)
    _configuration_bundle(payload)
    env = environment(tmp_path)
    repository = _repository(tmp_path, env)
    root = tmp_path / "runtime"
    script = scripts / "consumer.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:SourceRuntimeIdentity=$null\n"
        f"$PluginDir='{payload}'\n"
        f"$env:AGENT_INDEX_REPO='{repository}'\n"
        "$env:AGENT_INDEX_MACHINE='fixture-machine'\n"
        "function Write-Warn {param($Msg) throw $Msg}\n"
        + function("Get-PayloadHash", "ps1") + "\n"
        + function("Get-ActivationRole", "ps1") + "\n"
        f"$python='{sys._base_executable}'; $root='{root}'\n"
        f"$helper='{scripts / 'versioned_runtime.py'}'\n"
        "$before=Get-PayloadHash\n"
        "$adapter=(& $python -I $helper --root $root construction-shell --shell ps1 | Out-String)\n"
        "if($LASTEXITCODE -ne 0){throw 'adapter failed'}\n"
        "Invoke-Expression $adapter\n"
        "try {\n"
        "  if(Start-RuntimeConstruction -Python $python -Helper $helper -Root $root "
        "-Version '1.0.0' -PayloadHash $before -LinkName '.venv'){throw 'not cold'}\n"
        "  if((Get-ActivationRole) -ne 'unconfigured'){throw 'wrong role'}\n"
        "  $after=Get-PayloadHash\n"
        "  if($after -ne $before){throw 'read-only helpers changed admitted payload'}\n"
        "  Complete-RuntimeConstruction -PayloadHash $after\n"
        "} catch { [Console]::Error.WriteLine($_.ToString()); throw }\n"
        "finally { Stop-RuntimeConstruction }\n"
        "& $python -I $helper --root $root is-complete 1.0.0\n"
        "if($LASTEXITCODE -ne 0){throw 'completion did not publish'}\n",
        encoding="utf-8",
    )
    before = _files(scripts)
    result = _run([shell, "-NoProfile", "-File", str(script)], cwd=tmp_path, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _files(scripts) == before
    assert (root / "versions" / "1.0.0" / ".install-complete.json").is_file()
