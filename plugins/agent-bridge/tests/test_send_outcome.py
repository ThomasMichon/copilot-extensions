"""Typed ``agent-bridge send --json`` outcomes and refusal exit codes."""

from __future__ import annotations

import argparse
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as m
from agent_bridge import send_outcome
from agent_bridge import session_targeting_cli as stc
from agent_bridge.client import BridgeClientError
from agent_bridge.db import Database
from agent_bridge.routes import live_sessions


@pytest.fixture
def api(tmp_db: Database) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    app.include_router(live_sessions.router)
    return TestClient(app)


def _register(db: Database, sid: str, worktree_id: str = "wt-a", *, now: float | None = None) -> None:
    db.register_live_session(sid, machine=None, cwd=None, worktree_id=worktree_id, repo=None,
                             branch=None, pid=None, role=None, now=now or time.time())


def _post(api: TestClient, sid: str, **extra):
    return api.post(f"/api/v1/live-sessions/{sid}/messages",
                    json={"sender": "op", "body": "hi", **extra})


def _refusal(response) -> send_outcome.SendRefused:
    """Map a real route refusal the way the CLI does, pinning the route's
    detail text to the CLI's classification."""
    refused = send_outcome.refusal_from_live_error(
        BridgeClientError(response.status_code, response.json()["detail"]), "t")
    assert refused is not None, response.text
    return refused


# -- the route's refusals, mapped -----------------------------------------


def test_unregistered_target_is_unavailable_and_not_retryable(api):
    refused = _refusal(_post(api, "ghost"))
    assert (refused.outcome, refused.reason, refused.retryable, refused.exit_code) == (
        "refused_unavailable", "not_found", False, 69)


def test_lapsed_lease_is_unavailable_and_retryable(api, tmp_db):
    from agent_bridge.db import LIVE_SESSION_STALE_SECONDS

    _register(tmp_db, "cli-old", now=time.time() - LIVE_SESSION_STALE_SECONDS - 10)
    refused = _refusal(_post(api, "cli-old"))
    assert (refused.outcome, refused.reason, refused.retryable) == ("refused_unavailable", "stale", True)


def test_superseded_incarnation_is_unavailable_and_retryable(api, tmp_db):
    _register(tmp_db, "cli-old", now=time.time() - 5)
    _register(tmp_db, "cli-new")
    refused = _refusal(_post(api, "cli-old"))
    assert (refused.outcome, refused.reason, refused.retryable) == ("refused_unavailable", "superseded", True)


def test_expected_session_mismatch_is_unavailable_and_not_retryable(api, tmp_db):
    _register(tmp_db, "cli-1")
    refused = _refusal(_post(api, "cli-1", expected_session_id="cli-other"))
    assert (refused.outcome, refused.reason, refused.retryable) == (
        "refused_unavailable", "expected_mismatch", False)


def test_a_reused_idempotency_key_is_a_conflict(api, tmp_db):
    _register(tmp_db, "cli-1")
    assert _post(api, "cli-1", idempotency_key="k").status_code == 200
    refused = _refusal(_post(api, "cli-1", idempotency_key="k", body="different"))
    assert (refused.outcome, refused.reason, refused.exit_code) == ("refused_conflict", "idempotency_conflict", 65)


def test_an_identical_retry_reports_duplicate_with_the_original_id(api, tmp_db):
    _register(tmp_db, "cli-1")
    first = _post(api, "cli-1", idempotency_key="k").json()
    second = _post(api, "cli-1", idempotency_key="k").json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True and second["message_id"] == first["message_id"]
    assert send_outcome.live_outcome(second, "steer") == "duplicate"
    assert len(tmp_db.list_pending_live_messages("cli-1")) == 1


def test_a_duplicate_never_waits_for_a_reply(api, tmp_db):
    """A retry enqueues nothing, so there is no turn to wait for: it returns at
    once, never blocking or borrowing a later turn as its reply."""
    from agent_bridge.live_representation import LiveEventStore

    api.app.state.live_event_store = LiveEventStore()
    _register(tmp_db, "cli-1")
    first = _post(api, "cli-1", idempotency_key="k").json()
    started = time.monotonic()
    retry = _post(api, "cli-1", idempotency_key="k", wait=True, wait_timeout=3)
    assert time.monotonic() - started < 2  # it didn't sit out the reply wait
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["duplicate"] is True and body["message_id"] == first["message_id"]
    assert body["replied"] is False


