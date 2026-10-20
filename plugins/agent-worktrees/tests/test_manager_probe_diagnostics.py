"""A rejected Manager probe reports its cause without claiming launch success."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import manager_launch_cli as launch
from agent_worktrees.control_plane_providers import _ControlPlaneProviderManifest

Provider = Callable[[str], _ControlPlaneProviderManifest]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Provider:
    registry = tmp_path / "providers"
    registry.mkdir()
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDERS_DIR_ENV, str(registry))
    monkeypatch.setenv(launch._CONTROL_PLANE_PROVIDER_ENV, "worktree-manager")

    def select(script: str) -> _ControlPlaneProviderManifest:
        manifest = _ControlPlaneProviderManifest(
            provider="worktree-manager",
            command=(sys.executable, "-I", "-S", "-B", "-c", script),
            minimum_version=(0, 1, 0, 21),
            minimum_version_text="0.1.0-dev21",
            source_path=str(registry / "worktree-manager.json"),
        )
        Path(manifest.source_path).write_text(json.dumps({
            "schema_version": 1, "provider": manifest.provider,
            "command": list(manifest.command),
            "minimum_version": manifest.minimum_version_text,
        }), encoding="utf-8")
        return manifest
    return select


def _child_failure(message: str, *, stdout: bool = False) -> str:
    stream = "stdout" if stdout else "stderr"
    return f"import sys; print({message!r}, file=sys.{stream}); sys.exit(1)"


@pytest.mark.parametrize("cause", [
    "No solution found: dbus-next==0.2.3 was filtered by exclude-newer (7 days)",
    "Failed to spawn Python: traversal blocked (os error 448)",
])
@pytest.mark.parametrize("has_project", [False, True])
def test_real_probe_failure_reaches_bare_front_door_as_failure(
    provider: Provider, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], cause: str, has_project: bool,
) -> None:
    provider(_child_failure(cause))
    monkeypatch.setattr(core, "_is_noninteractive_invocation", lambda: False)
    monkeypatch.setattr(core, "_is_headless_project", lambda: False)
    monkeypatch.setattr(core, "_bundled_picker_available", lambda: False)
    monkeypatch.setattr(core.cfg, "active_project", lambda: "demo")
    monkeypatch.setattr(
        core, "_exec_worktree_manager", lambda *_a, **_kw: pytest.fail("failed provider launched"),
    )
    monkeypatch.setattr(core, "cmd_launch", lambda *_a: pytest.fail("nonexistent picker launched"))
    result = launch.dispatch_bare_invocation(has_project)
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert cause in captured.err
    assert "exit 1" in captured.err
    assert "usually a stale" not in captured.err
    assert "reinstall or remove" not in captured.err
    assert "Falling back to the bundled picker" not in captured.err


@pytest.mark.parametrize("padding", [0, 5000])
def test_probe_diagnostic_is_redacted_before_bounding(
    provider: Provider, capsys: pytest.CaptureFixture[str], padding: int,
) -> None:
    secret = "fixture-secret-" * 300
    message = (
        "No solution found at https://fixture-user:" + secret + "@packages.example/simple\n"
        + "x" * padding
        + "\nexclude-newer rejected the dependency"
        + "\nIndex request https://packages.example/simple?token=fixture-query-secret"
    )
    provider(_child_failure(message))
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "fixture-secret-" not in captured.err
    assert "fixture-query-secret" not in captured.err
    assert "exclude-newer rejected the dependency" in captured.err
    assert len(captured.err) < 2600
    assert ("truncated" in captured.err) is bool(padding)


def test_probe_uses_stdout_when_stderr_is_empty(
    provider: Provider, capsys: pytest.CaptureFixture[str],
) -> None:
    provider(_child_failure(
        "\x1b[31minterpreter startup failed before Manager\x1b[0m", stdout=True,
    ))
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert "interpreter startup failed before Manager" in captured.err
    assert "\x1b" not in captured.err
    assert captured.out == ""


def test_malformed_success_output_remains_failure_and_retains_evidence(
    provider: Provider, capsys: pytest.CaptureFixture[str],
) -> None:
    provider("print('Manager version metadata is unavailable')")
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert "Manager version metadata is unavailable" in captured.err
    assert "supported version" in captured.err
    assert captured.out == ""


def test_spawn_error_keeps_literal_cause_without_a_retry(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    provider("pass")
    calls = []

    def failed(argv: list[str], **kwargs: object) -> NoReturn:
        calls.append(argv)
        raise OSError("fixture traversal blocked (WinError 448)")

    monkeypatch.setattr(launch.subprocess, "run", failed)
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert len(calls) == 1
    assert "WinError 448" in captured.err
    assert captured.out == ""


def test_exhausted_timeouts_preserve_partial_stderr_and_attempt_count(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    provider("pass")
    calls = []

    def failed(argv: list[str], **kwargs: object) -> NoReturn:
        calls.append(argv)
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        raise subprocess.TimeoutExpired(argv, timeout, stderr=b"fixture resolver waiting")

    monkeypatch.setattr(launch.subprocess, "run", failed)
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert len(calls) == 2
    assert "timed out" in captured.err
    assert "fixture resolver waiting" in captured.err
    assert captured.out == ""


def test_transient_probe_timeout_does_not_report_a_failure(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = provider("pass")
    calls = []

    def eventually(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) == 1:
            timeout = kwargs["timeout"]
            assert isinstance(timeout, (int, float))
            raise subprocess.TimeoutExpired(argv, timeout, stderr="transient wait")
        return subprocess.CompletedProcess(argv, 0, "worktree-manager 1.2.3\n", "")

    monkeypatch.setattr(launch.subprocess, "run", eventually)
    assert launch._usable_worktree_manager() == manifest.command
    captured = capsys.readouterr()
    assert "transient wait" not in captured.err + captured.out


def test_real_bundled_fallback_keeps_its_exit_status(
    provider: Provider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider(_child_failure("fixture startup failure"))
    monkeypatch.setattr(core, "_is_noninteractive_invocation", lambda: False)
    monkeypatch.setattr(core, "_is_headless_project", lambda: False)
    monkeypatch.setattr(core, "_bundled_picker_available", lambda: True)
    monkeypatch.setattr(core.cfg, "active_project", lambda: "demo")
    monkeypatch.setattr(core, "cmd_launch", lambda _args: 7)
    assert launch.dispatch_bare_invocation(True) == 7


def test_healthy_provider_has_no_failure_diagnostic(
    provider: Provider, capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = provider("print('worktree-manager 1.2.3')")
    assert launch._usable_worktree_manager() == manifest.command
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
