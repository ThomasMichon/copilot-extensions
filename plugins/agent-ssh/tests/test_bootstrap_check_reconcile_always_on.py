"""Guard: bootstrap-check's background reconcile no longer gates on a
per-plugin opt-in (agent-bridge-unified-zdd-cutover Phase 0). The opt-in
existed because a raw reconcile could race a live daemon/session; now that
every reconcile-capable plugin's update path is always-ZDD (safe to run
unattended), the gate was removed rather than kept as a redundant consent
checkbox. This test proves:

* neither hook script still contains opt-in-gate text or a
  ``background_reconcile_<plugin>`` key reference, and
* a version drift reconciles unconditionally -- no
  ``.copilot-extensions/config.yaml`` required, and a stale
  ``background_reconcile_<plugin>`` key left over from before this change
  (e.g. set to ``false``) does not resurrect the old skip behavior.
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
# The legacy opt-in key -- kept only to prove a stale leftover key from
# before this gate's removal is now inert (see
# test_sh_reconciles_even_with_stale_opt_in_key_present).
_OPT_IN_KEY = "background_reconcile_agent-ssh"
# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher (both
# invoke an actual WSL distro rather than running this script in the
# environment under test). Prefer the real Git Bash location when present;
# otherwise filter both known WSL-launcher locations out of PATH before
# falling back to shutil.which, so this never silently selects one. See the
# agent-bridge/agent-codespaces sibling tests for the same pattern.
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _resolve_bash() -> str | None:
    if _GIT_BASH.is_file():
        return str(_GIT_BASH)
    path = os.environ.get("PATH")
    if not path:
        return None
    filtered = os.pathsep.join(
        part for part in path.split(os.pathsep)
        if "windowsapps" not in part.lower()
        and part.rstrip("\\").lower() != r"c:\windows\system32"
    )
    return shutil.which("bash", path=filtered)


_BASH = _resolve_bash()


def test_hook_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_has_no_opt_in_gate():
    text = _PS1.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


def test_sh_has_no_opt_in_gate():
    text = _SH.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


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
def test_sh_reconciles_without_any_opt_in_config(tmp_path):
    """No .copilot-extensions/config.yaml at all -- reconcile must still
    proceed unconditionally now that the opt-in gate is gone."""
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
    assert "SKIPPED" not in result.stderr, result.stderr
    assert "reconciling in background" in result.stderr, result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_reconciles_even_with_stale_opt_in_key_present(tmp_path):
    """A leftover `background_reconcile_<plugin>: false` from before this
    gate's removal must not resurrect the old skip behavior -- the key is
    now inert."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: false\n", encoding="utf-8"
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
