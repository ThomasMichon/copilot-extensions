"""Shell-owned launch publication, without starting a terminal or a mux."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import engine_client, launch_observations, relocated_launch, terminal_launch


@pytest.fixture
def channel(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    identity = {"version": 1, "nonce": "a" * 32, "host_pid": os.getpid()}
    request.write_text(json.dumps(identity), encoding="utf-8")
    script = tmp_path / "launch session \u65e5.ps1"
    script.write_text("exit 0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CE_TERMINAL_LAUNCH", str(request))
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    monkeypatch.delenv("WORKTREE_NO_MUX", raising=False)
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["fixture-engine"])
    events = []
    monkeypatch.setattr(
        launch_observations, "observe",
        lambda attempt, phase, **details: events.append((attempt, phase, details)),
    )
    return SimpleNamespace(
        request=request, identity=identity, script=script,
        result=tmp_path / "result.json", events=events,
    )


@pytest.mark.parametrize("with_attempt", [False, True])
def test_prepare_roundtrips_literal_launch_and_only_scoped_environment(
    channel, monkeypatch, tmp_path, with_attempt,
):
    from worktree_manager.warm_picker_contract import runtime_command

    engine = ["engine with spaces", "--literal", 'quote" and \u65e5']
    root = str(tmp_path / "manager with spaces")
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: engine)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", root)
    monkeypatch.setenv("WORKTREE_NO_MUX", "1")
    monkeypatch.setenv("UNRELATED_LAUNCH_FIXTURE", "must not be serialized")
    arguments = ["--project", "quote'\";$() \u65e5", "", "--worktree-id", "wt-a"]
    attempt = "b" * 32 if with_attempt else None
    before = dict(os.environ)

    assert terminal_launch.prepare(channel.script, arguments, attempt) is True

    result = json.loads(channel.result.read_text(encoding="utf-8"))
    assert result == {
        "version": 1, "nonce": channel.identity["nonce"],
        "script": str(channel.script.resolve()), "arguments": arguments,
        "cwd": str(tmp_path),
        "environment": {
            "WORKTREE_MANAGER_ENGINE_ARGV": json.dumps(engine),
            "WORKTREE_MANAGER_ROOT": root, "WORKTREE_NO_MUX": "1",
        },
        "completion": (
            runtime_command("worktree_manager.terminal_launch", attempt) if with_attempt else []
        ),
    }
    assert json.loads(result["environment"]["WORKTREE_MANAGER_ENGINE_ARGV"]) == engine
    assert dict(os.environ) == before
    assert channel.events == [(attempt, "launch-requested", {})]
    assert json.loads(channel.request.read_text(encoding="utf-8")) == channel.identity


@pytest.mark.parametrize("damage", [
    "relative", "wrong-name", "directory", "oversize", "json",
    "non-object", "version", "boolean-version", "nonce", "uppercase-nonce",
    "host", "boolean-host",
])
def test_invalid_request_never_publishes_or_observes(channel, monkeypatch, damage):
    request = channel.request
    identity = channel.identity.copy()
    if damage == "relative":
        monkeypatch.setenv("CE_TERMINAL_LAUNCH", request.name)
    elif damage == "wrong-name":
        request = request.rename(request.with_name("other.json"))
        monkeypatch.setenv("CE_TERMINAL_LAUNCH", str(request))
    elif damage == "directory":
        request.unlink()
        request.mkdir()
    elif damage == "oversize":
        request.write_text(" " * 4097, encoding="utf-8")
    elif damage == "json":
        request.write_text("{", encoding="utf-8")
    elif damage == "non-object":
        request.write_text("[]", encoding="utf-8")
    else:
        key, value = {
            "version": ("version", 2),
            "boolean-version": ("version", True),
            "nonce": ("nonce", "a" * 31),
            "uppercase-nonce": ("nonce", "A" * 32),
            "host": ("host_pid", 0),
            "boolean-host": ("host_pid", True),
        }[damage]
        identity[key] = value
        request.write_text(json.dumps(identity), encoding="utf-8")

    with pytest.raises(ValueError):
        terminal_launch.prepare(channel.script, ["--base"], None)
    assert not channel.result.exists()
    assert channel.events == []


def test_publication_is_exclusive_and_cannot_replace_the_first_selection(channel):
    assert terminal_launch.prepare(channel.script, ["--project", "first"], None)
    admitted = channel.result.read_bytes()

    with pytest.raises(FileExistsError):
        terminal_launch.prepare(channel.script, ["--project", "second"], None)

    assert channel.result.read_bytes() == admitted
    assert json.loads(admitted)["arguments"] == ["--project", "first"]
    assert all(phase != "launcher-returned" for _, phase, _ in channel.events)


def test_missing_selected_script_does_not_publish(channel):
    with pytest.raises(FileNotFoundError):
        terminal_launch.prepare(channel.script.with_name("absent.ps1"), [], None)
    assert not channel.result.exists()


@pytest.mark.parametrize("offered", [False, True])
def test_relocated_launch_prepares_instead_of_spawning_or_waiting(
    channel, monkeypatch, offered,
):
    monkeypatch.setattr(
        relocated_launch, "_core", lambda: SimpleNamespace(_is_windows=lambda: True),
    )
    calls = []

    def popen(arguments):
        assert not offered, "Shell-owned launch must not retain a Python Popen waiter"
        calls.append(arguments)
        return SimpleNamespace(wait=lambda: calls.append("wait") or 23)

    monkeypatch.setattr(relocated_launch.subprocess, "Popen", popen)
    if not offered:
        monkeypatch.delenv("CE_TERMINAL_LAUNCH")
    request = SimpleNamespace(
        project="project with spaces", mode="resume", worktree_id="wt-a",
        attempt_id=None, no_mux=False, new_window=False,
    )
    plan = SimpleNamespace(worktree_id="selected-wt", raw={})

    code = relocated_launch._run_relocated_mux_launch(request, plan, channel.script)

    selected = ["--project", request.project, "--worktree-id", "selected-wt"]
    if offered:
        assert code == 0
        assert calls == []
        result = json.loads(channel.result.read_text(encoding="utf-8"))
        assert result["arguments"] == selected
        assert result["cwd"] == str(Path.cwd())
        assert result["environment"]["WORKTREE_MANAGER_ROOT"] is None
        assert result["environment"]["WORKTREE_NO_MUX"] is None
        assert result["completion"] == []
    else:
        assert code == 23
        assert calls == [
            ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(channel.script), *selected],
            "wait",
        ]
        assert not channel.result.exists()


def test_without_host_channel_prepare_keeps_legacy_path_without_touching_files(
    channel, monkeypatch,
):
    monkeypatch.delenv("CE_TERMINAL_LAUNCH")
    assert terminal_launch.prepare(Path("does-not-exist.ps1"), [], None) is False
    assert not channel.result.exists()
    assert channel.events == []


def test_manager_entrypoint_does_not_log_return_before_shell_completion(channel, monkeypatch):
    from worktree_manager import __main__ as main, launcher

    attempt = "b" * 32
    plan = engine_client.launch_plan_from_dict({
        "action": "exec", "worktree_id": "selected-wt", "launch_attempt_id": attempt,
        "launch_attempt_receipt": {
            "version": 1, "operation_id": "c" * 32,
            "phase": "plan-ready", "worktree_id": "selected-wt",
        },
    })
    monkeypatch.setattr(main, "_is_windows", lambda: True)
    monkeypatch.setattr(main, "_relocated_launch_script", lambda: channel.script)
    monkeypatch.setattr(main, "_resolve_for", lambda *args, **kwargs: (plan, 0))
    monkeypatch.setattr(launcher, "uses_ahp", lambda *args: False)
    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen",
        lambda *args, **kwargs: pytest.fail("Preparation must not start a launch waiter"),
    )
    request = SimpleNamespace(
        project="example", mode="resume", worktree_id="selected-wt",
        attempt_id=attempt, no_mux=False, new_window=False,
    )

    assert main._run_launch(request) == 0
    result = json.loads(channel.result.read_text(encoding="utf-8"))
    assert result["arguments"][-3:] == [
        "--require-launch-attempt", "--launch-attempt-id", attempt,
    ]
    assert result["completion"][-3:] == ["-m", "worktree_manager.terminal_launch", attempt]
    assert channel.events == [(attempt, "launch-requested", {})]
    assert terminal_launch.main([attempt, "23"]) == 0
    assert channel.events[-1] == (attempt, "launcher-returned", {"exit_code": 23})


@pytest.mark.parametrize("arguments, expected", [
    (["b" * 32, "23"], 0),
    (["b" * 32, "130"], 0),
    (["b" * 32], 2),
    (["b" * 32, "invalid"], 1),
])
def test_completion_is_separate_and_retains_the_launchers_actual_exit(
    channel, arguments, expected,
):
    assert terminal_launch.main(arguments) == expected
    assert channel.events == (
        [(arguments[0], "launcher-returned", {"exit_code": int(arguments[1])})]
        if expected == 0 else []
    )
    assert not channel.result.exists()


def test_completion_failure_is_not_reported_as_success(channel, monkeypatch, capsys):
    from worktree_manager.launch_attempts import LaunchAttemptError

    def unavailable(*args, **kwargs):
        raise LaunchAttemptError("fixture journal unavailable")

    monkeypatch.setattr(launch_observations, "observe", unavailable)
    assert terminal_launch.main(["b" * 32, "23"]) == 1
    assert "Could not retain terminal launch outcome" in capsys.readouterr().err
