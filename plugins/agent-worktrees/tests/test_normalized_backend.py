"""Recordless normalized handoff, without a fabricated worktree admission."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import normalized_backend as backend
from agent_remote_driver.headed import caller


@pytest.mark.parametrize("identity", [[], ["--resume=existing"], ["--session-id", "selected"]])
def test_recordless_handoff_preserves_argv_and_rechecks_policy(tmp_path, monkeypatch, identity):
    observed = []
    monkeypatch.setattr(backend.config, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(backend.agent_policy, "require", lambda cwd: observed.append(cwd))
    monkeypatch.setenv(backend.launch_execution.NATIVE_HINT_ENV, "stale-record-correlation")
    command = ["copilot", "--model", "--resume=not-an-identity", *identity, "-i", "quoted '\nseed"]

    def execute(argv, **kwargs):
        assert observed == [str(Path.cwd())]
        assert kwargs["session_id"] == (identity[-1].split("=")[-1] if identity else argv[-1])
        assert argv[:len(command)] == command
        assert len(argv) == len(command) + (0 if identity else 2)
        assert kwargs["environment"].get(backend.launch_execution.NATIVE_HINT_ENV) is None
        attempt = kwargs["attempt_directory"]
        assert attempt.parent == tmp_path / "launch-attempts"
        assert kwargs["state_file"] == attempt / "headed.json"
        assert not attempt.exists()
        kwargs["admit"]()
        kwargs["spawned"](123, "native-birth")
        return 7, True

    monkeypatch.setattr(caller, "execute", execute)
    assert backend.launch(command) == 7
    assert observed == [str(Path.cwd()), str(Path.cwd())]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("arguments", [
    ["--acp", "--stdio"], ["--ahp"], ["--connect", "ws://localhost"],
    ["--continue"], ["--resume"], ["-r", "existing"], ["--help"],
    ["--environment-host", "example"], ["-p", "one-shot"], ["positional seed"],
])
def test_unselected_modes_keep_the_original_native_command(monkeypatch, arguments):
    command = ["copilot", *arguments]
    monkeypatch.setattr(backend.agent_policy, "require", lambda *_: pytest.fail("headed admission"))
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("headed launch"))
    observed = []
    monkeypatch.setattr(backend, "_interactive_console", lambda: False)
    monkeypatch.setattr(
        backend.subprocess, "Popen",
        lambda argv, **_kwargs: observed.append(argv) or SimpleNamespace(wait=lambda: 9),
    )
    if os.name != "nt":
        def execvp(executable, argv):
            assert executable == command[0]
            observed.append(argv)
            raise SystemExit(9)
        monkeypatch.setattr(backend.os, "execvp", execvp)
        with pytest.raises(SystemExit) as result:
            backend.launch(command)
        assert result.value.code == 9
    else:
        assert backend.launch(command) == 9
    assert observed == [command]


def test_repository_refusal_precedes_any_recordless_core_preparation(monkeypatch):
    def refuse(_cwd):
        raise ValueError("repository hosting disabled")
    monkeypatch.setattr(backend.agent_policy, "require", refuse)
    monkeypatch.setattr(backend.config, "install_dir", lambda: pytest.fail("storage before admission"))
    monkeypatch.setattr(caller, "execute", lambda *_a, **_kw: pytest.fail("launch before admission"))
    with pytest.raises(ValueError, match="hosting disabled"):
        backend.launch(["copilot"])


@pytest.mark.parametrize("form", ["separate", "equals"])
def test_native_directory_cannot_redirect_an_allowed_caller_to_a_denied_repo(tmp_path, monkeypatch, form):
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


def test_matching_directory_and_selector_shaped_values_keep_original_argv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backend.config, "install_dir", lambda: tmp_path)
    observed = []
    monkeypatch.setattr(backend.agent_policy, "require", observed.append)
    command = ["copilot", "-C", ".", "--model", "-C=not-a-directory", "--resume=selected"]
    def execute(argv, **kwargs):
        assert argv == command and kwargs["cwd"] == str(tmp_path.resolve())
        kwargs["admit"]()
        return 0, True
    monkeypatch.setattr(caller, "execute", execute)
    assert backend.launch(command) == 0
    assert observed == [str(tmp_path.resolve())] * 2


@pytest.mark.parametrize("outcome", ["uncertain", "unknown", "unsupported"])
def test_selected_failure_never_replays_the_direct_backend(tmp_path, monkeypatch, capsys, outcome):
    monkeypatch.setattr(backend.config, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(backend.agent_policy, "require", lambda _: None)
    monkeypatch.setattr(
        backend.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("direct fallback after selection"),
    )
    def execute(*_args, **_kwargs):
        if outcome == "unknown":
            raise ValueError("outcome is unconfirmed")
        if outcome == "unsupported":
            raise RuntimeError("Selected core does not support headed execution")
        return 0, False
    monkeypatch.setattr(caller, "execute", execute)
    monkeypatch.setattr(backend.sys, "argv", ["normalized_backend", "--", "copilot"])
    assert backend.main() == 3
    diagnostic = capsys.readouterr().err
    assert ("not replayed" if outcome == "uncertain" else "no fallback was started") in diagnostic
