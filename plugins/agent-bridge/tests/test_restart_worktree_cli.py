"""Tests for `agent-bridge restart-worktree` (the reclaim sequence's stop
half, agent-bridge-cold-resume Phase 3, #6744) -- a thin CLI wrapper over
POST /api/v1/worktrees/{id}/restart."""

from __future__ import annotations

import argparse

from agent_bridge import __main__ as m


class _FakeClient:
    def __init__(self, result):
        self._result = result
        self.calls: list[tuple[str, bool]] = []

    def restart_worktree(self, worktree_id, *, force=False, request_timeout=None):
        self.calls.append((worktree_id, force))
        return self._result


def _args(worktree_id, *, force=False, json=False):
    return argparse.Namespace(worktree_id=worktree_id, force=force, json=json)


def test_parser_wires_restart_worktree_verb():
    parser = m.build_parser()
    args = parser.parse_args(["restart-worktree", "wt-1", "--force", "--json"])
    assert args.worktree_id == "wt-1"
    assert args.force is True
    assert args.json is True


def test_graceful_stop_reports_ok(monkeypatch, capsys):
    from agent_bridge import restart_worktree_cli

    client = _FakeClient(
        {"worktree_id": "wt-1", "had_session": True, "method": "graceful", "ok": True}
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    restart_worktree_cli.cmd_restart_worktree(_args("wt-1"))
    assert client.calls == [("wt-1", False)]
    assert "quit gracefully" in capsys.readouterr().out


def test_hard_stop_reports_ok(monkeypatch, capsys):
    from agent_bridge import restart_worktree_cli

    client = _FakeClient(
        {"worktree_id": "wt-1", "had_session": True, "method": "hard", "ok": True}
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    restart_worktree_cli.cmd_restart_worktree(_args("wt-1", force=True))
    assert client.calls == [("wt-1", True)]
    assert "hard-stopped" in capsys.readouterr().out


def test_no_session_reports_nothing_to_stop(monkeypatch, capsys):
    from agent_bridge import restart_worktree_cli

    client = _FakeClient(
        {"worktree_id": "wt-1", "had_session": False, "method": "none", "ok": True}
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    restart_worktree_cli.cmd_restart_worktree(_args("wt-1"))
    assert "nothing to stop" in capsys.readouterr().out


def test_failed_stop_exits_nonzero(monkeypatch, capsys):
    import pytest

    from agent_bridge import restart_worktree_cli

    client = _FakeClient(
        {"worktree_id": "wt-1", "had_session": True, "method": "failed", "ok": False}
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    with pytest.raises(SystemExit) as ei:
        restart_worktree_cli.cmd_restart_worktree(_args("wt-1"))
    assert ei.value.code == 1
    assert "failed to stop" in capsys.readouterr().err


def test_json_output_emits_raw_payload(monkeypatch, capsys):
    from agent_bridge import restart_worktree_cli

    payload = {"worktree_id": "wt-1", "had_session": True, "method": "graceful", "ok": True}
    client = _FakeClient(payload)
    monkeypatch.setattr(m, "_get_client", lambda: client)
    restart_worktree_cli.cmd_restart_worktree(_args("wt-1", json=True))
    import json as _json

    assert _json.loads(capsys.readouterr().out) == payload
