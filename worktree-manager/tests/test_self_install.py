"""Tests for the versioned self-install (Phase 2/3 — same convention as the core).

Asserts the shared versioning artifacts: a plain-text ``current-version`` marker,
an immutable ``versions/<ver>/`` slot, and a ``~/.local/bin`` binstub — plus
idempotent, version-gated behavior. No real venv is built (uv is not invoked);
the fast materialize+marker+binstub path is exercised against a synthetic payload
and a synthetic HOME/root.
"""

from __future__ import annotations

from pathlib import Path

import worktree_manager.self_install as si
from worktree_manager.self_install import (
    current_version,
    needs_install,
    payload_version,
    self_install,
    status,
    version_slot,
)
from worktree_manager.__main__ import main


def _fake_payload(tmp: Path, version: str) -> Path:
    pd = tmp / "payload"
    pkg = pd / "src" / "worktree_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pd / "pyproject.toml").write_text("[project]\nname='x'\n")
    return pd


def _patch_local_bin(monkeypatch, tmp: Path) -> Path:
    lb = tmp / ".local" / "bin"
    monkeypatch.setattr(si, "local_bin", lambda: lb)
    return lb


def test_payload_version_reads_init(tmp_path):
    pd = _fake_payload(tmp_path, "9.9.9-dev1")
    assert payload_version(pd) == "9.9.9-dev1"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=True)
    assert res.action == "planned"
    assert res.version == "1.2.3"
    assert current_version(root) is None
    assert not (root / "current-version").exists()


def test_apply_installs_marker_slot_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    # marker file (plain text, names the active version)
    assert (root / "current-version").read_text().strip() == "1.2.3"
    assert current_version(root) == "1.2.3"
    # immutable version slot with the payload copied in
    slot = version_slot("1.2.3", root)
    assert slot.is_dir()
    assert (slot / "src" / "worktree_manager" / "__init__.py").exists()
    # binstub deployed to ~/.local/bin
    stub = lb / "worktree-manager"
    assert stub.exists()
    body = stub.read_text()
    assert "current-version" in body and "worktree-manager" in body


def test_apply_is_idempotent_and_version_gated(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert needs_install("1.2.3", root) is False
    again = self_install(pd, root=root, dry_run=False)
    assert again.action == "already-current"


def test_new_version_publishes_new_slot(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(_fake_payload(tmp_path, "1.0.0"), root=root, dry_run=False)
    # bump the payload version and re-install
    pd2 = _fake_payload(tmp_path / "b", "2.0.0")
    res = self_install(pd2, root=root, dry_run=False)
    assert res.action == "installed"
    assert current_version(root) == "2.0.0"
    assert version_slot("1.0.0", root).is_dir()  # old slot immutable, retained
    assert version_slot("2.0.0", root).is_dir()


def test_status_reports_marker_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "3.3.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    assert status(root).installed is False
    self_install(pd, root=root, dry_run=False)
    st = status(root)
    assert st.installed_version == "3.3.3"
    assert st.binstub is not None


def test_bin_directory_is_deployed_into_the_slot(tmp_path, monkeypatch):
    """Phase 3b Slice 2 (Mux relocation): the versioned self-install copies the
    WHOLE payload directory (``_copy_payload`` -> ``shutil.copytree``), so a
    sibling ``bin/`` directory of launcher scripts -- like
    ``worktree-manager/bin/launch-session.{sh,ps1,cmd}`` -- deploys to
    ``<slot>/bin/`` with no self-install code change. This proves that
    mechanism generically with a synthetic script, independent of the real
    launcher scripts' content."""
    pd = _fake_payload(tmp_path, "4.4.4")
    (pd / "bin").mkdir()
    (pd / "bin" / "launch-session.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    slot = version_slot("4.4.4", root)
    deployed = slot / "bin" / "launch-session.sh"
    assert deployed.exists()
    assert deployed.read_text() == (pd / "bin" / "launch-session.sh").read_text()


def test_self_install_command_dry_run(capsys):
    rc = main(["self-install"])
    out = capsys.readouterr().out
    assert "self-install" in out.lower()
    assert "current-version" in out
    assert rc in (0, 1)


def test_doctor_shows_self_section(capsys):
    main(["doctor"])
    assert "worktree-manager (self)" in capsys.readouterr().out
