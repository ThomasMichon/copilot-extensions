"""Tests for the `agent-worktrees follow-ups` command (worktree-finality-and-
obligations, Phase 3 -- the itemized follow-up ledger)."""

from __future__ import annotations

import argparse
import io
import json
import types
from contextlib import redirect_stdout

from agent_worktrees import __main__ as m
from agent_worktrees import state_root
from agent_worktrees import tracking


def _seed(tmp_path, monkeypatch):
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    wdir = tmp_path / "wt-A"
    wdir.mkdir(exist_ok=True)
    tracking.create_new_record(
        "wt-A", "worktree/wt-A", str(wdir), "test-chamber",
        "anomalous-potato", "wsl", tdir,
    )
    monkeypatch.setattr(
        "agent_worktrees.config.load_config",
        lambda *a, **k: types.SimpleNamespace(machine="anomalous-potato"),
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "test-chamber", False, False, True)
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(True, "ready", ready_root),
    )
    monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg_: wid or "wt-A")
    return tdir


def _args(target, *, refs=None, result_ref=None, reason="", worktree=None, json_=True):
    return argparse.Namespace(
        target=target, follow_up_refs=refs, follow_up_result_ref=result_ref,
        follow_up_reason=reason, follow_up_worktree=worktree, json=json_,
    )


def test_registered():
    assert m.COMMAND_MAP["follow-ups"] is m.cmd_follow_ups
    assert m._WORKTREE_VERBS.get("follow-ups") == "follow-ups"


def test_add_journals_open_item(tmp_path, monkeypatch, capfd):
    tdir = _seed(tmp_path, monkeypatch)
    rc = m.cmd_follow_ups(_args(["add", "deploy", "the", "thing"]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["summary"] == "deploy the thing"
    assert out["state"] == "open"
    assert out["reopened"] is False
    rec = tracking.load_record(tdir / "wt-A.yaml")
    assert len(rec.follow_ups) == 1


def test_add_with_refs(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_follow_ups(_args(["add", "fix", "bug"], refs=["issue:org/repo#9"]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["refs"] == [{"kind": "issue", "ref": "org/repo#9"}]


def test_add_reopens_finalized_owner(tmp_path, monkeypatch, capfd):
    tdir = _seed(tmp_path, monkeypatch)
    path = tdir / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalized"
    tracking.save_record(rec, path)
    rc = m.cmd_follow_ups(_args(["add", "resume", "work"]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["reopened"] is True
    assert tracking.load_record(path).status == "active"


def test_add_rejects_finalizing_owner(tmp_path, monkeypatch, capfd):
    tdir = _seed(tmp_path, monkeypatch)
    path = tdir / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.status = "finalizing"
    tracking.save_record(rec, path)
    rc = m.cmd_follow_ups(_args(["add", "x"]))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert "frozen" in out["error"]


def test_resolve_and_dismiss(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_follow_ups(_args(["add", "item", "one"]))
    added = json.loads(capfd.readouterr().out)

    rc = m.cmd_follow_ups(_args(["resolve", added["id"]], result_ref="org/repo#PR"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["state"] == "resolved" and out["result_ref"] == "org/repo#PR"

    m.cmd_follow_ups(_args(["add", "item", "two"]))
    added2 = json.loads(capfd.readouterr().out)
    rc = m.cmd_follow_ups(_args(["dismiss", added2["id"]], reason="not needed"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["state"] == "dismissed" and out["reason"] == "not needed"


def test_dismiss_requires_reason(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_follow_ups(_args(["add", "x"]))
    capfd.readouterr()
    rc = m.cmd_follow_ups(_args(["dismiss", "fu-whatever"], reason=""))
    assert rc == 2


def test_resolve_unknown_id(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_follow_ups(_args(["resolve", "fu-missing"]))
    assert rc == 1
    out = json.loads(capfd.readouterr().out)
    assert "no such follow-up" in out["error"]


def test_show_reports_open_count(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_follow_ups(_args(["add", "one"]))
    capfd.readouterr()
    m.cmd_follow_ups(_args(["add", "two"]))
    capfd.readouterr()
    rc = m.cmd_follow_ups(_args([]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["open_count"] == 2
    assert len(out["follow_ups"]) == 2


def test_show_human_output_lists_legacy_boolean(tmp_path, monkeypatch, capfd):
    tdir = _seed(tmp_path, monkeypatch)
    path = tdir / "wt-A.yaml"
    rec = tracking.load_record(path)
    rec.follow_up = True
    tracking.save_record(rec, path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.cmd_follow_ups(_args([], json_=False))
    assert rc == 0
    out = buf.getvalue()
    assert "1 open" in out
    assert "legacy boolean" in out


# --- activity.log_event instrumentation (#3113) -----------------------------

def test_add_logs_follow_up_added(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    rc = m.cmd_follow_ups(_args(["add", "deploy", "the", "thing"]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert len(logged) == 1
    assert logged[0][0] == ("follow_up_added",)
    assert logged[0][1]["worktree_id"] == "wt-A"
    assert logged[0][1]["follow_up_id"] == out["id"]
    assert logged[0][1]["summary"] == "deploy the thing"
    assert logged[0][1]["reopened"] is False


def test_resolve_and_dismiss_log_events(tmp_path, monkeypatch, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_follow_ups(_args(["add", "item", "one"]))
    added = json.loads(capfd.readouterr().out)

    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    rc = m.cmd_follow_ups(_args(["resolve", added["id"]], result_ref="org/repo#PR"))
    assert rc == 0
    capfd.readouterr()
    assert logged == [(("follow_up_resolved",), {
        "worktree_id": "wt-A", "follow_up_id": added["id"],
        "result_ref": "org/repo#PR"})]

    m.cmd_follow_ups(_args(["add", "item", "two"]))
    added2 = json.loads(capfd.readouterr().out)
    logged.clear()
    rc = m.cmd_follow_ups(_args(["dismiss", added2["id"]], reason="not needed"))
    assert rc == 0
    assert logged == [(("follow_up_dismissed",), {
        "worktree_id": "wt-A", "follow_up_id": added2["id"],
        "reason": "not needed"})]
