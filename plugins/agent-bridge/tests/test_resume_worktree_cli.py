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
    def __init__(self, *, session_resume=None, worktree_resume=None, agents=None):
        self._session_resume = session_resume
        self._worktree_resume = worktree_resume
        self._agents = list(agents or [])
        self.session_calls: list[str] = []
        self.worktree_calls: list[tuple[str, bool]] = []

    def list_agents(self):
        return list(self._agents)

    def resume_session(self, session_id, *, request_timeout=None):
        self.session_calls.append(session_id)
        if isinstance(self._session_resume, Exception):
            raise self._session_resume
        return self._session_resume

    def resume_worktree(
        self,
        worktree_id,
        *,
        reclaim=False,
        request_timeout=None,
    ):
        self.worktree_calls.append((worktree_id, reclaim))
        result = (
            self._worktree_resume(worktree_id, reclaim=reclaim)
            if callable(self._worktree_resume)
            else self._worktree_resume
        )
        if isinstance(result, Exception):
            raise result
        return result


def _args(target, *, force=False, json=False):
    return argparse.Namespace(session_id=target, force=force, json=json)


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


def test_singleton_repo_target_falls_back_to_anchor_key(monkeypatch, capsys):
    client = _FakeClient(
        session_resume=BridgeClientError(404, "not found"),
        worktree_resume=lambda worktree_id, reclaim=False: (
            BridgeClientError(404, "No session found")
            if worktree_id != "llama.cpp@anchor"
            else {"status": "idle", "session_id": "owned-anchor-1"}
        ),
        agents=[{"name": "llama.cpp@Lambda-Core", "project": "llama.cpp"}],
    )
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        "agent_bridge.resume_handoff_cli.find_singleton_repo",
        lambda target: (
            type("Repo", (), {"name": "llama.cpp", "path": "/repo/llama.cpp"})()
            if target == "llama.cpp"
            else None
        ),
    )

    m._cmd_resume(_args("llama.cpp@Lambda-Core"))

    assert client.worktree_calls == [
        ("llama.cpp@Lambda-Core", False),
        ("llama.cpp@anchor", False),
    ]
    assert "Repo llama.cpp loaded as owned session owned-anchor-1" in (
        capsys.readouterr().out
    )


class TestResumeJsonOutput:
    """#6744 Phase 3: --json is the sole headless take-over primitive now
    that `create --reclaim` is gone -- a caller (agent-dispatch) needs the
    session id back machine-readably, not scraped from a human summary line.
    """

    def test_force_take_over_emits_json_session_id(self, monkeypatch, capsys):
        client = _FakeClient(
            worktree_resume={"status": "idle", "session_id": "owned-9"},
        )
        _patch_client(monkeypatch, client)

        m._cmd_resume(_args("wt-6b68", force=True, json=True))

        import json as _json

        out = _json.loads(capsys.readouterr().out)
        assert out == {
            "session_id": "owned-9", "status": "idle",
            "verb": "took over", "worktree_id": "wt-6b68",
        }

    def test_live_holder_refusal_emits_json_error(self, monkeypatch, capsys):
        client = _FakeClient(
            session_resume=BridgeClientError(404, "not found"),
            worktree_resume=BridgeClientError(
                409, {"reason": "live_cli_holds_worktree", "session_id": "live-7"}
            ),
        )
        _patch_client(monkeypatch, client)

        with pytest.raises(SystemExit) as ei:
            m._cmd_resume(_args("wt-6b68", json=True))
        assert ei.value.code == 1

        import json as _json

        out = _json.loads(capsys.readouterr().out)
        assert out["reason"] == "live_cli_holds_worktree"
        assert out["session_id"] == "live-7"

    def test_resumed_owned_session_emits_json(self, monkeypatch, capsys):
        client = _FakeClient(session_resume={"status": "idle"})
        _patch_client(monkeypatch, client)

        m._cmd_resume(_args("sess-1", json=True))

        import json as _json

        out = _json.loads(capsys.readouterr().out)
        assert out == {"session_id": "sess-1", "status": "idle", "verb": "resumed"}
