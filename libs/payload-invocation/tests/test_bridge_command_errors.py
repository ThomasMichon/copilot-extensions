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


def _prepared_runtime(tmp_path: Path) -> tuple[list[str], dict[str, str], Path]:
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
    provider = runpy.run_path(str(ROOT / "libs/payload-invocation/command_evidence.py"))
    (scripts / "command-errors.pyz").write_bytes(provider["archive"](ROOT))
    return command, environment, home


@pytest.mark.parametrize("code", [0, 23])
@pytest.mark.parametrize("archive_available", [True, False])
def test_prepared_command_preserves_streams_arguments_and_result(
    tmp_path: Path, code: int, archive_available: bool,
) -> None:
    command, environment, home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    if not archive_available:
        (payload / "scripts/command-errors.pyz").unlink()
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
    warning = "" if archive_available else (
        "[agent-bridge] command evidence support unavailable; original command continues.\n"
    )
    assert result.stderr == warning + "native-stderr\n"
    log = home / ".copilot-extensions/logs/diagnostics/commands/command-errors.jsonl"
    if code == 0 or (not archive_available and os.name != "nt"):
        assert not log.exists()
    else:
        records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert all(record["exit_code"] == code for record in records)
        assert {record["code"] for record in records} == (
            ({"nonzero-exit"} if archive_available else set())
            | ({"native-command-failed"} if os.name == "nt" else set())
        )


@pytest.mark.parametrize("failure", ["parser", "exception", "reported"])
def test_receiving_module_errors_are_discoverable_without_opaque_context(
    tmp_path: Path, failure: str,
) -> None:
    command, environment, home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    program, expected_result, expected_code = {
        "parser": (
            "import argparse\nargparse.ArgumentParser().parse_args()\n", 2, "nonzero-exit",
        ),
        "exception": (
            "raise RuntimeError('private-exception-content')\n", 1, "uncaught-exception",
        ),
        "reported": (
            "from _command_evidence.process_telemetry.command_errors import current_recorder\n"
            "current_recorder().record('explicit-error', 'Producer-shaped error')\n",
            0, "explicit-error",
        ),
    }[failure]
    (payload / "agent_bridge.py").write_text(program, encoding="utf-8")
    result = _run(command, environment)
    assert result.returncode == expected_result, result.stderr
    assert result.stdout == ""
    if failure == "exception":
        assert "private-exception-content" in result.stderr
    elif failure == "parser":
        assert "private-argument-content" in result.stderr
    archive = payload / "scripts/command-errors.pyz"
    reader = _run([sys.executable, str(archive), "recent", "--limit", "3"], environment)
    assert reader.returncode == 0, reader.stderr
    discovery = json.loads(reader.stdout)
    assert discovery["unavailable"] == []
    records = [
        record for record in discovery["records"]
        if record["source"] == "agent-bridge-runtime"
    ]
    expected_codes = {expected_code}
    if failure == "parser" and sys.platform == "win32":
        expected_codes.add("parser-unrecognized-arguments")
    assert len(records) == len(expected_codes)
    assert {record["code"] for record in records} == expected_codes
    for record in records:
        assert record["command"] == "agent-bridge"
        assert record["exit_code"] == (2 if record["code"] == "nonzero-exit" else None)
    assert "private-" not in json.dumps(discovery)
    assert not (home / ".agent-bridge").exists()


def test_damaged_archive_never_replays_the_application(tmp_path: Path) -> None:
    command, environment, _home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    (payload / "scripts/command-errors.pyz").write_bytes(b"not a Python archive")
    (payload / "agent_bridge.py").write_text(
        "from pathlib import Path\nPath('application-ran').touch()\n", encoding="utf-8",
    )
    result = _run(command, environment)
    assert result.returncode != 0
    assert not (payload / "application-ran").exists()
    assert "original command continues" not in result.stderr


def test_receiving_storage_failure_preserves_the_application_result(tmp_path: Path) -> None:
    command, environment, home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    (home / ".copilot-extensions").write_text("blocked", encoding="utf-8")
    (payload / "agent_bridge.py").write_text(
        "import sys\nprint('original-error', file=sys.stderr)\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    result = _run(command, environment)
    assert result.returncode == 23
    assert result.stdout == ""
    assert "original-error" in result.stderr
    assert "command evidence unavailable" in result.stderr
    assert (home / ".copilot-extensions").read_text(encoding="utf-8") == "blocked"


def test_readiness_retains_its_isolated_probe_protocol(tmp_path: Path) -> None:
    command, environment, home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    (payload / "scripts/command-errors.pyz").write_bytes(b"must not execute")
    (payload / "scripts/versioned_runtime_probe.py").write_text(
        "import json, sys\n"
        "print(json.dumps([sys.flags.isolated, sys.flags.dont_write_bytecode, sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    command[-1] = "invocation-contract"
    result = _run(command, environment)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        1, 1, ["--plugin", "agent-bridge", "--module", "agent_bridge", "--", "invocation-contract"],
    ]
    assert result.stderr == ""
    assert not (home / ".copilot-extensions").exists()


def test_actual_receiving_os_command_line_retains_daemon_identity(tmp_path: Path) -> None:
    command, environment, _home = _prepared_runtime(tmp_path)
    command[-1] = "start"
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    program = "import json\n"
    if sys.platform == "win32":
        program += (
            "import ctypes\n"
            "get_command = ctypes.windll.kernel32.GetCommandLineW\n"
            "get_command.restype = ctypes.c_wchar_p\n"
            "print(json.dumps(get_command()))\n"
        )
    else:
        program += (
            "import os\nfrom pathlib import Path\n"
            "print(json.dumps([os.fsdecode(part) for part in "
            "Path('/proc/self/cmdline').read_bytes().rstrip(b'\\0').split(b'\\0')]))\n"
        )
    (payload / "agent_bridge.py").write_text(program, encoding="utf-8")
    result = _run(command, environment)
    assert result.returncode == 0, result.stderr
    identity = runpy.run_path(str(BRIDGE / "src/agent_bridge/service_process_identity.py"))
    observed = json.loads(result.stdout)
    if sys.platform == "win32":
        observed = identity["split_windows_command"](observed)
    assert str(payload / "scripts/command-errors.pyz") in observed
    assert identity["bridge_daemon_argv"](observed)


def test_receiving_archive_preserves_exec_pid_and_terminal(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("POSIX exec and native PTY contract")
    import pty

    command, environment, _home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    (payload / "agent_bridge.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps([os.getpid(), [stream.isatty() for stream in "
        "(sys.stdin, sys.stdout, sys.stderr)]]), flush=True)\n",
        encoding="utf-8",
    )
    master, slave = pty.openpty()
    process = subprocess.Popen(
        command, env=environment, cwd=payload, stdin=slave, stdout=slave, stderr=slave,
    )
    os.close(slave)
    try:
        assert process.wait(timeout=15) == 0
        assert json.loads(os.read(master, 4096)) == [process.pid, [True, True, True]]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)


def test_receiving_archive_preserves_termination_signal(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("POSIX exec signal contract")
    import select
    import signal

    command, environment, _home = _prepared_runtime(tmp_path)
    payload = Path(environment["AGENT_BRIDGE_PAYLOAD_ROOT"])
    (payload / "agent_bridge.py").write_text(
        "import signal\nprint('ready', flush=True)\nsignal.pause()\n", encoding="utf-8",
    )
    with subprocess.Popen(
        command, env=environment, cwd=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        try:
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready and process.stdout.readline() == b"ready\n"
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == -signal.SIGTERM
            assert stdout == stderr == b""
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


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
