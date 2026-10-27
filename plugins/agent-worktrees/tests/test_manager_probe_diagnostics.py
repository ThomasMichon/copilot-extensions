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


@pytest.mark.parametrize("stderr", ["", " \n\t", "\x1b[0m"])
def test_probe_uses_stdout_when_stderr_is_empty(
    provider: Provider, capsys: pytest.CaptureFixture[str],
    stderr: str,
) -> None:
    provider(
        "import sys; "
        "print('\\x1b[31minterpreter startup failed before Manager\\x1b[0m'); "
        f"sys.stderr.write({stderr!r}); sys.exit(1)"
    )
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


def test_timeout_stdout_fallback_is_redacted_and_keeps_last_useful_evidence(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    provider("pass")
    calls = []

    def failed(argv: list[str], **kwargs: object) -> NoReturn:
        calls.append(kwargs)
        timeout = kwargs["timeout"]
        assert timeout == 20.0
        partial = (
            b"\x1b[31mresolver at https://packages.example/simple?token=fixture-secret\x1b[0m"
            if len(calls) == 1 else b""
        )
        raise subprocess.TimeoutExpired(argv, timeout, output=partial, stderr=b" \n")

    monkeypatch.setattr(launch.subprocess, "run", failed)
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert len(calls) == 2
    assert "after 2 attempts (20s each)" in captured.err
    assert "resolver at https://packages.example/simple" in captured.err
    assert "fixture-secret" not in captured.err
    assert "\x1b" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("as_bytes", [False, True])
def test_controls_and_credentials_are_removed_before_line_and_character_limits(
    as_bytes: bool,
) -> None:
    message = (
        "\x1b]0;ignored title\x07"
        "https://user:" + "fixture-password" * 200 + "@packages.example/simple\n"
        + "\n".join(f"progress {index}" for index in range(12))
        + "\n" + "x" * 2200
        + "\n\x1b[31mhttps://packages.example/simple?token=fixture-secret\x1b[0m\x00"
    )
    diagnostic = launch._safe_probe_diagnostic(
        message.encode("utf-8") if as_bytes else message,
    )
    assert diagnostic.startswith("[diagnostic truncated]\n")
    assert len(diagnostic.split("\n", 1)[1]) == 2048
    assert "https://packages.example/simple?[redacted]" in diagnostic
    assert "fixture-password" not in diagnostic
    assert "fixture-secret" not in diagnostic
    assert "ignored title" not in diagnostic
    assert "\x1b" not in diagnostic
    assert "\x00" not in diagnostic


@pytest.mark.parametrize("separator", ["\r", "\xa0", "\v", "\x00"])
@pytest.mark.parametrize("scheme", ["Bea" + "rer", "Ba" + "sic"])
def test_control_normalization_preserves_credential_boundaries(
    scheme: str, separator: str,
) -> None:
    credential = "fixture-token-1234"
    message = scheme + separator + credential
    assert credential in message
    diagnostic = launch._safe_probe_diagnostic(message)
    assert credential not in diagnostic
    assert scheme in diagnostic
    assert "***" in diagnostic


@pytest.mark.parametrize("password", [
    "fixture-password", "1234", "first-fragment\rsecond-fragment",
    "first-fragment\xa0second-fragment", "first-fragment\x00second-fragment",
])
def test_partial_timeout_url_userinfo_is_redacted_before_bounding(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    password: str,
) -> None:
    provider("pass")
    calls = []

    def failed(argv: list[str], **kwargs: object) -> NoReturn:
        calls.append(argv)
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        partial = ("Fetching https://fixture-user:" + password).encode("utf-8")
        raise subprocess.TimeoutExpired(argv, timeout, stderr=partial)

    monkeypatch.setattr(launch.subprocess, "run", failed)
    assert launch._usable_worktree_manager() is None
    captured = capsys.readouterr()
    assert len(calls) == 2
    assert "Fetching https://" in captured.err
    assert "fixture-user" not in captured.err
    assert password not in captured.err
    assert "first-fragment" not in captured.err
    assert "second-fragment" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("separator", ["\r", "\xa0", "\x00"])
def test_nonprinting_url_userinfo_does_not_leave_password_fragments(separator: str) -> None:
    message = "https://fixture-user:first-fragment" + separator + "second-fragment@host.example"
    diagnostic = launch._safe_probe_diagnostic(message)
    assert "fixture-user" not in diagnostic
    assert "first-fragment" not in diagnostic
    assert "second-fragment" not in diagnostic
    assert "host.example" in diagnostic


def test_long_registered_source_keeps_provider_and_registration_attribution(
    provider: Provider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    manifest = provider(_child_failure("startup refused"))
    manifest = replace(manifest, source_path="/registered/" + "x" * 1000)
    monkeypatch.setattr(launch, "_control_plane_provider_selection", lambda: (manifest, ""))
    health = launch._worktree_manager_health()
    assert health.state == "failed"
    assert "'worktree-manager' via /registered/" in health.diagnostic
    assert len(health.diagnostic) < 500


@pytest.mark.parametrize("control", [
    "\x1b[2J", "\x1b[H", "\x1b[31", "\x1b]unfinished-title",
    "\x90dcs\x9c", "\x9b2J", "\x1bPstatus\x1b\\",
])
def test_incomplete_or_alternate_terminal_controls_cannot_hide_the_cause(control: str) -> None:
    diagnostic = launch._safe_probe_diagnostic(control + "\nfixture startup refused")
    assert "fixture startup refused" in diagnostic
    assert all(char.isprintable() or char in "\n\t" for char in diagnostic)


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = provider("print('worktree-manager 1.2.3')")
    monkeypatch.setattr(
        launch, "_safe_probe_diagnostic",
        lambda *_a, **_kw: pytest.fail("healthy probes must not format failure details"),
    )
    assert launch._usable_worktree_manager() == manifest.command
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
