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


# -- Peer-queue browse (Phase 8 Slice 8c) ------------------------------------


def _browse_args(**kw) -> argparse.Namespace:
    base = dict(
        machine="borealis", status=None, label=None, limit=200,
        repo=None, target_machine=None, target_repo=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_is_peer_machine_true_for_remote(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")
    assert remote_dispatch.is_peer_machine("borealis") is True


def test_is_peer_machine_false_for_local_and_unset(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "borealis")
    assert remote_dispatch.is_peer_machine("borealis") is False
    assert remote_dispatch.is_peer_machine(None) is False


def test_is_peer_machine_false_when_local_unresolvable(monkeypatch):
    # Can't prove it's remote -> stay local (safe degrade).
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)
    assert remote_dispatch.is_peer_machine("borealis") is False


def test_build_remote_browse_argv_list_forwards_filters_drops_machine():
    args = _browse_args(status="queued,started", label="bug", limit=50,
                        target_machine="borealis", target_repo="x")
    argv = remote_dispatch.build_remote_browse_argv("list", args, repo="gitea/lane")
    assert argv[:2] == ["agent-dispatch", "list"]
    # list needs no machine identity (scopes by --repo); dropping --machine keeps
    # a peer on an older agent-dispatch (no `list --machine`) compatible.
    assert "--machine" not in argv
    assert argv[argv.index("--status") + 1] == "queued,started"
    assert argv[argv.index("--label") + 1] == "bug"
    assert argv[argv.index("--limit") + 1] == "50"
    assert argv[argv.index("--repo") + 1] == "gitea/lane"  # locally-resolved lane
    assert argv[argv.index("--target-machine") + 1] == "borealis"
    assert argv[argv.index("--target-repo") + 1] == "x"


def test_build_remote_browse_argv_inbox_minimal():
    args = _browse_args(status="proposed", label=None, limit=200)
    argv = remote_dispatch.build_remote_browse_argv("inbox", args)
    assert argv[:2] == ["agent-dispatch", "inbox"]
    assert argv[argv.index("--machine") + 1] == "borealis"  # peer identity forwarded
    assert "--repo" not in argv  # inbox is cross-lane; no repo forwarded
    assert argv[argv.index("--status") + 1] == "proposed"


def test_browse_remote_builds_ssh_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)

    out = remote_dispatch.browse_remote("borealis", ["agent-dispatch", "list"])
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "borealis" in cmd
    assert "BatchMode=yes" in cmd
    assert "ConnectTimeout=5" in cmd
    assert cmd[-1] == "agent-dispatch list"
    assert out.stdout == "[]"


def test_browse_remote_unavailable_without_ssh(monkeypatch):
    import pytest

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: None)
    with pytest.raises(remote_dispatch.RemoteDispatchUnavailable):
        remote_dispatch.browse_remote("borealis", ["agent-dispatch", "inbox"])
