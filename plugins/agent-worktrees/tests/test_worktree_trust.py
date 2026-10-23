"""Native folder trust is explicit, serialized and scoped to admitted entry."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import (
    config_authority, permissions, session_binding_cli, tracking, tracking_write, worktree_trust,
)
from agent_worktrees.execution_spaces import ExecutionSpaceError


@pytest.fixture
def native_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    return checkout, home


def test_first_trust_creates_native_config_without_tool_approvals(native_checkout):
    checkout, home = native_checkout
    assert permissions.ensure_trusted_folder(str(checkout)) is True
    config = home / "config.json"
    assert json.loads(config.read_text()) == {"trustedFolders": [str(checkout.resolve())]}
    before = config.stat().st_mtime_ns
    assert permissions.ensure_trusted_folder(str(checkout)) is False
    assert config.stat().st_mtime_ns == before
    assert not (home / "permissions-config.json").exists()


def test_ensure_preserves_header_existing_trust_and_unrelated_settings(native_checkout):
    checkout, home = native_checkout
    home.mkdir()
    header = "// Managed settings.\n\n"
    initial = {"trustedFolders": ["/existing"], "endpoint": "https://example.test/path"}
    (home / "config.json").write_text(header + json.dumps(initial), encoding="utf-8")
    assert permissions.ensure_trusted_folder(str(checkout)) is True
    text = (home / "config.json").read_text()
    assert text.startswith(header)
    assert json.loads(text[len(header):]) == {
        **initial, "trustedFolders": ["/existing", str(checkout.resolve())],
    }


@pytest.mark.parametrize("content", ['{', '[]', '{"trustedFolders": {}}', '{"trustedFolders": [3]}'])
def test_invalid_config_is_explicit_and_preserved(native_checkout, content):
    checkout, home = native_checkout
    home.mkdir()
    config = home / "config.json"
    config.write_text(content)
    with pytest.raises(permissions.FolderTrustError, match="Could not ensure"):
        permissions.ensure_trusted_folder(str(checkout))
    assert config.read_text() == content


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "root"])
def test_non_checkout_path_cannot_create_trust(native_checkout, kind):
    checkout, home = native_checkout
    target = {
        "relative": Path("relative"),
        "missing": checkout / "missing",
        "file": checkout / "ordinary.txt",
        "root": Path(checkout.anchor),
    }[kind]
    if kind == "file":
        target.write_text("not a checkout")
    with pytest.raises(permissions.FolderTrustError, match="native checkout"):
        permissions.ensure_trusted_folder(str(target))
    assert not home.exists()


def test_strict_lock_failure_is_not_already_trusted(native_checkout):
    checkout, home = native_checkout
    home.mkdir()
    (home / "config.lock").mkdir()
    with pytest.raises(permissions.FolderTrustError, match="Could not lock"):
        permissions.ensure_trusted_folder(str(checkout))
    assert not (home / "config.json").exists()


def test_missing_lock_backend_is_explicit(native_checkout, monkeypatch):
    import sys

    checkout, home = native_checkout
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.raises(permissions.FolderTrustError, match="Could not lock"):
        permissions.ensure_trusted_folder(str(checkout))
    assert not (home / "config.json").exists()


def test_failed_atomic_replace_keeps_original(native_checkout, monkeypatch):
    checkout, home = native_checkout
    home.mkdir()
    config = home / "config.json"
    config.write_text('{"unrelated": true}')

    def refuse(*args):
        raise OSError("fixture write refusal")

    monkeypatch.setattr(permissions.os, "replace", refuse)
    with pytest.raises(permissions.FolderTrustError, match="fixture write refusal"):
        permissions.ensure_trusted_folder(str(checkout))
    assert config.read_text() == '{"unrelated": true}'
    assert not list(home.glob("*.tmp"))


def test_removed_checkout_clears_canonical_trust_for_lexical_path(native_checkout):
    checkout, home = native_checkout
    alias = checkout / ".." / checkout.name
    assert permissions.ensure_trusted_folder(str(alias))
    checkout.rmdir()
    assert permissions.remove_trusted_folder(str(alias))
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == []


def test_refused_receiving_authority_does_not_trust(native_checkout, sample_record, monkeypatch):
    checkout, home = native_checkout
    record = replace(sample_record, worktree_path=str(checkout))

    def refuse(record):
        raise ExecutionSpaceError("different execution space")

    monkeypatch.setattr(config_authority, "registration_record_config", refuse)
    with pytest.raises(ExecutionSpaceError, match="different execution space"):
        worktree_trust.ensure_record_trusted(record)
    assert not home.exists()


@pytest.mark.parametrize("foreign", ["machine", "platform"])
def test_real_execution_space_fence_precedes_trust(
    native_checkout, sample_record, tmp_path, monkeypatch, foreign,
):
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  local-space:\n    execution_platform: linux\n"
        "  other-space:\n    execution_platform: windows\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-space", platform="linux", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout),
        machine="other-space" if foreign == "machine" else "local-space",
        platform="windows",
    )
    with pytest.raises(ExecutionSpaceError, match="execution space|platform"):
        worktree_trust.ensure_record_trusted(record, config)
    assert not home.exists()


def test_terminal_managed_record_does_not_trust(
    native_checkout, sample_record, monkeypatch_config,
):
    checkout, home = native_checkout
    record = replace(sample_record, worktree_path=str(checkout), kind="bridge", status="finalized")
    with pytest.raises(permissions.FolderTrustError, match="terminal managed"):
        worktree_trust.ensure_record_trusted(record)
    assert not home.exists()


@pytest.mark.parametrize("source,broken_config,other_active_project", [
    ("hook:new", False, False), ("bind", False, False),
    ("hook:new", True, False), ("bind", True, False),
    ("hook:new", False, True), ("bind", False, True),
])
def test_registered_entry_repairs_trust_after_admission(
    native_checkout, sample_record, tmp_tracking_dir, monkeypatch_config, monkeypatch, source,
    broken_config, other_active_project, capfd,
):
    import argparse
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    record = replace(
        sample_record, worktree_path=str(checkout), kind="session", status="active", sessions=[],
        machine="test",
    )
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    if broken_config:
        home.mkdir()
        (home / "config.json").write_text("malformed")
    if other_active_project:
        ambient = tmp_tracking_dir.parent / "ambient" / "worktrees"
        ambient.mkdir(parents=True)
        monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient)
        monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda worktree_id: tmp_tracking_dir)
        monkeypatch.setattr(
            session_binding_cli.worktree_identity, "_resolve_worktree_id", lambda value: value,
        )
    monkeypatch.setattr(session_binding_cli, "_spawn_status_updater", lambda *args: None)
    monkeypatch.setattr(session_binding_cli, "_register_manager_owned_monitor_session", lambda *a, **k: True)
    monkeypatch.setattr(session_binding_cli, "_note_disposition_snapshot", lambda *a, **k: None)
    args = argparse.Namespace(
        session_id="current-session", worktree_id=record.worktree_id,
        worktree_dir=str(checkout), cwd=str(checkout), pane=None, pid=None,
        stdin=False, emit_context=False, handoff_token=None,
    )
    enter = session_binding_cli.cmd_bind_session if source == "bind" else session_binding_cli.cmd_register_session
    if broken_config:
        assert enter(args) != 0
        captured = capfd.readouterr()
        assert "folder trust" in captured.out + captured.err
        assert (home / "config.json").read_text() == "malformed"
        assert tracking.load_record(
            tmp_tracking_dir / f"{record.worktree_id}.yaml"
        ).session_entry("current-session") is not None
        return
    assert enter(args) == 0
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == [str(checkout.resolve())]
    assert enter(args) == 0
    assert not (home / "permissions-config.json").exists()


def test_rejected_registration_does_not_trust(
    native_checkout, sample_record, tmp_tracking_dir, monkeypatch_config, monkeypatch,
):
    import argparse

    checkout, home = native_checkout
    record = replace(sample_record, worktree_path=str(checkout))
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    monkeypatch.setattr(
        tracking_write, "dispatch",
        lambda *args, **kwargs: {"error": "lifecycle", "message": "fixture admission refusal"},
    )
    assert session_binding_cli.cmd_register_session(argparse.Namespace(
        worktree_id=record.worktree_id, session_id="rejected-session",
        cwd=str(checkout), stdin=False, pane=None, pid=None,
    )) == 1
    assert not home.exists()


@pytest.mark.parametrize("mismatch", ["machine", "platform"])
def test_legacy_record_without_topology_cannot_trust_foreign_checkout(
    native_checkout, sample_record, tmp_path, mismatch,
):
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    actual = cfg.detect_platform()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-host", platform=actual, repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout),
        machine="foreign-host" if mismatch == "machine" else config.machine,
        platform=actual if mismatch == "machine" else ("windows" if actual != "windows" else "linux"),
    )
    assert not (tmp_path / "machines.yaml").exists()
    with pytest.raises(ExecutionSpaceError, match="local execution|native execution platform"):
        worktree_trust.ensure_record_trusted(record, config)
    assert not home.exists()


def test_declared_legacy_alias_remains_local(
    native_checkout, sample_record, tmp_path,
):
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  local-space:\n    alias: historical-alias\n", encoding="utf-8",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-space", platform=cfg.detect_platform(), repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout), machine="historical-alias", platform=config.platform,
    )
    assert worktree_trust.ensure_record_trusted(record, config)
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == [str(checkout.resolve())]
