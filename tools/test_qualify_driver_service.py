"""The native Driver packet is explicit, isolated and narrowly selected."""

from contextlib import nullcontext
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

import pytest

import qualify_driver_service as qualification


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
