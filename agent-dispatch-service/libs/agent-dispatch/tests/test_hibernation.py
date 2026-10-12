"""Tests for the hibernate-the-wait substrate and the ``run`` CLI."""

from __future__ import annotations

import json

from agent_dispatch import hibernation
from agent_dispatch.__main__ import _cmd_run, build_parser


def _args(argv):
    return build_parser().parse_args(argv)


# -- resume_message ----------------------------------------------------------


def test_resume_message_default_success():
    spec = hibernation.RunSpec(command=("sleep", "1"), task_id="t-1")
    msg = hibernation.resume_message(spec, 0)
    assert "finished" in msg
    assert "t-1" in msg


def test_resume_message_default_failure_names_code():
    spec = hibernation.RunSpec(command=("false",))
    msg = hibernation.resume_message(spec, 3)
    assert "exited with code 3" in msg


def test_resume_message_explicit_override_wins():
    spec = hibernation.RunSpec(command=("x",), message="wake up")
    assert hibernation.resume_message(spec, 0) == "wake up"


def test_resume_message_gave_up_warning_survives_explicit_message():
    # A custom spec.message is normally written assuming the awaited step
    # DID resolve -- on a gave_up backstop it must never silently replace
    # the warning that nothing actually happened.
    spec = hibernation.RunSpec(command=("x",), message="wake up")
    msg = hibernation.resume_message(spec, 124, gave_up=True)
    assert "never resolved" in msg
    assert "wake up" in msg


def test_resume_message_gave_up_without_explicit_message():
    spec = hibernation.RunSpec(command=("x",), task_id="t-1")
    msg = hibernation.resume_message(spec, 124, gave_up=True)
    assert "never resolved" in msg
    assert "t-1" in msg


# -- run_and_resume ----------------------------------------------------------


def test_run_and_resume_runs_then_resumes():
    resumed = {}

    def runner(cmd):
        assert cmd == ("sleep", "1")
        return 0

    def resumer(worktree, message):
        resumed["worktree"] = worktree
        resumed["message"] = message
        return True

    spec = hibernation.RunSpec(command=("sleep", "1"), resume_worktree="m/wt-1")
    report = hibernation.run_and_resume(spec, runner=runner, resumer=resumer)
    assert report["returncode"] == 0
    assert report["resumed"] is True
    assert resumed["worktree"] == "m/wt-1"


def test_run_and_resume_without_worktree_resumes_nothing():
    def resumer(*a):  # pragma: no cover - must not be called
        raise AssertionError("no resume target -> resumer must not run")

    spec = hibernation.RunSpec(command=("true",))
    report = hibernation.run_and_resume(spec, runner=lambda c: 0, resumer=resumer)
    assert report["resumed"] is None


def test_run_and_resume_failed_resume_is_not_fatal():
    def resumer(*a):
        raise RuntimeError("bridge down")

    spec = hibernation.RunSpec(command=("true",), resume_worktree="m/wt-1")
    report = hibernation.run_and_resume(spec, runner=lambda c: 0, resumer=resumer)
    assert report["resumed"] is False  # swallowed, reported as a failed resume


def test_run_and_resume_carries_nonzero_code_into_message():
    seen = {}
    spec = hibernation.RunSpec(command=("false",), resume_worktree="wt")
    hibernation.run_and_resume(
        spec, runner=lambda c: 2, resumer=lambda w, m: seen.setdefault("m", m) or True
    )
    assert "exited with code 2" in seen["m"]


def test_run_and_resume_reattempts_on_timeout_without_waking(monkeypatch):
    """A `124` (timed out, no real transition) must never wake the resumer --
    it means the wait command itself already polled the full window and found
    nothing. Re-invoke the same wait in place instead (ThomasMichon/
    copilot-extensions#2576's remaining scope; the observed false-wake/
    duplicate-comment symptom was also seen independently in a consuming
    harness repo's own issue tracker)."""
    calls = []

    def runner(cmd):
        calls.append(cmd)
        # Times out twice, then a real transition fires.
        return 124 if len(calls) < 3 else 0

    resumed = {}

    def resumer(worktree, message):
        resumed["count"] = resumed.get("count", 0) + 1
        resumed["message"] = message
        return True

    spec = hibernation.RunSpec(
        command=("agent-worktrees", "pr-watch", "42"), resume_worktree="m/wt-1"
    )
    report = hibernation.run_and_resume(spec, runner=runner, resumer=resumer)

    assert len(calls) == 3  # two timeouts + the transition, all in-process
    assert resumed.get("count", 0) == 1  # the resumer only ever fires once
    assert report["returncode"] == 0
    assert report["reattempts_on_timeout"] == 2


