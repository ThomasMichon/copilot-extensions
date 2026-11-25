"""Actual command gates admit prepared applications without bootstrap acquisition."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from . import source_publication_fixture as fixture
from .test_managed_bootstrap_reader import prepared_managed_helper as prepared_managed_helper
from .test_managed_bootstrap_reader import find_prepared_managed_helper

SCENARIOS = (
    "payload-current", "global-current", "payload-lkg", "global-newest",
    "payload-namespaced", "global-board", "payload-telemetry", "payload-readiness",
    "global-missing-uv", "payload-missing-provider", "global-missing-capture",
    "telemetry-missing-uv",
)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture(scope="module")
def ready_command_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("dispatch-ready-payload") / "agent-dispatch"
    shutil.copytree(
        fixture.SCRIPTS, payload / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(fixture.PLUGIN / "bin", payload / "bin")
    for name in ("plugin.json", "payload-invocation.json", "readiness-isolation.json"):
        shutil.copy2(fixture.PLUGIN / name, payload / name)
    stage_command_admission_support(payload / "scripts")
    return payload


def stage_command_admission_support(scripts: Path) -> None:
    producer = runpy.run_path(str(fixture.REPO / "libs/payload-invocation/bootstrap_python.py"))
    outputs = producer["expected_files"](
        fixture.PLUGIN / "payload-invocation.json",
        json.loads((fixture.PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")),
        canonical_root=fixture.REPO,
    )
    for source, content in outputs.items():
        path = scripts / source.relative_to(fixture.SCRIPTS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


@pytest.fixture(scope="module")
def ready_application_python(prepared_managed_helper: tuple[Path, Path, Path]) -> Path:
    uv, _, managed_root = prepared_managed_helper
    result = subprocess.run(
        [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        capture_output=True, text=True, timeout=15, creationflags=fixture.HEADLESS,
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(managed_root)},
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    return Path(result.stdout.strip()).resolve()


def test_prepared_reader_finds_native_store_without_environment_override(
    monkeypatch: pytest.MonkeyPatch, prepared_managed_helper: tuple[Path, Path, Path],
) -> None:
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    monkeypatch.setattr(sys, "_base_executable", str(prepared_managed_helper[1]))
    assert find_prepared_managed_helper() == prepared_managed_helper


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ready_command_admits_managed_application_without_acquisition(
    tmp_path: Path, shell: str, scenario: str, ready_command_payload: Path,
    ready_application_python: Path, prepared_managed_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    host = shutil.which(shell)
    if not host or shell == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, bootstrap, managed_root = prepared_managed_helper
    assert bootstrap != ready_application_python
    env = fixture.environment(tmp_path)
    env.update(
        UV_OFFLINE="1", UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON_INSTALL_DIR=str(managed_root), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
    payload = tmp_path / "payload"
    shutil.copytree(ready_command_payload, payload)
    legacy = Path(env["HOME"]) / ".agent-dispatch"
    namespaced = scenario == "payload-namespaced"
    root = tmp_path / "selected application's \u03bb root" if namespaced else legacy
    slot = root / "versions" / "1.2.3"
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(ready_application_python), str(slot)],
        capture_output=True, text=True, timeout=20, creationflags=fixture.HEADLESS, env=env,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_dispatch"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    application = (
        "import json,os,sys\nfrom pathlib import Path\n"
        "value={'executable':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),\n"
        " 'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),\n"
        " 'root':os.environ.get('AGENT_DISPATCH_INSTALL_DIR'),\n"
        " 'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),\n"
        " 'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,\n"
        " 'selector':os.environ.get('UV_PYTHON')}\n"
        "Path(os.environ['READY_EVIDENCE']).write_text(json.dumps(value),encoding='utf-8')\n"
        "print('application stdout')\nprint('application stderr',file=sys.stderr)\nsys.exit(37)\n"
    )
    for name in ("__main__.py", "board_cli.py"):
        (package / name).write_text(application, encoding="utf-8")
    descriptor = slot / "share/copilot-extensions/agent_dispatch/readiness-isolation.json"
    descriptor.parent.mkdir(parents=True)
    shutil.copy2(payload / "readiness-isolation.json", descriptor)
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": "1.2.3", "completed_at": "2026-01-01T00:00:00Z", "pid": 1}),
        encoding="utf-8",
    )
    selector = "last-known-good" if scenario == "payload-lkg" else "current-version"
    if scenario != "global-newest":
        (root / selector).write_text("1.2.3\n", encoding="utf-8")
    if scenario == "payload-lkg":
        (root / "current-version").write_text("incomplete\n", encoding="utf-8")
    bin_dir = legacy / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("runtime-gate.ps1", "resolve-runtime.ps1"):
        shutil.copy2(payload / "scripts" / name, bin_dir / name)
    shutil.copytree(payload / "scripts/_bootstrap_python", bin_dir / "_bootstrap_python")
    context = tmp_path / "home/contexts/a/b/c/d/e" if namespaced else None
    decision = {
        "status": "ready", "actualMode": "namespaced" if namespaced else "legacy",
        "desiredMode": "namespaced" if namespaced else "legacy", "reason": "namespaced-active",
        "installGeneration": "fixture-generation", "runtimeRoot": str(root),
        "context": str(context) if context else "", "legacy": {"root": str(legacy)},
    }
    helper = (
        "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,$Context,$DurableHome,"
        "$ExpectedPluginId,$ExpectedPayloadRoot)\n"
        f"if ($Action -eq 'validate') {{ {_quote(json.dumps({'generation': 'fixture-generation', 'pluginRoot': str(root)}))} }}\n"
        f"else {{ {_quote(json.dumps(decision))} }}\nexit 0\n"
    )
    for directory in (payload / "scripts", bin_dir):
        context_helper = directory / "installation-context/installation-context.ps1"
        context_helper.parent.mkdir(exist_ok=True)
        context_helper.write_text(helper, encoding="utf-8")
    (legacy / "payload-dir").write_text(str(payload), encoding="utf-8")
    installer_marker = tmp_path / "installer-called"
    (payload / "scripts/install.ps1").write_text(
        f"[IO.File]::WriteAllText({_quote(installer_marker)},'forbidden')\nexit 99\n",
        encoding="utf-8",
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-called"
    for name in ("python", "python3", "py"):
        poison = commands / (name + (".cmd" if os.name == "nt" else ""))
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n'
            if os.name == "nt" else '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    if not scenario.endswith("missing-uv"):
        shutil.copy2(uv, commands / uv.name)
    support = bin_dir if scenario.startswith("global-") else payload / "scripts"
    missing = {
        "payload-missing-provider": "bootstrap-uv.ps1",
        "global-missing-capture": "bootstrap-capture.ps1" if os.name == "nt" else "versioned_runtime_native.ps1",
    }.get(scenario)
    if missing:
        path = support / "_bootstrap_python" / missing
        path.rename(path.with_suffix(".unavailable"))
    evidence = tmp_path / "application.json"
    caller = tmp_path / "caller.json"
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(ambient), READY_EVIDENCE=str(evidence),
        AGENT_DISPATCH_INSTALL_DIR=str(legacy), AGENT_DISPATCH_PAYLOAD_ROOT=str(payload),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if namespaced:
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    arguments = ["show", "space value", 'quote"value', "", "end\\", "\u03bb"]
    if "telemetry" in scenario:
        arguments = ["telemetry", "capabilities"]
    if scenario == "payload-readiness":
        arguments = ["installer-readiness"]
    if scenario == "global-board":
        env["AGENT_DISPATCH_COMMAND_MODULE"] = "agent_dispatch.board_cli"
    entry = (
        bin_dir / "runtime-gate.ps1" if scenario.startswith("global-")
        else payload / "bin/agent-dispatch.ps1"
    )
    harness = tmp_path / "invoke.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$env:PATH={_quote(commands)}\n"
        f"$forward=@({','.join(_quote(argument) for argument in arguments)})\n"
        f"& {_quote(entry)} @forward\n$code=$LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({_quote(caller)},"
        "(@{path=$env:PATH;selector=$env:UV_PYTHON;config=$env:UV_CONFIG_FILE;"
        "noConfig=$env:UV_NO_CONFIG} | ConvertTo-Json -Compress))\nexit $code\n",
        encoding="utf-8-sig",
    )
    original_slot, original_payload = _snapshot(slot), _snapshot(payload)
    samples: list[float] = []
    refused = "missing-" in scenario
    for _ in range(1 if refused else 2):
        started = time.monotonic()
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            cwd=tmp_path, env=env, input="caller input\n", capture_output=True,
            text=True, timeout=25, creationflags=fixture.HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not ambient.exists() and not installer_marker.exists(), result.stdout + result.stderr
        assert _snapshot(slot) == original_slot and _snapshot(payload) == original_payload
        if refused:
            assert result.returncode == 126, result.stdout + result.stderr
            assert "application runtime admission failed" in result.stderr
            assert not evidence.exists()
        else:
            assert result.returncode == 37, result.stdout + result.stderr
            assert result.stdout.strip() == "application stdout"
            assert "application stderr" in result.stderr
            actual = json.loads(evidence.read_text(encoding="utf-8"))
            assert Path(actual["executable"]) == python
            assert Path(actual["prefix"]) == slot and actual["version"] == [3, 10, 20]
            assert actual["argv"] == arguments and actual["stdin"] == "caller input\n"
            assert Path(actual["cwd"]) == tmp_path and Path(actual["root"]) == root
            assert actual["context"] == (str(context) if context else None)
            assert actual["isolated"] == int(scenario == "payload-readiness")
            assert actual["bytecode"] is True and actual["selector"] == "unrelated-python"
        restored = json.loads(caller.read_text(encoding="utf-8-sig"))
        assert restored == {
            "path": str(commands), "selector": "unrelated-python",
            "config": "missing-relative.toml", "noConfig": "false",
        }
    if "telemetry" in scenario:
        assert not (root / "logs").exists()
    record_property("dispatch_ready_admission", json.dumps({
        "shell": shell, "scenario": scenario, "samples_seconds": samples,
        "network": "disabled", "runtime_python": "3.10.20", "bootstrap_python": "3.11.18",
    }))
