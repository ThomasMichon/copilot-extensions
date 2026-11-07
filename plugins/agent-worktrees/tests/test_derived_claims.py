"""Tests for `derived` claims: marker-proven, owner-settled derived outputs."""

from __future__ import annotations

import json
import os
import subprocess
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import derived_claims as dc
from agent_worktrees import finalize_obligations, state_root, tracking, worktree_identity

_REAL_TRACKED_FILES = dc._tracked_files
_REAL_PROCESS_BLOCKER = dc._process_blocker


def _link_dir(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("cannot create a symlink here")
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip("cannot create a symlink or junction here")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    tdir = tmp_path / "worktrees"
    tdir.mkdir()
    wdir = tmp_path / "wt-A"
    wdir.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)
    tracking.create_new_record(
        "wt-A", "worktree/wt-A", str(wdir), "test-chamber", "anomalous-potato", "wsl", tdir,
    )
    monkeypatch.setattr("agent_worktrees.config.load_config",
                        lambda *a, **k: types.SimpleNamespace(machine="anomalous-potato"))
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name, **kwargs: cfg.Config(
            srcroot=str(tmp_path), machine="anomalous-potato", platform="wsl", repo_name=name,
            repos={"test-chamber": cfg.RepoConfig(
                anchor=str(wdir), worktree_root=str(tmp_path / "trees"),
            )},
        ),
    )
    ready_root = state_root.StateRoot(str(tmp_path), "launch_repo", "test-chamber", False, False, True)
    monkeypatch.setattr(m.state_root_mod, "coordination_readiness",
                        lambda config: state_root.CoordinationReadiness(True, "ready", ready_root))
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda wid, cfg_: wid or "wt-A")
    monkeypatch.setattr(dc, "_tracked_files", lambda checkout, target: None)
    monkeypatch.setattr(dc, "_process_blocker", lambda target: None)
    return types.SimpleNamespace(tdir=tdir, wdir=wdir, rec_path=tdir / "wt-A.yaml")


def _run(capfd, *argv):
    args = m.build_parser().parse_args(["claims", *argv, "--json"])
    rc = m.cmd_claims(args)
    out = capfd.readouterr().out
    return rc, json.loads(out) if out.strip() else {}


def _add(capfd, path):
    return _run(capfd, "add", "derived", str(path), "--creator", "pytest")


def _claim(seeded, path):
    return dc.find_claim(tracking.load_record(seeded.rec_path).resources, path)


def test_add_writes_marker_and_ledger_nonce(seeded, capfd):
    target = seeded.wdir / ".test-venvs"
    rc, out = _add(capfd, target)
    assert rc == 0 and out["created"] is True
    marker = dc.read_marker(target)
    assert marker.worktree_id == "wt-A" and marker.creator == "pytest"
    claim = _claim(seeded, target)
    assert claim.kind == "derived" and claim.is_unsettled
    assert dc.nonce_from_note(claim.note) == marker.nonce
    # Re-adding re-proves the same claim without minting a new nonce.
    rc, out = _add(capfd, target)
    assert rc == 0 and out["created"] is False
    assert dc.nonce_from_note(_claim(seeded, target).note) == marker.nonce


def test_add_refuses_preexisting_unmarked_dir(seeded, capfd):
    target = seeded.wdir / "build"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    rc, out = _add(capfd, target)
    assert rc == 1 and "refusing to adopt" in out["error"]
    assert _claim(seeded, target) is None
    assert not (target / dc.MARKER_NAME).exists()


def test_add_refuses_path_outside_checkout(seeded, capfd, tmp_path):
    rc, out = _add(capfd, tmp_path / "elsewhere")
    assert rc == 1 and "not inside" in out["error"]
    assert not (tmp_path / "elsewhere").exists()


def test_producer_api_claims_and_refuses(seeded):
    target = seeded.wdir / "out"
    result = dc.claim_directory(target, creator="producer")
    assert result["created"] is True and target.is_dir()
    other = seeded.wdir / "foreign"
    other.mkdir()
    with pytest.raises(dc.DerivedClaimError):
        dc.claim_directory(other, creator="producer")


def test_finalize_gate_blocks_on_open_derived_claim(seeded, capfd):
    _add(capfd, seeded.wdir / "cache")
    errors: list[str] = []
    ok = finalize_obligations.assert_obligations_settled(
        tracking.load_record(seeded.rec_path), "wt-A", abandon=False,
        emit_error=errors.append, emit_warning=lambda _m: None,
        format_ref=lambda *parts: "/".join(parts),
    )
    assert ok is False
    assert any("settle <path> --reclaim" in e and "release <path>" in e for e in errors)
    assert (seeded.wdir / "cache").is_dir()


