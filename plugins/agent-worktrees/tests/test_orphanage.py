"""Durable orphanage: --abandon re-homes obligations rather than dropping them.

resource-obligation-settlement (dotfiles#1161): `tracking.rehome_abandoned_
obligations` + `load_orphaned_obligations` + the `claims orphans` view, and the
finalize wiring that re-homes an abandoned worktree's unsettled obligations.
"""
from __future__ import annotations

import argparse
import json
import types

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import finalize, tracking


def _seed_project(tmp_path, monkeypatch, machine="m", project="p"):
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name or project}")
    (tmp_path / f".{project}").mkdir(parents=True, exist_ok=True)


def _claim(kind, ref, state="active", note=""):
    return tracking.ResourceClaim(kind=kind, ref=ref, state=state, note=note)


def _config(machine="m", project="p"):
    return types.SimpleNamespace(machine=machine, repo_name=project)


# ── rehome_abandoned_obligations / load_orphaned_obligations ─────────────────

def test_rehome_writes_registry_with_provenance(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    claims = [_claim("codespace", "verbose-space", note="borrowed"),
              _claim("worktree", "m/p/child")]
    added = tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config())
    assert len(added) == 2
    loaded = tracking.load_orphaned_obligations()
    assert {e["ref"] for e in loaded} == {"verbose-space", "m/p/child"}
    cs = next(e for e in loaded if e["ref"] == "verbose-space")
    assert cs["kind"] == "codespace" and cs["source_worktree"] == "wt-owner"
    assert cs["machine"] == "m" and cs["project"] == "p"
    assert cs["disposition"] == "abandoned" and cs["abandoned_at"]
    assert cs["note"] == "borrowed"


def test_rehome_is_idempotent(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    claims = [_claim("codespace", "verbose-space")]
    assert len(tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config())) == 1
    # Same source+ref again -> no duplicate.
    assert tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config()) == []
    assert len(tracking.load_orphaned_obligations()) == 1
    # A different owner re-homing the same ref IS recorded (distinct provenance).
    added = tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-other", config=_config())
    assert len(added) == 1
    assert len(tracking.load_orphaned_obligations()) == 2


def test_load_orphans_empty_when_absent(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    assert tracking.load_orphaned_obligations() == []


def test_rehome_is_best_effort_on_io_error(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(tracking, "orphanage_path",
                        lambda project=None: (_ for _ in ()).throw(OSError("nope")))
    # Never raises; returns [].
    assert tracking.rehome_abandoned_obligations(
        [_claim("codespace", "x")], source_worktree="w", config=_config()) == []


# ── claims orphans view ──────────────────────────────────────────────────────

def test_claims_orphans_json_lists_registry(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "verbose-space")], source_worktree="wt-o", config=_config())
    rc = m.cmd_claims(argparse.Namespace(target=["orphans"], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["count"] == 1 and out["orphaned"][0]["ref"] == "verbose-space"


def test_claims_orphans_empty_message(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(target=["orphans"], json=False))
    assert rc == 0
    assert "no re-homed obligations" in capfd.readouterr().out.lower()


# ── finalize wiring: --abandon re-homes only unsettled, before releasing ─────

def _record_with_active_claim():
    return types.SimpleNamespace(
        resources=[_claim("codespace", "verbose-space", state="active"),
                   _claim("worktree", "m/p/settled", state="at-rest")])


def test_finalize_rehome_helper_selects_only_unsettled(tmp_path, monkeypatch):
    seen = {}

    def _fake_rehome(claims, *, source_worktree, config, project=None):
        seen["refs"] = [c.ref for c in claims]
        seen["src"] = source_worktree
        return [{"ref": c.ref} for c in claims]

    monkeypatch.setattr(tracking, "rehome_abandoned_obligations", _fake_rehome)
    rec = _record_with_active_claim()
    out = finalize._rehome_abandoned_obligations(rec, "wt-owner", _config())
    # Only the active (unsettled) claim is re-homed; the at-rest one is not.
    assert seen["refs"] == ["verbose-space"]
    assert seen["src"] == "wt-owner"
    assert len(out) == 1


def test_finalize_rehome_helper_noop_when_nothing_unsettled(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(tracking, "rehome_abandoned_obligations",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    rec = types.SimpleNamespace(resources=[_claim("worktree", "m/p/x", state="at-rest")])
    assert finalize._rehome_abandoned_obligations(rec, "wt", _config()) == []
    assert called["n"] == 0  # nothing unsettled -> the registry is never touched


def test_finalize_abandon_rehomes_end_to_end(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    rec = _record_with_active_claim()
    finalize._rehome_abandoned_obligations(rec, "wt-owner", _config())
    loaded = tracking.load_orphaned_obligations()
    assert [e["ref"] for e in loaded] == ["verbose-space"]  # real registry write

