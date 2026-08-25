"""Orphanage cleanup consumer: reclaim re-homed obligations + drop the entry.

resource-obligation-settlement (dotfiles#1161): `cleanup.cleanup_orphanage` /
`reclaim_orphan` / `reclaim_codespace`, the `tracking.remove_orphaned_
obligations` write primitive, and the `claims cleanup` verb. The read-only
lister is covered by test_orphanage.py; here we exercise the *acting* consumer.
"""
from __future__ import annotations

import argparse
import json
import types

import agent_worktrees.__main__ as m
from agent_worktrees import cleanup, tracking
from agent_worktrees import config as cfg


def _seed_project(tmp_path, monkeypatch, project="p"):
    monkeypatch.setattr(cfg, "project_dir",
                        lambda name=None: tmp_path / f".{name or project}")
    (tmp_path / f".{project}").mkdir(parents=True, exist_ok=True)


def _claim(kind, ref, state="active", note=""):
    return tracking.ResourceClaim(kind=kind, ref=ref, state=state, note=note)


def _config(machine="m", project="p"):
    return types.SimpleNamespace(machine=machine, repo_name=project)


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ── tracking.remove_orphaned_obligations ─────────────────────────────────────

def test_remove_drops_matching_keeps_others(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a"), _claim("worktree", "m/p/b")],
        source_worktree="wt", config=_config())
    removed = tracking.remove_orphaned_obligations([("wt", "cs-a")])
    assert removed == 1
    left = tracking.load_orphaned_obligations()
    assert [e["ref"] for e in left] == ["m/p/b"]


