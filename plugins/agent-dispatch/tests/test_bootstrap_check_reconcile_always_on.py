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
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_NAME = _PLUGIN_ROOT.name
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# The legacy opt-in key, resolved to this plugin's literal name -- kept
# only to prove a stale leftover key from before this gate's removal is
# now inert (see test_sh_reconciles_even_with_stale_opt_in_key_present).
_OPT_IN_KEY = f"background_reconcile_{_PLUGIN_NAME}"


def _is_wsl_launcher(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold().rstrip("/") + "/"
    return "windowsapps" in normalized or "/system32/" in normalized


def _resolve_bash() -> str | None:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim.

    On Windows, ``shutil.which("bash")`` can resolve to a WSL launcher stub
    under ``WindowsApps`` or the classic ``C:\\Windows\\System32\\bash.exe``
    -- neither is a real POSIX bash for this test's purposes (the System32
    launcher invokes an actual WSL distro, which runs this script in a
    different environment than the one under test). Prefer the real Git
    Bash location when present, then fall back to a PATH-resolved bash with
    both known WSL-launcher locations excluded.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    path = os.environ.get("PATH")
    if path:
        filtered = os.pathsep.join(
            part for part in path.split(os.pathsep)
            if not _is_wsl_launcher(part)
        )
        bash = shutil.which("bash", path=filtered)
        if bash and not _is_wsl_launcher(bash):
            return bash
    bash = shutil.which("bash")
    if bash and not _is_wsl_launcher(bash):
        return bash
    return None


_BASH = _resolve_bash()


def _bash_env_path(path: Path) -> str:
    text = str(path)
    if not (_BASH and len(text) >= 2 and text[1] == ":"):
        return text
    suffix = text[2:].replace("\\", "/")
    bash_lower = _BASH.replace("\\", "/").casefold()
    if bash_lower.endswith("bash.exe") and "/git/" in bash_lower:
        # Git Bash (MSYS2) uses `/c/...`, not WSL's `/mnt/c/...`.
        return f"/{text[0].lower()}{suffix}"
    if bash_lower.endswith("bash.exe"):
        return f"/mnt/{text[0].lower()}{suffix}"
    return text


@pytest.mark.parametrize("bash", [
    r"C:\Program Files\Git\bin\bash.exe",
    "C:/Program Files/Git/bin/bash.exe",
    "c:/program files/GIT/usr/bin/bash.exe",
])
def test_git_bash_home_path_accepts_both_windows_separator_forms(monkeypatch, bash):
    monkeypatch.setitem(globals(), "_BASH", bash)
    assert _bash_env_path(Path(r"C:\Users\Fixture User\home")) == "/c/Users/Fixture User/home"


@pytest.mark.parametrize("bash", [
    r"c:\users\fixture\appdata\local\microsoft\windowsapps\bash.exe",
    "C:/Users/Fixture/AppData/Local/Microsoft/WindowsApps/bash.exe",
    "C:/Windows/System32/bash.exe",
])
def test_windows_launcher_aliases_are_never_selected_as_native_bash(monkeypatch, bash):
    monkeypatch.setattr(Path, "is_file", lambda _path: False)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: bash)
    monkeypatch.setenv("PATH", "")
    assert _resolve_bash() is None


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
    shutil.copyfile(_PLUGIN_ROOT / "scripts" / "action_controls.py", scripts_dir / "action_controls.py")
    shutil.copytree(
        _PLUGIN_ROOT / "src", plugin_dir / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
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
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    env.pop("BOOTSTRAP_KILLSWITCH_STATE_FILE", None)
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
    plugin_dir = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    _make_fake_install(home, _PLUGIN_NAME)

    env = _clean_env(
        {
            "HOME": _bash_env_path(home),
            "USERPROFILE": str(home),
            "COPILOT_PROJECT_DIR": _bash_env_path(project),
        }
    )
    script = plugin_dir / "scripts" / "bootstrap-check.sh"

    result = subprocess.run(
        [_BASH, os.path.basename(script)],
        cwd=str(script.parent),
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
    plugin_dir = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    (plugin_dir / "scripts" / ".copilot-extensions").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "scripts" / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: false\n", encoding="utf-8"
    )
    _make_fake_install(home, _PLUGIN_NAME)

    env = _clean_env(
        {
            "HOME": _bash_env_path(home),
            "USERPROFILE": str(home),
            "COPILOT_PROJECT_DIR": _bash_env_path(project),
        }
    )
    script = plugin_dir / "scripts" / "bootstrap-check.sh"

    result = subprocess.run(
        [_BASH, os.path.basename(script)],
        cwd=str(script.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" not in result.stderr, result.stderr
    assert "reconciling in background" in result.stderr, result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_relative_path_python_remains_available_to_home_check_and_worker(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    plugin = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    install = _make_fake_install(home, _PLUGIN_NAME)
    scripts = plugin / "scripts"
    binary = scripts / ".venv" / "bin"
    binary.mkdir(parents=True)
    calls = tmp_path / "interpreter-calls"
    marker = tmp_path / "installer-called"
    (binary / "python3").write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s|%s\\n' \"$0\" \"$PWD\" >> {shlex.quote(_bash_env_path(calls))}\n"
        f'exec {shlex.quote(_bash_env_path(Path(sys.executable)))} "$@"\n',
        encoding="utf-8", newline="\n",
    )
    (binary / "python3").chmod(0o755)
    (scripts / "install.sh").write_text(
        f"#!/usr/bin/env bash\nprintf 'called' > {shlex.quote(_bash_env_path(marker))}\n",
        encoding="utf-8", newline="\n",
    )
    env = _clean_env({"HOME": _bash_env_path(home), "USERPROFILE": str(home)})
    env["PATH"] = ".venv/bin" + os.pathsep + env["PATH"]
    result = subprocess.run(
        [_BASH, "bootstrap-check.sh"], cwd=scripts, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    log = install / "reconcile.log"
    assert marker.exists(), log.read_text(encoding="utf-8-sig") if log.exists() else result.stderr
    recorded = [line.split("|", 1) for line in calls.read_text().splitlines()]
    assert all(path.startswith("/") for path, _cwd in recorded), recorded
    assert sum(cwd == _bash_env_path(home) for _path, cwd in recorded) >= 2, recorded


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
