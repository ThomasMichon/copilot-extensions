"""Provider-owned attachment avoids a second Python front door, not its gates."""

from __future__ import annotations

import sys
import os
from types import SimpleNamespace

import pytest
from work_coalescing_singleton import client
from zdd import routing
from agent_procutil import interactive

from agent_worktrees import picker_fast_entry, picker_session_entry as entry


@pytest.fixture
def session(tmp_path, monkeypatch):
    directory = tmp_path / "picker-sessions" / "generation" / "session"
    directory.mkdir(parents=True)
    (tmp_path / "mux-daemon.token").write_text("fixture-token")
    handoff = {"manager_session": {"version": 1, "root": str(tmp_path)}}
    reply = {
        "available": True, "directory": str(directory),
        "attach_command": [sys.executable, "attach-fixture"],
        "complete_command": [sys.executable, "complete-fixture"],
    }
    requests, commands, released = [], [], []
    monkeypatch.setattr(
        routing, "read_active_endpoint",
        lambda root, **kwargs: SimpleNamespace(client_host="127.0.0.1", port=12345, version="generation"),
    )

    def request(*args, **kwargs):
        requests.append((args, kwargs))
        return reply

    def wait(command, environment):
        assert (directory / "attached").is_file()
        commands.append((command, environment))
        return 0 if len(commands) == 1 else 17

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(client, "release", lambda *args, **kwargs: released.append(args))
    monkeypatch.setattr(entry, "_wait", wait)
    monkeypatch.setattr(interactive, "run_interactive", lambda command, *, env: wait(command, env))
    return handoff, reply, directory, requests, commands, released


def test_authenticated_handoff_attaches_first_and_starts_decision_runtime_only_afterward(session):
    handoff, _, directory, requests, commands, released = session
    environment = {"PSMUX_SESSION": "outer", "TMUX": "outer", "AGENT_HOME": "selected-home"}
    assert entry.open_session(handoff, ["selected-engine"], environment) == 17
    assert requests[0][1]["payload"]["engine"] == ["selected-engine"]
    assert requests[0][1]["payload"]["provider_generation"] == "generation"
    assert requests[0][1]["payload"]["context"]["AGENT_HOME"] == "selected-home"
    assert commands[0] == ([sys.executable, "attach-fixture"], {"AGENT_HOME": "selected-home"})
    assert commands[1] == ([sys.executable, "complete-fixture", "--attach-exit", "0"], environment)
    assert len(released) == 1
    assert directory.exists()


def test_missing_spare_retains_cold_compatibility_without_attaching(session):
    handoff, reply, directory, _, commands, released = session
    reply["available"] = False
    assert entry.open_session(handoff, ["selected-engine"], {}) is None
    assert not commands and not (directory / "attached").exists()
    assert len(released) == 1


@pytest.mark.parametrize("invalid", ["foreign-directory", "relative-attach", "nul-complete"])
def test_invalid_admitted_reply_is_terminal_not_a_second_cold_ui(session, tmp_path, invalid):
    handoff, reply, directory, _, commands, _ = session
    if invalid == "foreign-directory":
        reply["directory"] = str(tmp_path)
    elif invalid == "relative-attach":
        reply["attach_command"] = ["relative-program"]
    else:
        reply["complete_command"] = [sys.executable, "\0"]
    assert entry.open_session(handoff, ["selected-engine"], {}) == 1
    assert not commands and not (directory / "attached").exists()


def test_absent_token_does_not_create_one_or_start_a_provider(session):
    handoff, _, directory, requests, commands, _ = session
    token = directory.parents[2] / "mux-daemon.token"
    token.unlink()
    assert entry.open_session(handoff, ["selected-engine"], {}) is None
    assert not requests and not commands and not token.exists()


def test_nonloopback_route_is_not_a_provider_launch_authority(session, monkeypatch):
    handoff, _, _, requests, commands, _ = session
    monkeypatch.setattr(
        routing, "read_active_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(client_host="example.invalid", port=12345),
    )
    assert entry.open_session(handoff, ["selected-engine"], {}) is None
    assert not requests and not commands


def test_completion_failure_after_attachment_never_falls_back(session, monkeypatch):
    handoff, _, _, _, _, _ = session
    monkeypatch.setattr(
        interactive, "run_interactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing executable")),
    )
    assert entry.open_session(handoff, ["selected-engine"], {}) == 1


def test_fast_entry_uses_provider_session_without_launching_a_manager_front_door(session, monkeypatch):
    handoff, _, directory, _, _, _ = session
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(picker_fast_entry.registry_paths, "registry_root", lambda: directory)
    monkeypatch.setattr(picker_fast_entry, "_request", lambda *_args: handoff)
    monkeypatch.setattr(
        entry, "open_session",
        lambda result, engine, env: 19 if result is handoff and engine[-1] == "agent_worktrees" else None,
    )
    assert picker_fast_entry.dispatch(["--project", "sample"]) == 19


