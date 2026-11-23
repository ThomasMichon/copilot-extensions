"""The native Driver packet is explicit, isolated and narrowly selected."""

from contextlib import nullcontext
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any
import xml.etree.ElementTree as ET

from agent_procutil.owned_process import run_caller_owned
import pytest

import qualify_driver_service as qualification
from windows_qualification_contract import DRIVER_SERVICE_CASES


@pytest.mark.portfolio_tier("T2")
def test_driver_packet_preserves_both_cross_project_junit_identities(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    files = (
        root / "plugins/agent-remote-driver/tests/native/test_session_host_application.py",
        root / "libs/agent-procutil/tests/test_durable_wmi_job_survival.py",
    )
    for path, (_module, case) in zip(files, DRIVER_SERVICE_CASES):
        path.parent.mkdir(parents=True)
        path.write_text(f"def {case}():\n    assert True\n", encoding="utf-8")
    (root / "libs/agent-procutil/pyproject.toml").write_text(
        "[tool.pytest.ini_options]\nminversion = '8.0'\n", encoding="utf-8",
    )
    report = sandbox / "report.xml"
    command = qualification.pytest_command(root, Path(sys.executable), sandbox)
    # This reporting-only subprocess is bounded by its 30-second owner.
    command.remove("--timeout=300")
    command.append("--junitxml=" + str(report))
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    reply = run_caller_owned(
        command, cwd=root, env=environment, timeout=30,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert reply.returncode == 0, reply.stdout + reply.stderr
    identities = sorted(
        (case.get("classname", "").split(".")[-1], case.get("name"))
        for case in ET.parse(report).iter("testcase")
    )
    assert identities == sorted(DRIVER_SERVICE_CASES)


@pytest.mark.parametrize("reason", ["arguments", "platform", "executable", "owner"])
def test_native_driver_packet_refuses_before_acquiring_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:
    executable = tmp_path / "copilot.exe"
    executable.touch()
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(
        argv=["qualification", "unexpected"] if reason == "arguments" else ["qualification"],
    ))
    monkeypatch.setattr(qualification, "os", SimpleNamespace(
        name="posix" if reason == "platform" else "nt",
        environ={"COPILOT_NATIVE_TEST_EXECUTABLE": str(executable)} if reason == "owner" else {},
    ))
    monkeypatch.setattr(qualification, "acquire", lambda *_args: pytest.fail("unadmitted resource"))
    with pytest.raises(ValueError):
        qualification.main()


def test_native_driver_packet_holds_original_cache_and_selects_only_its_lifetime_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "cache"
    python = cache / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    executable = tmp_path / "copilot.exe"
    executable.touch()
    sandbox = tmp_path / "sandbox"
    events: list[tuple[Any, ...]] = []
    environment = {"COPILOT_NATIVE_TEST_EXECUTABLE": str(executable), "RUNNER_TEMP": str(tmp_path)}
    monkeypatch.setattr(qualification, "sys", SimpleNamespace(argv=["qualification"]))
    monkeypatch.setattr(qualification, "os", SimpleNamespace(
        name="nt", environ=environment,
    ))
    monkeypatch.setattr(qualification, "ordinary_cache_root", lambda *_args: cache)
    monkeypatch.setattr(qualification, "venv_root", lambda *_args: tmp_path / "venvs")
    def acquire(timeout: float, root: Path) -> SimpleNamespace:
        events.append(("acquire", timeout, root))
        return SimpleNamespace(release=lambda: events.append(("release",)))
    monkeypatch.setattr(qualification, "acquire", acquire)
    monkeypatch.setattr(qualification, "TestSandbox", lambda **_kwargs: nullcontext(sandbox))
    monkeypatch.setattr(qualification, "record_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualification, "isolated_environment", lambda *_args, **_kwargs: {
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1", "fixture-sentinel": "retained",
    })

    def run(command: list[str], **options: Any) -> int:
        assert events == [("acquire", 0, cache)]
        assert command[0] == str(python)
        assert "--timeout=300" in command
        assert command[-2:] == [
            "-k", "installed_service_survives_all_installer_caller_jobs or "
            "tracked_cleanup_scope_refuses_a_child_outside_the_immediate_caller_job",
        ]
        assert options["native_application"] is True
        environment = options["env"]
        assert environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert environment["COPILOT_EXTENSIONS_NATIVE_DRIVER_SERVICE"] == "1"
        assert environment["COPILOT_NATIVE_TEST_EXECUTABLE"] == str(executable)
        assert environment["fixture-sentinel"] == "retained"
        assert options["sandbox"] == sandbox
        events.append(("native",))
        return 0

    monkeypatch.setattr(qualification, "run_contained", run)
    assert qualification.main() == 0
    assert events == [("acquire", 0, cache), ("native",), ("release",)]


@pytest.mark.parametrize("outcome", ["passed", "failed", "exception"])
def test_hosted_driver_packet_retains_junit_and_releases_cache_on_every_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    cache = tmp_path / "cache"
    python = cache / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    executable = tmp_path / "copilot.exe"
    executable.touch()
    sandbox = tmp_path / "sandbox"
    events: list[str] = []
    monkeypatch.setattr(qualification, "os", SimpleNamespace(name="nt", environ={
        "COPILOT_NATIVE_TEST_EXECUTABLE": str(executable), "RUNNER_TEMP": str(tmp_path),
    }))
    monkeypatch.setattr(qualification, "ordinary_cache_root", lambda *_args: cache)
    monkeypatch.setattr(qualification, "venv_root", lambda *_args: tmp_path / "venvs")
    monkeypatch.setattr(qualification, "acquire", lambda *_args: SimpleNamespace(
        release=lambda: events.append("released"),
    ))

    def owner(**options: Any) -> nullcontext[Path]:
        assert options["dir"] == tmp_path
        return nullcontext(sandbox)

    monkeypatch.setattr(qualification, "TestSandbox", owner)
    monkeypatch.setattr(qualification, "record_owner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qualification, "isolated_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(time, "monotonic", lambda: 100)
    output = tmp_path / "evidence"

    def run(command: list[str], **options: Any) -> int:
        assert command[-1] == "--junitxml=" + str(output / "junit/driver-service.xml")
        assert options["limits"].wall_seconds == 170
        print("retained native diagnostic")
        if outcome == "exception":
            raise RuntimeError("native owner refused")
        return 1 if outcome == "failed" else 0

    monkeypatch.setattr(qualification, "run_contained", run)
    if outcome == "passed":
        assert qualification.qualify(tmp_path, output=output, deadline=270) == 0
    else:
        with pytest.raises((ValueError, RuntimeError)):
            qualification.qualify(tmp_path, output=output, deadline=270)
    assert events == ["released"]
    assert "retained native diagnostic" in (output / "phase-driver-service.stdout.log").read_text()


def test_driver_packet_expired_preparation_never_acquires_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "copilot.exe"
    executable.touch()
    monkeypatch.setattr(qualification, "os", SimpleNamespace(name="nt", environ={
        "COPILOT_NATIVE_TEST_EXECUTABLE": str(executable), "RUNNER_TEMP": str(tmp_path),
    }))
    monkeypatch.setattr(time, "monotonic", lambda: 100)
    monkeypatch.setattr(qualification, "acquire", lambda *_args: pytest.fail("expired resource"))
    with pytest.raises(ValueError, match="aggregate deadline"):
        qualification.qualify(tmp_path, deadline=100)