def test_run_and_resume_pure_timeout_still_reports_zero_reattempts():
    spec = hibernation.RunSpec(command=("true",), resume_worktree="wt")
    seen = {}
    report = hibernation.run_and_resume(
        spec, runner=lambda c: 0, resumer=lambda w, m: seen.setdefault("m", m) or True
    )
    assert report["reattempts_on_timeout"] == 0
    assert "finished" in seen["m"]


def test_run_and_resume_only_timeouts_never_wakes_but_still_returns(monkeypatch):
    """With ``max_reattempts=None`` (unbounded re-arming), the loop re-arms on
    every ``124`` indefinitely and only escalates once a non-timeout result
    (a genuine transition, or an error) actually arrives -- here, an eventual
    error (code 3), which wakes the resumer."""
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 124 if len(calls) < 5 else 3

    resumed = {}

    def resumer(worktree, message):
        resumed["message"] = message
        return True

    spec = hibernation.RunSpec(command=("agent-worktrees", "pr-watch", "42"), resume_worktree="wt")
    report = hibernation.run_and_resume(
        spec, runner=runner, resumer=resumer, max_reattempts=None,
    )
    assert report["returncode"] == 3
    assert report["reattempts_on_timeout"] == 4
    assert report["gave_up"] is False
    assert "exited with code 3" in resumed["message"]


def test_run_and_resume_gives_up_after_max_reattempts_and_still_wakes(monkeypatch):
    """Under the default cap, a wait condition that never resolves (every
    attempt times out, with no eventual error to force an escalation) still
    wakes the resumer once ``max_reattempts`` is hit, rather than hibernating
    indefinitely."""
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 124  # never resolves, ever

    resumed = {}

    def resumer(worktree, message):
        resumed["count"] = resumed.get("count", 0) + 1
        resumed["message"] = message
        return True

    spec = hibernation.RunSpec(command=("agent-worktrees", "pr-watch", "42"), resume_worktree="wt")
    report = hibernation.run_and_resume(spec, runner=runner, resumer=resumer)

    assert len(calls) == hibernation.MAX_TIMEOUT_REATTEMPTS + 1
    assert report["reattempts_on_timeout"] == hibernation.MAX_TIMEOUT_REATTEMPTS
    assert report["returncode"] == 124
    assert report["gave_up"] is True
    assert resumed.get("count") == 1
    assert "giving up" in resumed["message"]


def test_run_and_resume_max_reattempts_zero_gives_up_immediately():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 124

    spec = hibernation.RunSpec(command=("c",), resume_worktree="wt")
    report = hibernation.run_and_resume(
        spec, runner=runner, resumer=lambda w, m: True, max_reattempts=0,
    )
    assert len(calls) == 1
    assert report["gave_up"] is True
    assert report["reattempts_on_timeout"] == 0


# -- detached_run_argv -------------------------------------------------------


def test_detached_run_argv_round_trips_flags_and_command():
    spec = hibernation.RunSpec(
        command=("agent-worktrees", "pr-watch", "42"),
        resume_worktree="m/wt-1",
        task_id="t-9",
    )
    argv = hibernation.detached_run_argv(spec, python="/py")
    assert argv[:4] == ["/py", "-m", "agent_dispatch", "run"]
    assert "--detach" not in argv  # this IS the detached copy
    assert "--resume" in argv and "m/wt-1" in argv
    assert "--task" in argv and "t-9" in argv
    # the wait command is fenced after '--'
    dd = argv.index("--")
    assert argv[dd + 1:] == ["agent-worktrees", "pr-watch", "42"]


# -- CLI: parsing ------------------------------------------------------------


def test_cli_parses_run_and_captures_command_after_dashdash():
    a = _args(["run", "--resume", "m/wt-1", "--", "sleep", "60"])
    assert a.func is _cmd_run
    assert a.resume == "m/wt-1"
    # The verbatim command after '--' is captured cross-version via _dashdash_tail
    # (was args.command/REMAINDER, which raised on 3.11 for the drive sibling; #383).
    assert a._dashdash_tail == ["sleep", "60"]


# -- CLI: foreground run -----------------------------------------------------


