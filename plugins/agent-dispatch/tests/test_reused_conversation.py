"""Legacy acquisition compatibility; ordinary startup has its own contract suite."""

import json
import subprocess

from agent_dispatch import bridge, embody, session_acquisition
from agent_dispatch.spawn_factories import make_embody_spawn
from tests._helpers import TEST_REPO


def make_legacy_acp_acquisition():
    """Exercise the retained legacy adapter, not ordinary factory selection."""
    def spawn(task):
        try:
            result = session_acquisition.acquire(task, mode="acp", prompt="assigned task")
            return True, session_acquisition.handle(result)
        except bridge.BridgeCarriedSessionBusy as exc:
            return False, {"error": str(exc), "deferred": True}
        except bridge.BridgeUnavailable as exc:
            return False, {"error": str(exc)}
    return spawn


def _task(ownership="reused", *, conversation_retired=False):
    return {
        "id": "review-task",
        "generation": 0,
        "spawn_reservation_key": "reservation-a",
        "repo": TEST_REPO,
        "spawn_worktree": "review-worktree",
        "spawn_worktree_path": "/example/review-worktree",
        "spawn_allocation_project": TEST_REPO,
        "spawn_worktree_ownership": ownership,
        "spawn_session_handle": None,
        "spawn_conversation_retired": conversation_retired,
    }


def _transport(monkeypatch, *, refusal=None, send_busy=False):
    calls = []
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(bridge, "_resolve_agent_record", lambda *a, **kw: None)

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[1:] == ["acquire", "--request-file", "-"]
        payload = json.loads(kwargs["input"])
        if refusal or send_busy or payload["conversation_retired"] is None:
            code = "session_busy" if send_busy else (
                "foreign_live_holder" if refusal and refusal.get("reason") == "live_cli_holds_worktree"
                else "retirement_unknown"
            )
            return subprocess.CompletedProcess(
                argv, 1, json.dumps({"detail": {"code": code, "message": "refused"}}), "refused",
            )
        fresh = payload["allocation"] == "created" or payload["conversation_retired"] is True
        sid = "replacement-conversation" if fresh else payload.get("prior_session_id") or "existing-conversation"
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            **{field: payload[field] for field in ("mode", "task_id", "task_generation", "worktree_id", "delivery_id")},
            "session_id": sid, "native_session_id": f"native-{sid}",
            "acquired": "created" if fresh else "resumed",
        }), "")

    monkeypatch.setattr(session_acquisition.subprocess, "run", run)
    return calls


def test_missing_handle_resumes_retained_worktree_conversation(monkeypatch):
    calls = _transport(monkeypatch)
    ok, handle = make_legacy_acp_acquisition()(_task())
    assert ok is True
    assert handle["session"] == "local-body:existing-conversation"
    assert calls[0] == ["bridge", "acquire", "--request-file", "-"]
    assert len(calls) == 1
    assert not any("create" in call or "--force" in call for call in calls)


def test_first_allocation_still_creates_a_conversation(monkeypatch):
    calls = _transport(monkeypatch)
    ok, handle = make_legacy_acp_acquisition()(_task("created"))
    assert ok is True
    assert handle["session"] == "local-body:replacement-conversation"
    assert calls[0][1] == "acquire"


def test_retained_worktree_never_takes_over_an_interactive_holder(monkeypatch):
    calls = _transport(monkeypatch, refusal={
        "reason": "live_cli_holds_worktree", "session_id": "interactive-holder",
    })
    ok, handle = make_legacy_acp_acquisition()(_task())
    assert ok is False
    assert handle["deferred"] is True
    assert len(calls) == 1
    assert not any("restart-worktree" in call or "create" in call for call in calls)


def test_resume_failure_is_not_a_success_shaped_fresh_conversation(monkeypatch):
    calls = _transport(monkeypatch, refusal={"reason": "coordinator_unreachable"})
    ok, handle = make_legacy_acp_acquisition()(_task())
    assert ok is False
    assert "refused" in handle["error"]
    assert len(calls) == 1


def test_busy_send_never_ends_or_replaces_the_resumed_conversation(monkeypatch):
    calls = _transport(monkeypatch, send_busy=True)
    ok, handle = make_legacy_acp_acquisition()(_task())
    assert ok is False
    assert handle["deferred"] is True
    assert len(calls) == 1
    assert not any(
        "end" in call or "restart-worktree" in call or "create" in call
        for call in calls
    )


