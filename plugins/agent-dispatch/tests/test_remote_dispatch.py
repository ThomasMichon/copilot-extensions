"""Tests for cross-machine dispatch (SSH-push, Phase 8 Slice 8a)."""

from __future__ import annotations

import argparse
import types

import pytest

from agent_dispatch import remote_dispatch


def _args(**kw) -> argparse.Namespace:
    base = dict(
        title="do X", prompt="", spawn=True, proposed=False,
        spawn_backend="embody", target_machine="emancipation-cube",
        label=None, require=None, affinity=None, target_repo=None,
        target_worktree=None, source=None, dedup_key=None, verify_timeout=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_is_cross_machine_true_for_remote_embody(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_cross_machine(_args(target_machine="emancipation-cube")) is True


def test_is_cross_machine_false_for_local_target(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "emancipation-cube")
    assert remote_dispatch.is_cross_machine(_args(target_machine="emancipation-cube")) is False


def test_is_cross_machine_false_without_target(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_cross_machine(_args(target_machine=None)) is False


def test_is_cross_machine_false_for_bridge_backend(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_cross_machine(_args(spawn_backend="bridge")) is False


def test_is_cross_machine_false_when_not_spawning(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_cross_machine(_args(spawn=False)) is False


def test_is_cross_machine_false_when_local_unresolvable(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)
    assert remote_dispatch.is_cross_machine(_args()) is False


def test_is_cross_machine_case_insensitive_local_target(monkeypatch):
    # A display-cased target that names *this* machine must read as local, not a
    # remote peer (else we'd SSH to ourselves).
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_cross_machine(
        _args(target_machine="Anomalous-Potato")
    ) is False


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
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)
    monkeypatch.setattr(
        remote_dispatch, "no_window_kwargs", lambda: {"creationflags": 123}
    )

    remote_dispatch.dispatch_to_remote(
        "emancipation-cube", _args(prompt="go"), repo="gitea/x", payload="the brief"
    )
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "emancipation-cube" in cmd  # the SSH alias, never a raw IP
    assert "BatchMode=yes" in cmd
    # the remote command is a single shell-quoted string
    remote_cmd = cmd[-1]
    assert "agent-dispatch create" in remote_cmd
    assert "--spawn-backend embody" in remote_cmd
    assert "'do X'" in remote_cmd  # title is shell-quoted
    assert captured["input"] == "the brief"  # payload streamed over stdin
    assert captured["kwargs"]["creationflags"] == 123


def test_dispatch_to_remote_unavailable_without_ssh(monkeypatch):
    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: None)
    import pytest

    with pytest.raises(remote_dispatch.RemoteDispatchUnavailable):
        remote_dispatch.dispatch_to_remote(
            "emancipation-cube", _args(), repo="r", payload=None
        )


# -- Peer-queue browse (Phase 8 Slice 8c) ------------------------------------


def _browse_args(**kw) -> argparse.Namespace:
    base = dict(
        machine="emancipation-cube", status=None, label=None, limit=200,
        repo=None, target_machine=None, target_repo=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_is_peer_machine_true_for_remote(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_peer_machine("emancipation-cube") is True


def test_is_peer_machine_false_for_local_and_unset(monkeypatch):
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "emancipation-cube")
    assert remote_dispatch.is_peer_machine("emancipation-cube") is False
    assert remote_dispatch.is_peer_machine(None) is False


def test_is_peer_machine_false_when_local_unresolvable(monkeypatch):
    # Can't prove it's remote -> stay local (safe degrade).
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: None)
    assert remote_dispatch.is_peer_machine("emancipation-cube") is False


def test_is_peer_machine_case_insensitive_local(monkeypatch):
    # The picker passes the machines.yaml display_name ("Anomalous-Potato") while the
    # resolved identity is the registry key ("anomalous-potato"); a display-cased name
    # for *this* machine must not be treated as a peer (no self-SSH).
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_peer_machine("Anomalous-Potato") is False


def test_is_peer_machine_case_insensitive_remote_still_peer(monkeypatch):
    # A display-cased name for a *different* machine is still a peer.
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")
    assert remote_dispatch.is_peer_machine("Emancipation-Cube") is True


def test_build_remote_browse_argv_list_forwards_filters_drops_machine():
    args = _browse_args(status="queued,started", label="bug", limit=50,
                        target_machine="emancipation-cube", target_repo="x")
    argv = remote_dispatch.build_remote_browse_argv("list", args, repo="gitea/lane")
    assert argv[:2] == ["agent-dispatch", "list"]
    # list needs no machine identity (scopes by --repo); dropping --machine keeps
    # a peer on an older agent-dispatch (no `list --machine`) compatible.
    assert "--machine" not in argv
    assert argv[argv.index("--status") + 1] == "queued,started"
    assert argv[argv.index("--label") + 1] == "bug"
    assert argv[argv.index("--limit") + 1] == "50"
    assert argv[argv.index("--repo") + 1] == "gitea/lane"  # locally-resolved lane
    assert argv[argv.index("--target-machine") + 1] == "emancipation-cube"
    assert argv[argv.index("--target-repo") + 1] == "x"


def test_build_remote_browse_argv_inbox_minimal():
    args = _browse_args(status="proposed", label=None, limit=200)
    argv = remote_dispatch.build_remote_browse_argv("inbox", args)
    assert argv[:2] == ["agent-dispatch", "inbox"]
    assert argv[argv.index("--machine") + 1] == "emancipation-cube"  # peer identity forwarded
    assert "--repo" not in argv  # inbox is cross-lane; no repo forwarded
    assert argv[argv.index("--status") + 1] == "proposed"


def test_build_remote_browse_argv_inbox_forwards_awaiting_steer():
    args = _browse_args(status="proposed", awaiting_steer=True)
    argv = remote_dispatch.build_remote_browse_argv("inbox", args)
    assert "--awaiting-steer" in argv  # steer surface forwarded to the peer
    # Absent by default (older callers / list) -> not forwarded.
    assert "--awaiting-steer" not in remote_dispatch.build_remote_browse_argv(
        "inbox", _browse_args(status="proposed"))
    assert "--awaiting-steer" not in remote_dispatch.build_remote_browse_argv(
        "list", _browse_args(status="proposed", awaiting_steer=True), repo="x")


def test_build_remote_browse_argv_inbox_forwards_board_without_status():
    args = _browse_args(status="proposed", board=True, recent_mins=45)
    argv = remote_dispatch.build_remote_browse_argv("inbox", args)
    assert "--board" in argv
    assert argv[argv.index("--recent-mins") + 1] == "45"
    assert "--status" not in argv
    assert "--awaiting-steer" not in argv


def test_browse_remote_builds_ssh_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)
    monkeypatch.setattr(
        remote_dispatch, "no_window_kwargs", lambda: {"creationflags": 123}
    )

    out = remote_dispatch.browse_remote("emancipation-cube", ["agent-dispatch", "list"])
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/ssh"
    assert "emancipation-cube" in cmd
    assert "BatchMode=yes" in cmd
    assert "ConnectTimeout=5" in cmd
    assert cmd[-1] == "agent-dispatch list"
    assert out.stdout == "[]"
    assert captured["kwargs"]["creationflags"] == 123


def test_browse_remote_unavailable_without_ssh(monkeypatch):
    import pytest

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: None)
    with pytest.raises(remote_dispatch.RemoteDispatchUnavailable):
        remote_dispatch.browse_remote("emancipation-cube", ["agent-dispatch", "inbox"])


def test_browse_remote_lowercases_display_cased_alias(monkeypatch):
    # A display-cased peer name ("Emancipation-Cube") must connect via its lowercase
    # `Host emancipation-cube` block, not a literal "Emancipation-Cube" hostname.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)

    remote_dispatch.browse_remote("Emancipation-Cube", ["agent-dispatch", "list"])
    cmd = captured["cmd"]
    assert "emancipation-cube" in cmd
    assert "Emancipation-Cube" not in cmd


def test_dispatch_to_remote_lowercases_display_cased_alias(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(remote_dispatch.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(remote_dispatch.subprocess, "run", fake_run)

    remote_dispatch.dispatch_to_remote(
        "Emancipation-Cube", _args(prompt="go"), repo="gitea/x", payload="brief"
    )
    cmd = captured["cmd"]
    assert "emancipation-cube" in cmd
    assert "Emancipation-Cube" not in cmd


# -- Actionable degradation for failed remote invocations (issue #2735) -------


def test_diagnose_remote_failure_not_installed_127():
    msg = remote_dispatch.diagnose_remote_failure(
        "mantis-counter", 127, "bash: agent-dispatch: command not found\n"
    )
    assert "mantis-counter" in msg
    assert "not installed" in msg
    assert "PATH" in msg


def test_diagnose_remote_failure_coordinator_unreachable():
    stderr = (
        "Traceback (most recent call last):\n"
        "httpx.ConnectError: [WinError 10061] No connection could be made "
        "because the target machine actively refused it\n"
    )
    msg = remote_dispatch.diagnose_remote_failure("emancipation-cube", 1, stderr)
    assert "emancipation-cube" in msg
    assert "coordinator" in msg
    assert "running" in msg
    # The raw traceback is not echoed.
    assert "Traceback" not in msg


def test_diagnose_remote_failure_generic_tail():
    msg = remote_dispatch.diagnose_remote_failure(
        "emancipation-cube", 3, "line one\nsomething specific went wrong\n"
    )
    assert "exit 3" in msg
    assert "something specific went wrong" in msg  # last non-empty line


def test_diagnose_remote_failure_no_stderr():
    msg = remote_dispatch.diagnose_remote_failure("emancipation-cube", 2, "")
    assert "emancipation-cube" in msg
    assert "exit 2" in msg


# -- local_machine resolution (configured alias, then identity/node fallbacks) ----


def test_local_machine_prefers_configured_environment(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "AugLoop1")
    monkeypatch.setattr(
        "agent_dispatch.identity.resolve_identity",
        lambda: pytest.fail("identity subprocess fallback should not run"),
    )
    assert remote_dispatch.local_machine() == "augloop1"


def test_local_machine_reads_configured_supervisor_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISE_MACHINE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MACHINE=AugLoop1\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "agent_dispatch.identity.resolve_identity",
        lambda: pytest.fail("identity subprocess fallback should not run"),
    )
    assert remote_dispatch.local_machine() == "augloop1"


def test_local_machine_falls_back_to_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISE_MACHINE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(
        "agent_dispatch.identity.resolve_identity", lambda: ("anomalous-potato", "wt-1")
    )
    assert remote_dispatch.local_machine() == "anomalous-potato"


def test_local_machine_falls_back_to_host_node_name(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISE_MACHINE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr("agent_dispatch.identity.resolve_identity", lambda: (None, None))
    monkeypatch.setattr("platform.node", lambda: "Anomalous-Potato")
    assert remote_dispatch.local_machine() == "anomalous-potato"


def test_local_machine_none_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISE_MACHINE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr("agent_dispatch.identity.resolve_identity", lambda: (None, None))
    monkeypatch.setattr("platform.node", lambda: "")
    assert remote_dispatch.local_machine() is None