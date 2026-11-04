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
