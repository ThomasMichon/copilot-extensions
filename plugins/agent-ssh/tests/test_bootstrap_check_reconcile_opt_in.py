"""Guard: agent-ssh's bootstrap-check background reconcile spawn requires an
explicit, checked-in opt-in (priority hardening: copilot-extensions
daemons/hooks were observed flooding a shared machine's process table).

Fanned out from the agent-bridge reference implementation/pilot. agent-ssh is
its own deploy-model family (manifest-path/agent-ssh) -- unlike the shared
psscriptroot template, it hardcodes its plugin name (no plugin.json read) and
resolves the plugin/pyproject location from the deploy manifest's
``source.path`` rather than its own script location, so its fake fixture
differs from the shared family's.

File-shape assertions plus two real executions of the bash counterpart
against an isolated fake plugin/install dir.
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
_OPT_IN_KEY = "background_reconcile_agent-ssh"
# Resolve bash to its FULL path -- see the agent-bridge/agent-codespaces
# sibling tests for why (Windows App Execution Alias interception).
_BASH = shutil.which("bash")


def test_hook_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_gates_before_spawn():
    text = _PS1.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_at = text.index("Start-Process -FilePath 'conhost.exe'")
    assert gate_at < spawn_at, "the opt-in check must precede the reconcile spawn"
    assert _OPT_IN_KEY in text
    assert ".copilot-extensions\\config.yaml" in text


def test_sh_gates_before_spawn():
    text = _SH.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_at = text.index('nohup bash "$init"')
    assert gate_at < spawn_at, "the opt-in check must precede the reconcile spawn"
    assert _OPT_IN_KEY in text
    assert ".copilot-extensions/config.yaml" in text


def _make_fake_plugin(root: Path) -> Path:
    """The fake 'source' plugin dir agent-ssh's manifest points at: its own
    pyproject.toml (the 'current' payload version) plus a harmless stub
    installer -- never invoke the REAL install.sh from a test."""
    plugin_dir = root / "plugin"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    (scripts_dir / "init.sh").write_text(
        "#!/usr/bin/env bash\necho stub-init-ran\n", encoding="utf-8"
    )
    (scripts_dir / "init.sh").chmod(0o755)
    return plugin_dir


def _make_fake_install(home: Path, plugin_dir: Path) -> Path:
    """A minimally 'drifted' install dir: deployed=1.0.0, payload=1.0.1,
    source.path pointing at the fake plugin (agent-ssh reads pyproject from
    there, not from its own script location)."""
    install_dir = home / ".agent-ssh"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.0.0", "path": str(plugin_dir)}}),
        encoding="utf-8",
    )
    return install_dir


def _clean_env(overrides: dict[str, str]) -> dict[str, str]:
    """See agent-bridge's sibling test for why WindowsApps must be excluded
    from PATH here (a python3 store-alias stub can shadow the real
    interpreter bash's ``command -v python3`` would otherwise resolve)."""
    env = dict(os.environ)
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    env.update(overrides)
    return env


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_skips_spawn_without_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin_dir = _make_fake_plugin(tmp_path)
    _make_fake_install(home, plugin_dir)

    env = _clean_env({"HOME": str(home), "COPILOT_PROJECT_DIR": str(project)})

    result = subprocess.run(
        [_BASH, str(_SH)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" in result.stderr, result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_proceeds_with_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: true\n", encoding="utf-8"
    )
    plugin_dir = _make_fake_plugin(tmp_path)
    _make_fake_install(home, plugin_dir)

    env = _clean_env({"HOME": str(home), "COPILOT_PROJECT_DIR": str(project)})

    result = subprocess.run(
        [_BASH, str(_SH)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" not in result.stderr, result.stderr
    assert "reconciling in background" in result.stderr, result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
