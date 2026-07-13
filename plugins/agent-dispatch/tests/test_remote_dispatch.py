"""Tests for cross-machine dispatch (SSH-push, Phase 8 Slice 8a)."""

from __future__ import annotations

import argparse
import types

from agent_dispatch import remote_dispatch


def _args(**kw) -> argparse.Namespace:
    base = dict(
        title="do X", prompt="", spawn=True, proposed=False,
        spawn_backend="embody", target_machine="borealis",
        label=None, require=None, affinity=None, target_repo=None,
        target_worktree=None, source=None, dedup_key=None, verify_timeout=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_is_cross_machine_true_for_remote_embody(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")
    assert remote_dispatch.is_cross_machine(_args(target_machine="borealis")) is True


def test_is_cross_machine_false_for_local_target(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "borealis")
    assert remote_dispatch.is_cross_machine(_args(target_machine="borealis")) is False


def test_is_cross_machine_false_without_target(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")
    assert remote_dispatch.is_cross_machine(_args(target_machine=None)) is False


def test_is_cross_machine_false_for_bridge_backend(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")
    assert remote_dispatch.is_cross_machine(_args(spawn_backend="bridge")) is False


def test_is_cross_machine_false_when_not_spawning(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")
    assert remote_dispatch.is_cross_machine(_args(spawn=False)) is False


def test_is_cross_machine_false_when_local_unresolvable(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)
    assert remote_dispatch.is_cross_machine(_args()) is False


def test_build_remote_argv_drops_target_machine_and_adds_repo():
    argv = remote_dispatch.build_remote_create_argv(
        _args(prompt="go", label=["a", "b"], require=["cap"]),
        repo="gitea/x", has_payload=True,
    )
    assert argv[:2] == ["agent-dispatch", "create"]
    assert "do X" in argv
    # explicit lane + embody spawn; no cross-machine re-hop
    assert argv[argv.index("--repo") + 1] == "gitea/x"
    assert "--spawn" in argv and argv[argv.index("--spawn-backend") + 1] == "embody"
    assert "--target-machine" not in argv
    assert argv[argv.index("--prompt") + 1] == "go"
    assert argv.count("--label") == 2 and argv.count("--require") == 1
    # payload rides stdin
    assert argv[argv.index("--payload-file") + 1] == "-"


def test_build_remote_argv_no_payload_flag_without_payload():
    argv = remote_dispatch.build_remote_create_argv(
        _args(), repo="r", has_payload=False
    )
    assert "--payload-file" not in argv


def test_dispatch_to_remote_builds_ssh_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)

    remote_dispatch.dispatch_to_remote(
        "borealis", _args(prompt="go"), repo="gitea/x", payload="the brief"
    )
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "borealis" in cmd  # the facility alias, never a raw IP
    assert "BatchMode=yes" in cmd
    # the remote command is a single shell-quoted string
    remote_cmd = cmd[-1]
    assert "agent-dispatch create" in remote_cmd
    assert "--spawn-backend embody" in remote_cmd
    assert "'do X'" in remote_cmd  # title is shell-quoted
    assert captured["input"] == "the brief"  # payload streamed over stdin


def test_dispatch_to_remote_unavailable_without_ssh(monkeypatch):
    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: None)
    import pytest

    with pytest.raises(remote_dispatch.RemoteDispatchUnavailable):
        remote_dispatch.dispatch_to_remote(
            "borealis", _args(), repo="r", payload=None
        )