def test_reclaim_deletes_exactly_the_claimed_dir(seeded, capfd):
    target = seeded.wdir / "cache"
    sibling = seeded.wdir / "cache-sibling"
    _add(capfd, target)
    _add(capfd, sibling)
    (target / "sub").mkdir()
    (target / "sub" / "f.bin").write_bytes(b"1")
    rc, out = _run(capfd, "settle", str(target), "--reclaim")
    assert rc == 0 and out["reclaimed"] is True
    assert not target.exists() and sibling.is_dir()
    assert _claim(seeded, target).state == "released"
    assert _claim(seeded, sibling).is_unsettled


def test_reclaim_dry_run_keeps_everything(seeded, capfd):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    rc, out = _run(capfd, "settle", str(target), "--reclaim", "--dry-run")
    assert rc == 0 and out["would_delete"] == str(target)
    assert target.is_dir() and _claim(seeded, target).is_unsettled


def _assert_refused(seeded, capfd, target, fragment):
    rc, out = _run(capfd, "settle", str(target), "--reclaim")
    assert rc == 1 and out["reclaimed"] is False
    assert any(fragment in r for r in out["refusals"]), out["refusals"]
    assert _claim(seeded, target).is_unsettled


def test_reclaim_refuses_nonce_mismatch(seeded, capfd):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    marker = target / dc.MARKER_NAME
    data = json.loads(marker.read_text())
    data["nonce"] = "0" * 32
    marker.write_text(json.dumps(data))
    _assert_refused(seeded, capfd, target, "nonce")
    assert target.is_dir()


def test_reclaim_refuses_path_outside_checkout(seeded, capfd, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    rec = tracking.load_record(seeded.rec_path)
    tracking.add_resource_claim(rec, tracking.ResourceClaim(
        kind="derived", ref=str(outside), created_at="2026-01-01T00:00:00",
        note=dc.format_note(creator="x", nonce="a" * 32)), save=False)
    tracking.save_record(rec, seeded.rec_path)
    _assert_refused(seeded, capfd, outside, "not inside")
    assert outside.is_dir()


def test_reclaim_refuses_live_process(seeded, capfd, monkeypatch):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    monkeypatch.setattr(dc, "_process_blocker", lambda t: "live process(es) still use it: x(1)")
    _assert_refused(seeded, capfd, target, "live process")
    assert target.is_dir()


def test_reclaim_refuses_reparse_point(seeded, capfd, tmp_path):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep")
    link = target / "link"
    _link_dir(link, victim)
    _assert_refused(seeded, capfd, target, "reparse point")
    assert (victim / "precious.txt").read_text() == "keep"


def test_linked_ancestor_refused_for_add_and_reclaim(seeded, capfd):
    real = seeded.wdir / "real"
    real.mkdir()
    _link_dir(seeded.wdir / "alias", real)
    rc, out = _add(capfd, seeded.wdir / "alias" / "cache")
    assert rc == 1 and "link or reparse point" in out["error"]
    assert not (real / "cache").exists()
    target = seeded.wdir / "owned" / "cache"
    _add(capfd, target)
    moved = seeded.wdir / "moved"
    (seeded.wdir / "owned").rename(moved)
    _link_dir(seeded.wdir / "owned", moved)
    _assert_refused(seeded, capfd, target, "link or reparse point")
    assert (moved / "cache" / dc.MARKER_NAME).exists()


def test_reclaim_refuses_reserved_claim_before_deleting(seeded, capfd, monkeypatch):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    monkeypatch.setattr(tracking, "claim_handoff_reservation", lambda rec, claim: "bundle-1")
    _assert_refused(seeded, capfd, target, "reserved")
    assert target.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows image-path check")
def test_process_blocker_covers_executables_inside(monkeypatch, tmp_path):
    from agent_worktrees import procs

    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda root: None)
    monkeypatch.setattr(procs, "processes_with_executable_under",
                        lambda root: [{"pid": 7, "name": "python.exe"}])
    assert "python.exe(7)" in _REAL_PROCESS_BLOCKER(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX /proc census")
def test_posix_census_refuses_uninspectable_own_process(monkeypatch, tmp_path):
    from agent_worktrees import derived_liveuse, procs

    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda root: None)
    target = tmp_path / "cache"
    target.mkdir()
    proc_root = tmp_path / "proc"

    def fake(pid, cwd, exe):
        entry = proc_root / str(pid)
        entry.mkdir(parents=True)
        (entry / "stat").write_text(f"{pid} (x) S 1")
        for name, dest in (("cwd", cwd), ("exe", exe)):
            if dest is None:
                (entry / name).write_text("unreadable")
            else:
                (entry / name).symlink_to(dest)

    fake(101, tmp_path, "/bin/true")
    assert derived_liveuse.blocker(target, proc_root=proc_root) is None
    fake(102, tmp_path, target / "bin" / "python")
    assert "pid 102" in derived_liveuse.blocker(target, proc_root=proc_root)
    (proc_root / "102" / "exe").unlink()
    (proc_root / "102" / "exe").symlink_to("/bin/true")
    fake(103, None, "/bin/true")
    assert "cannot inspect" in derived_liveuse.blocker(target, proc_root=proc_root)


