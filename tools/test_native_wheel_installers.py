"""Native recipe admission before any installation effects."""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell installer arguments")
@pytest.mark.parametrize("plugin,entry,action", [
    (name, "install.ps1", "update") for name in (
        "budget-guidance", "agent-ssh", "agent-bridge", "agent-codespaces",
        "agent-dispatch", "agent-vault", "agent-index", "agent-pull-requests",
        "agent-logger", "agent-worktrees",
    )
] + [
    (name, "init.ps1", "install")
    for name in ("agent-containers", "agent-machines", "agent-mcp")
])
def test_incomplete_wheel_input_refuses_before_native_effects(tmp_path, plugin, entry, action):
    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell unavailable")
    home = tmp_path / "home"
    home.mkdir()
    runtime = home / ("." + plugin)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "BUDGET_GUIDANCE_", "COPILOT_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File",
         str(ROOT / "plugins" / plugin / "scripts" / entry),
         "-Action", action, "-InstallDir", str(runtime),
         "-WheelProfile", str(tmp_path / "not-selected.json")],
        cwd=home, env=environment, capture_output=True, text=True,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "Closed wheels require" in result.stderr
    assert not runtime.exists()
    assert not (home / ".local").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native bootstrap readiness")
@pytest.mark.parametrize("plugin", ["agent-ssh", "budget-guidance"])
def test_native_bootstrap_validates_evidence_but_keeps_ordinary_presence(tmp_path, monkeypatch, plugin):
    assert_native_bootstrap(tmp_path, monkeypatch, plugin)


def assert_native_bootstrap(tmp_path, monkeypatch, plugin):
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "versioned-runtime"))
    monkeypatch.syspath_prepend(str(ROOT / "libs" / "installer-engine"))
    import install_source

    host = shutil.which("powershell.exe")
    assert host
    payload, home = tmp_path / "payload", tmp_path / "home"
    shutil.copytree(
        ROOT / "plugins" / plugin, payload,
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "tests"),
    )
    project = tomllib.loads((payload / "pyproject.toml").read_text())["project"]
    version = project["version"]
    row = {
        "name": plugin, "version": version,
        "filename": plugin.replace("-", "_") + "-1.0-py3-none-any.whl", "sha256": "sha256:" + "a" * 64,
        "selected_extras": [],
    }
    binding = {"wheels": [row]}
    lock = "sha256:" + hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    native_version = version + ".profile." + lock[7:23]
    root = home / ("." + plugin)
    if plugin == "budget-guidance":
        publication = payload / "scripts" / "installation-context" / "plugin-publication-probe.ps1"
        publication.write_text(
            "[Console]::Error.WriteLine('fixture: reconcile admission reached'); exit 19\n",
            encoding="utf-8",
        )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "BUDGET_GUIDANCE_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_SSH_NO_SELFPROVISION="1",
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    for fault in (None, "completion", "binding", "ordinary", "different-generation"):
        generation = version if fault == "ordinary" else native_version
        slot = root / "versions" / generation
        python = slot / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.touch()
        marker = slot / ".install-complete.json"
        if marker.exists():
            marker.unlink()
        if fault != "completion":
            install_source.engine.runtime.mark_complete(root, generation, payload_hash="b" * 64)
        receipt = {
            "construction": "closed-wheels", "lock_id": lock,
            "binding": {**binding, **({"backend": "changed"} if fault == "binding" else {})},
            "distributions": {plugin: {key: row[key] for key in ("version", "filename", "sha256")}},
        }
        (slot / ".runtime-input-origins.json").write_text(json.dumps({
            "schema": "installer-engine.frozen-input-origins", "version": 2,
            "wheel_inputs": receipt, "local_packages": [plugin],
        }))
        (root / "current-version").write_text(generation)
        (root / "deploy-manifest.json").write_text(json.dumps({
            "runtime_version": "0.0.0" if fault == "different-generation" else generation,
            "source": {"version": version, "plugin": plugin, "path": str(payload), "content_hash": "b" * 64},
        }))
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-File",
             str(payload / "scripts" / "bootstrap-check.ps1")],
            cwd=home, env=environment, capture_output=True, text=True,
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        invalid = fault in {"completion", "binding", "different-generation"}
        expected = 3 if invalid and plugin == "budget-guidance" else 0
        message = "bootstrap deferred" if plugin == "agent-ssh" else "reconcile admission reached"
        assert result.returncode == expected, result.stdout + result.stderr
        assert (message in result.stderr) == invalid, result.stderr
