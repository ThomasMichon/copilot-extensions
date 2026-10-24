"""Actual Manager seams retain boundary evidence in a protected SQLite journal."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from worktree_manager import __main__ as main
from worktree_manager import engine_client, launch_observations as observations, launcher
from worktree_manager.launch_attempts import LaunchAttemptError, LaunchAttempts
from worktree_manager.picker_app import LaunchRequest


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    store = LaunchAttempts(tmp_path)
    monkeypatch.setattr(observations, "LaunchAttempts", lambda: store)
    identity = uuid4().hex
    store.record_intent(
        identity, action="new", project="example", surface="new-dialog", is_local=True,
        prompt="private fixture\n exact prompt ", options={"no_mux": True},
    )
    monkeypatch.setattr(main, "_relocated_launch_script", lambda: None)
    monkeypatch.setattr(launcher, "uses_ahp", lambda *args: False)
    return store, LaunchRequest(
        project="example", mode="new", worktree_id=None, no_mux=True, attempt_id=identity,
    )


def events(attempt):
    store, request = attempt
    return store.detail(request.attempt_id)["events"]


def plan(action="exec", **extra):
    return engine_client.launch_plan_from_dict({
        "action": action, "worktree_id": "worktree-a", "cmd": ["fixture-copilot"],
        "work_dir": "fixture-directory", "launch_request_id": "request-a", "seed_id": "seed-a",
        **extra,
    })


@pytest.mark.parametrize("action", ["exec", "remote"])
@pytest.mark.parametrize("code", [0, 7])
def test_launch_records_only_manager_boundaries(attempt, monkeypatch, action, code):
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan(action))
    def launch(*args, **kwargs):
        assert events(attempt)[-1]["phase"] == "launch-requested"
        return code
    monkeypatch.setattr(launcher, "launch", launch)
    assert main._run_launch(attempt[1]) == code
    recorded = events(attempt)
    assert [event["phase"] for event in recorded] == [
        "confirmed", "resolution-requested", "resolved", "launch-requested", "launcher-returned",
    ]
    assert recorded[2]["details"] == {
        "worktree_id": "worktree-a", "request_id": "request-a", "seed_id": "seed-a",
    }
    assert recorded[-1]["details"] == {"exit_code": code}
    assert {event["owner"] for event in recorded} == {"manager"}
    assert "private fixture" not in json.dumps(recorded)


@pytest.mark.parametrize("mode", ["new", "resume"])
@pytest.mark.parametrize("worktree_id", [
    "d\u00e8sktop-wsl-20261021-010203-abcd",
    "de\u0301sktop-wsl-20261021-010203-abcd",
    "\u65e5\u672c-\U0001f5a5-wsl-20261021-010203-abcd",
])
def test_journaled_unicode_worktree_identity_reaches_launch_unchanged(
    attempt, monkeypatch, mode, worktree_id,
):
    store, _ = attempt
    identity = uuid4().hex
    selected = worktree_id if mode == "resume" else None
    store.record_intent(
        identity, action=mode, project="example", surface="picker-action",
        is_local=True, worktree_id=selected,
    )
    request = LaunchRequest(
        project="example", mode=mode, worktree_id=selected, attempt_id=identity,
    )
    monkeypatch.setattr(
        engine_client, "resolve_launch_plan", lambda *a, **kw: plan(worktree_id=worktree_id),
    )
    def launch(returned, **kwargs):
        assert returned.worktree_id == worktree_id
        return 0
    monkeypatch.setattr(launcher, "launch", launch)
    assert main._run_launch(request) == 0
    detail = store.detail(identity)
    assert detail["action"] == mode and detail["worktree_id"] == selected
    assert detail["events"][2]["details"]["worktree_id"] == worktree_id
    assert detail["events"][-1]["phase"] == "launcher-returned"


def test_seed_guard_refusal_precedes_engine(attempt, monkeypatch):
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: pytest.fail("engine"))
    request = replace(attempt[1], machine="remote-host", environment="linux", seed_prompt="fixture")
    assert main._run_launch(request) == 1
    assert [event["phase"] for event in events(attempt)] == ["confirmed", "refused"]


def test_resolution_error_is_unknown_not_evidence_of_no_allocation(attempt, monkeypatch):
    def resolve(*args, **kwargs):
        assert events(attempt)[-1]["phase"] == "resolution-requested"
        raise engine_client.EngineError("fixture unavailable")
    monkeypatch.setattr(engine_client, "resolve_launch_plan", resolve)
    assert main._run_launch(attempt[1]) == 1
    assert [event["phase"] for event in events(attempt)] == [
        "confirmed", "resolution-requested", "unknown",
    ]


@pytest.mark.parametrize("error", [RuntimeError("private exception fixture"), KeyboardInterrupt()])
def test_launcher_exception_retains_unknown_without_private_error(attempt, monkeypatch, capsys, error):
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan())
    def launch(*a, **kw):
        raise error
    monkeypatch.setattr(launcher, "launch", launch)
    with pytest.raises(type(error)):
        main._run_launch(attempt[1])
    assert events(attempt)[-1]["phase"] == "unknown"
    assert "private exception fixture" not in json.dumps(events(attempt)) + capsys.readouterr().out


@pytest.mark.parametrize("phase", ["resolution-requested", "resolved", "launch-requested"])
def test_required_observation_failure_blocks_next_effect(attempt, monkeypatch, capsys, phase):
    store, request = attempt
    append = store.append
    entered = []
    def failing_append(*args, **kwargs):
        if kwargs["phase"] == phase:
            raise LaunchAttemptError("private storage fixture")
        return append(*args, **kwargs)
    monkeypatch.setattr(store, "append", failing_append)
    def resolve(*a, **kw):
        entered.append("resolve")
        return plan()
    monkeypatch.setattr(engine_client, "resolve_launch_plan", resolve)
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: pytest.fail("no launch"))
    assert main._run_launch(request) == 1
    assert entered == ([] if phase == "resolution-requested" else ["resolve"])
    assert "private storage fixture" not in capsys.readouterr().out


def test_post_effect_storage_failure_does_not_fabricate_launch_failure(attempt, monkeypatch, caplog):
    store, request = attempt
    append = store.append
    def failing_append(*args, **kwargs):
        if kwargs["phase"] == "launcher-returned":
            raise LaunchAttemptError("private storage fixture")
        return append(*args, **kwargs)
    monkeypatch.setattr(store, "append", failing_append)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan())
    calls = []
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: calls.append("launch") or 0)
    assert main._run_launch(request) == 0
    assert calls == ["launch"]
    assert events(attempt)[-1]["phase"] == "launch-requested"
    output = caplog.text
    assert "WARNING" in output and "Do not retry automatically" in output
    assert "private storage fixture" not in output


def test_plan_noop_is_not_a_launcher_return(attempt, monkeypatch):
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan("none", exit_code=4))
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: pytest.fail("no launch"))
    assert main._run_launch(attempt[1]) == 4
    assert events(attempt)[-1]["phase"] == "refused"
    assert events(attempt)[-1]["details"] == {"exit_code": 4}


def test_relocated_launch_uses_same_attempt_without_claiming_native_handoff(attempt, monkeypatch):
    monkeypatch.setattr(main, "_relocated_launch_script", lambda: "fixture-script")
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan())
    def relocated(request, returned, script):
        assert request.attempt_id == attempt[1].attempt_id
        assert events(attempt)[-1]["phase"] == "launch-requested"
        return 0
    monkeypatch.setattr(main, "_run_relocated_mux_launch", relocated)
    assert main._run_launch(attempt[1]) == 0
    assert events(attempt)[-1]["phase"] == "launcher-returned"


@pytest.mark.parametrize("failure", ["none", "required-write", "preparation"])
def test_ahp_requested_boundary_precedes_any_hosted_session_mutation(attempt, monkeypatch, failure):
    from worktree_manager import ahp_provider

    store, request = attempt
    request = replace(request, ahp=True)
    monkeypatch.setattr(launcher, "uses_ahp", lambda *a: True)
    monkeypatch.setattr(engine_client, "require_agent_hosting", lambda *a: None)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan())
    append = store.append
    def failing_append(*args, **kwargs):
        if failure == "required-write" and kwargs["phase"] == "launch-requested":
            raise LaunchAttemptError("private storage fixture")
        return append(*args, **kwargs)
    monkeypatch.setattr(store, "append", failing_append)
    effects = []
    def prepare(*a):
        assert events(attempt)[-1]["phase"] == "launch-requested"
        effects.append("prepare")
        if failure == "preparation":
            raise ahp_provider.AhpProviderError("fixture preparation failure")
        return object()
    monkeypatch.setattr(ahp_provider, "ensure_session", prepare)
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda value, _: value)
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: effects.append("launch") or 0)
    assert main._run_launch(request) == (0 if failure == "none" else 1)
    assert effects == {
        "none": ["prepare", "launch"], "required-write": [], "preparation": ["prepare"],
    }[failure]
    expected = ["confirmed", "resolution-requested", "resolved"]
    if failure != "required-write":
        expected += ["launch-requested", "launcher-returned" if failure == "none" else "unknown"]
    assert [event["phase"] for event in events(attempt)] == expected


@pytest.mark.parametrize("result", [{"ok": False}, {"ok": True}, {"ok": True, "verified": True}])
def test_restore_boundary_precedes_remux_and_resume(attempt, monkeypatch, result):
    monkeypatch.setattr(engine_client, "project_binstub_command", lambda *a: ["fixture"])
    def remux(*a, **kw):
        assert events(attempt)[-1]["phase"] == "restore-requested"
        return result
    monkeypatch.setattr(engine_client, "run_json", remux)
    called = []
    def launch(request):
        assert events(attempt)[-1]["phase"] == "restore-returned"
        called.append(request.attempt_id)
        return 0
    monkeypatch.setattr(main, "_run_launch", launch)
    ok = bool(result.get("verified"))
    assert main._restore_production_picker_session(
        "example", "worktree-a", title=None, is_local=True, machine=None, environment=None,
        attempt_id=attempt[1].attempt_id,
    ) == (0 if ok else 1)
    assert called == ([attempt[1].attempt_id] if ok else [])
    assert events(attempt)[-1]["phase"] == ("restore-returned" if ok else "unknown")


def test_unjournaled_call_does_not_initialize_store(monkeypatch):
    monkeypatch.setattr(observations, "LaunchAttempts", lambda: pytest.fail("no store"))
    assert observations.run_attempt(None, lambda: observations.launch(None, lambda: 9)) == 9


def test_normal_launch_adds_only_four_in_process_observation_appends(attempt, monkeypatch):
    calls = []
    class AppendOnlyStore:
        def append(self, identity, **fields):
            assert identity == attempt[1].attempt_id
            calls.append(fields)
    monkeypatch.setattr(observations, "LaunchAttempts", AppendOnlyStore)
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: plan())
    monkeypatch.setattr(launcher, "launch", lambda *a, **kw: 0)
    assert main._run_launch(attempt[1]) == 0
    assert [call["phase"] for call in calls] == [
        "resolution-requested", "resolved", "launch-requested", "launcher-returned",
    ]


def test_missing_intent_never_becomes_execution_authority(attempt, monkeypatch):
    monkeypatch.setattr(engine_client, "resolve_launch_plan", lambda *a, **kw: pytest.fail("no resolve"))
    assert main._run_launch(replace(attempt[1], attempt_id=uuid4().hex)) == 1
    assert len(attempt[0].entries()) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_provider_dispatch_records_return_or_unknown(attempt, monkeypatch, failure):
    from worktree_manager.production_picker import runner

    monkeypatch.setattr(runner, "run", lambda project: {
        "action": "open-venue", "provider": "fixture-provider", "venue": "fixture-venue",
        "attempt_id": attempt[1].attempt_id,
    })
    def provider(provider, venue):
        assert (provider, venue) == ("fixture-provider", "fixture-venue")
        assert events(attempt)[-1]["phase"] == "launch-requested"
        if failure:
            raise OSError("fixture unavailable")
        return 7
    monkeypatch.setattr(launcher, "open_venue", provider)
    if failure:
        with pytest.raises(OSError):
            main._run_production_picker("example")
    else:
        assert main._run_production_picker("example") == 7
    assert [event["phase"] for event in events(attempt)] == [
        "confirmed", "launch-requested", "unknown" if failure else "launcher-returned",
    ]


@pytest.mark.parametrize("failure,resume", [(False, True), (False, False), (True, True)])
def test_task_open_observes_embodiment_off_thread_before_resume(attempt, monkeypatch, failure, resume):
    from worktree_manager import launch_confirmation
    from worktree_manager.production_picker import context
    from worktree_manager.production_picker.picker_tui import engine_worktree_actions, tasks

    identity = attempt[1].attempt_id
    resumed = []
    class Owner(engine_worktree_actions.PickerScreenWorktreeActionsMixin):
        real_ops = True
        src = SimpleNamespace(LOCAL=("fixture-host", "linux"), is_local=lambda *a: True)
        def _current_tab(self):
            return {"env": "linux", "source_kind": "machine-ssh"}
        def _run_bg(self, label, work, done=None, **kwargs):
            result = work()
            if done:
                done(result)
        def _resume_embodied_worktree_cli(self, worktree_id, **kwargs):
            assert events(attempt)[-1]["phase"] == "embodiment-returned"
            assert kwargs["attempt_id"] == identity
            resumed.append(worktree_id)
            return resume, ""
    monkeypatch.setattr(context, "project", lambda: "example")
    monkeypatch.setattr(
        launch_confirmation, "confirm_before_effect",
        lambda *a, saved, **kw: saved({"attempt_id": identity}),
    )
    monkeypatch.setattr(tasks, "_resolve_argv", lambda *a: ["fixture"])
    monkeypatch.setattr(tasks, "_child_process_env", lambda: {})
    class Process:
        returncode = 0
        def communicate(self, **kw):
            return ('{"worktree":"task-worktree","project":"example"}', "")
    def popen(*a, **kw):
        assert events(attempt)[-1]["phase"] == "embodiment-requested"
        if failure:
            raise FileNotFoundError("private fixture exception")
        return Process()
    monkeypatch.setattr(engine_worktree_actions.subprocess, "Popen", popen)
    owner = Owner()
    assert owner._embody_task_cli({"task_id": "task-a", "machine": "fixture-host"})[0]
    assert resumed == ([] if failure else ["task-worktree"])
    expected = [
        "confirmed", "embodiment-requested", "unknown" if failure else "embodiment-returned",
    ]
    if not failure and not resume:
        expected.append("refused")
    assert [event["phase"] for event in events(attempt)] == expected
    if not failure:
        assert events(attempt)[2]["details"] == {"worktree_id": "task-worktree"}


def test_restore_required_storage_failure_prevents_remux(attempt, monkeypatch):
    monkeypatch.setattr(attempt[0], "append", lambda *a, **kw: (_ for _ in ()).throw(
        LaunchAttemptError("fixture"),
    ))
    monkeypatch.setattr(engine_client, "project_binstub_command", lambda *a: pytest.fail("no remux"))
    assert main._restore_production_picker_session(
        "example", "worktree-a", title=None, is_local=True, machine=None, environment=None,
        attempt_id=attempt[1].attempt_id,
    ) == 1


def test_task_required_storage_failure_prevents_embodiment(attempt, monkeypatch):
    monkeypatch.setattr(attempt[0], "append", lambda *a, **kw: (_ for _ in ()).throw(
        LaunchAttemptError("fixture"),
    ))
    ok, message = observations.embody(attempt[1].attempt_id, lambda: pytest.fail("no embodiment"))
    assert not ok and "no embodiment started" in message
