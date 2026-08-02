"""Tests for the `agent-worktrees claims` ledger command (resource-claims)."""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout

from agent_worktrees import __main__ as m
from agent_worktrees import tracking


# --- parser + registration --------------------------------------------------

def test_claims_parser_optional_id():
    args = m.build_parser().parse_args(["claims"])
    assert args.command == "claims"
    assert args.worktree_id is None
    args2 = m.build_parser().parse_args(["claims", "wt-x", "--json"])
    assert args2.worktree_id == "wt-x" and args2.json is True


def test_claims_registered():
    assert m.COMMAND_MAP["claims"] is m.cmd_claims
    assert m._WORKTREE_VERBS.get("claims") == "claims"


# --- _inbound_claims degradation --------------------------------------------

def test_inbound_unavailable_without_dispatch(monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    res = m._inbound_claims("lambda-core", "wt-a", "")
    assert res["available"] is False
    assert "not installed" in res["reason"]


def test_inbound_parses_dispatch_output(monkeypatch, tmp_path):
    monkeypatch.setattr(m.shutil, "which", lambda name: "agent-dispatch")

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "assigned": [{"id": "t1", "status": "queued", "title": "A"}],
            "owned": [{"id": "t2", "status": "started", "title": "B"}],
        })
        stderr = ""

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _Proc())
    res = m._inbound_claims("lambda-core", "wt-a", str(tmp_path))
    assert res["available"] is True
    assert [t["id"] for t in res["assigned"]] == ["t1"]
    assert [t["id"] for t in res["owned"]] == ["t2"]


def test_inbound_handles_dispatch_error(monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: "agent-dispatch")

    class _Proc:
        returncode = 2
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _Proc())
    res = m._inbound_claims("lambda-core", "wt-a", "")
    assert res["available"] is False and res["reason"] == "boom"


# --- cmd_claims end-to-end --------------------------------------------------

def _seed(tmp_path, monkeypatch, *, owner_ref=None, resources=None):
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    wdir = tmp_path / "wt-A"
    wdir.mkdir(exist_ok=True)
    rec = tracking.create_new_record(
        "wt-A", "worktree/wt-A", str(wdir), "aperture-labs",
        "lambda-core", "wsl", tdir, owner_ref=owner_ref,
    )
    if resources:
        for c in resources:
            tracking.add_resource_claim(rec, c, save=False)
        tracking.save_record(rec, tdir / "wt-A.yaml")
    # config + inference stubs
    import types
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="lambda-core"))
    monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg_: wid or "wt-A")
    monkeypatch.setattr(m, "_inbound_claims",
                        lambda machine, wid, cwd: {"available": False,
                                                   "reason": "stubbed"})
    return tdir


def test_claims_json_outbound_and_owner(monkeypatch, tmp_path, capfd):
    claim = tracking.ResourceClaim(
        kind="worktree", ref="lambda-core/copilot-extensions/wt-B",
        created_at="2026-07-31T00:00:00")
    _seed(tmp_path, monkeypatch,
          owner_ref="lambda-core/aperture-labs/wt-owner#s1",
          resources=[claim])
    rc = m.cmd_claims(argparse.Namespace(worktree_id="wt-A", json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["worktree_id"] == "wt-A"
    assert out["owner_ref"] == "lambda-core/aperture-labs/wt-owner#s1"
    assert len(out["outbound"]) == 1
    assert out["outbound"][0]["ref"] == "lambda-core/copilot-extensions/wt-B"
    assert out["inbound"]["available"] is False


def test_claims_empty_ledger_json(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(worktree_id="wt-A", json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["outbound"] == [] and out["owner_ref"] is None


def test_claims_missing_worktree(monkeypatch, tmp_path):
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    import types
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="m"))
    monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg_: "ghost")
    rc = m.cmd_claims(argparse.Namespace(worktree_id="ghost", json=True))
    assert rc == 1


def test_claims_human_output(monkeypatch, tmp_path):
    claim = tracking.ResourceClaim(
        kind="worktree", ref="lambda-core/copilot-extensions/wt-B")
    _seed(tmp_path, monkeypatch, resources=[claim])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.cmd_claims(argparse.Namespace(worktree_id="wt-A", json=False))
    assert rc == 0
    text = buf.getvalue()
    assert "Claim ledger for wt-A" in text
    assert "lambda-core/copilot-extensions/wt-B" in text
    assert "Inbound" in text
