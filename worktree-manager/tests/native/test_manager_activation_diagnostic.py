"""A failed read remains failed while its caught exception becomes inspectable."""

import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

pytestmark = pytest.mark.timeout(90)
sys.path.insert(0, str(Path(__file__).parent))
from manager_suite_diagnostics import activation_probe


def test_activation_diagnostic_retains_original_reader_failure(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "setup.json").write_text(
        json.dumps({"base": sys.executable}), encoding="utf-8",
    )
    reader = (
        tmp_path / "h" / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-vault" / "scripts" / "runtime-activation.py"
    )
    reader.parent.mkdir(parents=True)
    original = (
        "import json\n"
        "try:\n raise RuntimeError('controlled configuration refusal')\n"
        "except RuntimeError:\n print(json.dumps({'state':'failed'}))\n"
        "raise SystemExit(3)\n"
    )
    reader.write_text(original, encoding="utf-8")
    result = activation_probe(tmp_path, dict(os.environ))
    assert result["activation_probe_returncode"] == 3
    assert json.loads(result["stdout"]) == {"state": "failed"}
    events = json.loads(result["stderr"])["activation_exceptions"]
    assert any(event["type"] == "RuntimeError" for event in events)
    assert reader.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("failure", ["missing-payload", "timeout", "output-limit"])
def test_secondary_probe_failure_preserves_original_command(
    tmp_path, monkeypatch, capsys, check, failure,
):
    from agent_procutil import _caller_owned, owned_process
    import manager_suite_fixture

    original = subprocess.CompletedProcess(
        ["original-update"], 17, "original stdout",
        "Agent Vault activation configuration could not be resolved",
    )
    monkeypatch.setattr(_caller_owned, "run", lambda *args, **kwargs: original)
    if failure != "missing-payload":
        (tmp_path / "setup.json").write_text(json.dumps({"base": sys.executable}))
        reader = (
            tmp_path / "h" / ".copilot" / "installed-plugins" / "copilot-extensions"
            / "agent-vault" / "scripts" / "runtime-activation.py"
        )
        reader.parent.mkdir(parents=True)
        reader.write_text("")

        def fail_probe(*args, **kwargs):
            kwargs["on_settled"]()
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args[0], 30)
            raise owned_process.OutputLimitExceeded("stderr", 64 * 1024)

        monkeypatch.setattr(owned_process, "run_caller_owned", fail_probe)
    if check:
        with pytest.raises(AssertionError, match="original stdout") as raised:
            manager_suite_fixture._run(["original-update"], tmp_path)
        assert original.stderr in str(raised.value)
    else:
        assert manager_suite_fixture._run(["original-update"], tmp_path, check=False) is original
    assert "activation_probe_error" in json.loads(capsys.readouterr().out)


def test_probe_cleanup_failure_retains_exact_custody(tmp_path, monkeypatch):
    from agent_procutil import owned_process

    (tmp_path / "setup.json").write_text(json.dumps({"base": sys.executable}))
    reader = (
        tmp_path / "h" / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-vault" / "scripts" / "runtime-activation.py"
    )
    reader.parent.mkdir(parents=True)
    reader.write_text("")
    process, job = object(), object()
    failure = owned_process.OwnedCleanupError(process, job)

    def fail_probe(*args, **kwargs):
        raise failure

    monkeypatch.setattr(owned_process, "run_caller_owned", fail_probe)
    with pytest.raises(owned_process.OwnedCleanupError) as raised:
        activation_probe(tmp_path, dict(os.environ))
    assert raised.value is failure
    assert raised.value.process is process
    assert raised.value.job is job


def test_instrumented_delivery_captures_original_peer_refusal(tmp_path, monkeypatch):
    from agent_procutil.owned_process import run_caller_owned
    from manager_suite_artifacts import _DELIVERY

    home = tmp_path / "h"
    config = home / ".copilot" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"installedPlugins":[]}')
    payload = tmp_path / "published" / "plugins" / "agent-vault"
    reader = payload / "scripts" / "runtime-activation.py"
    reader.parent.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"version":"1.0.0"}')
    roster = tmp_path / "published" / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({
        "plugins": [{"name": "agent-vault", "source": "plugins/agent-vault"}],
    }))
    reader.write_text(
        'import json, sys, subprocess\n'
        'def read_owner_context():\n'
        ' result = subprocess.CompletedProcess([], 7, b"", b"controlled refusal")\n'
        ' raise RuntimeError("owner refused")\n'
        'def main():\n'
        ' try:\n  read_owner_context()\n'
        ' except RuntimeError:\n  print(json.dumps({"state":"failed"}))\n'
        ' return 3\n'
        '\nif __name__ == "__main__":\n raise SystemExit(main())\n',
    )
    monkeypatch.setenv("SUITE_FIXTURE", str(tmp_path))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(sys, "argv", ["delivery", "copilot", "plugin", "update", "agent-vault"])
    with pytest.raises(SystemExit) as delivered:
        exec(compile(_DELIVERY, "fixture-delivery", "exec"), {})
    assert delivered.value.code == 0
    installed = (
        home / ".copilot" / "installed-plugins" / "copilot-extensions"
        / "agent-vault" / "scripts" / "runtime-activation.py"
    )
    result = run_caller_owned(
        [sys.executable, "-I", "-B", str(installed)],
        timeout=30, max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 3
    assert json.loads(result.stdout) == {"state": "failed"}
    assert json.loads(result.stderr) == {
        "fixture_activation_peer_returncode": 7,
        "fixture_activation_peer_stderr": "controlled refusal",
    }
