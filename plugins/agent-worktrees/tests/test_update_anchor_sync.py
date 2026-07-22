"""Tests for the ``update`` anchor fast-forward step.

``_fast_forward_project_anchors`` closes the anchor-sync lag: after ``update``
refreshes the plugin payload, the managed repo's anchor checkout (source of
truth for in-repo ``.agent-worktrees/config.yaml`` bindings) is fast-forwarded
too -- but only when it is on the default branch, clean, and strictly behind.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


@pytest.fixture
def anchor_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Return (seed, anchor): a clone tracking origin/master plus a seed
    checkout used to advance origin so the anchor falls behind."""
    origin_bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("clone", str(origin_bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    _commit(seed, "base.txt", "v1")
    _git("push", "origin", "master", cwd=seed)

    anchor = tmp_path / "anchor"
    _git("clone", str(origin_bare), str(anchor), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    return seed, anchor


def _advance_origin(seed: Path, name: str = "next.txt") -> None:
    _commit(seed, name, "more")
    _git("push", "origin", "master", cwd=seed)


def _install_config(monkeypatch: pytest.MonkeyPatch, anchor: Path) -> None:
    repo = cfg.RepoConfig(
        anchor=str(anchor),
        worktree_root=str(anchor.parent / "wt"),
        default_branch="master",
        remote="origin",
    )
    config = cfg.Config(
        srcroot=str(anchor.parent),
        machine="test",
        platform="linux",
        repo_name="anchor",
        repos={"anchor": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)


def test_clean_behind_anchor_is_fast_forwarded(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # The commit pushed to origin is now present in the anchor checkout.
    assert (anchor / "next.txt").exists()


def test_up_to_date_anchor_is_left_alone(anchor_repo, monkeypatch):
    _seed, anchor = anchor_repo
    _install_config(monkeypatch, anchor)

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    m._fast_forward_project_anchors()
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    assert head_before == head_after


def test_non_default_branch_anchor_is_skipped(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    # Anchor is checked out on a feature branch, not master.
    _git("checkout", "-b", "feature", cwd=anchor)
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # Still on feature branch and the origin/master commit was NOT pulled in.
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(anchor),
        capture_output=True, text=True,
    ).stdout.strip()
    assert current == "feature"
    assert not (anchor / "next.txt").exists()


def test_dirty_anchor_is_left_untouched(anchor_repo, monkeypatch):
    seed, anchor = anchor_repo
    _advance_origin(seed)
    (anchor / "base.txt").write_text("uncommitted change")
    _install_config(monkeypatch, anchor)

    m._fast_forward_project_anchors()

    # Fast-forward refused -- the new origin commit is not present.
    assert not (anchor / "next.txt").exists()


def test_no_config_is_non_fatal(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)
    # Must not raise.
    m._fast_forward_project_anchors()


# ── stale-anchor self-heal (catch-22 break) ─────────────────────────────────
# The interactive picker parses the anchor's machines.yaml to identify this
# machine; a stale anchor missing the self-entry crashes it, yet the anchor
# fast-forward that adds the entry otherwise runs only *after* a successful
# resolve. ``_heal_stale_anchor_if_self_missing`` breaks that loop by
# fast-forwarding the anchor first, but only when the self-entry is absent.


def _write_machines(repo: Path, keys: list[str]) -> None:
    lines = ["machines:"]
    for k in keys:
        lines += [f"  {k}:", f"    display_name: {k}", "    environment: Test"]
    (repo / "machines.yaml").write_text("\n".join(lines) + "\n")


def _make_origin_anchor(tmp_path: Path, base_keys: list[str]) -> tuple[Path, Path]:
    """(seed, anchor) sharing an origin; base commit carries machines(base_keys)."""
    origin_bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("clone", str(origin_bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    (seed / "base.txt").write_text("v1")
    _write_machines(seed, base_keys)
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "base", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    anchor = tmp_path / "anchor"
    _git("clone", str(origin_bare), str(anchor), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    return seed, anchor


def test_self_missing_triggers_anchor_heal(tmp_path, monkeypatch):
    # Anchor knows only 'otherbox'; origin advances to add this host + a marker.
    seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    _write_machines(seed, ["otherbox", "testbox"])
    (seed / "next.txt").write_text("more")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "add testbox", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m.socket, "gethostname", lambda: "testbox")

    # Precondition: the stale anchor lacks this machine's self-entry.
    assert cfg.find_machine_entry(cfg.load_machines_yaml(anchor), "testbox") is None

    m._heal_stale_anchor_if_self_missing(cfg.load_config())

    # The anchor was fast-forwarded, so the self-entry now resolves.
    assert (anchor / "next.txt").exists()
    assert cfg.find_machine_entry(
        cfg.load_machines_yaml(anchor), "testbox") is not None


def test_self_present_skips_heal(tmp_path, monkeypatch):
    # This host is already in the anchor's machines.yaml -> no heal, no ff even
    # though origin has advanced (the marker must NOT be pulled in).
    seed, anchor = _make_origin_anchor(tmp_path, ["otherbox", "testbox"])
    (seed / "next.txt").write_text("more")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "unrelated advance", cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m.socket, "gethostname", lambda: "testbox")

    m._heal_stale_anchor_if_self_missing(cfg.load_config())

    assert not (anchor / "next.txt").exists()


def test_heal_non_fatal_when_ff_raises(tmp_path, monkeypatch):
    _seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m.socket, "gethostname", lambda: "testbox")

    def _boom() -> None:
        raise RuntimeError("ff failed")

    monkeypatch.setattr(m, "_fast_forward_project_anchors", _boom)

    config = cfg.load_config()
    # Must not raise, and returns the original config unchanged.
    assert m._heal_stale_anchor_if_self_missing(config) is config


def test_unreadable_registry_does_not_trigger_heal(tmp_path, monkeypatch):
    # A missing/malformed machines.yaml is treated as "present" so an unrelated
    # I/O error never fires the pull-forward (and its network fetch).
    _seed, anchor = _make_origin_anchor(tmp_path, ["otherbox"])
    (anchor / "machines.yaml").unlink()
    _install_config(monkeypatch, anchor)
    monkeypatch.setattr(m.socket, "gethostname", lambda: "testbox")

    called = {"ff": 0}
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: called.__setitem__("ff", called["ff"] + 1))

    m._heal_stale_anchor_if_self_missing(cfg.load_config())
    assert called["ff"] == 0
