from __future__ import annotations

import json
import os
import shutil
import subprocess
import venv
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _copy_payload(tmp_path: Path, name: str) -> Path:
    payload = tmp_path / name
    shutil.copytree(
        PLUGIN,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
        ),
    )
    return payload


def _site_packages(interpreter: Path) -> Path:
    result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return Path(result.stdout.strip())


def _write_module(path: Path, module_name: str) -> None:
    path.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"MODULE_NAME = {module_name!r}\n"
        "home = Path(os.environ['AGENT_LOGGER_HOME'])\n"
        "(home / 'runtime-artifacts').mkdir(parents=True, exist_ok=True)\n"
        "artifact = home / 'runtime-artifacts' / f\"{MODULE_NAME.replace('.', '_')}.json\"\n"
        "payload = {\n"
        "    'module': MODULE_NAME,\n"
        "    'args': sys.argv[1:],\n"
        "    'home': os.environ.get('AGENT_LOGGER_HOME'),\n"
        "    'context': os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),\n"
        "    'installation_id': os.environ.get('AGENT_LOGGER_INSTALLATION_ID'),\n"
        "    'task_name': os.environ.get('AGENT_LOGGER_TASK_NAME'),\n"
        "    'timer_name': os.environ.get('AGENT_LOGGER_TIMER_NAME'),\n"
        "}\n"
        "artifact.write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')\n"
        "capture = os.environ.get('TEST_CAPTURE')\n"
        "if capture:\n"
        "    Path(capture).write_text(json.dumps(payload, sort_keys=True), encoding='utf-8')\n"
        "print(json.dumps(payload, sort_keys=True))\n",
        encoding="utf-8",
    )