def test_remove_deletes_file_when_empty(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a")], source_worktree="wt", config=_config())
    assert tracking.orphanage_path().exists()
    tracking.remove_orphaned_obligations([("wt", "cs-a")])
    assert not tracking.orphanage_path().exists()


def test_remove_noop_on_empty_or_unmatched(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a")], source_worktree="wt", config=_config())
    assert tracking.remove_orphaned_obligations([]) == 0
    assert tracking.remove_orphaned_obligations([("wt", "no-such")]) == 0
    assert len(tracking.load_orphaned_obligations()) == 1


def test_remove_best_effort_on_error(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(tracking, "load_orphaned_obligations",
                        lambda project=None: (_ for _ in ()).throw(OSError("boom")))
    assert tracking.remove_orphaned_obligations([("wt", "x")]) == 0


# ── reclaim_codespace ────────────────────────────────────────────────────────

def test_reclaim_codespace_dry_run_reports_intent(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(cleanup, "_run_codespaces",
                        lambda *a, **k: called.__setitem__("n", 1))
    r = cleanup.reclaim_codespace("cs-x", apply=False)
    assert r.reclaimed and "would delete" in r.detail
    assert called["n"] == 0  # dry-run never shells out


def test_reclaim_codespace_apply_success(monkeypatch):
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))
    r = cleanup.reclaim_codespace("cs-x", apply=True)
    assert r.status == "reclaimed" and "deleted" in r.detail


def test_reclaim_codespace_404_is_already_gone(monkeypatch):
    monkeypatch.setattr(
        cleanup, "_run_codespaces",
        lambda *a, **k: _proc(1, stderr="HTTP 404: Not Found"))
    r = cleanup.reclaim_codespace("cs-x", apply=True)
    assert r.status == "reclaimed" and "already gone" in r.detail


def test_reclaim_codespace_real_failure_retains(monkeypatch):
    monkeypatch.setattr(
        cleanup, "_run_codespaces",
        lambda *a, **k: _proc(1, stderr="HTTP 500: server exploded"))
    r = cleanup.reclaim_codespace("cs-x", apply=True)
    assert r.status == "failed" and "exploded" in r.detail


def test_reclaim_codespace_binstub_unavailable(monkeypatch):
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: None)
    r = cleanup.reclaim_codespace("cs-x", apply=True)
    assert r.status == "failed" and "binstub" in r.detail


def test_reclaim_codespace_empty_name(monkeypatch):
    r = cleanup.reclaim_codespace("", apply=True)
    assert r.status == "failed"


# ── reclaim_worktree ─────────────────────────────────────────────────────────

def _repo(anchor="D:/anchor"):
    return types.SimpleNamespace(anchor=anchor)


def test_reclaim_worktree_dry_run_reports_intent(monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: _repo())
    called = {"n": 0}
    monkeypatch.setattr(cleanup, "_run_worktrees",
                        lambda *a, **k: called.__setitem__("n", 1))
    r = cleanup.reclaim_worktree("m/p/child", _config(), apply=False)
    assert r.reclaimed and "would finalize" in r.detail and "child" in r.detail
    assert called["n"] == 0


def test_reclaim_worktree_apply_success_runs_in_anchor(monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: _repo(anchor="D:/child-anchor"))
    seen = {}

    def _run(args, *, cwd=None, **k):
        seen["args"] = args
        seen["cwd"] = cwd
        return _proc(0)
    monkeypatch.setattr(cleanup, "_run_worktrees", _run)
    monkeypatch.setattr(
        cleanup.tracking, "load_orphaned_obligations_strict",
        lambda project=None: [])
    r = cleanup.reclaim_worktree("m/p/child", _config(), apply=True)
    assert r.status == "reclaimed"
    assert seen["cwd"] == "D:/child-anchor"
    assert seen["args"] == [
        "finalize", "child", "--abandon",
        "--handoff-to", "claims-cleanup", "--json"]


def test_reclaim_worktree_retains_parent_until_nested_handoff_accepted(
        monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: _repo())
    monkeypatch.setattr(cleanup, "_run_worktrees", lambda *a, **k: _proc(0))
    monkeypatch.setattr(
        cleanup.tracking, "load_orphaned_obligations_strict",
        lambda project=None: [{
            "source_worktree": "child",
            "ref": "m/other/grandchild",
            "handoff_to": "claims-cleanup",
        }])
    r = cleanup.reclaim_worktree("m/p/child", _config(), apply=True)
    assert r.status == "failed"
    assert "nested obligation" in r.detail
    assert "claims cleanup child --apply" in r.detail


def test_reclaim_worktree_finalize_refusal_retains(monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: _repo())
    monkeypatch.setattr(
        cleanup, "_run_worktrees",
        lambda *a, **k: _proc(1, stderr="Work is not upstream"))
    r = cleanup.reclaim_worktree("m/p/child", _config(), apply=True)
    assert r.status == "failed" and "not upstream" in r.detail


def test_reclaim_worktree_unresolvable_project_fails(monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: None)
    monkeypatch.setattr(cleanup.repos_mod, "find_repo", lambda project: None)
    r = cleanup.reclaim_worktree("m/p/child", _config(), apply=True)
    assert r.status == "failed" and "resolve project" in r.detail


def test_reclaim_worktree_falls_back_to_global_repo_registry(
        tmp_path, monkeypatch):
    anchor = tmp_path / "child-anchor"
    anchor.mkdir()
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: None)
    monkeypatch.setattr(
        cleanup.repos_mod, "find_repo",
        lambda project: types.SimpleNamespace(local_path=lambda: str(anchor)))
    seen = {}

    def _run(args, *, cwd=None, **kwargs):
        seen["cwd"] = cwd
        return _proc(0)

    monkeypatch.setattr(cleanup, "_run_worktrees", _run)
    monkeypatch.setattr(
        cleanup.tracking, "load_orphaned_obligations_strict",
        lambda project=None: [])
    r = cleanup.reclaim_worktree("m/dev.tmichon/child", _config(), apply=True)
    assert r.status == "reclaimed"
    assert seen["cwd"] == str(anchor)


def test_reclaim_worktree_unparseable_ref_fails():
    r = cleanup.reclaim_worktree("", _config(), apply=True)
    assert r.status == "failed"


def test_reclaim_worktree_cross_machine_skips_before_local_resolution(
        monkeypatch):
    resolved = {"called": False}

    def _find(project):
        resolved["called"] = True
        return None

    monkeypatch.setattr(cleanup.repos_mod, "find_repo", _find)
    r = cleanup.reclaim_worktree(
        "other/p/child", _config(machine="local"), apply=True)
    assert r.status == "skipped" and "other" in r.detail
    assert resolved["called"] is False


def test_reclaim_orphan_worktree_dispatches(monkeypatch):
    monkeypatch.setattr(cleanup.sweep_mod, "repo_for_project",
                        lambda project, config: _repo())
    monkeypatch.setattr(cleanup, "_run_worktrees", lambda *a, **k: _proc(0))
    monkeypatch.setattr(
        cleanup.tracking, "load_orphaned_obligations_strict",
        lambda project=None: [])
    entry = {"kind": "worktree", "ref": "m/p/child", "machine": "m"}
    r = cleanup.reclaim_orphan(entry, _config(machine="m"), apply=True)
    assert r.status == "reclaimed"


# ── reclaim_orphan dispatch ──────────────────────────────────────────────────

def test_reclaim_orphan_cross_machine_skipped():
    entry = {"kind": "codespace", "ref": "cs", "machine": "other"}
    r = cleanup.reclaim_orphan(entry, _config(machine="m"), apply=True)
    assert r.status == "skipped" and "cross-machine" in r.detail


