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
    res = m._inbound_claims("anomalous-potato", "wt-a", "")
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
    res = m._inbound_claims("anomalous-potato", "wt-a", str(tmp_path))
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
    res = m._inbound_claims("anomalous-potato", "wt-a", "")
    assert res["available"] is False and res["reason"] == "boom"


# --- cmd_claims end-to-end --------------------------------------------------

def _seed(tmp_path, monkeypatch, *, owner_ref=None, resources=None):
    tdir = tmp_path / "worktrees"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    wdir = tmp_path / "wt-A"
    wdir.mkdir(exist_ok=True)
    rec = tracking.create_new_record(
        "wt-A", "worktree/wt-A", str(wdir), "test-chamber",
        "anomalous-potato", "wsl", tdir, owner_ref=owner_ref,
    )
    if resources:
        for c in resources:
            tracking.add_resource_claim(rec, c, save=False)
        tracking.save_record(rec, tdir / "wt-A.yaml")
    # config + inference stubs
    import types
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="anomalous-potato"))
    monkeypatch.setattr(m, "_infer_worktree_id", lambda wid, cfg_: wid or "wt-A")
    monkeypatch.setattr(m, "_inbound_claims",
                        lambda machine, wid, cwd: {"available": False,
                                                   "reason": "stubbed"})
    return tdir


def test_claims_json_outbound_and_owner(monkeypatch, tmp_path, capfd):
    claim = tracking.ResourceClaim(
        kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B",
        created_at="2026-07-31T00:00:00")
    _seed(tmp_path, monkeypatch,
          owner_ref="anomalous-potato/test-chamber/wt-owner#s1",
          resources=[claim])
    rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["worktree_id"] == "wt-A"
    assert out["owner_ref"] == "anomalous-potato/test-chamber/wt-owner#s1"
    assert len(out["outbound"]) == 1
    assert out["outbound"][0]["ref"] == "anomalous-potato/copilot-extensions/wt-B"
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
        kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")
    _seed(tmp_path, monkeypatch, resources=[claim])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.cmd_claims(argparse.Namespace(target=["wt-A"], json=False))
    assert rc == 0
    text = buf.getvalue()
    assert "Claim ledger for wt-A" in text
    assert "anomalous-potato/copilot-extensions/wt-B" in text
    assert "Inbound" in text


# --- claims release ---------------------------------------------------------

def _release_args(ref, *, remove=False, json_=True):
    return argparse.Namespace(
        target=["release", ref], remove=remove, release_worktree=None,
        json=json_)


def test_claims_release_marks_released(monkeypatch, tmp_path, capfd):
    ref = "anomalous-potato/copilot-extensions/wt-B"
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
    ref = "anomalous-potato/copilot-extensions/wt-B"
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
              kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")])
    rc = m.cmd_claims(_release_args("anomalous-potato/copilot-extensions/wt-Z"))
    assert rc == 1


def test_claims_release_missing_ref(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["release"], remove=False, release_worktree=None, json=True))
    assert rc == 2


# --- claims add -------------------------------------------------------------

def _add_args(kind, ref, *, note="", json_=True):
    return argparse.Namespace(
        target=["add", kind, ref], note=note, release_worktree=None, json=json_)


def test_claims_add_journals_active_claim(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_args("codespace", "cs-blue", note="example-web"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["kind"] == "codespace" and out["ref"] == "cs-blue"
    assert out["state"] == "active"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1
    c = rec.resources[0]
    assert c.kind == "codespace" and c.ref == "cs-blue" and c.note == "example-web"
    assert c.is_unsettled and c.created_at  # active + timestamped


def test_claims_add_rejects_unknown_kind(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_args("gizmo", "x"))
    assert rc == 2


def test_claims_add_dedups_by_ref(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_claims(_add_args("codespace", "cs-blue"))
    capfd.readouterr()
    m.cmd_claims(_add_args("codespace", "cs-blue", note="second"))
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert len(rec.resources) == 1  # refreshed, not duplicated


def test_claims_add_missing_operands(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(
        target=["add", "codespace"], note="", release_worktree=None, json=True))
    assert rc == 2


# --- claims add --owner-ref (cross-project resolution, 3b-wiring/2) ----------

def _add_ownerref_args(kind, ref, owner_ref, *, note="", json_=True):
    return argparse.Namespace(
        target=["add", kind, ref], note=note, release_worktree=None,
        claim_owner_ref=owner_ref, json=json_)


def _seed_ownerref(tmp_path, monkeypatch, *, machine="anomalous-potato"):
    """Seed a borrowing-worktree record in ITS OWN project dir, and point the
    'current' cwd at a DIFFERENT project (the daemon-cwd gotcha)."""
    import types
    # The borrowing worktree lives in project 'example-web'.
    owner_proj_dir = tmp_path / ".example-web"
    owner_wt_dir = owner_proj_dir / "worktrees"
    owner_wt_dir.mkdir(parents=True, exist_ok=True)
    wdir = tmp_path / "borrower"
    wdir.mkdir(exist_ok=True)
    tracking.create_new_record(
        "wt-borrower", "worktree/wt-borrower", str(wdir), "example-web",
        machine, "wsl", owner_wt_dir,
    )
    # The 'current' project (daemon cwd) is a different one entirely.
    cur_tdir = tmp_path / ".dotfiles" / "worktrees"
    cur_tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: cur_tdir)
    monkeypatch.setattr("agent_worktrees.config.project_dir",
                        lambda name=None: tmp_path / f".{name}")
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine=machine))
    monkeypatch.setattr(m, "_inbound_claims",
                        lambda machine, wid, cwd: {"available": False,
                                                   "reason": "stubbed"})
    return owner_wt_dir


