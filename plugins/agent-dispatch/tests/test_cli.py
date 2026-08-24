"""Light tests for the agent-dispatch CLI argument layer."""

from __future__ import annotations

import pytest

from agent_dispatch.__main__ import (
    _parse_affinity,
    _resolve_bind_host_resilient,
    _resolve_client_target,
    build_parser,
)


@pytest.fixture(autouse=True)
def _isolate_discovery(monkeypatch, tmp_path):
    """Point endpoint + routing discovery at empty tmp dirs so target resolution is
    deterministic and never reads a live coordinator's rendezvous / routing table."""
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.delenv("AGENT_DISPATCH_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_FAILOVER_MACHINE", raising=False)


# -- SSH-transport failover routing (_should_ssh_failover + _client) ---------


def test_should_ssh_failover_none_when_unset(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.delenv("AGENT_DISPATCH_FAILOVER_MACHINE", raising=False)
    assert m._should_ssh_failover(_args(["list"])) is None


def test_should_ssh_failover_none_on_explicit_url_or_shared(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.setenv("AGENT_DISPATCH_FAILOVER_MACHINE", "peer-host")
    monkeypatch.setattr("agent_dispatch.remote_dispatch.is_peer_machine", lambda _m: True)
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: False)
    assert m._should_ssh_failover(_args(["--url", "http://x:9847", "list"])) is None
    assert m._should_ssh_failover(_args(["--shared", "list"])) is None


def test_should_ssh_failover_none_when_local_live(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.setenv("AGENT_DISPATCH_FAILOVER_MACHINE", "peer-host")
    monkeypatch.setattr("agent_dispatch.remote_dispatch.is_peer_machine", lambda _m: True)
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: True)
    assert m._should_ssh_failover(_args(["list"])) is None


def test_should_ssh_failover_none_when_not_a_peer(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.setenv("AGENT_DISPATCH_FAILOVER_MACHINE", "myself")
    monkeypatch.setattr("agent_dispatch.remote_dispatch.is_peer_machine", lambda _m: False)
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: False)
    assert m._should_ssh_failover(_args(["list"])) is None


def test_should_ssh_failover_returns_peer_when_local_down(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.setenv("AGENT_DISPATCH_FAILOVER_MACHINE", "peer-host")
    monkeypatch.setattr("agent_dispatch.remote_dispatch.is_peer_machine", lambda _m: True)
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: False)
    assert m._should_ssh_failover(_args(["list"])) == "peer-host"


def test_client_opens_and_owns_ssh_tunnel_on_failover(monkeypatch):
    from agent_dispatch import __main__ as m

    monkeypatch.setattr(m, "_should_ssh_failover", lambda _args: "peer-host")
    monkeypatch.setattr(m, "_ensure_local_coordinator", lambda _args: None)

    class _FakeTunnel:
        base_url = "http://127.0.0.1:59123"
        closed = False

        def close(self):
            type(self).closed = True

    fake = _FakeTunnel()
    monkeypatch.setattr("agent_dispatch.ssh_tunnel.open_coordinator_tunnel", lambda _p: fake)
    client = m._client(_args(["list"]))
    # The client rides the tunnel URL, carries no token, and owns the tunnel.
    assert client._tunnel is fake
    client.close()
    assert _FakeTunnel.closed is True


def test_client_failover_tunnel_unavailable_exits(monkeypatch):
    from agent_dispatch import __main__ as m
    from agent_dispatch import ssh_tunnel

    monkeypatch.setattr(m, "_should_ssh_failover", lambda _args: "peer-host")
    monkeypatch.setattr(m, "_ensure_local_coordinator", lambda _args: None)

    def _boom(_peer):
        raise ssh_tunnel.TunnelUnavailable("ssh down")

    monkeypatch.setattr("agent_dispatch.ssh_tunnel.open_coordinator_tunnel", _boom)
    with pytest.raises(SystemExit):
        m._client(_args(["list"]))


def test_parse_affinity():
    assert _parse_affinity(["agent=w1", "worktree=wt-2"]) == {"agent": "w1", "worktree": "wt-2"}
    assert _parse_affinity(None) == {}


# -- steer wake ownership ----------------------------------------------------


def test_steer_uses_coordinator_wake_result(monkeypatch, capsys):
    import json

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def steer(self, task_id, **kwargs):
            assert task_id == "task-1"
            assert kwargs["wake"] is True
            return {
                "id": task_id,
                "owner": "host/worktree-1",
                "steer_woken": True,
            }

    monkeypatch.setattr("agent_dispatch.__main__._client", lambda _args: FakeClient())
    args = _args(["steer", "submit", "task-1", "--field", "decision=continue"])

    assert args.func(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["woken"] is True
    assert "steer_woken" not in output["task"]


def test_steer_falls_back_when_old_coordinator_omits_wake_result(
    monkeypatch, capsys
):
    import json

    from agent_dispatch import bridge

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def steer(self, task_id, **_kwargs):
            return {"id": task_id, "owner": "host/worktree-1"}

    calls = []
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda _args: FakeClient())
    monkeypatch.setattr(
        bridge,
        "resume_steered_owner",
        lambda owner, task_id, message=None: calls.append(
            (owner, task_id, message)
        ) or True,
    )
    args = _args(["steer", "submit", "task-1", "--field", "decision=continue"])

    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["woken"] is True
    assert calls == [("host/worktree-1", "task-1", None)]


# -- coordinator target resolution: local vs shared/elected -----------------


def _args(argv):
    return build_parser().parse_args(argv)


def test_resolve_target_explicit_url_wins(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://coordinator.example/dispatch")
    args = _args(["--url", "http://direct:9847", "--shared", "list"])
    url, _ = _resolve_client_target(args)
    assert url == "http://direct:9847"  # --url trumps --shared


def test_resolve_target_shared_routes_to_shared_coordinator(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://coordinator.example/dispatch")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "shared-secret")
    args = _args(["--shared", "list"])
    url, token = _resolve_client_target(args)
    assert url == "https://coordinator.example/dispatch"
    assert token == "shared-secret"


def test_resolve_target_shared_unconfigured_errors_loudly(monkeypatch):
    import pytest

    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    args = _args(["--shared", "list"])
    with pytest.raises(SystemExit):
        _resolve_client_target(args)


def test_resolve_target_local_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    args = _args(["list"])
    url, _ = _resolve_client_target(args)
    assert url == "http://127.0.0.1:9847"


def test_resolve_target_falls_back_to_shared_when_local_down(monkeypatch):
    # Failover: local coordinator not live + a shared coordinator configured ->
    # dispatch transparently onto the shared/hosted (standby) coordinator.
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://coordinator.example/dispatch")
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "shared-secret")
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: False)
    args = _args(["list"])
    url, token = _resolve_client_target(args)
    assert url == "https://coordinator.example/dispatch"
    assert token == "shared-secret"


def test_resolve_target_prefers_local_when_live(monkeypatch):
    # A shared coordinator is configured, but the local one is live -> local wins
    # (no unnecessary cross-machine hop).
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://coordinator.example/dispatch")
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    monkeypatch.setattr("agent_dispatch.__main__.has_live_local_coordinator", lambda: True)
    args = _args(["list"])
    url, _ = _resolve_client_target(args)
    assert url == "http://127.0.0.1:9847"


# -- supervise override (operator kill-switch) -------------------------------


def test_override_parser_shapes_namespace():
    args = _args(["supervise", "override", "disable", "declared:general:general",
                  "--reason", "runaway"])
    assert args.supervise_command == "override"
    assert args.override_command == "disable"
    assert args.id == "declared:general:general"
    assert args.reason == "runaway"


def test_override_disable_enable_roundtrip_via_cli(monkeypatch, tmp_path, capsys):
    import json

    from agent_dispatch import overrides as ov

    ovpath = tmp_path / "overrides.json"
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(ovpath))

    # disable
    args = _args(["supervise", "override", "disable", "u1", "--reason", "boom"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "u1" and out["overridden_off"] is True and out["reason"] == "boom"
    assert ov.overridden_off_ids(ov.load_overrides(ovpath)) == {"u1"}

    # list shows it
    args = _args(["supervise", "override", "list"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overridden_off"] == ["u1"]

    # enable clears it
    args = _args(["supervise", "override", "enable", "u1"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overridden_off"] is False and out["cleared"] is True
    assert ov.load_overrides(ovpath) == {}


def test_parser_create_flags():
    args = build_parser().parse_args(
        ["create", "do it", "--require", "logger", "--affinity", "agent=w1", "--proposed"]
    )
    assert args.command == "create"
    assert args.title == "do it"
    assert args.require == ["logger"]
    assert args.affinity == ["agent=w1"]
    assert args.proposed is True


def test_parser_create_goal_flags():
    args = build_parser().parse_args(
        ["create", "pursue", "--goal", "reach X", "--done-criteria", "X is met"]
    )
    assert args.goal == "reach X"
    assert args.done_criteria == "X is met"


def test_parser_create_goal_flags_default_none():
    args = build_parser().parse_args(["create", "plain"])
    assert args.goal is None
    assert args.done_criteria is None


def test_parser_claim_flags():
    # The bare positional is now the TASK id (consistent with start/complete/yield);
    # the owner/worker id moved to the explicit --worker/--as flag.
    args = build_parser().parse_args(
        ["claim", "t1", "--capability", "review", "--lease-seconds", "60"]
    )
    assert args.task_id == "t1"
    assert args.worker_id is None
    assert args.capability == ["review"]
    assert args.lease_seconds == 60


def test_parser_claim_worker_flag():
    # Explicit owner override via --worker (and its --as alias).
    a = build_parser().parse_args(["claim", "--worker", "m/wt"])
    assert a.worker_id == "m/wt" and a.task_id is None
    b = build_parser().parse_args(["claim", "t1", "--as", "m/wt"])
    assert b.worker_id == "m/wt" and b.task_id == "t1"


def test_parser_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_cmd_serve_reroots_cwd_to_runtime_dir(monkeypatch, tmp_path):
    import argparse
    from pathlib import Path

    from agent_dispatch import __main__, runtime_version, server

    start = tmp_path / "payload"
    runtime = tmp_path / "runtime"
    start.mkdir()
    monkeypatch.chdir(start)
    monkeypatch.setattr(runtime_version, "install_dir", lambda: runtime)

    seen = {}

    def fake_serve(cfg, *, passive=False):
        seen["cwd"] = Path.cwd()
        seen["cfg"] = cfg
        seen["passive"] = passive

    monkeypatch.setattr(server, "serve", fake_serve)
    args = argparse.Namespace(
        host="127.0.0.1", port=None, db=None, token=None, passive=False
    )

    assert __main__._cmd_serve(args) == 0
    assert seen["cwd"] == runtime
    assert seen["cwd"] != start
    assert runtime.is_dir()


def test_cmd_serve_runtime_dir_resolution_failure_is_nonfatal(
    monkeypatch, tmp_path, capsys
):
    import argparse
    from pathlib import Path

    from agent_dispatch import __main__, runtime_version, server

    start = tmp_path / "payload"
    fallback = tmp_path / "home"
    start.mkdir()
    monkeypatch.chdir(start)
    monkeypatch.setattr(
        runtime_version,
        "install_dir",
        lambda: (_ for _ in ()).throw(OSError("boom")),
    )
    monkeypatch.setattr(__main__.Path, "home", staticmethod(lambda: fallback))

    seen = {}

    def fake_serve(cfg, *, passive=False):
        seen["cwd"] = Path.cwd()

    monkeypatch.setattr(server, "serve", fake_serve)
    args = argparse.Namespace(
        host="127.0.0.1", port=None, db=None, token=None, passive=False
    )

    assert __main__._cmd_serve(args) == 0
    assert seen["cwd"] == fallback
    assert "could not resolve runtime cwd" in capsys.readouterr().err


def test_parser_dashdash_tail_captured_for_drive_and_run():
    """`recipes drive` (leading positional) and `run` capture a verbatim
    `-- <command>` tail via `_dashdash_tail`, robustly across CPython versions
    (argparse's raw `--` + nargs='*' handling raised on 3.11 but not 3.12, #383).
    """
    d = build_parser().parse_args([
        "recipes", "drive", "reviewer", "--signal", "work-done",
        "--resume", "m/wt-1", "--execute", "--", "agent-worktrees", "pr-watch", "42",
    ])
    # Options before the `--` still parse as options, not swallowed into the tail.
    assert d.name == "reviewer" and d.signal == "work-done"
    assert d.resume == "m/wt-1" and d.execute is True
    assert d._dashdash_tail == ["agent-worktrees", "pr-watch", "42"]

    r = build_parser().parse_args(["run", "--resume", "m/wt", "--", "sleep", "5"])
    assert r.resume == "m/wt"
    assert r._dashdash_tail == ["sleep", "5"]


def test_parser_dashdash_left_intact_for_other_subcommands():
    """The `--` interception is scoped to run / recipes-drive (resolved by the
    parsed subcommand, not token membership); other subcommands keep argparse's
    native `--` "end of options" escape hatch."""
    a = build_parser().parse_args(["create", "--", "-weird-title"])
    assert not hasattr(a, "_dashdash_tail")
    assert a.title == "-weird-title"


def test_parser_dashdash_scoping_ignores_positional_named_run():
    """A positional VALUE equal to 'run' (here create's title) must NOT be
    mistaken for the `run` subcommand and trigger `--` interception (#383 review).
    """
    import pytest

    # Without '--': parses fine, title == "run", no tail captured.
    a = build_parser().parse_args(["create", "run"])
    assert a.title == "run" and not hasattr(a, "_dashdash_tail")

    # With '--': the tail is NOT swallowed into _dashdash_tail; argparse handles
    # it natively (rejects the stray positional) instead of silently dropping it.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["create", "run", "--", "-weird"])


def test_parser_create_spawn_flags():
    args = build_parser().parse_args(
        ["create", "x", "--spawn", "--spawn-agent", "w", "--async"]
    )
    assert args.spawn is True
    assert args.spawn_agent == "w"
    assert args.run_async is True


def test_parser_claim_task_flag():
    # `--task` remains a back-compat alias for the positional task id.
    args = build_parser().parse_args(["claim", "--task", "t9"])
    assert args.task == "t9" and args.task_id is None


def test_parser_consume_flags():
    args = build_parser().parse_args(
        ["consume", "t9", "--worktree", "wt-1", "--result-ref", "consumed:wt-1"]
    )
    assert args.command == "consume"
    assert args.task_id == "t9"
    assert args.worktree == "wt-1"
    assert args.result_ref == "consumed:wt-1"


def test_spawn_helper_degrades_gracefully(monkeypatch, capsys):
    import argparse

    from agent_dispatch import __main__, bridge

    def boom(*_a, **_k):
        raise bridge.BridgeUnavailable("no bridge")

    monkeypatch.setattr(bridge, "spawn_worker", boom)
    args = argparse.Namespace(spawn_agent="task-worker", run_async=False, url=None)
    __main__._do_spawn(args, {"id": "t1"})
    err = capsys.readouterr().err
    assert "--spawn skipped" in err
    assert "t1" in err


def test_parser_worktree_status():
    args = build_parser().parse_args(["worktree-status"])
    assert args.command == "worktree-status"


def test_parser_claimant():
    args = build_parser().parse_args(["claimant", "task-123"])
    assert args.command == "claimant"
    assert args.task_id == "task-123"


def test_split_owner():
    from agent_dispatch.__main__ import _split_owner

    assert _split_owner("anomalous-potato/wt-abc") == ("anomalous-potato", "wt-abc")
    assert _split_owner(None) == (None, None)
    assert _split_owner("") == (None, None)
    # No slash -> treat the whole value as the machine, worktree unknown.
    assert _split_owner("bare") == ("bare", None)
    # A worktree id may itself contain slashes; only the first split is the
    # machine boundary.
    assert _split_owner("m/a/b") == ("m", "a/b")


def test_claimant_reports_owner_when_claimed(monkeypatch):
    import argparse
    import contextlib
    import io
    import json

    from agent_dispatch import __main__

    class _FakeClient:
        def get(self, task_id):
            return {
                "id": task_id, "status": "started",
                "owner": "anomalous-potato/wt-abc",
                "owner_session_id": "sess-9", "repo": "r",
                "target_worktree": "wt-pin", "target_machine": "m2",
            }

    @contextlib.contextmanager
    def _fake_client(args, **kw):
        yield _FakeClient()

    monkeypatch.setattr(__main__, "_client", _fake_client)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = __main__._cmd_claimant(argparse.Namespace(task_id="t1"))
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["claimed"] is True
    assert out["machine"] == "anomalous-potato" and out["worktree"] == "wt-abc"
    assert out["worker_id"] == "anomalous-potato/wt-abc"
    assert out["resolved_from"] == "owner"
    assert out["owner_session_id"] == "sess-9"


def test_claimant_falls_back_to_target_when_unclaimed(monkeypatch):
    import argparse
    import contextlib
    import io
    import json

    from agent_dispatch import __main__

    class _FakeClient:
        def get(self, task_id):
            return {
                "id": task_id, "status": "queued", "owner": None,
                "target_worktree": "wt-pin", "target_machine": "m2", "repo": "r",
            }

    @contextlib.contextmanager
    def _fake_client(args, **kw):
        yield _FakeClient()

    monkeypatch.setattr(__main__, "_client", _fake_client)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = __main__._cmd_claimant(argparse.Namespace(task_id="t2"))
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["claimed"] is False
    assert out["machine"] == "m2" and out["worktree"] == "wt-pin"
    assert out["resolved_from"] == "target"
    assert out["worker_id"] is None


def test_identity_flags_take_precedence(monkeypatch):
    import argparse

    from agent_dispatch import __main__, identity

    # If both flags are present, no resolution subprocess is attempted.
    def boom():
        raise AssertionError("resolve_identity should not be called when both flags given")

    monkeypatch.setattr(identity, "resolve_identity", boom)
    args = argparse.Namespace(machine="m1", worktree="w1")
    assert __main__._identity(args) == ("m1", "w1")


def test_identity_falls_back_to_resolution(monkeypatch):
    import argparse

    from agent_dispatch import __main__, identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: ("host-a", "wt-7"))
    args = argparse.Namespace(machine=None, worktree=None)
    assert __main__._identity(args) == ("host-a", "wt-7")


def test_parser_inbox_defaults():
    args = build_parser().parse_args(["inbox"])
    assert args.command == "inbox"
    assert args.status == "proposed"
    assert args.machine is None
    assert args.limit == 200


def test_parser_inbox_flags():
    args = build_parser().parse_args(
        ["inbox", "--machine", "host-a", "--status", "proposed,queued", "--limit", "5"]
    )
    assert args.machine == "host-a"
    assert args.status == "proposed,queued"
    assert args.limit == 5


def test_parser_inbox_awaiting_steer_flag():
    args = build_parser().parse_args(["inbox", "--awaiting-steer"])
    assert args.awaiting_steer is True
    # Default off.
    assert build_parser().parse_args(["inbox"]).awaiting_steer is False


class _FakeClient:
    """A stand-in DispatchClient capturing the params passed to ``list``."""

    def __init__(self, tasks):
        self._tasks = tasks
        self.calls: list[dict] = []

    def list(self, **params):
        self.calls.append(params)
        return list(self._tasks)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


def test_inbox_scopes_cross_lane_to_this_machine(monkeypatch, capsys):
    import json

    from agent_dispatch import __main__, identity

    tasks = [
        {"id": "t1", "target_machine": "host-a", "status": "proposed"},
        {"id": "t2", "target_machine": None, "status": "proposed"},
        {"id": "t3", "target_machine": "host-b", "status": "proposed"},
    ]
    fake = _FakeClient(tasks)
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("host-a", "wt-1"))

    args = build_parser().parse_args(["inbox"])
    rc = args.func(args)
    assert rc == 0

    # Cross-lane query: repo is None (all lanes), status defaulted to proposed.
    assert fake.calls == [{"repo": None, "status": "proposed", "label": None, "limit": 200}]

    emitted = json.loads(capsys.readouterr().out)
    ids = {t["id"] for t in emitted}
    # host-a match + machine-agnostic kept; host-b dropped.
    assert ids == {"t1", "t2"}


def test_inbox_requires_a_machine(monkeypatch, capsys):
    from agent_dispatch import __main__, identity

    monkeypatch.setattr(__main__, "_client", lambda args: _FakeClient([]))
    monkeypatch.setattr(identity, "resolve_identity", lambda: (None, None))

    args = build_parser().parse_args(["inbox"])
    assert args.func(args) == 2
    assert "could not resolve this machine" in capsys.readouterr().err


def test_inbox_awaiting_steer_surfaces_proposed_plus_awaiting(monkeypatch, capsys):
    import json

    from agent_dispatch import __main__, identity

    tasks = [
        {"id": "p1", "target_machine": "host-a", "status": "proposed",
         "awaiting_steer": False},
        {"id": "c1", "target_machine": "host-a", "status": "claimed",
         "awaiting_steer": True},   # blocked on steering -> kept
        {"id": "s1", "target_machine": None, "status": "started",
         "awaiting_steer": False},  # owned, not awaiting -> dropped
        {"id": "c2", "target_machine": "host-b", "status": "claimed",
         "awaiting_steer": True},   # awaiting but other machine -> dropped
    ]
    fake = _FakeClient(tasks)
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("host-a", "wt-1"))

    args = build_parser().parse_args(["inbox", "--awaiting-steer"])
    assert args.func(args) == 0

    # The fetch widens to the owned states (a card-blocked task is claimed/
    # started, not a filterable "held").
    assert fake.calls == [
        {"repo": None, "status": "proposed,claimed,started", "label": None, "limit": 200}
    ]
    emitted = json.loads(capsys.readouterr().out)
    ids = {t["id"] for t in emitted}
    # Pickable proposed (p1) + awaiting-steer on this machine (c1); the owned-but-
    # not-awaiting started task (s1) and the other machine's awaiting task (c2)
    # are dropped.
    assert ids == {"p1", "c1"}


# -- Deferred-completion pickup (takeover) + complete owner auto-resolution ---


def test_parser_complete_owner_optional():
    # Both `complete <id>` and `complete <id> <owner>` parse.
    a = build_parser().parse_args(["complete", "t1"])
    assert a.task_id == "t1" and a.worker_id is None
    b = build_parser().parse_args(["complete", "t1", "m/wt", "--result-ref", "pr/9"])
    assert b.worker_id == "m/wt" and b.result_ref == "pr/9"


def test_parser_start_owner_optional():
    # Both `start <id>` and `start <id> <owner>` parse (worktree-identity symmetry).
    a = build_parser().parse_args(["start", "t1"])
    assert a.task_id == "t1" and a.worker_id is None
    b = build_parser().parse_args(["start", "t1", "m/wt"])
    assert b.worker_id == "m/wt"


def test_parser_yield_owner_optional():
    a = build_parser().parse_args(["yield", "t1", "--note", "blocked"])
    assert a.task_id == "t1" and a.worker_id is None and a.note == "blocked"
    b = build_parser().parse_args(["yield", "t1", "m/wt"])
    assert b.worker_id == "m/wt"


def test_parser_yield_exclude_self_and_deprecated_alias():
    """`--exclude-self` is the clear name; `--not-me` stays a back-compat alias."""
    a = build_parser().parse_args(["yield", "t1", "--exclude-self", "worktree"])
    assert a.exclude_self == "worktree"
    b = build_parser().parse_args(["yield", "t1", "--not-me", "machine"])
    assert b.exclude_self == "machine"


def test_yield_exclude_self_appends_scoped_exclusion(monkeypatch):
    """`yield --exclude-self worktree` translates to a worktree-scoped exclusion."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def yield_task(self, task_id, worker_id, *, note=None, exclude=None):
            seen.update(task_id=task_id, worker_id=worker_id, note=note, exclude=exclude)
            return {"id": task_id, "status": "queued"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))

    args = build_parser().parse_args(["yield", "t1", "--exclude-self", "worktree"])
    args.func(args)
    assert seen["exclude"] == "worktree:wt-7"


def test_abandon_duplicate_of_implies_permit_and_records_ref(monkeypatch):
    """`abandon --duplicate-of REF` self-permits and folds the dedup ref into the reason."""
    from agent_dispatch import __main__

    seen = {}

    class _C:
        def abandon(self, task_id, *, worker_id=None, permitted=False, reason=None):
            seen.update(task_id=task_id, worker_id=worker_id, permitted=permitted, reason=reason)
            return {"id": task_id, "status": "abandoned"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())

    args = build_parser().parse_args(["abandon", "t1", "--duplicate-of", "pr/42"])
    args.func(args)
    assert seen["permitted"] is True
    assert "duplicate of pr/42" in seen["reason"]


def test_parser_progress_owner_optional_and_fields():
    a = build_parser().parse_args(
        ["progress", "t1", "--phase", "impl", "--summary", "did the thing"]
    )
    assert a.task_id == "t1" and a.worker_id is None
    assert a.phase == "impl" and a.summary == "did the thing"
    b = build_parser().parse_args(
        ["progress", "t1", "m/wt", "--summary", "s", "--pr", "pr/9", "--blocker", "b"]
    )
    assert b.worker_id == "m/wt" and b.pr == "pr/9" and b.blocker == "b"


def test_progress_resolves_owner_from_identity(monkeypatch):
    """`progress <id>` (no owner) resolves owner = machine/worktree from CWD."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def progress(self, task_id, worker_id, *, phase="", summary, blocker=None, pr=None):
            seen.update(
                task_id=task_id, worker_id=worker_id, phase=phase,
                summary=summary, blocker=blocker, pr=pr,
            )
            return {"id": task_id, "status": "started", "owner": worker_id}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))

    args = build_parser().parse_args(
        ["progress", "T5", "--phase", "impl", "--summary", "wired it", "--pr", "pr/1"]
    )
    assert args.func(args) == 0
    assert seen == {
        "task_id": "T5", "worker_id": "anomalous-potato/wt-7", "phase": "impl",
        "summary": "wired it", "blocker": None, "pr": "pr/1",
    }


def test_start_resolves_owner_from_identity(monkeypatch):
    """`start <id>` (no owner) resolves owner = machine/worktree from CWD."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def start(self, task_id, owner):
            seen["task_id"] = task_id
            seen["owner"] = owner
            return {"id": task_id, "status": "started", "owner": owner}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))

    args = build_parser().parse_args(["start", "T5"])
    assert args.func(args) == 0
    assert seen == {"task_id": "T5", "owner": "anomalous-potato/wt-7"}


def test_yield_resolves_owner_from_identity(monkeypatch):
    """`yield <id>` (no owner) resolves owner = machine/worktree from CWD."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def yield_task(self, task_id, owner, *, note=None, exclude=None):
            seen["task_id"] = task_id
            seen["owner"] = owner
            seen["note"] = note
            seen["exclude"] = exclude
            return {"id": task_id, "status": "queued", "owner": owner}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))

    args = build_parser().parse_args(["yield", "T5", "--note", "blocked"])
    assert args.func(args) == 0
    assert seen == {
        "task_id": "T5", "owner": "anomalous-potato/wt-7", "note": "blocked", "exclude": None,
    }


