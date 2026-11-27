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

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_NAME = _PLUGIN_ROOT.name
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# The legacy opt-in key, resolved to this plugin's literal name -- kept
# only to prove a stale leftover key from before this gate's removal is
# now inert (see test_sh_reconciles_even_with_stale_opt_in_key_present).
_OPT_IN_KEY = f"background_reconcile_{_PLUGIN_NAME}"
def _resolve_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
    if git_bash.is_file():
        return str(git_bash)
    # WSL launchers cannot consume these native Windows fixture paths.
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    filtered = os.pathsep.join(
        part for part in os.environ.get("PATH", "").split(os.pathsep)
        if "windowsapps" not in part.lower() and Path(part) != system32
    )
    return shutil.which("bash", path=filtered)


_BASH = _resolve_bash()


def test_hook_scripts_exist() -> None:
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_has_no_opt_in_gate() -> None:
    text = _PS1.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


def test_sh_has_no_opt_in_gate() -> None:
    text = _SH.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


def _make_fake_install(home: Path, name: str) -> Path:
    """A minimally 'drifted' install dir: deployed=1.0.0, payload=1.0.1.

    No runtime selector is present, so the hook must submit the fixed install
    recipe rather than trusting a package-version match alone.
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
    plugin_dir = root / "home/.copilot/installed-plugins/fixture-market" / name
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
    shutil.copyfile(_PS1, scripts_dir / _PS1.name)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts/versioned_runtime_native.ps1", scripts_dir / "versioned_runtime_native.ps1",
    )
    query = scripts_dir / "installation-context/json-query.awk"
    query.parent.mkdir()
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts/installation-context/json-query.awk", query,
    )
    driver = plugin_dir / "libs/update-driver/scripts"
    driver.mkdir(parents=True)
    (driver / "first-use.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Installation driver lifecycle contract: budget-install-v1\n"
        "# Installation driver lifecycle contract: budget-admission-deadline-v1\n"
        'printf "%s\\n" "$@" >"$HOME/driver-args"\n'
        '[[ "${BOOTSTRAP_DRIVER_STATUS:-0}" == 0 ]] || exit "$BOOTSTRAP_DRIVER_STATUS"\n'
        'printf "%s\\n" "${BOOTSTRAP_DRIVER_ACK:-launched:42 log:'
        '$1/first-use-12345678-1234-1234-1234-123456789abc.log}"\n',
        encoding="utf-8",
    )
    acknowledgement = json.dumps({
        "launched": True, "pid": 42, "created": 123,
        "log": str(root / "home/.copilot-extensions/update-driver"
                   / "first-use-12345678123412341234123456789abc.log"),
    }).replace("'", "''")
    (driver / "first-use.ps1").write_text(
        "# Installation driver lifecycle contract: budget-install-v1\n"
        "param($Root, $Payload, $RuntimeRoot, $Project, $InstallerAction)\n"
        "[IO.File]::WriteAllLines([IO.Path]::Combine($env:USERPROFILE, 'driver-args'), "
        "@($Root, $Payload, $RuntimeRoot, $Project, '--installer-action', $InstallerAction))\n"
        "if ($env:BOOTSTRAP_DRIVER_STATUS) { exit ([int]$env:BOOTSTRAP_DRIVER_STATUS) }\n"
        "if ($env:BOOTSTRAP_DRIVER_ACK) { [Console]::Out.WriteLine($env:BOOTSTRAP_DRIVER_ACK) } else {\n"
        f"[Console]::Out.WriteLine('{acknowledgement}')\n"
        "}\nexit 0\n",
        encoding="utf-8",
    )
    return plugin_dir


def _clean_env(overrides: dict[str, str]) -> dict[str, str]:
    """A subprocess env with the Windows Store's python3.exe alias stub (a
    no-op unless Python is installed via the Store) removed from PATH -- it
    can shadow the real interpreter ahead of it, which would make bash's
    ``command -v python3`` resolve to a dud and silently short-circuit this
    hook before it ever reaches the gate under test."""
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith(("COPILOT_", "BUDGET_GUIDANCE_", "UPDATE_DRIVER_")):
            del env[key]
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    env.update(overrides)
    return env


@pytest.mark.skipif(_BASH is None or not sys.platform.startswith("linux"), reason="native Linux admission")
def test_sh_reconciles_without_any_opt_in_config(tmp_path: Path) -> None:
    """No .copilot-extensions/config.yaml at all -- reconcile must still
    proceed unconditionally now that the opt-in gate is gone."""
    assert _BASH is not None
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
    assert "SKIPPED" not in result.stderr, result.stderr
    assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    assert "bootstrap install supervision launched" in result.stderr, result.stderr
    arguments = (home / "driver-args").read_text().splitlines()
    assert arguments[:6] == [
        str(home / ".copilot-extensions/update-driver"), str(plugin_dir),
        str(home / ".budget-guidance"), str(project), "--installer-action", "install",
    ]
    assert arguments[6] == "--admission-deadline" and float(arguments[7]) > 0
    assert not (home / ".budget-guidance/reconcile.lock").exists()


@pytest.mark.skipif(_BASH is None or not sys.platform.startswith("linux"), reason="native Linux admission")
def test_sh_reconciles_even_with_stale_opt_in_key_present(tmp_path: Path) -> None:
    """A leftover `background_reconcile_<plugin>: false` from before this
    gate's removal must not resurrect the old skip behavior -- the key is
    now inert."""
    assert _BASH is not None
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: false\n", encoding="utf-8"
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
    assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    assert "bootstrap install supervision launched" in result.stderr, result.stderr
    assert (home / "driver-args").is_file()


def _run_hook(
    tmp_path: Path, host: str, *, overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    shell = _BASH if host == "bash" else shutil.which(host)
    if shell is None or (host == "bash" and not sys.platform.startswith("linux")):
        pytest.skip(f"{host} unavailable for this native fixture")
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project with spaces"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    installation = _make_fake_install(home, _PLUGIN_NAME)
    environment = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home), **(overrides or {}),
    })
    command = (
        [shell, str(payload / "scripts/bootstrap-check.sh")] if host == "bash" else
        [shell, "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts/bootstrap-check.ps1")]
    )
    result = subprocess.run(
        command, cwd=project, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    return result, home, payload, installation


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
def test_driver_submission_retains_fixed_install_action_and_project(tmp_path: Path, host: str) -> None:
    result, home, payload, installation = _run_hook(tmp_path, host)
    assert result.returncode == 0 and result.stdout == "{}", (
        result.stdout + result.stderr
        + f"\nSynthetic driver entered: {(home / 'driver-args').is_file()}"
    )
    assert "bootstrap install supervision launched" in result.stderr
    assert "Launch is not durable request acceptance" in result.stderr
    arguments = (home / "driver-args").read_text(encoding="utf-8-sig").splitlines()
    assert arguments[:6] == [
        str(home / ".copilot-extensions/update-driver"), str(payload),
        str(installation), str(tmp_path / "project with spaces"), "--installer-action", "install",
    ]
    if host == "bash":
        assert arguments[6] == "--admission-deadline" and float(arguments[7]) > 0
    else:
        assert len(arguments) == 6
    assert not (installation / "reconcile.lock").exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
@pytest.mark.parametrize("fault", ["exit", "malformed", "foreign-log"])
def test_driver_failure_never_falls_back_to_raw_installer(tmp_path: Path, host: str, fault: str) -> None:
    overrides = {
        "exit": {"BOOTSTRAP_DRIVER_STATUS": "19"},
        "malformed": {"BOOTSTRAP_DRIVER_ACK": "not-an-ack"},
        "foreign-log": {"BOOTSTRAP_DRIVER_ACK": (
            json.dumps({"launched": True, "pid": 42, "created": 123, "log": "/foreign/owner.log"})
            if host != "bash" and os.name == "nt" else "launched:42 log:/foreign/owner.log"
        )},
    }[fault]
    result, home, _payload, installation = _run_hook(tmp_path, host, overrides=overrides)
    assert result.returncode == 3 and result.stdout == "{}", result.stdout + result.stderr
    assert (home / "driver-args").exists()
    assert "supervision launched" not in result.stderr
    assert "stub-install-ran" not in result.stdout + result.stderr
    assert not (installation / "reconcile.lock").exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
@pytest.mark.parametrize("gate", ["no-self", "pinned", "context", "nested"])
def test_automatic_driver_submission_preserves_context_gates(tmp_path: Path, host: str, gate: str) -> None:
    key, value = {
        "no-self": ("BUDGET_GUIDANCE_NO_SELFPROVISION", "1"),
        "pinned": ("BUDGET_GUIDANCE_PINNED_CONTEXT", "pinned"),
        "context": ("COPILOT_EXTENSIONS_CONTEXT", "selected"),
        "nested": ("UPDATE_DRIVER_WITNESS", "existing-owner"),
    }[gate]
    result, home, _payload, installation = _run_hook(tmp_path, host, overrides={key: value})
    assert result.returncode == (3 if gate == "nested" else 0)
    assert result.stdout == "{}", result.stdout + result.stderr
    assert not (home / "driver-args").exists()
    assert not (installation / "reconcile.lock").exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
@pytest.mark.parametrize("managed_available", [True, False])
def test_managed_runtime_requires_prepared_uv_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_available: bool, host: str,
) -> None:
    shell = _BASH if host == "bash" else shutil.which(host)
    if shell is None or (host == "bash" and not sys.platform.startswith("linux")):
        pytest.skip(f"{host} unavailable for this native fixture")
    monkeypatch.syspath_prepend(str(_PLUGIN_ROOT.parents[1] / "tools"))
    helper = importlib.import_module("test_native_wheel_installers")
    helper.assert_native_bootstrap(
        tmp_path, monkeypatch, _PLUGIN_NAME, shell=shell, managed_available=managed_available,
        powershell=host != "bash",
    )


@pytest.mark.parametrize("record", ["recent", "expired", "malformed", "directory"])
@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
def test_legacy_reconcile_record_never_authorizes_process_retirement(
    tmp_path: Path, record: str, host: str,
) -> None:
    shell = _BASH if host == "bash" else shutil.which(host)
    if shell is None or (host == "bash" and os.name == "nt"):
        pytest.skip(f"{host} unavailable for this native fixture")
    home = tmp_path / "home"
    home.mkdir()
    installation = _make_fake_install(home, _PLUGIN_NAME)
    payload = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    scripts = payload / "scripts"
    shutil.copy2(_PS1, scripts / _PS1.name)
    lock = installation / "reconcile.lock"
    if record == "directory":
        lock.mkdir()
    else:
        lock.write_text("invalid\n" if record == "malformed" else "12345\n")
    if record != "recent":
        os.utime(lock, (1_000_000_000, 1_000_000_000))
    before = lock.stat().st_mtime_ns
    probe = tmp_path / "process-probe"
    install = tmp_path / "installer-ran"
    environment = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "BOOTSTRAP_PROBE": str(probe),
        "BOOTSTRAP_INSTALL": str(install), "COPILOT_EXTENSIONS_CONTEXT": "",
    })
    if host == "bash":
        injection = tmp_path / "fixture.sh"
        injection.write_text(
            'kill() { printf "%s\\n" "$*" >>"$BOOTSTRAP_PROBE"; return 0; }\n',
        )
        environment["BASH_ENV"] = str(injection)
        (scripts / "install.sh").write_text(
            '#!/usr/bin/env bash\nprintf ran >"$BOOTSTRAP_INSTALL"\n',
        )
        command = [shell, str(scripts / _SH.name)]
    else:
        (scripts / "install.ps1").write_text(
            "[IO.File]::WriteAllText($env:BOOTSTRAP_INSTALL, 'ran')\n",
        )
        entry = tmp_path / "fixture.ps1"
        entry.write_text(
            "function Get-Process { [IO.File]::AppendAllText($env:BOOTSTRAP_PROBE, 'get'); $true }\n"
            "function Stop-Process { [IO.File]::AppendAllText($env:BOOTSTRAP_PROBE, 'stop') }\n"
            "function Start-Process { [IO.File]::WriteAllText($env:BOOTSTRAP_INSTALL, 'launch') }\n"
            "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/bootstrap-check.ps1')\n",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(entry)]
    result = subprocess.run(
        command, cwd=home, env=environment, capture_output=True, text=True,
        timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}", result.stdout + result.stderr
    assert "legacy reconcile ownership is unsettled" in result.stderr
    assert not probe.exists() and not install.exists()
    assert lock.stat().st_mtime_ns == before
    if record == "directory":
        assert lock.is_dir()
    else:
        assert lock.read_text() == ("invalid\n" if record == "malformed" else "12345\n")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
