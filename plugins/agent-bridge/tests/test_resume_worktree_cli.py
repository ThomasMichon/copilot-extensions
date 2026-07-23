"""Tests for `agent-bridge resume` worktree fallback + break-glass take-over.

`resume <target>` first tries an owned ACP session; on 404 it treats the
target as a worktree handle and ensures a live owned session (a *dormant*
worktree is loaded with just a note). A *live* interactive CLI holding the
worktree yields a 409 `live_cli_holds_worktree`, which the CLI turns into a
break-glass refusal unless `--force` is passed.
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class _FakeClient:
    def __init__(self, *, session_resume=None, worktree_resume=None):
        self._session_resume = session_resume
        self._worktree_resume = worktree_resume
        self.session_calls: list[str] = []
        self.worktree_calls: list[tuple[str, bool]] = []

    def resume_session(self, session_id):
        self.session_calls.append(session_id)
        if isinstance(self._session_resume, Exception):
            raise self._session_resume
        return self._session_resume

    def resume_worktree(self, worktree_id, *, reclaim=False):
        self.worktree_calls.append((worktree_id, reclaim))
        if isinstance(self._worktree_resume, Exception):
            raise self._worktree_resume
        return self._worktree_resume


def _args(target, *, force=False):
    return argparse.Namespace(session_id=target, force=force)


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(m, "_get_client", lambda *a, **k: client)


def test_owned_session_resume_wins(monkeypatch, capsys):
    client = _FakeClient(session_resume={"status": "idle"})
    _patch_client(monkeypatch, client)

    m._cmd_resume(_args("sess-1"))

    assert client.session_calls == ["sess-1"]
    assert client.worktree_calls == []  # never fell through to worktree path
    assert "Session sess-1 resumed (idle)" in capsys.readouterr().out


def test_dormant_worktree_loaded_with_note(monkeypatch, capsys):
    # Session resume 404s (not an owned session) -> worktree fallback loads it.
    client = _FakeClient(
        session_resume=BridgeClientError(404, "Session wt-6b68 not found"),
        worktree_resume={"status": "idle", "session_id": "owned-9"},
    )
    _patch_client(monkeypatch, client)

    m._cmd_resume(_args("wt-6b68"))

    assert client.worktree_calls == [("wt-6b68", False)]  # reclaim not forced
    out = capsys.readouterr().out
    assert "Worktree wt-6b68 loaded as owned session owned-9" in out


def test_live_holder_refused_without_force(monkeypatch, capsys):
    client = _FakeClient(
        session_resume=BridgeClientError(404, "not found"),
        worktree_resume=BridgeClientError(
            409, {"reason": "live_cli_holds_worktree", "session_id": "live-7"}
        ),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit) as ei:
        m._cmd_resume(_args("wt-6b68"))
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "BREAK-GLASS" in err
    assert "live-7" in err
    assert "--force" in err


def test_force_takes_over_live_holder(monkeypatch, capsys):
    client = _FakeClient(
        worktree_resume={"status": "idle", "session_id": "owned-9"},
    )
    _patch_client(monkeypatch, client)

    m._cmd_resume(_args("wt-6b68", force=True))

    # --force skips the session-resume attempt and reclaims the worktree.
    assert client.session_calls == []
    assert client.worktree_calls == [("wt-6b68", True)]
    assert "Worktree wt-6b68 took over as owned session owned-9" in (
        capsys.readouterr().out
    )


def test_unknown_target_reports_neither(monkeypatch, capsys):
    client = _FakeClient(
        session_resume=BridgeClientError(404, "not found"),
        worktree_resume=BridgeClientError(404, "No session found"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(SystemExit) as ei:
        m._cmd_resume(_args("bogus"))
    assert ei.value.code == 1
    assert "neither a bridge-owned session nor a recognized worktree" in (
        capsys.readouterr().err
    )