def test_start_without_identity_errors(monkeypatch, capsys):
    """`start <id>` with no owner and no resolvable identity fails cleanly."""
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: (None, None))
    args = build_parser().parse_args(["start", "T5"])
    assert args.func(args) == 2
    assert "could not resolve the owner for start" in capsys.readouterr().err


def test_parser_consume_defer_complete_flag():
    a = build_parser().parse_args(["consume", "t9"])
    assert a.defer_complete is False
    b = build_parser().parse_args(["consume", "t9", "--defer-complete"])
    assert b.defer_complete is True


# -- serve bind-host resolution (coordinator inversion) ---------------------


def _serve_args(**kw):
    import argparse

    base = dict(host=None, port=None, db=None, token=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_serve_host_explicit_flag_wins(monkeypatch):
    from agent_dispatch import __main__
    from agent_dispatch.config import load_config

    monkeypatch.setattr(__main__.sys, "platform", "win32")
    host = __main__._resolve_serve_host(_serve_args(host="0.0.0.0"), load_config())  # noqa: S104
    assert host == "0.0.0.0"  # noqa: S104 -- operator explicitly asked for it


def test_resolve_serve_host_env_override(monkeypatch):
    from agent_dispatch import __main__, config

    monkeypatch.setattr(__main__.sys, "platform", "win32")
    monkeypatch.setenv("AGENT_DISPATCH_HOST", "172.19.240.1")
    base = config.load_config()  # picks up the env host
    assert __main__._resolve_serve_host(_serve_args(), base) == "172.19.240.1"


def test_resolve_serve_host_windows_resolves_bind(monkeypatch):
    from agent_dispatch import __main__, config

    monkeypatch.delenv("AGENT_DISPATCH_HOST", raising=False)
    monkeypatch.setattr(__main__.sys, "platform", "win32")
    monkeypatch.setattr("agent_dispatch.netinfo.resolve_bind_host", lambda: "172.19.240.9")
    assert __main__._resolve_serve_host(_serve_args(), config.load_config()) == "172.19.240.9"


def test_resolve_serve_host_linux_uses_default(monkeypatch):
    from agent_dispatch import __main__, config

    monkeypatch.delenv("AGENT_DISPATCH_HOST", raising=False)
    monkeypatch.setattr(__main__.sys, "platform", "linux")
    base = config.load_config()
    assert __main__._resolve_serve_host(_serve_args(), base) == base.host


def test_complete_resolves_owner_from_identity(monkeypatch, capsys):
    """`complete <id>` (no owner) resolves owner = machine/worktree from CWD."""
    from agent_dispatch import __main__, identity

    completed = {}

    class _C:
        def complete(self, task_id, worker_id, *, result_ref=None):
            completed["task_id"] = task_id
            completed["worker_id"] = worker_id
            return {"id": task_id, "status": "completed", "owner": worker_id}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))

    args = build_parser().parse_args(["complete", "T5"])
    assert args.func(args) == 0
    assert completed == {"task_id": "T5", "worker_id": "anomalous-potato/wt-7"}


