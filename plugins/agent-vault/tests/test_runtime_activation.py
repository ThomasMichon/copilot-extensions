"""Actual payload first-touch controls with no Vault runtime or backend."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import _clean_env, _make_fake_plugin
from .test_bootstrap_driver import native_bootstrap
from .test_identity_payload_gate import prepare_payload, invoke

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("configuration", [None, False, "invalid", "malformed"])
@pytest.mark.parametrize("entry", ["bootstrap", "readiness", "update"])
def test_native_inactive_or_invalid_first_touch_never_installs(tmp_path, configuration, entry):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    (tmp_path / ".agent-vault.json").unlink()
    if configuration is not None:
        (project / ".agent-vault.json").write_text(
            "{" if configuration == "malformed"
            else json.dumps({"enabled": configuration}), encoding="utf-8",
        )
    existing = home / ".agent-vault" / "retained-custody.json"
    if configuration is False:
        existing.parent.mkdir()
        existing.write_bytes(b'{"fixture":"retained service and database custody"}')
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    suffix = "ps1" if os.name == "nt" else "sh"
    if entry == "bootstrap":
        argv = native_bootstrap(payload, project, project)
    else:
        filename = ("installer-readiness." if entry == "readiness" else "install.") + suffix
        shutil.copyfile(PLUGIN / "scripts" / filename, payload / "scripts" / filename)
        if os.name == "nt":
            argv = [
                str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts" / filename),
            ]
        else:
            argv = ["bash", str(payload / "scripts" / filename)]
        if entry == "update":
            argv.append("update")
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    invalid = configuration in ("invalid", "malformed")
    assert (result.returncode != 0) is invalid, result.stdout + result.stderr
    if entry == "readiness":
        assert json.loads(result.stdout)["state"] == ("failed" if invalid else "configuration-empty")
    elif entry == "bootstrap":
        assert json.loads(result.stdout) == {}
    if configuration is False:
        assert existing.read_bytes() == b'{"fixture":"retained service and database custody"}'
        assert list(existing.parent.iterdir()) == [existing]
    else:
        assert not (home / ".agent-vault").exists()
    assert not (home / ".copilot-extensions").exists()
    assert not (project / "handoff").exists()
    assert not (payload / "scripts" / "inline-entered").exists()


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_command_first_touch_inactive_without_runtime(tmp_path, mode):
    home, payload, runtime, _identity, context = prepare_payload(tmp_path, mode)
    (tmp_path / ".agent-vault.json").unlink(missing_ok=True)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
    })
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    result = invoke(payload, env, tmp_path, "get", "Example", "password")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "inactive" in result.stderr
    readiness = invoke(payload, env, tmp_path, "installer-readiness")
    assert readiness.returncode == 0, readiness.stderr
    assert json.loads(readiness.stdout)["state"] == "configuration-empty"
    assert not (runtime / "versions").exists()
    assert not (runtime / ".provision-status").exists()


def test_actual_payload_reader_uses_bundled_sdk_without_site_packages(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(payload / "scripts" / "runtime-activation.py"),
         "--cwd", str(project)], env=env, cwd=project, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configured"
