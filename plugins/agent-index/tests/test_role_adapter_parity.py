from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from test_effective_config import _deliver_configuration, _repo
from test_effective_config import qualified_owner_reply as qualified_owner_reply
from test_runtime_gate import _fake_runtime, _fixture, _run

PLUGIN = Path(__file__).resolve().parents[1]

def _require_shell(shell):
    if shell == "bash" and os.name == "nt":
        pytest.skip("Native POSIX gate qualification is not Windows Bash")
    command = "powershell.exe" if shell == "powershell" else shell
    if not shutil.which(command):
        pytest.skip(f"{shell} is unavailable")


def _role_reader():
    spec = importlib.util.spec_from_file_location(
        "_index_role_adapter_parity", PLUGIN / "scripts" / "resolve-activation-role.py",
    )
    assert spec and spec.loader
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


@pytest.mark.parametrize("machine,expected", [("indexer", "host"), ("client", "client")])
def test_role_reader_uses_winning_knowledge_namespace(
    tmp_path, monkeypatch, qualified_owner_reply, machine, expected,
):
    repo = _repo(tmp_path / "repo", requires_external=True)
    base = repo / ".agent-index" / "config.yaml"
    base.parent.mkdir()
    base.write_text("corpus:\n  sources: [{name: 'git:example'}]\n", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    overlay = knowledge / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("indexers: [{machine: indexer, ssh: indexer}]\n", encoding="utf-8")
    _deliver_configuration(monkeypatch, repo, knowledge)
    monkeypatch.delenv("AGENT_INDEX_CONFIG_DATA_B64", raising=False)
    monkeypatch.delenv("AGENT_INDEX_REPO", raising=False)
    assert _role_reader().resolve_repository(str(repo), machine) == expected


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("machine,expected", [("indexer", "host"), ("client", "client")])
def test_payload_role_uses_merged_repository_reader(tmp_path, shell, machine, expected):
    _require_shell(shell)
    script, env = _fixture(tmp_path, shell)
    overlay = tmp_path / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("indexers: [{machine: indexer, ssh: indexer}]\n", encoding="utf-8")
    env["AGENT_INDEX_MACHINE"] = machine
    env["AGENT_INDEX_ROLE"] = "host"
    env["TEST_PYTHON"] = ""
    result = _run(shell, script, env, "role", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["role"] == expected
    assert not Path(env["AGENT_INDEX_HOME"]).exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("state,exit_code", [("ready", 0), ("failed", 1)])
def test_payload_readiness_relays_native_adapter_without_provisioning(
    tmp_path, shell, state, exit_code,
):
    _require_shell(shell)
    script, env = _fixture(tmp_path, shell)
    overlay = tmp_path / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("indexers: [{machine: indexer, ssh: indexer}]\n", encoding="utf-8")
    env["AGENT_INDEX_MACHINE"] = "client"
    runtime = _fake_runtime(tmp_path, (
        "import json, sys\n"
        "assert sys.argv[1:] == ['installer-readiness']\n"
        f"print(json.dumps({{'schema':'copilot-extensions.module-readiness','version':1,"
        f"'module':'agent-index/runtime','state':{state!r},'detail':'native adapter'}}))\n"
        f"raise SystemExit({exit_code})\n"
    ))
    env["TEST_PYTHON"] = runtime.as_posix() if shell == "bash" else str(runtime)
    result = _run(shell, script, env, "installer-readiness")
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == state
    assert not Path(env["AGENT_INDEX_HOME"]).exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("failure", ["exit", "invalid-config", "valid-empty"])
def test_payload_configuration_failure_is_not_empty_readiness(tmp_path, shell, failure):
    _require_shell(shell)
    script, env = _fixture(tmp_path, shell)
    env["TEST_PYTHON"] = ""
    if failure == "exit":
        (script.parent / "resolve_effective_config.py").write_text(
            "raise SystemExit(23)\n", encoding="utf-8",
        )
    elif failure == "invalid-config":
        (tmp_path / ".agent-index" / "config.yaml").write_text(
            "indexers: invalid\n", encoding="utf-8",
        )
    else:
        (tmp_path / ".agent-index" / "config.yaml").unlink()
    result = _run(shell, script, env, "installer-readiness")
    assert result.returncode == (0 if failure == "valid-empty" else 1), result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == (
        "configuration-empty" if failure == "valid-empty" else "failed"
    )
    assert not Path(env["AGENT_INDEX_HOME"]).exists()


@pytest.mark.parametrize("payload", ["", "not-base64", "e30="])
def test_explicit_role_reader_distinguishes_invalid_and_empty(payload, monkeypatch, capsys):
    reader = _role_reader()
    monkeypatch.setattr(sys, "argv", ["role-reader", "--data-b64", payload, "--machine", "client"])
    result = reader.main()
    if payload == "e30=":
        assert result == 0
        assert capsys.readouterr().out.strip() == "unconfigured"
    else:
        assert result == 1