@pytest.mark.parametrize("key", [
    "GH_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN", "COPILOT_AGENT_SESSION_ID",
    "AGENT_WORKTREES_BIND_SESSION_ID", "CE_UPDATE_AUTH", "CONTEXT_HANDOFF_ID",
])
def test_session_or_authorization_context_stays_on_the_cold_path(session, key):
    handoff, _, _, requests, commands, _ = session
    assert entry.open_session(handoff, ["selected-engine"], {key: "private-value"}) is None
    assert not requests and not commands


def test_launch_carries_caller_cwd_but_no_environment_values(session, monkeypatch, tmp_path):
    handoff, _, _, requests, _, _ = session
    monkeypatch.chdir(tmp_path)
    environment = {"HOME": "caller-home", "AGENT_WORKTREES_PICKER_STREAM": "0"}
    entry.open_session(handoff, ["selected-engine"], environment)
    payload = requests[0][1]["payload"]
    assert payload["cwd"] == os.fspath(tmp_path)
    assert "caller-home" not in str(payload)
    assert len(payload["context"]["environment_sha256"]) == 64


def test_launch_identity_is_carried_separately_from_reusable_environment_scope(session):
    handoff, _, _, requests, _, _ = session
    environment = {
        "PYTHONUTF8": "1", "AGENT_WORKTREES_LAUNCH_ID": "sample-fresh-launch",
        "AGENT_WORKTREES_BINSTUB_STARTED": "fixture-start",
    }
    assert entry.invocation_context(environment) == entry.invocation_context({})
    entry.open_session(handoff, ["selected-engine"], environment)
    payload = requests[0][1]["payload"]
    assert payload["launch_id"] == "sample-fresh-launch"
    assert payload["binstub_started"] == "fixture-start"


def test_selected_provider_root_normalizes_only_the_missing_launcher_default(session):
    handoff, _, directory, requests, _, _ = session
    root = directory.parents[2]
    assert entry.invocation_context({}, manager_root=root) == entry.invocation_context({
        "WORKTREE_MANAGER_ROOT": str(root),
    })
    assert entry.invocation_context({
        "WORKTREE_MANAGER_ROOT": str(root / "override"),
    }, manager_root=root) != entry.invocation_context({}, manager_root=root)
    entry.open_session(handoff, ["selected-engine"], {})
    assert requests[0][1]["payload"]["context"] == entry.invocation_context({}, manager_root=root)


def test_thin_client_preserves_the_selected_nondefault_socket_command(session):
    handoff, reply, _, _, commands, _ = session
    reply["attach_command"] = [sys.executable, "-S", "/fixture/tmux-selected.sock", "attach-fixture"]
    assert entry.open_session(handoff, ["selected-engine"], {}) == 17
    assert commands[0][0] == reply["attach_command"]


def test_generated_project_launcher_preserves_reusable_scope_across_fresh_launch_ids(session, tmp_path):
    import json
    import shutil
    import subprocess
    from agent_procutil import no_window_kwargs
    from agent_worktrees import project_command_templates

    payload_root = tmp_path / "payload"
    command_dir = payload_root / "bin" / "payload"
    command_dir.mkdir(parents=True)
    if os.name == "nt":
        shell = shutil.which("pwsh")
        if shell is None:
            pytest.skip("PowerShell is unavailable")
        command = command_dir / "agent-worktrees.ps1"
        command.write_text(
            "[ordered]@{PYTHONUTF8=$env:PYTHONUTF8;"
            "AGENT_WORKTREES_LAUNCH_ID=$env:AGENT_WORKTREES_LAUNCH_ID} | ConvertTo-Json -Compress\n"
        )
        name = "sample.ps1"
        prefix = [shell, "-NoLogo", "-NoProfile", "-File"]
    else:
        shell = shutil.which("bash")
        if shell is None:
            pytest.skip("Bash is unavailable")
        command = command_dir / "agent-worktrees"
        command.write_text(
            '#!/usr/bin/env bash\nprintf \'{"PYTHONUTF8":"%s","AGENT_WORKTREES_LAUNCH_ID":"%s"}\\n\' '
            '"$PYTHONUTF8" "$AGENT_WORKTREES_LAUNCH_ID"\n'
        )
        command.chmod(0o700)
        name = "sample"
        prefix = [shell]
    generated = project_command_templates.render("sample", payload_root, windows=os.name == "nt")
    launcher = tmp_path / name
    launcher.write_text(generated[name], encoding="utf-8")
    base_environment = {"PATH": os.environ["PATH"], "AGENT_WORKTREES_LAUNCH_ID": "resident-launch"}
    for name in ("SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE"):
        if name in os.environ:
            base_environment[name] = os.environ[name]
    ids = []
    handoff, _, _, requests, _, _ = session
    for _ in range(2):
        completed = subprocess.run(
            [*prefix, str(launcher)], env=base_environment,
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert completed.returncode == 0, completed.stderr
        environment = {**base_environment, **json.loads(completed.stdout)}
        ids.append(environment["AGENT_WORKTREES_LAUNCH_ID"])
        assert entry.invocation_context(environment) == entry.invocation_context(base_environment)
        entry.open_session(handoff, ["selected-engine"], environment)
        assert requests[-1][1]["payload"]["launch_id"] == ids[-1]
    assert len(set(ids)) == 2 and all(value.startswith("sample-") for value in ids)