def test_other_errors_are_not_classified():
    assert send_outcome.refusal_from_live_error(BridgeClientError(500, "boom"), "t") is None
    assert send_outcome.refusal_from_live_error(BridgeClientError(409, "something else"), "t") is None


# -- the CLI -----------------------------------------------------------------


class _LiveClient:
    def __init__(self, *, result=None, error=None):
        self._result, self._error = result or {}, error

    def daemon_supports(self, _version):
        return True

    def resolve_live_session(self, _handle):
        return {"session_id": "live-1"}

    def send_live_message(self, session_id, **_kw):
        if self._error:
            raise self._error
        return {"ok": True, "session_id": session_id, "message_id": 7, **self._result}


def _send_args(**kw):
    base = dict(target="wt-target", prompt="hi", prompt_file=None, new=False, json=True,
                no_wait=True, delivery="steer", expected_session_id=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _run(monkeypatch, capsys, client, **kw):
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda _a: "peer")
    monkeypatch.setattr(m, "_live_reply_to", lambda _a: "wt-caller")
    code = 0
    try:
        m._cmd_send(_send_args(**kw))
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, json.loads(out.out), out.err


@pytest.mark.parametrize("delivery,outcome", [
    ("queue", "queued"), ("steer", "steered"), ("interrupt", "interrupted")])
def test_live_delivery_reports_its_mode_and_keeps_the_existing_keys(monkeypatch, capsys, delivery, outcome):
    code, out, _ = _run(monkeypatch, capsys, _LiveClient(), delivery=delivery)
    assert code == 0
    assert out["outcome"] == outcome and out["delivered"] is True and out["message_id"] == 7


def test_live_duplicate_is_accepted(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, _LiveClient(result={"duplicate": True}))
    assert (code, out["outcome"]) == (0, "duplicate")


def test_a_keyed_send_to_an_older_daemon_does_not_guess(monkeypatch, capsys):
    """An older daemon's response has no duplicate field: a keyed retry can't be
    told from a first delivery, so it is never reported as a fresh one."""
    code, out, _ = _run(monkeypatch, capsys, _LiveClient(), idempotency_key="k")
    assert (code, out["outcome"], out["duplicate"]) == (0, "accepted", None)


def test_an_unkeyed_send_to_an_older_daemon_is_unaffected(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, _LiveClient())
    assert (code, out["outcome"]) == (0, "steered") and "duplicate" not in out


def test_a_keyed_first_delivery_on_a_current_daemon(monkeypatch, capsys):
    code, out, _ = _run(monkeypatch, capsys, _LiveClient(result={"duplicate": False}), idempotency_key="k")
    assert (code, out["outcome"]) == (0, "steered")


def test_live_refusal_prints_one_typed_document_and_exits_distinctly(monkeypatch, capsys):
    error = BridgeClientError(409, "live session x was superseded by y; refusing delivery")
    code, out, err = _run(monkeypatch, capsys, _LiveClient(error=error))
    assert code == 69
    assert out == {"outcome": "refused_unavailable", "target": "live-1", "retryable": True,
                   "reason": "superseded", "error": error.detail}
    assert "[FAIL]" in err


def test_live_refusal_without_json_keeps_text_and_the_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(m, "_get_client", lambda: _LiveClient(error=BridgeClientError(404, "live session not found")))
    monkeypatch.setattr(m, "_live_sender_label", lambda _a: "peer")
    monkeypatch.setattr(m, "_live_reply_to", lambda _a: "wt-caller")
    with pytest.raises(SystemExit) as exc:
        m._cmd_send(_send_args(json=False))
    assert exc.value.code == 69
    out = capsys.readouterr()
    assert out.out == "" and "live session not found" in out.err


def test_an_unclassified_error_still_propagates(monkeypatch, capsys):
    monkeypatch.setattr(m, "_get_client", lambda: _LiveClient(error=BridgeClientError(500, "boom")))
    with pytest.raises(BridgeClientError):
        m._cmd_send(_send_args())


class _PromptClient:
    def __init__(self, result):
        self._result = result

    def daemon_supports(self, _version):
        return True

    def resolve_live_session(self, _handle):
        return None

    def submit_prompt(self, session_id, prompt, **_kw):
        return dict(self._result)


@pytest.mark.parametrize("result,outcome", [
    ({"turn_index": 3}, "delivered"), ({"queued": True, "queue_id": 4, "position": 1}, "queued")])