def test_claims_add_owner_ref_lands_on_cross_project_record(monkeypatch, tmp_path, capfd):
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-xyz", "anomalous-potato/example-web/wt-borrower", note="borrow"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["worktree_id"] == "wt-borrower" and out["ref"] == "cs-xyz"
    # Landed on the BORROWING project's record, not the current (dotfiles) one.
    rec = tracking.load_record(owner_wt_dir / "wt-borrower.yaml")
    assert [c.ref for c in rec.resources] == ["cs-xyz"]
    assert rec.resources[0].is_unsettled  # active
    # The current-project tracking dir got NOTHING.
    assert not list((tmp_path / ".dotfiles" / "worktrees").glob("*.yaml"))


def test_claims_add_owner_ref_cross_machine_defers(monkeypatch, tmp_path, capfd):
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-remote", "other-box/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out.get("deferred") is True and out["reason"] == "cross-machine-owner"
    # No local ledger write anywhere.
    assert not list((tmp_path / ".example-web" / "worktrees").glob("*.yaml")) or \
        all(not tracking.load_record(p).resources
            for p in (tmp_path / ".example-web" / "worktrees").glob("*.yaml"))


def test_claims_add_owner_ref_rejects_unqualified(monkeypatch, tmp_path):
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args("codespace", "cs-x", "just-an-id"))
    assert rc == 2


def test_claims_add_owner_ref_missing_record(monkeypatch, tmp_path):
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-x", "anomalous-potato/example-web/no-such-wt"))
    assert rc == 1  # resolved to a same-machine path that doesn't exist


# --- claims settle ----------------------------------------------------------

def _settle_args(ref, *, released=False, json_=True):
    return argparse.Namespace(
        target=["settle", ref], released=released, release_worktree=None, json=json_)


def test_claims_settle_marks_at_rest(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-blue"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["disposition"] == "at-rest"
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    c = rec.resources[0]
    assert c.state == "at-rest" and c.is_at_rest and not c.is_unsettled
    assert c.is_live  # at-rest is still held


def test_claims_settle_released(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-blue", released=True))
    assert rc == 0
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].state == "released"


def test_claims_settle_unknown_ref(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          resources=[tracking.ResourceClaim(kind="codespace", ref="cs-blue")])
    rc = m.cmd_claims(_settle_args("cs-red"))
    assert rc == 1


def _settle_ownerref_args(ref, owner_ref, *, released=False, json_=True):
    return argparse.Namespace(
        target=["settle", ref], released=released, release_worktree=None,
        claim_owner_ref=owner_ref, json=json_)


def test_claims_settle_owner_ref_lands_on_cross_project_record(monkeypatch, tmp_path, capfd):
    owner_wt_dir = _seed_ownerref(tmp_path, monkeypatch)
    # First journal an active claim onto the borrowing record via owner-ref.
    m.cmd_claims(_add_ownerref_args(
        "codespace", "cs-xyz", "anomalous-potato/example-web/wt-borrower"))
    capfd.readouterr()
    # Now settle it via owner-ref (disconnect-hook path).
    rc = m.cmd_claims(_settle_ownerref_args(
        "cs-xyz", "anomalous-potato/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["disposition"] == "at-rest" and out["worktree_id"] == "wt-borrower"
    rec = tracking.load_record(owner_wt_dir / "wt-borrower.yaml")
    assert rec.resources[0].state == "at-rest" and not rec.resources[0].is_unsettled


def test_claims_settle_owner_ref_cross_machine_defers(monkeypatch, tmp_path, capfd):
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_settle_ownerref_args(
        "cs-remote", "other-box/example-web/wt-borrower"))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out.get("deferred") is True and out["reason"] == "cross-machine-owner"


def test_claims_settle_owner_ref_rejects_unqualified(monkeypatch, tmp_path):
    _seed_ownerref(tmp_path, monkeypatch)
    rc = m.cmd_claims(_settle_ownerref_args("cs-x", "just-an-id"))
    assert rc == 2


def test_claims_add_then_settle_roundtrip(monkeypatch, tmp_path, capfd):
    _seed(tmp_path, monkeypatch)
    m.cmd_claims(_add_args("codespace", "cs-blue"))
    capfd.readouterr()
    m.cmd_claims(_settle_args("cs-blue"))
    rec = tracking.load_record(tmp_path / "worktrees" / "wt-A.yaml")
    assert rec.resources[0].state == "at-rest"