def test_gone_carried_body_falls_back_to_worktree_resume_not_create(monkeypatch):
    calls = _transport(monkeypatch)
    monkeypatch.setattr(embody, "local_body_verdict", lambda sid: "gone")
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **kw: False)
    task = {**_task(), "spawn_session_handle": "local-body:stopped-body"}
    ok, handle = make_legacy_acp_acquisition()(task)
    assert ok is True
    assert handle["session"] == "local-body:stopped-body"
    assert not any("create" in call for call in calls)


def test_retired_conversation_never_recovers_via_worktree_resume(monkeypatch):
    """``worktree_ownership == "reused"`` alone also matches a worktree whose
    carried session was deliberately retired (an operator rearm) -- the
    worktree-resume fallback resumes whatever session is latest in that
    directory, independent of the dropped ``spawn_session_handle``, so it
    would silently resurrect the exact conversation the rearm retired. A
    genuinely fresh conversation must be created instead -- never
    ``--strict resume``."""
    calls = _transport(monkeypatch)
    ok, handle = make_legacy_acp_acquisition()(_task(conversation_retired=True))
    assert ok is True
    assert handle["session"] == "local-body:replacement-conversation"
    assert calls[0][1] == "acquire"
    assert not any(
        call[1:3] == ["--json", "resume"] for call in calls
    )


def test_missing_conversation_retired_marker_fails_closed(monkeypatch):
    """Coordinator/client version-skew hazard: an older coordinator's
    reservation response predates ``conversation_retired`` entirely, so the
    reused-allocation task payload the supervisor builds carries no
    ``spawn_conversation_retired`` key at all. This must fail CLOSED (treat
    as retired, create a genuinely fresh conversation) rather than coercing
    the missing/unknown marker to "not retired" and strict-resuming
    whatever session is latest in the worktree directory -- which could be
    the exact conversation an operator rearm just retired."""
    calls = _transport(monkeypatch)
    task = {
        "id": "review-task",
        "generation": 0,
        "spawn_reservation_key": "reservation-a",
        "repo": TEST_REPO,
        "spawn_worktree": "review-worktree",
        "spawn_worktree_path": "/example/review-worktree",
        "spawn_allocation_project": TEST_REPO,
        "spawn_worktree_ownership": "reused",
        "spawn_session_handle": None,
        # spawn_conversation_retired deliberately absent -- the pre-field
        # coordinator shape.
    }
    ok, handle = make_legacy_acp_acquisition()(task)
    assert ok is False
    assert "refused" in handle["error"]
    assert not any(call[1:3] == ["--json", "resume"] for call in calls)


def _embody_cmd(monkeypatch):
    """Capture the ``agent-worktrees embody`` argv the CLI spawn path builds,
    without actually shelling out."""
    calls = []

    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])

    def fake_run(cmd, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append(payload)

        class _Proc:
            returncode = 0
            stdout = json.dumps({
                **{field: payload[field] for field in ("mode", "task_id", "task_generation", "worktree_id", "delivery_id")},
                "session_id": "cli-session", "native_session_id": "cli-session", "acquired": "resumed",
            })
            stderr = ""

        return _Proc()

    monkeypatch.setattr(session_acquisition.subprocess, "run", fake_run)
    return calls


def test_cli_spawn_resumes_a_retained_not_retired_conversation(monkeypatch):
    """The CLI (``agent-worktrees embody``) spawn path must thread the SAME
    fail-closed ``resume_worktree_eligible`` decision the bridge/headless
    path already makes -- a reused, not-retired allocation resumes its head
    session (``--resume-head``) rather than cold-starting."""
    calls = _embody_cmd(monkeypatch)
    ok, _handle = make_embody_spawn()(_task())
    assert ok is True
    assert calls[0]["allocation"] == "reused"
    assert calls[0]["conversation_retired"] is False


def test_cli_spawn_never_resumes_a_retired_conversation(monkeypatch):
    calls = _embody_cmd(monkeypatch)
    ok, _handle = make_embody_spawn()(_task(conversation_retired=True))
    assert ok is True
    assert calls[0]["conversation_retired"] is True


def test_cli_spawn_fails_closed_on_missing_retired_marker(monkeypatch):
    calls = _embody_cmd(monkeypatch)
    task = {**_task(), "spawn_conversation_retired": None}
    ok, _handle = make_embody_spawn()(task)
    assert ok is True
    assert calls[0]["conversation_retired"] is None


def test_cli_spawn_never_resumes_a_freshly_created_worktree(monkeypatch):
    calls = _embody_cmd(monkeypatch)
    ok, _handle = make_embody_spawn()(_task("created"))
    assert ok is True
    assert calls[0]["allocation"] == "created"
