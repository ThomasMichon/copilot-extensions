"""Guard: bootstrap-check's background reconcile spawn requires an explicit,
checked-in opt-in (priority hardening: copilot-extensions daemons/hooks were
observed flooding a shared machine's process table -- every session start,
in every checked-out project, independently version-checking and potentially
spawning a background installer process tree).

agent-bridge is the reference implementation (tools/check-bootstrap-sync.py's
``versioned-venv/agent-bridge-reference`` singleton family) -- this is the
pilot for the gate; sibling plugins fan out separately.

These are file-shape assertions over the hook scripts (matching this repo's
existing convention, e.g. test_install_ps1_supervisor_cwd.py) plus one real
execution of the bash counterpart (available on the Linux CI runner) proving
the gate actually suppresses the spawn without an opt-in and permits it with
one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# Resolve bash to its FULL path rather than invoking the bare command name:
# on Windows, a Windows App Execution Alias can intercept a bare "bash.exe"
# process-creation call (routing it to WSL) even when shutil.which() finds
# Git Bash first on PATH -- the alias interception happens below PATH search,
# at CreateProcess time, unless the explicit resolved path is used.
_BASH = shutil.which("bash")


def test_hook_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_gates_before_spawn():
    text = _PS1.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_at = text.index("Start-Process -FilePath 'conhost.exe'")
    assert gate_at < spawn_at, "the opt-in check must precede the reconcile spawn"
    assert "background_reconcile:\\s*true" in text
    assert ".copilot-extensions\\config.yaml" in text


def test_sh_gates_before_spawn():
    text = _SH.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_at = text.index('nohup bash "${target[@]}"')
    assert gate_at < spawn_at, "the opt-in check must precede the reconcile spawn"
    assert "background_reconcile:[[:space:]]*true" in text
    assert ".copilot-extensions/config.yaml" in text


def _make_fake_install(home: Path, name: str = "agent-bridge") -> Path:
    """A minimally 'drifted' install dir: deployed=1.0.0, payload=1.0.1."""
    install_dir = home / f".{name}"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.0.0"}}), encoding="utf-8"
    )
    # No .venv/venv/current-version -- runtimeHealthy stays False, so the
    # drift branch is reached regardless of the version comparison.
    return install_dir


def _make_fake_plugin(root: Path, name: str = "agent-bridge") -> Path:
    """An isolated copy of the plugin dir shape bootstrap-check.sh expects,
    with a harmless stub installer so a spawned reconcile does no real work
    (never invoke the REAL install.sh from a test -- it does a live install)."""
    plugin_dir = root / "plugin"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (plugin_dir / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    shutil.copyfile(_SH, scripts_dir / "bootstrap-check.sh")
    (scripts_dir / "install.sh").write_text(
        "#!/usr/bin/env bash\necho stub-install-ran\n", encoding="utf-8"
    )
    (scripts_dir / "install.sh").chmod(0o755)
    (scripts_dir / "bootstrap-check.sh").chmod(0o755)
    return plugin_dir


def _clean_env(overrides: dict[str, str]) -> dict[str, str]:
    """A subprocess env with the Windows Store's python3.exe alias stub (a
    no-op unless Python is installed via the Store) removed from PATH -- it
    can shadow the real interpreter ahead of it, which would make bash's
    ``command -v python3`` resolve to a dud and silently short-circuit this
    hook before it ever reaches the gate under test."""
    env = dict(os.environ)
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(
            p for p in parts if "WindowsApps" not in p
        )
    env.update(overrides)
    return env


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_skips_spawn_without_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin_dir = _make_fake_plugin(tmp_path)
    _make_fake_install(home)

    env = _clean_env({"HOME": str(home), "COPILOT_PROJECT_DIR": str(project)})

    result = subprocess.run(
        [_BASH, str(plugin_dir / "scripts" / "bootstrap-check.sh")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" in result.stderr, result.stderr
    status_file = home / ".agent-bridge" / "reconcile-status.json"
    assert not status_file.exists(), "no opt-in -> no reconcile should be attempted"


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_attempts_spawn_with_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        "background_reconcile: true\n", encoding="utf-8"
    )
    plugin_dir = _make_fake_plugin(tmp_path)
    _make_fake_install(home)

    env = _clean_env({"HOME": str(home), "COPILOT_PROJECT_DIR": str(project)})

    result = subprocess.run(
        [_BASH, str(plugin_dir / "scripts" / "bootstrap-check.sh")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" not in result.stderr, result.stderr
    status_file = home / ".agent-bridge" / "reconcile-status.json"
    # The background reconcile is async (nohup ... &); give it a moment.
    import time

    for _ in range(50):
        if status_file.exists():
            break
        time.sleep(0.1)
    assert status_file.exists(), "opted in -> a reconcile attempt should be recorded"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
