"""Foreground admission owns its child without an execution deadline or daemon."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

import agent_procutil
from agent_procutil import owned_process
from agent_worktrees import __main__ as core
from agent_worktrees import config as cfg
from agent_worktrees import manager_launch_cli as launch
from agent_worktrees import picker_fast_entry, picker_session_entry, registry_paths, terminal_launch
from agent_worktrees.control_plane_providers import _ControlPlaneProviderManifest


@pytest.fixture
def selected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _ControlPlaneProviderManifest:
    manifest = _ControlPlaneProviderManifest(
        provider="worktree-manager", command=("selected-manager",),
        minimum_version=(0, 1, 0, 21), minimum_version_text="0.1.0-dev21",
        source_path=str(tmp_path / "provider.json"),
    )
    monkeypatch.setattr(launch, "_control_plane_provider_selection", lambda: (manifest, ""))
    return manifest


def test_bare_entry_explicitly_selects_owned_deadline_free_probe(
    selected: _ControlPlaneProviderManifest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def query(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert options["timeout"] is None
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 4096
        environment = options["env"]
        assert isinstance(environment, dict)
        assert environment["PYTHONUTF8"] == "1"
        assert "PYTHONHOME" not in environment
        assert "UV_INTERNAL__PYTHONHOME" not in environment
        return subprocess.CompletedProcess(command, 0, "worktree-manager 1.2.3\n", "")

    monkeypatch.setenv("PYTHONHOME", "must-not-inherit")
    monkeypatch.setenv("UV_INTERNAL__PYTHONHOME", "must-not-inherit")
    monkeypatch.setattr(owned_process, "run_caller_owned", query)
    monkeypatch.setattr(core, "_is_noninteractive_invocation", lambda: False)
    monkeypatch.setattr(core, "_is_headless_project", lambda: False)
    monkeypatch.setattr(core, "_exec_worktree_manager", lambda *_args: 17)
    monkeypatch.setattr(cfg, "active_project", lambda: "sample")
    assert launch.dispatch_bare_invocation(True) == 17
    assert calls == [[*selected.command, "--version"]]


@pytest.mark.parametrize("reason", ["runtime", "os"])
@pytest.mark.parametrize("fallback", ["bundled", "install-trigger"])
def test_unsupported_public_preflight_uses_normal_fallback_without_launch(
    selected: _ControlPlaneProviderManifest, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], reason: str, fallback: str,
) -> None:
    calls: list[str] = []

    def unsupported() -> NoReturn:
        calls.append("support")
        message = "pidfd support unavailable https://example.invalid/path?token=fixture-secret"
        if reason == "os":
            raise OSError(38, message)
        raise RuntimeError(message)

    monkeypatch.setattr(owned_process, "require_caller_owned_support", unsupported)
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *_args, **_options: pytest.fail("unsupported preflight reached native launch"),
    )
    monkeypatch.setattr(core, "_is_noninteractive_invocation", lambda: False)
    monkeypatch.setattr(core, "_is_headless_project", lambda: False)
    monkeypatch.setattr(core, "_bundled_picker_available", lambda: fallback == "bundled")
    monkeypatch.setattr(cfg, "active_project", lambda: "sample")
    monkeypatch.setattr(
        core, "_exec_worktree_manager",
        lambda *_args: pytest.fail("unsupported ownership must not admit Manager"),
    )

    def bundled(_arguments: list[str]) -> int:
        calls.append("bundled")
        return 17

    def install(_project: str | None) -> int:
        calls.append("install-trigger")
        return 0

    monkeypatch.setattr(core, "cmd_launch", bundled)
    monkeypatch.setattr(core, "cmd_manager_install_trigger", install)
    assert launch.dispatch_bare_invocation(True) == (17 if fallback == "bundled" else 1)
    assert calls == ["support", fallback]
    captured = capsys.readouterr()
    assert "caller-owned process support" in captured.err
    assert "pidfd support unavailable" in captured.err
    assert "fixture-secret" not in captured.out + captured.err
    assert selected.command[0] in captured.err


@pytest.mark.parametrize("reason", ["runtime", "os"])
@pytest.mark.parametrize("timeout", [None, 10.0])
def test_terminal_support_refusal_precedes_capture(
    monkeypatch: pytest.MonkeyPatch, reason: str, timeout: float | None,
) -> None:
    calls: list[str] = []

    def unsupported() -> NoReturn:
        calls.append("support")
        if reason == "os":
            raise OSError(38, "native observation is unavailable")
        raise RuntimeError("native observation is unavailable")

    monkeypatch.setattr(terminal_launch, "_channel", "selected-offer")
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(terminal_launch, "validate_or_exit", lambda: None)
    monkeypatch.setattr(owned_process, "require_caller_owned_support", unsupported)
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *_args, **_options: pytest.fail("unsupported terminal capture reached native launch"),
    )
    environment = {"FIXTURE_KEPT": "original"}
    with pytest.raises(RuntimeError, match="process support; no launch was admitted"):
        terminal_launch.select_receiver(["selected-manager"], environment, timeout=timeout)
    assert calls == ["support"]
    assert environment == {"FIXTURE_KEPT": "original"}


@pytest.mark.parametrize("consumer", ["health", "terminal"])
@pytest.mark.parametrize("failure", ["preflight-cleanup", "preflight-cancel", "capture-runtime"])
def test_preflight_cannot_swallow_custody_cancellation_or_later_runtime_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    consumer: str, failure: str,
) -> None:
    problem: BaseException
    if failure == "preflight-cleanup":
        problem = owned_process.OwnedCleanupError(None)
    elif failure == "preflight-cancel":
        problem = KeyboardInterrupt()
    else:
        problem = RuntimeError("caller-owned completion is unconfirmed")
    calls: list[str] = []

    def support() -> None:
        calls.append("support")
        if failure.startswith("preflight"):
            raise problem

    def capture(_command: list[str], **options: object) -> NoReturn:
        calls.append("capture")
        assert options["timeout"] is None
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 4096
        raise problem

    monkeypatch.setattr(owned_process, "require_caller_owned_support", support)
    monkeypatch.setattr(owned_process, "run_caller_owned", capture)
    monkeypatch.setattr(terminal_launch, "_channel", "selected-offer")
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(terminal_launch, "validate_or_exit", lambda: None)
    with pytest.raises(type(problem)) as caught:
        if consumer == "health":
            launch._probe_worktree_manager_version(["manager", "--version"], timeout=None)
        else:
            terminal_launch.select_receiver(["manager"], {}, timeout=None)
    assert caught.value is problem
    assert calls == (["support"] if failure.startswith("preflight") else ["support", "capture"])
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.skipif(os.name != "nt", reason="Native registered Windows batch provider")
@pytest.mark.parametrize("suffix", [".cmd", ".BAT"])
@pytest.mark.parametrize("prefix", [
    "fixed-prefix", "prefix with spaces", r"C:\Program Files\selected\entry.txt", "single'quote-value",
])
def test_registered_batch_provider_admits_health_and_pins_terminal_receiver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, prefix: str,
) -> None:
    directory = tmp_path / "space & tools \u65e5"
    directory.mkdir()
    provider = directory / ("worktree-manager" + suffix)
    pinned = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "worktree_manager"]
    response = {
        "package": "worktree_manager", "capability": terminal_launch.CAPABILITY,
        "runtime": {"command": pinned, "root": str(tmp_path)},
    }
    provider.write_text(
        f'@echo off\nif not "%~1"=="{prefix}" exit /b 31\n'
        'if not "%~2"=="--version" exit /b 32\n'
        "if defined CE_TERMINAL_CAPABILITY_QUERY goto capability\n"
        "echo worktree-manager 1.2.3\nexit /b 0\n:capability\n"
        f"echo {json.dumps(response)}\nexit /b 0\n",
        encoding="utf-8",
    )
    registry = tmp_path / "providers"
    registry.mkdir()
    command = [str(provider), prefix]
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager", "command": command,
        "minimum_version": "0.1.0-dev21",
    }), encoding="utf-8")
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDERS_DIR_ENV, str(registry))
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDER_ENV, "worktree-manager")
    monkeypatch.delenv(terminal_launch.QUERY_ENVIRONMENT, raising=False)
    for foreground in (False, True):
        health = launch._worktree_manager_health(foreground=foreground)
        assert health == launch._ManagerHealth("ready", tuple(command))
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}))
    monkeypatch.setattr(terminal_launch, "_channel", str(request))
    selected, environment = terminal_launch.select_receiver(
        [*command, "--resident-picker"], dict(os.environ), timeout=None,
    )
    assert selected == [*pinned, "--resident-picker"]
    assert environment[terminal_launch.ENVIRONMENT] == str(request)
    assert environment["WORKTREE_MANAGER_ROOT"] == str(tmp_path)
    assert "COPILOT_VENUE_BATCH_EXECUTABLE" not in environment


@pytest.mark.skipif(os.name != "nt", reason="Native registered Windows batch provider")
@pytest.mark.parametrize("prefix", [
    "flag&ver", "flag|ver", "flag<input", "flag>output", "flag^value", "(flag)",
    "%COMSPEC%", "!COMSPEC!", 'flag"value', "flag\rvalue", "flag\nvalue",
    "flag\0value", "flag\tvalue", "flag\x7fvalue", "C:\\path space\\",
    "flag;value", "flag,value", "flag=value",
])
def test_unsafe_registered_batch_prefix_is_rejected_before_health_or_terminal_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    prefix: str,
) -> None:
    provider = tmp_path / "selected.cmd"
    started = tmp_path / "started"
    provider.write_text(
        '@echo off\necho started>"%~dp0started"\necho worktree-manager 1.2.3\n',
        encoding="utf-8",
    )
    registry = tmp_path / "providers"
    registry.mkdir()
    command = [str(provider), prefix]
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager", "command": command,
        "minimum_version": "0.1.0-dev21",
    }), encoding="utf-8")
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDERS_DIR_ENV, str(registry))
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDER_ENV, "worktree-manager")
    def unexpected_launch(*_args: object, **_options: object) -> NoReturn:
        pytest.fail("unsafe batch prefix reached a subprocess launch boundary")

    monkeypatch.setattr(owned_process, "run_caller_owned", unexpected_launch)
    monkeypatch.setattr(launch.subprocess, "run", unexpected_launch)
    for foreground in (False, True):
        health = launch._worktree_manager_health(foreground=foreground)
        assert health.state == "failed" and health.command is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot be passed literally" in captured.err
    assert "No launch was admitted" in captured.err
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}))
    monkeypatch.setattr(terminal_launch, "_channel", str(request))
    environment = {"FIXTURE_KEPT": "original"}
    with pytest.raises(terminal_launch._UnsafeBatchArguments, match="cannot be passed literally"):
        terminal_launch.select_receiver(
            [*command, "--resident-picker"], environment, timeout=None,
        )
    assert environment == {"FIXTURE_KEPT": "original"}
    assert not started.exists()


def test_nonbatch_probe_prefix_remains_literal_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = 'flag&value|%PATH%!name^<>"quoted" C:\\path space\\'
    command = [sys.executable, prefix]
    calls: list[list[str]] = []

    def query(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        assert options["timeout"] is None
        return subprocess.CompletedProcess(arguments, 0, "worktree-manager 1.2.3", "")

    monkeypatch.setattr(owned_process, "run_caller_owned", query)
    version, process = launch._probe_worktree_manager_version([*command, "--version"], timeout=None)
    assert version is not None and process is not None
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}))
    monkeypatch.setattr(terminal_launch, "_channel", str(request))
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    assert terminal_launch.select_receiver(command, {}, timeout=None) == (command, {})
    assert calls == [[*command, "--version"], [*command, "--version"]]


def _invalid_utf8_provider_script(tmp_path: Path, stream: str) -> tuple[str, Path]:
    attempts = tmp_path / "attempts"
    descriptor = {"stdout": 1, "stderr": 2}[stream]
    script = (
        "import os\nfrom pathlib import Path\n"
        f"with Path({str(attempts)!r}).open('ab') as record:\n"
        " record.write(b'probe\\n')\n"
        "os.write(1, b'worktree-manager 1.2.3\\n')\n"
        f"os.write({descriptor}, b'fixture decoding diagnostic "
        "https://example.invalid/path?token=fixture-secret \\xff')\n"
    )
    return script, attempts


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("fallback", ["bundled", "install-trigger"])
def test_real_invalid_utf8_probe_rejects_version_and_uses_normal_fallback_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    stream: str, fallback: str,
) -> None:
    script, attempts = _invalid_utf8_provider_script(tmp_path, stream)
    command = [sys.executable, "-I", "-S", "-B", "-c", script]
    registry = tmp_path / "providers"
    registry.mkdir()
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager", "command": command,
        "minimum_version": "0.1.0-dev21",
    }), encoding="utf-8")
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDERS_DIR_ENV, str(registry))
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDER_ENV, "worktree-manager")
    monkeypatch.setattr(core, "_is_noninteractive_invocation", lambda: False)
    monkeypatch.setattr(core, "_is_headless_project", lambda: False)
    monkeypatch.setattr(core, "_bundled_picker_available", lambda: fallback == "bundled")
    monkeypatch.setattr(cfg, "active_project", lambda: "sample")
    monkeypatch.setattr(
        core, "_exec_worktree_manager",
        lambda *_args: pytest.fail("invalid output must not admit a Manager version"),
    )
    fallbacks: list[str] = []

    def bundled(_arguments: list[str]) -> int:
        fallbacks.append("bundled")
        return 17

    def install(_project: str | None) -> int:
        fallbacks.append("install-trigger")
        return 0

    monkeypatch.setattr(core, "cmd_launch", bundled)
    monkeypatch.setattr(core, "cmd_manager_install_trigger", install)
    assert launch.dispatch_bare_invocation(True) == (17 if fallback == "bundled" else 1)
    assert fallbacks == [fallback]
    assert attempts.read_bytes() == b"probe\n"
    captured = capsys.readouterr()
    assert "output could not be decoded" in captured.err
    assert "fixture-secret" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    if os.name == "nt":
        assert "fixture decoding diagnostic" in captured.err


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_invalid_utf8_terminal_query_never_admits_or_replays_the_receiver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str,
) -> None:
    script, attempts = _invalid_utf8_provider_script(tmp_path, stream)
    command = [sys.executable, "-I", "-S", "-B", "-c", script, "--resident-picker"]
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}))
    monkeypatch.setattr(terminal_launch, "_channel", str(request))
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    environment = {**os.environ, "FIXTURE_KEPT": "original"}
    original = dict(environment)
    with pytest.raises(RuntimeError, match="non-UTF-8 output; no launch was admitted"):
        terminal_launch.select_receiver(command, environment, timeout=None)
    assert attempts.read_bytes() == b"probe\n"
    assert environment == original
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_foreground_real_capture_overflow_is_failure_not_a_version(
    stream: str, capsys: pytest.CaptureFixture[str],
) -> None:
    script = (
        "import sys; print('worktree-manager 1.2.3'); "
        f"sys.{stream}.write('fixture-private-' * 1000)"
    )
    version, process = launch._probe_worktree_manager_version(
        [sys.executable, "-I", "-S", "-B", "-c", script], timeout=None,
    )
    assert version is None and process is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "limit" in captured.err
    assert "fixture-private" not in captured.err


@pytest.mark.parametrize(
    ("exit_code", "text", "expected"),
    [(7, "worktree-manager 1.2.3", None), (0, "malformed", None),
     (0, "worktree-manager 0.1.0-dev20", (0, 1, 0, 20)),
     (0, "worktree-manager 1.2.3", (1, 2, 3, 1_000_000))],
)
def test_foreground_capture_preserves_real_exit_and_version(
    exit_code: int, text: str, expected: tuple[int, int, int, int] | None,
) -> None:
    script = f"import sys; print({text!r}); sys.exit({exit_code})"
    version, process = launch._probe_worktree_manager_version(
        [sys.executable, "-I", "-S", "-B", "-c", script], timeout=None,
    )
    assert version == expected
    assert process is not None
    assert process.returncode == exit_code
    assert process.stdout.strip() == text


def test_foreground_cancellation_propagates_without_replay_or_fallback(
    selected: _ControlPlaneProviderManifest, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def cancel(command: list[str], **options: object) -> NoReturn:
        assert options["timeout"] is None
        calls.append(command)
        raise KeyboardInterrupt

    monkeypatch.setattr(owned_process, "run_caller_owned", cancel)
    with pytest.raises(KeyboardInterrupt):
        launch._worktree_manager_health(foreground=True)
    assert calls == [[*selected.command, "--version"]]
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_foreground_external_timeout_keeps_redacted_partial_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted(command: list[str], **options: object) -> NoReturn:
        assert options["timeout"] is None
        raise subprocess.TimeoutExpired(
            command, 1,
            stderr=b"\x1b[31mresolver https://example.invalid/path?token=fixture-secret\x1b[0m",
        )

    monkeypatch.setattr(owned_process, "run_caller_owned", interrupted)
    assert launch._probe_worktree_manager_version(["manager", "--version"], timeout=None) == (None, None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "resolver https://example.invalid/path" in captured.err
    assert "fixture-secret" not in captured.err
    assert "\x1b" not in captured.err


def test_foreground_unconfirmed_cleanup_preserves_owner_custody(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    failure = owned_process.OwnedCleanupError(None)
    failure.operation_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    def unconfirmed(command: list[str], **options: object) -> NoReturn:
        raise failure

    monkeypatch.setattr(owned_process, "run_caller_owned", unconfirmed)
    with pytest.raises(owned_process.OwnedCleanupError) as caught:
        launch._probe_worktree_manager_version(["manager", "--version"], timeout=None)
    assert caught.value is failure
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_nonforeground_health_retains_finite_retry(
    selected: _ControlPlaneProviderManifest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def finite(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert options["timeout"] == 20.0
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 20)
        return subprocess.CompletedProcess(command, 0, "worktree-manager 1.2.3", "")

    monkeypatch.setattr(launch.subprocess, "run", finite)
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *_args, **_kwargs: pytest.fail("diagnostic recovery opted into foreground custody"),
    )
    monkeypatch.setattr(
        owned_process, "require_caller_owned_support",
        lambda: pytest.fail("finite diagnostic recovery unexpectedly required foreground support"),
    )
    assert launch._usable_worktree_manager() == selected.command
    assert calls == [[*selected.command, "--version"]] * 2


def test_mux_launcher_resolution_retains_finite_recovery_and_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    slot = tmp_path / "versions" / "current"
    (slot / "bin").mkdir(parents=True)
    (tmp_path / "current-version").write_text("current", encoding="utf-8")
    monkeypatch.setattr(core, "_worktree_manager_root", lambda: tmp_path)
    calls: list[list[str]] = []

    def finite(command: list[str], **options: object) -> NoReturn:
        assert options["timeout"] == 20.0
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 20)

    monkeypatch.setattr(launch.subprocess, "run", finite)
    monkeypatch.setattr(
        owned_process, "run_caller_owned",
        lambda *_args, **_kwargs: pytest.fail("mux resolution used foreground execution"),
    )
    assert launch._usable_worktree_manager_launcher_dir() is None
    assert len(calls) == 2
    assert all(command[:5] == ["uv", "run", "--quiet", "--project", str(slot)] for command in calls)


@pytest.mark.parametrize("error", ["overflow", "cancel", "cleanup"])
def test_terminal_identity_query_keeps_custody_and_stream_limits(
    error: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(terminal_launch, "_channel", str(tmp_path / "offer"))
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(terminal_launch, "validate_or_exit", lambda: None)

    def rejected(command: list[str], **options: object) -> NoReturn:
        assert options["timeout"] is None
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 4096
        if error == "cancel":
            raise KeyboardInterrupt
        if error == "cleanup":
            failure = owned_process.OwnedCleanupError(None)
            failure.operation_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            raise failure
        raise owned_process.OutputLimitExceeded("stdout", 4096)

    monkeypatch.setattr(owned_process, "run_caller_owned", rejected)
    expected_error = (
        KeyboardInterrupt if error == "cancel"
        else owned_process.OwnedCleanupError if error == "cleanup"
        else owned_process.OutputLimitExceeded
    )
    with pytest.raises(expected_error):
        terminal_launch.select_receiver(["manager", "--resident-picker"], {}, timeout=None)


def test_nonforeground_terminal_query_keeps_its_finite_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_launch, "_channel", "selected-offer")
    monkeypatch.setattr(terminal_launch, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(terminal_launch, "validate_or_exit", lambda: None)

    def query(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert options["timeout"] == 10
        assert options["max_stdout_bytes"] == options["max_stderr_bytes"] == 4096
        return subprocess.CompletedProcess(command, 0, "worktree-manager 1.2.3", "")

    monkeypatch.setattr(owned_process, "run_caller_owned", query)
    assert terminal_launch.select_receiver(["manager"], {}) == (["manager"], {})


@pytest.mark.parametrize("subcommand", [None, ["update"], ["picker", "mock"]])
def test_manager_handoff_only_removes_the_capability_deadline_for_bare_entry(
    subcommand: list[str] | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(launch, "_agent_worktrees_launch_command", lambda _project: ["engine"])
    calls: list[float | None] = []

    def select(
        command: list[str], environment: dict[str, str], *, timeout: float | None = 10,
    ) -> tuple[list[str], dict[str, str]]:
        calls.append(timeout)
        return command, environment

    monkeypatch.setattr(terminal_launch, "select_receiver", select)
    monkeypatch.setattr(
        agent_procutil, "spawn_sync_in_kill_on_close_job",
        lambda *_args, **_kwargs: (
            SimpleNamespace(wait=lambda: 17), SimpleNamespace(close=lambda: None),
        ),
    )
    with pytest.raises(SystemExit) as stopped:
        launch._exec_worktree_manager(("manager",), None, subcommand=subcommand)
    assert stopped.value.code == 17
    expected: float | None = None if subcommand is None else 10
    assert calls == [expected]


@pytest.mark.skipif(os.name != "nt", reason="Windows foreground Job custody")
def test_fast_entry_does_not_acquire_a_manager_service_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "manager_command": ["selected-manager", "--resident-picker"],
        "bootstrap": {"project": "sample"}, "runtime_root": str(tmp_path),
        "manager_session": {"version": 1, "root": str(tmp_path)},
    }
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(registry_paths, "registry_root", lambda: tmp_path)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(picker_fast_entry, "_request", lambda *_args: result)
    monkeypatch.setattr(terminal_launch, "_channel", None)
    monkeypatch.setattr(
        picker_session_entry, "open_session",
        lambda *_args: pytest.fail("pre-UI launch contacted the Manager service"),
    )
    events: list[str] = []

    def spawn(command: list[str], *, env: dict[str, str]) -> tuple[SimpleNamespace, SimpleNamespace]:
        assert command == ["selected-manager", "--resident-picker", "--project", "sample"]
        assert json.loads(env[picker_fast_entry.BOOTSTRAP_ENV]) == result
        assert json.loads(env["WORKTREE_MANAGER_ENGINE_ARGV"]) == [
            sys.executable, "-B", "-m", "agent_worktrees",
        ]
        return (
            SimpleNamespace(wait=lambda: 17, poll=lambda: 17),
            SimpleNamespace(close=lambda: events.append("closed")),
        )

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    assert picker_fast_entry.dispatch(["--project", "sample"]) == 17
    assert events == ["closed"]
