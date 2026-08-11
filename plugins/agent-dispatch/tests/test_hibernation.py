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
    rc = _cmd_run(_args(["run", "--resume", "m/wt-1", "--task", "t-1", "--", "sleep", "1"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["returncode"] == 0
    assert out["resumed"] is True
    assert ran["cmd"] == ["sleep", "1"]
    assert nudged["wt"] == "m/wt-1"


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
