"""Tests for `agent-bridge handoff*` verbs.

`handoff <target>` first tries an owned ACP session; on 404 it treats the
target as a worktree handle and hands off that worktree's current session.
Mirrors the resume verb's resolution order.
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class _FakeClient:
    def __init__(
        self, *, session_handoff=None, worktree_handoff=None, handoff_request=None
    ):
        self._session_handoff = session_handoff
        self._worktree_handoff = worktree_handoff
        self._handoff_request = handoff_request
        self.session_calls: list[tuple[str, str | None, bool]] = []
        self.worktree_calls: list[tuple[str, str | None, bool]] = []
        self.request_calls: list[tuple[str, str, str, str | None]] = []

    def handoff_session(self, session_id, *, reason=None, seed=True):
        self.session_calls.append((session_id, reason, seed))
        if isinstance(self._session_handoff, Exception):
            raise self._session_handoff
        return self._session_handoff

    def handoff_worktree(self, worktree_id, *, reason=None, seed=True):
        self.worktree_calls.append((worktree_id, reason, seed))
        if isinstance(self._worktree_handoff, Exception):
            raise self._worktree_handoff
        return self._worktree_handoff

    def handoff_request(
        self, worktree_id, *, session_id, seed_text, handoff_token=None
    ):
        self.request_calls.append(
            (worktree_id, session_id, seed_text, handoff_token)
        )
        if isinstance(self._handoff_request, Exception):
            raise self._handoff_request
        return self._handoff_request


def _args(target, *, reason=None, no_seed=False):
    return argparse.Namespace(session_id=target, reason=reason, no_seed=no_seed)


def _request_args(
    *,
    worktree_id="wt-1",
    session_id="sess-1",
    handoff_token="task:1",
    seed="/consume-handoff task:1",
    json=False,
):
    return argparse.Namespace(
        worktree_id=worktree_id,
        session_id=session_id,
        handoff_token=handoff_token,
        seed=seed,
        json=json,
    )


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(m, "_get_client", lambda *a, **k: client)


def test_owned_session_handoff_wins(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff={"status": "idle", "session_id": "sess-2"}
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff(_args("sess-1", reason="ctx"))

    assert client.session_calls == [("sess-1", "ctx", True)]
    assert client.worktree_calls == []  # never fell through
    assert "Session sess-1 handed off -> successor sess-2 (idle)" in (
        capsys.readouterr().out
    )


def test_worktree_fallback_on_404(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff=BridgeClientError(404, "Session wt-1 not found"),
        worktree_handoff={"status": "idle", "session_id": "owned-9"},
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff(_args("wt-1", no_seed=True))

    assert client.session_calls == [("wt-1", None, False)]  # seed disabled
    assert client.worktree_calls == [("wt-1", None, False)]
    assert "Worktree wt-1 handed off -> successor owned-9 (idle)" in (
        capsys.readouterr().out
    )


def test_conflict_409_is_not_worktree_fallback(monkeypatch, capsys):
    # A 409 (mid-turn / command agent) is a hard failure, NOT a signal to try
    # the worktree path (only 404 = "not an owned session" falls through).
    client = _FakeClient(
        session_handoff=BridgeClientError(409, "cannot hand off mid-turn"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff(_args("sess-1"))
    assert client.worktree_calls == []


def test_worktree_not_found_reports_and_exits(monkeypatch, capsys):
    client = _FakeClient(
        session_handoff=BridgeClientError(404, "not an owned session"),
        worktree_handoff=BridgeClientError(404, "no session for worktree"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff(_args("ghost"))
    err = capsys.readouterr().err
    assert "neither a bridge-owned session nor a worktree" in err


def test_handoff_request_json_round_trips(monkeypatch, capsys):
    client = _FakeClient(
        handoff_request={
            "session_id": "succ-2",
            "acp_session_id": "acp-succ-2",
            "status": "idle",
        }
    )
    _patch_client(monkeypatch, client)

    m._cmd_handoff_request(_request_args(json=True))

    assert client.request_calls == [
        ("wt-1", "sess-1", "/consume-handoff task:1", "task:1")
    ]
    payload = m.json.loads(capsys.readouterr().out)
    assert payload == {
        "accepted": True,
        "worktree_id": "wt-1",
        "requested_session_id": "sess-1",
        "handoff_token": "task:1",
        "successor_session_id": "succ-2",
        "successor_acp_session_id": "acp-succ-2",
        "successor_status": "idle",
    }


def test_handoff_request_no_session_degrades_cleanly(monkeypatch, capsys):
    client = _FakeClient(
        handoff_request=BridgeClientError(
            404, "No current session found for worktree wt-1 matching sess-1"
        )
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit):
        m._cmd_handoff_request(_request_args())
    err = capsys.readouterr().err
    assert "No current session for worktree wt-1 matches sess-1" in err


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _check_args(*, worktree_id=None, all=False, execute=False, json=False):
    return argparse.Namespace(worktree_id=worktree_id, all=all, execute=execute, json=json)


class TestHandoffCheck:
    """``agent-bridge handoff-check`` -- a thin passthrough to
    ``agent-worktrees handoffs-check``, the ground-layer owner of the mux/pane
    primitives a CLI-hosted worktree's cutover depends on."""

    def test_no_agent_worktrees_on_path_fails_fast(self, monkeypatch):
        monkeypatch.setattr(m.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit):
            m._cmd_handoff_check(_check_args(worktree_id="wt-1"))

    def test_passes_worktree_id_and_execute_through(self, monkeypatch):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _FakeCompletedProcess(
                m.json.dumps({"checked": 1, "found": 0, "executed": True, "findings": []})
            )

        monkeypatch.setattr(m.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_handoff_check(_check_args(worktree_id="wt-1", execute=True, json=True))

        assert exc_info.value.code == 0
        assert captured["argv"] == [
            "/usr/bin/agent-worktrees", "handoffs-check", "--json",
            "--worktree-id", "wt-1", "--execute",
        ]

    def test_passes_all_flag_through(self, monkeypatch):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _FakeCompletedProcess(
                m.json.dumps({"checked": 3, "found": 0, "executed": False, "findings": []})
            )

        monkeypatch.setattr(m.subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            m._cmd_handoff_check(_check_args(all=True, json=True))

        assert captured["argv"] == [
            "/usr/bin/agent-worktrees", "handoffs-check", "--json", "--all",
        ]

    def test_json_output_round_trips_the_underlying_payload(self, monkeypatch, capsys):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        payload = {
            "checked": 1, "found": 1, "executed": True,
            "findings": [{
                "worktree_id": "wt-1", "predecessor_session_id": "old-sess",
                "retire_pane": "%9", "executed": True, "retired": True,
            }],
        }
        monkeypatch.setattr(
            m.subprocess, "run",
            lambda argv, **kwargs: _FakeCompletedProcess(m.json.dumps(payload)),
        )

        with pytest.raises(SystemExit):
            m._cmd_handoff_check(_check_args(worktree_id="wt-1", execute=True, json=True))

        assert m.json.loads(capsys.readouterr().out) == payload

    def test_human_readable_output_reports_each_finding(self, monkeypatch, capsys):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        payload = {
            "checked": 1, "found": 1, "executed": True,
            "findings": [{
                "worktree_id": "wt-1", "predecessor_session_id": "old-sess",
                "retire_pane": "%9", "executed": True, "retired": True,
            }],
        }
        monkeypatch.setattr(
            m.subprocess, "run",
            lambda argv, **kwargs: _FakeCompletedProcess(m.json.dumps(payload)),
        )

        with pytest.raises(SystemExit):
            m._cmd_handoff_check(_check_args(worktree_id="wt-1", execute=True))

        out = capsys.readouterr().out
        assert "retired predecessor old-sess" in out

    def test_no_findings_reports_clean(self, monkeypatch, capsys):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        monkeypatch.setattr(
            m.subprocess, "run",
            lambda argv, **kwargs: _FakeCompletedProcess(
                m.json.dumps({"checked": 1, "found": 0, "executed": False, "findings": []})
            ),
        )

        with pytest.raises(SystemExit):
            m._cmd_handoff_check(_check_args(worktree_id="wt-1"))

        assert "no stalled predecessor retirements found" in capsys.readouterr().out

    def test_underlying_error_reports_failure_not_success(self, monkeypatch, capsys):
        """Regression: an error payload from agent-worktrees (e.g. an unknown
        worktree) must be surfaced as a failure, not silently read as "no
        findings" (which looked identical: no "findings" key either way)."""
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        monkeypatch.setattr(
            m.subprocess, "run",
            lambda argv, **kwargs: _FakeCompletedProcess(
                m.json.dumps({"error": "Worktree not found: wt-missing"}), returncode=1,
            ),
        )

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_handoff_check(_check_args(worktree_id="wt-missing"))

        assert exc_info.value.code != 0
        assert "Worktree not found: wt-missing" in capsys.readouterr().err

    def test_unparseable_output_reports_failure_not_success(self, monkeypatch, capsys):
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        monkeypatch.setattr(
            m.subprocess, "run",
            lambda argv, **kwargs: _FakeCompletedProcess("not json", returncode=0),
        )

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_handoff_check(_check_args(worktree_id="wt-1"))

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "no stalled predecessor retirements found" not in err
        assert "unparseable" in err

    def test_empty_stdout_nonzero_exit_reports_failure_not_success(self, monkeypatch, capsys):
        """Regression: a nonzero agent-worktrees exit with completely empty
        stdout (no JSON at all) made json.loads fall back to "{}" -- which
        looks identical, from cmd_handoff_check's perspective, to a
        legitimate "no findings" result. Must surface the failure (and its
        stderr) instead of silently reporting success."""
        monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-worktrees")
        proc = _FakeCompletedProcess("", returncode=1)
        proc.stderr = "some underlying failure text"
        monkeypatch.setattr(m.subprocess, "run", lambda argv, **kwargs: proc)

        with pytest.raises(SystemExit) as exc_info:
            m._cmd_handoff_check(_check_args(worktree_id="wt-1"))

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "no stalled predecessor retirements found" not in err
        assert "exited 1" in err
        assert "some underlying failure text" in err