def test_reclaim_orphan_unknown_kind_unsupported():
    entry = {"kind": "bridge", "ref": "sess", "machine": "m"}
    r = cleanup.reclaim_orphan(entry, _config(machine="m"), apply=False)
    assert r.status == "unsupported"


def test_reclaim_orphan_codespace_dispatches(monkeypatch):
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))
    entry = {"kind": "codespace", "ref": "cs-x", "machine": "m"}
    r = cleanup.reclaim_orphan(entry, _config(machine="m"), apply=True)
    assert r.status == "reclaimed"


def test_reclaim_orphan_reclaimer_error_is_failed(monkeypatch):
    def _boom(ref, apply, config):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(cleanup._RECLAIMERS, "codespace", _boom)
    entry = {"kind": "codespace", "ref": "cs-x", "machine": "m"}
    r = cleanup.reclaim_orphan(entry, _config(machine="m"), apply=True)
    assert r.status == "failed" and "kaboom" in r.detail


# ── cleanup_orphanage: dry-run vs apply, selective removal ───────────────────

def test_cleanup_dry_run_does_not_remove(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a")], source_worktree="wt", config=_config())
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))
    rows = cleanup.cleanup_orphanage(_config(), apply=False)
    assert rows[0]["status"] == "reclaimed"
    # dry-run: registry untouched.
    assert len(tracking.load_orphaned_obligations()) == 1


def test_cleanup_apply_removes_only_reclaimed(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-good"), _claim("codespace", "cs-bad"),
         _claim("bridge", "sess-x")],
        source_worktree="wt", config=_config())

    def _fake(args, **k):
        name = args[1]
        return _proc(0) if name == "cs-good" else _proc(1, stderr="HTTP 500")
    monkeypatch.setattr(cleanup, "_run_codespaces", _fake)

    rows = cleanup.cleanup_orphanage(_config(), apply=True)
    by_ref = {r["ref"]: r["status"] for r in rows}
    assert by_ref == {"cs-good": "reclaimed", "cs-bad": "failed",
                      "sess-x": "unsupported"}
    # Only the reclaimed entry is dropped; failed + unsupported are retained.
    left = {e["ref"] for e in tracking.load_orphaned_obligations()}
    assert left == {"cs-bad", "sess-x"}


def test_cleanup_empty_orphanage(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    assert cleanup.cleanup_orphanage(_config(), apply=True) == []


def test_cleanup_selects_exact_ref_or_source_worktree(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a"), _claim("codespace", "cs-b")],
        source_worktree="owner-a", config=_config())
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-c")],
        source_worktree="owner-b", config=_config())
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))

    by_ref = cleanup.cleanup_orphanage(
        _config(), apply=True, selectors={"cs-a"})
    assert [row["ref"] for row in by_ref] == ["cs-a"]
    by_owner = cleanup.cleanup_orphanage(
        _config(), apply=True, selectors={"owner-b"})
    assert [row["ref"] for row in by_owner] == ["cs-c"]
    assert [entry["ref"] for entry in tracking.load_orphaned_obligations()] == [
        "cs-b"]


# ── claims cleanup verb (CLI integration) ────────────────────────────────────

def _cleanup_args(apply=False, json_=False, selectors=None):
    return argparse.Namespace(
        target=["cleanup", *(selectors or [])], apply=apply, json=json_)


def test_claims_cleanup_verb_json(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "load_config", lambda: _config())
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a")], source_worktree="wt", config=_config())
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))
    rc = m.cmd_claims(_cleanup_args(apply=True, json_=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["applied"] is True and out["reclaimed"] == 1
    assert out["results"][0]["ref"] == "cs-a"
    assert tracking.load_orphaned_obligations() == []


def test_claims_cleanup_verb_empty_text(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "load_config", lambda: _config())
    rc = m.cmd_claims(_cleanup_args(apply=False, json_=False))
    assert rc == 0
    assert "orphanage is empty" in capfd.readouterr().out.lower()


def test_claims_cleanup_verb_passes_selectors(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "load_config", lambda: _config())
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "cs-a"), _claim("codespace", "cs-b")],
        source_worktree="owner", config=_config())
    monkeypatch.setattr(cleanup, "_run_codespaces", lambda *a, **k: _proc(0))
    rc = m.cmd_claims(_cleanup_args(
        apply=True, json_=True, selectors=["cs-a"]))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["selectors"] == ["cs-a"]
    assert [row["ref"] for row in out["results"]] == ["cs-a"]
    assert [entry["ref"] for entry in tracking.load_orphaned_obligations()] == [
        "cs-b"]