def test_prompt_path_outcomes_and_stdout_is_only_json(monkeypatch, capsys, result, outcome):
    def resolve(_client, _target, force=False):
        print("[>] Resuming stopped session s-1...")  # progress text must not reach stdout
        return "s-1"

    monkeypatch.setattr(m, "_resolve_target", resolve)
    monkeypatch.setattr(m, "_caller_id_for", lambda _a: "caller")
    monkeypatch.setattr(m, "_connection_identity", lambda _c, sid: {"session_id": sid})
    monkeypatch.setattr(stc, "_mark_resume_if_behind", lambda *a, **k: False)
    code, out, err = _run(monkeypatch, capsys, _PromptClient(result), queue=outcome == "queued")
    assert code == 0 and out["outcome"] == outcome and out["session_id"] == "s-1"
    assert "Resuming stopped session" in err


def test_an_unknown_target_is_refused_unavailable_not_silent(monkeypatch, capsys):
    """A stale id that is neither a session nor an agent: one typed document."""
    def unknown(_client, target, force=False):
        print(f"[FAIL] '{target}' is not a known agent name or session ID", file=__import__("sys").stderr)
        raise SystemExit(m._SEND_UNAVAILABLE_EXIT)

    monkeypatch.setattr(m, "_resolve_target", unknown)
    monkeypatch.setattr(m, "_caller_id_for", lambda _a: "caller")
    code, out, _ = _run(monkeypatch, capsys, _PromptClient({}))
    assert code == 69 and out["outcome"] == "refused_unavailable" and out["reason"] == "not_found"


def test_the_real_resolver_exits_unavailable_for_an_unknown_target(monkeypatch):
    class Empty:
        def get_session(self, _t):
            raise BridgeClientError(404, "nope")

        def list_agents(self):
            return []

    def no_agent(*_a, **_k):
        raise BridgeClientError(404, "no agent")

    monkeypatch.setattr(m, "_start_agent_session", no_agent)
    monkeypatch.setattr(m, "_resolve_read_worktree_session", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        stc._resolve_target(Empty(), "stale-id")
    assert exc.value.code == 69


def test_a_session_gone_between_resolve_and_submit_is_refused_unavailable(monkeypatch, capsys):
    class Gone(_PromptClient):
        def submit_prompt(self, session_id, prompt, **_kw):
            raise BridgeClientError(404, f"Session {session_id} not found")

    monkeypatch.setattr(m, "_resolve_target", lambda *a, **k: "s-1")
    monkeypatch.setattr(m, "_caller_id_for", lambda _a: "caller")
    monkeypatch.setattr(stc, "_mark_resume_if_behind", lambda *a, **k: False)
    code, out, _ = _run(monkeypatch, capsys, Gone({}))
    assert code == 69 and out["outcome"] == "refused_unavailable" and out["reason"] == "not_found"


def test_a_turn_that_starts_between_the_check_and_the_submit_is_refused_busy(monkeypatch, capsys):
    class Racing(_PromptClient):
        def submit_prompt(self, session_id, prompt, **_kw):
            raise BridgeClientError(409, "Session s-1 is running, not idle")

    monkeypatch.setattr(m, "_resolve_target", lambda *a, **k: "s-1")
    monkeypatch.setattr(m, "_caller_id_for", lambda _a: "caller")
    monkeypatch.setattr(stc, "_mark_resume_if_behind", lambda *a, **k: False)
    code, out, _ = _run(monkeypatch, capsys, Racing({}))
    assert code == 75 and out["outcome"] == "refused_busy"


def test_a_busy_target_is_refused_busy_with_exit_75(monkeypatch, capsys):
    def busy(_client, _target, force=False):
        print("[BUSY] running a turn", file=__import__("sys").stderr)
        raise SystemExit(m._SEND_BUSY_EXIT)

    monkeypatch.setattr(m, "_resolve_target", busy)
    monkeypatch.setattr(m, "_caller_id_for", lambda _a: "caller")
    code, out, _ = _run(monkeypatch, capsys, _PromptClient({}))
    assert code == 75
    assert out["outcome"] == "refused_busy" and out["retryable"] is True and out["target"] == "wt-target"


def test_a_usage_error_is_not_a_typed_refusal(monkeypatch, capsys):
    monkeypatch.setattr(m, "_get_client", lambda: _LiveClient())
    with pytest.raises(SystemExit) as exc:
        m._cmd_send(_send_args(prompt="   "))  # an empty live message is a usage error
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
