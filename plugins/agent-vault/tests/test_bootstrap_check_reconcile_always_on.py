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
_PLUGIN_NAME = _PLUGIN_ROOT.name
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# The legacy opt-in key, resolved to this plugin's literal name -- kept
# only to prove a stale leftover key from before this gate's removal is
# now inert (see test_sh_reconciles_even_with_stale_opt_in_key_present).
_OPT_IN_KEY = f"background_reconcile_{_PLUGIN_NAME}"
# Resolve bash to its FULL path rather than invoking the bare command name: on
# Windows, a Windows App Execution Alias can intercept a bare "bash.exe"
# process-creation call (routing it to WSL) even when shutil.which() finds
# Git Bash first on PATH -- the alias interception happens below PATH search,
# at CreateProcess time, unless the explicit resolved path is used.
_BASH = shutil.which("bash")
if (
    os.name == "nt" and _BASH and os.environ.get("SystemRoot")
    and Path(_BASH).resolve().is_relative_to(Path(os.environ["SystemRoot"]).resolve())
):
    # The Windows WSL launcher cannot execute these native Windows fixture paths.
    _BASH = None


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
    """Record fixed-driver handoff, never install a real runtime or service."""
    plugin_dir = root / "plugin"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (plugin_dir / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    for filename in (
        "bootstrap-check.sh", "bootstrap-check.ps1",
        "runtime-environment.sh", "runtime-environment.ps1",
        "bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1",
    ):
        shutil.copyfile(_PLUGIN_ROOT / "scripts" / filename, scripts_dir / filename)
    (scripts_dir / "install.sh").write_text(
        '#!/usr/bin/env bash\nprintf inline >"$(dirname "$0")/inline-entered"\nexit 99\n',
        encoding="utf-8",
    )
    (scripts_dir / "init.ps1").write_text(
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'inline-entered'), 'inline')\nexit 99\n",
        encoding="utf-8",
    )
    driver = plugin_dir / "libs" / "update-driver" / "scripts"
    driver.mkdir(parents=True)
    (driver / "first-use.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# Installation driver lifecycle contract: vault-stamp-v1\n"
        '[[ "$5" == --installer-action ]] || exit 91\n'
        'printf "%s\\n" "$1" "$2" "$3" "$4" "$6" '
        '"${AGENT_VAULT_INSTALLATION_ID-<absent>}" "${AGENT_VAULT_PIPE-<absent>}" >"$4/handoff"\n'
        'if [[ -z "${BOOTSTRAP_TEST_NO_ACK:-}" ]]; then\n'
        '  ack_root="$1"; [[ -z "${BOOTSTRAP_TEST_FOREIGN_ACK:-}" ]] || ack_root="$4"\n'
        '  printf "launched:424242 log:%s/first-use-00000000-0000-0000-0000-000000000000.log\\n" "$ack_root"\n'
        'fi\n'
        'exit "${BOOTSTRAP_TEST_DRIVER_EXIT_CODE:-0}"\n',
        encoding="utf-8",
    )
    (driver / "first-use.ps1").write_text(
        "# Installation driver lifecycle contract: vault-stamp-v1\n"
        "param([string]$Root,[string]$Payload,[string]$RuntimeRoot,[string]$Project,"
        "[string]$InstallerAction)\n"
        "$id = [Environment]::GetEnvironmentVariable('AGENT_VAULT_INSTALLATION_ID')\n"
        "$pipe = [Environment]::GetEnvironmentVariable('AGENT_VAULT_PIPE')\n"
        "if ($null -eq $id) { $id = '<absent>' }; if ($null -eq $pipe) { $pipe = '<absent>' }\n"
        "[IO.File]::WriteAllLines((Join-Path $Project 'handoff'), "
        "[string[]]@($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction,$id,$pipe), "
        "(New-Object Text.UTF8Encoding($false)))\n"
        "if (-not $env:BOOTSTRAP_TEST_NO_ACK) {\n"
        "$ackRoot = if ($env:BOOTSTRAP_TEST_FOREIGN_ACK) { $Project } else { $Root }\n"
        "[Console]::Out.WriteLine((@{launched=$true;project=$Project;pid=424242;created=1;"
        "log=(Join-Path $ackRoot 'first-use-00000000000000000000000000000000.log')} | ConvertTo-Json -Compress))\n"
        "}\n"
        "exit ([int]$env:BOOTSTRAP_TEST_DRIVER_EXIT_CODE)\n",
        encoding="utf-8",
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
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "AGENT_VAULT_NO_SELFPROVISION",
        "AGENT_VAULT_PINNED_CONTEXT", "UPDATE_DRIVER_WITNESS", "BOOTSTRAP_TEST_DRIVER_EXIT_CODE",
        "BOOTSTRAP_TEST_NO_ACK", "BOOTSTRAP_TEST_FOREIGN_ACK",
    ):
        env.pop(key, None)
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    env.update(overrides)
    return env


@pytest.mark.skipif(os.name == "nt" or _BASH is None, reason="POSIX bootstrap; Windows uses PowerShell")
def test_sh_reconciles_without_any_opt_in_config(tmp_path):
    """No .copilot-extensions/config.yaml at all -- reconcile must still
    proceed unconditionally now that the opt-in gate is gone."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    plugin_dir = _make_fake_plugin(tmp_path, _PLUGIN_NAME)
    _make_fake_install(home, _PLUGIN_NAME)

    env = _clean_env({
        "HOME": str(home), "COPILOT_PROJECT_DIR": str(project),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })

    result = subprocess.run(
        [_BASH, str(plugin_dir / "scripts" / "bootstrap-check.sh")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "bootstrap provision supervision launched" in result.stderr, result.stderr
    assert (project / "handoff").read_text().splitlines()[4] == "provision"
    assert not (plugin_dir / "scripts" / "inline-entered").exists()


@pytest.mark.skipif(os.name == "nt" or _BASH is None, reason="POSIX bootstrap; Windows uses PowerShell")
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
    _make_fake_install(home, _PLUGIN_NAME)

    env = _clean_env({
        "HOME": str(home), "COPILOT_PROJECT_DIR": str(project),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })

    result = subprocess.run(
        [_BASH, str(plugin_dir / "scripts" / "bootstrap-check.sh")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "SKIPPED" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "bootstrap provision supervision launched" in result.stderr, result.stderr
    assert (project / "handoff").read_text().splitlines()[4] == "provision"
    assert not (plugin_dir / "scripts" / "inline-entered").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