class _FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode


def test_run_foreground_executes_then_nudges(capsys, monkeypatch):
    ran = {}
    nudged = {}

    def fake_run(cmd, **k):
        ran["cmd"] = cmd
        return _FakeProc(0)

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_dispatch.bridge.send_nudge",
        lambda wt, msg, **k: nudged.update(wt=wt, msg=msg) or True,
    )
    # No agent-worktrees CLI in this test env -- the claim-release call must
    # degrade gracefully rather than share the patched subprocess.run above
    # (which fakes the *wait command's* process, not agent-worktrees').
    monkeypatch.setattr(
        "agent_dispatch.hibernation_claims.agent_worktrees_launch_prefix", lambda: None
    )
    rc = _cmd_run(_args(["run", "--resume", "m/wt-1", "--task", "t-1", "--", "sleep", "1"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["returncode"] == 0
    assert out["resumed"] is True
    assert ran["cmd"] == ["sleep", "1"]
    assert nudged["wt"] == "m/wt-1"
    assert out["claim_released"] is None  # no agent-worktrees CLI -- degraded, not fatal


def test_run_foreground_wait_runs_windowless(capsys, monkeypatch):
    """The operator-supplied wait command (e.g. `agent-worktrees pr-watch
    wait ...`) must never pop a visible console -- whether run directly in
    the foreground or re-exec'd inside a detached `run --detach` waiter
    (which calls this same non-detached path as its `waiter_child`). Without
    ``creationflags=CREATE_NO_WINDOW``, a console-subsystem/batch-wrapped
    command spawned from a window-less parent (the detached waiter) gets a
    brand-new visible console window -- the accumulating headed cmd.exe
    windows this regression guards against."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return _FakeProc(0)

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_dispatch.execution_cli.no_window_kwargs",
        lambda: {"creationflags": 0x08000000},
    )
    # Isolate the wait command's own call from any follow-on agent-bridge nudge
    # subprocess call (a separate, already-covered concern).
    monkeypatch.setattr("agent_dispatch.bridge.send_nudge", lambda wt, msg, **k: True)
    rc = _cmd_run(
        _args(["run", "--resume", "m/wt-1", "--", "agent-worktrees", "pr-watch", "42"])
    )
    assert rc == 0
    assert calls[0]["cmd"] == ["agent-worktrees", "pr-watch", "42"]
    assert calls[0]["kwargs"].get("creationflags") == 0x08000000



def test_run_requires_a_command(capsys):
    rc = _cmd_run(_args(["run", "--resume", "m/wt-1"]))
    assert rc == 2
    assert "needs a command" in capsys.readouterr().err


# -- CLI: detached run -------------------------------------------------------


def test_run_detach_spawns_waiter_without_executing_the_wait(capsys, monkeypatch):
    spawned = {}

    def fake_spawn(spec):
        spawned["spec"] = spec
        return {"pid": 4242, "argv": ["/py", "-m", "agent_dispatch", "run"]}

    monkeypatch.setattr("agent_dispatch.__main__._spawn_detached_waiter", fake_spawn)

    def _boom(*a, **k):  # pragma: no cover - the wait must not run in this process
        raise AssertionError("--detach must not run the wait inline")

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", _boom)

    rc = _cmd_run(_args(["run", "--detach", "--resume", "m/wt-1", "--", "sleep", "99"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["detached"] is True
    assert out["pid"] == 4242
    assert spawned["spec"].command == ("sleep", "99")
    # no --task -> nothing to suspend, and no client call should even be attempted
    assert out["suspended"] is None


# -- CLI: detached run atomically suspends its task --------------------------


class _FakeSuspendClient:
    """A minimal fake standing in for DispatchClient's context-manager + prepare."""

    def __init__(self, *, raises: Exception | None = None, owner: str | None = "headless-abc123"):
        self.calls = []
        self._raises = raises
        self._owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, task_id):
        return {"owner": self._owner} if self._owner else {}

    def prepare_run_waiter(self, task_id, *, worker_id, host, reason, resume_worktree, command):
        self.calls.append((task_id, worker_id, reason))
        if self._raises:
            raise self._raises
        return {
            "task_id": task_id,
            "generation": 7,
            "task_generation": 7,
            "owner_session_id": "session-1",
            "resume_worktree": resume_worktree,
            "command": command,
            "state": "preparing",
        }


class _FakeWaiterFinishClient:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def finish_run_waiter(self, task_id, **payload):
        self.calls.append((task_id, payload))
        return {"accepted": True, "waiter": {"generation": payload["generation"]}}

    def arm_run_waiter(self, task_id, **payload):
        self.calls.append((task_id, payload))
        return {"accepted": True, "waiter": {"generation": payload["generation"]}}

    def abort_run_waiter(self, task_id, **payload):
        self.calls.append((task_id, {"abort": payload}))
        return {"accepted": True, "waiter": {"generation": payload["generation"]}}


def test_run_detach_with_task_suspends_atomically(capsys, monkeypatch):
    from agent_dispatch import hibernation_claims, identity

    monkeypatch.setattr(
        "agent_dispatch.__main__._spawn_detached_waiter",
        lambda spec: {"pid": 1, "argv": []},
    )
    fake = _FakeSuspendClient()
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt-1"))
    claimed = {}

    def fake_add_claim(task_id, **k):
        claimed["call"] = (task_id, k)
        return {"state": "active"}

    monkeypatch.setattr(hibernation_claims, "add_hibernation_claim", fake_add_claim)

    rc = _cmd_run(
        _args(
            [
                "run",
                "--detach",
                "--resume",
                "m/wt-1",
                "--task",
                "t-1",
                "--",
                "agent-worktrees",
                "pr-watch",
                "42",
            ]
        )
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["detached"] is True
    # the suspend uses the TASK's own recorded owner (a headless worker id
    # here), not CWD/machine-worktree identity -- see the next test for the
    # regression this protects against (#2576's remaining scope).
    assert out["suspended"]["status"] == "suspended"
    assert out["suspended"]["worker_id"] == "headless-abc123"
    assert out["suspended"]["claim"] == {"state": "active"}
    assert out["suspended"]["generation"] == 7
    assert out["suspended"]["owner_session_id"] == "session-1"
    assert fake.calls == [
        ("t-1", "headless-abc123", "hibernating: agent-worktrees pr-watch 42")
    ]
    # the claim is journaled for the task, independent of who resolves as owner
    assert claimed["call"][0] == "t-1:7"
    assert claimed["call"][1]["note"] == "hibernating: agent-worktrees pr-watch 42"


def test_run_detach_with_task_suspends_using_headless_owner_not_cwd_identity(
    capsys, monkeypatch
):
    """Regression test: a headless-embodied worker's real owner id (e.g.
    ``headless-xxxx``) never equals the CWD-derived ``machine/worktree``
    identity. Before this fix, ``_suspend_for_detached_wait`` always composed
    from CWD identity, so the suspend call 409'd for every headless worker
    ("owned by 'headless-xxx', not 'machine/worktree'") and the task stayed
    `started` -- continuing to occupy its pool's concurrency slot -- for its
    entire hibernation. This proves the suspend now targets the task's actual
    recorded owner even when CWD identity resolves to something else entirely.
    """
    from agent_dispatch import hibernation_claims, identity

    monkeypatch.setattr(
        "agent_dispatch.__main__._spawn_detached_waiter",
        lambda spec: {"pid": 1, "argv": []},
    )
    fake = _FakeSuspendClient(owner="headless-real-owner")
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    # CWD identity resolves to something entirely different from the task's
    # real owner -- the old bug composed THIS as the suspend worker_id.
    monkeypatch.setattr(
        identity, "resolve_identity", lambda: ("owner_user-cloud1", "some-other-worktree")
    )
    monkeypatch.setattr(
        hibernation_claims, "add_hibernation_claim", lambda task_id, **k: {"state": "active"}
    )

    rc = _cmd_run(
        _args(
            [
                "run", "--detach", "--resume", "m/wt-1", "--task", "t-1",
                "--", "agent-worktrees", "pr-watch", "42",
            ]
        )
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["suspended"]["worker_id"] == "headless-real-owner"
    assert fake.calls[0][1] == "headless-real-owner"


def test_run_detach_claim_add_degrades_gracefully_without_agent_worktrees(
    capsys, monkeypatch
):
    """No agent-worktrees CLI on this host -- suspend still proceeds; the
    claim is simply ``None`` (defense in depth, not a precondition)."""
    from agent_dispatch import hibernation_claims, identity

    monkeypatch.setattr(
        "agent_dispatch.__main__._spawn_detached_waiter",
        lambda spec: {"pid": 1, "argv": []},
    )
    fake = _FakeSuspendClient()
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt-1"))
    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: None)

    rc = _cmd_run(
        _args(["run", "--detach", "--resume", "m/wt-1", "--task", "t-1", "--", "sleep", "1"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["suspended"]["status"] == "suspended"
    assert out["suspended"]["claim"] is None


def test_release_hibernation_claim_shells_out_to_agent_worktrees(monkeypatch):
    """Unit-level: release_hibernation_claim builds the expected argv and parses
    a successful JSON reply, and also best-effort mirrors the disposition
    externally (#2584 follow-up)."""
    from agent_dispatch import hibernation_claims

    calls = []

    class _Proc:
        returncode = 0
        stdout = '{"worktree_id": "wt-1", "ref": "t-1", "action": "released"}'
        stderr = ""

    def fake_run(argv, **k):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(hibernation_claims.subprocess, "run", fake_run)

    result = hibernation_claims.release_hibernation_claim("t-1")
    assert result == {"worktree_id": "wt-1", "ref": "t-1", "action": "released"}
    assert calls[0] == ["aw", "claims", "release", "t-1", "--json"]
    assert calls[1] == [
        "aw", "claims", "mirror-status", "task", "t-1",
        "--status", "released", "--holder", "agent-dispatch", "--json",
    ]


def test_add_hibernation_claim_shells_out_to_agent_worktrees(monkeypatch):
    from agent_dispatch import hibernation_claims

    calls = []

    class _Proc:
        returncode = 0
        stdout = '{"worktree_id": "wt-1", "ref": "t-1", "kind": "task", "state": "active"}'
        stderr = ""

    def fake_run(argv, **k):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(hibernation_claims.subprocess, "run", fake_run)

    result = hibernation_claims.add_hibernation_claim("t-1", note="hibernating: sleep 1")
    assert result["kind"] == "task"
    assert calls[0] == [
        "aw",
        "claims",
        "add",
        "task",
        "t-1",
        "--json",
        "--note",
        "hibernating: sleep 1",
    ]
    assert calls[1] == [
        "aw", "claims", "mirror-status", "task", "t-1",
        "--status", "active", "--holder", "agent-dispatch", "--json",
    ]


def test_add_hibernation_claim_failure_does_not_mirror(monkeypatch):
    """The external mirror is only attempted after a successful local claim add
    -- a failed local journal must not also attempt (and mis-report) a mirror
    write."""
    from agent_dispatch import hibernation_claims

    calls = []

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def fake_run(argv, **k):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(hibernation_claims.subprocess, "run", fake_run)

    result = hibernation_claims.add_hibernation_claim("t-1")
    assert result is None
    assert len(calls) == 1  # only the (failed) claims-add call, no mirror attempt


def test_run_detach_suspend_failure_does_not_fail_the_detach(capsys, monkeypatch):
    from agent_dispatch import identity
    from agent_dispatch.client import DispatchError

    monkeypatch.setattr(
        "agent_dispatch.__main__._spawn_detached_waiter",
        lambda spec: {"pid": 1, "argv": []},
    )
    fake = _FakeSuspendClient(raises=DispatchError(503, "coordinator unreachable"))
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("m", "wt-1"))

    rc = _cmd_run(
        _args(["run", "--detach", "--resume", "m/wt-1", "--task", "t-1", "--", "sleep", "1"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["detached"] is False
    assert "coordinator unreachable" in out["rollback"]["error"]


def test_run_detach_with_task_but_no_resolvable_owner_reports_error(capsys, monkeypatch):
    from agent_dispatch import identity

    monkeypatch.setattr(
        "agent_dispatch.__main__._spawn_detached_waiter",
        lambda spec: {"pid": 1, "argv": []},
    )
    # No owner recorded on the task (or lookup unavailable) -- falls through to
    # CWD-derived identity, which also fails to resolve here.
    monkeypatch.setattr(
        "agent_dispatch.__main__._client", lambda args: _FakeSuspendClient(owner=None)
    )
    monkeypatch.setattr(identity, "resolve_identity", lambda: (None, None))

    rc = _cmd_run(
        _args(["run", "--detach", "--resume", "m/wt-1", "--task", "t-1", "--", "sleep", "1"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["detached"] is False
    assert "could not resolve" in out["error"]


def test_waiter_child_reattempts_timeout_and_queues_finish(capsys, monkeypatch, tmp_path):
    from agent_dispatch import companion, remote_dispatch

    calls = []

    def fake_run(argv, check=False, **_kwargs):
        calls.append(argv)
        class _Proc:
            returncode = 124 if len(calls) < 3 else 0
        return _Proc()

    monkeypatch.setattr("agent_dispatch.__main__.subprocess.run", fake_run)
    monkeypatch.setattr(companion, "process_start_token", lambda _pid: "token-123")
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "test-host")
    fake = _FakeWaiterFinishClient()
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)

    rc = _cmd_run(
        _args(
            [
                "run",
                "--waiter-child",
                "--waiter-generation",
                "3",
                "--resume",
                "m/wt-1",
                "--task",
                "t-1",
                "--",
                "sleep",
                "1",
            ]
        )
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(calls) == 3
    assert out["returncode"] == 0
    assert out["reattempts_on_timeout"] == 2
    assert fake.calls[0][0] == "t-1"
    assert fake.calls[0][1]["generation"] == 3
    assert out["resumed"] is None


def test_waiter_child_aborts_preparing_waiter_when_identity_missing(capsys, monkeypatch):
    from agent_dispatch import companion, remote_dispatch

    monkeypatch.setattr(companion, "process_start_token", lambda _pid: None)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "test-host")
    fake = _FakeWaiterFinishClient()
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)

    rc = _cmd_run(
        _args(
            [
                "run",
                "--waiter-child",
                "--waiter-generation",
                "3",
                "--resume",
                "m/wt-1",
                "--task",
                "t-1",
                "--",
                "sleep",
                "1",
            ]
        )
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["returncode"] == 125
    assert fake.calls[0][0] == "t-1"
    assert fake.calls[0][1]["abort"]["generation"] == 3


def test_release_hibernation_claim_for_host_worktree_mirrors_with_project(monkeypatch):
    from agent_dispatch import hibernation_claims, identity, remote_dispatch

    calls = []

    class _Proc:
        returncode = 0
        stdout = '{"worktree_id":"wt-1","ref":"t-1","action":"released"}'
        stderr = ""

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(hibernation_claims.subprocess, "run", fake_run)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(identity, "name_for_repo", lambda repo: "project-alpha")

    result = hibernation_claims.release_hibernation_claim_for_host_worktree(
        "t-1",
        "host-a",
        "wt-1",
        "example.com/acme/widget",
    )

    assert result == {"worktree_id": "wt-1", "ref": "t-1", "action": "released"}
    assert calls == [
        [
            "aw",
            "--project",
            "project-alpha",
            "claims",
            "release",
            "t-1",
            "--worktree",
            "wt-1",
            "--json",
        ],
        [
            "aw",
            "--project",
            "project-alpha",
            "claims",
            "mirror-status",
            "task",
            "t-1",
            "--status",
            "released",
            "--holder",
            "agent-dispatch",
            "--json",
        ],
    ]


def test_remote_release_hibernation_claim_for_host_worktree_mirrors_with_project(monkeypatch):
    from types import SimpleNamespace

    from agent_dispatch import bridge_remote, hibernation_claims, identity, remote_dispatch

    mirror_calls = []
    ssh_calls = []

    def fake_run(argv, **_kwargs):
        mirror_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(hibernation_claims, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(hibernation_claims.subprocess, "run", fake_run)
    monkeypatch.setattr(hibernation_claims.shutil, "which", lambda exe: "/usr/bin/ssh" if exe == "ssh" else None)
    monkeypatch.setattr(
        hibernation_claims,
        "run_ssh_capture",
        lambda argv, timeout=None: ssh_calls.append((argv, timeout))
        or SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(identity, "name_for_repo", lambda repo: "project-alpha")
    monkeypatch.setattr(bridge_remote, "normalize_host", lambda host: f"ssh-{host}")

    result = hibernation_claims.release_hibernation_claim_for_host_worktree(
        "t-1",
        "host-b",
        "wt-1",
        "example.com/acme/widget",
    )

    assert result == {}
    assert ssh_calls == [
        (
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=3",
                "ssh-host-b",
                "agent-worktrees --project project-alpha claims release t-1 --worktree wt-1 --json",
            ],
            15.0,
        )
    ]
    assert mirror_calls == [
        [
            "aw",
            "--project",
            "project-alpha",
            "claims",
            "mirror-status",
            "task",
            "t-1",
            "--status",
            "released",
            "--holder",
            "agent-dispatch",
            "--json",
        ]
    ]
