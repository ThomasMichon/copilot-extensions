"""Light tests for the agent-dispatch CLI argument layer."""

from __future__ import annotations

from agent_dispatch.__main__ import _parse_affinity, build_parser


def test_parse_affinity():
    assert _parse_affinity(["agent=w1", "worktree=wt-2"]) == {"agent": "w1", "worktree": "wt-2"}
    assert _parse_affinity(None) == {}


def test_parser_create_flags():
    args = build_parser().parse_args(
        ["create", "do it", "--require", "logger", "--affinity", "agent=w1", "--proposed"]
    )
    assert args.command == "create"
    assert args.title == "do it"
    assert args.require == ["logger"]
    assert args.affinity == ["agent=w1"]
    assert args.proposed is True


def test_parser_claim_flags():
    args = build_parser().parse_args(
        ["claim", "w1", "--capability", "review", "--lease-seconds", "60"]
    )
    assert args.worker_id == "w1"
    assert args.capability == ["review"]
    assert args.lease_seconds == 60


def test_parser_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_create_spawn_flags():
    args = build_parser().parse_args(
        ["create", "x", "--spawn", "--spawn-agent", "w", "--async"]
    )
    assert args.spawn is True
    assert args.spawn_agent == "w"
    assert args.run_async is True


def test_parser_claim_task_flag():
    args = build_parser().parse_args(["claim", "w1", "--task", "t9"])
    assert args.task == "t9"


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
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))

    args = build_parser().parse_args(
        ["progress", "T5", "--phase", "impl", "--summary", "wired it", "--pr", "pr/1"]
    )
    assert args.func(args) == 0
    assert seen == {
        "task_id": "T5", "worker_id": "lambda-core/wt-7", "phase": "impl",
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
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))

    args = build_parser().parse_args(["start", "T5"])
    assert args.func(args) == 0
    assert seen == {"task_id": "T5", "owner": "lambda-core/wt-7"}


def test_yield_resolves_owner_from_identity(monkeypatch):
    """`yield <id>` (no owner) resolves owner = machine/worktree from CWD."""
    from agent_dispatch import __main__, identity

    seen = {}

    class _C:
        def yield_task(self, task_id, owner, *, note=None):
            seen["task_id"] = task_id
            seen["owner"] = owner
            seen["note"] = note
            return {"id": task_id, "status": "queued", "owner": owner}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(__main__, "_client", lambda args: _C())
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))

    args = build_parser().parse_args(["yield", "T5", "--note", "blocked"])
    assert args.func(args) == 0
    assert seen == {"task_id": "T5", "owner": "lambda-core/wt-7", "note": "blocked"}


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
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))

    args = build_parser().parse_args(["complete", "T5"])
    assert args.func(args) == 0
    assert completed == {"task_id": "T5", "worker_id": "lambda-core/wt-7"}


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


def test_parser_focus_positional_and_list():
    a = build_parser().parse_args(["focus", "working on X"])
    assert a.focus_text == "working on X" and a.list is False
    b = build_parser().parse_args(["focus", "--list", "--machine", "borealis"])
    assert b.list is True and b.machine == "borealis" and b.focus_text is None


def test_focus_writes_through_status_core(monkeypatch):
    # Convergence: `focus <text>` forwards to the worktree record via
    # aw_set_summary (the `agent-worktrees status` verb), not a parallel store.
    from agent_dispatch import identity

    seen = {}

    def _set_summary(summary):
        seen["summary"] = summary
        return True

    monkeypatch.setattr(identity, "aw_set_summary", _set_summary)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))
    args = build_parser().parse_args(["focus", "driving Phase 8"])
    assert args.func(args) == 0
    assert seen["summary"] == "driving Phase 8"


def test_focus_list_derives_from_records(monkeypatch, capsys):
    # `focus --list` derives from `agent-worktrees list --json`; a record with
    # no summary contributes no focus line.
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "aw_list_records", lambda machine=None: [
        {"machine": "lambda-core", "id": "wt-7", "summary": "Phase 8",
         "status_note_at": "2026-07-15T10:00:00"},
        {"machine": "lambda-core", "id": "wt-8", "summary": ""},
    ])
    args = build_parser().parse_args(["focus", "--list"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "wt-7" in out and "Phase 8" in out
    assert "wt-8" not in out


def test_focus_write_through_failure_errors(monkeypatch, capsys):
    from agent_dispatch import identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: ("lambda-core", "wt-7"))
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
    args = build_parser().parse_args(["list", "--machine", "borealis"])
    assert args.command == "list"
    assert args.machine == "borealis"


def test_list_peer_browse_delegates_over_ssh(monkeypatch, capsys):
    import types

    from agent_dispatch import __main__, remote_dispatch

    monkeypatch.setattr(__main__, "_scope_repo", lambda args: "gitea/lane")
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")

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

    args = build_parser().parse_args(["list", "--machine", "borealis", "--status", "started"])
    rc = args.func(args)
    assert rc == 0
    assert captured["machine"] == "borealis"
    assert captured["argv"][:2] == ["agent-dispatch", "list"]
    assert "--repo" in captured["argv"]  # locally-resolved lane forwarded
    assert "--machine" not in captured["argv"]  # list drops it (old-peer compatible)
    assert "t-remote" in capsys.readouterr().out


def test_inbox_peer_browse_delegates_over_ssh(monkeypatch, capsys):
    import types

    from agent_dispatch import __main__, remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")

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

    args = build_parser().parse_args(["inbox", "--machine", "borealis"])
    rc = args.func(args)
    assert rc == 0
    assert captured["machine"] == "borealis"
    assert captured["argv"][:2] == ["agent-dispatch", "inbox"]
    assert captured["argv"][captured["argv"].index("--machine") + 1] == "borealis"


def test_peer_browse_degrades_when_ssh_unavailable(monkeypatch, capsys):
    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")

    def fake_browse(machine, argv, **kw):
        raise remote_dispatch.RemoteDispatchUnavailable("ssh not found on PATH")

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)

    args = build_parser().parse_args(["inbox", "--machine", "borealis"])
    assert args.func(args) == 2
    assert "unavailable" in capsys.readouterr().err


def test_peer_browse_surfaces_actionable_diagnosis_on_127(monkeypatch, capsys):
    import types

    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "lambda-core")

    def fake_browse(machine, argv, **kw):
        return types.SimpleNamespace(
            returncode=127, stdout="", stderr="bash: agent-dispatch: command not found\n"
        )

    monkeypatch.setattr(remote_dispatch, "browse_remote", fake_browse)

    args = build_parser().parse_args(["inbox", "--machine", "wheatley"])
    rc = args.func(args)
    assert rc == 127
    err = capsys.readouterr().err
    assert "wheatley" in err
    assert "not installed" in err
    # The raw remote line is not dumped verbatim.
    assert "command not found" not in err
