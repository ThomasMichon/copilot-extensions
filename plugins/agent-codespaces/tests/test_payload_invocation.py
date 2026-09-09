"""Payload-local invocation and explicit installation-context gates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_codespaces_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-codespaces",
        "module": "agent_codespaces",
        "legacyRuntimeRoot": ".agent-codespaces",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_CODESPACES_NO_SELFPROVISION",
        "purpose": "Manage GitHub Codespaces lifecycle transport and dispatch",
        "payloadRootEnv": "AGENT_CODESPACES_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }
    posix = (PLUGIN / "bin" / "agent-codespaces").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-codespaces.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in posix
    assert "payload-dir" not in powershell


def test_runtime_gates_keep_first_use_provisioning_lock() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(encoding="utf-8")
    assert ".provision.lock" in posix
    assert ".provision.lock" in powershell


def test_runtime_dir_honors_agent_codespaces_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "AGENT_CODESPACES_HOME",
        str(tmp_path / "cell" / "plugins" / "agent-codespaces"),
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "sandbox-home"))

    from agent_codespaces import config

    assert config._runtime_dir() == tmp_path / "cell" / "plugins" / "agent-codespaces"


def test_invoke_runtime_root_falls_back_to_agent_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_CODESPACES_HOME", raising=False)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "sandbox-home"))

    from agent_codespaces import _invoke

    assert _invoke._runtime_root() == tmp_path / "sandbox-home" / ".agent-codespaces"


# -- _venv_python / _resolve_runtime_python (the versioned-runtime resolver) --
# Regression coverage for the same class of bug procutil.resolve_own_runtime_python
# fixed in agent-dispatch: a self-relaunch/module-argv spawn site must resolve
# the installed CURRENT-VERSION slot, never a stale hard-coded legacy path (the
# old ``.venv/`` layout no longer exists once a runtime migrates to
# ``versions/<version>/``) and never fall through to whatever interpreter
# happens to be running the current process.


def _make_slot(root, version: str, *, complete: bool = False):
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    py = root / "versions" / version / sub
    py.parent.mkdir(parents=True)
    py.write_text("")
    if complete:
        (root / "versions" / version / ".install-complete.json").write_text("{}")
    return py


def test_resolve_runtime_python_prefers_current_version_marker(tmp_path) -> None:
    from agent_codespaces import _invoke

    root = tmp_path / ".agent-codespaces"
    py = _make_slot(root, "0.4.0-dev118")
    _make_slot(root, "0.4.0-dev999")  # newer slot exists but marker wins
    (root / "current-version").write_text("0.4.0-dev118")
    assert _invoke._resolve_runtime_python(root) == py


def test_resolve_runtime_python_ignores_legacy_venv_layout(tmp_path) -> None:
    """A bare ``.venv/`` dir (the old hard-coded path) is NOT a versioned slot
    and must never resolve -- the exact layout that silently degraded every
    self-relaunch to ``sys.executable`` once a runtime migrated away from it."""
    from agent_codespaces import _invoke

    root = tmp_path / ".agent-codespaces"
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    assert _invoke._resolve_runtime_python(root) is None


def test_venv_python_uses_installed_slot_over_sys_executable(
    monkeypatch, tmp_path,
) -> None:
    from agent_codespaces import _invoke

    root = tmp_path / ".agent-codespaces"
    py = _make_slot(root, "0.4.0-dev118")
    (root / "current-version").write_text("0.4.0-dev118")
    monkeypatch.setattr(_invoke, "_runtime_root", lambda: root)
    assert _invoke._venv_python() == str(py)


def test_venv_python_falls_back_to_sys_executable_when_unresolved(
    monkeypatch, tmp_path,
) -> None:
    from agent_codespaces import _invoke

    monkeypatch.setattr(_invoke, "_runtime_root", lambda: tmp_path / "empty")
    assert _invoke._venv_python() == sys.executable


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_selects_cell_runtime(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "cell-a" / "plugins" / "agent-codespaces"
    payload = _fake_runtime_gate_payload(
        tmp_path / "market-a" / "agent-codespaces",
        runtime_root=runtime,
    )
    (payload / "bin").mkdir(parents=True, exist_ok=True)
    (payload / "bin" / "agent-codespaces").write_text(
        (PLUGIN / "bin" / "agent-codespaces").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (payload / "bin" / "agent-codespaces").chmod(0o755)

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-codespaces"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    context = runtime / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
            "TEST_AGENT_RT_PY": sys.executable,
            "TEST_STATUS_JSON": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "runtimeRoot": str(runtime),
                    "context": str(context),
                    "installGeneration": 7,
                    "policy": {"enabled": True},
                    "legacy": {"tombstone": None, "disposition": "inactive"},
                }
            ),
            "TEST_VALIDATE_JSON": json.dumps({"generation": 7}),
        }
    )

    result = subprocess.run(
        [str(payload / "bin" / "agent-codespaces"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"Runtime dir: {runtime}" in result.stdout
    assert not shadow_marker.exists()


def _fake_runtime_gate_payload(
    root: Path,
    *,
    runtime_root: Path,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"name": "agent-codespaces", "version": "test"}),
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    subdir = scripts / "installation-context"
    subdir.mkdir()
    (scripts / "runtime-gate.sh").write_text(
        (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (scripts / "resolve-runtime.sh").write_text(
        '#!/usr/bin/env bash\nAGENT_RT_PY="$TEST_AGENT_RT_PY"\n',
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'installer should not run in this test\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    (subdir / "json-query.awk").write_text(
        (PLUGIN / "scripts" / "installation-context" / "json-query.awk").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (subdir / "installation-context.sh").write_text(
        "#!/usr/bin/env bash\n"
        "case \"$1\" in\n"
        "  status) printf '%s' \"$TEST_STATUS_JSON\" ;;\n"
        "  validate) printf '%s' \"$TEST_VALIDATE_JSON\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    for path in (
        scripts / "runtime-gate.sh",
        scripts / "resolve-runtime.sh",
        scripts / "install.sh",
        subdir / "installation-context.sh",
    ):
        path.chmod(0o755)
    runtime_root.mkdir(parents=True, exist_ok=True)
    return root
