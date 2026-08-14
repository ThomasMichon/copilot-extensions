"""End-to-end delivery test: real git-fetch → versioned slot publish (self_update).

Unlike ``test_self_install`` / ``test_update`` (which mock git + self_install to
stay fully offline), this exercises the **real** ``self_update`` delivery path —
an actual ``git clone``/``fetch`` followed by the real versioned-install — against
a **local git remote**, so it needs only ``git`` on PATH (no network, no PyPI, no
uv venv build; the delivery mechanics copy files + write the marker, and the uv
venv only materializes when the *binstub* later runs). It closes the Phase-6 "6b:
self-updating delivery validated end-to-end (bootstrap fetch → versioned slot)"
item as always-on CI coverage.

The remote is selected via the **user-level source config** (`[source]` in
``config.toml``), the same override that lets the updater track a fork / canary
branch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import worktree_manager.self_install as si
from worktree_manager import source_config as sc
from worktree_manager.self_install import (
    current_version,
    self_install,
    self_update,
    version_slot,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required for the delivery test"
)

_GIT_ID = [
    "-c", "user.email=e2e@example.invalid",
    "-c", "user.name=e2e",
    "-c", "commit.gpgsign=false",
]


def _write_payload(root: Path, version: str) -> None:
    """Materialize a minimal worktree-manager payload tree under ``root``."""
    pkg = root / "worktree-manager" / "src" / "worktree_manager"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', "utf-8")
    (root / "worktree-manager" / "pyproject.toml").write_text(
        f"[project]\nname='copilot-extensions-worktree-manager'\nversion='{version}'\n",
        "utf-8",
    )


def _make_remote(tmp: Path, version: str, branch: str = "main") -> Path:
    """A local git repo (on ``branch``) serving a bumped worktree-manager payload."""
    remote = tmp / "remote"
    remote.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(remote)], check=True)
    _write_payload(remote, version)
    subprocess.run(["git", "-C", str(remote), *_GIT_ID, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(remote), *_GIT_ID, "commit", "-q", "-m", "payload"],
                   check=True)
    return remote


def _seed_older_install(tmp: Path, root: Path, version: str) -> None:
    """Install an older payload so the fetched one is a genuine upgrade."""
    older = tmp / "older"
    _write_payload(older, version)
    res = self_install(older / "worktree-manager", root=root, dry_run=False)
    assert res.action == "installed" and current_version(root) == version


def test_self_update_fetches_local_remote_and_publishes_new_slot(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "localbin")
    _seed_older_install(tmp_path, root, "1.0.0")

    remote = _make_remote(tmp_path, "9.9.9")
    sc.set_source(repo=str(remote), root=root)  # user-level source override

    # First self_update: clones the remote (no staging yet) and publishes the new slot.
    res = self_update(root=root, ref="main", dry_run=False)
    assert res.action == "updated"
    assert res.previous == "1.0.0"
    assert res.version == "9.9.9"
    assert current_version(root) == "9.9.9"
    assert version_slot("9.9.9", root).is_dir()
    # The immutable older slot is retained.
    assert version_slot("1.0.0", root).is_dir()
    # The published slot carries the fetched payload.
    assert (version_slot("9.9.9", root) / "src" / "worktree_manager" / "__init__.py").exists()


def test_self_update_second_run_is_version_gated(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "localbin")
    _seed_older_install(tmp_path, root, "1.0.0")

    remote = _make_remote(tmp_path, "9.9.9")
    sc.set_source(repo=str(remote), root=root)

    assert self_update(root=root, ref="main", dry_run=False).action == "updated"
    # Second run re-fetches into the existing staging (fetch path) and is a
    # version-gated no-op — the remote payload is unchanged.
    again = self_update(root=root, ref="main", dry_run=False)
    assert again.action == "already-current"
    assert again.version == "9.9.9"
    assert current_version(root) == "9.9.9"


def test_self_update_fetch_path_honors_switched_source(tmp_path, monkeypatch):
    """After the first update, re-pointing the source config at a different remote
    must take effect on the *fetch* path (staging already exists) — i.e. the
    override is authoritative every run, not just on the initial clone."""
    root = tmp_path / "root"
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "localbin")
    _seed_older_install(tmp_path, root, "1.0.0")

    remote_a = _make_remote(tmp_path / "a", "9.9.9")
    sc.set_source(repo=str(remote_a), root=root)
    assert self_update(root=root, ref="main", dry_run=False).version == "9.9.9"

    # Switch the configured source to a second remote with a newer payload; staging
    # now exists, so this exercises the fetch path — which must honor the new repo.
    remote_b = _make_remote(tmp_path / "b", "9.9.10")
    sc.set_source(repo=str(remote_b), root=root)
    res = self_update(root=root, ref="main", dry_run=False)
    assert res.action == "updated"
    assert res.version == "9.9.10"
    assert current_version(root) == "9.9.10"
    assert version_slot("9.9.10", root).is_dir()


def test_self_update_uses_configured_ref(tmp_path, monkeypatch):
    """With no explicit ref, self_update fetches the branch from the source config."""
    root = tmp_path / "root"
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "localbin")
    _seed_older_install(tmp_path, root, "1.0.0")

    # Remote whose payload lives on a 'canary' branch, not 'main'.
    remote = _make_remote(tmp_path, "9.9.11", branch="canary")
    sc.set_source(repo=str(remote), ref="canary", root=root)

    res = self_update(root=root, dry_run=False)  # ref resolved from config
    assert res.action == "updated"
    assert res.version == "9.9.11"
    assert current_version(root) == "9.9.11"
