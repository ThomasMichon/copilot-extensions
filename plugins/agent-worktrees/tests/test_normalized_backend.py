"""Recordless normalized handoff, without a fabricated worktree admission."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from agent_worktrees import agent_policy, config, launch_execution
from agent_worktrees import normalized_backend as backend
from agent_remote_driver.headed import caller


@pytest.mark.parametrize("identity", [[], ["--resume=existing"], ["--session-id", "selected"]])
def test_recordless_handoff_preserves_argv_and_rechecks_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: list[str],
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_policy, "require", lambda cwd: observed.append(cwd))
    monkeypatch.setenv(launch_execution.NATIVE_HINT_ENV, "stale-record-correlation")
    command = ["copilot", "--model", "--resume=not-an-identity", *identity, "-i", "quoted '\nseed"]

    def execute(argv: list[str], **kwargs: Any) -> tuple[int, bool]:
        assert observed == [str(Path.cwd())]
        if identity:
            assert kwargs["session_id"] == identity[-1].split("=")[-1]
        expected = command + ([] if identity else ["--session-id", kwargs["session_id"]])
        assert argv == [*expected, f"-C={kwargs['cwd']}"]
        assert kwargs["environment"].get(launch_execution.NATIVE_HINT_ENV) is None
        attempt = kwargs["attempt_directory"]
        assert attempt.parent == tmp_path / "launch-attempts"
        assert kwargs["state_file"] == attempt / "headed.json"
        assert (attempt / "runtime").is_dir()
        kwargs["admit"]()
        kwargs["spawned"](123, "native-birth")
        return 7, True

    monkeypatch.setattr(caller, "execute", execute)
    assert backend.launch(command) == 7
    assert observed == [str(Path.cwd()), str(Path.cwd())]
    assert not list(tmp_path.rglob("*.yaml"))


def test_recordless_refuses_an_existing_non_directory_storage_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "launch-attempts").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_policy, "require", lambda _: None)
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("unsafe storage execution"))
    with pytest.raises(ValueError, match="Could not prepare recordless launch storage"):
        backend.launch(["copilot"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
@pytest.mark.parametrize("mode", [0o775, 0o777])
def test_resume_does_not_require_private_launch_storage_or_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int,
) -> None:
    shared = tmp_path / "shared"
    root = shared / "installation"
    parent = root / "launch-attempts"
    parent.mkdir(parents=True)
    for path in (shared, root, parent):
        path.chmod(mode)
    before = {path: path.stat().st_mode for path in (shared, root, parent)}
    monkeypatch.setattr(config, "install_dir", lambda: root)
    monkeypatch.setattr(agent_policy, "require", lambda _: None)

    def execute(argv: list[str], **kwargs: Any) -> tuple[int, bool]:
        assert "--resume=existing" in argv
        attempt = kwargs["attempt_directory"]
        assert attempt.parent == parent and (attempt / "runtime").is_dir()
        assert attempt.stat().st_mode & 0o077 == 0
        assert (attempt / "runtime").stat().st_mode & 0o077 == 0
        return 0, True

    monkeypatch.setattr(caller, "execute", execute)
    assert backend.launch(["copilot", "--resume=existing"]) == 0
    assert {path: path.stat().st_mode for path in before} == before


@pytest.mark.parametrize("arguments", [
    ["--acp", "--stdio"], ["--ahp"], ["--connect", "ws://localhost"],
    ["--continue"], ["--resume"], ["-r", "existing"], ["--help"],
    ["--environment-host", "example"], ["-p", "one-shot"], ["positional seed"],
])
def test_unselected_modes_keep_the_original_native_command(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    command = ["copilot", *arguments]
    monkeypatch.setattr(agent_policy, "require", lambda *_: pytest.fail("headed admission"))
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("headed launch"))
    observed: list[list[str]] = []
    monkeypatch.setattr(backend, "_interactive_console", lambda: False)

    def popen(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
        observed.append(argv)
        return SimpleNamespace(wait=lambda: 9)
    monkeypatch.setattr(subprocess, "Popen", popen)
    if os.name != "nt":
        def execvp(executable: str, argv: list[str]) -> NoReturn:
            assert executable == command[0]
            observed.append(argv)
            raise SystemExit(9)
        monkeypatch.setattr(os, "execvp", execvp)
        with pytest.raises(SystemExit) as result:
            backend.launch(command)
        assert result.value.code == 9
    else:
        assert backend.launch(command) == 9
    assert observed == [command]


@pytest.mark.skipif(os.name != "nt", reason="the hidden-console stdio handoff is Windows-only")
@pytest.mark.parametrize("interactive", [False, True])
def test_windows_direct_backend_keeps_caller_stdio_only_when_hidden(
    monkeypatch: pytest.MonkeyPatch, interactive: bool,
) -> None:
    monkeypatch.setattr(backend, "_interactive_console", lambda: interactive)
    observed: list[dict[str, Any]] = []

    def popen(_argv: list[str], **kwargs: Any) -> SimpleNamespace:
        observed.append(kwargs)
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert backend.launch(["copilot", "--acp", "--stdio"]) == 0
    (kwargs,) = observed
    if interactive:
        assert kwargs == {}
    else:
        assert kwargs["stdin"] is sys.stdin
        assert kwargs["stdout"] is sys.stdout
        assert kwargs["stderr"] is sys.stderr
        assert kwargs["creationflags"]


def test_repository_refusal_precedes_any_recordless_core_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_cwd: str) -> NoReturn:
        raise ValueError("repository hosting disabled")
    monkeypatch.setattr(agent_policy, "require", refuse)
    monkeypatch.setattr(config, "install_dir", lambda: pytest.fail("storage before admission"))
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("launch before admission"))
    with pytest.raises(ValueError, match="hosting disabled"):
        backend.launch(["copilot"])


@pytest.mark.parametrize("form", ["separate", "equals"])
def test_native_directory_cannot_redirect_an_allowed_caller_to_a_denied_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str,
) -> None:
    allowed, denied = tmp_path / "allowed", tmp_path / "denied"
    for path in (allowed, denied):
        (path / ".git").mkdir(parents=True)
    policy = denied / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    monkeypatch.chdir(allowed)
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("redirected launch"))
    directory = ["-C", str(denied)] if form == "separate" else [f"-C={denied}"]
    with pytest.raises(ValueError, match="directory must match"):
        backend.launch(["copilot", *directory, "--resume=selected"])


def test_matching_directory_and_selector_shaped_values_keep_original_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    observed: list[str] = []
    monkeypatch.setattr(agent_policy, "require", observed.append)
    command = ["copilot", "-C", ".", "--model", "-C=not-a-directory", "--resume=selected"]
    def execute(argv: list[str], **kwargs: Any) -> tuple[int, bool]:
        assert argv == [
            "copilot", "--model", "-C=not-a-directory", "--resume=selected", f"-C={tmp_path.resolve()}",
        ]
        assert kwargs["cwd"] == str(tmp_path.resolve())
        kwargs["admit"]()
        return 0, True
    monkeypatch.setattr(caller, "execute", execute)
    assert backend.launch(command) == 0
    assert observed == [str(tmp_path.resolve())] * 2


@pytest.mark.parametrize("outcome", ["uncertain", "unknown", "unsupported"])
def test_selected_failure_never_replays_the_direct_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_policy, "require", lambda _: None)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_kw: pytest.fail("direct fallback after selection"),
    )
    def execute(*_args: Any, **_kwargs: Any) -> tuple[int, bool]:
        if outcome == "unknown":
            raise ValueError("outcome is unconfirmed")
        if outcome == "unsupported":
            raise RuntimeError("Selected core does not support headed execution")
        return 0, False
    monkeypatch.setattr(caller, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["normalized_backend", "--", "copilot"])
    assert backend.main() == 3
    diagnostic = capsys.readouterr().err
    assert ("not replayed" if outcome == "uncertain" else "no fallback was started") in diagnostic
