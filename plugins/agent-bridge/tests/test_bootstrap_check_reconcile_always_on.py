"""Guard: bootstrap-check's background reconcile no longer gates on a
per-plugin opt-in (agent-bridge-unified-zdd-cutover Phase 0). The opt-in
existed because a raw reconcile could race a live daemon/session; now that
agent-bridge's own update path is always-ZDD (spawn passive -> health-gate ->
flip -> drain -> retire, safe to run unattended), that justification is
gone -- the gate was removed rather than kept as a redundant consent
checkbox. This is the reference implementation
(tools/check-bootstrap-sync.py's ``versioned-venv/agent-bridge-reference``
singleton family) -- sibling plugins made the same change separately.

These are file-shape assertions over the hook scripts (matching this repo's
existing convention, e.g. test_install_ps1_supervisor_cwd.py) plus real
executions of the bash counterpart (available on the Linux CI runner)
proving a version drift now reconciles unconditionally, with the existing
reconcile-status.json observability still recording the attempt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# A bare shutil.which("bash") can resolve to a Windows App Execution Alias
# stub or the classic `C:\Windows\System32\bash.exe` WSL launcher (both
# invoke an actual WSL distro rather than running this script in the
# environment under test). Prefer the real Git Bash location when present;
# otherwise filter both known WSL-launcher locations out of PATH before
# falling back to shutil.which, so this never silently selects one.
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
    hook before it ever reaches the code under test."""
    env = dict(os.environ)
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(
            p for p in parts if "WindowsApps" not in p
        )
    env.update(overrides)
    return env


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_reconciles_without_any_opt_in_config(tmp_path):
    """No .copilot-extensions/config.yaml at all -- reconcile must still
    proceed unconditionally, and the existing reconcile-status.json
    observability must still record the attempt."""
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
    assert "SKIPPED" not in result.stderr, result.stderr
    status_file = home / ".agent-bridge" / "reconcile-status.json"
    # The background reconcile is async (nohup ... &); give it a moment.
    for _ in range(50):
        if status_file.exists():
            break
        time.sleep(0.1)
    assert status_file.exists(), "a reconcile attempt should be recorded unconditionally"


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_reconcile_status_records_completion_not_just_launch(tmp_path):
    """Phase 0 review finding: the status file must be overwritten with
    completion info (completed_at/exit_code/success) once the installer
    actually finishes -- reporting only the launch timestamp would make a
    failed or wedged reconcile look falsely healthy."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin_dir = _make_fake_plugin(tmp_path)
    _make_fake_install(home)

    env = _clean_env({"HOME": str(home), "COPILOT_PROJECT_DIR": str(project)})

    subprocess.run(
        [_BASH, str(plugin_dir / "scripts" / "bootstrap-check.sh")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    status_file = home / ".agent-bridge" / "reconcile-status.json"
    status = None
    for _ in range(100):
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8"))
            if "completed_at" in status:
                break
        time.sleep(0.1)
    assert status is not None, "reconcile-status.json was never written"
    assert "completed_at" in status, "completion was never recorded: " + json.dumps(status)
    assert status.get("success") is True, status
    assert status.get("exit_code") == 0, status
    assert status.get("from") == "1.0.0" and status.get("to") == "1.0.1", status


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_reconciles_even_with_stale_opt_in_key_present(tmp_path):
    """A leftover `background_reconcile_agent-bridge: false` from before this
    gate's removal must not resurrect the old skip behavior -- the key is
    now inert."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        "background_reconcile_agent-bridge: false\n", encoding="utf-8"
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
    for _ in range(50):
        if status_file.exists():
            break
        time.sleep(0.1)
    assert status_file.exists(), "a reconcile attempt should be recorded unconditionally"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