def test_live_use_includes_this_process_executable(monkeypatch, tmp_path):
    from agent_worktrees import derived_liveuse, procs

    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda root: None)
    target = tmp_path / "cache"
    (target / "bin").mkdir(parents=True)
    monkeypatch.setattr(derived_liveuse.sys, "executable", str(target / "bin" / "python"))
    assert "this process runs an executable" in derived_liveuse.blocker(target)


@pytest.mark.skipif(os.name != "nt", reason="Windows in-use enforcement")
def test_windows_reclaim_keeps_dir_held_open(tmp_path):
    target = tmp_path / "cache"
    target.mkdir()
    held = open(target / "held.txt", "w")
    try:
        removed, detail = dc.remove_directory(target)
    finally:
        held.close()
    assert not removed and "refused to move it aside" in detail
    assert (target / "held.txt").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["cache"]
    assert dc.remove_directory(target)[0] and not tmp_path.joinpath("cache").exists()


def test_reclaim_refuses_git_tracked_file(seeded, capfd, monkeypatch):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git unavailable")
    monkeypatch.setattr(dc, "_tracked_files", _REAL_TRACKED_FILES)
    target = seeded.wdir / "cache"
    _add(capfd, target)
    (target / "tracked.txt").write_text("x")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], cwd=seeded.wdir, check=True)
    subprocess.run([*git, "add", "cache/tracked.txt"], cwd=seeded.wdir, check=True)
    # An inherited index selector must not hide the checkout's tracked files.
    monkeypatch.setenv("GIT_INDEX_FILE", str(seeded.wdir.parent / "empty-index"))
    _assert_refused(seeded, capfd, target, "Git-tracked")
    assert (target / "tracked.txt").exists()


def test_reclaim_in_git_checkout_with_untracked_content(seeded, capfd, monkeypatch):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git unavailable")
    monkeypatch.setattr(dc, "_tracked_files", _REAL_TRACKED_FILES)
    subprocess.run(["git", "init", "-q"], cwd=seeded.wdir, check=True)
    target = seeded.wdir / "cache"
    _add(capfd, target)
    (target / "untracked.txt").write_text("x")
    rc, out = _run(capfd, "settle", str(target), "--reclaim")
    assert rc == 0 and out["reclaimed"] is True and not target.exists()

def test_release_keeps_dir(seeded, capfd, monkeypatch):
    target = seeded.wdir / "cache"
    _add(capfd, target)
    monkeypatch.chdir(seeded.wdir)
    rc, out = _run(capfd, "release", "cache")
    assert rc == 0
    assert target.is_dir() and not _claim(seeded, target).is_unsettled


def test_sweep_never_reclaims_derived(seeded):
    rec = tracking.load_record(seeded.rec_path)
    tracking.add_resource_claim(rec, tracking.ResourceClaim(
        kind="derived", ref=str(seeded.wdir / "x"), created_at="2026-01-01T00:00:00",
        note=dc.format_note(creator="t", nonce="b" * 32)), save=False)
    reclaimed = tracking.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False)
    assert reclaimed == []
    assert dc.find_claim(rec.resources, seeded.wdir / "x").is_unsettled


def test_cli_plumbing_reachable_from_parser():
    args = m.build_parser().parse_args(
        ["claims", "settle", "p", "--reclaim", "--dry-run"])
    assert args.reclaim is True and args.dry_run is True
    args = m.build_parser().parse_args(["claims", "add", "derived", "p", "--creator", "t"])
    assert args.derived_creator == "t"
    assert Path(dc.canonical_path("p")).is_absolute()
