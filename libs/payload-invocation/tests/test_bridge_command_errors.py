"""Native Bridge ingress failures use the published evidence owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "plugins" / "agent-bridge"


def _fixture(tmp_path: Path) -> tuple[list[str], dict[str, str], Path]:
    if sys.platform not in {"win32", "linux"}:
        pytest.skip("native command evidence supports Windows and Linux")
    windows = os.name == "nt"
    shell = shutil.which("powershell.exe" if windows else "bash")
    if shell is None:
        pytest.skip("native shell unavailable")
    suffix = "ps1" if windows else "sh"
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    gate = scripts / f"runtime-gate.{suffix}"
    shutil.copyfile(BRIDGE / "scripts" / gate.name, gate)
    provider = runpy.run_path(str(ROOT / "libs/payload-invocation/command_evidence.py"))
    evidence = scripts / "command-errors"
    evidence.mkdir()
    for name, content in provider["native_files"](ROOT).items():
        (evidence / name).write_bytes(content)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_BRIDGE_", "AGENT_RT_", "COPILOT_EXTENSIONS_", "UPDATE_DRIVER_"))
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        AGENT_BRIDGE_PAYLOAD_ROOT=str(payload),
        COMMAND_ERROR_PRIVATE_SENTINEL="private-environment-content",
    )
    command = [shell]
    if windows:
        command += ["-NoLogo", "-NoProfile", "-NonInteractive", "-File"]
    command += [str(gate), "private-argument-content"]
    return command, environment, home


def test_unsupported_native_platform_does_not_start_a_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(pytest.skip.Exception, match="Windows and Linux"):
        _fixture(tmp_path)
    assert not list(tmp_path.iterdir())


def _run(command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, env=environment, input="private-stdin-content", text=True,
        capture_output=True, timeout=15, cwd=environment.get("AGENT_BRIDGE_PAYLOAD_ROOT"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize("failure", ["payload", "support", "resolver"])
def test_native_entry_retains_failure_without_private_inputs(tmp_path: Path, failure: str) -> None:
    command, environment, home = _fixture(tmp_path)
    scripts = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"]) / "scripts"
    if failure == "payload":
        environment.pop("AGENT_BRIDGE_PAYLOAD_ROOT")
    elif failure == "resolver":
        suffix = "ps1" if os.name == "nt" else "sh"
        (scripts / f"install.{suffix}").write_text("", encoding="utf-8")
        (scripts / f"resolve-runtime.{suffix}").write_text("# older resolver\n", encoding="utf-8")
        context = scripts / "installation-context"
        context.mkdir()
        (context / f"installation-context.{suffix}").write_text("", encoding="utf-8")
        (context / "json-query.awk").write_text("", encoding="utf-8")
    result = _run(command, environment)
    assert result.returncode == 126, result.stderr
    assert result.stdout == ""
    assert "unavailable" in result.stderr
    log = home / ".copilot-extensions/logs/diagnostics/commands/command-errors.jsonl"
    text = log.read_text(encoding="utf-8")
    record, = [json.loads(line) for line in text.splitlines()]
    assert record["command"] == "agent-bridge"
    assert record["source"] == "agent-bridge-runtime-gate"
    assert record["exit_code"] == 126
    assert record["code"] == "entrypoint-unavailable"
    assert record["completeness"] == "producer-shaped"
    assert "private-" not in text
    assert not (home / ".agent-bridge").exists()
    if failure == "resolver":
        provider = runpy.run_path(str(ROOT / "libs/payload-invocation/command_evidence.py"))
        archive = tmp_path / "command-errors.pyz"
        archive.write_bytes(provider["archive"](ROOT))
        reader = _run([sys.executable, str(archive), "recent", "--limit", "1"], environment)
        assert reader.returncode == 0, reader.stderr
        discovery = json.loads(reader.stdout)
        assert discovery["unavailable"] == []
        assert discovery["records"] == [record]


@pytest.mark.parametrize("unavailable", ["assets", "empty-assets", "writer-failure", "storage"])
def test_missing_evidence_preserves_the_original_failure(tmp_path: Path, unavailable: str) -> None:
    command, environment, home = _fixture(tmp_path)
    scripts = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"]) / "scripts"
    if unavailable == "assets":
        (scripts / "command-errors/command-errors.ps1").unlink()
        (scripts / "command-errors/command-errors.sh").unlink()
    elif unavailable in {"empty-assets", "writer-failure"}:
        (scripts / "command-errors/command-errors.ps1").write_text(
            "" if unavailable == "empty-assets" else
            "function Write-CommandErrorEvidence { throw 'private-writer-content' }\n",
            encoding="utf-8",
        )
        (scripts / "command-errors/command-errors.sh").write_text(
            "" if unavailable == "empty-assets" else
            "command_error_write() { return 43; }\n",
            encoding="utf-8",
        )
    else:
        (home / ".copilot-extensions").write_text("blocked", encoding="utf-8")
    result = _run(command, environment)
    assert result.returncode == 126
    assert result.stdout == ""
    assert "installation-context runtime support is unavailable" in result.stderr
    assert "command evidence unavailable" in result.stderr


def test_context_failure_never_archives_opaque_diagnostics(tmp_path: Path) -> None:
    command, environment, home = _fixture(tmp_path)
    scripts = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"]) / "scripts"
    suffix = "ps1" if os.name == "nt" else "sh"
    (scripts / f"install.{suffix}").write_text("", encoding="utf-8")
    (scripts / f"resolve-runtime.{suffix}").write_text(
        "# Trace contract: normal/read-only/ready-v1\n", encoding="utf-8",
    )
    context = scripts / "installation-context"
    context.mkdir()
    (context / "json-query.awk").write_text("", encoding="utf-8")
    (context / f"installation-context.{suffix}").write_text(
        "throw 'private-exception-content'\n" if os.name == "nt" else
        "printf 'private-exception-content\\n' >&2\nexit 17\n",
        encoding="utf-8",
    )
    result = _run(command, environment)
    assert result.returncode == (1 if os.name == "nt" else 126)
    assert result.stdout == ""
    assert "private-exception-content" in result.stderr
    log = home / ".copilot-extensions/logs/diagnostics/commands/command-errors.jsonl"
    text = log.read_text(encoding="utf-8")
    record, = [json.loads(line) for line in text.splitlines()]
    assert record["exit_code"] == (None if os.name == "nt" else 126)
    assert "private-" not in text


@pytest.mark.parametrize("code", [0, 23])
def test_prepared_command_preserves_streams_arguments_and_result(tmp_path: Path, code: int) -> None:
    command, environment, home = _fixture(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    scripts = payload / "scripts"
    context = scripts / "installation-context"
    context.mkdir()
    shutil.copyfile(
        BRIDGE / "scripts/installation-context/json-query.awk", context / "json-query.awk",
    )
    status = json.dumps({
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "legacy": {"root": str(home / ".agent-bridge")},
    })
    environment.update(TEST_GATE_STATUS=status, TEST_RUNTIME_PYTHON=sys.executable)
    if os.name == "nt":
        (context / "installation-context.ps1").write_text(
            "param($Action,$PayloadRoot,$PluginId,$LegacyRoot)\n"
            "$env:TEST_GATE_STATUS\n$global:LASTEXITCODE=0\n", encoding="utf-8",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "# Trace contract: normal/read-only/ready-v1\nparam($TraceMode)\n"
            "$script:AgentRtPy=$env:TEST_RUNTIME_PYTHON\n", encoding="utf-8",
        )
        (scripts / "install.ps1").write_text("throw 'must not provision'\n", encoding="utf-8")
    else:
        (context / "installation-context.sh").write_text(
            'printf "%s\\n" "$TEST_GATE_STATUS"\n', encoding="utf-8",
        )
        (scripts / "resolve-runtime.sh").write_text(
            '# Trace contract: normal/read-only/ready-v1\nAGENT_RT_PY="$TEST_RUNTIME_PYTHON"\n',
            encoding="utf-8",
        )
        (scripts / "install.sh").write_text("exit 97\n", encoding="utf-8")
    (payload / "agent_bridge.py").write_text(
        "import json, sys\n"
        "print(json.dumps([sys.argv[1:], sys.stdin.read()], ensure_ascii=True))\n"
        "print('native-stderr', file=sys.stderr)\n"
        f"raise SystemExit({code})\n", encoding="utf-8",
    )
    command += ["", "a b", 'say "hi"', "tr\\", "\u03bb"]
    result = _run(command, environment)
    assert result.returncode == code, result.stderr
    assert json.loads(result.stdout) == [
        ["private-argument-content", "", "a b", 'say "hi"', "tr\\", "\u03bb"],
        "private-stdin-content",
    ]
    assert result.stderr == "native-stderr\n"
    log = home / ".copilot-extensions/logs/diagnostics/commands/command-errors.jsonl"
    if code == 0 or os.name != "nt":
        # POSIX exec transfers observation to the receiving runtime.
        assert not log.exists()
    else:
        record, = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert record["exit_code"] == code
        assert record["code"] == "native-command-failed"


def test_first_use_driver_failure_keeps_its_result_and_evidence(tmp_path: Path) -> None:
    command, environment, home = _fixture(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    scripts = payload / "scripts"
    context = scripts / "installation-context"
    context.mkdir()
    runtime = home / ".agent-bridge"
    environment["TEST_GATE_STATUS"] = json.dumps({
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "legacy": {"root": str(runtime)}, "allowMutation": True,
        "deployment": {
            "allowPluginPublication": True,
            "path": str(runtime / "runtime-deployment.json"),
        },
    })
    shutil.copyfile(
        BRIDGE / "scripts/installation-context/json-query.awk", context / "json-query.awk",
    )
    driver = payload / "libs/update-driver/scripts"
    driver.mkdir(parents=True)
    contract = "# Installation driver lifecycle contract: bridge-command-v1\n"
    if os.name == "nt":
        (context / "installation-context.ps1").write_text(
            "param($Action,$PayloadRoot,$PluginId,$LegacyRoot)\n"
            "$env:TEST_GATE_STATUS\n$global:LASTEXITCODE=0\n", encoding="utf-8",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "# Trace contract: normal/read-only/ready-v1\nparam($TraceMode)\n",
            encoding="utf-8",
        )
        (scripts / "install.ps1").write_text("throw 'must not provision'\n", encoding="utf-8")
        (driver / "first-use.ps1").write_text(
            contract + "param($Root,$Payload,$RuntimeRoot,$Project,$InstallerAction,[switch]$Wait)\n"
            "[Console]::Error.WriteLine('driver-stderr'); exit 37\n", encoding="utf-8",
        )
    else:
        (context / "installation-context.sh").write_text(
            'printf "%s\\n" "$TEST_GATE_STATUS"\n', encoding="utf-8",
        )
        (scripts / "resolve-runtime.sh").write_text(
            "# Trace contract: normal/read-only/ready-v1\nAGENT_RT_PY=''\n", encoding="utf-8",
        )
        (scripts / "install.sh").write_text("exit 97\n", encoding="utf-8")
        (driver / "first-use.sh").write_text(
            contract + "printf 'driver-stderr\\n' >&2\nexit 37\n", encoding="utf-8",
        )
    result = _run(command, environment)
    assert result.returncode == 37, result.stderr
    assert result.stdout == ""
    assert "driver-stderr" in result.stderr
    log = home / ".copilot-extensions/logs/diagnostics/commands/command-errors.jsonl"
    record, = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert record["exit_code"] == 37
    assert record["code"] == "bootstrap-failed"
