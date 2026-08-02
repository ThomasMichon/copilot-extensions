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
    assert args.target == []
    args2 = m.build_parser().parse_args(["claims", "wt-x", "--json"])
    assert args2.target == ["wt-x"] and args2.json is True


def test_claims_release_parser():
    args = m.build_parser().parse_args(
        ["claims", "release", "m/p/wt-B", "--remove"])
    assert args.target == ["release", "m/p/wt-B"]
    assert args.remove is True


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

    captured = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "assigned": [{"id": "t1", "status": "queued", "title": "A"}],
            "owned": [{"id": "t2", "status": "started", "title": "B"}],
        })
        stderr = ""

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(m.subprocess, "run", _run)
    res = m._inbound_claims("lambda-core", "wt-a", str(tmp_path))
    assert res["available"] is True
    assert [t["id"] for t in res["assigned"]] == ["t1"]
    assert [t["id"] for t in res["owned"]] == ["t2"]
    # Regression: agent-dispatch emits JSON by default and rejects a --json flag,
    # so the command must NOT pass one (worktree-status has no --json).
    assert "--json" not in captured["cmd"]
    assert "worktree-status" in captured["cmd"]


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
    rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["worktree_id"] == "wt-A"
    assert out["owner_ref"] == "lambda-core/aperture-labs/wt-owner#s1"
    assert len(out["outbound"]) == 1
    assert out["outbound"][0]["ref"] == "lambda-core/copilot-extensions/wt-B"
    assert out["inbound"]["available"] is False


def test_claims_empty_ledger_json(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(target=[], json=True))
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
    rc = m.cmd_claims(argparse.Namespace(target=["ghost"], json=True))
    assert rc == 1


def test_claims_human_output(monkeypatch, tmp_path):
    claim = tracking.ResourceClaim(
        kind="worktree", ref="lambda-core/copilot-extensions/wt-B")
    _seed(tmp_path, monkeypatch, resources=[claim])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=False))
    assert rc == 0
    text = buf.getvalue()
    assert "Claim ledger for wt-A" in text
    assert "lambda-core/copilot-extensions/wt-B" in text
    assert "Inbound" in text


# --- claims release ---------------------------------------------------------

def _release_args(ref, *, remove=False, json_=True):
    return argparse.Namespace(
        target=["release", ref], remove=remove, release_worktree=None,
        json=json_)


def test_claims_release_marks_released(monkeypatch, tmp_path, capfd):
    ref = "lambda-core/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(kind="worktree", ref=ref)])
    rc = m.cmd_claims(_release_args(ref))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["action"] == "released" and out["ref"] == ref
    # Reload: the claim persists but is no longer live.
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1
    assert rec.resources[0].state == "released"
    assert rec.live_resources == []


def test_claims_release_remove_drops_entry(monkeypatch, tmp_path, capfd):
    ref = "lambda-core/copilot-extensions/wt-B"
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(kind="worktree", ref=ref)])
    rc = m.cmd_claims(_release_args(ref, remove=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["action"] == "removed"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources == []


def test_claims_release_unknown_ref(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(
              kind="worktree", ref="lambda-core/copilot-extensions/wt-B")])
    rc = m.cmd_claims(_release_args("lambda-core/copilot-extensions/wt-Z"))
    assert rc == 1


def test_claims_release_missing_ref(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["release"], remove=False, release_worktree=None, json=True))
    assert rc == 2
