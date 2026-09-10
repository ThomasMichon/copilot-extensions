"""Guard: bootstrap-check's background reconcile spawn requires an explicit,
checked-in, per-plugin opt-in (priority hardening: copilot-extensions
daemons/hooks were observed flooding a shared machine's process table --
every session start, in every checked-out project, independently
version-checking and potentially spawning a background installer process
tree).

Fanned out from the agent-bridge reference implementation/pilot
(tools/check-bootstrap-sync.py's ``versioned-venv/agent-bridge-reference``
family). This plugin's bootstrap-check is byte-identical across its
``versioned-venv/psscriptroot`` family (or, for agent-mcp, coincidentally
identical text despite being classified separately) -- so is this test file,
verbatim, across every member; only the plugin under test differs, resolved
from this file's own location.

These are file-shape assertions over the hook scripts (matching this repo's
existing convention, e.g. test_install_ps1_supervisor_cwd.py) plus two real
executions of the bash counterpart against an isolated fake plugin/install
dir, proving the spawn is suppressed without the opt-in and proceeds with it.
Unlike agent-bridge, this family has no reconcile-status.json observability,
so "proceeded" is verified via the synchronous pre-spawn stderr message
rather than a status file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_NAME = _PLUGIN_ROOT.name
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# The shared template embeds the DYNAMIC $name variable in its opt-in key
# (resolved from plugin.json at runtime), not this plugin's literal name --
# so the static file-shape check looks for the variable form, while the
# runtime behavior tests below use the resolved literal key.
_OPT_IN_KEY = f"background_reconcile_{_PLUGIN_NAME}"


def _resolve_bash() -> str | None:
    """Return a usable Bash path, excluding the WindowsApps WSL alias stub."""
    path = os.environ.get("PATH")
    if path:
        filtered = os.pathsep.join(part for part in path.split(os.pathsep) if "WindowsApps" not in part)
        bash = shutil.which("bash", path=filtered)
        if bash:
            return bash
    bash = shutil.which("bash")
    if bash and "WindowsApps" not in bash:
        return bash
    return None


_BASH = _resolve_bash()


def test_hook_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_gates_before_spawn():
    text = _PS1.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_candidates = [
        i
        for i in (
            text.find("Start-Process -FilePath 'conhost.exe'"),
        )
        if i != -1
    ]
    assert spawn_candidates, "no reconcile spawn site found in " + str(_PS1)
    assert gate_at < min(spawn_candidates), "the opt-in check must precede the reconcile spawn"
    assert "background_reconcile_$name" in text
    assert ".copilot-extensions\\config.yaml" in text


def test_sh_gates_before_spawn():
    text = _SH.read_text(encoding="utf-8")
    gate_at = text.index("OPT-IN GATE")
    spawn_candidates = [
        i
        for i in (
            text.find('nohup bash "${target[@]}"'),
            text.find('nohup bash "$init"'),
        )
        if i != -1
    ]
    assert spawn_candidates, "no reconcile spawn site found in " + str(_SH)
    assert gate_at < min(spawn_candidates), "the opt-in check must precede the reconcile spawn"
    assert "background_reconcile_${name}" in text
    assert ".copilot-extensions/config.yaml" in text


def _make_fake_install(home: Path, name: str) -> Path:
    """A minimally 'drifted' install dir: deployed=1.0.0, payload=1.0.1.

    Multi-line, indented JSON: budget-guidance's bootstrap-check (the
    'pythonless' family member) parses the manifest with a line-oriented awk
    script, not a real JSON parser, so it needs 'source'/'version' on their
    own lines the way a real ``ConvertTo-Json``/``json.dump(indent=...)``
    deploy would produce -- a compact single-line dump silently fails to
    match its patterns.
    """
    install_dir = home / f".{name}"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deploy-manifest.json").write_text(
        json.dumps(
            {"source": {"version": "1.0.0", "path": str(install_dir / "src")}},
            indent=2,
        ),
        encoding="utf-8",
    )
    # No .venv/venv/current-version -- provisioned stays False, so the drift
    # branch is reached regardless of the version comparison.
    return install_dir


def _make_fake_plugin(root: Path, name: str) -> Path:
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
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    env.update(overrides)
    return env


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_skips_spawn_without_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin_dir = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    _make_fake_install(home, _PLUGIN_NAME)

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


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_proceeds_with_opt_in(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: true\n", encoding="utf-8"
    )
    plugin_dir = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    _make_fake_install(home, _PLUGIN_NAME)

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
    assert "reconciling in background" in result.stderr, result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
