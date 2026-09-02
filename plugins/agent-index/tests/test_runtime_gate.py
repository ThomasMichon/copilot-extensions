from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN / "src"


def test_reconcile_update_uses_installer_declared_arguments() -> None:
    manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    readiness = json.loads(
        (PLUGIN / "installer-readiness.json").read_text(encoding="utf-8")
    )
    installer = readiness["modules"][0]["installer"]

    assert "zeroDowntimeUpdate" not in manifest
    assert installer["windows"]["arguments"] == ["update"]
    assert installer["linux"]["arguments"] == ["update"]
    assert installer["wsl"]["arguments"] == ["update"]


def test_runtime_gates_serialize_provisioning() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(
        encoding="utf-8"
    )
    assert 'flock 9' in posix
    assert 'mkdir "$PROVISION_LOCK_DIR"' in posix
    assert "[IO.File]::Open(" in powershell
    assert "[IO.FileShare]::None" in powershell


def test_activation_import_checks_use_target_slot_python() -> None:
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    posix_activate = posix.split("_versioned_activate() {", 1)[1].split(
        "_versioned_current() {", 1
    )[0]
    ps_activate = powershell.split("function Invoke-VersionedActivate {", 1)[1].split(
        "function Get-VersionedCurrent {", 1
    )[0]
    assert 'local py="$VENV_DIR/bin/python"' in posix_activate
    assert 'py="$LINK_DIR/bin/python"' not in posix_activate
    assert "$py = $VenvPython" in ps_activate
    assert "else { $LinkPython }" not in ps_activate


def _fixture(tmp_path: Path, shell: str) -> tuple[Path, dict[str, str]]:
    payload = tmp_path / f"payload-{shell}"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["AGENT_INDEX_HOME"] = str(tmp_path / f"home-{shell}")
    env["PYTHONPATH"] = str(SOURCE_ROOT)
    env.pop("AGENT_INDEX_ROLE", None)
    env.pop("AGENT_INDEX_CONFIG", None)
    if shell == "bash":
        shutil.copy2(PLUGIN / "scripts" / "runtime-gate.sh", scripts / "runtime-gate.sh")
        (scripts / "resolve-runtime.sh").write_text(
            'AGENT_RT_PY="${TEST_PYTHON:-}"\n', encoding="utf-8"
        )
        env["AGENT_INDEX_HOME"] = Path(env["AGENT_INDEX_HOME"]).as_posix()
        env["PYTHONPATH"] = SOURCE_ROOT.as_posix()
        env["TEST_PYTHON"] = Path(sys.executable).as_posix()
        return scripts / "runtime-gate.sh", env
    shutil.copy2(PLUGIN / "scripts" / "runtime-gate.ps1", scripts / "runtime-gate.ps1")
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n", encoding="utf-8"
    )
    env["TEST_PYTHON"] = sys.executable
    return scripts / "runtime-gate.ps1", env


def _run(shell: str, script: Path, env: dict[str, str], *args: str):
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX runtime-gate test")
        return subprocess.run(
            ["bash", script.as_posix(), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=script.parents[2],
        )
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is not installed")
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=script.parents[2],
    )


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_status_without_runtime_is_non_mutating_setup_required(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert payload["runtime"]["state"] == "absent"
    assert payload["schema_version"] == 1
    assert payload["version"] == "9.9.9"
    assert not home.exists()


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("runtime_state", ["stamped", "broken"])
def test_status_classifies_non_runnable_runtime(
    tmp_path: Path, shell: str, runtime_state: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])
    home.mkdir(parents=True)
    if runtime_state == "stamped":
        (home / "payload-dir").write_text("payload", encoding="utf-8")
    else:
        (home / "current-version").write_text("1.0.0\n", encoding="utf-8")
        slot = home / "versions" / "1.0.0"
        slot.mkdir(parents=True)
        (slot / "python-placeholder").write_text("", encoding="utf-8")

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime"]["state"] == runtime_state
    assert payload["setup_required"] is True


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_complete_runtime_without_role_reports_setup_required(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert payload["runtime"]["state"] == "ready"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_operational_command_without_role_is_blocked(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)

    result = _run(shell, script, env, "search", "anything", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_configured_role_with_missing_runtime_is_not_called_dormant(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    env["AGENT_INDEX_ROLE"] = "client"

    result = _run(shell, script, env, "status")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "runtime_unavailable"
    assert payload["setup_required"] is False
    assert payload["role"] == "client"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("role", ["client", "host"])
def test_configured_roles_are_reported_without_mutation(
    tmp_path: Path, shell: str, role: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["AGENT_INDEX_ROLE"] = role

    result = _run(shell, script, env, "role", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"role": role, "setup_required": False, "state": "ready"}


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_noninteractive_setup_requires_explicit_role(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""

    result = _run(shell, script, env, "setup", "--yes", "--json")

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"
    assert "explicit role choice" in payload["error"]
    assert "setup --single --yes" in payload["setup"]["noninteractive"][0]


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_authored_indexers_do_not_replace_noninteractive_setup_choice(
    tmp_path: Path, shell: str
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    repo = tmp_path / "repo"
    config = repo / ".agent-index" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("indexers:\n  - machine: box-a\n", encoding="utf-8")

    result = _run(
        shell, script, env, "setup", "--yes", "--repo", str(repo), "--json"
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["state"] == "setup_required"


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
@pytest.mark.parametrize("indexer_args", [["--indexer"], ["--indexer", "--yes"], ["--indexer="]])
def test_missing_indexer_value_does_not_provision(
    tmp_path: Path, shell: str, indexer_args: list[str]
) -> None:
    if shell == "bash" and shutil.which("bash") is None:
        pytest.skip("bash is not installed")
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    home = Path(env["AGENT_INDEX_HOME"])

    result = _run(shell, script, env, "setup", *indexer_args, "--yes", "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["state"] == "setup_required"
    assert not (home / "versions").exists()
