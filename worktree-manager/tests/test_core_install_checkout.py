"""Tests for core-install checkout discovery (worktree-manager #540).

`resolve_checkout()` must find a copilot-extensions checkout from **cwd** (walk up)
or the **bootstrap staging clone** (`<root>/staging`), not only from this package's
installed slot — so `setup` can drive the real agent-worktrees installer right after
a bootstrap.
"""

from __future__ import annotations

import os
from pathlib import Path

import worktree_manager.core_install as ci


def _make_checkout(root: Path) -> Path:
    """Materialize the minimal markers `_is_checkout` looks for."""
    (root / ".github" / "plugin").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "plugin" / "marketplace.json").write_text("{}", "utf-8")
    scripts = root / "plugins" / "agent-worktrees" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "install.sh").write_text("#!/usr/bin/env bash\n", "utf-8")
    (scripts / "install.ps1").write_text("# ps1\n", "utf-8")
    return root


def test_is_checkout(tmp_path):
    assert not ci._is_checkout(tmp_path)
    _make_checkout(tmp_path)
    assert ci._is_checkout(tmp_path)


def test_resolve_from_cwd(tmp_path, monkeypatch):
    co = _make_checkout(tmp_path / "co")
    sub = co / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)  # cwd inside the checkout (walk up finds it)
    # No staging fallback available for this root.
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "wmroot"))
    assert ci.resolve_checkout() == co


def test_resolve_from_staging_when_cwd_has_none(tmp_path, monkeypatch):
    # cwd is NOT inside a checkout…
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    # …but the bootstrap staging clone is a full checkout.
    wmroot = tmp_path / "wmroot"
    _make_checkout(wmroot / "staging")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(wmroot))
    assert ci.resolve_checkout() == wmroot / "staging"


def test_resolve_none_when_neither(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "empty-root"))
    assert ci.resolve_checkout() is None


def test_install_command_uses_resolved_checkout(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    wmroot = tmp_path / "wmroot"
    _make_checkout(wmroot / "staging")
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(wmroot))
    cmd = ci.install_command()  # no explicit repo_root -> resolves staging
    assert cmd is not None
    name = "install.ps1" if os.name == "nt" else "install.sh"
    assert cmd[-1] == "install"
    assert name in cmd[-2]