def _fake_runtime(runtime_root: Path) -> Path:
    slot = runtime_root / "versions" / "9.9.9"
    interpreter = (
        slot / "Scripts" / "python.exe"
        if os.name == "nt"
        else slot / "bin" / "python"
    )
    if not interpreter.is_file():
        venv.EnvBuilder(with_pip=False).create(slot)
    site_packages = _site_packages(interpreter)
    package = site_packages / "agent_logger"
    if package.exists():
        shutil.rmtree(package)
    (package / "segmenter").mkdir(parents=True)
    (package / "sync").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _write_module(package / "__main__.py", "agent_logger")
    _write_module(package / "segmenter" / "collate.py", "agent_logger.segmenter.collate")
    _write_module(package / "segmenter" / "prepare_log.py", "agent_logger.segmenter.prepare_log")
    _write_module(package / "segmenter" / "read_digest.py", "agent_logger.segmenter.read_digest")
    _write_module(package / "segmenter" / "ramp_up.py", "agent_logger.segmenter.ramp_up")
    (package / "sync" / "__init__.py").write_text("", encoding="utf-8")
    _write_module(package / "sync" / "engine.py", "agent_logger.sync.engine")
    (runtime_root / "current-version").write_text("9.9.9\n", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps(
            {
                "version": "9.9.9",
                "completed_at": "2026-09-08T00:00:00Z",
                "pid": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return interpreter


def _write_gate_stubs(payload: Path) -> None:
    context_dir = payload / "scripts" / "installation-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "installation-context.sh").write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "$1" == status ]]; then
  if [[ -n "${TEST_INSTALLATION_STATUS:-}" ]]; then
    printf '%s\\n' "$TEST_INSTALLATION_STATUS"
  else
    printf '%s\\n' '{"status":"ready","reason":"policy-default-false","actualMode":"legacy","desiredMode":"legacy","policy":{"state":"valid","enabled":false},"installationMode":{"marketplaces":{}}}'
  fi
  exit 0
fi
root="${TEST_CELL_ROOT:?}"
printf '{"pluginRoot":"%s","versionsRoot":"%s/versions","snapshotsRoot":"%s/snapshots","stateRoot":"%s/state","runRoot":"%s/run","logsRoot":"%s/logs","cacheRoot":"%s/cache","namespaceGeneration":1,"generation":1}\\n' "$root" "$root" "$root" "$root" "$root" "$root" "$root"
""",
        encoding="utf-8",
        newline="\n",
    )
    (context_dir / "installation-context.sh").chmod(0o700)
    (context_dir / "json-query.awk").write_text(
        (PLUGIN / "scripts" / "installation-context" / "json-query.awk").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
        newline="\n",
    )
    (payload / "scripts" / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="${TEST_PYTHON:-}"\n',
        encoding="utf-8",
        newline="\n",
    )
    (context_dir / "installation-context.ps1").write_text(
        """$ErrorActionPreference = 'Stop'
if ($args[0] -eq 'status') {
    if ($env:TEST_INSTALLATION_STATUS) {
        Write-Output $env:TEST_INSTALLATION_STATUS
    } else {
        [ordered]@{
            status = 'ready'
            reason = 'policy-default-false'
            actualMode = 'legacy'
            desiredMode = 'legacy'
            policy = [ordered]@{ state = 'valid'; enabled = $false }
            installationMode = [ordered]@{ marketplaces = [ordered]@{} }
        } | ConvertTo-Json -Compress -Depth 4
    }
    exit 0
}
$root = $env:TEST_CELL_ROOT
[ordered]@{
    pluginRoot = $root
    versionsRoot = Join-Path $root 'versions'
    snapshotsRoot = Join-Path $root 'snapshots'
    stateRoot = Join-Path $root 'state'
    runRoot = Join-Path $root 'run'
    logsRoot = Join-Path $root 'logs'
    cacheRoot = Join-Path $root 'cache'
    namespaceGeneration = 1
    generation = 1
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n", encoding="utf-8"
    )


def _run_payload_command(
    shell: str,
    payload: Path,
    command: str,
    env: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    cwd = payload.parent
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX runtime-gate test")
        return subprocess.run(
            ["bash", str(payload / "bin" / command), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
            check=False,
        )
    if os.name != "nt":
        pytest.skip("native Windows PowerShell runtime-gate test")
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is unavailable")
    return subprocess.run(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(payload / "bin" / f"{command}.ps1"), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        check=False,
    )


@pytest.mark.parametrize(
    ("shell", "command", "expected_module"),
    [
        ("powershell", "agent-logger", "agent_logger"),
        ("powershell", "collate-session", "agent_logger.segmenter.collate"),
        ("bash", "collate-session", "agent_logger.segmenter.collate"),
    ],
)
def test_payload_dispatch_uses_cell_scoped_runtime(
    tmp_path: Path,
    shell: str,
    command: str,
    expected_module: str,
) -> None:
    payload = _copy_payload(tmp_path, f"payload-{shell}-{command}")
    _write_gate_stubs(payload)
    cell_root = tmp_path / "marketplaces" / shell / "plugins" / "agent-logger"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    runtime = _fake_runtime(cell_root)
    capture = tmp_path / f"{shell}-{command}.json"
    env = os.environ.copy()
    profile = tmp_path / f"profile-{shell}-{command}"
    profile.mkdir()
    env.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "TEST_CAPTURE": str(capture),
            "TEST_CELL_ROOT": str(cell_root),
            "TEST_PYTHON": str(runtime),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "installGeneration": 1,
                    "context": str(context),
                    "marketplaceId": "example--1234",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )

    result = _run_payload_command(shell, payload, command, env, "--probe")

    assert result.returncode == 0, result.stderr
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["module"] == expected_module
    assert observed["args"] == ["--probe"]
    assert observed["home"] == str(cell_root)
    assert observed["context"] == str(context)
    assert observed["installation_id"] == "example--1234/agent-logger"
    assert observed["timer_name"].startswith("agent-logger-sync-")
    assert observed["task_name"].startswith("Agent Logger Session Sync - ")


def test_two_namespaced_cells_keep_runtime_artifacts_separate(tmp_path: Path) -> None:
    shell = (
        "powershell"
        if os.name == "nt" and (shutil.which("powershell.exe") or shutil.which("pwsh"))
        else "bash"
    )
    payload_a = _copy_payload(tmp_path, "payload-a")
    payload_b = _copy_payload(tmp_path, "payload-b")
    _write_gate_stubs(payload_a)
    _write_gate_stubs(payload_b)
    cell_a = tmp_path / "marketplaces" / "alpha" / "plugins" / "agent-logger"
    cell_b = tmp_path / "marketplaces" / "beta" / "plugins" / "agent-logger"
    context_a = cell_a / "install.json"
    context_b = cell_b / "install.json"
    context_a.parent.mkdir(parents=True)
    context_b.parent.mkdir(parents=True)
    context_a.write_text("{}\n", encoding="utf-8")
    context_b.write_text("{}\n", encoding="utf-8")
    runtime_a = _fake_runtime(cell_a)
    runtime_b = _fake_runtime(cell_b)
    capture_a = tmp_path / "capture-a.json"
    capture_b = tmp_path / "capture-b.json"
    profile = tmp_path / "profile"
    profile.mkdir()

    env_a = os.environ.copy()
    env_a.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "TEST_CAPTURE": str(capture_a),
            "TEST_CELL_ROOT": str(cell_a),
            "TEST_PYTHON": str(runtime_a),
            "COPILOT_EXTENSIONS_CONTEXT": str(context_a),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "installGeneration": 1,
                    "context": str(context_a),
                    "marketplaceId": "cell-a",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )
    env_b = os.environ.copy()
    env_b.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "TEST_CAPTURE": str(capture_b),
            "TEST_CELL_ROOT": str(cell_b),
            "TEST_PYTHON": str(runtime_b),
            "COPILOT_EXTENSIONS_CONTEXT": str(context_b),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "installGeneration": 1,
                    "context": str(context_b),
                    "marketplaceId": "cell-b",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )

    result_a = _run_payload_command(shell, payload_a, "collate-session", env_a, "--alpha")
    result_b = _run_payload_command(shell, payload_b, "collate-session", env_b, "--beta")

    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    observed_a = json.loads(capture_a.read_text(encoding="utf-8"))
    observed_b = json.loads(capture_b.read_text(encoding="utf-8"))
    assert observed_a["home"] == str(cell_a)
    assert observed_b["home"] == str(cell_b)
    assert observed_a["installation_id"] == "cell-a/agent-logger"
    assert observed_b["installation_id"] == "cell-b/agent-logger"
    assert observed_a["home"] != observed_b["home"]
    artifact_a = cell_a / "runtime-artifacts" / "agent_logger_segmenter_collate.json"
    artifact_b = cell_b / "runtime-artifacts" / "agent_logger_segmenter_collate.json"
    assert artifact_a.is_file()
    assert artifact_b.is_file()
    assert artifact_a.read_text(encoding="utf-8") != artifact_b.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("shell", ["powershell", "bash"])
def test_cross_cell_context_mismatch_is_rejected(tmp_path: Path, shell: str) -> None:
    payload = _copy_payload(tmp_path, f"payload-mismatch-{shell}")
    _write_gate_stubs(payload)
    cell_root = tmp_path / "marketplaces" / "mismatch" / "plugins" / "agent-logger"
    context = cell_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    profile = tmp_path / f"profile-mismatch-{shell}"
    profile.mkdir()
    env.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "TEST_CELL_ROOT": str(cell_root),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "provenance-blocked",
                    "reason": "context-plugin-mismatch",
                    "actualMode": "legacy",
                    "desiredMode": "namespaced",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )

    result = _run_payload_command(shell, payload, "collate-session", env, "--probe")

    assert result.returncode == 126
    assert "blocks invocation" in result.stderr or (
        "requested installation context is not active" in result.stderr
    )