def test_claim_positional_is_the_task(monkeypatch, capsys):
    """`claim <id>` targets THAT task (positional == task id), not the worker."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def claim(self, **kw):
            seen.update(kw)
            return {"id": kw.get("task_id"), "owner": "m/wt", "status": "claimed"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))

    args = build_parser().parse_args(["claim", "abc123"])
    assert args.func(args) == 0
    assert seen["task_id"] == "abc123"
    assert seen["worker_id"] is None  # owner resolves from CWD, not the positional


def test_claim_conflicting_task_ids_errors(monkeypatch, capsys):
    """A positional task id that disagrees with --task is refused (exit 2)."""
    from agent_dispatch import __main__

    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")
    args = build_parser().parse_args(["claim", "aaa", "--task", "bbb"])
    assert args.func(args) == 2
    assert "conflicting task ids" in capsys.readouterr().err


class _PickupClient:
    """A fake client tracking the consume lifecycle transitions."""

    def __init__(self, status="queued"):
        self.status = status
        self.transitions: list[str] = []

    def get(self, task_id):
        return {"id": task_id, "status": self.status, "owner": None}

    def approve(self, task_id):
        self.transitions.append("approve")
        return {"id": task_id, "status": "queued"}

    def claim(self, **kw):
        self.transitions.append("claim")
        return {"id": kw.get("task_id"), "owner": "m/wt", "status": "claimed"}

    def start(self, task_id, owner):
        self.transitions.append("start")
        return {"id": task_id, "status": "started", "owner": owner}

    def complete(self, task_id, owner, *, result_ref=None):
        self.transitions.append("complete")
        return {"id": task_id, "status": "completed", "owner": owner}

    def payload(self, task_id):
        return {"payload": "the brief"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_consume_baton_completes_on_pickup(monkeypatch, capsys):
    from agent_dispatch import __main__, identity

    fake = _PickupClient("proposed")
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")

    args = build_parser().parse_args(["consume", "T1"])
    assert args.func(args) == 0
    # Baton mode drives all the way to completed.
    assert fake.transitions == ["approve", "claim", "start", "complete"]
    assert "the brief" in capsys.readouterr().out


def test_consume_defer_complete_stops_at_started(monkeypatch, capsys):
    from agent_dispatch import __main__, identity

    fake = _PickupClient("proposed")
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")

    args = build_parser().parse_args(["consume", "T1", "--defer-complete"])
    assert args.func(args) == 0
    # Deferred: take ownership + start, but NEVER complete -- the successor does.
    assert fake.transitions == ["approve", "claim", "start"]
    assert "complete" not in fake.transitions
    assert "the brief" in capsys.readouterr().out


class _SpentHandoffClient:
    """A fake client whose task is an already-completed handoff baton."""

    def __init__(self, *, labels=None, source=None, status="completed"):
        self._task = {
            "id": "T1",
            "status": status,
            "owner": None,
            "labels": labels if labels is not None else ["handoff"],
            "source": source,
            "result_ref": "resumed:wt-9",
        }
        self.transitions: list[str] = []

    def get(self, task_id):
        return dict(self._task, id=task_id)

    def approve(self, task_id):
        self.transitions.append("approve")
        return self._task

    def claim(self, **kw):
        self.transitions.append("claim")
        return {"owner": "m/wt"}

    def start(self, task_id, owner):
        self.transitions.append("start")

    def complete(self, task_id, owner, *, result_ref=None):
        self.transitions.append("complete")

    def payload(self, task_id):
        self.transitions.append("payload")
        return {"payload": "PAYLOAD-XYZZY"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_consume_completed_handoff_is_not_replayed(monkeypatch, capsys):
    """A spent (completed) handoff baton is refused with exit 3, never replayed --
    so a re-seeded live-cutover successor does not redo finished work."""
    from agent_dispatch import __main__, identity

    fake = _SpentHandoffClient(labels=["handoff"])
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")

    args = build_parser().parse_args(["consume", "T1"])
    assert args.func(args) == 3
    out = capsys.readouterr().out
    # STOP notice replaces the brief; no lifecycle transitions or payload read.
    assert "already COMPLETED" in out
    assert "PAYLOAD-XYZZY" not in out
    assert fake.transitions == []


def test_consume_completed_handoff_by_source_is_not_replayed(monkeypatch, capsys):
    """The handoff is recognized by source=context-handoff too (no label)."""
    from agent_dispatch import __main__, identity

    fake = _SpentHandoffClient(labels=[], source="context-handoff")
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")

    args = build_parser().parse_args(["consume", "T1", "--defer-complete"])
    assert args.func(args) == 3
    assert "already COMPLETED" in capsys.readouterr().out
    assert fake.transitions == []


def test_consume_completed_non_handoff_still_prints_payload(monkeypatch, capsys):
    """The debounce is scoped to handoffs: a completed *non-handoff* task
    consumed again still just prints its payload (unchanged behavior)."""
    from agent_dispatch import __main__, identity

    fake = _SpentHandoffClient(labels=[], source=None)
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt"))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "repo")

    args = build_parser().parse_args(["consume", "T1"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "PAYLOAD-XYZZY" in out
    # Terminal task: no re-claim transitions, just the payload read.
    assert fake.transitions == ["payload"]
    a = build_parser().parse_args(["focus", "working on X"])
    assert a.focus_text == "working on X" and a.list is False
    b = build_parser().parse_args(["focus", "--list", "--machine", "emancipation-cube"])
    assert b.list is True and b.machine == "emancipation-cube" and b.focus_text is None


def test_focus_writes_through_status_core(monkeypatch):
    # Convergence: `focus <text>` forwards to the worktree record via
    # aw_set_summary (the `agent-worktrees status` verb), not a parallel store.
    from agent_dispatch import identity

    seen = {}

    def _set_summary(summary):
        seen["summary"] = summary
        return True

    monkeypatch.setattr(identity, "aw_set_summary", _set_summary)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))
    args = build_parser().parse_args(["focus", "driving Phase 8"])
    assert args.func(args) == 0
    assert seen["summary"] == "driving Phase 8"


def test_focus_list_derives_from_records(monkeypatch, capsys):
    # `focus --list` derives from `agent-worktrees list --json`; a record with
    # no summary contributes no focus line.
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "aw_list_records", lambda machine=None: [
        {"machine": "anomalous-potato", "id": "wt-7", "summary": "Phase 8",
         "status_note_at": "2026-07-15T10:00:00"},
        {"machine": "anomalous-potato", "id": "wt-8", "summary": ""},
    ])
    args = build_parser().parse_args(["focus", "--list"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "wt-7" in out and "Phase 8" in out
    assert "wt-8" not in out


def test_focus_write_through_failure_errors(monkeypatch, capsys):
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: ("anomalous-potato", "wt-7"))
    monkeypatch.setattr(identity, "aw_set_summary", lambda _s: False)
    args = build_parser().parse_args(["focus", "x"])
    assert args.func(args) == 2
    assert "write-through failed" in capsys.readouterr().err


def test_focus_without_identity_errors(monkeypatch, capsys):
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: (None, None))
    args = build_parser().parse_args(["focus", "x"])
    assert args.func(args) == 2
    assert "could not resolve this worktree's identity" in capsys.readouterr().err


# -- Peer-queue browse (Phase 8 Slice 8c) ------------------------------------


def test_parser_list_machine_flag():
    args = build_parser().parse_args(["list", "--machine", "emancipation-cube"])
    assert args.command == "list"
    assert args.machine == "emancipation-cube"


def test_list_peer_browse_delegates_over_ssh(monkeypatch, capsys):
    import types

    from agent_dispatch import __main__, remote_dispatch

    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "gitea/lane")
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")

    captured = {}

    def fake_browse(machine, argv, **kw):
        captured["machine"] = machine
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout='[{"id": "t-remote"}]\n', stderr="")

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)
    # The local coordinator client must NOT be used for a peer browse.
    monkeypatch.setattr(
        __main__, "_client",
        lambda args: (_ for _ in ()).throw(AssertionError("local client used for peer browse")),
    )

    args = build_parser().parse_args(["list", "--machine", "emancipation-cube", "--status", "started"])
    rc = args.func(args)
    assert rc == 0
    assert captured["machine"] == "emancipation-cube"
    assert captured["argv"][:2] == ["agent-dispatch", "list"]
    assert "--repo" in captured["argv"]  # locally-resolved lane forwarded
    assert "--machine" not in captured["argv"]  # list drops it (old-peer compatible)
    assert "t-remote" in capsys.readouterr().out


def test_inbox_peer_browse_delegates_over_ssh(monkeypatch, capsys):
    import types

    from agent_dispatch import __main__, remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")

    captured = {}

    def fake_browse(machine, argv, **kw):
        captured["machine"] = machine
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)
    monkeypatch.setattr(
        __main__, "_client",
        lambda args: (_ for _ in ()).throw(AssertionError("local client used for peer browse")),
    )

    args = build_parser().parse_args(["inbox", "--machine", "emancipation-cube"])
    rc = args.func(args)
    assert rc == 0
    assert captured["machine"] == "emancipation-cube"
    assert captured["argv"][:2] == ["agent-dispatch", "inbox"]
    assert captured["argv"][captured["argv"].index("--machine") + 1] == "emancipation-cube"


def test_peer_browse_degrades_when_ssh_unavailable(monkeypatch, capsys):
    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")

    def fake_browse(machine, argv, **kw):
        raise remote_dispatch.RemoteDispatchUnavailable("ssh not found on PATH")

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)

    args = build_parser().parse_args(["inbox", "--machine", "emancipation-cube"])
    assert args.func(args) == 2
    assert "unavailable" in capsys.readouterr().err


def test_peer_browse_surfaces_actionable_diagnosis_on_127(monkeypatch, capsys):
    import types

    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "anomalous-potato")

    def fake_browse(machine, argv, **kw):
        return types.SimpleNamespace(
            returncode=127, stdout="", stderr="bash: agent-dispatch: command not found\n"
        )

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)

    args = build_parser().parse_args(["inbox", "--machine", "mantis-counter"])
    rc = args.func(args)
    assert rc == 127
    err = capsys.readouterr().err
    assert "mantis-counter" in err
    assert "not installed" in err
    # The raw remote line is not dumped verbatim.
    assert "command not found" not in err


# -- bind-host resolution retry (NAT logon-before-WSL race, #2889) -----------


def test_bind_host_resilient_returns_first_success():
    calls = []

    def resolver():
        calls.append(1)
        return "127.0.0.1"

    slept = []
    got = _resolve_bind_host_resilient(
        resolver, retries=5, delay=0.01, sleep=slept.append, log=lambda m: None
    )
    assert got == "127.0.0.1"
    assert len(calls) == 1          # mirrored resolves immediately
    assert slept == []              # no retry, no sleep


def test_bind_host_resilient_retries_until_adapter_ready():
    # Simulate NAT: the vEthernet(WSL) adapter is not up for the first 2 tries
    # (resolve_bind_host raises), then it comes up and resolves.
    attempts = {"n": 0}

    def resolver():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("vEthernet (WSL) has no IPv4 yet")
        return "172.19.240.1"

    slept = []
    logs = []
    got = _resolve_bind_host_resilient(
        resolver, retries=10, delay=0.01, sleep=slept.append, log=logs.append
    )
    assert got == "172.19.240.1"
    assert attempts["n"] == 3
    assert len(slept) == 2          # slept between the 2 failed attempts
    assert len(logs) == 2           # each failed attempt logged


def test_bind_host_resilient_reraises_after_exhaustion():
    import pytest

    def resolver():
        raise RuntimeError("vEthernet (WSL) never came up")

    slept = []
    with pytest.raises(RuntimeError, match="never came up"):
        _resolve_bind_host_resilient(
            resolver, retries=3, delay=0.01, sleep=slept.append, log=lambda m: None
        )
    assert len(slept) == 2          # sleeps between the 3 attempts, not after the last


def test_bind_host_resilient_reads_env_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_BIND_RETRIES", "2")
    monkeypatch.setenv("AGENT_DISPATCH_BIND_RETRY_DELAY", "0")
    attempts = {"n": 0}

    def resolver():
        attempts["n"] += 1
        raise RuntimeError("still down")

    import pytest

    with pytest.raises(RuntimeError):
        _resolve_bind_host_resilient(resolver, sleep=lambda s: None, log=lambda m: None)
    assert attempts["n"] == 2       # honored AGENT_DISPATCH_BIND_RETRIES=2


# -- inbox --board: status-grouped picker board ------------------------------


class TestInboxBoard:
    def _grp(self, **kw):
        from agent_dispatch import __main__ as m
        return m._board_group(kw)

    def test_group_mapping(self):
        assert self._grp(status="proposed") == "Proposed"
        assert self._grp(status="queued") == "Queued"
        assert self._grp(status="claimed") == "Started"
        assert self._grp(status="started") == "Started"
        assert self._grp(status="completed") == "Completed"
        assert self._grp(status="abandoned") == "Abandoned"
        assert self._grp(status="dead_letter") == "Abandoned"

    def test_awaiting_steer_is_blocked(self):
        # A live task blocked on the operator's steer reads as Blocked, whatever
        # its underlying lifecycle state.
        assert self._grp(status="started", awaiting_steer=True) == "Blocked"
        assert self._grp(status="claimed", awaiting_steer=True) == "Blocked"

    def test_terminal_wins_over_stale_awaiting_steer(self):
        # A task abandoned/completed WHILE awaiting-steer keeps a stale flag; it
        # must group as terminal, never Blocked.
        assert self._grp(status="abandoned", awaiting_steer=True) == "Abandoned"
        assert self._grp(status="completed", awaiting_steer=True) == "Completed"

    def test_activity_is_independent_from_lifecycle_phase(self):
        from agent_dispatch import __main__ as m

        active = {
            "status": "started",
            "awaiting_steer": False,
            "embodiment": {"turn_state": "running", "liveness": "active"},
        }
        blocked_but_active = {**active, "awaiting_steer": True}
        idle = {
            "status": "started",
            "embodiment": {"turn_state": "idle", "liveness": "idle"},
        }
        stale_owner = {"status": "started", "last_liveness": "live"}

        assert m._board_group(active) == "Started"
        assert m._board_activity(active) == "ACTIVE"
        assert m._board_group(blocked_but_active) == "Blocked"
        assert m._board_activity(blocked_but_active) == "ACTIVE"
        assert m._board_activity(idle) is None
        assert m._board_activity(stale_owner) is None

    def test_stalled_turn_is_not_reported_active(self):
        from agent_dispatch import __main__ as m

        task = {
            "status": "started",
            "embodiment": {"turn_state": "running", "liveness": "stalled"},
        }
        assert m._board_activity(task) == "STALLED"

    def test_picker_manifest_badges_activity_separately_from_group(self):
        import json
        from pathlib import Path

        manifest = json.loads(
            (Path(__file__).parents[1] / "pivots" / "agent-dispatch.json")
            .read_text(encoding="utf-8")
        )
        assert manifest["entry"]["group"] == "group"
        assert manifest["entry"]["badges"] == ["activity", "labels"]

    def test_sort_orders_by_group_priority(self):
        from agent_dispatch import __main__ as m
        tasks = [
            {"status": "completed", "updated_at": 100},
            {"status": "started", "awaiting_steer": True, "updated_at": 100},
            {"status": "queued", "updated_at": 100},
            {"status": "proposed", "updated_at": 100},
            {"status": "abandoned", "updated_at": 100},
            {"status": "started", "updated_at": 100},
        ]
        tasks.sort(key=m._board_sort_key)
        assert [m._board_group(t) for t in tasks] == [
            "Blocked", "Proposed", "Queued", "Started", "Completed", "Abandoned",
        ]

    def test_sort_within_group_is_recent_first(self):
        from agent_dispatch import __main__ as m
        older = {"status": "started", "updated_at": 100}
        newer = {"status": "started", "updated_at": 200}
        got = sorted([older, newer], key=m._board_sort_key)
        assert got == [newer, older]

    def test_recency_keep_active_always_terminal_windowed(self):
        from agent_dispatch import __main__ as m
        cutoff = 1000.0
        # Active tasks are always kept regardless of age.
        assert m._board_keep({"status": "started", "updated_at": 0}, cutoff) is True
        assert m._board_keep({"status": "proposed"}, cutoff) is True
        # Terminal tasks: kept only when their terminal time is at/after cutoff.
        assert m._board_keep(
            {"status": "completed", "completed_at": 1500}, cutoff) is True
        assert m._board_keep(
            {"status": "abandoned", "completed_at": 500}, cutoff) is False
        # Missing terminal timestamp -> dropped (can't prove it's recent).
        assert m._board_keep({"status": "completed"}, cutoff) is False

    def test_parser_accepts_board_flags(self):
        args = _args(["inbox", "--machine", "m1", "--board", "--recent-mins", "30"])
        assert args.board is True
        assert args.recent_mins == 30

    def test_board_defaults(self):
        args = _args(["inbox", "--board"])
        assert args.board is True
        assert args.recent_mins == 120     # documented default
